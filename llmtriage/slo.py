"""按 SLO 折算 goodput：把"每秒多少 token"换成"多少比例的请求真的达标"。

为什么不用 tok/s 当结论：吞吐是**在什么约束下**测出来的，脱离 TTFT/TPOT 约束的
tok/s 不可比（LSSP 的实测结论正是调度器排名跨工作负载/指标不可移植）。goodput 的
定义取自 DistServe (arXiv 2401.09670)：**同时**满足 TTFT 与 TPOT 的请求占比。

诚实边界：
- 本模块只算，不测。样本从哪来（batch=1 的单请求、还是线上真实分布）决定了结论能
  推广到哪；batch=1 的延迟不能外推到高并发。
- goodput 的分母是"送进来的请求"，不含被客户端提前放弃的请求。
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .finding import Finding

DEFAULT_TARGET_GOODPUT = 0.99


@dataclass
class RequestSample:
    ttft_s: float
    tpot_s: float


def parse_samples_csv(text: str) -> list[RequestSample]:
    """读 `ttft_s,tpot_s` 两列。空行/坏行丢弃（丢了几行会在 detail 里报）。"""
    rows = list(csv.DictReader(io.StringIO(text.strip() + "\n")))
    out: list[RequestSample] = []
    for r in rows:
        try:
            out.append(RequestSample(float(r["ttft_s"]), float(r["tpot_s"])))
        except (TypeError, ValueError, KeyError):
            continue
    return out


def percentile(values: list[float], q: float) -> float:
    """nearest-rank，q 取 0-100。不插值——插值出来的 p99 在样本少时是编的。"""
    if not values:
        raise ValueError("empty")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(q / 100.0 * len(ordered) + 0.5)) - 1))
    return ordered[k]


def evaluate_slo(samples: list[RequestSample], ttft_slo_s: float, tpot_slo_s: float,
                 target_goodput: float = DEFAULT_TARGET_GOODPUT) -> Finding:
    if ttft_slo_s <= 0 or tpot_slo_s <= 0:
        raise ValueError("SLO 目标必须为正数（单位：秒）")
    if not samples:
        return Finding(kind="slo", verdict="UNKNOWN", state="UNKNOWN",
                       evidence="没有可用样本（ttft_s/tpot_s 为空）",
                       detail={"ttft_slo_s": ttft_slo_s, "tpot_slo_s": tpot_slo_s})

    ttft = [s.ttft_s for s in samples]
    tpot = [s.tpot_s for s in samples]
    bad_ttft = sum(1 for x in ttft if x > ttft_slo_s)
    bad_tpot = sum(1 for x in tpot if x > tpot_slo_s)
    good = sum(1 for s in samples
               if s.ttft_s <= ttft_slo_s and s.tpot_s <= tpot_slo_s)
    goodput = good / len(samples)

    if bad_ttft and bad_tpot:
        binding = "both"
    elif bad_ttft:
        binding = "ttft"
    elif bad_tpot:
        binding = "tpot"
    else:
        binding = "none"

    detail = {
        "n": len(samples),
        "goodput": round(goodput, 4),
        "target_goodput": target_goodput,
        "ttft_slo_s": ttft_slo_s,
        "tpot_slo_s": tpot_slo_s,
        "violate_ttft": bad_ttft,
        "violate_tpot": bad_tpot,
        "binding": binding,
        "ttft_p50": round(percentile(ttft, 50), 4),
        "ttft_p95": round(percentile(ttft, 95), 4),
        "ttft_p99": round(percentile(ttft, 99), 4),
        "tpot_p50": round(percentile(tpot, 50), 4),
        "tpot_p95": round(percentile(tpot, 95), 4),
        "tpot_p99": round(percentile(tpot, 99), 4),
    }
    evidence = ("n=%d goodput=%.3f 目标=%.3f 超 TTFT %d 条 / 超 TPOT %d 条（SLO %.3fs / %.3fs）"
                % (len(samples), goodput, target_goodput, bad_ttft, bad_tpot,
                   ttft_slo_s, tpot_slo_s))

    if goodput >= target_goodput:
        return Finding(kind="slo", verdict="OK", state="OBSERVED", evidence=evidence, detail=detail)

    hint = {
        "ttft": "超的是 TTFT：瓶颈多在 prefill / 输入长度 / 排队，不在 decode",
        "tpot": "超的是 TPOT：瓶颈多在 decode 带宽 / KV 读取 / batch 组成",
        "both": "两个都超：先看排队与 KV 占用（llmtriage metrics），再谈调参",
        "none": "",
    }[binding]
    return Finding(kind="slo", verdict="DEGRADED", state="OBSERVED",
                   evidence=evidence, detail={**detail, "hint": hint})
