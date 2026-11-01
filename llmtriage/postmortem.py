"""事故复盘报告：把一次分诊变成可以直接贴进工单/周报的文档。

设计上只有一条硬规矩：**不确定就写不确定**。
- 根因必须三态声明：CONFIRMED（有证据闭合）/ SUSPECTED（有线索未闭合）/ UNRESOLVED（不知道）。
- 没有验证证据时，验证小节显式写"未验证"，绝不让读者以为修好了。
- 报告里不出现"已修复"这类断言——修复与否由验证小节承担，而不是由措辞承担。
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

ROOT_CAUSE_STATES = ("CONFIRMED", "SUSPECTED", "UNRESOLVED")

STATE_NOTE = {
    "CONFIRMED": "有证据闭合：按该原因处置后，验证项全部通过。",
    "SUSPECTED": "有线索但未闭合：处置可能有效，但尚未排除其他因素。",
    "UNRESOLVED": "根因未确定：以下处置只用于限制影响，不是根因修复。",
}


def _as_dict(f: Any) -> dict:
    if isinstance(f, dict):
        return f
    if hasattr(f, "to_dict"):
        return f.to_dict()
    raise TypeError("finding must be a Finding or a dict, got %r" % type(f))


def _verdict_of(f: dict) -> str:
    return f.get("verdict", "UNKNOWN")


def _is_bad(f: dict) -> bool:
    return _verdict_of(f) not in {"OK", "READY", "GO", "MEASURED", "NO_ERROR_FOUND"}


def _evidence_list(findings: Sequence[dict]) -> list[str]:
    items = []
    for f in findings:
        ev = (f.get("evidence") or "").strip()
        if not ev:
            continue
        for line in ev.splitlines():
            line = line.strip()
            if line:
                items.append("[%s/%s] %s" % (f.get("kind", "?"), _verdict_of(f), line))
    return items


def _summary_line(title: str, findings: Sequence[dict], verdict_state: str) -> str:
    bad = [_verdict_of(f) for f in findings if _is_bad(f)]
    unk = sum(1 for f in findings if _verdict_of(f) == "UNKNOWN")
    bits = ["本次事件「%s」共采集 %d 条证据。" % (title, len(findings))]
    if bad:
        bits.append("异常项：" + "、".join(sorted(set(bad))) + "。")
    if unk:
        bits.append("其中 %d 项数据不足（UNKNOWN），已按「未确认」处理。" % unk)
    if not bad and not unk:
        bits.append("未发现异常项。")
    return " ".join(bits)


def build_postmortem(title: str,
                     findings: Iterable[Any],
                     date: str,
                     impact: str = "",
                     root_cause: str = "",
                     root_cause_state: str = "UNRESOLVED",
                     actions: Sequence[str] = (),
                     verification: Sequence[str] = (),
                     timeline: Sequence[str] = (),
                     ) -> str:
    if root_cause_state not in ROOT_CAUSE_STATES:
        raise ValueError("root_cause_state must be one of %s" % (ROOT_CAUSE_STATES,))

    fs = [_as_dict(f) for f in findings]
    worst = "OK"
    for f in fs:
        if _is_bad(f):
            worst = _verdict_of(f)
            break
    lines: list[str] = []

    lines += ["# 事后复盘：%s" % title, ""]
    lines += ["| 项目 | 值 |", "|---|---|",
              "| 日期 | %s |" % date,
              "| 分诊结论 | `%s` |" % worst,
              "| 根因状态 | `%s` |" % root_cause_state,
              "| 证据条数 | %d |" % len(fs), ""]

    lines += ["## 摘要", "", _summary_line(title, fs, root_cause_state), ""]

    lines += ["## 影响", "", impact or "（未填写：请在提交前补充影响范围与规模）", ""]

    lines += ["## 时间线", ""]
    if timeline:
        lines += ["| 时刻 | 事件 |", "|---|---|"]
        for t in timeline:
            if "|" in t:
                when, what = t.split("|", 1)
                lines.append("| %s | %s |" % (when.strip(), what.strip()))
            else:
                lines.append("| — | %s |" % t)
    else:
        lines.append("（无结构化时间线；可用 `--timeline \"10:00:09|引擎退出\"` 补充）")
    lines.append("")

    lines += ["## 分诊发现", ""]
    for f in fs:
        lines += ["### %s → `%s`（%s）" % (f.get("kind", "?"), _verdict_of(f), f.get("state", "UNKNOWN")), ""]
        ev = (f.get("evidence") or "").strip()
        if ev:
            lines += ["```", ev, "```", ""]
        for k, v in (f.get("detail") or {}).items():
            if v in ("", None, [], {}):
                continue
            lines.append("- **%s**：%s" % (k, v))
        if (f.get("detail") or {}):
            lines.append("")

    lines += ["## 根因分析", ""]
    lines += ["**状态：`%s`** —— %s" % (root_cause_state, STATE_NOTE[root_cause_state]), ""]
    if root_cause_state == "UNRESOLVED":
        lines += ["根因未确定。已排除的假设与仍需采集的证据：", ""]
        for f in fs:
            if _verdict_of(f) == "UNKNOWN":
                lines.append("- `%s` 仍为 UNKNOWN：%s" % (f.get("kind", "?"),
                                                          (f.get("evidence") or "").splitlines()[0][:160]))
        lines.append("")
    elif root_cause:
        lines += [root_cause, ""]
    else:
        lines += ["（声明为 %s 但未填写根因描述，请补充）" % root_cause_state, ""]

    lines += ["## 处置动作", ""]
    if actions:
        for i, a in enumerate(actions, 1):
            lines.append("%d. %s" % (i, a))
    else:
        lines.append("（无处置动作）")
    lines.append("")

    lines += ["## 验证", ""]
    if verification:
        for v in verification:
            lines.append("- [x] %s" % v)
    else:
        lines += ["**未验证**：本次没有可用于确认处置生效的证据。",
                  "在补充验证前，不得把该事件标记为已解决。"]
    lines.append("")

    lines += ["## 未解决项", ""]
    open_items = []
    if root_cause_state != "CONFIRMED":
        open_items.append("根因仍为 `%s`，需继续定位。" % root_cause_state)
    if not verification:
        open_items.append("缺少验证证据，处置有效性未确认。")
    for f in fs:
        if _verdict_of(f) == "UNKNOWN":
            open_items.append("`%s` 数据不足，需补采证据。" % f.get("kind", "?"))
    if open_items:
        lines += ["- " + x for x in open_items]
    else:
        lines.append("（无）")
    lines.append("")

    lines += ["## 附录：证据清单", ""]
    ev = _evidence_list(fs)
    if ev:
        lines += ["```", *ev, "```"]
    else:
        lines.append("（无证据行）")
    lines.append("")

    lines += ["---", "",
              "状态说明：`CONFIRMED` 有证据闭合 / `SUSPECTED` 有线索未闭合 / `UNRESOLVED` 根因未确定。",
              "本报告由 llmtriage 生成；每条结论的原始证据见上文代码块。"]
    return "\n".join(lines)


def from_triage_file(path: str, title: str = "LLM 推理部署事件",
                     date: str = "", impact: str = "",
                     root_cause: str = "", root_cause_state: str = "UNRESOLVED",
                     actions: Sequence[str] = (), verification: Sequence[str] = (),
                     timeline: Sequence[str] = ()) -> str:
    """从 `llmtriage triage --json` 的产物生成复盘。只给分诊结果时，根因默认 UNRESOLVED。"""
    payload = json.loads(open(path, encoding="utf-8").read())
    findings = payload.get("findings") or []
    return build_postmortem(
        title=title, findings=findings, date=date, impact=impact,
        root_cause=root_cause, root_cause_state=root_cause_state,
        actions=actions, verification=verification, timeline=timeline,
    )
