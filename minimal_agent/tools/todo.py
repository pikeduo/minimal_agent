"""待办工具的无持久化接口实现。"""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import ToolExecutionError
from .base import ToolExecutionContext


class TodoTool:
    """通过注入的 TodoService 操作当前用户和会话的待办。"""

    name = "todo"
    description = "添加、查询或完成当前会话中的待办。"
    parameters = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "action": {"const": "add"},
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                },
                "required": ["action", "title"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"action": {"const": "list"}},
                "required": ["action"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "complete"},
                    "todo_id": {"type": "string", "minLength": 1, "maxLength": 100},
                },
                "required": ["action", "todo_id"],
                "additionalProperties": False,
            },
        ]
    }

    def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        """仅调用已注入服务；阶段 4 不提供 SQLite 实现。"""

        if context.todo_service is None:
            raise ToolExecutionError(
                "todo_service_unavailable",
                "待办持久化服务尚未配置。",
            )

        action = arguments.get("action")
        if action == "add":
            return context.todo_service.add(
                context.user_id,
                context.session_id,
                arguments["title"],
            )
        if action == "list":
            return context.todo_service.list(context.user_id, context.session_id)
        if action == "complete":
            return context.todo_service.complete(
                context.user_id,
                context.session_id,
                arguments["todo_id"],
            )
        raise ToolExecutionError("invalid_todo_action", "待办操作不受支持。")
