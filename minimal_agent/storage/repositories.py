"""按用户和会话双重授权的 SQLite 仓储。"""

from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

from ..auth import hash_session_token
from ..errors import DomainValidationError, ResourceNotFoundError
from ..models import Message, MessageRole, Session, ToolResult, ToolResultStatus
from .database import SQLiteDatabase
from .entities import SessionSummary, TodoItem, TodoStatus, User


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


class UserRepository(_OwnershipRepository):
    """保存注册用户与密码哈希，Web 层永不取得原始密码。"""

    def create(self, *, username: str, password_hash: str) -> User:
        """创建用户名唯一的本地用户。"""

        _require_text(username, name="username")
        _require_text(password_hash, name="password_hash")
        user_id = str(uuid4())
        created_at = _utc_now()
        with self._database.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (id, display_name, username, password_hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, username, username, password_hash, _timestamp(created_at)),
                )
            except sqlite3.IntegrityError as exc:
                raise DomainValidationError("用户名已被使用") from exc
        return User(user_id=user_id, username=username, created_at=created_at)

    def get_authentication(self, *, username: str) -> tuple[User, str] | None:
        """读取认证所需的用户和密码哈希，仅供登录校验使用。"""

        _require_text(username, name="username")
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT id, username, password_hash, created_at
                FROM users
                WHERE username = ? AND password_hash IS NOT NULL
                """,
                (username,),
            ).fetchone()
        if row is None:
            return None
        return (
            User(
                user_id=row["id"],
                username=row["username"],
                created_at=_parse_timestamp(row["created_at"]),
            ),
            row["password_hash"],
        )

    def get(self, *, user_id: str) -> User | None:
        """读取已注册用户的安全展示信息。"""

        _require_text(user_id, name="user_id")
        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT id, username, created_at
                FROM users
                WHERE id = ? AND username IS NOT NULL AND password_hash IS NOT NULL
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return User(
            user_id=row["id"],
            username=row["username"],
            created_at=_parse_timestamp(row["created_at"]),
        )


class AuthSessionRepository(_OwnershipRepository):
    """保存可撤销的服务端登录会话，不在数据库保存原始 Token。"""

    def create(self, *, user_id: str, token: str, expires_at: datetime) -> None:
        """创建登录会话，仅保存 Token 的 SHA-256 摘要。"""

        _require_text(user_id, name="user_id")
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise DomainValidationError("expires_at 必须包含时区信息")
        with self._database.connection() as connection:
            if connection.execute(
                "SELECT 1 FROM users WHERE id = ?", (user_id,)
            ).fetchone() is None:
                raise ResourceNotFoundError("用户不存在。")
            connection.execute(
                """
                INSERT INTO auth_sessions (token_hash, user_id, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    hash_session_token(token),
                    user_id,
                    _timestamp(expires_at),
                    _timestamp(_utc_now()),
                ),
            )

    def get_user_id(self, *, token: str) -> str | None:
        """验证会话 Token，清理过期记录并返回所属用户 ID。"""

        if not isinstance(token, str) or not token.strip():
            return None
        now = _utc_now()
        with self._database.connection() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?",
                (_timestamp(now),),
            )
            row = connection.execute(
                """
                SELECT user_id FROM auth_sessions
                WHERE token_hash = ? AND expires_at > ?
                """,
                (hash_session_token(token), _timestamp(now)),
            ).fetchone()
        return row["user_id"] if row is not None else None

    def delete(self, *, token: str) -> None:
        """撤销指定浏览器的登录会话，未知 Token 保持幂等。"""

        if not isinstance(token, str) or not token.strip():
            return
        with self._database.connection() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?",
                (hash_session_token(token),),
            )


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

    def delete(self, *, user_id: str, session_id: str) -> None:
        """删除当前用户的会话及其关联消息、工具结果、摘要和待办。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            for table_name in (
                "messages",
                "tool_results",
                "session_summaries",
                "todos",
            ):
                connection.execute(
                    f"DELETE FROM {table_name} WHERE user_id = ? AND session_id = ?",
                    (user_id, session_id),
                )
            connection.execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )

    def update_title_if_empty(
        self,
        *,
        user_id: str,
        session_id: str,
        title: str,
    ) -> bool:
        """仅为尚无消息的会话写入首条用户消息作为标题。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        _require_text(title, name="title")
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            result = connection.execute(
                """
                UPDATE sessions
                SET title = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM messages
                    WHERE user_id = ? AND session_id = ?
                  )
                """,
                (
                    title,
                    _timestamp(_utc_now()),
                    session_id,
                    user_id,
                    user_id,
                    session_id,
                ),
            )
        return result.rowcount == 1

    def delete_if_empty(self, *, user_id: str, session_id: str) -> bool:
        """仅删除没有聊天消息的新会话，并同步清理其关联数据。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            has_messages = connection.execute(
                """
                SELECT 1 FROM messages
                WHERE user_id = ? AND session_id = ?
                LIMIT 1
                """,
                (user_id, session_id),
            ).fetchone()
            if has_messages is not None:
                return False
            for table_name in (
                "messages",
                "tool_results",
                "session_summaries",
                "todos",
            ):
                connection.execute(
                    f"DELETE FROM {table_name} WHERE user_id = ? AND session_id = ?",
                    (user_id, session_id),
                )
            result = connection.execute(
                "DELETE FROM sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
        return result.rowcount == 1


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

    def get_user_message(
        self,
        *,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> Message:
        """读取当前会话中的一条用户消息，供失败后安全重试。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        _require_text(message_id, name="message_id")
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            row = connection.execute(
                """
                SELECT id, user_id, session_id, role, content, created_at, run_id
                FROM messages
                WHERE id = ? AND user_id = ? AND session_id = ? AND role = ?
                """,
                (message_id, user_id, session_id, MessageRole.USER.value),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("未找到可重新发送的用户消息。")
        return Message(
            message_id=row["id"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            role=MessageRole(row["role"]),
            content=row["content"],
            created_at=_parse_timestamp(row["created_at"]),
            run_id=row["run_id"],
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


class SessionSummaryRepository(_OwnershipRepository):
    """读取和更新 Session 压缩摘要及其覆盖游标。"""

    def get(self, *, user_id: str, session_id: str) -> SessionSummary | None:
        """获取当前用户当前 Session 的摘要。"""

        _require_text(user_id, name="user_id")
        _require_text(session_id, name="session_id")
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=user_id,
                session_id=session_id,
            )
            row = connection.execute(
                """
                SELECT user_id, session_id, content, covered_through_message_id, updated_at
                FROM session_summaries
                WHERE user_id = ? AND session_id = ?
                """,
                (user_id, session_id),
            ).fetchone()
        if row is None:
            return None
        return SessionSummary(
            user_id=row["user_id"],
            session_id=row["session_id"],
            content=row["content"],
            covered_through_message_id=row["covered_through_message_id"],
            updated_at=_parse_timestamp(row["updated_at"]),
        )

    def save(self, summary: SessionSummary) -> None:
        """保存摘要并原子更新其覆盖到的最后消息 ID。"""

        if not isinstance(summary, SessionSummary):
            raise DomainValidationError("summary 必须是 SessionSummary")
        with self._database.connection() as connection:
            self._require_owned_session(
                connection,
                user_id=summary.user_id,
                session_id=summary.session_id,
            )
            connection.execute(
                """
                INSERT INTO session_summaries (
                    session_id, user_id, content, covered_through_message_id, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    content = excluded.content,
                    covered_through_message_id = excluded.covered_through_message_id,
                    updated_at = excluded.updated_at
                """,
                (
                    summary.session_id,
                    summary.user_id,
                    summary.content,
                    summary.covered_through_message_id,
                    _timestamp(summary.updated_at),
                ),
            )
