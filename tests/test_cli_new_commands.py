"""新子命令的 CLI 契约测试：退出码语义 + 人可读输出。"""
from __future__ import annotations

from pathlib import Path

from llmtriage.cli import main

SATURATED = """vllm:num_requests_running 8
vllm:num_requests_waiting 47
vllm:gpu_cache_usage_perc 0.98
"""

IDLE = """vllm:num_requests_running 1
vllm:num_requests_waiting 0
vllm:gpu_cache_usage_perc 0.2
"""

NO_KV = """vllm:num_requests_running 1
"""

FAKE_LEAD_LOG = """Error: Failed core proc(s): {}
(EngineCore_DP0 pid=4242) ImportError: libflash_attn_2_cuda.so: cannot open shared object file
"""


def _p(tmp_path: Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_cli_metrics_saturated_exits_1(tmp_path, capsys):
    assert main(["metrics", "--file", _p(tmp_path, "m.txt", SATURATED)]) == 1
    out = capsys.readouterr().out
    assert "SATURATED" in out


def test_cli_metrics_idle_exits_0(tmp_path, capsys):
    assert main(["metrics", "--file", _p(tmp_path, "m.txt", IDLE)]) == 0


def test_cli_metrics_missing_kv_exits_3(tmp_path):
    """数据不足必须是 3，不能混成 0（静默通过）也不能混成 1（真告警）。"""
    assert main(["metrics", "--file", _p(tmp_path, "m.txt", NO_KV)]) == 3


def test_cli_slo_good_exits_0(tmp_path):
    csv = _p(tmp_path, "s.csv", "ttft_s,tpot_s\n0.4,0.02\n")
    assert main(["slo", "--csv", csv, "--ttft-slo", "1.0", "--tpot-slo", "0.1"]) == 0


def test_cli_slo_bad_exits_1(tmp_path, capsys):
    csv = _p(tmp_path, "s.csv", "ttft_s,tpot_s\n3.0,0.02\n")
    assert main(["slo", "--csv", csv, "--ttft-slo", "1.0", "--tpot-slo", "0.1"]) == 1
    assert "goodput" in capsys.readouterr().out


def test_cli_explain_from_log_prints_next_check(tmp_path, capsys):
    code = main(["explain", "--log", _p(tmp_path, "l.log", FAKE_LEAD_LOG)])
    out = capsys.readouterr().out
    assert code == 0
    assert "下一句检查" in out


def test_cli_explain_without_evidence_exits_3(capsys):
    assert main(["explain"]) == 3
    assert "不要凭印象猜原因" in capsys.readouterr().out


def test_cli_triage_explain_appends_hypotheses(tmp_path, capsys):
    out = tmp_path / "rep.md"
    code = main(["triage", "--log", _p(tmp_path, "l.log", FAKE_LEAD_LOG),
                 "--explain", "--out", str(out)])
    assert code in (1, 3)
    md = out.read_text(encoding="utf-8")
    assert "待验证假设" in md
    assert "下一句检查" in md
