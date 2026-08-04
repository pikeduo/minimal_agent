from __future__ import annotations

from datetime import datetime, timezone

from minimal_agent.context import ContextBuilder, ContextCompressor, ConversationService
from minimal_agent.models import FinalAnswer, Message, MessageRole
from minimal_agent.providers import ScriptedLLMProvider
from minimal_agent.runtime import AgentRuntime
from minimal_agent.storage import (
    MessageRepository,
    SessionRepository,
    SessionSummaryRepository,
    SQLiteDatabase,
    ToolResultRepository,
)
from minimal_agent.tools import CalculatorTool, ToolRegistry


def make_components(tmp_path):
    database = SQLiteDatabase(tmp_path / "minimal_agent.sqlite3")
    database.initialize()
    sessions = SessionRepository(database)
    messages = MessageRepository(database)
    summaries = SessionSummaryRepository(database)
    tool_results = ToolResultRepository(database)
    sessions.create(user_id="user-a", title="压缩会话", session_id="session-compression")
    return sessions, messages, summaries, tool_results


def append_messages(messages, count: int, *, start: int = 1) -> None:
    for index in range(start, start + count):
        messages.append(
            Message(
                message_id=f"message-{index}",
                user_id="user-a",
                session_id="session-compression",
                role=MessageRole.USER if index % 2 else MessageRole.ASSISTANT,
                content=f"历史消息 {index}",
                created_at=datetime(2026, 8, 4, 0, index, tzinfo=timezone.utc),
            )
        )


def make_compressor(messages, summaries) -> ContextCompressor:
    return ContextCompressor(
        message_repository=messages,
        summary_repository=summaries,
        max_context_messages=3,
        keep_recent=2,
    )


def test_compressor_keeps_recent_messages_and_preserves_original_history(tmp_path) -> None:
    sessions, messages, summaries, tool_results = make_components(tmp_path)
    append_messages(messages, 5)
    compressor = make_compressor(messages, summaries)
    builder = ContextBuilder(
        session_repository=sessions,
        message_repository=messages,
        tool_result_repository=tool_results,
        max_messages=2,
        max_tool_results=2,
        compressor=compressor,
    )

    context = builder.build(user_id="user-a", session_id="session-compression")

    assert [message.message_id for message in context.messages] == ["message-4", "message-5"]
    assert context.summary is not None
    assert "历史消息 1" in context.summary
    assert "历史消息 3" in context.summary
    assert summaries.get(
        user_id="user-a", session_id="session-compression"
    ).covered_through_message_id == "message-3"
    assert len(messages.list_for_session(user_id="user-a", session_id="session-compression")) == 5


def test_compressor_uses_coverage_cursor_to_avoid_duplicate_summaries(tmp_path) -> None:
    _, messages, summaries, _ = make_components(tmp_path)
    append_messages(messages, 5)
    compressor = make_compressor(messages, summaries)

    first = compressor.compress(user_id="user-a", session_id="session-compression")
    repeated = compressor.compress(user_id="user-a", session_id="session-compression")
    append_messages(messages, 2, start=6)
    incremental = compressor.compress(user_id="user-a", session_id="session-compression")

    assert first.compressed is True
    assert repeated.compressed is False
    assert incremental.compressed is True
    assert incremental.summary.covered_through_message_id == "message-5"
    assert incremental.summary.content.count("历史消息 1") == 1
    assert "历史消息 5" in incremental.summary.content


def test_conversation_passes_deterministic_summary_to_provider(tmp_path) -> None:
    sessions, messages, summaries, tool_results = make_components(tmp_path)
    append_messages(messages, 3)
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    provider = ScriptedLLMProvider((FinalAnswer("已读取摘要。"),))
    runtime = AgentRuntime(
        provider=provider,
        tool_registry=registry,
        model="scripted-model",
        max_steps=2,
    )
    compressor = make_compressor(messages, summaries)
    builder = ContextBuilder(
        session_repository=sessions,
        message_repository=messages,
        tool_result_repository=tool_results,
        max_messages=2,
        max_tool_results=2,
        compressor=compressor,
    )
    service = ConversationService(
        context_builder=builder,
        message_repository=messages,
        tool_result_repository=tool_results,
        runtime=runtime,
    )

    result = service.respond(
        user_id="user-a",
        session_id="session-compression",
        content="请继续处理。",
    )

    assert result.assistant_message is not None
    assert provider.received_requests[0].session_summary is not None
    assert "历史消息 1" in provider.received_requests[0].session_summary
    assert [message.message_id for message in provider.received_requests[0].messages] == [
        "message-3",
        result.user_message.message_id,
    ]
