"""基于 OpenAI-compatible SDK 的 DeepSeek Provider。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from ..errors import DomainValidationError
from ..models import (
    FinalAnswer,
    MessageRole,
    ProviderError,
    ProviderErrorKind,
    ToolCall,
    ToolCallBatch,
    ToolResult,
)
from .base import LLMRequest, ProviderResult


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider:
    """将 DeepSeek Chat Completions 响应转换为项目内部模型。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
        client: Any | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise DomainValidationError("api_key 必须是非空字符串")
        if not isinstance(base_url, str) or not base_url.strip():
            raise DomainValidationError("base_url 必须是非空字符串")

        self._client = client or OpenAI(api_key=api_key, base_url=base_url)

    def complete(self, request: LLMRequest) -> ProviderResult:
        """调用模型，并只返回内部 DTO 或经过脱敏的错误。"""

        if not isinstance(request, LLMRequest):
            raise DomainValidationError("request 必须是 LLMRequest")

        try:
            request_arguments: dict[str, Any] = {
                "model": request.model,
                "messages": self._build_messages(request),
                "extra_body": {"thinking": {"type": "disabled"}},
            }
            tools = self._build_tools(request)
            if tools:
                request_arguments["tools"] = tools
        except (TypeError, ValueError, KeyError):
            return ProviderError(
                kind=ProviderErrorKind.INVALID_RESPONSE,
                safe_message="模型请求包含不支持的结构化数据。",
                retryable=False,
            )

        try:
            response = self._client.chat.completions.create(**request_arguments)
        except AuthenticationError:
            return self._error(
                ProviderErrorKind.AUTHENTICATION,
                "模型服务认证失败。",
                retryable=False,
            )
        except PermissionDeniedError:
            return self._error(
                ProviderErrorKind.AUTHENTICATION,
                "模型服务没有访问权限。",
                retryable=False,
            )
        except RateLimitError:
            return self._error(
                ProviderErrorKind.RATE_LIMIT,
                "模型服务请求过于频繁，请稍后重试。",
                retryable=True,
            )
        except (APIConnectionError, APITimeoutError):
            return self._error(
                ProviderErrorKind.UNAVAILABLE,
                "模型服务暂时不可用，请稍后重试。",
                retryable=True,
            )
        except BadRequestError:
            return self._error(
                ProviderErrorKind.INVALID_RESPONSE,
                "模型服务拒绝了请求。",
                retryable=False,
            )
        except APIStatusError as exc:
            return self._error(
                ProviderErrorKind.UNAVAILABLE,
                "模型服务暂时不可用，请稍后重试。",
                retryable=exc.status_code >= 500,
            )
        except APIError:
            return self._error(
                ProviderErrorKind.UNAVAILABLE,
                "模型服务暂时不可用，请稍后重试。",
                retryable=True,
            )
        except Exception:
            return self._error(
                ProviderErrorKind.UNKNOWN,
                "模型服务调用失败。",
                retryable=True,
            )

        return self._parse_response(response)

    @staticmethod
    def _build_tools(request: LLMRequest) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for schema in request.tool_schemas:
            name = schema["name"]
            description = schema.get("description", "")
            parameters = schema["parameters"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("工具名称不能为空")
            if not isinstance(description, str):
                raise ValueError("工具描述必须是字符串")
            if not isinstance(parameters, Mapping):
                raise ValueError("工具参数必须是 JSON 对象")
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": DeepSeekProvider._compatible_parameters(
                            parameters
                        ),
                    },
                }
            )
        return tools

    @staticmethod
    def _compatible_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
        """移除 DeepSeek 函数调用不接受的长度约束，保留本地执行校验。"""

        def normalize(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {
                    key: normalize(item)
                    for key, item in value.items()
                    if key not in {"minLength", "maxLength"}
                }
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value

        normalized_parameters = normalize(parameters)
        if not isinstance(normalized_parameters, dict):
            raise ValueError("工具参数必须规范化为 JSON 对象")
        if normalized_parameters.get("type") != "object":
            raise ValueError("DeepSeek 工具参数顶层必须是 object")
        return normalized_parameters

    @staticmethod
    def _build_messages(request: LLMRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if request.session_summary is not None:
            messages.append(
                {
                    "role": "system",
                    "content": f"会话摘要：{request.session_summary}",
                }
            )

        for message in request.messages:
            if message.role in {
                MessageRole.SYSTEM,
                MessageRole.USER,
                MessageRole.ASSISTANT,
            }:
                messages.append(
                    {"role": message.role.value, "content": message.content}
                )
            else:
                messages.append(
                    {
                        "role": "system",
                        "content": f"历史工具消息：{message.content}",
                    }
                )

        call_ids: set[str] = set()
        results_by_call_id = {
            result.tool_call_id: result for result in request.tool_results
        }
        for batch in request.tool_call_batches:
            call_ids.update(call.tool_call_id for call in batch.calls)
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call.tool_call_id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(
                                    dict(call.arguments), ensure_ascii=False
                                ),
                            },
                        }
                        for call in batch.calls
                    ],
                }
            )
            for call in batch.calls:
                result = results_by_call_id.get(call.tool_call_id)
                if result is not None:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.tool_call_id,
                            "content": json.dumps(
                                result.to_dict(), ensure_ascii=False
                            ),
                        }
                    )

        historical_results = [
            result.to_dict()
            for result in request.tool_results
            if result.tool_call_id not in call_ids
        ]
        if historical_results:
            messages.append(
                {
                    "role": "system",
                    "content": "历史工具执行结果："
                    + json.dumps(historical_results, ensure_ascii=False),
                }
            )
        return messages

    @classmethod
    def _parse_response(cls, response: Any) -> ProviderResult:
        try:
            choices = response.choices
            if not choices:
                raise ValueError("响应没有候选项")
            message = choices[0].message
            raw_calls = message.tool_calls or ()
            if raw_calls:
                calls = tuple(cls._parse_tool_call(raw_call) for raw_call in raw_calls)
                return ToolCallBatch(calls=calls)
            content = message.content
            if not isinstance(content, str) or not content.strip():
                raise ValueError("响应没有最终文本")
            return FinalAnswer(content=content)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return cls._error(
                ProviderErrorKind.INVALID_RESPONSE,
                "模型服务返回了无法解析的响应。",
                retryable=False,
            )

    @staticmethod
    def _parse_tool_call(raw_call: Any) -> ToolCall:
        function = raw_call.function
        tool_call_id = raw_call.id
        name = function.name
        arguments = json.loads(function.arguments)
        if not isinstance(arguments, dict):
            raise ValueError("工具参数不是 JSON 对象")
        return ToolCall(
            tool_call_id=tool_call_id,
            name=name,
            arguments=arguments,
        )

    @staticmethod
    def _error(
        kind: ProviderErrorKind, safe_message: str, *, retryable: bool
    ) -> ProviderError:
        return ProviderError(
            kind=kind,
            safe_message=safe_message,
            retryable=retryable,
        )
