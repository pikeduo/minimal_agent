from fastapi.testclient import TestClient

from minimal_agent.app import create_app
from minimal_agent.config import load_settings


def make_client(tmp_path) -> TestClient:
    settings = load_settings(
        {
            "OPENAI_API_KEY": "test-secret-that-must-not-be-rendered",
            "DATABASE_PATH": str(tmp_path / "app.sqlite3"),
            "TRACE_PATH": str(tmp_path / "agent-trace.jsonl"),
            "MAX_AGENT_STEPS": "8",
            "MAX_CONTEXT_MESSAGES": "24",
            "CONTEXT_KEEP_RECENT": "12",
        }
    )
    return TestClient(create_app(settings))


def test_app_factory_exposes_health_check(tmp_path) -> None:
    response = make_client(tmp_path).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_page_is_available_without_leaking_api_key(tmp_path) -> None:
    response = make_client(tmp_path).get("/")

    assert response.status_code == 200
    assert "Minimal Agent Runtime" in response.text
    assert "test-secret-that-must-not-be-rendered" not in response.text


def test_settings_use_safe_defaults_when_api_key_is_missing() -> None:
    settings = load_settings({})

    assert settings.openai_api_key is None
    assert settings.openai_model == "deepseek-v4-flash"
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.max_agent_steps == 8
    assert settings.max_context_messages == 24
    assert settings.context_keep_recent == 12
