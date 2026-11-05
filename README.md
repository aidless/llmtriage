# llmtriage

**Triage a broken LLM inference deployment in one command.** Readiness, *is the GPU actually doing the work*, *is that error message a fake lead*, and *will this batch finish before the deadline* — with an evidence line for every verdict.

[![ci](https://github.com/aidless/llmtriage/actions/workflows/ci.yml/badge.svg)](https://github.com/aidless/llmtriage/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> Status: **0.2.0, early**. Single host, OpenAI-compatible HTTP, local files. Not a production monitor. No Kubernetes, no Prometheus, no cluster mode. Not published to a package index. See [Evidence boundaries](#evidence-boundaries) and [Failure modes](#failure-modes).

**What you can re-run:** `llmtriage logs --file examples/vllm_fake_lead.log` resolves the parent's `Failed core proc(s): {}` to the child process's `ImportError: libflash_attn_2_cuda.so`. CI runs the suite that covers this path. This file does not freeze a pass count.

---

## Why this exists

Benchmarking tools answer *how fast is it*. GPU monitors answer *what is the card doing*. Neither answers the question you actually have at 2 a.m. when a deployment will not serve:

**"Is this thing broken, and if so, which layer?"**

Three failure patterns kept costing hours on real deployments, and each one is invisible to a benchmark or a monitor:

| Pattern | What you see | What is really happening |
|---|---|---|
| **Fake lead** | `Engine core initialization failed. Failed core proc(s): {}` | The real exception is in the **child process** stderr (`EngineCore_DP0 pid=…`). Following the parent's message sends you down the wrong path. |
| **Silent CPU fallback** | Throughput is terrible (e.g. 4.32 tok/s) | Per-GPU memory is near zero. The check reports `DEGRADED` and **hints** that tokens may be on the CPU. It does not observe the CPU. |
| **Budget overrun** | The run finishes *after* the deadline, with nothing persisted | tokens ÷ tok/s × requests already exceeds the wall clock. You needed that arithmetic **before** launching. |

`llmtriage` makes those three checks runnable, and forces every verdict to carry evidence and a three-state label.

## Install

**Not on PyPI.** On 2026-11-05, `https://pypi.org/pypi/llmtriage/json` returned **404**. `[tool.llmtriage] distribution` in `pyproject.toml` is `"source"`. There is no index package. `tests/test_readme_honesty.py` fails if these READMEs grow an index install line while that field stays `"source"`.

From a clone (this is what CI installs):

```bash
git clone https://github.com/aidless/llmtriage.git
cd llmtriage
python -m pip install -e ".[dev]"   # this checkout, with pytest
python -m pip install -e .          # this checkout, runtime only
```

From git, without keeping a checkout:

```bash
python -m pip install "git+https://github.com/aidless/llmtriage.git"
```

Runtime dependencies are the standard library. `pytest` is only the `dev` extra. CI does not build a wheel and does not upload a release. `MANIFEST.in` lists `examples/`, `docs/`, and `tests/` so an sdist can carry them; that is packaging layout, not a published release.

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

# 6. Is the service queueing or thrashing its KV cache? (reads /metrics, no writes)
llmtriage metrics --url http://127.0.0.1:8000
llmtriage metrics --file metrics.txt        # offline: paste a saved capture

# 7. Throughput means nothing without SLOs: what fraction of requests actually met TTFT+TPOT?
llmtriage slo --csv samples.csv --ttft-slo 1.0 --tpot-slo 0.1

# 8. Given the evidence collected above, what is the most likely cause and what do I check next?
llmtriage explain --log vllm.log --metrics metrics.txt

# Everything at once
llmtriage triage --base-url http://127.0.0.1:8000 --log vllm.log \
                 --tokens 12000 --tok-per-s 4.32 --deadline 4500 --requests 2 \
                 --explain --out triage.md

# Turn that report into a postmortem a reviewer will accept
llmtriage postmortem --from triage.json --title "vLLM 起服失败，整批任务零产出" \
                     --root-cause-state UNRESOLVED \
                     --action "屏蔽残缺包" --verify "重启后 /v1/models 返回 200" \
                     --out postmortem.md
```

> Shell note: the exit codes are real, so `llmtriage logs ... && next-step` **stops** when a
> problem is found (exit 1). Chain with `;` or `|| true` in scripts where that is intended.

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

```console
$ llmtriage metrics --file examples/vllm_metrics_saturated.txt
# 指标快照

**总体结论：`SATURATED`（证据状态：OBSERVED）** —— 1 项异常（metrics）

## metrics → `SATURATED`（OBSERVED）

证据：

```
vllm:num_requests_running{model_name="qwen3_4b"} 8.0 | vllm:num_requests_waiting{model_name="qwen3_4b"} 47.0 | vllm:gpu_cache_usage_perc{model_name="qwen3_4b"} 0.98 | vllm:num_preemptions_total{model_name="qwen3_4b"} 312.0 | ...
```
- **kv_usage**：0.98
- **waiting**：47.0
- **preemptions_total**：312.0
- **prefix_hit_rate**：0.1333
- **hint**：排队 47 且 KV 占用 98%：先降并发/扩副本，再谈压测数字
# exit 1
```

```console
$ llmtriage slo --csv examples/slo_samples.csv --ttft-slo 1.0 --tpot-slo 0.1
## slo → `DEGRADED`（OBSERVED）

n=8 goodput=0.750 目标=0.990 超 TTFT 2 条 / 超 TPOT 0 条（SLO 1.000s / 0.100s）
- **binding**：ttft
- **ttft_p50**：0.51 · **ttft_p95**：2.4 · **tpot_p50**：0.023
- **hint**：超的是 TTFT：瓶颈多在 prefill / 输入长度 / 排队，不在 decode
# exit 1
```

```console
$ llmtriage explain --log examples/vllm_fake_lead.log --metrics examples/vllm_metrics_saturated.txt
## 下一步：待验证假设（按优先级）

### 1. [HIGH] 引擎子进程崩溃，父进程日志里的报错是假线索（依赖包残缺或版本不匹配最常见）

- 依据：logs
- 下一句检查：`读子进程日志尾部：grep -n 'pid=' 启动日志 | tail -50；再单独 import 可疑包 …`

### 2. [HIGH] 请求排队且 KV 缓存打满：吞吐被显存容量与并发上限卡住（排队 47 / 占用 98%）

- 依据：metrics
- 下一句检查：`① 降 --max-num-seqs 观察 waiting 是否回落 ② 扩副本 ③ 开 --enable-prefix-caching …`
# exit 0
```

## Postmortem reports

`llmtriage postmortem` turns findings into the document the JD-shaped work actually ends with:
*"评估报告、优化建议和问题复盘"*. It is opinionated in one direction only — **it will not let an
unknown root cause read as a fix**:

- root cause is a **three-state declaration**: `CONFIRMED` (evidence closes the loop) /
  `SUSPECTED` (clue, not closed) / `UNRESOLVED` (do not know). Selecting `UNRESOLVED` makes the
  report say *"根因未确定：以下处置只用于限制影响，不是根因修复"*.
- with no verification evidence, the 验证 section prints **未验证** and *"在补充验证前，不得把该事件
  标记为已解决"*. The string `已修复` never appears in generated output — whether it is fixed is
  answered by the verification section, not by wording.
- every finding's raw `evidence` lines are copied verbatim into appendix 证据清单.

Two generated examples ship in the repo, both produced by the CLI (not hand-written):

| File | Shape |
|---|---|
| [`examples/postmortem-example.md`](examples/postmortem-example.md) | `CONFIRMED`, 4 timeline entries, verification filled |
| [`examples/postmortem-unresolved-example.md`](examples/postmortem-unresolved-example.md) | `UNRESOLVED`, no verification → flagged, open items enumerated |
| [`examples/vllm_metrics_saturated.txt`](examples/vllm_metrics_saturated.txt) | a short Prometheus text sample using vLLM metric names; it trips `SATURATED`. The file has no capture log saying which process produced it |
| [`examples/slo_samples.csv`](examples/slo_samples.csv) | 8 `ttft_s,tpot_s` samples where 2 misses make goodput 0.75 |

## Evidence boundaries

These labels are about **claims this repository makes**. They use the same three words the tool prints on a finding. A finding's own label is only as good as the input you gave that command.

An older paragraph on this page said the project had not been run on physical AMD hardware, and that a W7900 run was only planned. That contradicted [`docs/evidence-rocm-w7900-2026-11-01.md`](docs/evidence-rocm-w7900-2026-11-01.md). The record below is the one that matches the files in the tree.

### Observed

- **CI** (`.github/workflows/ci.yml`): `ubuntu-latest`, Python 3.10 and 3.12, `python -m pip install -e ".[dev]"`, then `python -m pytest -q`. No GPU runner, no live inference server, no package publish.
- **Replay of one stored ROCm CSV.** [`tests/fixtures/rocm_smi_w7900_2026-11-01.csv`](tests/fixtures/rocm_smi_w7900_2026-11-01.csv) is the header `device,GPU use (%),VRAM Total Memory (B),VRAM Total Used Memory (B)` and one data row `card0,1,51522830336,28004352`. `test_real_w7900_rocm_capture_parses_with_correct_units` feeds that text to `parse_rocm_smi`. `51522830336` bytes is exactly 49136 MB; used memory rounds to 27 MB; the sample classifies `idle`. CI runs that replay. CI does not execute `rocm-smi`.
- **The write-up of that capture** ([`docs/evidence-rocm-w7900-2026-11-01.md`](docs/evidence-rocm-w7900-2026-11-01.md)) pastes `rocm-smi` 4.0.0 and ROCM-SMI-LIB 7.8.0, notes `nvidia-smi` was absent, and pastes a CLI transcript of `DEGRADED` / exit 1 on an idle card. The automated test re-parses the CSV. It does not re-execute that transcript.
- **Other unit tests** use synthetic strings, the files under `examples/`, an in-process HTTP stub for `probe`, and fake `nvidia-smi` / `rocm-smi` binaries on `PATH`. They observe how the code treats those inputs.

### Inferred

- The write-up calls the machine an AMD Radeon PRO W7900 because that is the instance type that was rented (AMD Radeon Cloud, 2026-11-01). The CSV has no product-name column. The write-up says `rocm-smi --showproductname` was not captured, and the pasted `rocminfo` marketing name is the CPU (EPYC 9334), not the GPU. ~48 GiB of VRAM (49136 MB = 47.98 GiB) is consistent with a 48 GB card. It does not, by itself, prove the marketing name. No gfx target is stored in the fixture or the write-up.
- "Tokens are on the CPU" is a hint attached when every sampled GPU is under 15% memory and under 20% utilization. Those cut-offs are constants in `llmtriage/gpu.py`. They are not a measurement that the CPU ran the matmul.
- `explain` prints ranked hypotheses tied to findings, at `HIGH` or `MEDIUM` only. A hypothesis is not a closed root cause.

### Unknown

- NVIDIA hardware. Parsing is synthetic `nvidia-smi` CSV plus a stub binary. No `nvidia-smi` capture is stored.
- The AMD `in_use` path (a served model under load). The stored capture is an idle card. The roadmap notes scheduling attempts that did not produce that capture.
- Multi-GPU, MIG, `amd-smi`, and any `rocm-smi` build other than the one pasted in the write-up. Reordering a **synthetic** CSV shows the parser uses column names. That is not a run of another real `rocm-smi`.
- A live vLLM or SGLang process, Kubernetes, Prometheus, authentication, and more than one host. `probe` tests speak to a local stub.
- Installing a wheel of this project from a package index. Nothing has been published.

### What CI covers

| In CI | Not in CI |
|---|---|
| Editable install and `pytest` on Ubuntu, Python 3.10 and 3.12 | A GPU host, or real `nvidia-smi` / `rocm-smi` |
| Pure functions on checked-in fixtures and synthetic text | A running vLLM or SGLang process |
| Exit codes 0/1/2/3 on those fixtures | Wheel build, upload, or an index install |
| Postmortem wording (`已修复` absent; `未验证` when `--verify` is empty) | That an incident in production is fixed |

### What this tool does not prove

A green run does not prove the deployment is healthy, fast, or safe to launch.

- `NO_ERROR_FOUND` means none of the log patterns matched.
- `READY` means `/v1/models` returned 200 to this process.
- `GO` means the arithmetic on the tok/s you supplied fits the deadline, including a 20% margin constant. `probe --measure` is one request. It is not a server ceiling.
- `OK` on metrics means this scrape did not cross the heuristic thresholds in `llmtriage/metrics.py` (KV usage 0.90, waiting requests 8).
- `explain` does not confirm a root cause.
- The postmortem generator will not print `已修复`. It also will not notice a real fix you forgot to pass in `--verify`.

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
- `INFERRED` — derived from measurements, not directly observed. The report aggregator will keep this label if a finding carries it. **No command sets it today.** Current checks emit `OBSERVED` or `UNKNOWN` only. The CPU-fallback sentence is a hint field on an `OBSERVED` GPU finding, not an `INFERRED` finding.
- `UNKNOWN` — not enough data. **A clean log is `NO_ERROR_FOUND`, not "healthy"**, and a missing `nvidia-smi` is `UNKNOWN`, not "0 GPUs".

This is the point of the tool: the expensive failures are the ones that *look* fine.

## Scope and non-goals

It is deliberately **not**:

- a benchmark suite → use [inference-perf](https://github.com/kubernetes-sigs/inference-perf) (TTFT/TPOT/ITL/goodput, saturation sweeps) or [llmperf](https://github.com/ray-project/llmperf)
- a GPU monitor / exporter → use [nvitop](https://github.com/XuehaiPan/nvitop), [nvtop](https://github.com/Syllo/nvtop), [DCGM exporter](https://github.com/NVIDIA/dcgm-exporter)
- a capacity simulator → it does one piece of arithmetic, transparently, on numbers you supply

Known limits, stated rather than hidden:

- `gpu` tries `nvidia-smi` first, then `rocm-smi`. ROCm columns are mapped **by name, not by position**. An unrecognised schema returns `UNKNOWN`. `amd-smi` is not called. If both binaries are on `PATH`, NVIDIA wins and the AMD device is not read.
- The hardware record is one idle ROCm CSV plus its write-up. See [Evidence boundaries](#evidence-boundaries). Replaying that CSV is not a certification that ROCm serving works, and it does not cover the `in_use` path.
- `probe --measure` is **single-request (batch=1)** throughput. It is an input to a budget calculation, not a server throughput ceiling.
- `logs` matches a small, explicit pattern set (vLLM / SGLang engine-core style failures). Unknown stacks come back `UNKNOWN` rather than a guess.

## Failure modes

Ways a verdict can be wrong even when the evidence line is copied faithfully:

1. **Narrow log patterns.** `logs` looks for a few engine-core strings (`Failed core proc(s)`, initialization failed) and a child line shaped like `(Name pid=N) SomeError: ...`. Other stacks, a truncated file, or a parent error with no traceback become `UNKNOWN` or `NO_ERROR_FOUND`. `NO_ERROR_FOUND` is not "healthy".
2. **Idle GPU is a weak signal.** Every card under 15% memory and 20% utilization becomes `DEGRADED` plus a CPU-fallback hint. A small model, a gap between samples, or a card that is busy in a counter this tool does not read can be labeled `idle`. Missing binaries, a non-zero exit, or an unrecognised `rocm-smi` header are `UNKNOWN`, not "zero GPUs".
3. **The ROCm fixture is one idle sample.** It does not exercise `in_use`, a second GPU, MIG, or another `rocm-smi` version. The product name is not in the file.
4. **Budget trusts its inputs.** Throughput is not measured unless you ran `probe --measure`, and that measurement is one request. Missing or non-positive tok/s is exit 3 (`UNKNOWN`), not a pass. The 20% margin is a constant in `llmtriage/budget.py`.
5. **Metric and SLO cut-offs are local heuristics.** KV usage ≥ 0.90 and waiting requests ≥ 8 are not a published standard. Goodput describes the CSV you passed in. A scrape with no KV series is `UNKNOWN`, not `OK`.
6. **A hypothesis is a next command, not a fix.** `explain` will not emit a hypothesis with no finding. A matching finding still only supports the check it prints. Exit code 1 means a check fired. It does not name a production root cause, and it is not a page.

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
python -m pytest -q      # NVIDIA + ROCm parsing (synthetic CSV and the stored idle capture),
                         # budget math, log triage (with a negative control), probe against
                         # an in-process HTTP stub, report schema, postmortem wording, exit codes
```

The pass count is the number `pytest` prints. Do not copy it back into this file. CI is [`.github/workflows/ci.yml`](.github/workflows/ci.yml): Ubuntu, Python 3.10 and 3.12, editable install, then `pytest`. That job does not see a GPU.

Tests are written before the implementation (RED → GREEN). Counter-proofs, not only happy paths:

- the log classifier carries a **negative control**: a genuine parent-process traceback must *not*
  be reported as a fake lead;
- the ROCm parser is fed the **same data in a reordered column layout** and must produce identical
  results — that is what makes "map by column name" a claim instead of a comment;
- the postmortem generator is asserted to **contain no `已修复`** when the root cause is unresolved,
  and to print `未验证` when no verification evidence is supplied.

## License

MIT
