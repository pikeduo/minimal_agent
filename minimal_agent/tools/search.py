"""确定性 Mock 搜索工具。"""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import ToolExecutionError
from .base import ToolExecutionContext


class SearchTool:
    """在固定资料集内检索，不访问真实互联网。"""

    name = "search"
    description = "从固定 Mock 资料中检索与查询相关的内容。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 200}
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    _DOCUMENTS = (
        {
            "title": "Agent 工具调用基础",
            "snippet": "工具应由模型返回的结构化调用决定，并在执行前校验参数。",
            "url": "mock://agent-tools",
        },
        {
            "title": "厦门天气出行提示",
            "snippet": "沿海城市天气变化较快，出行前可查询确定性天气 Mock。",
            "url": "mock://xiamen-weather",
        },
        {
            "title": "周报待办清单",
            "snippet": "将周报草稿、数据核对和提交时间拆分为独立待办。",
            "url": "mock://weekly-report-todos",
        },
    )

    def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        """以固定排序返回 Mock 资料，不发起网络请求。"""

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolExecutionError("invalid_query", "查询内容必须是非空字符串。")
        normalized_query = " ".join(query.casefold().split())
        if len(normalized_query) > 200:
            raise ToolExecutionError("query_too_long", "查询内容超过长度限制。")

        ranked_results: list[tuple[int, int, Mapping[str, str]]] = []
        query_terms = normalized_query.split()
        for position, document in enumerate(self._DOCUMENTS):
            searchable_text = " ".join(document.values()).casefold()
            score = sum(term in searchable_text for term in query_terms)
            if score:
                ranked_results.append((score, position, document))

        ranked_results.sort(key=lambda item: (-item[0], item[1]))
        return {
            "query": query,
            "results": [dict(item[2]) for item in ranked_results[:3]],
        }
