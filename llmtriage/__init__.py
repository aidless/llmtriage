"""llmtriage —— LLM 推理部署现场分诊工具。

设计原则（三条，来自真实排障事故）：
1. 每条结论必须带证据（执行的命令 + 原始输出），没有证据就是 UNKNOWN，不许猜。
2. 三态：OBSERVED（实测）/ INFERRED（据实测推断）/ UNKNOWN（数据不足）。UNKNOWN 绝不静默变成"通过"。
3. 退出码是契约：0 通过 / 1 发现问题 / 2 用法错误 / 3 数据不足。
"""

__version__ = "0.1.0"

STATE_OBSERVED = "OBSERVED"
STATE_INFERRED = "INFERRED"
STATE_UNKNOWN = "UNKNOWN"
