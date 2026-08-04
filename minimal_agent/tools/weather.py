"""确定性 Mock 天气工具。"""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import ToolExecutionError
from .base import ToolExecutionContext


class WeatherTool:
    """从固定天气资料读取地点信息，不访问真实天气服务。"""

    name = "weather"
    description = "查询固定 Mock 天气资料中的地点、天气和温度。"
    parameters = {
        "type": "object",
        "properties": {
            "location": {"type": "string", "minLength": 1, "maxLength": 64},
            "date": {
                "type": "string",
                "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
            },
        },
        "required": ["location"],
        "additionalProperties": False,
    }
    _DEFAULT_DATE = "2026-08-04"
    _WEATHER_BY_LOCATION = {
        "厦门": {"condition": "晴", "temperature_c": 28},
        "北京": {"condition": "多云", "temperature_c": 26},
        "上海": {"condition": "小雨", "temperature_c": 24},
    }

    def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        """返回固定天气记录；未知地点也返回稳定的结构化结果。"""

        location = arguments.get("location")
        if not isinstance(location, str) or not location.strip():
            raise ToolExecutionError("invalid_location", "地点必须是非空字符串。")
        normalized_location = location.strip()
        date = arguments.get("date", self._DEFAULT_DATE)
        if not isinstance(date, str):
            raise ToolExecutionError("invalid_date", "日期必须是字符串。")

        weather = self._WEATHER_BY_LOCATION.get(normalized_location)
        if weather is None:
            return {
                "location": normalized_location,
                "date": date,
                "condition": "暂无数据",
                "temperature_c": None,
            }
        return {
            "location": normalized_location,
            "date": date,
            "condition": weather["condition"],
            "temperature_c": weather["temperature_c"],
        }
