from __future__ import annotations

from typing import Any, Mapping

import pytest

from minimal_agent.errors import DomainValidationError, ToolExecutionError
from minimal_agent.models import ToolCall, ToolResultStatus
from minimal_agent.tools import (
    CalculatorTool,
    SearchTool,
    TodoTool,
    ToolExecutionContext,
    ToolRegistry,
    WeatherTool,
)


def make_context() -> ToolExecutionContext:
    return ToolExecutionContext(user_id="user-1", session_id="session-1")


def make_call(name: str, arguments: Mapping[str, Any]) -> ToolCall:
    return ToolCall(tool_call_id="call-1", name=name, arguments=arguments)


def test_registry_exports_all_builtin_tool_schemas_in_registration_order() -> None:
    registry = ToolRegistry()
    for tool in (CalculatorTool(), SearchTool(), WeatherTool(), TodoTool()):
        registry.register(tool)

    assert [schema["name"] for schema in registry.export_schemas()] == [
        "calculator",
        "search",
        "weather",
        "todo",
    ]
    assert registry.export_schemas()[0]["parameters"]["required"] == ["expression"]


def test_registry_rejects_duplicate_or_invalid_schema_tools() -> None:
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    with pytest.raises(DomainValidationError, match="已注册"):
        registry.register(CalculatorTool())

    class InvalidSchemaTool:
        name = "invalid"
        description = "无效 Schema 工具"
        parameters = {"type": "not-a-json-schema-type"}

        def execute(
            self,
            arguments: Mapping[str, Any],
            context: ToolExecutionContext,
        ) -> Mapping[str, Any]:
            return {}

    with pytest.raises(DomainValidationError, match="JSON Schema"):
        registry.register(InvalidSchemaTool())


def test_registry_returns_safe_error_for_unknown_and_invalid_arguments() -> None:
    registry = ToolRegistry()
    registry.register(WeatherTool())

    unknown = registry.execute(make_call("unknown", {}), make_context())
    invalid = registry.execute(make_call("weather", {}), make_context())

    assert unknown.status is ToolResultStatus.ERROR
    assert unknown.error_code == "unknown_tool"
    assert invalid.status is ToolResultStatus.ERROR
    assert invalid.error_code == "invalid_arguments"


def test_calculator_supports_basic_operations_and_rejects_unsafe_ast() -> None:
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    success = registry.execute(
        make_call("calculator", {"expression": "(1 + 2) * 3 - 4 / 2"}),
        make_context(),
    )
    unsafe = registry.execute(
        make_call("calculator", {"expression": "__import__('os').system('echo unsafe')"}),
        make_context(),
    )
    exponent = registry.execute(
        make_call("calculator", {"expression": "2 ** 10"}),
        make_context(),
    )
    zero_division = registry.execute(
        make_call("calculator", {"expression": "1 / 0"}),
        make_context(),
    )

    assert success.status is ToolResultStatus.SUCCESS
    assert success.result["value"] == 7.0
    assert unsafe.error_code == "unsupported_expression"
    assert exponent.error_code == "unsupported_expression"
    assert zero_division.error_code == "division_by_zero"


def test_search_and_weather_are_deterministic_mocks() -> None:
    registry = ToolRegistry()
    registry.register(SearchTool())
    registry.register(WeatherTool())

    first_search = registry.execute(make_call("search", {"query": "Agent"}), make_context())
    second_search = registry.execute(make_call("search", {"query": "Agent"}), make_context())
    first_weather = registry.execute(
        make_call("weather", {"location": "厦门", "date": "2026-08-05"}),
        make_context(),
    )
    second_weather = registry.execute(
        make_call("weather", {"location": "厦门", "date": "2026-08-05"}),
        make_context(),
    )

    assert first_search.status is ToolResultStatus.SUCCESS
    assert first_search.result == second_search.result
    assert first_weather.result == second_weather.result == {
        "location": "厦门",
        "date": "2026-08-05",
        "condition": "晴",
        "temperature_c": 28,
    }


def test_todo_requires_a_later_injected_persistence_service() -> None:
    registry = ToolRegistry()
    registry.register(TodoTool())

    result = registry.execute(
        make_call("todo", {"action": "add", "title": "提交周报"}),
        make_context(),
    )

    assert result.status is ToolResultStatus.ERROR
    assert result.error_code == "todo_service_unavailable"


def test_registry_preserves_explicit_tool_execution_error() -> None:
    class FailingTool:
        name = "failing"
        description = "总是失败的测试工具"
        parameters = {"type": "object", "additionalProperties": False}

        def execute(
            self,
            arguments: Mapping[str, Any],
            context: ToolExecutionContext,
        ) -> Mapping[str, Any]:
            raise ToolExecutionError("expected_failure", "预期的安全失败。")

    registry = ToolRegistry()
    registry.register(FailingTool())

    result = registry.execute(make_call("failing", {}), make_context())

    assert result.status is ToolResultStatus.ERROR
    assert result.error_code == "expected_failure"
    assert result.error_message == "预期的安全失败。"
