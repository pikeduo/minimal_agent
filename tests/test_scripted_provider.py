from datetime import datetime, timezone

import pytest

from minimal_agent.errors import DomainValidationError
from minimal_agent.models import (
    FinalAnswer,
    Message,
    MessageRole,
    ProviderError,
    ProviderErrorKind,
    ToolCall,
    ToolCallBatch,
)
from minimal_agent.providers import LLMRequest, ScriptedLLMProvider


def make_request(content: str = "请处理这条消息") -> LLMRequest:
    message = Message(
        message_id="message-1",
        user_id="user-1",
        session_id="session-1",
        role=MessageRole.USER,
        content=content,
        created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    return LLMRequest(
        model="scripted-model",
        messages=(message,),
        tool_schemas=(
            {
                "name": "calculator",
                "parameters": {"type": "object"},
            },
        ),
    )


def test_scripted_provider_returns_responses_in_preconfigured_order() -> None:
    tool_batch = ToolCallBatch(
        (ToolCall("call-1", "calculator", {"expression": "1 + 1"}),),
        "调用计算器",
    )
    final_answer = FinalAnswer("计算结果为 2。", "返回最终答案")
    provider = ScriptedLLMProvider((tool_batch, final_answer))

    first = provider.complete(make_request("任意文本一"))
    second = provider.complete(make_request("任意文本二"))

    assert first is tool_batch
    assert second is final_answer
    assert provider.remaining_responses == 0
    assert [request.messages[0].content for request in provider.received_requests] == [
        "任意文本一",
        "任意文本二",
    ]


def test_scripted_provider_returns_safe_error_when_responses_are_exhausted() -> None:
    provider = ScriptedLLMProvider(())

    result = provider.complete(make_request())

    assert isinstance(result, ProviderError)
    assert result.kind is ProviderErrorKind.UNKNOWN
    assert result.retryable is False
    assert "预设响应" in result.safe_message


def test_scripted_provider_preserves_preconfigured_provider_error() -> None:
    expected_error = ProviderError(
        ProviderErrorKind.UNAVAILABLE,
        "模型服务暂不可用。",
        retryable=True,
    )
    provider = ScriptedLLMProvider((expected_error,))

    assert provider.complete(make_request()) is expected_error


def test_request_copies_schemas_and_serializes_internal_models() -> None:
    schema = {"name": "weather", "parameters": {"type": "object"}}
    request = LLMRequest(
        model="scripted-model",
        messages=(make_request().messages[0],),
        tool_schemas=(schema,),
    )
    schema["name"] = "changed-after-construction"
    schema["parameters"]["type"] = "array"

    assert request.to_dict() == {
        "model": "scripted-model",
        "messages": [
            {
                "message_id": "message-1",
                "user_id": "user-1",
                "session_id": "session-1",
                "role": "user",
                "content": "请处理这条消息",
                "created_at": "2026-08-04T00:00:00+00:00",
                "run_id": None,
            }
        ],
        "tool_schemas": [
            {"name": "weather", "parameters": {"type": "object"}}
        ],
        "tool_results": [],
    }


def test_provider_contract_rejects_invalid_request_and_scripted_response() -> None:
    with pytest.raises(DomainValidationError, match="messages"):
        LLMRequest(model="scripted-model", messages=())
    with pytest.raises(DomainValidationError, match="JSON"):
        LLMRequest(
            model="scripted-model",
            messages=(make_request().messages[0],),
            tool_schemas=({"invalid": object()},),
        )
    with pytest.raises(DomainValidationError, match="预设结果"):
        ScriptedLLMProvider(("not-a-provider-result",))
