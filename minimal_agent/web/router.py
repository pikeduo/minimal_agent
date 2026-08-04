"""Agent Runtime 接入前可用的基础路由。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


def create_router(template_directory: Path) -> APIRouter:
    """使用项目本地模板创建路由。"""

    router = APIRouter()
    templates = Jinja2Templates(directory=str(template_directory))

    @router.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="home.html")

    @router.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return router
