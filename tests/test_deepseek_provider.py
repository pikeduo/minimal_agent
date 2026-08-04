from datetime import datetime, timezone
from types import SimpleNamespace

from minimal_agent.models import (
    FinalAnswer,
    Message,
    MessageRole,
    ProviderError,
    ProviderErrorKind,
    ToolCall,
    ToolCallBatch,
    ToolResult,
    ToolResultStatus,
)
from minimal_agent.providers import DeepSeekProvider, LLMRequest
from minimal_agent.providers.deepseek import SYSTEM_INSTRUCTION
from minimal_agent.runtime import AgentRuntime
from minimal_agent.tools import CalculatorTool, ToolRegistry


class FakeCompletions:
    """记录 SDK 调用参数并返回预设响应的离线替身。"""

    def __init__(self, response: object | Exception | tuple[object | Exception, ...]) -> None:
        self.calls: list[dict[str, object]] = []
        self._responses = list(response) if isinstance(response, tuple) else [response]

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(
    response: object | Exception | tuple[object | Exception, ...],
) -> tuple[object, FakeCompletions]:
    completions = FakeCompletions(response)
    return (
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        completions,
    )


def make_request(**overrides: object) -> LLMRequest:
    message = Message(
        message_id="message-1",
        user_id="user-1",
        session_id="session-1",
        role=MessageRole.USER,
        content="请计算 1 + 1。",
        created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    values: dict[str, object] = {
        "model": "deepseek-v4-flash",
        "messages": (message,),
        "tool_schemas": (
            {
                "name": "calculator",
                "description": "计算表达式。",
                "parameters": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            },
        ),
    }
    values.update(overrides)
    return LLMRequest(**values)


def response_with_message(
    *,
    content: str | None = None,
    tool_calls: object = None,
    finish_reason: str | None = None,
    **attributes: object,
) -> object:
    message_attributes = {
        "content": content,
        "tool_calls": tool_calls,
        **attributes,
    }
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(**message_attributes),
                finish_reason=finish_reason,
            )
        ]
    )


def test_deepseek_provider_maps_final_answer_and_openai_tool_schema() -> None:
    client, completions = make_client(response_with_message(content="结果是 2。"))
    provider = DeepSeekProvider(api_key="test-key", client=client)

    result = provider.complete(make_request(session_summary="用户正在计算简单表达式。"))

    assert result == FinalAnswer("结果是 2。")
    assert completions.calls == [
        {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "system", "content": "会话摘要：用户正在计算简单表达式。"},
                {"role": "user", "content": "请计算 1 + 1。"},
            ],
            "extra_body": {"thinking": {"type": "disabled"}},
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "description": "计算表达式。",
                        "parameters": {
                            "type": "object",
                            "properties": {"expression": {"type": "string"}},
                            "required": ["expression"],
                        },
                    },
                }
            ],
        }
    ]


def test_deepseek_provider_includes_greeting_and_capability_instruction() -> None:
    client, completions = make_client(response_with_message(content="你好！"))
    provider = DeepSeekProvider(api_key="test-key", client=client)

    provider.complete(make_request())

    system_message = completions.calls[0]["messages"][0]
    assert system_message == {"role": "system", "content": SYSTEM_INSTRUCTION}
    assert "简短问候" in SYSTEM_INSTRUCTION
    assert "计算、搜索、天气查询和待办管理" in SYSTEM_INSTRUCTION
    assert "计算工具调用协议" in SYSTEM_INSTRUCTION
    assert "第一步只能发起 calculator 的结构化 ToolCall" in SYSTEM_INSTRUCTION
    assert 'calculator({"expression":"123 * 456"})' in SYSTEM_INSTRUCTION


def test_deepseek_provider_includes_current_todo_snapshot_in_context() -> None:
    client, completions = make_client(response_with_message(content="已读取待办状态。"))
    provider = DeepSeekProvider(api_key="test-key", client=client)

    result = provider.complete(
        make_request(
            current_todos=(
                {
                    "todo_id": "todo-1",
                    "title": "整理周报",
                    "status": "completed",
                    "completed_at": "2026-08-04T10:00:00+00:00",
                },
            )
        )
    )

    assert result == FinalAnswer("已读取待办状态。")
    assert completions.calls[0]["messages"][1] == {
        "role": "system",
        "content": (
            "当前会话待办状态："
            '[{"todo_id": "todo-1", "title": "整理周报", "status": "completed", '
            '"completed_at": "2026-08-04T10:00:00+00:00"}]'
        ),
    }


def test_deepseek_provider_maps_multiple_tool_calls_to_internal_batch() -> None:
    raw_calls = [
        SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(
                name="calculator", arguments='{"expression": "1 + 1"}'
            ),
        ),
        SimpleNamespace(
            id="call-2",
            function=SimpleNamespace(name="weather", arguments='{"city": "厦门"}'),
        ),
    ]
    client, _ = make_client(response_with_message(tool_calls=raw_calls))
    provider = DeepSeekProvider(api_key="test-key", client=client)

    result = provider.complete(make_request())

    assert result == ToolCallBatch(
        (
            ToolCall("call-1", "calculator", {"expression": "1 + 1"}),
            ToolCall("call-2", "weather", {"city": "厦门"}),
        )
    )


def test_deepseek_provider_maps_short_decision_summary_for_answer_and_tool_call() -> None:
    answer_client, _ = make_client(
        response_with_message(
            content="结果是 2。",
            reasoning_summary="  已完成计算并整理结果。  ",
        )
    )
    answer_provider = DeepSeekProvider(api_key="test-key", client=answer_client)

    answer = answer_provider.complete(make_request())

    assert answer == FinalAnswer("结果是 2。", "已完成计算并整理结果。")

    raw_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="calculator", arguments='{"expression": "1 + 1"}'
        ),
    )
    tool_client, _ = make_client(
        response_with_message(
            tool_calls=[raw_call],
            decision_summary="需要调用计算工具。",
        )
    )
    tool_provider = DeepSeekProvider(api_key="test-key", client=tool_client)

    tool_batch = tool_provider.complete(make_request())

    assert tool_batch == ToolCallBatch(
        (ToolCall("call-1", "calculator", {"expression": "1 + 1"}),),
        "需要调用计算工具。",
    )


def test_deepseek_provider_ignores_full_reasoning_content() -> None:
    client, _ = make_client(
        response_with_message(
            content="结果是 2。",
            reasoning_content="这是一段不应被读取、保存或展示的完整思维链。",
        )
    )
    provider = DeepSeekProvider(api_key="test-key", client=client)

    result = provider.complete(make_request())

    assert result == FinalAnswer("结果是 2。")


def test_deepseek_provider_rejects_length_truncated_response_safely() -> None:
    client, _ = make_client(
        response_with_message(
            content="这是一段在中途被截断的回答",
            finish_reason="length",
        )
    )
    provider = DeepSeekProvider(api_key="test-key", client=client)

    result = provider.complete(make_request())

    assert result == ProviderError(
        kind=ProviderErrorKind.INCOMPLETE_RESPONSE,
        safe_message="模型回复不完整，请重新发送。",
        retryable=True,
    )


def test_deepseek_provider_rejects_overlong_decision_summary_safely() -> None:
    client, _ = make_client(
        response_with_message(
            content="结果是 2。",
            decision_summary="a" * 501,
        )
    )
    provider = DeepSeekProvider(api_key="test-key", client=client)

    result = provider.complete(make_request())

    assert result == ProviderError(
        kind=ProviderErrorKind.INVALID_RESPONSE,
        safe_message="模型服务返回了无法解析的响应。",
        retryable=False,
    )


def test_deepseek_provider_removes_unsupported_length_constraints_from_tools() -> None:
    client, completions = make_client(response_with_message(content="已处理。"))
    provider = DeepSeekProvider(api_key="test-key", client=client)

    result = provider.complete(
        make_request(
            tool_schemas=(
                {
                    "name": "todo",
                    "description": "处理待办。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 200,
                            }
                        },
                        "required": ["title"],
                        "additionalProperties": False,
                    },
                },
            )
        )
    )

    parameters = completions.calls[0]["tools"][0]["function"]["parameters"]
    assert result == FinalAnswer("已处理。")
    assert parameters == {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
        "additionalProperties": False,
    }


def test_deepseek_provider_sends_internal_tool_exchange_in_protocol_order() -> None:
    client, completions = make_client(response_with_message(content="工具执行完成。"))
    provider = DeepSeekProvider(api_key="test-key", client=client)
    batch = ToolCallBatch(
        (ToolCall("call-1", "calculator", {"expression": "1 + 1"}),)
    )
    tool_result = ToolResult(
        tool_call_id="call-1",
        tool_name="calculator",
        status=ToolResultStatus.SUCCESS,
        result={"value": 2},
    )

    result = provider.complete(
        make_request(tool_call_batches=(batch,), tool_results=(tool_result,))
    )

    assert result == FinalAnswer("工具执行完成。")
    messages = completions.calls[0]["messages"]
    assert messages[0] == {"role": "system", "content": SYSTEM_INSTRUCTION}
    assert messages[1] == {"role": "user", "content": "请计算 1 + 1。"}
    assert messages[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "calculator",
                    "arguments": '{"expression": "1 + 1"}',
                },
            }
        ],
    }
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": (
            '{"tool_call_id": "call-1", "tool_name": "calculator", '
            '"status": "success", "result": {"value": 2}, '
            '"error_code": null, "error_message": null}'
        ),
    }


def test_deepseek_provider_drives_runtime_tool_loop_without_network() -> None:
    raw_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="calculator", arguments='{"expression": "1 + 1"}'
        ),
    )
    client, completions = make_client(
        (
            response_with_message(tool_calls=[raw_call]),
            response_with_message(content="计算结果是 2。"),
        )
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    runtime = AgentRuntime(
        provider=DeepSeekProvider(api_key="test-key", client=client),
        tool_registry=registry,
        model="deepseek-v4-flash",
        max_steps=3,
    )

    result = runtime.run(
        user_id="user-1",
        session_id="session-1",
        messages=make_request().messages,
    )

    assert result.final_answer == FinalAnswer("计算结果是 2。")
    assert result.tool_results[0].result["value"] == 2
    second_request_messages = completions.calls[1]["messages"]
    assert second_request_messages[-1]["role"] == "tool"
    assert '"value": 2' in second_request_messages[-1]["content"]


def test_deepseek_provider_rejects_unparseable_tool_arguments_safely() -> None:
    raw_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="calculator", arguments="not-json"),
    )
    client, _ = make_client(response_with_message(tool_calls=[raw_call]))
    provider = DeepSeekProvider(api_key="test-key", client=client)

    result = provider.complete(make_request())

    assert isinstance(result, ProviderError)
    assert result.kind is ProviderErrorKind.INVALID_RESPONSE
    assert result.retryable is False


def test_deepseek_provider_converts_client_failure_to_safe_error() -> None:
    client, _ = make_client(RuntimeError("密钥和原始错误都不应泄露"))
    provider = DeepSeekProvider(api_key="test-key", client=client)

    result = provider.complete(make_request())

    assert result == ProviderError(
        kind=ProviderErrorKind.UNKNOWN,
        safe_message="模型服务调用失败。",
        retryable=True,
    )
