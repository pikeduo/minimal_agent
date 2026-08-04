"""用于离线测试的按顺序响应 Provider。"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Deque

from ..errors import DomainValidationError
from ..models import FinalAnswer, ProviderError, ProviderErrorKind, ToolCallBatch
from .base import LLMRequest, ProviderResult


class ScriptedLLMProvider:
    """只消费预设结果队列的离线 Provider，不解析用户输入。"""

    def __init__(self, responses: Iterable[ProviderResult]) -> None:
        prepared_responses = tuple(responses)
        if not all(
            isinstance(response, (FinalAnswer, ToolCallBatch, ProviderError))
            for response in prepared_responses
        ):
            raise DomainValidationError(
                "ScriptedLLMProvider 的预设结果只能是内部 Provider 结果"
            )
        self._responses: Deque[ProviderResult] = deque(prepared_responses)
        self._received_requests: list[LLMRequest] = []

    @property
    def received_requests(self) -> tuple[LLMRequest, ...]:
        """返回已接收请求的只读快照。"""

        return tuple(self._received_requests)

    @property
    def remaining_responses(self) -> int:
        """返回尚未消费的预设结果数量。"""

        return len(self._responses)

    def complete(self, request: LLMRequest) -> ProviderResult:
        """记录请求并按原始队列顺序返回下一个预设结果。"""

        if not isinstance(request, LLMRequest):
            raise DomainValidationError("request 必须是 LLMRequest")

        self._received_requests.append(request)
        if not self._responses:
            return ProviderError(
                kind=ProviderErrorKind.UNKNOWN,
                safe_message="脚本化 Provider 没有更多预设响应。",
                retryable=False,
            )
        return self._responses.popleft()
