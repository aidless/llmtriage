"""运行预算换算：这批 token 在截止时间前跑得完吗？

真实教训：12000 token @ 4.32 tok/s ≈ 46 分钟/题，两题 1.6h 会撞上 1.71h 的配额墙，
结果跑满墙钟却一个结果都没落地。宁可先降 token 量保 JSON 先落盘。
"""
from __future__ import annotations

from .finding import Finding

DEFAULT_MARGIN = 0.2   # 20% 余量：加载、排队、抖动


def estimate(tokens: int, tok_per_s: float | None, deadline_s: float,
             requests: int = 1, margin: float = DEFAULT_MARGIN) -> Finding:
    if not tok_per_s or tok_per_s <= 0:
        return Finding(kind="budget", verdict="UNKNOWN", state="UNKNOWN",
                       evidence="tok_per_s 缺失或为 0：先测吞吐（llmtriage probe --measure）再算预算",
                       detail={"tokens": tokens, "deadline_s": deadline_s, "requests": requests})
    if tokens <= 0 or deadline_s <= 0 or requests <= 0:
        return Finding(kind="budget", verdict="UNKNOWN", state="UNKNOWN",
                       evidence="参数非法：tokens/deadline/requests 必须为正",
                       detail={"tokens": tokens, "deadline_s": deadline_s, "requests": requests})

    per_request = tokens / tok_per_s
    wall = per_request * requests
    required = wall * (1 + margin)
    verdict = "GO" if required <= deadline_s else "NO_GO"
    return Finding(
        kind="budget", verdict=verdict, state="OBSERVED",
        evidence="%d tok ÷ %.4g tok/s = %.1fs/请求；×%d 请求 = %.1fs；含 %.0f%% 余量需 %.1fs，"
                 "截止 %.1fs" % (tokens, tok_per_s, per_request, requests, wall, margin * 100,
                                required, deadline_s),
        wall_clock_s=round(wall, 1),
        required_s=round(required, 1),
        per_request_s=round(per_request, 1),
        detail={"tokens": tokens, "tok_per_s": tok_per_s, "requests": requests,
                "margin": margin, "deadline_s": deadline_s,
                "suggestion": "" if verdict == "GO" else
                "要么降 token 量到 %.0f 以内，要么把 deadline 提到 %.0fs 以上" % (
                    deadline_s / (1 + margin) * tok_per_s / requests, required)},
    )
