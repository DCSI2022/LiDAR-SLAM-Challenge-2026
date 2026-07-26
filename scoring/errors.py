class EvaluationError(Exception):
    """评分流程中可预期错误的基类。"""


class ParticipantInputError(EvaluationError):
    """参赛提交不满足公开格式或最低可评条件。"""


class ConfigurationError(EvaluationError):
    """组织方配置或私有 reference 不合法。"""
