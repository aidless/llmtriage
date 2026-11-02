"""Prometheus 文本格式的推理服务指标解析与饱和判据。

为什么有这个模块：`probe` 只能回答"服务活没活着"，回答不了"它是不是已经在排队"。
压测/线上排查时最先要看的三件事是排队长度、KV 缓存占用、抢占次数——它们都在
`/metrics` 里，但格式是"指标名 + 标签 + 数值"，且**跨版本会改名**。

判据来源与边界（诚实标注）：
- 三个 SLI 的**语义**取自 DistServe (arXiv 2401.09670) 的排队/goodput 框架与
  LUMINOS 对 LLM 推理 SLI/SLO 的定义；
- **阈值（0.90 / 8）是我们自己定的启发式，不是论文给出的分界线**。改阈值前先测
  自己集群的基线，不要把它当成学术结论引用；
- 指标名漂移（`vllm:gpu_cache_usage_perc` -> `vllm:kv_cache_usage_perc`）必须兼容——
  和 gpu.py 里"按列名子串匹配、按列位置匹配会静默错位"是同一个教训：改名不兼容
  = 升级后静默失明，比报错更糟。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .finding import Finding

# 指标名别名：新老版本 / 不同后端
KV_USAGE_NAMES = (
    "vllm:gpu_cache_usage_perc",
    "vllm:kv_cache_usage_perc",
    "vllm:gpu_cache_usage_percent",
    "gpu_cache_usage_perc",
    "kv_cache_usage_perc",
)
WAITING_NAMES = ("vllm:num_requests_waiting", "num_requests_waiting")
RUNNING_NAMES = ("vllm:num_requests_running", "num_requests_running")
PREEMPT_NAMES = ("vllm:num_preemptions_total", "num_preemptions_total")
PREFIX_HIT_NAMES = ("vllm:prefix_cache_hits_total", "prefix_cache_hits_total",
                    "vllm:gpu_prefix_cache_hit_rate", "gpu_prefix_cache_hit_rate")
PREFIX_QUERY_NAMES = ("vllm:prefix_cache_queries_total", "prefix_cache_queries_total")

# 启发式阈值（见模块 docstring：不是论文结论）
KV_SATURATED = 0.90
WAITING_BACKLOG = 8.0

_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"\s+(?P<value>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)"
)
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


@dataclass
class Sample:
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)


def parse_metrics(text: str) -> list[Sample]:
    """解析 Prometheus 文本格式。注释行（HELP/TYPE）跳过，标签保留但不参与求和键。"""
    out: list[Sample] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        labels = dict(_LABEL_RE.findall(m.group("labels") or ""))
        out.append(Sample(m.group("name"), float(m.group("value")), labels))
    return out


def total(samples: list[Sample], names: tuple[str, ...]) -> float | None:
    """同名列跨标签求和（多 engine / 多 model_name）。一条都没有 ⇒ None，不返回 0。

    返回 0 会让"指标缺失"看起来像"排队为 0"，这是最危险的静默失败。
    """
    hits = [s.value for s in samples if s.name in names]
    return sum(hits) if hits else None


def _ratio(value: float | None) -> float | None:
    """把 usage 归一化到 0-1：有的版本吐 0.98，有的吐 98.0。"""
    if value is None:
        return None
    return value / 100.0 if value > 1.0 else value


def snapshot_metrics(text: str) -> Finding:
    samples = parse_metrics(text)
    waiting = total(samples, WAITING_NAMES)
    running = total(samples, RUNNING_NAMES)
    kv = _ratio(total(samples, KV_USAGE_NAMES))
    preemptions = total(samples, PREEMPT_NAMES)
    prefix_hits = total(samples, PREFIX_HIT_NAMES)
    prefix_query = total(samples, PREFIX_QUERY_NAMES)

    raw = [line.strip() for line in text.splitlines()
           if line.strip() and not line.strip().startswith("#")]
    evidence = " | ".join(raw[:12])

    if kv is None:
        return Finding(
            kind="metrics", verdict="UNKNOWN", state="UNKNOWN",
            evidence=evidence or "metrics 文本为空",
            detail={"parsed_samples": len(samples),
                    "hint": "没找到 KV/GPU cache usage 指标：确认端点是不是 /metrics，"
                            "以及 vLLM 版本是否改了指标名（gpu_cache_usage_perc / kv_cache_usage_perc）"},
        )

    detail = {
        "kv_usage": round(kv, 4),
        "waiting": waiting if waiting is not None else None,
        "running": running if running is not None else None,
        "preemptions_total": preemptions,
        "samples": len(samples),
        "kv_saturated_threshold": KV_SATURATED,
        "waiting_backlog_threshold": WAITING_BACKLOG,
    }
    if prefix_hits is not None and prefix_query:
        detail["prefix_hit_rate"] = round(prefix_hits / prefix_query, 4)

    waiting_high = waiting is not None and waiting >= WAITING_BACKLOG
    if waiting_high and kv >= KV_SATURATED:
        return Finding(kind="metrics", verdict="SATURATED", state="OBSERVED",
                       evidence=evidence, detail={**detail, "hint":
                       "排队 %d 且 KV 占用 %.0f%%：先降并发/扩副本，再谈压测数字" % (int(waiting), kv * 100)})
    if kv >= KV_SATURATED:
        return Finding(kind="metrics", verdict="KV_PRESSURE", state="OBSERVED",
                       evidence=evidence, detail={**detail, "hint":
                       "KV 占用 %.0f%%：抢占或降 batch 即将发生" % (kv * 100)})
    if waiting_high:
        return Finding(kind="metrics", verdict="QUEUE_BACKLOG", state="OBSERVED",
                       evidence=evidence, detail={**detail, "hint":
                       "排队 %d 但 KV 未满：瓶颈可能在 prefill/调度，不在显存" % int(waiting)})
    return Finding(kind="metrics", verdict="OK", state="OBSERVED",
                   evidence=evidence, detail=detail)
