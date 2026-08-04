"""项目内部可安全处理的错误类型。"""


class MinimalAgentError(Exception):
    """项目领域错误的基类。"""


class DomainValidationError(MinimalAgentError, ValueError):
    """领域模型字段或结构不符合约束。"""


class InvalidStateTransition(MinimalAgentError):
    """运行状态不允许执行目标迁移。"""
