import logging

from fastapi.testclient import TestClient

from minimal_agent.app import _configure_server_logging, create_app
from minimal_agent.config import load_settings


def make_client(tmp_path) -> TestClient:
    settings = load_settings(
        {
            "OPENAI_API_KEY": "test-secret-that-must-not-be-rendered",
            "DATABASE_PATH": str(tmp_path / "app.sqlite3"),
            "TRACE_PATH": str(tmp_path / "agent-trace.jsonl"),
            "SERVER_LOG_PATH": str(tmp_path / "server.log"),
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
    assert "Minimal Agent" in response.text
    assert "test-secret-that-must-not-be-rendered" not in response.text


def test_settings_use_safe_defaults_when_api_key_is_missing() -> None:
    settings = load_settings({})

    assert settings.openai_api_key is None
    assert settings.openai_model == "deepseek-v4-flash"
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.server_log_path == "logs/server.log"
    assert settings.max_agent_steps == 8
    assert settings.max_context_messages == 24
    assert settings.context_keep_recent == 12


def test_settings_reject_invalid_context_bounds_and_keeps_explicit_model() -> None:
    configured = load_settings(
        {
            "OPENAI_MODEL": "deepseek-v4-flash",
            "MAX_CONTEXT_MESSAGES": "3",
            "CONTEXT_KEEP_RECENT": "2",
        }
    )

    assert configured.openai_model == "deepseek-v4-flash"
    assert configured.max_context_messages == 3
    assert configured.context_keep_recent == 2

    try:
        load_settings(
            {"MAX_CONTEXT_MESSAGES": "2", "CONTEXT_KEEP_RECENT": "3"}
        )
    except ValueError as exc:
        assert "CONTEXT_KEEP_RECENT" in str(exc)
    else:
        raise AssertionError("配置边界无效时必须拒绝加载")


def test_server_logging_appends_uvicorn_access_output_to_file(tmp_path) -> None:
    log_path = tmp_path / "server.log"
    logger = logging.getLogger("uvicorn.access")
    previous_level = logger.level

    try:
        logger.setLevel(logging.INFO)
        _configure_server_logging(str(log_path))
        logger.info("GET /static/site.css 304 Not Modified")
    finally:
        logger.setLevel(previous_level)

    assert "GET /static/site.css 304 Not Modified" in log_path.read_text(
        encoding="utf-8"
    )
