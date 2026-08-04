"""Minimal Agent Runtime 的 FastAPI 应用工厂。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import Settings, load_settings
from .web.router import create_router


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建 Web 应用，但暂不初始化 Runtime 服务。"""

    app_settings = settings or load_settings()
    app = FastAPI(title="Minimal Agent Runtime", version="0.1.0")
    app.state.settings = app_settings
    app.mount(
        "/static",
        StaticFiles(directory=PROJECT_ROOT / "static"),
        name="static",
    )
    app.include_router(create_router(PROJECT_ROOT / "templates"))
    return app


def main() -> None:
    """通过包的命令行脚本启动开发服务器。"""

    import uvicorn

    uvicorn.run("minimal_agent.app:create_app", factory=True, host="127.0.0.1", port=8000)
