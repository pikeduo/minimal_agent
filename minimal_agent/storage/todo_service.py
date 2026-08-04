"""将 SQLite Todo 仓储适配为工具服务端口。"""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import ResourceNotFoundError, ToolExecutionError
from .repositories import TodoRepository


class SQLiteTodoService:
    """只允许通过当前用户和 Session 操作 Todo 的服务适配器。"""

    def __init__(self, repository: TodoRepository) -> None:
        if not isinstance(repository, TodoRepository):
            raise TypeError("repository 必须是 TodoRepository")
        self._repository = repository

    def add(self, user_id: str, session_id: str, title: str) -> Mapping[str, Any]:
        """新增待办并返回结构化记录。"""

        todo = self._repository.add(
            user_id=user_id,
            session_id=session_id,
            title=title,
        )
        return {"action": "add", "todo": todo.to_dict()}

    def list(self, user_id: str, session_id: str) -> Mapping[str, Any]:
        """列出当前会话待办。"""

        todos = self._repository.list_for_session(
            user_id=user_id,
            session_id=session_id,
        )
        return {"action": "list", "todos": [todo.to_dict() for todo in todos]}

    def complete(
        self,
        user_id: str,
        session_id: str,
        todo_id: str,
    ) -> Mapping[str, Any]:
        """完成当前会话待办，并隐藏跨会话资源细节。"""

        try:
            todo = self._repository.complete(
                user_id=user_id,
                session_id=session_id,
                todo_id=todo_id,
            )
        except ResourceNotFoundError as exc:
            raise ToolExecutionError(
                "todo_not_found",
                "待办不存在或不属于当前会话。",
            ) from exc
        return {"action": "complete", "todo": todo.to_dict()}
