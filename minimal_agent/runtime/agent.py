"""由内部 Provider 和工具注册表驱动的最小 Agent Loop。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
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
from ..tracing import JsonlTraceRecorder
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
        trace_recorder: JsonlTraceRecorder | None = None,
    ) -> None:
        if not callable(getattr(provider, "complete", None)):
            raise DomainValidationError("provider 必须提供 complete 方法")
        if not isinstance(tool_registry, ToolRegistry):
            raise DomainValidationError("tool_registry 必须是 ToolRegistry")
        if not isinstance(model, str) or not model.strip():
            raise DomainValidationError("model 必须是非空字符串")
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
            raise DomainValidationError("max_steps 必须是正整数")
        if trace_recorder is not None and not isinstance(
            trace_recorder, JsonlTraceRecorder
        ):
            raise DomainValidationError("trace_recorder 必须是 JsonlTraceRecorder 或 None")

        self._provider = provider
        self._tool_registry = tool_registry
        self._model = model
        self._max_steps = max_steps
        self._todo_service = todo_service
        self._trace_recorder = trace_recorder

    def run(
        self,
        *,
        user_id: str,
        session_id: str,
        messages: tuple[Message, ...],
        run_id: str | None = None,
        historical_tool_results: tuple[ToolResult, ...] = (),
        session_summary: str | None = None,
        context_compressed: bool = False,
        current_todos: tuple[Mapping[str, Any], ...] = (),
        provider_override: LLMProvider | None = None,
    ) -> RuntimeResult:
        """运行 Provider—工具循环，直到最终回答、上限或安全失败。"""

        self._validate_messages(user_id=user_id, session_id=session_id, messages=messages)
        if not all(isinstance(result, ToolResult) for result in historical_tool_results):
            raise DomainValidationError("historical_tool_results 只能包含 ToolResult")
        if session_summary is not None and (
            not isinstance(session_summary, str) or not session_summary.strip()
        ):
            raise DomainValidationError("session_summary 必须是非空字符串或 None")
        if not isinstance(context_compressed, bool):
            raise DomainValidationError("context_compressed 必须是布尔值")
        if not all(isinstance(todo, Mapping) for todo in current_todos):
            raise DomainValidationError("current_todos 只能包含 JSON 对象")
        if provider_override is not None and not callable(
            getattr(provider_override, "complete", None)
        ):
            raise DomainValidationError("provider_override 必须提供 complete 方法或 None")
        active_provider = (
            provider_override if provider_override is not None else self._provider
        )
        active_run = AgentRun(
            run_id=run_id or str(uuid4()),
            user_id=user_id,
            session_id=session_id,
        ).start()
        self._trace(
            event="run.started",
            run=active_run,
            data={"model": self._model, "max_steps": self._max_steps},
        )
        self._trace(
            event="session.loaded",
            run=active_run,
            data={"user_id": user_id, "session_id": session_id},
        )
        self._trace(
            event="context.built",
            run=active_run,
            data={
                "message_count": len(messages),
                "historical_tool_result_count": len(historical_tool_results),
                "has_summary": session_summary is not None,
                "todo_count": len(current_todos),
            },
        )
        if context_compressed:
            self._trace(event="context.compressed", run=active_run, data={})
        tool_results: list[ToolResult] = []
        tool_call_batches: list[ToolCallBatch] = []
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
                tool_call_batches=tuple(tool_call_batches),
                session_summary=session_summary,
                current_todos=current_todos,
            )
            self._trace(
                event="llm.requested",
                run=active_run,
                data={
                    "step": active_run.step,
                    "message_count": len(request.messages),
                    "tool_names": [
                        schema["name"]
                        for schema in request.tool_schemas
                        if isinstance(schema.get("name"), str)
                    ],
                    "tool_result_count": len(request.tool_results),
                },
            )
            response = self._complete_safely(
                request,
                provider=active_provider,
            )

            if isinstance(response, ProviderError):
                self._trace(
                    event="llm.responded",
                    run=active_run,
                    data={"response_type": "provider_error", "error_kind": response.kind.value},
                )
                failed_run = active_run.finish(RunStatus.FAILED)
                self._trace(
                    event="run.failed",
                    run=failed_run,
                    data={"error_kind": response.kind.value, "retryable": response.retryable},
                )
                return RuntimeResult(
                    run=failed_run,
                    final_answer=None,
                    provider_error=response,
                    tool_results=tuple(tool_results),
                )
            if isinstance(response, FinalAnswer):
                self._trace(
                    event="llm.responded",
                    run=active_run,
                    data={
                        "response_type": "final_answer",
                        "has_decision_summary": response.decision_summary is not None,
                    },
                )
                completed_run = active_run.finish(RunStatus.COMPLETED)
                self._trace(
                    event="run.completed",
                    run=completed_run,
                    data={"answer_length": len(response.content)},
                )
                return RuntimeResult(
                    run=completed_run,
                    final_answer=response,
                    provider_error=None,
                    tool_results=tuple(tool_results),
                )
            if isinstance(response, ToolCallBatch):
                self._trace(
                    event="llm.responded",
                    run=active_run,
                    data={
                        "response_type": "tool_call_batch",
                        "tool_call_count": len(response.calls),
                        "has_decision_summary": response.decision_summary is not None,
                    },
                )
                tool_call_batches.append(response)
                for call in response.calls:
                    self._trace(
                        event="tool.started",
                        run=active_run,
                        data={"tool_call_id": call.tool_call_id, "tool_name": call.name},
                    )
                    tool_result = self._tool_registry.execute(call, tool_context)
                    tool_results.append(tool_result)
                    if tool_result.status.value == "success":
                        self._trace(
                            event="tool.succeeded",
                            run=active_run,
                            data={
                                "tool_call_id": tool_result.tool_call_id,
                                "tool_name": tool_result.tool_name,
                            },
                        )
                    else:
                        self._trace(
                            event="tool.failed",
                            run=active_run,
                            data={
                                "tool_call_id": tool_result.tool_call_id,
                                "tool_name": tool_result.tool_name,
                                "error_code": tool_result.error_code,
                            },
                        )
                continue

            failed_run = active_run.finish(RunStatus.FAILED)
            self._trace(
                event="llm.responded",
                run=active_run,
                data={"response_type": "unsupported"},
            )
            self._trace(
                event="run.failed",
                run=failed_run,
                data={"error_kind": ProviderErrorKind.INVALID_RESPONSE.value},
            )
            return RuntimeResult(
                run=failed_run,
                final_answer=None,
                provider_error=ProviderError(
                    kind=ProviderErrorKind.INVALID_RESPONSE,
                    safe_message="模型服务返回了不支持的结果。",
                    retryable=False,
                ),
                tool_results=tuple(tool_results),
            )

        max_steps_run = active_run.finish(RunStatus.MAX_STEPS)
        self._trace(
            event="run.failed",
            run=max_steps_run,
            data={"reason": "max_steps"},
        )
        return RuntimeResult(
            run=max_steps_run,
            final_answer=None,
            provider_error=None,
            tool_results=tuple(tool_results),
        )

    @staticmethod
    def _complete_safely(
        request: LLMRequest,
        *,
        provider: LLMProvider,
    ) -> ProviderResult:
        try:
            return provider.complete(request)
        except Exception:
            return ProviderError(
                kind=ProviderErrorKind.UNKNOWN,
                safe_message="模型服务调用失败。",
                retryable=True,
            )

    def _trace(self, *, event: str, run: AgentRun, data: dict[str, Any]) -> None:
        """向可选的本地 Trace 写入最小化元数据。"""

        if self._trace_recorder is not None:
            self._trace_recorder.emit(event=event, run_id=run.run_id, data=data)

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
