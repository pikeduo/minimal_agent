"""项目内部可安全处理的错误类型。"""


class MinimalAgentError(Exception):
    """项目领域错误的基类。"""


class DomainValidationError(MinimalAgentError, ValueError):
    """领域模型字段或结构不符合约束。"""


class InvalidStateTransition(MinimalAgentError):
    """运行状态不允许执行目标迁移。"""


class ToolExecutionError(MinimalAgentError):
    """工具主动返回的、可安全呈现给模型的执行错误。"""

    def __init__(self, error_code: str, safe_message: str) -> None:
        if not isinstance(error_code, str) or not error_code.strip():
            raise DomainValidationError("error_code 必须是非空字符串")
        if not isinstance(safe_message, str) or not safe_message.strip():
            raise DomainValidationError("safe_message 必须是非空字符串")
        self.error_code = error_code
        self.safe_message = safe_message
        super().__init__(safe_message)
