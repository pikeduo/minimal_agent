"""基于环境变量的应用配置。"""

from __future__ import annotations

from dataclasses import dataclass
from os import environ
from typing import Mapping

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """可安全传递给应用服务的配置。

    `openai_api_key` 绝不由 Web 层渲染，也不会写入序列化诊断信息。
    """

    openai_api_key: str | None
    openai_model: str
    deepseek_base_url: str
    database_path: str
    trace_path: str
    server_log_path: str
    max_agent_steps: int
    max_context_messages: int
    context_keep_recent: int


def _positive_int(value: str | None, *, name: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if parsed <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return parsed


def load_settings(environment: Mapping[str, str] | None = None) -> Settings:
    """从 `.env` 和进程环境加载配置，且不记录密钥。"""

    if environment is None:
        load_dotenv()
        environment = environ

    max_context_messages = _positive_int(
        environment.get("MAX_CONTEXT_MESSAGES"),
        name="MAX_CONTEXT_MESSAGES",
        default=24,
    )
    context_keep_recent = _positive_int(
        environment.get("CONTEXT_KEEP_RECENT"),
        name="CONTEXT_KEEP_RECENT",
        default=12,
    )
    if context_keep_recent > max_context_messages:
        raise ValueError("CONTEXT_KEEP_RECENT 不能超过 MAX_CONTEXT_MESSAGES")

    api_key = environment.get("OPENAI_API_KEY") or None
    return Settings(
        openai_api_key=api_key,
        openai_model=environment.get("OPENAI_MODEL", "deepseek-v4-flash"),
        deepseek_base_url=environment.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        ),
        database_path=environment.get("DATABASE_PATH", "data/minimal_agent.sqlite3"),
        trace_path=environment.get("TRACE_PATH", "logs/agent-trace.jsonl"),
        server_log_path=environment.get("SERVER_LOG_PATH", "logs/server.log"),
        max_agent_steps=_positive_int(
            environment.get("MAX_AGENT_STEPS"),
            name="MAX_AGENT_STEPS",
            default=8,
        ),
        max_context_messages=max_context_messages,
        context_keep_recent=context_keep_recent,
    )
