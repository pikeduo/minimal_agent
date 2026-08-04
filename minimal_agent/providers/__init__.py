"""LLM Provider 的统一契约与离线实现。"""

from .base import LLMProvider, LLMRequest, ProviderResult
from .scripted import ScriptedLLMProvider

__all__ = ["LLMProvider", "LLMRequest", "ProviderResult", "ScriptedLLMProvider"]
