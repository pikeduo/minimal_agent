"""SQLite Todo 记录对应的内部实体。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..errors import DomainValidationError


@dataclass(frozen=True)
class User:
    """可安全传递到 Web 层的已注册用户，不包含密码哈希。"""

    user_id: str
    username: str
    created_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("user_id", "username"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"{field_name} 必须是非空字符串")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise DomainValidationError("created_at 必须包含时区信息")


class TodoStatus(str, Enum):
    """待办的可持久化状态。"""

    OPEN = "open"
    COMPLETED = "completed"


@dataclass(frozen=True)
class SessionSummary:
    """覆盖一段旧消息的确定性 Session 摘要。"""

    user_id: str
    session_id: str
    content: str
    covered_through_message_id: str
    updated_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "user_id",
            "session_id",
            "content",
            "covered_through_message_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"{field_name} 必须是非空字符串")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise DomainValidationError("updated_at 必须包含时区信息")


@dataclass(frozen=True)
class TodoItem:
    """已按用户和会话归属的待办记录。"""

    todo_id: str
    user_id: str
    session_id: str
    title: str
    status: TodoStatus
    created_at: datetime
    completed_at: datetime | None

    def __post_init__(self) -> None:
        for field_name in ("todo_id", "user_id", "session_id", "title"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise DomainValidationError(f"{field_name} 必须是非空字符串")
        if not isinstance(self.status, TodoStatus):
            raise DomainValidationError("status 必须是 TodoStatus 枚举值")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise DomainValidationError("created_at 必须包含时区信息")
        if self.completed_at is not None and (
            self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None
        ):
            raise DomainValidationError("completed_at 必须包含时区信息")
        if self.status is TodoStatus.OPEN and self.completed_at is not None:
            raise DomainValidationError("未完成待办不能包含完成时间")
        if self.status is TodoStatus.COMPLETED and self.completed_at is None:
            raise DomainValidationError("已完成待办必须包含完成时间")

    def to_dict(self) -> dict[str, str | None]:
        """转换为工具和 Web 层可安全使用的字典。"""

        return {
            "todo_id": self.todo_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "title": self.title,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat()
            if self.completed_at is not None
            else None,
        }
