"""由内部 Provider 和工具注册表驱动的最小 Agent Loop。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..errors import DomainValidationError
from ..models import (
    AgentRun,
    FinalAnswer,
    Message,
    ProviderError,
    ProviderErrorKind,
    RunStatus,
    ToolCallBatch,
    ToolResult,
)
from ..providers.base import LLMProvider, LLMRequest, ProviderResult
from ..tools.base import TodoService, ToolExecutionContext
from ..tools.registry import ToolRegistry


@dataclass(frozen=True)
class RuntimeResult:
    """一次最小 Agent Loop 的最终状态及其可观察输出。"""

    run: AgentRun
    final_answer: FinalAnswer | None
    provider_error: ProviderError | None
    tool_results: tuple[ToolResult, ...]

    def __post_init__(self) -> None:
        if self.run.status is RunStatus.COMPLETED and self.final_answer is None:
            raise DomainValidationError("已完成的 Run 必须包含最终答案")
        if self.run.status is RunStatus.FAILED and self.provider_error is None:
            raise DomainValidationError("失败的 Run 必须包含 Provider 错误")
        if self.final_answer is not None and self.provider_error is not None:
            raise DomainValidationError("最终答案和 Provider 错误不能同时存在")


class AgentRuntime:
    """自行实现循环、工具调度和最大步骤限制的最小 Runtime。"""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        model: str,
        max_steps: int,
        todo_service: TodoService | None = None,
    ) -> None:
        if not callable(getattr(provider, "complete", None)):
            raise DomainValidationError("provider 必须提供 complete 方法")
        if not isinstance(tool_registry, ToolRegistry):
            raise DomainValidationError("tool_registry 必须是 ToolRegistry")
        if not isinstance(model, str) or not model.strip():
            raise DomainValidationError("model 必须是非空字符串")
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
            raise DomainValidationError("max_steps 必须是正整数")

        self._provider = provider
        self._tool_registry = tool_registry
        self._model = model
        self._max_steps = max_steps
        self._todo_service = todo_service

    def run(
        self,
        *,
        user_id: str,
        session_id: str,
        messages: tuple[Message, ...],
        run_id: str | None = None,
        historical_tool_results: tuple[ToolResult, ...] = (),
    ) -> RuntimeResult:
        """运行 Provider—工具循环，直到最终回答、上限或安全失败。"""

        self._validate_messages(user_id=user_id, session_id=session_id, messages=messages)
        if not all(isinstance(result, ToolResult) for result in historical_tool_results):
            raise DomainValidationError("historical_tool_results 只能包含 ToolResult")
        active_run = AgentRun(
            run_id=run_id or str(uuid4()),
            user_id=user_id,
            session_id=session_id,
        ).start()
        tool_results: list[ToolResult] = []
        tool_context = ToolExecutionContext(
            user_id=user_id,
            session_id=session_id,
            todo_service=self._todo_service,
        )

        for _ in range(self._max_steps):
            active_run = active_run.next_step()
            request = LLMRequest(
                model=self._model,
                messages=messages,
                tool_schemas=self._tool_registry.export_schemas(),
                tool_results=tuple((*historical_tool_results, *tool_results)),
            )
            response = self._complete_safely(request)

            if isinstance(response, ProviderError):
                return RuntimeResult(
                    run=active_run.finish(RunStatus.FAILED),
                    final_answer=None,
                    provider_error=response,
                    tool_results=tuple(tool_results),
                )
            if isinstance(response, FinalAnswer):
                return RuntimeResult(
                    run=active_run.finish(RunStatus.COMPLETED),
                    final_answer=response,
                    provider_error=None,
                    tool_results=tuple(tool_results),
                )
            if isinstance(response, ToolCallBatch):
                for call in response.calls:
                    tool_results.append(self._tool_registry.execute(call, tool_context))
                continue

            return RuntimeResult(
                run=active_run.finish(RunStatus.FAILED),
                final_answer=None,
                provider_error=ProviderError(
                    kind=ProviderErrorKind.INVALID_RESPONSE,
                    safe_message="模型服务返回了不支持的结果。",
                    retryable=False,
                ),
                tool_results=tuple(tool_results),
            )

        return RuntimeResult(
            run=active_run.finish(RunStatus.MAX_STEPS),
            final_answer=None,
            provider_error=None,
            tool_results=tuple(tool_results),
        )

    def _complete_safely(self, request: LLMRequest) -> ProviderResult:
        try:
            return self._provider.complete(request)
        except Exception:
            return ProviderError(
                kind=ProviderErrorKind.UNKNOWN,
                safe_message="模型服务调用失败。",
                retryable=True,
            )

    @staticmethod
    def _validate_messages(
        *,
        user_id: str,
        session_id: str,
        messages: tuple[Message, ...],
    ) -> None:
        if not messages:
            raise DomainValidationError("messages 不能为空")
        if not all(isinstance(message, Message) for message in messages):
            raise DomainValidationError("messages 只能包含 Message")
        if any(
            message.user_id != user_id or message.session_id != session_id
            for message in messages
        ):
            raise DomainValidationError("messages 必须属于当前用户和会话")
