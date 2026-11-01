"""GPU 快照：判断"token 到底在 GPU 上跑，还是悄悄退回 CPU"。

为什么需要这一步：本机实测过 4.32 tok/s 的吞吐，比该卡带宽允许值低约 40 倍。
只看吞吐无法区分两种情况——(a) 真的在 GPU 上，这就是硬件上限；
(b) 层放置异常/回退到 CPU，GPU 显存几乎为零。必须看每卡显存与利用率。
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from .finding import Finding

NVIDIA_QUERY = ("nvidia-smi",
                "--query-gpu=index,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits")

# ROCm 的 rocm-smi --csv 输出列名随版本变化（且大小写/空格不一致），
# 所以解析必须按列名做子串匹配，绝不能按位置。
ROCM_QUERY = ("rocm-smi", "--showmeminfo", "vram", "--showuse", "--csv")

MB = 1024 * 1024

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


def _find_col(header: list[str], *needles: str) -> int | None:
    """按列名找下标：所有 needle 都必须是某个列名的子串（大小写不敏感）。"""
    for i, name in enumerate(header):
        low = name.lower()
        if all(n.lower() in low for n in needles):
            return i
    return None


def parse_rocm_smi(text: str) -> list[GpuSample]:
    """解析 `rocm-smi --showmeminfo vram --showuse --csv`。

    列名认不全就抛 ValueError —— 上层会把它转成 UNKNOWN，
    宁可不说话，也不按位置瞎解出错误的显存数字。
    """
    rows = [ln for ln in text.splitlines() if ln.strip()]
    if len(rows) < 2:
        raise ValueError("rocm-smi output has no data rows: %r" % text[:200])

    header = [c.strip().strip('"') for c in rows[0].split(",")]
    i_dev = _find_col(header, "device")
    i_total = _find_col(header, "vram", "total memory")
    i_used = _find_col(header, "vram", "used memory")
    i_use = _find_col(header, "gpu use") or _find_col(header, "use")
    if i_dev is None or i_total is None or i_used is None:
        raise ValueError("unrecognised rocm-smi columns: %r" % header)

    samples: list[GpuSample] = []
    for line in rows[1:]:
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) <= max(i_dev, i_total, i_used):
            continue
        dev = parts[i_dev]
        digits = "".join(ch for ch in dev if ch.isdigit())
        try:
            index = int(digits) if digits else len(samples)
            total = int(float(parts[i_total]))
            used = int(float(parts[i_used]))
            util = int(float(parts[i_use])) if i_use is not None and parts[i_use] not in ("", "N/A") else None
        except ValueError:
            continue
        samples.append(GpuSample(index, round(used / MB), round(total / MB), util))
    if not samples:
        raise ValueError("no ROCm GPU rows parsed: %r" % text[:200])
    return samples


def classify_sample(s: GpuSample) -> str:
    if s.mem_frac >= IN_USE_MEM_FRAC or (s.util_pct or 0) >= IN_USE_UTIL:
        return "in_use"
    return "idle"


def _run(cmd: tuple[str, ...], env_path: str | None) -> subprocess.CompletedProcess | None:
    env = None
    if env_path:
        env = dict(os.environ, PATH=env_path)
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True, timeout=30, env=env)
    except FileNotFoundError:
        return None
    except subprocess.SubprocessError as exc:
        raise RuntimeError("%s failed: %s" % (cmd[0], exc)) from exc


def _finding_from_samples(samples: list[GpuSample], evidence: str, backend: str) -> Finding:
    classes = [classify_sample(s) for s in samples]
    verdict = "OK" if "in_use" in classes else "DEGRADED"
    hint = ""
    if "in_use" not in classes:
        hint = ("所有卡都空闲/显存接近 0：如果服务自称在生成，token 很可能在 CPU 上产生，"
                "应先修层放置/显存配置，而不是继续砍 token 预算。")
    return Finding(
        kind="gpu", verdict=verdict, state="OBSERVED", evidence=evidence,
        detail={
            "backend": backend,
            "gpus": [{"index": s.index, "mem_used_mb": s.mem_used_mb,
                      "mem_total_mb": s.mem_total_mb, "util_pct": s.util_pct,
                      "class": c} for s, c in zip(samples, classes)],
            "hint": hint,
        },
    )


def snapshot(env_path: str | None = None) -> Finding:
    """先试 nvidia-smi，找不到再试 rocm-smi。两条都没有 ⇒ UNKNOWN，不冒充'正常'。"""
    p = _run(NVIDIA_QUERY, env_path)
    if p is not None:
        if p.returncode != 0:
            return Finding(kind="gpu", verdict="UNKNOWN", state="UNKNOWN",
                           evidence="nvidia-smi exit=%d: %s" % (p.returncode, p.stderr.strip()[:300]))
        try:
            samples = parse_nvidia_smi(p.stdout)
        except ValueError as exc:
            return Finding(kind="gpu", verdict="UNKNOWN", state="UNKNOWN", evidence=str(exc))
        return _finding_from_samples(samples, " ".join(NVIDIA_QUERY) + "  ->  " +
                                     p.stdout.strip().replace("\n", " | "), "nvidia-smi")

    p = _run(ROCM_QUERY, env_path)
    if p is not None:
        if p.returncode != 0:
            return Finding(kind="gpu", verdict="UNKNOWN", state="UNKNOWN",
                           evidence="rocm-smi exit=%d: %s" % (p.returncode, p.stderr.strip()[:300]))
        try:
            samples = parse_rocm_smi(p.stdout)
        except ValueError as exc:
            return Finding(kind="gpu", verdict="UNKNOWN", state="UNKNOWN", evidence=str(exc))
        return _finding_from_samples(samples, " ".join(ROCM_QUERY) + "  ->  " +
                                     p.stdout.strip().replace("\n", " | "), "rocm-smi")

    return Finding(kind="gpu", verdict="UNKNOWN", state="UNKNOWN",
                   evidence="neither nvidia-smi nor rocm-smi found on PATH",
                   detail={"hint": "装驱动与工具链，或手工跑 nvidia-smi / rocm-smi 贴输出"})
