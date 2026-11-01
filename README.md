# llmtriage

**Triage a broken LLM inference deployment in one command.** Readiness, *is the GPU actually doing the work*, *is that error message a fake lead*, and *will this batch finish before the deadline* — with an evidence line for every verdict.

[![ci](https://github.com/aidless/llmtriage/actions/workflows/ci.yml/badge.svg)](https://github.com/aidless/llmtriage/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> Status: **0.1.0, early**. Works on a single host against an OpenAI-compatible endpoint. No Kubernetes, no Prometheus, no cluster mode — see [Scope and non-goals](#scope-and-non-goals).

---

## Why this exists

Benchmarking tools answer *how fast is it*. GPU monitors answer *what is the card doing*. Neither answers the question you actually have at 2 a.m. when a deployment will not serve:

**"Is this thing broken, and if so, which layer?"**

Three failure patterns kept costing hours on real deployments, and each one is invisible to a benchmark or a monitor:

| Pattern | What you see | What is really happening |
|---|---|---|
| **Fake lead** | `Engine core initialization failed. Failed core proc(s): {}` | The real exception is in the **child process** stderr (`EngineCore_DP0 pid=…`). Following the parent's message sends you down the wrong path. |
| **Silent CPU fallback** | Throughput is terrible (e.g. 4.32 tok/s) | Per-GPU memory is near zero: tokens are being generated on the **CPU**. Cutting the token budget will not help; fixing layer placement will. |
| **Budget overrun** | The run finishes *after* the deadline, with nothing persisted | tokens ÷ tok/s × requests already exceeds the wall clock. You needed that arithmetic **before** launching. |

`llmtriage` makes those three checks runnable, and forces every verdict to carry evidence and a three-state label.

## Install

```bash
pip install -e ".[dev]"     # from a clone; zero runtime dependencies (stdlib only)
```

## Usage

```bash
# 1. Is the endpoint up, and what does it serve?
llmtriage probe --base-url http://127.0.0.1:8000

# 2. Same, plus a single-request throughput measurement (feeds the budget check)
llmtriage probe --base-url http://127.0.0.1:8000 --measure --model qwen3_4b

# 3. Are the GPUs actually doing the work?
llmtriage gpu

# 4. Is this error message a fake lead?
llmtriage logs --file examples/vllm_fake_lead.log

# 5. Will this batch finish before the deadline?
llmtriage budget --tokens 12000 --tok-per-s 4.32 --deadline 7200

# Everything at once, as a Markdown report you can paste into an incident ticket
llmtriage triage --base-url http://127.0.0.1:8000 --log vllm.log \
                 --tokens 12000 --tok-per-s 4.32 --deadline 4500 --requests 2 \
                 --out triage.md
```

### Real output (no hand-written examples)

```console
$ llmtriage budget --tokens 12000 --tok-per-s 4.32 --deadline 4500 --requests 2 --json
{
  "findings": [{
    "kind": "budget",
    "verdict": "NO_GO",
    "state": "OBSERVED",
    "evidence": "12000 tok ÷ 4.32 tok/s = 2777.8s/请求；×2 请求 = 5555.6s；含 20% 余量需 6666.7s，截止 4500.0s",
    "detail": { "suggestion": "要么降 token 量到 8100 以内，要么把 deadline 提到 6667s 以上" }
  }],
  "verdict": "NO_GO"
}
# exit 1
```

```console
$ llmtriage logs --file examples/vllm_fake_lead.log
## logs → `FAKE_LEAD_SUBPROCESS_ERROR`（OBSERVED）

证据：
(EngineCore_DP0 pid=48213) ImportError: libflash_attn_2_cuda.so: cannot open shared object file
- **next_step**：以子进程异常为准去查（缺包/驱动/权限），不要照父进程提示去查
# exit 1
```

## The two contracts

### 1. Exit codes (machine-usable in CI or a runbook)

| Code | Meaning |
|---|---|
| `0` | All checks passed |
| `1` | A problem was found (`NO_GO` / `DEGRADED` / `NOT_READY` / `FAKE_LEAD_SUBPROCESS_ERROR` / `GENUINE_PARENT_ERROR`) |
| `2` | Usage error |
| `3` | **Insufficient data (`UNKNOWN`)** — never silently treated as success |

### 2. Three-state evidence

Every finding is labelled:

- `OBSERVED` — measured in this run (the command and its raw output are in `evidence`)
- `INFERRED` — derived from measurements, not directly observed
- `UNKNOWN` — not enough data. **A clean log is `NO_ERROR_FOUND`, not "healthy"**, and a missing `nvidia-smi` is `UNKNOWN`, not "0 GPUs".

This is the point of the tool: the expensive failures are the ones that *look* fine.

## Scope and non-goals

It is deliberately **not**:

- a benchmark suite → use [inference-perf](https://github.com/kubernetes-sigs/inference-perf) (TTFT/TPOT/ITL/goodput, saturation sweeps) or [llmperf](https://github.com/ray-project/llmperf)
- a GPU monitor / exporter → use [nvitop](https://github.com/XuehaiPan/nvitop), [nvtop](https://github.com/Syllo/nvtop), [DCGM exporter](https://github.com/NVIDIA/dcgm-exporter)
- a capacity simulator → it does one piece of arithmetic, transparently, on numbers you supply

Known limits, stated rather than hidden:

- `gpu` tries `nvidia-smi` first, then `rocm-smi`. ROCm parsing maps columns **by name, not by position** (column order varies across `rocm-smi` versions); an unrecognised schema returns `UNKNOWN` instead of a guessed number. `amd-smi` is not yet wired up.
- Verified against a stubbed `rocm-smi` only — **not yet run on physical AMD hardware** by this project (W7900/gfx1100 runs are planned, not done).
- `probe --measure` is **single-request (batch=1)** throughput. It is an input to a budget calculation, **not** a server throughput ceiling.
- `logs` matches a small, explicit pattern set (vLLM / SGLang engine-core style failures). Unknown stacks come back `UNKNOWN` rather than a guess.

## Prior art and how this differs

Surveyed 2026-10-31 via the GitHub API; star counts move, so treat them as a timestamp:

| Project | ★ | What it solves | What it does not |
|---|---|---|---|
| [vllm](https://github.com/vllm-project/vllm) / [sglang](https://github.com/sgl-project/sglang) | 96k / 38k | serving engine | no triage verdicts |
| [nvitop](https://github.com/XuehaiPan/nvitop) / [nvtop](https://github.com/Syllo/nvtop) | 7.2k / 11k | see GPU + processes | no inference-layer reasoning |
| [dcgm-exporter](https://github.com/NVIDIA/dcgm-exporter) | 1.9k | metrics for Prometheus | needs a metrics stack; no verdicts |
| [inference-perf](https://github.com/kubernetes-sigs/inference-perf) / [llmperf](https://github.com/ray-project/llmperf) | 266 / 1.1k | benchmark latency & throughput | assumes the deployment already works |
| **llmtriage** | — | *is the deployment broken, and at which layer* | no benchmarking, no dashboards |

## Development

```bash
python -m pytest -q      # 23 tests: NVIDIA + ROCm parsing, budget math, log triage (with a
                         # negative control), a live OpenAI-stub probe, report schema, exit codes
```

Tests are written before the implementation (RED → GREEN). Two of them are counter-proofs rather
than happy paths:

- the log classifier carries a **negative control**: a genuine parent-process traceback must *not*
  be reported as a fake lead;
- the ROCm parser is fed the **same data in a reordered column layout** and must produce identical
  results — that is what makes "map by column name" a claim instead of a comment.

## License

MIT
