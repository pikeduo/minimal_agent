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
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "complete"]},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "todo_id": {"type": "string", "minLength": 1, "maxLength": 100},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        """仅调用已注入服务；阶段 4 不提供 SQLite 实现。"""

        action = arguments.get("action")
        if action == "add":
            title = arguments.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ToolExecutionError("invalid_todo_title", "待办标题必须是非空字符串。")
            if context.todo_service is None:
                raise ToolExecutionError(
                    "todo_service_unavailable",
                    "待办持久化服务尚未配置。",
                )
            return context.todo_service.add(
                context.user_id,
                context.session_id,
                title,
            )
        if action == "list":
            if context.todo_service is None:
                raise ToolExecutionError(
                    "todo_service_unavailable",
                    "待办持久化服务尚未配置。",
                )
            return context.todo_service.list(context.user_id, context.session_id)
        if action == "complete":
            todo_id = arguments.get("todo_id")
            if not isinstance(todo_id, str) or not todo_id.strip():
                raise ToolExecutionError("invalid_todo_id", "待办 ID 必须是非空字符串。")
            if context.todo_service is None:
                raise ToolExecutionError(
                    "todo_service_unavailable",
                    "待办持久化服务尚未配置。",
                )
            return context.todo_service.complete(
                context.user_id,
                context.session_id,
                todo_id,
            )
        raise ToolExecutionError("invalid_todo_action", "待办操作不受支持。")
