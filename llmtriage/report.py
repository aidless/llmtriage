"""把多条 Finding 合成一份可交付的分诊报告（Markdown + JSON）。"""
from __future__ import annotations

from typing import Iterable

from .finding import Finding, SEVERITY

OK_VERDICTS = {"OK", "READY", "GO", "MEASURED", "NO_ERROR_FOUND"}


def overall(findings: list[Finding]) -> tuple[str, str]:
    if not findings:
        return "UNKNOWN", "UNKNOWN"
    worst = max(findings, key=lambda f: f.severity())
    verdict = worst.verdict
    if verdict == "UNKNOWN":
        return "UNKNOWN", "UNKNOWN"
    states = {f.state for f in findings}
    state = "UNKNOWN" if "UNKNOWN" in states and verdict in OK_VERDICTS else "OBSERVED"
    if "INFERRED" in states and state != "UNKNOWN":
        state = "INFERRED"
    return verdict, state


def build(findings: Iterable[Finding], title: str = "LLM 推理部署分诊报告",
          meta: dict | None = None) -> dict:
    findings = list(findings)
    verdict, state = overall(findings)
    summary = _summary_line(findings)
    md = _markdown(title, meta or {}, findings, verdict, state, summary)
    return {
        "title": title,
        "meta": meta or {},
        "findings": [f.to_dict() for f in findings],
        "summary": summary,
        "verdict": verdict,
        "state": state,
        "markdown": md,
    }


def _summary_line(findings: list[Finding]) -> str:
    bad = [f for f in findings if f.severity() >= 2]
    unk = [f for f in findings if f.severity() == 1]
    parts = []
    if bad:
        parts.append("%d 项异常（%s）" % (len(bad), "、".join(f.kind for f in bad)))
    if unk:
        parts.append("%d 项数据不足（%s）" % (len(unk), "、".join(f.kind for f in unk)))
    if not parts:
        parts.append("全部检查通过")
    return "；".join(parts)


def _markdown(title, meta, findings, verdict, state, summary) -> str:
    lines = ["# " + title, ""]
    if meta:
        lines.append("| 项目 | 值 |")
        lines.append("|---|---|")
        for k, v in meta.items():
            lines.append("| %s | %s |" % (k, v))
        lines.append("")
    lines.append("**总体结论：`%s`（证据状态：%s）** —— %s" % (verdict, state, summary))
    lines.append("")
    for f in findings:
        lines.append("## %s → `%s`（%s）" % (f.kind, f.verdict, f.state))
        lines.append("")
        lines.append("证据：")
        lines.append("")
        lines.append("```")
        lines.append(f.evidence or "（无）")
        lines.append("```")
        for key, val in (f.detail or {}).items():
            if val in ("", None, [], {}):
                continue
            lines.append("- **%s**：%s" % (key, val))
        lines.append("")
    lines.append("---")
    lines.append("状态说明：OBSERVED = 本次实测；INFERRED = 由实测推断；UNKNOWN = 数据不足（不等于通过）。")
    return "\n".join(lines)
