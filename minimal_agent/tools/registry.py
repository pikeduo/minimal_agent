"""工具注册、Schema 校验和安全调度。"""

from __future__ import annotations

import json
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ..errors import DomainValidationError, ToolExecutionError
from ..models import ToolCall, ToolResult, ToolResultStatus
from .base import Tool, ToolExecutionContext


class ToolRegistry:
    """维护有序工具集合，并将所有执行结果转换为 ToolResult。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    @property
    def tools(self) -> tuple[Tool, ...]:
        """按注册顺序返回工具快照。"""

        return tuple(self._tools.values())

    def register(self, tool: Tool) -> None:
        """注册一个通过元数据和 JSON Schema 检查的工具。"""

        if not isinstance(tool, Tool):
            raise DomainValidationError("注册对象必须实现 Tool 契约")
        self._validate_tool_metadata(tool)
        if tool.name in self._tools:
            raise DomainValidationError(f"工具 {tool.name} 已注册")
        self._tools[tool.name] = tool

    def export_schemas(self) -> tuple[dict[str, Any], ...]:
        """导出可传递给 Provider 的工具元数据与参数 Schema。"""

        return tuple(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": self._copy_json_object(tool.parameters, name="parameters"),
            }
            for tool in self.tools
        )

    def execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolResult:
        """校验并执行 ToolCall，任何可预期失败均转换为安全结果。"""

        if not isinstance(call, ToolCall):
            raise DomainValidationError("call 必须是 ToolCall")
        if not isinstance(context, ToolExecutionContext):
            raise DomainValidationError("context 必须是 ToolExecutionContext")

        tool = self._tools.get(call.name)
        if tool is None:
            return self._error_result(
                call,
                error_code="unknown_tool",
                error_message="请求的工具未注册。",
            )

        try:
            Draft202012Validator(tool.parameters).validate(dict(call.arguments))
        except ValidationError:
            return self._error_result(
                call,
                error_code="invalid_arguments",
                error_message="工具参数不符合要求。",
            )

        try:
            result = tool.execute(call.arguments, context)
            return ToolResult(
                tool_call_id=call.tool_call_id,
                tool_name=tool.name,
                status=ToolResultStatus.SUCCESS,
                result=result,
            )
        except ToolExecutionError as exc:
            return self._error_result(
                call,
                error_code=exc.error_code,
                error_message=exc.safe_message,
            )
        except Exception:
            return self._error_result(
                call,
                error_code="tool_execution_failed",
                error_message="工具执行失败。",
            )

    def _validate_tool_metadata(self, tool: Tool) -> None:
        for field_name in ("name", "description"):
            value = getattr(tool, field_name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"工具 {field_name} 必须是非空字符串")
        self._copy_json_object(tool.parameters, name="parameters")
        try:
            Draft202012Validator.check_schema(tool.parameters)
        except SchemaError as exc:
            raise DomainValidationError("工具 parameters 不是有效的 JSON Schema") from exc

    @staticmethod
    def _copy_json_object(value: Mapping[str, Any], *, name: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise DomainValidationError(f"{name} 必须是 JSON 对象")
        try:
            serialized_value = json.dumps(dict(value), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError(f"{name} 必须可序列化为 JSON") from exc
        return json.loads(serialized_value)

    @staticmethod
    def _error_result(
        call: ToolCall,
        *,
        error_code: str,
        error_message: str,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=call.tool_call_id,
            tool_name=call.name,
            status=ToolResultStatus.ERROR,
            result={},
            error_code=error_code,
            error_message=error_message,
        )
