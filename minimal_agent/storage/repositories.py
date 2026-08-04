"""按用户和会话双重授权的 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from ..errors import DomainValidationError, ResourceNotFoundError
from ..models import Message, MessageRole, Session, ToolResult, ToolResultStatus
from .database import SQLiteDatabase
from .entities import TodoItem, TodoStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _require_text(value: str, *, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} 必须是非空字符串")


class _OwnershipRepository:
    """为各仓储提供统一的会话所有权检查。"""

    def __init__(self, database: SQLiteDatabase) -> None:
        if not isinstance(database, SQLiteDatabase):
            raise DomainValidationError("database 必须是 SQLiteDatabase")
        self._database = database

    @staticmethod
    def _require_owned_session(
        connection: sqlite3.Connection,
        *,
        user_id: str,
        session_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT id, user_id, title, created_at
            FROM sessions
            WHERE id = ? AND user_id = ?
            """,
            (session_id, user_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("会话不存在或无权访问。")
        return row


class SessionRepository(_OwnershipRepository):
    """创建、查询并按用户隔离 Session。"""

    def create(
        self,
        *,
        user_id: str,
        title: str,
        session_id: str | None = None,
    ) -> Session:
        """为用户创建独立 Session，并确保用户记录存在。"""

        _require_text(user_id, name="user_id")
        _require_text(title, name="title")
        resolved_session_id = session_id or str(uuid4())
        _require_text(resolved_session_id, name="session_id")
        now = _utc_now()
        with self._database.connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO users (id, display_name, created_at)
                VALUES (?, '', ?)
                """,
                (user_id, _timestamp(now)),
            )
            try:
                connection.execute(
                    """
                    INSERT INTO sessions (id, user_id, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_session_id,
                        user_id,
                        title,
                        _timestamp(now),
                        _timestamp(now),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainValidationError("session_id 已存在") from exc
        return Session(resolved_session_id, user_id, title, now)

    def get(self, *, user_id: str, session_id: str) -> Session:
        """获取当前用户拥有的 Session；跨用户访问统一拒绝。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        with self._database.connection() as connection:
            row = self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
        return Session(
            session_id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            created_at=_parse_timestamp(row["created_at"]),
        )

    def list_for_user(self, *, user_id: str) -> tuple[Session, ...]:
        """按最近更新时间列出当前用户的 Session。"""

        _require_text(user_id, name="user_id")
        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, user_id, title, created_at
                FROM sessions
                WHERE user_id = ?
                ORDER BY updated_at DESC, id ASC
                """,
                (user_id,),
            ).fetchall()
        return tuple(
            Session(
                session_id=row["id"],
                user_id=row["user_id"],
                title=row["title"],
                created_at=_parse_timestamp(row["created_at"]),
            )
            for row in rows
        )


class MessageRepository(_OwnershipRepository):
    """追加并读取属于当前用户和 Session 的消息。"""

    def append(self, message: Message) -> None:
        """持久化一条已归属的内部消息。"""

        if not isinstance(message, Message):
            raise DomainValidationError("message 必须是 Message")
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=message.user_id,
                session_id=message.session_id,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO messages (
                        id, session_id, user_id, run_id, role, content, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.message_id,
                        message.session_id,
                        message.user_id,
                        message.run_id,
                        message.role.value,
                        message.content,
                        _timestamp(message.created_at),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainValidationError("message_id 已存在") from exc
            connection.execute(
                """
                UPDATE sessions SET updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (_timestamp(_utc_now()), message.session_id, message.user_id),
            )

    def list_for_session(self, *, user_id: str, session_id: str) -> tuple[Message, ...]:
        """读取当前用户和 Session 的全部消息。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            rows = connection.execute(
                """
                SELECT id, user_id, session_id, role, content, created_at, run_id
                FROM messages
                WHERE user_id = ? AND session_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (user_id, session_id),
            ).fetchall()
        return tuple(
            Message(
                message_id=row["id"],
                user_id=row["user_id"],
                session_id=row["session_id"],
                role=MessageRole(row["role"]),
                content=row["content"],
                created_at=_parse_timestamp(row["created_at"]),
                run_id=row["run_id"],
            )
            for row in rows
        )


class TodoRepository(_OwnershipRepository):
    """以用户和 Session 双重边界持久化 Todo。"""

    def add(
        self,
        *,
        user_id: str,
        session_id: str,
        title: str,
        todo_id: str | None = None,
    ) -> TodoItem:
        """为当前 Session 新增一个未完成待办。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        _require_text(title, name="title")
        resolved_todo_id = todo_id or str(uuid4())
        _require_text(resolved_todo_id, name="todo_id")
        now = _utc_now()
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO todos (
                        id, user_id, session_id, title, status, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        resolved_todo_id,
                        user_id,
                        session_id,
                        title,
                        TodoStatus.OPEN.value,
                        _timestamp(now),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainValidationError("todo_id 已存在") from exc
            self._touch_session(connection, user_id=user_id, session_id=session_id)
        return TodoItem(
            todo_id=resolved_todo_id,
            user_id=user_id,
            session_id=session_id,
            title=title,
            status=TodoStatus.OPEN,
            created_at=now,
            completed_at=None,
        )

    def list_for_session(self, *, user_id: str, session_id: str) -> tuple[TodoItem, ...]:
        """列出当前用户当前 Session 的所有待办。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            rows = connection.execute(
                """
                SELECT id, user_id, session_id, title, status, created_at, completed_at
                FROM todos
                WHERE user_id = ? AND session_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (user_id, session_id),
            ).fetchall()
        return tuple(self._todo_from_row(row) for row in rows)

    def complete(
        self,
        *,
        user_id: str,
        session_id: str,
        todo_id: str,
    ) -> TodoItem:
        """完成当前用户当前 Session 的一项待办，重复完成保持幂等。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        _require_text(todo_id, name="todo_id")
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            row = connection.execute(
                """
                SELECT id, user_id, session_id, title, status, created_at, completed_at
                FROM todos
                WHERE id = ? AND user_id = ? AND session_id = ?
                """,
                (todo_id, user_id, session_id),
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("待办不存在或不属于当前会话。")
            current_todo = self._todo_from_row(row)
            if current_todo.status is TodoStatus.COMPLETED:
                return current_todo

            completed_at = _utc_now()
            connection.execute(
                """
                UPDATE todos
                SET status = ?, completed_at = ?
                WHERE id = ? AND user_id = ? AND session_id = ?
                """,
                (
                    TodoStatus.COMPLETED.value,
                    _timestamp(completed_at),
                    todo_id,
                    user_id,
                    session_id,
                ),
            )
            self._touch_session(connection, user_id=user_id, session_id=session_id)
        return TodoItem(
            todo_id=current_todo.todo_id,
            user_id=current_todo.user_id,
            session_id=current_todo.session_id,
            title=current_todo.title,
            status=TodoStatus.COMPLETED,
            created_at=current_todo.created_at,
            completed_at=completed_at,
        )

    @staticmethod
    def _todo_from_row(row: sqlite3.Row) -> TodoItem:
        return TodoItem(
            todo_id=row["id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            title=row["title"],
            status=TodoStatus(row["status"]),
            created_at=_parse_timestamp(row["created_at"]),
            completed_at=_parse_timestamp(row["completed_at"]),
        )

    @staticmethod
    def _touch_session(
        connection: sqlite3.Connection,
        *,
        user_id: str,
        session_id: str,
    ) -> None:
        connection.execute(
            """
            UPDATE sessions SET updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (_timestamp(_utc_now()), session_id, user_id),
        )


class ToolResultRepository(_OwnershipRepository):
    """持久化当前 Session 的必要工具结果，供后续追问召回。"""

    def append_for_run(
        self,
        *,
        user_id: str,
        session_id: str,
        run_id: str,
        tool_results: tuple[ToolResult, ...],
    ) -> None:
        """保存一次 Run 产生的工具结果，不保存供应商原始响应。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        _require_text(run_id, name="run_id")
        if not all(isinstance(result, ToolResult) for result in tool_results):
            raise DomainValidationError("tool_results 只能包含 ToolResult")
        if not tool_results:
            return

        now = _timestamp(_utc_now())
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            connection.executemany(
                """
                INSERT INTO tool_results (
                    tool_call_id, run_id, user_id, session_id, tool_name, status,
                    result_json, error_code, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        result.tool_call_id,
                        run_id,
                        user_id,
                        session_id,
                        result.tool_name,
                        result.status.value,
                        json.dumps(result.result, ensure_ascii=False),
                        result.error_code,
                        result.error_message,
                        now,
                    )
                    for result in tool_results
                ),
            )

    def list_recent(
        self,
        *,
        user_id: str,
        session_id: str,
        limit: int,
    ) -> tuple[ToolResult, ...]:
        """按发生顺序读取当前 Session 最近的工具结果。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise DomainValidationError("limit 必须是正整数")

        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            rows = connection.execute(
                """
                SELECT tool_call_id, tool_name, status, result_json, error_code, error_message
                FROM tool_results
                WHERE user_id = ? AND session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, session_id, limit),
            ).fetchall()

        return tuple(
            ToolResult(
                tool_call_id=row["tool_call_id"],
                tool_name=row["tool_name"],
                status=ToolResultStatus(row["status"]),
                result=json.loads(row["result_json"]),
                error_code=row["error_code"],
                error_message=row["error_message"],
            )
            for row in reversed(rows)
        )
