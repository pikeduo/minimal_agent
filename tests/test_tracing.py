"""运行生命周期 Trace 与脱敏行为的离线集成测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from minimal_agent.models import (
    FinalAnswer,
    Message,
    MessageRole,
    ProviderError,
    ProviderErrorKind,
    ToolCall,
    ToolCallBatch,
)
from minimal_agent.providers import ScriptedLLMProvider
from minimal_agent.runtime import AgentRuntime
from minimal_agent.tools import CalculatorTool, ToolRegistry
from minimal_agent.tracing import JsonlTraceRecorder


def make_message() -> Message:
    return Message(
        message_id="message-1",
        user_id="user-1",
        session_id="session-1",
        role=MessageRole.USER,
        content="请计算 2 + 3。",
        created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


def make_runtime(*, responses: tuple[object, ...], trace_path) -> AgentRuntime:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return AgentRuntime(
        provider=ScriptedLLMProvider(responses),
        tool_registry=registry,
        model="scripted-model",
        max_steps=3,
        trace_recorder=JsonlTraceRecorder(trace_path),
    )


def read_events(path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_runtime_writes_linked_successful_lifecycle_events_without_content(tmp_path) -> None:
    trace_path = tmp_path / "logs" / "agent-trace.jsonl"
    runtime = make_runtime(
        responses=(
            ToolCallBatch((ToolCall("call-1", "calculator", {"expression": "2 + 3"}),)),
            FinalAnswer("计算结果为 5。"),
        ),
        trace_path=trace_path,
    )

    result = runtime.run(
        user_id="user-1",
        session_id="session-1",
        messages=(make_message(),),
        run_id="run-trace-success",
        context_compressed=True,
    )

    events = read_events(trace_path)
    assert result.final_answer == FinalAnswer("计算结果为 5。")
    assert [event["event"] for event in events] == [
        "run.started",
        "session.loaded",
        "context.built",
        "context.compressed",
        "llm.requested",
        "llm.responded",
        "tool.started",
        "tool.succeeded",
        "llm.requested",
        "llm.responded",
        "run.completed",
    ]
    assert {event["run_id"] for event in events} == {"run-trace-success"}
    trace_text = trace_path.read_text(encoding="utf-8")
    assert "2 + 3" not in trace_text
    assert "计算结果为 5。" not in trace_text


def test_runtime_writes_safe_failure_and_tool_failure_events(tmp_path) -> None:
    trace_path = tmp_path / "agent-trace.jsonl"
    runtime = make_runtime(
        responses=(
            ToolCallBatch((ToolCall("call-1", "calculator", {"wrong": "field"}),)),
            ProviderError(
                ProviderErrorKind.UNAVAILABLE,
                "模型服务暂不可用。",
                retryable=True,
            ),
        ),
        trace_path=trace_path,
    )

    result = runtime.run(
        user_id="user-1",
        session_id="session-1",
        messages=(make_message(),),
        run_id="run-trace-failure",
    )

    events = read_events(trace_path)
    event_names = [event["event"] for event in events]
    assert result.provider_error is not None
    assert "tool.failed" in event_names
    assert event_names[-1] == "run.failed"
    failed_event = events[-1]
    assert failed_event["data"] == {
        "error_kind": "unavailable",
        "retryable": True,
    }


def test_recorder_removes_sensitive_fields_and_text_patterns(tmp_path) -> None:
    trace_path = tmp_path / "agent-trace.jsonl"
    recorder = JsonlTraceRecorder(trace_path)

    written = recorder.emit(
        event="test.sensitive",
        run_id="run-sensitive",
        data={
            "api_key": "top-secret-key",
            "headers": {
                "Authorization": "Bearer authorization-secret",
                "visible": "保留该字段",
            },
            "nested": {"traceback": "hidden stack", "safe": "api-key=inline-secret"},
            "plain": "sk-secret-value",
        },
    )

    assert written is True
    trace_text = trace_path.read_text(encoding="utf-8")
    assert "top-secret-key" not in trace_text
    assert "authorization-secret" not in trace_text
    assert "hidden stack" not in trace_text
    assert "inline-secret" not in trace_text
    assert "sk-secret-value" not in trace_text
    event = read_events(trace_path)[0]
    assert event["data"] == {
        "headers": {"visible": "保留该字段"},
        "nested": {"safe": "[已脱敏]"},
        "plain": "[已脱敏]",
    }


def test_trace_write_failure_does_not_interrupt_runtime(tmp_path) -> None:
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("占位文件", encoding="utf-8")
    runtime = make_runtime(
        responses=(FinalAnswer("仍可完成。"),),
        trace_path=blocked_parent / "agent-trace.jsonl",
    )

    result = runtime.run(
        user_id="user-1",
        session_id="session-1",
        messages=(make_message(),),
        run_id="run-trace-write-failure",
    )

    assert result.final_answer == FinalAnswer("仍可完成。")


def test_provider_exception_never_writes_exception_text_or_traceback(tmp_path) -> None:
    class RaisingProvider:
        def complete(self, request):
            raise RuntimeError("原始异常中的密钥 top-secret-key 不应被记录")

    trace_path = tmp_path / "agent-trace.jsonl"
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    runtime = AgentRuntime(
        provider=RaisingProvider(),
        tool_registry=registry,
        model="scripted-model",
        max_steps=2,
        trace_recorder=JsonlTraceRecorder(trace_path),
    )

    result = runtime.run(
        user_id="user-1",
        session_id="session-1",
        messages=(make_message(),),
        run_id="run-provider-exception",
    )

    trace_text = trace_path.read_text(encoding="utf-8")
    assert result.provider_error is not None
    assert result.provider_error.safe_message == "模型服务调用失败。"
    assert "top-secret-key" not in trace_text
    assert "RuntimeError" not in trace_text
    assert "traceback" not in trace_text.lower()
