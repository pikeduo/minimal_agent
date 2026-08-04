"""工具运行所需的最小契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from ..errors import DomainValidationError


@runtime_checkable
class TodoService(Protocol):
    """Todo 工具依赖的持久化端口，具体 SQLite 实现在阶段 6 提供。"""

    def add(self, user_id: str, session_id: str, title: str) -> Mapping[str, Any]:
        """为当前用户和会话创建待办。"""

    def list(self, user_id: str, session_id: str) -> Mapping[str, Any]:
        """列出当前用户和会话的待办。"""

    def complete(
        self,
        user_id: str,
        session_id: str,
        todo_id: str,
    ) -> Mapping[str, Any]:
        """完成当前用户和会话中的一项待办。"""


@dataclass(frozen=True)
class ToolExecutionContext:
    """执行工具时显式传入的已授权身份和受限依赖。"""

    user_id: str
    session_id: str
    todo_service: TodoService | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise DomainValidationError("user_id 必须是非空字符串")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise DomainValidationError("session_id 必须是非空字符串")


@runtime_checkable
class Tool(Protocol):
    """所有 Runtime 工具必须实现的元数据与执行接口。"""

    name: str
    description: str
    parameters: Mapping[str, Any]

    def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        """执行通过 Schema 校验的参数并返回结构化结果。"""
