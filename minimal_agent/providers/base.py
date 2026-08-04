"""Provider 之间共享的内部请求与返回契约。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Union

from ..errors import DomainValidationError
from ..models import (
    FinalAnswer,
    LLMResponse,
    Message,
    ProviderError,
    ToolCallBatch,
    ToolResult,
)


def _require_text(value: str, *, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{name} 必须是非空字符串")


def _copy_json_object(value: Mapping[str, Any], *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{name} 必须是 JSON 对象")
    try:
        serialized_value = json.dumps(dict(value), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{name} 必须可序列化为 JSON") from exc
    return json.loads(serialized_value)


@dataclass(frozen=True)
class LLMRequest:
    """Runtime 传递给 Provider 的供应商无关请求。"""

    model: str
    messages: tuple[Message, ...]
    tool_schemas: tuple[Mapping[str, Any], ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    session_summary: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.model, name="model")
        if not self.messages:
            raise DomainValidationError("messages 不能为空")
        if not all(isinstance(message, Message) for message in self.messages):
            raise DomainValidationError("messages 只能包含 Message")
        copied_schemas = tuple(
            _copy_json_object(schema, name="tool_schemas")
            for schema in self.tool_schemas
        )
        object.__setattr__(self, "tool_schemas", copied_schemas)
        if not all(isinstance(result, ToolResult) for result in self.tool_results):
            raise DomainValidationError("tool_results 只能包含 ToolResult")
        if self.session_summary is not None and (
            not isinstance(self.session_summary, str) or not self.session_summary.strip()
        ):
            raise DomainValidationError("session_summary 必须是非空字符串或 None")

    def to_dict(self) -> dict[str, Any]:
        """生成可供 Provider Adapter 使用的内部数据副本。"""

        return {
            "model": self.model,
            "messages": [message.to_dict() for message in self.messages],
            "tool_schemas": [dict(schema) for schema in self.tool_schemas],
            "tool_results": [result.to_dict() for result in self.tool_results],
            "session_summary": self.session_summary,
        }


ProviderResult = Union[LLMResponse, ProviderError]


class LLMProvider(Protocol):
    """底层模型调用的最小端口，不承担 Runtime 或工具职责。"""

    def complete(self, request: LLMRequest) -> ProviderResult:
        """根据内部请求返回最终答案、工具调用或安全 Provider 错误。"""
