"""脱敏 JSONL Trace 的本地写入器。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


_SENSITIVE_KEY_PARTS = (
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "traceback",
    "stacktrace",
    "rawresponse",
)
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)bearer\s+[^\s]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]+\b"),
    re.compile(r"(?i)api[ _-]?key\s*[:=]\s*[^\s]+"),
)
_REDACTED_TEXT = "[已脱敏]"


class JsonlTraceRecorder:
    """以最佳努力方式追加脱敏的、可按 run_id 关联的 JSONL 事件。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = Lock()

    def emit(
        self,
        *,
        event: str,
        run_id: str,
        data: Mapping[str, Any] | None = None,
    ) -> bool:
        """写入单个事件；本地 I/O 失败时返回 False，不中断业务流程。"""

        if not isinstance(event, str) or not event.strip():
            raise ValueError("event 必须是非空字符串")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空字符串")
        if data is not None and not isinstance(data, Mapping):
            raise ValueError("data 必须是 JSON 对象或 None")

        record = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "data": self._sanitize_mapping(data or {}),
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            with self._lock, self._path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            return False
        return True

    @classmethod
    def _sanitize_mapping(cls, value: Mapping[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if cls._is_sensitive_key(key):
                continue
            sanitized[key] = cls._sanitize_value(item)
        return sanitized

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else "[非 JSON 数值]"
        if isinstance(value, str):
            return cls._sanitize_text(value)
        if isinstance(value, Mapping):
            return cls._sanitize_mapping(value)
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_value(item) for item in value]
        return "[非 JSON 值]"

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", key.lower())
        return any(part in normalized for part in _SENSITIVE_KEY_PARTS)

    @staticmethod
    def _sanitize_text(value: str) -> str:
        sanitized = value
        for pattern in _SENSITIVE_TEXT_PATTERNS:
            sanitized = pattern.sub(_REDACTED_TEXT, sanitized)
        return sanitized
