from __future__ import annotations

from datetime import datetime, timezone

import pytest

from minimal_agent.errors import DomainValidationError
from minimal_agent.models import (
    FinalAnswer,
    Message,
    MessageRole,
    ProviderError,
    ProviderErrorKind,
    RunStatus,
    ToolCall,
    ToolCallBatch,
    ToolResultStatus,
)
from minimal_agent.providers import ScriptedLLMProvider
from minimal_agent.runtime import AgentRuntime
from minimal_agent.tools import CalculatorTool, SearchTool, ToolRegistry, WeatherTool


def make_message(content: str = "请处理这个任务") -> Message:
    return Message(
        message_id="message-1",
        user_id="user-1",
        session_id="session-1",
        role=MessageRole.USER,
        content=content,
        created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (CalculatorTool(), SearchTool(), WeatherTool()):
        registry.register(tool)
    return registry


def make_runtime(
    responses: tuple[object, ...],
    *,
    max_steps: int = 4,
) -> tuple[AgentRuntime, ScriptedLLMProvider]:
    provider = ScriptedLLMProvider(responses)
    return (
        AgentRuntime(
            provider=provider,
            tool_registry=make_registry(),
            model="scripted-model",
            max_steps=max_steps,
        ),
        provider,
    )


def run(runtime: AgentRuntime):
    return runtime.run(
        user_id="user-1",
        session_id="session-1",
        messages=(make_message(),),
        run_id="run-1",
    )


def test_runtime_returns_direct_answer_without_tool_execution() -> None:
    runtime, provider = make_runtime((FinalAnswer("可以直接回答。"),))

    result = run(runtime)

    assert result.run.status is RunStatus.COMPLETED
    assert result.run.step == 1
    assert result.final_answer == FinalAnswer("可以直接回答。")
    assert result.tool_results == ()
    assert provider.received_requests[0].tool_results == ()


def test_runtime_executes_one_tool_and_returns_result_to_next_provider_call() -> None:
    runtime, provider = make_runtime(
        (
            ToolCallBatch((ToolCall("call-1", "calculator", {"expression": "2 + 3"}),)),
            FinalAnswer("计算结果为 5。"),
        )
    )

    result = run(runtime)

    assert result.run.status is RunStatus.COMPLETED
    assert result.run.step == 2
    assert result.tool_results[0].result == {"expression": "2 + 3", "value": 5}
    assert provider.received_requests[1].tool_results == result.tool_results


def test_runtime_supports_multiple_sequential_tool_rounds() -> None:
    runtime, provider = make_runtime(
        (
            ToolCallBatch((ToolCall("call-1", "weather", {"location": "厦门"}),)),
            ToolCallBatch((ToolCall("call-2", "search", {"query": "Agent"}),)),
            FinalAnswer("已查询天气和资料。"),
        )
    )

    result = run(runtime)

    assert result.run.status is RunStatus.COMPLETED
    assert [tool_result.tool_name for tool_result in result.tool_results] == [
        "weather",
        "search",
    ]
    assert len(provider.received_requests[2].tool_results) == 2


def test_runtime_executes_multiple_calls_from_the_same_provider_response_in_order() -> None:
    runtime, provider = make_runtime(
        (
            ToolCallBatch(
                (
                    ToolCall("call-1", "calculator", {"expression": "1 + 1"}),
                    ToolCall("call-2", "calculator", {"expression": "2 + 2"}),
                )
            ),
            FinalAnswer("两个计算均已完成。"),
        )
    )

    result = run(runtime)

    assert [tool_result.result["value"] for tool_result in result.tool_results] == [2, 4]
    assert [tool_result.tool_call_id for tool_result in provider.received_requests[1].tool_results] == [
        "call-1",
        "call-2",
    ]


def test_runtime_returns_parameter_failure_to_provider_and_allows_correction() -> None:
    runtime, provider = make_runtime(
        (
            ToolCallBatch((ToolCall("call-1", "calculator", {"wrong": "field"}),)),
            ToolCallBatch((ToolCall("call-2", "calculator", {"expression": "6 / 2"}),)),
            FinalAnswer("参数已修正，结果为 3。"),
        )
    )

    result = run(runtime)

    assert [tool_result.status for tool_result in result.tool_results] == [
        ToolResultStatus.ERROR,
        ToolResultStatus.SUCCESS,
    ]
    assert result.tool_results[0].error_code == "invalid_arguments"
    assert provider.received_requests[1].tool_results[0].error_code == "invalid_arguments"


def test_runtime_returns_execution_failure_to_provider_and_allows_correction() -> None:
    runtime, provider = make_runtime(
        (
            ToolCallBatch((ToolCall("call-1", "calculator", {"expression": "1 / 0"}),)),
            ToolCallBatch((ToolCall("call-2", "calculator", {"expression": "8 - 3"}),)),
            FinalAnswer("除零错误已修正，结果为 5。"),
        )
    )

    result = run(runtime)

    assert result.tool_results[0].error_code == "division_by_zero"
    assert result.tool_results[1].result["value"] == 5
    assert provider.received_requests[1].tool_results[0].error_code == "division_by_zero"


def test_runtime_stops_after_maximum_provider_steps() -> None:
    runtime, provider = make_runtime(
        (
            ToolCallBatch((ToolCall("call-1", "calculator", {"expression": "1 + 1"}),)),
            ToolCallBatch((ToolCall("call-2", "calculator", {"expression": "2 + 2"}),)),
            FinalAnswer("不应被读取。"),
        ),
        max_steps=2,
    )

    result = run(runtime)

    assert result.run.status is RunStatus.MAX_STEPS
    assert result.run.step == 2
    assert result.final_answer is None
    assert len(result.tool_results) == 2
    assert len(provider.received_requests) == 2


def test_runtime_finishes_safely_when_provider_returns_error_or_raises() -> None:
    runtime, _ = make_runtime(
        (
            ProviderError(
                ProviderErrorKind.UNAVAILABLE,
                "模型服务暂不可用。",
                retryable=True,
            ),
        )
    )
    provider_result = run(runtime)

    class RaisingProvider:
        def complete(self, request):
            raise RuntimeError("不应泄露给用户")

    raising_runtime = AgentRuntime(
        provider=RaisingProvider(),
        tool_registry=make_registry(),
        model="scripted-model",
        max_steps=2,
    )
    raised_result = run(raising_runtime)

    assert provider_result.run.status is RunStatus.FAILED
    assert provider_result.provider_error is not None
    assert provider_result.provider_error.kind is ProviderErrorKind.UNAVAILABLE
    assert raised_result.run.status is RunStatus.FAILED
    assert raised_result.provider_error is not None
    assert raised_result.provider_error.safe_message == "模型服务调用失败。"


def test_runtime_rejects_cross_session_messages_and_invalid_step_limit() -> None:
    provider = ScriptedLLMProvider((FinalAnswer("不应调用。"),))

    with pytest.raises(DomainValidationError, match="max_steps"):
        AgentRuntime(
            provider=provider,
            tool_registry=make_registry(),
            model="scripted-model",
            max_steps=0,
        )

    runtime, _ = make_runtime((FinalAnswer("不应调用。"),))
    foreign_message = Message(
        message_id="message-2",
        user_id="other-user",
        session_id="other-session",
        role=MessageRole.USER,
        content="不属于当前会话。",
        created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    with pytest.raises(DomainValidationError, match="当前用户和会话"):
        runtime.run(
            user_id="user-1",
            session_id="session-1",
            messages=(foreign_message,),
        )
