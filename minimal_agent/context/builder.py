"""仅从当前 Session 构建最小必要 Context。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..errors import DomainValidationError
from ..models import Message, ToolResult
from ..storage import (
    MessageRepository,
    SessionRepository,
    TodoRepository,
    ToolResultRepository,
)
from .compression import ContextCompressor


@dataclass(frozen=True)
class SessionContext:
    """一次 LLM 请求可使用的 Session 局部历史。"""

    messages: tuple[Message, ...]
    tool_results: tuple[ToolResult, ...]
    summary: str | None = None
    compressed: bool = False
    current_todos: tuple[Mapping[str, Any], ...] = ()


class ContextBuilder:
    """以复合身份读取最近消息和相关工具结果。"""

    def __init__(
        self,
        *,
        session_repository: SessionRepository,
        message_repository: MessageRepository,
        tool_result_repository: ToolResultRepository,
        max_messages: int,
        max_tool_results: int,
        compressor: ContextCompressor | None = None,
        todo_repository: TodoRepository | None = None,
    ) -> None:
        if not isinstance(session_repository, SessionRepository):
            raise DomainValidationError("session_repository 必须是 SessionRepository")
        if not isinstance(message_repository, MessageRepository):
            raise DomainValidationError("message_repository 必须是 MessageRepository")
        if not isinstance(tool_result_repository, ToolResultRepository):
            raise DomainValidationError(
                "tool_result_repository 必须是 ToolResultRepository"
            )
        if todo_repository is not None and not isinstance(todo_repository, TodoRepository):
            raise DomainValidationError("todo_repository 必须是 TodoRepository 或 None")
        for name, value in (
            ("max_messages", max_messages),
            ("max_tool_results", max_tool_results),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise DomainValidationError(f"{name} 必须是正整数")

        self._session_repository = session_repository
        self._message_repository = message_repository
        self._tool_result_repository = tool_result_repository
        self._max_messages = max_messages
        self._max_tool_results = max_tool_results
        self._compressor = compressor
        self._todo_repository = todo_repository

    def build(self, *, user_id: str, session_id: str) -> SessionContext:
        """验证 Session 所有权后，按时间顺序返回近期上下文。"""

        self._session_repository.get(user_id=user_id, session_id=session_id)
        compression_result = (
            self._compressor.compress(user_id=user_id, session_id=session_id)
            if self._compressor is not None
            else None
        )
        messages = self._message_repository.list_for_session(
            user_id=user_id,
            session_id=session_id,
        )
        tool_results = self._tool_result_repository.list_recent(
            user_id=user_id,
            session_id=session_id,
            limit=self._max_tool_results,
        )
        message_limit = (
            self._compressor.keep_recent
            if compression_result is not None
            and compression_result.summary is not None
            else self._max_messages
        )
        current_todos = (
            tuple(
                {
                    "todo_id": todo.todo_id,
                    "title": todo.title,
                    "status": todo.status.value,
                    "completed_at": (
                        todo.completed_at.isoformat()
                        if todo.completed_at is not None
                        else None
                    ),
                }
                for todo in self._todo_repository.list_for_session(
                    user_id=user_id,
                    session_id=session_id,
                )
            )
            if self._todo_repository is not None
            else ()
        )
        return SessionContext(
            messages=messages[-message_limit:],
            tool_results=tool_results,
            summary=compression_result.summary.content
            if compression_result is not None and compression_result.summary is not None
            else None,
            compressed=compression_result.compressed
            if compression_result is not None
            else False,
            current_todos=current_todos,
        )
