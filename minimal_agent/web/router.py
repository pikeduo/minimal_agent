"""基于当前开发身份的 Session、聊天和 Todo Web 路由。"""

from __future__ import annotations

from pathlib import Path
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..config import Settings
from ..context import ConversationService
from ..errors import DomainValidationError, ResourceNotFoundError
from ..storage import MessageRepository, SessionRepository, TodoRepository


_USER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_MESSAGE_LENGTH = 4_000
_MAX_SESSION_TITLE_LENGTH = 100
_MAX_TODO_TITLE_LENGTH = 200


@dataclass(frozen=True)
class WebServices:
    """Web 层所需的应用服务与安全展示配置。"""

    conversation_service: ConversationService
    session_repository: SessionRepository
    message_repository: MessageRepository
    todo_repository: TodoRepository
    settings: Settings


def create_router(template_directory: Path, services: WebServices) -> APIRouter:
    """使用项目本地模板和已组装的服务创建路由。"""

    router = APIRouter()
    templates = Jinja2Templates(directory=str(template_directory))

    @router.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        user_id = _current_user(request)
        sessions = services.session_repository.list_for_user(user_id=user_id)
        return templates.TemplateResponse(
            request=request,
            name="sessions.html",
            context={"sessions": sessions, "user_id": user_id},
        )

    @router.post("/sessions")
    def create_session(
        request: Request,
        title: str = Form("新会话"),
    ) -> Response:
        user_id = _current_user(request)
        cleaned_title = _clean_text(
            title,
            field_name="会话标题",
            max_length=_MAX_SESSION_TITLE_LENGTH,
        )
        if cleaned_title is None:
            return _validation_error(
                request,
                templates,
                "会话标题不能为空且不能过长。",
            )
        session = services.session_repository.create(
            user_id=user_id,
            title=cleaned_title,
        )
        return RedirectResponse(url=f"/sessions/{session.session_id}", status_code=303)

    @router.get("/sessions/{session_id}", response_class=HTMLResponse)
    def chat_page(request: Request, session_id: str) -> HTMLResponse:
        user_id = _current_user(request)
        session = _owned_session_or_404(
            services.session_repository,
            user_id=user_id,
            session_id=session_id,
        )
        messages = services.message_repository.list_for_session(
            user_id=user_id,
            session_id=session_id,
        )
        todos = services.todo_repository.list_for_session(
            user_id=user_id,
            session_id=session_id,
        )
        return templates.TemplateResponse(
            request=request,
            name="chat.html",
            context={
                "session": session,
                "session_id": session_id,
                "messages": messages,
                "todos": todos,
                "run_status": None,
                "error_message": None,
            },
        )

    @router.post("/sessions/{session_id}/messages", response_class=HTMLResponse)
    def post_message(
        request: Request,
        session_id: str,
        content: str = Form(...),
    ) -> Response:
        user_id = _current_user(request)
        _owned_session_or_404(
            services.session_repository,
            user_id=user_id,
            session_id=session_id,
        )
        cleaned_content = _clean_text(
            content,
            field_name="消息",
            max_length=_MAX_MESSAGE_LENGTH,
        )
        if cleaned_content is None:
            return _validation_error(request, templates, "消息不能为空且不能过长。")

        try:
            result = services.conversation_service.respond(
                user_id=user_id,
                session_id=session_id,
                content=cleaned_content,
            )
        except ResourceNotFoundError:
            raise HTTPException(status_code=404, detail="未找到会话。") from None

        if not _is_htmx(request):
            return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)

        messages = services.message_repository.list_for_session(
            user_id=user_id,
            session_id=session_id,
        )
        todos = services.todo_repository.list_for_session(
            user_id=user_id,
            session_id=session_id,
        )
        return templates.TemplateResponse(
            request=request,
            name="fragments/chat_update.html",
            context={
                "messages": messages,
                "todos": todos,
                "session_id": session_id,
                "todo_oob": True,
                "run_status": _run_status(result.runtime_result),
                "error_message": _runtime_error_message(result.runtime_result),
            },
        )

    @router.get("/sessions/{session_id}/todos", response_class=HTMLResponse)
    def todo_fragment(request: Request, session_id: str) -> HTMLResponse:
        user_id = _current_user(request)
        _owned_session_or_404(
            services.session_repository,
            user_id=user_id,
            session_id=session_id,
        )
        todos = services.todo_repository.list_for_session(
            user_id=user_id,
            session_id=session_id,
        )
        return templates.TemplateResponse(
            request=request,
            name="fragments/todos.html",
            context={"todos": todos, "session_id": session_id},
        )

    @router.post("/sessions/{session_id}/todos", response_class=HTMLResponse)
    def add_todo(
        request: Request,
        session_id: str,
        title: str = Form(...),
    ) -> Response:
        user_id = _current_user(request)
        _owned_session_or_404(
            services.session_repository,
            user_id=user_id,
            session_id=session_id,
        )
        cleaned_title = _clean_text(
            title,
            field_name="待办",
            max_length=_MAX_TODO_TITLE_LENGTH,
        )
        if cleaned_title is None:
            return _validation_error(request, templates, "待办不能为空且不能过长。")
        services.todo_repository.add(
            user_id=user_id,
            session_id=session_id,
            title=cleaned_title,
        )
        return _todo_response(request, templates, services, user_id, session_id)

    @router.post("/sessions/{session_id}/todos/{todo_id}/complete", response_class=HTMLResponse)
    def complete_todo(
        request: Request,
        session_id: str,
        todo_id: str,
    ) -> Response:
        user_id = _current_user(request)
        _owned_session_or_404(
            services.session_repository,
            user_id=user_id,
            session_id=session_id,
        )
        try:
            services.todo_repository.complete(
                user_id=user_id,
                session_id=session_id,
                todo_id=todo_id,
            )
        except ResourceNotFoundError:
            raise HTTPException(status_code=404, detail="未找到待办。") from None
        return _todo_response(request, templates, services, user_id, session_id)

    @router.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> HTMLResponse:
        _current_user(request)
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "model": services.settings.openai_model,
                "max_agent_steps": services.settings.max_agent_steps,
                "max_context_messages": services.settings.max_context_messages,
                "context_keep_recent": services.settings.context_keep_recent,
                "api_key_configured": services.settings.openai_api_key is not None,
            },
        )

    @router.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return router


def _current_user(request: Request) -> str:
    """取得开发期身份上下文，路由不接受表单中的目标用户 ID。"""

    user_id = request.headers.get("X-User-ID", "demo-user")
    if not _USER_ID_PATTERN.fullmatch(user_id):
        raise HTTPException(status_code=400, detail="用户身份格式无效。")
    return user_id


def _owned_session_or_404(
    session_repository: SessionRepository,
    *,
    user_id: str,
    session_id: str,
) -> Any:
    """校验 UUID 和当前用户的 Session 所有权，失败时不泄露资源信息。"""

    try:
        UUID(session_id)
        return session_repository.get(user_id=user_id, session_id=session_id)
    except (ValueError, ResourceNotFoundError):
        raise HTTPException(status_code=404, detail="未找到会话。") from None


def _clean_text(value: str, *, field_name: str, max_length: int) -> str | None:
    """去除前后空白，并限制表单文本长度。"""

    if not isinstance(value, str):
        return None
    cleaned_value = value.strip()
    if not cleaned_value or len(cleaned_value) > max_length:
        return None
    return cleaned_value


def _is_htmx(request: Request) -> bool:
    """判断请求是否要求 HTML 局部片段。"""

    return request.headers.get("HX-Request", "").lower() == "true"


def _validation_error(
    request: Request,
    templates: Jinja2Templates,
    message: str,
) -> HTMLResponse:
    """为 HTMX 返回安全错误片段，为普通表单返回 422 页面。"""

    return templates.TemplateResponse(
        request=request,
        name="fragments/error.html",
        context={"error_message": message},
        status_code=422,
    )


def _todo_response(
    request: Request,
    templates: Jinja2Templates,
    services: WebServices,
    user_id: str,
    session_id: str,
) -> Response:
    """根据请求类型返回 Todo 片段或聊天页面重定向。"""

    if not _is_htmx(request):
        return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)
    todos = services.todo_repository.list_for_session(
        user_id=user_id,
        session_id=session_id,
    )
    return templates.TemplateResponse(
        request=request,
        name="fragments/todos.html",
        context={"todos": todos, "session_id": session_id},
    )


def _run_status(runtime_result: Any) -> str:
    """将 Runtime 结果压缩为不包含参数和原始响应的状态文本。"""

    if runtime_result.tool_results:
        tool_names = "、".join(result.tool_name for result in runtime_result.tool_results)
        return f"已完成工具调用：{tool_names}"
    return "已完成回复。"


def _runtime_error_message(runtime_result: Any) -> str | None:
    """只向页面返回 Provider 已提供的安全错误摘要。"""

    if runtime_result.provider_error is None:
        return None
    return runtime_result.provider_error.safe_message
