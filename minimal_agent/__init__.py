"""Minimal Agent Runtime 包。"""

from typing import Any

__all__ = ["create_app"]


def create_app(*args: Any, **kwargs: Any) -> Any:
    """延迟导入 Web 应用工厂，保持领域模型可独立使用。"""

    from .app import create_app as app_factory

    return app_factory(*args, **kwargs)
