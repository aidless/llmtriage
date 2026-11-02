"""metrics / slo / explain 三个新判据的测试。

写法纪律：每条断言先红后绿（2026-11-01 首批运行应为 collect error / 全红），
并且每个"该判坏"的用例都配一个"该判好"的用例 —— 只测红不测绿的门是装饰。
"""
import pytest


# ------------------------------------------------------------------ fixtures
VLLM_METRICS_SATURATED = """# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="qwen"} 8.0
# HELP vllm:num_requests_waiting Number of requests waiting to be processed.
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting{model_name="qwen"} 47.0
# HELP vllm:gpu_cache_usage_perc GPU KV-cache usage. 1 means 100 percent usage.
# TYPE vllm:gpu_cache_usage_perc gauge
vllm:gpu_cache_usage_perc{model_name="qwen"} 0.98
# HELP vllm:num_preemptions_total Cumulative number of preemption from the engine.
# TYPE vllm:num_preemptions_total counter
vllm:num_preemptions_total{model_name="qwen"} 312.0
# HELP vllm:prefix_cache_hits_total Prefix cache hits.
# TYPE vllm:prefix_cache_hits_total counter
vllm:prefix_cache_hits_total{model_name="qwen"} 120.0
vllm:prefix_cache_queries_total{model_name="qwen"} 900.0
"""

VLLM_METRICS_IDLE = """# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="qwen"} 1.0
vllm:num_requests_waiting{model_name="qwen"} 0.0
vllm:gpu_cache_usage_perc{model_name="qwen"} 0.21
vllm:num_preemptions_total{model_name="qwen"} 0.0
"""

# 新版本把 gpu_cache_usage_perc 改名成 kv_cache_usage_perc —— 列名漂移必须兼容
VLLM_METRICS_RENAMED = """vllm:num_requests_running 2.0
vllm:num_requests_waiting 1.0
vllm:kv_cache_usage_perc 0.55
"""

VLLM_METRICS_NO_USAGE = """vllm:num_requests_running 2.0
"""


# ------------------------------------------------------------------ metrics
def test_parse_metrics_skips_comments_and_keeps_labels():
    from llmtriage.metrics import parse_metrics

    samples = parse_metrics(VLLM_METRICS_RENAMED)
    assert [s.name for s in samples] == ["vllm:num_requests_running",
                                         "vllm:num_requests_waiting",
                                         "vllm:kv_cache_usage_perc"]
    assert samples[2].value == 0.55


def test_metrics_saturated_is_flagged_with_evidence():
    from llmtriage.metrics import snapshot_metrics

    f = snapshot_metrics(VLLM_METRICS_SATURATED)
    assert f.kind == "metrics"
    assert f.state == "OBSERVED"
    assert f.verdict == "SATURATED"
    assert f.detail["waiting"] == 47.0
    assert f.detail["kv_usage"] == pytest.approx(0.98)
    assert "waiting" in f.evidence


def test_metrics_idle_is_not_flagged():
    from llmtriage.metrics import snapshot_metrics

    f = snapshot_metrics(VLLM_METRICS_IDLE)
    assert f.verdict == "OK"
    assert f.state == "OBSERVED"


def test_metrics_accepts_renamed_kv_metric():
    """改名后的 kv_cache_usage_perc 必须认，否则升级 vLLM 就静默看不见 KV 压力。"""
    from llmtriage.metrics import snapshot_metrics

    f = snapshot_metrics(VLLM_METRICS_RENAMED)
    assert f.detail["kv_usage"] == pytest.approx(0.55)
    assert f.state == "OBSERVED"


def test_metrics_missing_usage_is_unknown_not_ok():
    """缺 KV 指标时必须 UNKNOWN —— 不能因为"没报错"就判通过。"""
    from llmtriage.metrics import snapshot_metrics

    f = snapshot_metrics(VLLM_METRICS_NO_USAGE)
    assert f.verdict == "UNKNOWN"
    assert f.state == "UNKNOWN"


def test_metrics_sums_across_engines():
    """多引擎/多 model_name 的同一 gauge 必须求和，不能只取最后一行。"""
    from llmtriage.metrics import snapshot_metrics

    text = ('vllm:num_requests_waiting{model_name="a"} 5\n'
            'vllm:num_requests_waiting{model_name="b"} 7\n'
            'vllm:gpu_cache_usage_perc{model_name="a"} 0.5\n')
    f = snapshot_metrics(text)
    assert f.detail["waiting"] == 12.0


# ------------------------------------------------------------------ slo
SLO_CSV_GOOD = """ttft_s,tpot_s
0.4,0.02
0.6,0.03
0.5,0.025
"""

SLO_CSV_MIXED = """ttft_s,tpot_s
0.4,0.02
2.5,0.03
0.5,0.60
0.6,0.02
"""


def test_slo_goodput_all_pass_is_ok():
    from llmtriage.slo import evaluate_slo, parse_samples_csv

    r = evaluate_slo(parse_samples_csv(SLO_CSV_GOOD), ttft_slo_s=1.0, tpot_slo_s=0.1)
    assert r.verdict == "OK"
    assert r.state == "OBSERVED"
    assert r.detail["goodput"] == pytest.approx(1.0)
    assert r.detail["n"] == 3


def test_slo_goodput_counts_both_constraints():
    """goodput 的定义是"同时满足 TTFT 与 TPOT"，任一超了都不算。"""
    from llmtriage.slo import evaluate_slo, parse_samples_csv

    r = evaluate_slo(parse_samples_csv(SLO_CSV_MIXED), ttft_slo_s=1.0, tpot_slo_s=0.1)
    assert r.detail["goodput"] == pytest.approx(0.5)
    assert r.detail["violate_ttft"] == 1
    assert r.detail["violate_tpot"] == 1
    assert r.verdict == "DEGRADED"


def test_slo_reports_binding_constraint():
    """两个约束里谁在拖后腿必须说清楚，否则用户不知道该调 prefill 还是 decode。"""
    from llmtriage.slo import evaluate_slo, parse_samples_csv

    r = evaluate_slo(parse_samples_csv(SLO_CSV_MIXED), ttft_slo_s=1.0, tpot_slo_s=0.1)
    assert r.detail["binding"] == "both"


def test_slo_percentiles():
    from llmtriage.slo import evaluate_slo, parse_samples_csv

    rows = "ttft_s,tpot_s\n" + "".join("%.2f,0.02\n" % (i / 10.0) for i in range(1, 101))
    r = evaluate_slo(parse_samples_csv(rows), ttft_slo_s=5.0, tpot_slo_s=1.0)
    assert r.detail["ttft_p50"] == pytest.approx(5.0, abs=0.15)
    assert r.detail["ttft_p95"] == pytest.approx(9.5, abs=0.15)


def test_slo_empty_samples_is_unknown():
    from llmtriage.slo import evaluate_slo, parse_samples_csv

    r = evaluate_slo(parse_samples_csv("ttft_s,tpot_s\n"), ttft_slo_s=1.0, tpot_slo_s=0.1)
    assert r.verdict == "UNKNOWN"
    assert r.state == "UNKNOWN"


def test_slo_rejects_nonpositive_targets():
    from llmtriage.slo import evaluate_slo, parse_samples_csv

    with pytest.raises(ValueError):
        evaluate_slo(parse_samples_csv(SLO_CSV_GOOD), ttft_slo_s=0, tpot_slo_s=0.1)


# ------------------------------------------------------------------ explain
def _findings_for_explain():
    from llmtriage.budget import estimate
    from llmtriage.logs import analyse
    from llmtriage.metrics import snapshot_metrics

    return [
        analyse("Error: Failed core proc(s): {}\n"
                "(EngineCore_DP0 pid=4242) ImportError: libflash_attn_2_cuda.so: cannot open shared object file\n"),
        estimate(tokens=12000, tok_per_s=4.32, deadline_s=4500, requests=2),
        snapshot_metrics(VLLM_METRICS_SATURATED),
    ]


def test_explain_ranks_cause_for_subprocess_error_first():
    from llmtriage.explain import explain

    hyp = explain(_findings_for_explain())
    assert hyp, "有异常时必须给出至少一条假设"
    top = hyp[0]
    assert top.confidence == "HIGH"
    assert "子进程" in top.cause or "依赖" in top.cause


def test_explain_every_hypothesis_has_next_check_and_evidence():
    """每条假设都必须能指回真实存在的 finding，并给出下一句可执行的检查。"""
    from llmtriage.explain import explain

    findings = _findings_for_explain()
    kinds = {f.kind for f in findings}
    for h in explain(findings):
        assert h.next_check.strip(), h.id
        assert h.from_findings, h.id
        assert set(h.from_findings) <= kinds, h.id


def test_explain_uses_metrics_to_flag_overload():
    from llmtriage.explain import explain
    from llmtriage.metrics import snapshot_metrics

    hyp = explain([snapshot_metrics(VLLM_METRICS_SATURATED)])
    assert any("排队" in h.cause or "KV" in h.cause for h in hyp)


def test_explain_says_dont_cut_tokens_when_gpu_is_idle():
    """GPU 空载 + 低吞吐时，正确的下一步是查层放置，不是继续砍 token 预算。"""
    from llmtriage.explain import explain
    from llmtriage.gpu import GpuSample, _finding_from_samples

    idle = _finding_from_samples([GpuSample(0, 27, 49136, 0)],
                                 "rocm-smi -> card0,0", "rocm-smi")
    hyp = explain([idle])
    assert any("层放置" in h.next_check or "CPU" in h.cause for h in hyp)


def test_explain_without_findings_is_unknown():
    from llmtriage.explain import explain

    assert explain([]) == []
