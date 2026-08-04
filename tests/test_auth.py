"""本地账户密码和服务端登录会话的离线测试。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from minimal_agent.auth import hash_password, new_session_token, verify_password
from minimal_agent.storage import AuthSessionRepository, SQLiteDatabase, UserRepository


def test_password_hash_is_non_reversible_and_verifiable() -> None:
    encoded_hash = hash_password("password-123")

    assert encoded_hash.startswith("scrypt$")
    assert "password-123" not in encoded_hash
    assert verify_password("password-123", encoded_hash) is True
    assert verify_password("wrong-password", encoded_hash) is False


def test_server_session_can_be_revoked_and_expires(tmp_path) -> None:
    database = SQLiteDatabase(tmp_path / "auth.sqlite3")
    database.initialize()
    user = UserRepository(database).create(
        username="safe-user",
        password_hash=hash_password("password-123"),
    )
    sessions = AuthSessionRepository(database)
    token = new_session_token()

    sessions.create(
        user_id=user.user_id,
        token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    assert sessions.get_user_id(token=token) == user.user_id

    sessions.delete(token=token)
    assert sessions.get_user_id(token=token) is None

    expired_token = new_session_token()
    sessions.create(
        user_id=user.user_id,
        token=expired_token,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert sessions.get_user_id(token=expired_token) is None


def test_database_migrates_existing_users_table_for_authentication(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )

    SQLiteDatabase(database_path).initialize()
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        auth_sessions_exists = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'auth_sessions'
            """
        ).fetchone()

    assert {"username", "password_hash"}.issubset(columns)
    assert auth_sessions_exists is not None
