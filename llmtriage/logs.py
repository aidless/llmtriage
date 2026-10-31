"""日志分诊：分清"父进程的假线索"和"真错误"。

真实事故（vLLM on ROCm）：父进程日志只有
    Engine core initialization failed. Failed core proc(s): {}
这句话什么都说明不了——真正的原因写在子进程的 stderr 里：
    (EngineCore_DP0 pid=48213) ImportError: libflash_attn_2_cuda.so: cannot open shared object file
照父进程结论去查，会绕很久；照子进程结论（镜像里 flash_attn 残缺）才能一步修好。
"""
from __future__ import annotations

import re

from .finding import Finding

FAKE_LEAD_PATTERNS = (
    r"Failed core proc\(s\)",
    r"Engine core initialization failed",
    r"EngineCore.*(failed|died)",
)

TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\)")
EXC_LINE_RE = re.compile(r"^\s*(?:\((?P<proc>[\w\-]+) pid=(?P<pid>\d+)\)\s*)?"
                         r"(?P<exc>[A-Za-z_][\w.]*(?:Error|Exception|Interrupt)):\s*(?P<msg>.+)$")
CHILD_TAG_RE = re.compile(r"\((?P<proc>[\w\-]+) pid=(?P<pid>\d+)\)")


def analyse(text: str) -> Finding:
    lines = text.splitlines()
    has_fake_lead = any(re.search(p, line) for line in lines for p in FAKE_LEAD_PATTERNS)

    # 子进程 stderr 行：带 (EngineCore_DP0 pid=123) 前缀，且内容是异常
    child_exc = []
    child_pid = None
    child_proc = None
    for line in lines:
        m = EXC_LINE_RE.match(line)
        if not m:
            continue
        tag = CHILD_TAG_RE.search(line)
        if tag:
            child_exc.append(line.strip())
            child_pid = tag.group("pid")
            child_proc = tag.group("proc")

    # 父进程自己的 traceback（无进程前缀）
    parent_tb = [i for i, line in enumerate(lines) if TRACEBACK_RE.search(line)]
    parent_exc = [line.strip() for line in lines
                  if EXC_LINE_RE.match(line) and not CHILD_TAG_RE.search(line)]

    if has_fake_lead and child_exc:
        return Finding(
            kind="logs", verdict="FAKE_LEAD_SUBPROCESS_ERROR", state="OBSERVED",
            evidence="父进程假线索 + 子进程真实异常：\n" + "\n".join(child_exc[:5]),
            child_pid=child_pid,
            detail={"child_process": child_proc, "parent_lead": next(
                (l.strip() for l in lines if any(re.search(p, l) for p in FAKE_LEAD_PATTERNS)), ""),
                "child_exceptions": child_exc[:5],
                "next_step": "以子进程异常为准去查（缺包/驱动/权限），不要照父进程提示去查"},
        )

    if parent_tb and parent_exc:
        return Finding(
            kind="logs", verdict="GENUINE_PARENT_ERROR", state="OBSERVED",
            evidence="父进程自身 traceback：\n" + "\n".join(parent_exc[:5]),
            detail={"exceptions": parent_exc[:5]},
        )

    if has_fake_lead:
        return Finding(
            kind="logs", verdict="UNKNOWN", state="UNKNOWN",
            evidence="只看到 'Failed core proc(s)' 这类父进程假线索，但日志里没有子进程 stderr —— "
                     "信息不足，需要拿到子进程日志（多数框架会写到单独文件或 stderr 尾部）",
            detail={"hint": "先找 EngineCore_* / Worker_* 子进程的输出，再判定原因"},
        )

    return Finding(
        kind="logs", verdict="NO_ERROR_FOUND", state="OBSERVED",
        evidence="日志中未匹配到已知错误模式（这只说明'没找到'，不等于'健康'）",
        detail={"lines_scanned": len(lines)},
    )
