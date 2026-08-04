"""基于当前开发身份的 Session、聊天和 Todo Web 路由。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..auth import hash_password, new_session_token, verify_password
from ..config import Settings
from ..context import ConversationService
from ..errors import DomainValidationError, ResourceNotFoundError
from ..providers.base import LLMProvider
from ..storage import (
    AuthSessionRepository,
    MessageRepository,
    SessionRepository,
    TodoRepository,
    User,
    UserRepository,
)


_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
_MAX_MESSAGE_LENGTH = 4_000
_MAX_SESSION_TITLE_LENGTH = 100
_MAX_TODO_TITLE_LENGTH = 200
_AUTH_COOKIE_NAME = "minimal_agent_session"


@dataclass(frozen=True)
class WebServices:
    """Web 层所需的应用服务与安全展示配置。"""

    conversation_service: ConversationService
    user_repository: UserRepository
    auth_session_repository: AuthSessionRepository
    session_repository: SessionRepository
    message_repository: MessageRepository
    todo_repository: TodoRepository
    settings: Settings
    browser_key_provider_factory: Callable[[str], LLMProvider]


def create_router(template_directory: Path, services: WebServices) -> APIRouter:
    """使用项目本地模板和已组装的服务创建路由。"""

    router = APIRouter()
    templates = Jinja2Templates(directory=str(template_directory))

    @router.get("/register", response_class=HTMLResponse)
    def register_page(request: Request) -> Response:
        if _current_user(request, services) is not None:
            return RedirectResponse(url="/", status_code=303)
        return _auth_page(request, templates, mode="register")

    @router.post("/register", response_class=HTMLResponse)
    def register(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        if _current_user(request, services) is not None:
            return RedirectResponse(url="/", status_code=303)
        cleaned_username = _clean_username(username)
        if cleaned_username is None:
            return _auth_page(
                request,
                templates,
                mode="register",
                username=username,
                error_message="用户名只能使用 3 到 32 位字母、数字、下划线或连字符。",
                status_code=422,
            )
        try:
            password_hash = hash_password(password)
            user = services.user_repository.create(
                username=cleaned_username,
                password_hash=password_hash,
            )
        except DomainValidationError as exc:
            message = (
                "用户名已被使用。"
                if "用户名已被使用" in str(exc)
                else "密码长度必须在 8 到 128 个字符之间。"
            )
            return _auth_page(
                request,
                templates,
                mode="register",
                username=cleaned_username,
                error_message=message,
                status_code=422,
            )
        return _login_response(user, services)

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        if _current_user(request, services) is not None:
            return RedirectResponse(url="/", status_code=303)
        return _auth_page(request, templates, mode="login")

    @router.post("/login", response_class=HTMLResponse)
    def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        cleaned_username = _clean_username(username)
        authentication = (
            services.user_repository.get_authentication(username=cleaned_username)
            if cleaned_username is not None
            else None
        )
        if authentication is None or not verify_password(password, authentication[1]):
            return _auth_page(
                request,
                templates,
                mode="login",
                username=username,
                error_message="用户名或密码错误。",
                status_code=401,
            )
        return _login_response(authentication[0], services)

    @router.post("/logout")
    def logout(request: Request) -> RedirectResponse:
        token = request.cookies.get(_AUTH_COOKIE_NAME)
        if token is not None:
            services.auth_session_repository.delete(token=token)
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie(_AUTH_COOKIE_NAME, path="/")
        return response

    @router.get("/", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        user = _current_user(request, services)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
        user_id = user.user_id
        sessions = services.session_repository.list_for_user(user_id=user_id)
        return templates.TemplateResponse(
            request=request,
            name="sessions.html",
            context={"sessions": sessions, "current_user": user},
        )

    @router.post("/sessions")
    def create_session(
        request: Request,
        title: str = Form("新会话"),
    ) -> Response:
        user_id = _require_current_user(request, services).user_id
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

    @router.post("/sessions/{session_id}/delete")
    def delete_session(request: Request, session_id: str) -> RedirectResponse:
        """删除当前用户拥有的会话，并回到会话首页。"""

        user_id = _require_current_user(request, services).user_id
        _owned_session_or_404(
            services.session_repository,
            user_id=user_id,
            session_id=session_id,
        )
        services.session_repository.delete(user_id=user_id, session_id=session_id)
        return RedirectResponse(url="/", status_code=303)

    @router.get("/sessions/{session_id}", response_class=HTMLResponse)
    def chat_page(request: Request, session_id: str) -> HTMLResponse:
        user = _require_current_user(request, services)
        user_id = user.user_id
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
                "current_user": user,
            },
        )

    @router.post("/sessions/{session_id}/messages", response_class=HTMLResponse)
    def post_message(
        request: Request,
        session_id: str,
        content: str = Form(...),
    ) -> Response:
        user_id = _require_current_user(request, services).user_id
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

        browser_api_key = _browser_api_key(request)
        try:
            result = services.conversation_service.respond(
                user_id=user_id,
                session_id=session_id,
                content=cleaned_content,
                provider_override=(
                    services.browser_key_provider_factory(browser_api_key)
                    if browser_api_key is not None
                    else None
                ),
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
        user_id = _require_current_user(request, services).user_id
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
        user_id = _require_current_user(request, services).user_id
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
        user_id = _require_current_user(request, services).user_id
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
        user = _require_current_user(request, services)
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "model": services.settings.openai_model,
                "max_agent_steps": services.settings.max_agent_steps,
                "max_context_messages": services.settings.max_context_messages,
                "context_keep_recent": services.settings.context_keep_recent,
                "api_key_configured": services.settings.openai_api_key is not None,
                "current_user": user,
            },
        )

    @router.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return router


def _current_user(request: Request, services: WebServices) -> User | None:
    """从 HttpOnly Cookie 取得当前用户，不再接受可伪造的用户请求头。"""

    token = request.cookies.get(_AUTH_COOKIE_NAME)
    if token is None:
        return None
    user_id = services.auth_session_repository.get_user_id(token=token)
    if user_id is None:
        return None
    return services.user_repository.get(user_id=user_id)


def _require_current_user(request: Request, services: WebServices) -> User:
    """要求请求携带有效登录会话，否则返回统一认证失败响应。"""

    user = _current_user(request, services)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录。")
    return user


def _clean_username(value: str) -> str | None:
    """校验可作为登录标识的最小用户名格式。"""

    if not isinstance(value, str):
        return None
    cleaned_value = value.strip()
    return cleaned_value if _USERNAME_PATTERN.fullmatch(cleaned_value) else None


def _auth_page(
    request: Request,
    templates: Jinja2Templates,
    *,
    mode: str,
    username: str = "",
    error_message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """渲染登录或注册表单，绝不回填密码。"""

    return templates.TemplateResponse(
        request=request,
        name="auth.html",
        context={
            "mode": mode,
            "username": username,
            "error_message": error_message,
            "current_user": None,
        },
        status_code=status_code,
    )


def _login_response(user: User, services: WebServices) -> RedirectResponse:
    """创建服务端会话并签发仅限本浏览器读取的 Cookie。"""

    token = new_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=services.settings.auth_session_days
    )
    services.auth_session_repository.create(
        user_id=user.user_id,
        token=token,
        expires_at=expires_at,
    )
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=_AUTH_COOKIE_NAME,
        value=token,
        max_age=services.settings.auth_session_days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=services.settings.auth_cookie_secure,
        path="/",
    )
    return response


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


def _browser_api_key(request: Request) -> str | None:
    """读取浏览器暂存的密钥，但绝不将其写入 Trace、数据库或响应。"""

    api_key = request.headers.get("X-DeepSeek-API-Key")
    if api_key is None:
        return None
    cleaned_key = api_key.strip()
    if not cleaned_key or len(cleaned_key) > 512:
        raise HTTPException(status_code=422, detail="浏览器密钥格式无效。")
    return cleaned_key


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
