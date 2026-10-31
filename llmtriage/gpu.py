"""GPU 快照：判断"token 到底在 GPU 上跑，还是悄悄退回 CPU"。

为什么需要这一步：本机实测过 4.32 tok/s 的吞吐，比该卡带宽允许值低约 40 倍。
只看吞吐无法区分两种情况——(a) 真的在 GPU 上，这就是硬件上限；
(b) 层放置异常/回退到 CPU，GPU 显存几乎为零。必须看每卡显存与利用率。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from .finding import Finding

NVIDIA_QUERY = ("nvidia-smi",
                "--query-gpu=index,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits")

# 判定阈值：显存占用 ≥15% 或 利用率 ≥20% 视为"该卡确实在干活"
IN_USE_MEM_FRAC = 0.15
IN_USE_UTIL = 20


@dataclass
class GpuSample:
    index: int
    mem_used_mb: int
    mem_total_mb: int
    util_pct: int | None = None

    @property
    def mem_frac(self) -> float:
        return self.mem_used_mb / self.mem_total_mb if self.mem_total_mb else 0.0


def parse_nvidia_smi(text: str) -> list[GpuSample]:
    """解析 `--format=csv,noheader,nounits` 输出。空输出抛 ValueError（不能当 0 卡）。"""
    samples: list[GpuSample] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            index = int(parts[0])
            used = int(float(parts[1]))
            total = int(float(parts[2]))
            util = int(float(parts[3])) if len(parts) > 3 and parts[3] not in ("", "[N/A]") else None
        except ValueError:
            continue
        samples.append(GpuSample(index, used, total, util))
    if not samples:
        raise ValueError("no GPU rows parsed from nvidia-smi output: %r" % text[:200])
    return samples


def classify_sample(s: GpuSample) -> str:
    if s.mem_frac >= IN_USE_MEM_FRAC or (s.util_pct or 0) >= IN_USE_UTIL:
        return "in_use"
    return "idle"


def snapshot() -> Finding:
    """跑一次 nvidia-smi 并给出结论。命令缺失/无输出 ⇒ UNKNOWN，不冒充'正常'。"""
    try:
        p = subprocess.run(list(NVIDIA_QUERY), capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return Finding(kind="gpu", verdict="UNKNOWN", state="UNKNOWN",
                       evidence="nvidia-smi not found on PATH",
                       detail={"hint": "AMD 环境请用 rocm-smi / amd-smi（见 README）"})
    except subprocess.SubprocessError as exc:
        return Finding(kind="gpu", verdict="UNKNOWN", state="UNKNOWN",
                       evidence="nvidia-smi failed: %s" % exc)
    if p.returncode != 0:
        return Finding(kind="gpu", verdict="UNKNOWN", state="UNKNOWN",
                       evidence="nvidia-smi exit=%d: %s" % (p.returncode, p.stderr.strip()[:300]))
    try:
        samples = parse_nvidia_smi(p.stdout)
    except ValueError as exc:
        return Finding(kind="gpu", verdict="UNKNOWN", state="UNKNOWN", evidence=str(exc))

    classes = [classify_sample(s) for s in samples]
    verdict = "OK" if "in_use" in classes else "DEGRADED"
    hint = ""
    if "in_use" not in classes:
        hint = ("所有卡都空闲/显存接近 0：如果服务自称在生成，token 很可能在 CPU 上产生，"
                "应先修层放置/显存配置，而不是继续砍 token 预算。")
    return Finding(
        kind="gpu", verdict=verdict, state="OBSERVED",
        evidence=" ".join(NVIDIA_QUERY) + "  ->  " + p.stdout.strip().replace("\n", " | "),
        detail={
            "gpus": [{"index": s.index, "mem_used_mb": s.mem_used_mb,
                      "mem_total_mb": s.mem_total_mb, "util_pct": s.util_pct,
                      "class": c} for s, c in zip(samples, classes)],
            "hint": hint,
        },
    )
