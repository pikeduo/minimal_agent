"""不依赖供应商 SDK 的内部领域模型。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Union

from .errors import DomainValidationError, InvalidStateTransition


DECISION_SUMMARY_MAX_LENGTH = 500


def _require_text(value: str, *, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} 必须是非空字符串")


def _validate_summary(value: str | None) -> None:
    if value is None:
        return
    _require_text(value, name="decision_summary")
    if len(value) > DECISION_SUMMARY_MAX_LENGTH:
        raise DomainValidationError(
            f"decision_summary 长度不能超过 {DECISION_SUMMARY_MAX_LENGTH} 个字符"
        )


def _validate_timestamp(value: datetime, *, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(f"{name} 必须包含时区信息")


def _copy_json_object(value: Mapping[str, Any], *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{name} 必须是 JSON 对象")
    try:
        serialized_value = json.dumps(dict(value), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{name} 必须可序列化为 JSON") from exc
    return json.loads(serialized_value)


def _timestamp() -> datetime:
    return datetime.now(timezone.utc)


class MessageRole(str, Enum):
    """内部 Context 支持的消息角色。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class RunStatus(str, Enum):
    """一次 Agent Run 的生命周期状态。"""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    MAX_STEPS = "max_steps"


class ToolResultStatus(str, Enum):
    """工具调用的结构化结果状态。"""

    SUCCESS = "success"
    ERROR = "error"


class ProviderErrorKind(str, Enum):
    """Provider 错误的稳定分类。"""

    AUTHENTICATION = "authentication"
    INCOMPLETE_RESPONSE = "incomplete_response"
    INVALID_RESPONSE = "invalid_response"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Session:
    """属于单个用户的会话元数据。"""

    session_id: str
    user_id: str
    title: str
    created_at: datetime = field(default_factory=_timestamp)

    def __post_init__(self) -> None:
        _require_text(self.session_id, name="session_id")
        _require_text(self.user_id, name="user_id")
        _require_text(self.title, name="title")
        _validate_timestamp(self.created_at, name="created_at")

    def to_dict(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "title": self.title,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class Message:
    """与用户和会话绑定的一条内部消息。"""

    message_id: str
    user_id: str
    session_id: str
    role: MessageRole
    content: str
    created_at: datetime = field(default_factory=_timestamp)
    run_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.message_id, name="message_id")
        _require_text(self.user_id, name="user_id")
        _require_text(self.session_id, name="session_id")
        if not isinstance(self.role, MessageRole):
            raise DomainValidationError("role 必须是 MessageRole 枚举值")
        _require_text(self.content, name="content")
        _validate_timestamp(self.created_at, name="created_at")
        if self.run_id is not None:
            _require_text(self.run_id, name="run_id")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "message_id": self.message_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "role": self.role.value,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "run_id": self.run_id,
        }


@dataclass(frozen=True)
class AgentRun:
    """一次 Agent 执行的身份、步骤和显式状态。"""

    run_id: str
    user_id: str
    session_id: str
    status: RunStatus = RunStatus.CREATED
    step: int = 0

    def __post_init__(self) -> None:
        _require_text(self.run_id, name="run_id")
        _require_text(self.user_id, name="user_id")
        _require_text(self.session_id, name="session_id")
        if not isinstance(self.status, RunStatus):
            raise DomainValidationError("status 必须是 RunStatus 枚举值")
        if not isinstance(self.step, int) or isinstance(self.step, bool) or self.step < 0:
            raise DomainValidationError("step 必须是非负整数")

    def start(self) -> AgentRun:
        """将新建 Run 转换为可执行状态。"""

        if self.status is not RunStatus.CREATED:
            raise InvalidStateTransition("只有新建状态的 Run 可以启动")
        return replace(self, status=RunStatus.RUNNING)

    def next_step(self) -> AgentRun:
        """推进一个已启动的执行步骤。"""

        if self.status is not RunStatus.RUNNING:
            raise InvalidStateTransition("只有运行中的 Run 可以推进步骤")
        return replace(self, step=self.step + 1)

    def finish(self, status: RunStatus) -> AgentRun:
        """以一个终止状态结束正在运行的 Run。"""

        terminal_statuses = {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.MAX_STEPS,
        }
        if self.status is not RunStatus.RUNNING:
            raise InvalidStateTransition("只有运行中的 Run 可以结束")
        if status not in terminal_statuses:
            raise InvalidStateTransition("Run 必须以终止状态结束")
        return replace(self, status=status)

    def to_dict(self) -> dict[str, str | int]:
        return {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "status": self.status.value,
            "step": self.step,
        }


@dataclass(frozen=True)
class ToolCall:
    """由 Provider 转换出的单个结构化工具调用。"""

    tool_call_id: str
    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_text(self.tool_call_id, name="tool_call_id")
        _require_text(self.name, name="name")
        object.__setattr__(
            self,
            "arguments",
            _copy_json_object(self.arguments, name="arguments"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "arguments": dict(self.arguments),
        }


@dataclass(frozen=True)
class ToolResult:
    """工具执行后的结构化成功或安全错误结果。"""

    tool_call_id: str
    tool_name: str
    status: ToolResultStatus
    result: Mapping[str, Any]
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.tool_call_id, name="tool_call_id")
        _require_text(self.tool_name, name="tool_name")
        if not isinstance(self.status, ToolResultStatus):
            raise DomainValidationError("status 必须是 ToolResultStatus 枚举值")
        object.__setattr__(
            self,
            "result",
            _copy_json_object(self.result, name="result"),
        )

        if self.status is ToolResultStatus.SUCCESS:
            if self.error_code is not None or self.error_message is not None:
                raise DomainValidationError("成功的工具结果不能包含错误信息")
            return

        if self.error_code is None or self.error_message is None:
            raise DomainValidationError("失败的工具结果必须包含错误代码和安全错误信息")
        _require_text(self.error_code, name="error_code")
        _require_text(self.error_message, name="error_message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "result": dict(self.result),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class FinalAnswer:
    """Provider 已完成工具决策后的最终用户可见答案。"""

    content: str
    decision_summary: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.content, name="content")
        _validate_summary(self.decision_summary)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "type": "final_answer",
            "content": self.content,
            "decision_summary": self.decision_summary,
        }


@dataclass(frozen=True)
class ToolCallBatch:
    """Provider 在一次响应中请求的一组工具调用。"""

    calls: tuple[ToolCall, ...]
    decision_summary: str | None = None

    def __post_init__(self) -> None:
        if not self.calls:
            raise DomainValidationError("工具调用批次不能为空")
        if not all(isinstance(call, ToolCall) for call in self.calls):
            raise DomainValidationError("工具调用批次只能包含 ToolCall")
        call_ids = [call.tool_call_id for call in self.calls]
        if len(call_ids) != len(set(call_ids)):
            raise DomainValidationError("同一工具调用批次的 ID 必须唯一")
        _validate_summary(self.decision_summary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tool_call_batch",
            "calls": [call.to_dict() for call in self.calls],
            "decision_summary": self.decision_summary,
        }


@dataclass(frozen=True)
class ProviderError:
    """可记录、可展示安全摘要的 Provider 失败结果。"""

    kind: ProviderErrorKind
    safe_message: str
    retryable: bool

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProviderErrorKind):
            raise DomainValidationError("kind 必须是 ProviderErrorKind 枚举值")
        _require_text(self.safe_message, name="safe_message")
        if not isinstance(self.retryable, bool):
            raise DomainValidationError("retryable 必须是布尔值")

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "kind": self.kind.value,
            "safe_message": self.safe_message,
            "retryable": self.retryable,
        }


LLMResponse = Union[FinalAnswer, ToolCallBatch]
