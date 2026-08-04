from __future__ import annotations

from datetime import datetime, timezone

import pytest

from minimal_agent.errors import ResourceNotFoundError
from minimal_agent.models import Message, MessageRole, ToolCall, ToolResultStatus
from minimal_agent.storage import (
    MessageRepository,
    SessionRepository,
    SQLiteDatabase,
    SQLiteTodoService,
    TodoRepository,
    TodoStatus,
)
from minimal_agent.tools import TodoTool, ToolExecutionContext, ToolRegistry


@pytest.fixture
def repositories(tmp_path):
    database = SQLiteDatabase(tmp_path / "minimal_agent.sqlite3")
    database.initialize()
    return (
        SessionRepository(database),
        MessageRepository(database),
        TodoRepository(database),
    )


def test_sessions_and_messages_are_isolated_by_user_and_session(repositories) -> None:
    sessions, messages, _ = repositories
    window_one = sessions.create(
        user_id="user-a",
        title="天气计划",
        session_id="session-weather",
    )
    window_two = sessions.create(
        user_id="user-a",
        title="周报计划",
        session_id="session-report",
    )
    messages.append(
        Message(
            message_id="message-weather",
            user_id="user-a",
            session_id=window_one.session_id,
            role=MessageRole.USER,
            content="厦门天气如何？",
            created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
    )
    messages.append(
        Message(
            message_id="message-report",
            user_id="user-a",
            session_id=window_two.session_id,
            role=MessageRole.USER,
            content="写周报。",
            created_at=datetime(2026, 8, 4, 1, tzinfo=timezone.utc),
        )
    )

    assert [message.content for message in messages.list_for_session(
        user_id="user-a", session_id=window_one.session_id
    )] == ["厦门天气如何？"]
    assert [message.content for message in messages.list_for_session(
        user_id="user-a", session_id=window_two.session_id
    )] == ["写周报。"]
    assert {session.session_id for session in sessions.list_for_user(user_id="user-a")} == {
        "session-weather",
        "session-report",
    }

    with pytest.raises(ResourceNotFoundError):
        sessions.get(user_id="user-b", session_id=window_one.session_id)
    with pytest.raises(ResourceNotFoundError):
        messages.list_for_session(user_id="user-b", session_id=window_one.session_id)


def test_todo_repository_crud_and_session_isolation(repositories) -> None:
    sessions, _, todos = repositories
    sessions.create(user_id="user-a", title="窗口一", session_id="session-one")
    sessions.create(user_id="user-a", title="窗口二", session_id="session-two")
    sessions.create(user_id="user-b", title="窗口三", session_id="session-three")
    first_todo = todos.add(
        user_id="user-a",
        session_id="session-one",
        title="查询天气",
        todo_id="todo-weather",
    )
    todos.add(
        user_id="user-a",
        session_id="session-two",
        title="提交周报",
        todo_id="todo-report",
    )

    completed_todo = todos.complete(
        user_id="user-a",
        session_id="session-one",
        todo_id=first_todo.todo_id,
    )

    assert completed_todo.status is TodoStatus.COMPLETED
    assert completed_todo.completed_at is not None
    assert [todo.title for todo in todos.list_for_session(
        user_id="user-a", session_id="session-one"
    )] == ["查询天气"]
    assert [todo.title for todo in todos.list_for_session(
        user_id="user-a", session_id="session-two"
    )] == ["提交周报"]
    with pytest.raises(ResourceNotFoundError):
        todos.list_for_session(user_id="user-b", session_id="session-one")
    with pytest.raises(ResourceNotFoundError):
        todos.complete(
            user_id="user-b",
            session_id="session-three",
            todo_id="todo-weather",
        )


def test_todo_tool_performs_real_sqlite_crud_through_injected_service(repositories) -> None:
    sessions, _, todos = repositories
    sessions.create(user_id="user-a", title="周报", session_id="session-report")
    registry = ToolRegistry()
    registry.register(TodoTool())
    context = ToolExecutionContext(
        user_id="user-a",
        session_id="session-report",
        todo_service=SQLiteTodoService(todos),
    )

    added = registry.execute(
        ToolCall("call-add", "todo", {"action": "add", "title": "提交周报"}),
        context,
    )
    listed = registry.execute(ToolCall("call-list", "todo", {"action": "list"}), context)
    todo_id = added.result["todo"]["todo_id"]
    completed = registry.execute(
        ToolCall("call-complete", "todo", {"action": "complete", "todo_id": todo_id}),
        context,
    )

    assert added.status is ToolResultStatus.SUCCESS
    assert listed.result["todos"][0]["title"] == "提交周报"
    assert completed.result["todo"]["status"] == "completed"


def test_persisted_data_is_available_after_recreating_repositories(tmp_path) -> None:
    path = tmp_path / "minimal_agent.sqlite3"
    database = SQLiteDatabase(path)
    database.initialize()
    first_sessions = SessionRepository(database)
    first_todos = TodoRepository(database)
    first_sessions.create(user_id="user-a", title="长期会话", session_id="session-long")
    first_todos.add(
        user_id="user-a",
        session_id="session-long",
        title="继续跟进",
        todo_id="todo-long",
    )

    reopened_database = SQLiteDatabase(path)
    reopened_database.initialize()
    reopened_todos = TodoRepository(reopened_database)

    assert [todo.todo_id for todo in reopened_todos.list_for_session(
        user_id="user-a", session_id="session-long"
    )] == ["todo-long"]
