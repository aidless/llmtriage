"""Triage tests — written BEFORE the implementation (RED first).

Contract under test (exit codes are part of the public API):
    0 = all checks passed
    1 = a problem was found (NO_GO / DEGRADED / FAKE_LEAD / GENUINE_ERROR)
    2 = usage error
    3 = insufficient data (UNKNOWN) — never silently treated as success
"""

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


# ---------------------------------------------------------------- gpu parsing
NVIDIA_CSV = """0, 22184, 24576, 97
1, 12, 24576, 0
"""


def test_parse_nvidia_csv():
    from llmtriage.gpu import parse_nvidia_smi

    samples = parse_nvidia_smi(NVIDIA_CSV)
    assert len(samples) == 2
    assert samples[0].index == 0
    assert samples[0].mem_used_mb == 22184
    assert samples[0].mem_total_mb == 24576
    assert samples[0].util_pct == 97


def test_classify_in_use_vs_idle():
    from llmtriage.gpu import classify_sample, parse_nvidia_smi

    loaded, free = parse_nvidia_smi(NVIDIA_CSV)
    assert classify_sample(loaded) == "in_use"
    assert classify_sample(free) == "idle"


def test_parse_empty_output_is_unknown_not_zero():
    from llmtriage.gpu import parse_nvidia_smi

    with pytest.raises(ValueError):
        parse_nvidia_smi("")


# ------------------------------------------------------------ runtime budget
def test_budget_go():
    from llmtriage.budget import estimate

    r = estimate(tokens=12000, tok_per_s=4.32, deadline_s=7200)
    assert r.verdict == "GO"
    assert 2770 < r.wall_clock_s < 2790
    assert r.state == "OBSERVED"


def test_budget_no_go_against_quota_wall():
    """12000 tok @ 4.32 tok/s = ~46 min/request; two requests blow a 1.71h wall."""
    from llmtriage.budget import estimate

    r = estimate(tokens=12000, tok_per_s=4.32, deadline_s=4500, requests=2, margin=0.2)
    assert r.verdict == "NO_GO"
    assert r.required_s > 4500


def test_budget_unknown_when_throughput_missing():
    from llmtriage.budget import estimate

    r = estimate(tokens=12000, tok_per_s=0, deadline_s=7200)
    assert r.verdict == "UNKNOWN"
    assert r.state == "UNKNOWN"


# -------------------------------------------------- log: fake lead vs genuine
FAKE_LEAD_LOG = """
INFO 10:00:01 Starting vLLM server for qwen3_4b
INFO 10:00:03 Loading model weights
ERROR 10:00:09 Engine core initialization failed. Failed core proc(s): {}
INFO 10:00:09 Shutting down
Traceback (most recent call last):
  File "vllm/engine/async_llm_engine.py", line 214, in from_engine_args
    raise RuntimeError("Engine core initialization failed")
RuntimeError: Engine core initialization failed
"""

CHILD_LOG_LINES = """
(EngineCore_DP0 pid=48213)   File "vllm/model_executor/layers/rotary_embedding.py", line 137, in <module>
(EngineCore_DP0 pid=48213)     from flash_attn.ops.triton.rotary import apply_rotary
(EngineCore_DP0 pid=48213) ImportError: libflash_attn_2_cuda.so: cannot open shared object file
"""

GENUINE_PARENT_ERROR = """
ERROR 11:02:00 failed to load checkpoint /models/qwen3
Traceback (most recent call last):
  File "vllm/model_executor/model_loader/loader.py", line 88, in load_model
    state = torch.load(ckpt)
FileNotFoundError: [Errno 2] No such file or directory: '/models/qwen3/model.safetensors'
"""

CLEAN_LOG = """
INFO 12:00:00 server started on :8000
INFO 12:00:05 GET /v1/models 200
INFO 12:03:10 POST /v1/chat/completions 200
"""


def test_fake_lead_requires_child_process_stderr():
    from llmtriage.logs import analyse

    r = analyse(FAKE_LEAD_LOG + CHILD_LOG_LINES)
    assert r.verdict == "FAKE_LEAD_SUBPROCESS_ERROR"
    assert "flash_attn" in r.evidence
    assert r.child_pid == "48213"


def test_negative_control_genuine_parent_error_not_flagged_as_fake_lead():
    from llmtriage.logs import analyse

    r = analyse(GENUINE_PARENT_ERROR)
    assert r.verdict == "GENUINE_PARENT_ERROR"
    assert r.verdict != "FAKE_LEAD_SUBPROCESS_ERROR"


def test_clean_log_is_no_error_found_not_healthy():
    from llmtriage.logs import analyse

    r = analyse(CLEAN_LOG)
    assert r.verdict == "NO_ERROR_FOUND"
    assert r.state == "OBSERVED"


# --------------------------------------------------------------- probe (live)
class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            body = json.dumps({"data": [{"id": "qwen3_4b", "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        time.sleep(0.05)  # pretend to generate
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": "ok"}}],
             "usage": {"completion_tokens": 20, "prompt_tokens": 5, "total_tokens": 25}}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def live_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % srv.server_port
    srv.shutdown()


def test_probe_ready_against_live_openai_stub(live_server):
    from llmtriage.probe import readiness

    r = readiness(live_server, timeout=5)
    assert r.ready is True
    assert r.models == ["qwen3_4b"]
    assert r.state == "OBSERVED"


def test_probe_not_ready_on_404():
    from llmtriage.probe import readiness

    r = readiness("http://127.0.0.1:1", timeout=1)
    assert r.ready is False
    assert r.state == "OBSERVED"


def test_probe_measures_throughput_from_usage(live_server):
    from llmtriage.probe import measure_throughput

    r = measure_throughput(live_server, model="qwen3_4b", prompt="hi", timeout=10)
    assert r.completion_tokens == 20
    assert r.tok_per_s > 0


# ------------------------------------------------------------------- report
def test_report_json_schema_and_states():
    from llmtriage.budget import estimate
    from llmtriage.logs import analyse
    from llmtriage.report import build

    findings = [estimate(tokens=12000, tok_per_s=4.32, deadline_s=4500, requests=2)]
    rep = build(findings, title="t", meta={"host": "x"})
    assert set(rep) >= {"title", "meta", "findings", "summary", "verdict", "state"}
    for f in rep["findings"]:
        assert f["state"] in {"OBSERVED", "INFERRED", "UNKNOWN"}
    assert rep["verdict"] == "NO_GO"
    md = rep["markdown"]
    assert "NO_GO" in md
    json.dumps(rep, ensure_ascii=False)  # must be serialisable


def test_report_unknown_is_propagated_not_hidden():
    from llmtriage.report import build
    from llmtriage.budget import estimate

    rep = build([estimate(tokens=1, tok_per_s=0, deadline_s=1)])
    assert rep["verdict"] == "UNKNOWN"
    assert rep["state"] == "UNKNOWN"


# ---------------------------------------------------------------------- CLI
def _run(*args):
    return subprocess.run([sys.executable, "-m", "llmtriage", *args],
                          capture_output=True, text=True, timeout=60)


def test_cli_budget_go_exits_0():
    p = _run("budget", "--tokens", "12000", "--tok-per-s", "4.32",
             "--deadline", "7200", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["verdict"] == "GO"


def test_cli_budget_no_go_exits_1():
    p = _run("budget", "--tokens", "12000", "--tok-per-s", "4.32",
             "--deadline", "4500", "--requests", "2", "--json")
    assert p.returncode == 1, p.stderr


def test_cli_budget_unknown_exits_3():
    p = _run("budget", "--tokens", "100", "--deadline", "60", "--json")
    assert p.returncode == 3, p.stderr


def test_cli_logs_fake_lead_exits_1(tmp_path):
    f = tmp_path / "vllm.log"
    f.write_text(FAKE_LEAD_LOG + CHILD_LOG_LINES, encoding="utf-8")
    p = _run("logs", "--file", str(f), "--json")
    assert p.returncode == 1, p.stderr
    assert json.loads(p.stdout)["verdict"] == "FAKE_LEAD_SUBPROCESS_ERROR"


def test_cli_gpu_reads_fake_nvidia_smi(tmp_path):
    """Real binary is not required: a stub on PATH must be used, not a mock object."""
    stub = tmp_path / "nvidia-smi"
    stub.write_text("#!/bin/sh\nprintf '0, 22184, 24576, 97\\n0, 1, 2\\n'\n")
    stub.chmod(0o755)
    p = subprocess.run([sys.executable, "-m", "llmtriage", "gpu", "--json"],
                       capture_output=True, text=True, timeout=60,
                       env={"PATH": "%s:/usr/bin:/bin" % tmp_path, "HOME": str(tmp_path)})
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert data["findings"][0]["state"] == "OBSERVED"


# ---------------------------------------------------------- ROCm (AMD) backend
# 真实环境是 AMD Radeon PRO W7900 / gfx1100，nvidia-smi 不存在。
# rocm-smi 的列名与顺序随版本变化 ⇒ 解析必须**按列名**映射，不能按位置。
ROCM_CSV = """device,VRAM Total Memory (B),VRAM Total Used Memory (B),GPU use (%)
card0,51091324928,45875200,97
card1,51091324928,0,0
"""

ROCM_CSV_REORDERED = """device,GPU use (%),VRAM Total Used Memory (B),VRAM Total Memory (B)
card0,97,45875200,51091324928
card1,0,0,51091324928
"""


def test_parse_rocm_smi_by_column_name():
    from llmtriage.gpu import parse_rocm_smi

    s = parse_rocm_smi(ROCM_CSV)
    assert len(s) == 2
    assert s[0].index == 0
    assert s[0].mem_total_mb == pytest.approx(51091324928 / (1024 * 1024), rel=0.01)
    assert s[0].mem_used_mb == pytest.approx(45875200 / (1024 * 1024), rel=0.01)
    assert s[0].util_pct == 97


def test_parse_rocm_smi_survives_column_reordering():
    """同一份数据换个列顺序必须解出同样的结果——这是'按列名'而非'按位置'的反证。"""
    from llmtriage.gpu import parse_rocm_smi

    a = parse_rocm_smi(ROCM_CSV)
    b = parse_rocm_smi(ROCM_CSV_REORDERED)
    assert [(x.index, x.mem_used_mb, x.mem_total_mb, x.util_pct) for x in a] == \
           [(x.index, x.mem_used_mb, x.mem_total_mb, x.util_pct) for x in b]


def test_parse_rocm_smi_unknown_schema_raises_not_guesses():
    """列名认不出来时必须抛错（上层转 UNKNOWN），绝不按位置瞎解。"""
    from llmtriage.gpu import parse_rocm_smi

    with pytest.raises(ValueError):
        parse_rocm_smi("device,temperature,power\ncard0,45,180\n")


def test_gpu_backend_falls_back_to_rocm_when_nvidia_missing(tmp_path):
    """PATH 里只有 rocm-smi、没有 nvidia-smi 时，必须走 ROCm 分支。"""
    stub = tmp_path / "rocm-smi"
    stub.write_text("#!/bin/sh\nprintf 'device,VRAM Total Memory (B),VRAM Total Used Memory (B),GPU use (%%)\\n"
                    "card0,51091324928,45875200,97\\n'\n")
    stub.chmod(0o755)
    from llmtriage.gpu import snapshot

    f = snapshot(env_path="%s:/usr/bin:/bin" % tmp_path)
    assert f.state == "OBSERVED"
    assert f.verdict == "OK"
    assert "rocm-smi" in f.evidence
