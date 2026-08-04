"""FastAPI、Jinja2 与 HTMX Web 界面的离线集成测试。"""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from minimal_agent.app import create_app
from minimal_agent.config import Settings
from minimal_agent.models import FinalAnswer, ToolCall, ToolCallBatch
from minimal_agent.providers import ScriptedLLMProvider


def make_settings(tmp_path) -> Settings:
    return Settings(
        openai_api_key="test-secret-that-must-not-be-rendered",
        openai_model="deepseek-v4-flash",
        deepseek_base_url="https://api.deepseek.com",
        database_path=str(tmp_path / "web.sqlite3"),
        trace_path=str(tmp_path / "agent-trace.jsonl"),
        server_log_path=str(tmp_path / "server.log"),
        auth_session_days=7,
        auth_cookie_secure=False,
        max_agent_steps=4,
        max_context_messages=12,
        context_keep_recent=6,
    )


def make_client(tmp_path, responses: tuple[object, ...]) -> TestClient:
    client = TestClient(
        create_app(
            make_settings(tmp_path),
            provider=ScriptedLLMProvider(responses),
        )
    )
    register_user(client, "user-a")
    return client


def register_user(client: TestClient, username: str, password: str = "password-123") -> None:
    response = client.post(
        "/register",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def current_user_id(client: TestClient) -> str:
    token = client.cookies.get("minimal_agent_session")
    assert token is not None
    user_id = client.app.state.services.auth_session_repository.get_user_id(token=token)
    assert user_id is not None
    return user_id


def second_user_client(client: TestClient, username: str = "user-b") -> TestClient:
    other_client = TestClient(client.app)
    register_user(other_client, username)
    return other_client


def create_session(client: TestClient) -> str:
    response = client.post(
        "/sessions",
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", maxsplit=1)[-1]


def test_session_pages_and_settings_hide_api_key(tmp_path) -> None:
    client = make_client(tmp_path, ())
    session_id = create_session(client)
    create_session(client)

    home = client.get("/")
    chat = client.get(f"/sessions/{session_id}")
    settings = client.get("/settings")

    assert home.status_code == 200
    assert "新会话" in home.text
    assert 'name="title"' not in home.text
    assert 'href="/static/site.css?v=20260804-2"' in home.text
    assert f"/sessions/{session_id}" in home.text
    assert f"/sessions/{session_id}/delete" in home.text
    assert "确定删除此会话" in home.text
    assert chat.status_code == 200
    assert 'hx-post="/sessions/' in chat.text
    assert 'id="chat-panel"' in chat.text
    assert 'id="todos-panel"' in chat.text
    assert 'id="browser-key-chat-status"' in chat.text
    assert f'action="/sessions/{session_id}/leave"' in chat.text
    assert f'data-empty-session-leave-url="/sessions/{session_id}/leave"' in chat.text
    assert settings.status_code == 200
    assert "deepseek-v4-flash" in settings.text
    assert "test-secret-that-must-not-be-rendered" not in settings.text


def test_browser_key_settings_and_static_script_are_available(tmp_path) -> None:
    client = make_client(tmp_path, ())

    settings = client.get("/settings")
    script = client.get("/static/site.js")

    assert settings.status_code == 200
    assert 'type="password"' in settings.text
    assert 'id="browser-api-key-input"' in settings.text
    assert f'data-current-user-id="{current_user_id(client)}"' in settings.text
    assert "不会由网页写入 <code>.env</code>" in settings.text
    assert 'src="/static/site.js"' in settings.text
    assert script.status_code == 200
    assert "minimal-agent.deepseek-api-key:" in script.text
    assert "storageKeyForCurrentUser" in script.text
    assert "currentUserId" in script.text
    assert "X-DeepSeek-API-Key" in script.text
    assert "submitChatWithBrowserKey" in script.text
    assert '"HX-Request": "true"' in script.text
    assert "initializeEmptySessionCleanup" in script.text
    assert "navigator.sendBeacon" in script.text


def test_register_login_logout_and_cookie_protect_private_pages(tmp_path) -> None:
    client = TestClient(create_app(make_settings(tmp_path), provider=ScriptedLLMProvider(())))

    anonymous_home = client.get("/", follow_redirects=False)
    anonymous_create = client.post("/sessions", follow_redirects=False)
    invalid_register = client.post(
        "/register",
        data={"username": "ab", "password": "password-123"},
    )
    registered = client.post(
        "/register",
        data={"username": "safe-user", "password": "password-123"},
        follow_redirects=False,
    )
    logged_out = client.post("/logout", follow_redirects=False)
    invalid_login = client.post(
        "/login",
        data={"username": "safe-user", "password": "wrong-password"},
    )
    logged_in = client.post(
        "/login",
        data={"username": "safe-user", "password": "password-123"},
        follow_redirects=False,
    )
    home = client.get("/")

    assert anonymous_home.status_code == 303
    assert anonymous_home.headers["location"] == "/login"
    assert anonymous_create.status_code == 303
    assert anonymous_create.headers["location"] == "/login"
    assert invalid_register.status_code == 422
    assert "用户名只能使用" in invalid_register.text
    assert registered.status_code == 303
    assert "HttpOnly" in registered.headers["set-cookie"]
    assert "SameSite=lax" in registered.headers["set-cookie"]
    assert "password-123" not in registered.text
    assert logged_out.status_code == 303
    assert invalid_login.status_code == 401
    assert "用户名或密码错误。" in invalid_login.text
    assert logged_in.status_code == 303
    assert home.status_code == 200
    assert "safe-user" in home.text


def test_new_session_uses_first_message_as_title_and_removes_empty_session_on_leave(tmp_path) -> None:
    client = make_client(tmp_path, (FinalAnswer("已记录首条消息。"),))
    user_id = current_user_id(client)

    empty_session_id = create_session(client)
    empty_leave = client.post(
        f"/sessions/{empty_session_id}/leave",
        follow_redirects=False,
    )

    assert empty_leave.status_code == 303
    assert empty_leave.headers["location"] == "/"
    assert client.get(f"/sessions/{empty_session_id}").status_code == 404
    assert client.app.state.services.session_repository.list_for_user(user_id=user_id) == ()

    session_id = create_session(client)
    sent = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "整理本周面试项目的测试结果"},
        headers={"HX-Request": "true"},
    )
    retained_leave = client.post(
        f"/sessions/{session_id}/leave",
        follow_redirects=False,
    )
    session = client.app.state.services.session_repository.get(
        user_id=user_id,
        session_id=session_id,
    )

    assert sent.status_code == 200
    assert session.title == "整理本周面试项目的测试结果"
    assert retained_leave.status_code == 303
    assert client.get(f"/sessions/{session_id}").status_code == 200


def test_browser_key_header_uses_ephemeral_provider_without_persisting_key(tmp_path) -> None:
    browser_key = "browser-key-that-must-not-be-persisted"
    received_keys: list[str] = []
    browser_provider = ScriptedLLMProvider((FinalAnswer("浏览器密钥已用于本次请求。"),))

    def create_browser_provider(api_key: str) -> ScriptedLLMProvider:
        received_keys.append(api_key)
        return browser_provider

    client = TestClient(
        create_app(
            make_settings(tmp_path),
            provider=ScriptedLLMProvider(()),
            browser_key_provider_factory=create_browser_provider,
        )
    )
    register_user(client, "user-a")
    session_id = create_session(client)

    response = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "使用浏览器密钥回答。"},
        headers={"HX-Request": "true", "X-DeepSeek-API-Key": browser_key},
    )

    stored_messages = client.app.state.services.message_repository.list_for_session(
        user_id=current_user_id(client),
        session_id=session_id,
    )
    trace_content = (tmp_path / "agent-trace.jsonl").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert received_keys == [browser_key]
    assert "浏览器密钥已用于本次请求。" in response.text
    assert browser_key not in response.text
    assert browser_key not in trace_content
    assert all(browser_key not in message.content for message in stored_messages)


def test_invalid_browser_key_header_is_rejected_without_leaking_value(tmp_path) -> None:
    client = make_client(tmp_path, ())
    session_id = create_session(client)
    invalid_key = "a" * 513

    response = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "你好"},
        headers={"HX-Request": "true", "X-DeepSeek-API-Key": invalid_key},
    )

    assert response.status_code == 422
    assert "浏览器密钥格式无效。" in response.text
    assert invalid_key not in response.text


def test_htmx_message_submission_renders_final_answer_and_safe_tool_status(tmp_path) -> None:
    client = make_client(
        tmp_path,
        (
            ToolCallBatch(
                (ToolCall("call-1", "calculator", {"expression": "2 + 3"}),)
            ),
            FinalAnswer("计算结果为 **5**。\n\n- 已完成计算"),
        ),
    )
    session_id = create_session(client)

    response = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "请计算 2 + 3。"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert 'id="chat-panel"' in response.text
    assert "请计算 2 + 3。" in response.text
    assert "计算结果为 <strong>5</strong>。" in response.text
    assert "<li>已完成计算</li>" in response.text
    assert "**5**" not in response.text
    assert "已完成工具调用：calculator" in response.text
    assert 'hx-swap-oob="true"' in response.text
    assert "2 + 3&quot;" not in response.text


def test_todo_htmx_add_and_complete_are_scoped_to_session(tmp_path) -> None:
    client = make_client(tmp_path, ())
    session_id = create_session(client)

    added = client.post(
        f"/sessions/{session_id}/todos",
        data={"title": "整理测试结果"},
        headers={"HX-Request": "true"},
    )
    todos = client.get(
        f"/sessions/{session_id}/todos",
        headers={},
    )
    todo_id = client.app.state.services.todo_repository.list_for_session(
        user_id=current_user_id(client),
        session_id=session_id,
    )[0].todo_id
    completed = client.post(
        f"/sessions/{session_id}/todos/{todo_id}/complete",
        headers={"HX-Request": "true"},
    )

    assert added.status_code == 200
    assert "整理测试结果" in added.text
    assert todos.status_code == 200
    assert "整理测试结果" in todos.text
    assert completed.status_code == 200
    assert "completed" in completed.text


def test_cross_user_session_routes_return_not_found(tmp_path) -> None:
    client = make_client(tmp_path, ())
    other_client = second_user_client(client)
    session_id = create_session(client)

    page = other_client.get(f"/sessions/{session_id}")
    todo = other_client.get(f"/sessions/{session_id}/todos")
    message = other_client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "越权消息"},
        headers={"HX-Request": "true"},
    )

    assert page.status_code == 404
    assert todo.status_code == 404
    assert message.status_code == 404
    assert "私有窗口" not in page.text


def test_delete_session_removes_related_data_and_rejects_cross_user(tmp_path) -> None:
    client = make_client(tmp_path, (FinalAnswer("已保存。"),))
    other_client = second_user_client(client)
    session_id = create_session(client)
    client.post(
        f"/sessions/{session_id}/todos",
        data={"title": "会话内待办"},
        headers={"HX-Request": "true"},
    )
    client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "会话内消息"},
        headers={"HX-Request": "true"},
    )

    denied = other_client.post(
        f"/sessions/{session_id}/delete",
    )
    deleted = client.post(
        f"/sessions/{session_id}/delete",
        follow_redirects=False,
    )
    home = client.get("/")
    removed = client.get(f"/sessions/{session_id}")

    assert denied.status_code == 404
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/"
    assert "待删除会话" not in home.text
    assert removed.status_code == 404
    assert client.app.state.services.session_repository.list_for_user(
        user_id=current_user_id(client)
    ) == ()


def test_missing_api_key_returns_safe_chat_error_without_network(tmp_path) -> None:
    client = TestClient(
        create_app(replace(make_settings(tmp_path), openai_api_key=None))
    )
    register_user(client, "user-a")
    session_id = create_session(client)

    response = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "你好"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "未配置 DeepSeek API Key。" in response.text
    assert 'href="/settings"' in response.text
    assert "点击前往设置 <strong>DeepSeek API Key</strong>" in response.text
    assert f'action="/sessions/{session_id}/messages/' in response.text
    assert "重新发送" in response.text
    assert "OPENAI_API_KEY" not in response.text
    assert "Traceback" not in response.text


def test_retry_failed_message_uses_browser_key_without_duplicate_user_message(tmp_path) -> None:
    retry_provider = ScriptedLLMProvider((FinalAnswer("配置密钥后已成功回复。"),))
    received_keys: list[str] = []

    def create_browser_provider(api_key: str) -> ScriptedLLMProvider:
        received_keys.append(api_key)
        return retry_provider

    client = TestClient(
        create_app(
            replace(make_settings(tmp_path), openai_api_key=None),
            browser_key_provider_factory=create_browser_provider,
        )
    )
    register_user(client, "user-a")
    session_id = create_session(client)
    failed = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "请重新发送这条消息。"},
        headers={"HX-Request": "true"},
    )
    user_message_id = client.app.state.services.message_repository.list_for_session(
        user_id=current_user_id(client),
        session_id=session_id,
    )[0].message_id
    retried = client.post(
        f"/sessions/{session_id}/messages/{user_message_id}/retry",
        headers={"HX-Request": "true", "X-DeepSeek-API-Key": "user-a-key"},
    )
    messages = client.app.state.services.message_repository.list_for_session(
        user_id=current_user_id(client),
        session_id=session_id,
    )

    assert failed.status_code == 200
    assert retried.status_code == 200
    assert received_keys == ["user-a-key"]
    assert [message.role.value for message in messages] == ["user", "assistant"]
    assert [message.content for message in messages] == [
        "请重新发送这条消息。",
        "配置密钥后已成功回复。",
    ]
    assert "配置密钥后已成功回复。" in retried.text


def test_message_validation_does_not_consume_provider_and_normal_form_redirects(tmp_path) -> None:
    provider = ScriptedLLMProvider((FinalAnswer("已保存消息。"),))
    client = TestClient(create_app(make_settings(tmp_path), provider=provider))
    register_user(client, "user-a")
    session_id = create_session(client)

    invalid = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "   "},
        headers={"HX-Request": "true"},
    )
    submitted = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "请保存这条消息。"},
        follow_redirects=False,
    )
    page = client.get(
        f"/sessions/{session_id}",
        headers={},
    )

    assert invalid.status_code == 422
    assert "消息不能为空且不能过长。" in invalid.text
    assert provider.remaining_responses == 0
    assert submitted.status_code == 303
    assert submitted.headers["location"] == f"/sessions/{session_id}"
    assert "请保存这条消息。" in page.text
    assert "已保存消息。" in page.text


def test_web_rejects_invalid_identity_session_and_cross_session_todo(tmp_path) -> None:
    client = make_client(tmp_path, ())
    first_session_id = create_session(client)
    second_session_id = create_session(client)
    client.post(
        f"/sessions/{first_session_id}/todos",
        data={"title": "仅属于第一个窗口"},
        headers={"HX-Request": "true"},
    )
    todo_id = client.app.state.services.todo_repository.list_for_session(
        user_id=current_user_id(client),
        session_id=first_session_id,
    )[0].todo_id

    ignored_identity_header = client.get("/", headers={"X-User-ID": "invalid user id"})
    invalid_session = client.get("/sessions/not-a-uuid")
    cross_session_todo = client.post(
        f"/sessions/{second_session_id}/todos/{todo_id}/complete",
        headers={"HX-Request": "true"},
    )

    assert ignored_identity_header.status_code == 200
    assert invalid_session.status_code == 404
    assert cross_session_todo.status_code == 404
    remaining = client.app.state.services.todo_repository.list_for_session(
        user_id=current_user_id(client),
        session_id=first_session_id,
    )
    assert remaining[0].status.value == "open"
