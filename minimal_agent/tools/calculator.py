"""受限 AST 驱动的四则运算工具。"""

from __future__ import annotations

import ast
import math
from typing import Any, Mapping

from ..errors import ToolExecutionError
from .base import ToolExecutionContext


class CalculatorTool:
    """只支持数字、括号与基础四则运算的安全计算器。"""

    name = "calculator"
    description = "计算基础加减乘除表达式，支持括号。"
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
            }
        },
        "required": ["expression"],
        "additionalProperties": False,
    }

    _MAX_EXPRESSION_LENGTH = 200
    _MAX_AST_NODES = 64
    _MAX_ABSOLUTE_VALUE = 1_000_000_000_000

    def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        """解析并计算表达式，绝不使用 eval。"""

        expression = arguments.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ToolExecutionError("invalid_expression", "表达式必须是非空字符串。")
        if len(expression) > self._MAX_EXPRESSION_LENGTH:
            raise ToolExecutionError("expression_too_long", "表达式长度超过限制。")

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise ToolExecutionError("invalid_expression", "表达式格式无效。") from exc

        if sum(1 for _ in ast.walk(tree)) > self._MAX_AST_NODES:
            raise ToolExecutionError("expression_too_complex", "表达式复杂度超过限制。")

        try:
            value = self._evaluate(tree)
        except ZeroDivisionError as exc:
            raise ToolExecutionError("division_by_zero", "除数不能为零。") from exc
        self._validate_number(value)
        return {"expression": expression, "value": value}

    def _evaluate(self, node: ast.AST) -> int | float:
        if isinstance(node, ast.Expression):
            return self._evaluate(node.body)
        if isinstance(node, ast.Constant):
            self._validate_number(node.value)
            return node.value
        if isinstance(node, ast.UnaryOp):
            value = self._evaluate(node.operand)
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.USub):
                return -value
            raise ToolExecutionError("unsupported_expression", "表达式包含不支持的操作。")
        if isinstance(node, ast.BinOp):
            left = self._evaluate(node.left)
            right = self._evaluate(node.right)
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            elif isinstance(node.op, ast.Div):
                value = left / right
            else:
                raise ToolExecutionError("unsupported_expression", "表达式包含不支持的操作。")
            self._validate_number(value)
            return value
        raise ToolExecutionError("unsupported_expression", "表达式包含不支持的内容。")

    def _validate_number(self, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolExecutionError("unsupported_expression", "表达式只能包含数字。")
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolExecutionError("number_out_of_range", "计算结果超出允许范围。")
        if abs(value) > self._MAX_ABSOLUTE_VALUE:
            raise ToolExecutionError("number_out_of_range", "计算结果超出允许范围。")
