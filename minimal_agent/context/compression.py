"""无需额外 LLM 的确定性 Session 历史压缩。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..errors import DomainValidationError
from ..models import Message, MessageRole
from ..storage import MessageRepository, SessionSummary, SessionSummaryRepository


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CompressionResult:
    """一次压缩尝试的摘要状态与是否产生新覆盖范围。"""

    summary: SessionSummary | None
    compressed: bool


class ContextCompressor:
    """保留最近消息原文，将更早消息追加为确定性基础摘要。"""

    _MAX_MESSAGE_CHARS = 240

    def __init__(
        self,
        *,
        message_repository: MessageRepository,
        summary_repository: SessionSummaryRepository,
        max_context_messages: int,
        keep_recent: int,
    ) -> None:
        if not isinstance(message_repository, MessageRepository):
            raise DomainValidationError("message_repository 必须是 MessageRepository")
        if not isinstance(summary_repository, SessionSummaryRepository):
            raise DomainValidationError("summary_repository 必须是 SessionSummaryRepository")
        for name, value in (
            ("max_context_messages", max_context_messages),
            ("keep_recent", keep_recent),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise DomainValidationError(f"{name} 必须是正整数")
        if keep_recent > max_context_messages:
            raise DomainValidationError("keep_recent 不能超过 max_context_messages")

        self._message_repository = message_repository
        self._summary_repository = summary_repository
        self._max_context_messages = max_context_messages
        self._keep_recent = keep_recent

    @property
    def keep_recent(self) -> int:
        """返回发生压缩后仍以原文保留的最近消息数量。"""

        return self._keep_recent

    def compress(self, *, user_id: str, session_id: str) -> CompressionResult:
        """压缩超过阈值的旧消息，并通过游标避免重复处理。"""

        messages = self._message_repository.list_for_session(
            user_id=user_id,
            session_id=session_id,
        )
        existing_summary = self._summary_repository.get(
            user_id=user_id,
            session_id=session_id,
        )
        if len(messages) <= self._max_context_messages:
            return CompressionResult(summary=existing_summary, compressed=False)

        candidates = messages[: len(messages) - self._keep_recent]
        new_messages = self._messages_after_coverage(
            messages=messages,
            candidates=candidates,
            existing_summary=existing_summary,
        )
        if not new_messages:
            return CompressionResult(summary=existing_summary, compressed=False)

        new_section = self._summarize_messages(new_messages)
        content = (
            f"{existing_summary.content}\n{new_section}"
            if existing_summary is not None
            else new_section
        )
        summary = SessionSummary(
            user_id=user_id,
            session_id=session_id,
            content=content,
            covered_through_message_id=new_messages[-1].message_id,
            updated_at=_utc_now(),
        )
        self._summary_repository.save(summary)
        return CompressionResult(summary=summary, compressed=True)

    @staticmethod
    def _messages_after_coverage(
        *,
        messages: tuple[Message, ...],
        candidates: tuple[Message, ...],
        existing_summary: SessionSummary | None,
    ) -> tuple[Message, ...]:
        if existing_summary is None:
            return candidates
        covered_index = next(
            (
                index
                for index, message in enumerate(messages)
                if message.message_id == existing_summary.covered_through_message_id
            ),
            None,
        )
        if covered_index is None:
            raise DomainValidationError("摘要覆盖游标对应的消息不存在")
        return candidates[covered_index + 1 :]

    def _summarize_messages(self, messages: tuple[Message, ...]) -> str:
        summary_lines = ["会话历史摘要："]
        for message in messages:
            role = self._role_label(message.role)
            content = " ".join(message.content.split())[: self._MAX_MESSAGE_CHARS]
            summary_lines.append(f"- {role}：{content}")
        return "\n".join(summary_lines)

    @staticmethod
    def _role_label(role: MessageRole) -> str:
        labels = {
            MessageRole.SYSTEM: "系统",
            MessageRole.USER: "用户",
            MessageRole.ASSISTANT: "助手",
            MessageRole.TOOL: "工具",
        }
        return labels[role]
