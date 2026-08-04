"""需要显式授权的真实 DeepSeek API 冒烟测试。"""

from __future__ import annotations

import os

import pytest

from minimal_agent.models import FinalAnswer, Message, MessageRole, ProviderError
from minimal_agent.providers import DEFAULT_DEEPSEEK_BASE_URL, DeepSeekProvider, LLMRequest


@pytest.mark.smoke
def test_deepseek_real_api_smoke() -> None:
    """仅在显式标记且存在密钥时发起一次真实模型请求。"""

    if os.getenv("RUN_LLM_SMOKE") != "1":
        pytest.skip("未设置 RUN_LLM_SMOKE=1，默认不访问真实模型服务。")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("真实 Smoke Test 需要 OPENAI_API_KEY。")

    provider = DeepSeekProvider(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
    )
    request = LLMRequest(
        model=os.getenv("OPENAI_MODEL", "deepseek-v4-flash"),
        messages=(
            Message(
                message_id="smoke-message-1",
                user_id="smoke-user",
                session_id="smoke-session",
                role=MessageRole.USER,
                content="请只回复：冒烟测试成功。",
            ),
        ),
    )

    result = provider.complete(request)

    if isinstance(result, ProviderError):
        pytest.fail(f"真实模型服务调用失败：{result.safe_message}")
    assert isinstance(result, FinalAnswer)
    assert result.content.strip()
