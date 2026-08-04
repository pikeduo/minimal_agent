"""将消息持久化、Context 构建和 Runtime 调用串联为连续对话。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from ..errors import DomainValidationError
from ..models import Message, MessageRole
from ..runtime import AgentRuntime, RuntimeResult
from ..storage import MessageRepository, ToolResultRepository
from .builder import ContextBuilder, SessionContext


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ConversationResult:
    """一次用户消息处理后保存的消息和 Runtime 结果。"""

    user_message: Message
    assistant_message: Message | None
    context: SessionContext
    runtime_result: RuntimeResult


class ConversationService:
    """最小连续对话编排，不承担 Web 身份认证或 Trace。"""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        message_repository: MessageRepository,
        tool_result_repository: ToolResultRepository,
        runtime: AgentRuntime,
    ) -> None:
        if not isinstance(context_builder, ContextBuilder):
            raise DomainValidationError("context_builder 必须是 ContextBuilder")
        if not isinstance(message_repository, MessageRepository):
            raise DomainValidationError("message_repository 必须是 MessageRepository")
        if not isinstance(tool_result_repository, ToolResultRepository):
            raise DomainValidationError(
                "tool_result_repository 必须是 ToolResultRepository"
            )
        if not isinstance(runtime, AgentRuntime):
            raise DomainValidationError("runtime 必须是 AgentRuntime")

        self._context_builder = context_builder
        self._message_repository = message_repository
        self._tool_result_repository = tool_result_repository
        self._runtime = runtime

    def respond(
        self,
        *,
        user_id: str,
        session_id: str,
        content: str,
    ) -> ConversationResult:
        """保存用户输入、构建 Context、运行 Agent 并保存可见结果。"""

        if not isinstance(content, str) or not content.strip():
            raise DomainValidationError("content 必须是非空字符串")

        user_message = Message(
            message_id=str(uuid4()),
            user_id=user_id,
            session_id=session_id,
            role=MessageRole.USER,
            content=content,
            created_at=_utc_now(),
        )
        self._message_repository.append(user_message)
        context = self._context_builder.build(user_id=user_id, session_id=session_id)
        runtime_result = self._runtime.run(
            user_id=user_id,
            session_id=session_id,
            messages=context.messages,
            historical_tool_results=context.tool_results,
        )
        self._tool_result_repository.append_for_run(
            user_id=user_id,
            session_id=session_id,
            run_id=runtime_result.run.run_id,
            tool_results=runtime_result.tool_results,
        )

        assistant_message = None
        if runtime_result.final_answer is not None:
            assistant_message = Message(
                message_id=str(uuid4()),
                user_id=user_id,
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content=runtime_result.final_answer.content,
                created_at=_utc_now(),
                run_id=runtime_result.run.run_id,
            )
            self._message_repository.append(assistant_message)

        return ConversationResult(
            user_message=user_message,
            assistant_message=assistant_message,
            context=context,
            runtime_result=runtime_result,
        )
