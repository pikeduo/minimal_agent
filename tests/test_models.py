from datetime import datetime, timezone

import pytest

from minimal_agent.errors import DomainValidationError, InvalidStateTransition
from minimal_agent.models import (
    AgentRun,
    FinalAnswer,
    Message,
    MessageRole,
    ProviderError,
    ProviderErrorKind,
    RunStatus,
    Session,
    ToolCall,
    ToolCallBatch,
    ToolResult,
    ToolResultStatus,
)


def test_session_and_message_serialize_to_internal_data() -> None:
    timestamp = datetime(2026, 8, 4, tzinfo=timezone.utc)
    session = Session("session-1", "user-1", "天气计划", timestamp)
    message = Message(
        "message-1",
        "user-1",
        "session-1",
        MessageRole.USER,
        "明天厦门天气如何？",
        timestamp,
        "run-1",
    )

    assert session.to_dict()["created_at"] == "2026-08-04T00:00:00+00:00"
    assert message.to_dict() == {
        "message_id": "message-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "role": "user",
        "content": "明天厦门天气如何？",
        "created_at": "2026-08-04T00:00:00+00:00",
        "run_id": "run-1",
    }


def test_models_reject_blank_identity_and_naive_timestamp() -> None:
    with pytest.raises(DomainValidationError, match="session_id"):
        Session("", "user-1", "标题")

    with pytest.raises(DomainValidationError, match="时区"):
        Message(
            "message-1",
            "user-1",
            "session-1",
            MessageRole.USER,
            "内容",
            datetime(2026, 8, 4),
        )
    with pytest.raises(DomainValidationError, match="role"):
        Message(
            "message-2",
            "user-1",
            "session-1",
            "user",
            "内容",
            datetime(2026, 8, 4, tzinfo=timezone.utc),
        )


def test_agent_run_allows_only_explicit_state_transitions() -> None:
    run = AgentRun("run-1", "user-1", "session-1")

    running = run.start().next_step()
    finished = running.finish(RunStatus.COMPLETED)

    assert finished.status is RunStatus.COMPLETED
    assert finished.step == 1
    with pytest.raises(InvalidStateTransition):
        finished.next_step()
    with pytest.raises(InvalidStateTransition):
        run.finish(RunStatus.COMPLETED)
    with pytest.raises(DomainValidationError, match="status"):
        AgentRun("run-2", "user-1", "session-1", "running")


def test_tool_call_and_batch_are_json_serializable_and_copy_arguments() -> None:
    arguments = {"expression": "(1 + 2) * 3"}
    call = ToolCall("call-1", "calculator", arguments)
    arguments["expression"] = "不应影响模型"
    batch = ToolCallBatch((call,), "调用计算器")

    assert batch.to_dict() == {
        "type": "tool_call_batch",
        "calls": [
            {
                "tool_call_id": "call-1",
                "name": "calculator",
                "arguments": {"expression": "(1 + 2) * 3"},
            }
        ],
        "decision_summary": "调用计算器",
    }


def test_tool_call_batch_rejects_empty_or_duplicate_call_ids() -> None:
    first_call = ToolCall("call-1", "weather", {"location": "厦门"})
    duplicate_call = ToolCall("call-1", "search", {"query": "厦门天气"})

    with pytest.raises(DomainValidationError, match="不能为空"):
        ToolCallBatch(())
    with pytest.raises(DomainValidationError, match="唯一"):
        ToolCallBatch((first_call, duplicate_call))
    with pytest.raises(DomainValidationError, match="ToolCall"):
        ToolCallBatch(("call-1",))


def test_final_answer_provider_error_and_tool_result_have_safe_serialization() -> None:
    answer = FinalAnswer("厦门明天晴，温度 28℃。", "直接回答天气结果")
    provider_error = ProviderError(
        ProviderErrorKind.RATE_LIMIT,
        "模型服务暂时繁忙，请稍后重试。",
        retryable=True,
    )
    failed_result = ToolResult(
        "call-1",
        "calculator",
        ToolResultStatus.ERROR,
        result={},
        error_code="invalid_expression",
        error_message="表达式只支持基础四则运算。",
    )

    assert answer.to_dict()["type"] == "final_answer"
    assert provider_error.to_dict()["retryable"] is True
    assert failed_result.to_dict()["status"] == "error"

    with pytest.raises(DomainValidationError, match="不能包含错误信息"):
        ToolResult(
            "call-2",
            "weather",
            ToolResultStatus.SUCCESS,
            result={"condition": "sunny"},
            error_code="unexpected",
        )


def test_tool_call_rejects_non_json_arguments_and_long_decision_summary() -> None:
    with pytest.raises(DomainValidationError, match="JSON"):
        ToolCall("call-1", "calculator", {"value": object()})
    with pytest.raises(DomainValidationError, match="长度"):
        FinalAnswer("答案", "a" * 501)
