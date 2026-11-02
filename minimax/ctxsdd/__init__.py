"""ctxsdd：面向软件持续演进的 SDD 上下文管理系统。

为 AI 开发助手按开发任务提供当前有效的规格、相关代码和测试，
并通过真实的大模型调用与测试验证完成代码修改。
"""

__version__ = "1.0.0"


class CtxsddError(RuntimeError):
    """ctxsdd 统一业务异常。"""
