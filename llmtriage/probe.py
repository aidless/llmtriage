"""OpenAI 兼容端点的就绪探针与吞吐量测（只用标准库）。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .finding import Finding


def _get_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 (本地内网端点)
        return resp.status, json.loads(resp.read().decode("utf-8"))


def fetch_metrics_text(base_url: str, timeout: float = 5.0) -> str:
    """取 Prometheus 文本格式的 /metrics。失败抛 RuntimeError，由 CLI 转成 UNKNOWN。"""
    url = base_url.rstrip("/") + "/metrics"
    req = urllib.request.Request(url, headers={"Accept": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310 (本地内网端点)
            return resp.read().decode("utf-8", "replace")
    except Exception as exc:                                          # noqa: BLE001
        raise RuntimeError("%s 取不到：%s" % (url, exc)) from exc


def readiness(base_url: str, timeout: float = 5.0) -> Finding:
    """GET /v1/models。连不上/非 200 ⇒ NOT_READY（state=OBSERVED，因为这是实测结果）。"""
    url = base_url.rstrip("/") + "/v1/models"
    try:
        status, payload = _get_json(url, timeout)
    except urllib.error.HTTPError as exc:
        return Finding(kind="probe", verdict="NOT_READY", state="OBSERVED", ready=False,
                       evidence="GET %s -> HTTP %d" % (url, exc.code))
    except Exception as exc:                                          # 连不上/DNS/超时
        return Finding(kind="probe", verdict="NOT_READY", state="OBSERVED", ready=False,
                       evidence="GET %s -> %s: %s" % (url, type(exc).__name__, exc))

    models = [m.get("id", "") for m in payload.get("data", []) if isinstance(m, dict)]
    state = "OBSERVED" if status == 200 else "OBSERVED"
    return Finding(kind="probe", verdict="READY" if status == 200 else "NOT_READY",
                   state=state, ready=status == 200, models=models,
                   evidence="GET %s -> HTTP %d, models=%s" % (url, status, models))


def measure_throughput(base_url: str, model: str, prompt: str = "hi",
                       timeout: float = 120.0) -> Finding:
    """发一条非流式 chat 请求，用 usage.completion_tokens / 墙钟 得到 tok/s。

    注意：这是单请求口径（batch=1），只用于估预算，不能当服务吞吐上限。
    """
    url = base_url.rstrip("/") + "/v1/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 64,
        "stream": False,
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:    # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return Finding(kind="throughput", verdict="UNKNOWN", state="UNKNOWN",
                       evidence="POST %s -> %s: %s" % (url, type(exc).__name__, exc))
    elapsed = time.perf_counter() - t0

    usage = payload.get("usage") or {}
    n_out = usage.get("completion_tokens")
    if not n_out:
        return Finding(kind="throughput", verdict="UNKNOWN", state="UNKNOWN",
                       evidence="响应里没有 usage.completion_tokens，无法计算吞吐")
    tps = n_out / elapsed
    return Finding(kind="throughput", verdict="MEASURED", state="OBSERVED",
                   completion_tokens=n_out, tok_per_s=round(tps, 4),
                   evidence="POST %s: %d tok / %.2fs = %.2f tok/s（单请求口径）" % (
                       url, n_out, elapsed, tps),
                   detail={"elapsed_s": round(elapsed, 4), "prompt_tokens": usage.get("prompt_tokens")})
