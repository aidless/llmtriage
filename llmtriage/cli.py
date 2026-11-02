"""命令行入口。退出码是契约：0 通过 / 1 发现问题 / 2 用法错误 / 3 数据不足。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .budget import estimate
from .explain import explain, format_hypotheses
from .finding import SEVERITY
from .gpu import snapshot
from .logs import analyse
from .metrics import snapshot_metrics
from .postmortem import build_postmortem, from_triage_file
from .probe import fetch_metrics_text, measure_throughput, readiness
from .report import build
from .slo import evaluate_slo, parse_samples_csv

OK_VERDICTS = {"OK", "READY", "GO", "MEASURED", "NO_ERROR_FOUND"}


def _exit_code(verdict: str) -> int:
    if verdict == "UNKNOWN":
        return 3
    return 0 if verdict in OK_VERDICTS else 1


def _emit(rep: dict, as_json: bool, out: str | None) -> int:
    if out:
        Path(out).write_text(rep["markdown"], encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "markdown"},
                     ensure_ascii=False, indent=2) if as_json else rep["markdown"])
    return _exit_code(rep["verdict"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="llmtriage", description="LLM 推理部署现场分诊")
    ap.add_argument("--version", action="version", version="llmtriage " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_gpu = sub.add_parser("gpu", help="每卡显存/利用率快照，判断是否真的在用 GPU")
    p_gpu.add_argument("--json", action="store_true")

    p_probe = sub.add_parser("probe", help="OpenAI 兼容端点就绪（可顺带测吞吐）")
    p_probe.add_argument("--base-url", required=True)
    p_probe.add_argument("--model")
    p_probe.add_argument("--measure", action="store_true", help="并发一条 chat 请求测 tok/s")
    p_probe.add_argument("--json", action="store_true")

    p_logs = sub.add_parser("logs", help="日志分诊：父进程假线索 vs 子进程真异常")
    p_logs.add_argument("--file", required=True)
    p_logs.add_argument("--json", action="store_true")

    p_bud = sub.add_parser("budget", help="运行预算：这批 token 能在 deadline 前跑完吗")
    p_bud.add_argument("--tokens", type=int, required=True)
    p_bud.add_argument("--tok-per-s", type=float, dest="tok_per_s")
    p_bud.add_argument("--deadline", type=float, required=True)
    p_bud.add_argument("--requests", type=int, default=1)
    p_bud.add_argument("--margin", type=float, default=0.2)
    p_bud.add_argument("--json", action="store_true")

    p_met = sub.add_parser("metrics", help="解析 /metrics：排队 / KV 占用 / 抢占，判是否饱和")
    p_met.add_argument("--url", help="服务根地址，如 http://127.0.0.1:8000")
    p_met.add_argument("--file", help="已抓下来的 Prometheus 文本（离线分析用）")
    p_met.add_argument("--json", action="store_true")
    p_met.add_argument("--out")

    p_slo = sub.add_parser("slo", help="按 TTFT/TPOT 目标折算 goodput（不是 tok/s）")
    p_slo.add_argument("--csv", required=True, help="两列 ttft_s,tpot_s 的 CSV")
    p_slo.add_argument("--ttft-slo", type=float, required=True, dest="ttft_slo")
    p_slo.add_argument("--tpot-slo", type=float, required=True, dest="tpot_slo")
    p_slo.add_argument("--target-goodput", type=float, default=0.99, dest="target_goodput")
    p_slo.add_argument("--json", action="store_true")
    p_slo.add_argument("--out")

    p_exp = sub.add_parser("explain", help="把分诊结论压成排序假设 + 下一句检查")
    p_exp.add_argument("--from", dest="src", help="triage --json 的产物")
    p_exp.add_argument("--log")
    p_exp.add_argument("--metrics")
    p_exp.add_argument("--out")

    p_tri = sub.add_parser("triage", help="一键跑全部可用检查并出报告")
    p_tri.add_argument("--base-url")
    p_tri.add_argument("--model")
    p_tri.add_argument("--log")
    p_tri.add_argument("--tokens", type=int)
    p_tri.add_argument("--tok-per-s", type=float, dest="tok_per_s")
    p_tri.add_argument("--deadline", type=float)
    p_tri.add_argument("--requests", type=int, default=1)
    p_tri.add_argument("--json", action="store_true")
    p_tri.add_argument("--out", help="把 Markdown 报告写到文件")
    p_tri.add_argument("--no-metrics", action="store_true", dest="no_metrics",
                       help="不抓 /metrics（默认给了 --base-url 就会抓）")
    p_tri.add_argument("--explain", action="store_true",
                       help="在报告末尾附上排序假设与下一句检查")

    p_pm = sub.add_parser("postmortem", help="由分诊结果生成事故复盘（根因三态，不猜）")
    p_pm.add_argument("--from", dest="src", help="triage --json 的产物；省略则用 --log/--base-url 现跑")
    p_pm.add_argument("--base-url")
    p_pm.add_argument("--log")
    p_pm.add_argument("--title", default="LLM 推理部署事件")
    p_pm.add_argument("--date", default="")
    p_pm.add_argument("--impact", default="")
    p_pm.add_argument("--root-cause", default="")
    p_pm.add_argument("--root-cause-state", default="UNRESOLVED",
                      choices=["CONFIRMED", "SUSPECTED", "UNRESOLVED"])
    p_pm.add_argument("--action", action="append", default=[])
    p_pm.add_argument("--verify", action="append", default=[])
    p_pm.add_argument("--timeline", action="append", default=[])
    p_pm.add_argument("--out", help="输出 Markdown 路径（省略则打到 stdout）")

    a = ap.parse_args(argv)

    if a.cmd == "gpu":
        rep = build([snapshot()], title="GPU 快照")
        return _emit(rep, a.json, None)

    if a.cmd == "probe":
        fs = [readiness(a.base_url)]
        if a.measure:
            model = a.model or (fs[0].models or [""])[0]
            fs.append(measure_throughput(a.base_url, model=model))
        return _emit(build(fs, title="端点就绪与吞吐"), a.json, None)

    if a.cmd == "logs":
        text = Path(a.file).read_text(encoding="utf-8", errors="replace")
        return _emit(build([analyse(text)], title="日志分诊"), a.json, None)

    if a.cmd == "budget":
        f = estimate(tokens=a.tokens, tok_per_s=a.tok_per_s, deadline_s=a.deadline,
                     requests=a.requests, margin=a.margin)
        return _emit(build([f], title="运行预算"), a.json, None)

    if a.cmd == "postmortem":
        kw = dict(title=a.title, date=a.date, impact=a.impact, root_cause=a.root_cause,
                  root_cause_state=a.root_cause_state, actions=a.action,
                  verification=a.verify, timeline=a.timeline)
        if a.src:
            md = from_triage_file(a.src, **kw)
        else:
            fs = []
            if a.base_url:
                fs.append(readiness(a.base_url))
            fs.append(snapshot())
            if a.log:
                fs.append(analyse(Path(a.log).read_text(encoding="utf-8", errors="replace")))
            md = build_postmortem(findings=fs, **kw)
        if a.out:
            Path(a.out).write_text(md, encoding="utf-8")
            print("written: %s (%d bytes)" % (a.out, len(md.encode("utf-8"))))
        else:
            print(md)
        return 0

    if a.cmd == "metrics":
        if a.file:
            text = Path(a.file).read_text(encoding="utf-8", errors="replace")
        elif a.url:
            try:
                text = fetch_metrics_text(a.url)
            except RuntimeError as exc:
                f = _unknown("metrics", str(exc))
                return _emit(build([f], title="指标快照"), a.json, a.out)
        else:
            ap.error("metrics 需要 --url 或 --file")
        return _emit(build([snapshot_metrics(text)], title="指标快照"), a.json, a.out)

    if a.cmd == "slo":
        try:
            samples = parse_samples_csv(Path(a.csv).read_text(encoding="utf-8", errors="replace"))
            f = evaluate_slo(samples, ttft_slo_s=a.ttft_slo, tpot_slo_s=a.tpot_slo,
                             target_goodput=a.target_goodput)
        except ValueError as exc:
            ap.error(str(exc))
        return _emit(build([f], title="SLO goodput"), a.json, a.out)

    if a.cmd == "explain":
        fs = _collect(a)
        hyp = explain(fs)
        print(format_hypotheses(hyp))
        if a.out:
            Path(a.out).write_text(format_hypotheses(hyp), encoding="utf-8")
        return 0 if hyp else 3

    # triage
    fs = []
    if a.base_url:
        fs.append(readiness(a.base_url))
        if not a.no_metrics:
            try:
                fs.append(snapshot_metrics(fetch_metrics_text(a.base_url)))
            except RuntimeError as exc:
                fs.append(_unknown("metrics", str(exc)))
    fs.append(snapshot())
    if a.log:
        fs.append(analyse(Path(a.log).read_text(encoding="utf-8", errors="replace")))
    if a.tokens and a.deadline:
        fs.append(estimate(tokens=a.tokens, tok_per_s=a.tok_per_s, deadline_s=a.deadline,
                           requests=a.requests))
    rep = build(fs, title="LLM 推理部署分诊报告")
    if a.explain:
        rep["hypotheses"] = [h.to_dict() for h in explain(fs)]
        rep["markdown"] = rep["markdown"] + "\n\n" + format_hypotheses(explain(fs))
    return _emit(rep, a.json, a.out)


if __name__ == "__main__":
    sys.exit(main())


def _unknown(kind: str, evidence: str):
    from .finding import Finding
    return Finding(kind=kind, verdict="UNKNOWN", state="UNKNOWN", evidence=evidence)


def _collect(a) -> list:
    """explain 的取证：--from 读 triage 产物，否则现跑能跑的那几项。"""
    fs = []
    if getattr(a, "src", None):
        data = json.loads(Path(a.src).read_text(encoding="utf-8"))
        fs.extend(_findings_from_json(data))
    if getattr(a, "log", None):
        fs.append(analyse(Path(a.log).read_text(encoding="utf-8", errors="replace")))
    if getattr(a, "metrics", None):
        fs.append(snapshot_metrics(Path(a.metrics).read_text(encoding="utf-8", errors="replace")))
    return fs


def _findings_from_json(data) -> list:
    from .finding import Finding
    out = []
    for d in data.get("findings", []) if isinstance(data, dict) else []:
        known = {k: v for k, v in d.items()
                 if k in Finding.__dataclass_fields__}
        out.append(Finding(**known))
    return out
