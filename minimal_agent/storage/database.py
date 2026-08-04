"""SQLite 连接与阶段 6 的基础表初始化。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..errors import DomainValidationError


class SQLiteDatabase:
    """创建短生命周期 SQLite 连接，并显式初始化基础表。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not str(self.path).strip():
            raise DomainValidationError("数据库路径不能为空")

    def initialize(self) -> None:
        """创建用户、会话、消息和待办表及必要索引。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (id, user_id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_user_updated
                    ON sessions(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    run_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id, user_id)
                        REFERENCES sessions(id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_user_session_created
                    ON messages(user_id, session_id, created_at, id);

                CREATE TABLE IF NOT EXISTS tool_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_call_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('success', 'error')),
                    result_json TEXT NOT NULL,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id, user_id)
                        REFERENCES sessions(id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_tool_results_user_session_created
                    ON tool_results(user_id, session_id, id DESC);

                CREATE TABLE IF NOT EXISTS session_summaries (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    covered_through_message_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id, user_id)
                        REFERENCES sessions(id, user_id)
                );

                CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('open', 'completed')),
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY (session_id, user_id)
                        REFERENCES sessions(id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_todos_user_session_status_created
                    ON todos(user_id, session_id, status, created_at);
                """
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """获取启用外键且自动提交/回滚的短连接。"""

        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
