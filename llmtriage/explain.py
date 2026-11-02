"""把一堆分诊结论压成"最可能的几类原因 + 下一句该跑什么"。

为什么需要：`triage` 回答"哪里不对"，但用户（尤其线上当班的人）要的是"先查哪个、
下一句敲什么命令"。文献里这一层叫 RCA（root cause analysis）：Cloud-OpsBench
(arXiv 2603.00468) 把评测做成 **evidence-grounded**——诊断必须挂在证据上；GALA+
(ASE 2026) 的输出形态正是 **ranked diagnoses + next action**。本模块只做这两件事。

硬纪律（比功能重要）：
1. 没有对应 finding 就不许产出假设——每条假设的 `from_findings` 必须是真实存在
   的 kind，否则就是编原因；
2. 每条假设必须带一条**可执行的** `next_check`（一句命令或一次观测），否则是空话；
3. 不给"置信度 процентов"这种假精度：只有 HIGH（有直接证据）/ MEDIUM（有间接证据）
   两档，并且 HIGH 只给"证据本身就指向该原因"的情形。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .finding import Finding


@dataclass
class Hypothesis:
    id: str
    cause: str
    confidence: str                  # HIGH / MEDIUM
    from_findings: list[str]
    next_check: str
    note: str = ""

    def to_dict(self) -> dict:
        d = {"id": self.id, "cause": self.cause, "confidence": self.confidence,
             "from_findings": list(self.from_findings), "next_check": self.next_check}
        if self.note:
            d["note"] = self.note
        return d


def _by(findings: list[Finding], kind: str, verdicts: set[str] | None = None) -> list[Finding]:
    return [f for f in findings
            if f.kind == kind and (verdicts is None or f.verdict in verdicts)]


def explain(findings: list[Finding]) -> list[Hypothesis]:
    out: list[Hypothesis] = []

    # 1) 假线索：父进程只报 "Failed core proc(s): {}"，真异常在子进程日志里
    fake = _by(findings, "logs", {"FAKE_LEAD_SUBPROCESS_ERROR"})
    if fake:
        out.append(Hypothesis(
            id="H-DEP-BROKEN",
            cause="引擎子进程崩溃，父进程日志里的报错是假线索（依赖包残缺或版本不匹配最常见）",
            confidence="HIGH",
            from_findings=["logs"],
            next_check="读子进程日志尾部：grep -n 'pid=' 启动日志 | tail -50；"
                       "再单独 import 可疑包（如 python -c 'import flash_attn') 看是否缺 .so",
            note=fake[0].detail.get("child_proc", "") or "",
        ))

    genuine = _by(findings, "logs", {"GENUINE_PARENT_ERROR"})
    if genuine:
        out.append(Hypothesis(
            id="H-PARENT-ERROR",
            cause="父进程自身抛异常（不是假线索）",
            confidence="MEDIUM",
            from_findings=["logs"],
            next_check="按父进程 traceback 的最后一帧定位：确认是配置缺项、路径错、还是端口占用",
        ))

    # 2) 过载：排队 + KV 打满才是"容量不够"，只有一项要分开说
    sat = _by(findings, "metrics", {"SATURATED"})
    if sat:
        d = sat[0].detail
        out.append(Hypothesis(
            id="H-OVERLOADED",
            cause="请求排队且 KV 缓存打满：吞吐被显存容量与并发上限卡住（排队 %s / %s）"
                  % (_fmt_int(d.get("waiting")), _fmt_pct(d.get("kv_usage"))),
            confidence="HIGH",
            from_findings=["metrics"],
            next_check="① 降 --max-num-seqs 观察 waiting 是否回落 ② 扩副本 ③ "
                       "开 --enable-prefix-caching（前缀共享 workload 收益最大）",
        ))

    press = _by(findings, "metrics", {"KV_PRESSURE", "QUEUE_BACKLOG"})
    if press:
        f = press[0]
        if f.verdict == "KV_PRESSURE":
            cause = "KV 缓存接近打满（%s）：下一步会被抢占，吞吐会掉" % _fmt_pct(f.detail.get("kv_usage"))
            nxt = "调低 --gpu-memory-utilization 或 --max-model-len；观察 num_preemptions_total 是否增长"
        else:
            cause = "请求在排队（%s）但 KV 未满：瓶颈更可能在 prefill/调度，不在显存" % _fmt_int(f.detail.get("waiting"))
            nxt = "看输入长度分布与 prefill 耗时；对比 TTFT/TPOT 谁先超（llmtriage slo）"
        out.append(Hypothesis(id="H-PRESSURE", cause=cause, confidence="MEDIUM",
                              from_findings=["metrics"], next_check=nxt))

    # 3) 服务没起来
    not_ready = _by(findings, "probe", {"NOT_READY"})
    if not_ready:
        out.append(Hypothesis(
            id="H-NOT-READY",
            cause="端点未就绪：进程活着不等于模型加载完成",
            confidence="HIGH",
            from_findings=["probe"],
            next_check="看启动日志是否停在权重加载/图捕获；再确认 /v1/models 返回的 model 名与客户端一致",
        ))

    # 4) 预算与窗口不匹配（这是"排期"问题，不是性能问题）
    no_go = _by(findings, "budget", {"NO_GO"})
    if no_go:
        d = no_go[0].detail
        out.append(Hypothesis(
            id="H-BUDGET",
            cause="预算与时间窗口不匹配：按当前吞吐需要 %ss，窗口只有 %ss"
                  % (_fmt(d.get("required_s"), "所需"), _fmt(d.get("deadline_s"), "窗口")),
            confidence="MEDIUM",
            from_findings=["budget"],
            next_check="要么降 tokens（%s），要么延长 deadline；先确认 tok/s 是不是 batch=1 的读数"
                       % _fmt(d.get("suggested_max_tokens"), "建议上限"),
        ))

    # 5) SLO 谁在拖后腿
    slo = _by(findings, "slo", {"DEGRADED"})
    if slo:
        d = slo[0].detail
        out.append(Hypothesis(
            id="H-SLO",
            cause="goodput %s 低于目标 %s，主要超的是 %s"
                  % (_fmt(d.get("goodput"), "值"), _fmt(d.get("target_goodput"), "目标"),
                     {"ttft": "TTFT(prefill)", "tpot": "TPOT(decode)",
                      "both": "两者都超", "none": "无"}[d.get("binding", "none")]),
            confidence="MEDIUM",
            from_findings=["slo"],
            next_check="先看 llmtriage metrics 的排队与 KV 占用；没有过载才谈调参（batch/量化/并行度）",
        ))

    # 6) GPU 空载：这是"东西没跑在 GPU 上"，不是"跑得慢"
    idle = _by(findings, "gpu", {"DEGRADED"})
    if idle:
        out.append(Hypothesis(
            id="H-CPU-FALLBACK",
            cause="GPU 空载/显存接近 0：token 很可能在 CPU 上产生（或模型根本没上卡）",
            confidence="MEDIUM",
            from_findings=["gpu"],
            next_check="检查模型层放置/offload 配置（device_map、max_memory、tensor_parallel 是否把层挤到 CPU），"
                       "改完再重测吞吐；此时继续砍 token 预算不会变快",
        ))

    unknown_gpu = _by(findings, "gpu", {"UNKNOWN"})
    if unknown_gpu and not idle:
        out.append(Hypothesis(
            id="H-GPU-UNREADABLE",
            cause="GPU 状态读不到：既不能确认在用，也不能确认空载",
            confidence="MEDIUM",
            from_findings=["gpu"],
            next_check="手工跑 nvidia-smi / rocm-smi 贴输出；容器里先确认 /dev/kfd 与驱动库可见",
            note=unknown_gpu[0].evidence[:160],
        ))

    return out


def _fmt(value, unit: str) -> str:
    if value is None:
        return "%s未知" % unit
    try:
        return "%.2f" % float(value)
    except (TypeError, ValueError):
        return str(value)


def _fmt_pct(value) -> str:
    if value is None:
        return "占用未知"
    try:
        return "占用 %.0f%%" % (float(value) * 100)
    except (TypeError, ValueError):
        return str(value)


def format_hypotheses(hyp: list[Hypothesis]) -> str:
    """渲染成 Markdown 片段，供 CLI / 复盘文档复用。"""
    if not hyp:
        return ("## 下一步\n\n没有可产出的假设：先跑 `llmtriage triage` 收集证据（不要凭印象猜原因）。")
    lines = ["## 下一步：待验证假设（按优先级）", ""]
    for i, h in enumerate(hyp, 1):
        lines.append("### %d. [%s] %s" % (i, h.confidence, h.cause))
        lines.append("")
        lines.append("- 依据：%s" % "、".join(h.from_findings))
        lines.append("- 下一句检查：`%s`" % h.next_check)
        if h.note:
            lines.append("- 备注：%s" % h.note.replace("\n", " "))
        lines.append("")
    return "\n".join(lines)


def _fmt_int(value) -> str:
    if value is None:
        return "未知"
    try:
        return str(int(round(float(value))))
    except (TypeError, ValueError):
        return str(value)
