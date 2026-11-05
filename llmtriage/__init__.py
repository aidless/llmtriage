"""llmtriage —— LLM 推理部署现场分诊工具。

设计原则（三条，来自真实排障事故）：
1. 每条结论必须带证据（执行的命令 + 原始输出），没有证据就是 UNKNOWN，不许猜。
2. 三态：OBSERVED（实测）/ INFERRED（据实测推断）/ UNKNOWN（数据不足）。UNKNOWN 绝不静默变成"通过"。
3. 退出码是契约：0 通过 / 1 发现问题 / 2 用法错误 / 3 数据不足。
"""

def _detect_version() -> str:
    """版本单一来源：打包元数据（pyproject.toml）。

    两处各写一个版本号时，`llmtriage --version` 可以和正在运行的代码不一致。
    现在只有一个来源：
    - 源码树 / 可编辑安装：读同仓库的 pyproject.toml（改了立刻生效，不必重装）
    - wheel 安装：读安装元数据（此时仓库根目录不在 site-packages 里）
    """
    import re
    from pathlib import Path

    try:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if pyproject.is_file():
            m = re.search(r'^version\s*=\s*"([^"]+)"',
                          pyproject.read_text(encoding="utf-8"), re.M)
            if m:
                return m.group(1)
    except Exception:
        pass
    try:
        from importlib.metadata import version as _dist_version
        return _dist_version("llmtriage")
    except Exception:
        return "0.0.0+unknown"


__version__ = _detect_version()

STATE_OBSERVED = "OBSERVED"
STATE_INFERRED = "INFERRED"
STATE_UNKNOWN = "UNKNOWN"
