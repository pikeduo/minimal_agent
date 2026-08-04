"""仅从当前 Session 构建最小必要 Context。"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import DomainValidationError
from ..models import Message, ToolResult
from ..storage import MessageRepository, SessionRepository, ToolResultRepository
from .compression import ContextCompressor


@dataclass(frozen=True)
class SessionContext:
    """一次 LLM 请求可使用的 Session 局部历史。"""

    messages: tuple[Message, ...]
    tool_results: tuple[ToolResult, ...]
    summary: str | None = None
    compressed: bool = False


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
    ) -> None:
        if not isinstance(session_repository, SessionRepository):
            raise DomainValidationError("session_repository 必须是 SessionRepository")
        if not isinstance(message_repository, MessageRepository):
            raise DomainValidationError("message_repository 必须是 MessageRepository")
        if not isinstance(tool_result_repository, ToolResultRepository):
            raise DomainValidationError(
                "tool_result_repository 必须是 ToolResultRepository"
            )
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
        return SessionContext(
            messages=messages[-self._max_messages :],
            tool_results=tool_results,
            summary=compression_result.summary.content
            if compression_result is not None and compression_result.summary is not None
            else None,
            compressed=compression_result.compressed
            if compression_result is not None
            else False,
        )
