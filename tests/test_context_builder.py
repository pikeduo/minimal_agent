from __future__ import annotations

from datetime import datetime, timezone

import pytest

from minimal_agent.context import ContextBuilder, ConversationService
from minimal_agent.errors import ResourceNotFoundError
from minimal_agent.models import (
    FinalAnswer,
    Message,
    MessageRole,
    ToolCall,
    ToolCallBatch,
    ToolResult,
    ToolResultStatus,
)
from minimal_agent.providers import ScriptedLLMProvider
from minimal_agent.runtime import AgentRuntime
from minimal_agent.storage import (
    MessageRepository,
    SessionRepository,
    SQLiteDatabase,
    ToolResultRepository,
)
from minimal_agent.tools import CalculatorTool, ToolRegistry


def make_components(tmp_path):
    database = SQLiteDatabase(tmp_path / "minimal_agent.sqlite3")
    database.initialize()
    sessions = SessionRepository(database)
    messages = MessageRepository(database)
    tool_results = ToolResultRepository(database)
    return sessions, messages, tool_results


def append_message(messages, *, message_id, session_id, content, hour=0):
    messages.append(
        Message(
            message_id=message_id,
            user_id="user-a",
            session_id=session_id,
            role=MessageRole.USER,
            content=content,
            created_at=datetime(2026, 8, 4, hour, tzinfo=timezone.utc),
        )
    )


def test_context_builder_keeps_recent_current_session_data_only(tmp_path) -> None:
    sessions, messages, tool_results = make_components(tmp_path)
    sessions.create(user_id="user-a", title="天气", session_id="session-weather")
    sessions.create(user_id="user-a", title="周报", session_id="session-report")
    append_message(messages, message_id="message-1", session_id="session-weather", content="第一条")
    append_message(
        messages,
        message_id="message-2",
        session_id="session-weather",
        content="第二条",
        hour=1,
    )
    append_message(
        messages,
        message_id="message-3",
        session_id="session-weather",
        content="第三条",
        hour=2,
    )
    append_message(
        messages,
        message_id="message-4",
        session_id="session-report",
        content="周报内容",
        hour=3,
    )
    tool_results.append_for_run(
        user_id="user-a",
        session_id="session-weather",
        run_id="run-weather",
        tool_results=(
            ToolResult(
                "call-weather",
                "weather",
                ToolResultStatus.SUCCESS,
                {"location": "厦门", "temperature_c": 28},
            ),
        ),
    )
    tool_results.append_for_run(
        user_id="user-a",
        session_id="session-report",
        run_id="run-report",
        tool_results=(
            ToolResult(
                "call-report",
                "search",
                ToolResultStatus.SUCCESS,
                {"query": "周报"},
            ),
        ),
    )
    builder = ContextBuilder(
        session_repository=sessions,
        message_repository=messages,
        tool_result_repository=tool_results,
        max_messages=2,
        max_tool_results=2,
    )

    context = builder.build(user_id="user-a", session_id="session-weather")

    assert [message.content for message in context.messages] == ["第二条", "第三条"]
    assert [result.tool_call_id for result in context.tool_results] == ["call-weather"]
    with pytest.raises(ResourceNotFoundError):
        builder.build(user_id="user-b", session_id="session-weather")


def test_conversation_service_supports_normal_and_tool_result_followups(tmp_path) -> None:
    sessions, messages, tool_results = make_components(tmp_path)
    sessions.create(user_id="user-a", title="计算", session_id="session-calc")
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    provider = ScriptedLLMProvider(
        (
            ToolCallBatch((ToolCall("call-1", "calculator", {"expression": "2 + 2"}),)),
            FinalAnswer("第一次计算完成。"),
            FinalAnswer("上次计算结果为 4。"),
        )
    )
    runtime = AgentRuntime(
        provider=provider,
        tool_registry=registry,
        model="scripted-model",
        max_steps=4,
    )
    builder = ContextBuilder(
        session_repository=sessions,
        message_repository=messages,
        tool_result_repository=tool_results,
        max_messages=8,
        max_tool_results=8,
    )
    service = ConversationService(
        context_builder=builder,
        message_repository=messages,
        tool_result_repository=tool_results,
        runtime=runtime,
    )

    first = service.respond(
        user_id="user-a",
        session_id="session-calc",
        content="帮我计算 2 + 2。",
    )
    second = service.respond(
        user_id="user-a",
        session_id="session-calc",
        content="上次结果是多少？",
    )

    assert first.assistant_message is not None
    assert second.assistant_message is not None
    assert second.assistant_message.content == "上次计算结果为 4。"
    assert [message.content for message in provider.received_requests[2].messages] == [
        "帮我计算 2 + 2。",
        "第一次计算完成。",
        "上次结果是多少？",
    ]
    assert provider.received_requests[2].tool_results[0].result["value"] == 4
    assert [message.role for message in messages.list_for_session(
        user_id="user-a", session_id="session-calc"
    )] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]


def test_conversation_service_rejects_other_user_session_access(tmp_path) -> None:
    sessions, messages, tool_results = make_components(tmp_path)
    sessions.create(user_id="user-a", title="私有会话", session_id="session-private")
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    service = ConversationService(
        context_builder=ContextBuilder(
            session_repository=sessions,
            message_repository=messages,
            tool_result_repository=tool_results,
            max_messages=8,
            max_tool_results=8,
        ),
        message_repository=messages,
        tool_result_repository=tool_results,
        runtime=AgentRuntime(
            provider=ScriptedLLMProvider((FinalAnswer("不应调用。"),)),
            tool_registry=registry,
            model="scripted-model",
            max_steps=2,
        ),
    )

    with pytest.raises(ResourceNotFoundError):
        service.respond(
            user_id="user-b",
            session_id="session-private",
            content="越权访问。",
        )
