"""Minimal Agent Runtime 的 FastAPI 应用工厂。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import Settings, load_settings
from .context import ContextBuilder, ContextCompressor, ConversationService
from .models import ProviderError, ProviderErrorKind
from .providers import DeepSeekProvider, LLMProvider, LLMRequest
from .runtime import AgentRuntime
from .storage import (
    MessageRepository,
    SessionRepository,
    SessionSummaryRepository,
    SQLiteDatabase,
    SQLiteTodoService,
    TodoRepository,
    ToolResultRepository,
)
from .tools import CalculatorTool, SearchTool, TodoTool, ToolRegistry, WeatherTool
from .tracing import JsonlTraceRecorder
from .web.router import WebServices, create_router


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


class _MissingApiKeyProvider:
    """未配置密钥时返回安全错误，避免在 Web 测试或本地页面中触网。"""

    def complete(self, request: LLMRequest) -> ProviderError:
        return ProviderError(
            kind=ProviderErrorKind.AUTHENTICATION,
            safe_message="模型服务尚未配置，请设置 OPENAI_API_KEY。",
            retryable=False,
        )


def create_app(
    settings: Settings | None = None,
    *,
    provider: LLMProvider | None = None,
    browser_key_provider_factory: Callable[[str], LLMProvider] | None = None,
) -> FastAPI:
    """创建包含本地 Runtime、Session、Todo 和 Web 路由的应用。"""

    app_settings = settings or load_settings()
    _configure_server_logging(app_settings.server_log_path)
    services = _build_services(
        app_settings,
        provider=provider,
        browser_key_provider_factory=browser_key_provider_factory,
    )
    app = FastAPI(title="Minimal Agent", version="0.1.0")
    app.state.settings = app_settings
    app.state.services = services
    app.mount(
        "/static",
        StaticFiles(directory=PROJECT_ROOT / "static"),
        name="static",
    )
    app.include_router(create_router(PROJECT_ROOT / "templates", services))
    return app


def _configure_server_logging(log_path: str) -> None:
    """将 Uvicorn 的访问和错误输出追加写入本地日志文件。"""

    resolved_path = Path(log_path).resolve()
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        for logger_name in ("uvicorn.access", "uvicorn.error", None):
            logger = logging.getLogger(logger_name)
            if any(
                getattr(handler, "_minimal_agent_log_path", None) == str(resolved_path)
                for handler in logger.handlers
            ):
                continue
            handler = logging.FileHandler(resolved_path, encoding="utf-8")
            handler._minimal_agent_log_path = str(resolved_path)  # type: ignore[attr-defined]
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            logger.addHandler(handler)
    except OSError:
        return


def _build_services(
    settings: Settings,
    *,
    provider: LLMProvider | None,
    browser_key_provider_factory: Callable[[str], LLMProvider] | None,
) -> WebServices:
    """组装单进程演示所需的本地依赖，不在 LLM 调用期间持有数据库连接。"""

    database = SQLiteDatabase(settings.database_path)
    database.initialize()
    sessions = SessionRepository(database)
    messages = MessageRepository(database)
    summaries = SessionSummaryRepository(database)
    tool_results = ToolResultRepository(database)
    todos = TodoRepository(database)
    todo_service = SQLiteTodoService(todos)

    registry = ToolRegistry()
    for tool in (CalculatorTool(), SearchTool(), WeatherTool(), TodoTool()):
        registry.register(tool)

    runtime = AgentRuntime(
        provider=provider or _provider_from_settings(settings),
        tool_registry=registry,
        model=settings.openai_model,
        max_steps=settings.max_agent_steps,
        todo_service=todo_service,
        trace_recorder=JsonlTraceRecorder(settings.trace_path),
    )
    compressor = ContextCompressor(
        message_repository=messages,
        summary_repository=summaries,
        max_context_messages=settings.max_context_messages,
        keep_recent=settings.context_keep_recent,
    )
    context_builder = ContextBuilder(
        session_repository=sessions,
        message_repository=messages,
        tool_result_repository=tool_results,
        max_messages=settings.max_context_messages,
        max_tool_results=settings.max_context_messages,
        compressor=compressor,
    )
    conversation_service = ConversationService(
        context_builder=context_builder,
        message_repository=messages,
        tool_result_repository=tool_results,
        runtime=runtime,
    )
    return WebServices(
        conversation_service=conversation_service,
        session_repository=sessions,
        message_repository=messages,
        todo_repository=todos,
        settings=settings,
        browser_key_provider_factory=(
            browser_key_provider_factory
            or (
                lambda api_key: DeepSeekProvider(
                    api_key=api_key,
                    base_url=settings.deepseek_base_url,
                )
            )
        ),
    )


def _provider_from_settings(settings: Settings) -> LLMProvider:
    """仅在密钥存在时构造真实 Provider，否则保留安全的本地失败行为。"""

    if settings.openai_api_key is None:
        return _MissingApiKeyProvider()
    return DeepSeekProvider(
        api_key=settings.openai_api_key,
        base_url=settings.deepseek_base_url,
    )


def main() -> None:
    """通过包的命令行脚本启动开发服务器。"""

    import uvicorn

    uvicorn.run("minimal_agent.app:create_app", factory=True, host="127.0.0.1", port=8000)
