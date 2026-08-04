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
        max_agent_steps=4,
        max_context_messages=12,
        context_keep_recent=6,
    )


def make_client(tmp_path, responses: tuple[object, ...]) -> TestClient:
    return TestClient(
        create_app(
            make_settings(tmp_path),
            provider=ScriptedLLMProvider(responses),
        )
    )


def create_session(client: TestClient, title: str, *, user_id: str = "user-a") -> str:
    response = client.post(
        "/sessions",
        data={"title": title},
        headers={"X-User-ID": user_id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", maxsplit=1)[-1]


def test_session_pages_and_settings_hide_api_key(tmp_path) -> None:
    client = make_client(tmp_path, ())
    session_id = create_session(client, "工作计划")
    create_session(client, "第二个窗口")

    home = client.get("/", headers={"X-User-ID": "user-a"})
    chat = client.get(f"/sessions/{session_id}", headers={"X-User-ID": "user-a"})
    settings = client.get("/settings", headers={"X-User-ID": "user-a"})

    assert home.status_code == 200
    assert "工作计划" in home.text
    assert "第二个窗口" in home.text
    assert f"/sessions/{session_id}" in home.text
    assert chat.status_code == 200
    assert 'hx-post="/sessions/' in chat.text
    assert 'id="chat-panel"' in chat.text
    assert 'id="todos-panel"' in chat.text
    assert settings.status_code == 200
    assert "deepseek-v4-flash" in settings.text
    assert "test-secret-that-must-not-be-rendered" not in settings.text


def test_browser_key_settings_and_static_script_are_available(tmp_path) -> None:
    client = make_client(tmp_path, ())

    settings = client.get("/settings", headers={"X-User-ID": "user-a"})
    script = client.get("/static/site.js")

    assert settings.status_code == 200
    assert 'type="password"' in settings.text
    assert 'id="browser-api-key-input"' in settings.text
    assert "不会由网页写入 <code>.env</code>" in settings.text
    assert script.status_code == 200
    assert "minimal-agent.deepseek-api-key" in script.text
    assert "X-DeepSeek-API-Key" in script.text


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
    session_id = create_session(client, "浏览器密钥窗口")

    response = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "使用浏览器密钥回答。"},
        headers={
            "X-User-ID": "user-a",
            "HX-Request": "true",
            "X-DeepSeek-API-Key": browser_key,
        },
    )

    stored_messages = client.app.state.services.message_repository.list_for_session(
        user_id="user-a",
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
    session_id = create_session(client, "密钥校验窗口")
    invalid_key = "a" * 513

    response = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "你好"},
        headers={
            "X-User-ID": "user-a",
            "HX-Request": "true",
            "X-DeepSeek-API-Key": invalid_key,
        },
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
            FinalAnswer("计算结果为 5。"),
        ),
    )
    session_id = create_session(client, "计算窗口")

    response = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "请计算 2 + 3。"},
        headers={"X-User-ID": "user-a", "HX-Request": "true"},
    )

    assert response.status_code == 200
    assert 'id="chat-panel"' in response.text
    assert "请计算 2 + 3。" in response.text
    assert "计算结果为 5。" in response.text
    assert "已完成工具调用：calculator" in response.text
    assert 'hx-swap-oob="true"' in response.text
    assert "2 + 3&quot;" not in response.text


def test_todo_htmx_add_and_complete_are_scoped_to_session(tmp_path) -> None:
    client = make_client(tmp_path, ())
    session_id = create_session(client, "待办窗口")

    added = client.post(
        f"/sessions/{session_id}/todos",
        data={"title": "整理测试结果"},
        headers={"X-User-ID": "user-a", "HX-Request": "true"},
    )
    todos = client.get(
        f"/sessions/{session_id}/todos",
        headers={"X-User-ID": "user-a"},
    )
    todo_id = client.app.state.services.todo_repository.list_for_session(
        user_id="user-a",
        session_id=session_id,
    )[0].todo_id
    completed = client.post(
        f"/sessions/{session_id}/todos/{todo_id}/complete",
        headers={"X-User-ID": "user-a", "HX-Request": "true"},
    )

    assert added.status_code == 200
    assert "整理测试结果" in added.text
    assert todos.status_code == 200
    assert "整理测试结果" in todos.text
    assert completed.status_code == 200
    assert "completed" in completed.text


def test_cross_user_session_routes_return_not_found(tmp_path) -> None:
    client = make_client(tmp_path, ())
    session_id = create_session(client, "私有窗口", user_id="user-a")

    page = client.get(f"/sessions/{session_id}", headers={"X-User-ID": "user-b"})
    todo = client.get(
        f"/sessions/{session_id}/todos",
        headers={"X-User-ID": "user-b"},
    )
    message = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "越权消息"},
        headers={"X-User-ID": "user-b", "HX-Request": "true"},
    )

    assert page.status_code == 404
    assert todo.status_code == 404
    assert message.status_code == 404
    assert "私有窗口" not in page.text


def test_missing_api_key_returns_safe_chat_error_without_network(tmp_path) -> None:
    client = TestClient(
        create_app(replace(make_settings(tmp_path), openai_api_key=None))
    )
    session_id = create_session(client, "未配置模型")

    response = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "你好"},
        headers={"X-User-ID": "user-a", "HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "模型服务尚未配置，请设置 OPENAI_API_KEY。" in response.text
    assert "Traceback" not in response.text


def test_message_validation_does_not_consume_provider_and_normal_form_redirects(tmp_path) -> None:
    provider = ScriptedLLMProvider((FinalAnswer("已保存消息。"),))
    client = TestClient(create_app(make_settings(tmp_path), provider=provider))
    session_id = create_session(client, "普通表单")

    invalid = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "   "},
        headers={"X-User-ID": "user-a", "HX-Request": "true"},
    )
    submitted = client.post(
        f"/sessions/{session_id}/messages",
        data={"content": "请保存这条消息。"},
        headers={"X-User-ID": "user-a"},
        follow_redirects=False,
    )
    page = client.get(
        f"/sessions/{session_id}",
        headers={"X-User-ID": "user-a"},
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
    first_session_id = create_session(client, "第一个窗口")
    second_session_id = create_session(client, "第二个窗口")
    client.post(
        f"/sessions/{first_session_id}/todos",
        data={"title": "仅属于第一个窗口"},
        headers={"X-User-ID": "user-a", "HX-Request": "true"},
    )
    todo_id = client.app.state.services.todo_repository.list_for_session(
        user_id="user-a",
        session_id=first_session_id,
    )[0].todo_id

    invalid_identity = client.get("/", headers={"X-User-ID": "invalid user id"})
    invalid_session = client.get(
        "/sessions/not-a-uuid",
        headers={"X-User-ID": "user-a"},
    )
    cross_session_todo = client.post(
        f"/sessions/{second_session_id}/todos/{todo_id}/complete",
        headers={"X-User-ID": "user-a", "HX-Request": "true"},
    )

    assert invalid_identity.status_code == 400
    assert invalid_session.status_code == 404
    assert cross_session_todo.status_code == 404
    remaining = client.app.state.services.todo_repository.list_for_session(
        user_id="user-a",
        session_id=first_session_id,
    )
    assert remaining[0].status.value == "open"
