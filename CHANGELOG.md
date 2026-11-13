# Changelog

## 0.2.0 — 2026-11-01

Three checks that turn raw signals into conclusions, plus the small step from a finding to
an actionable next command. All three are offline-testable, so CI covers them without a GPU.

- **`metrics`** — parses Prometheus text from `/metrics` (or a saved file) into a verdict:
  `SATURATED` (queueing *and* KV cache nearly full), `KV_PRESSURE`, `QUEUE_BACKLOG`, `OK`,
  or `UNKNOWN` when the KV metric is absent. Tolerates the vLLM metric rename
  (`gpu_cache_usage_perc` → `kv_cache_usage_perc`) and sums across engines; summing across
  labels is the difference between "47 waiting" and "7 waiting".
  Thresholds (0.90 / 8) are **heuristics, not published cut-offs**.
- **`slo`** — reads `ttft_s,tpot_s` samples and reports **goodput** (fraction of requests
  meeting TTFT *and* TPOT, after DistServe arXiv 2401.09670), p50/p95/p99, and which
  constraint binds. `tok/s` is not a conclusion: it is meaningless without the SLO it was
  measured under.
- **`explain`** — turns findings into ranked hypotheses, each carrying the finding kinds it
  came from and one executable `next_check`. A hypothesis without evidence cannot be
  produced (tested), and there are only two confidence levels — no fake percentages.
- `triage` now pulls `/metrics` when given `--base-url` (`--no-metrics` opts out) and appends
  the hypothesis block with `--explain`.
- Fixed a test that was **red locally and green in CI**: the ROCm fallback test hard-coded
  `:$PATH` including `/usr/bin`, so a machine with a real `nvidia-smi` took the NVIDIA branch.
  It now uses an isolated bin directory, and a second test pins the opposite direction
  (both backends present → NVIDIA wins).
- `docs/roadmap-2026-11-01-expand-then-simplify.md` records the paper→feature map, what we
  deliberately did **not** adopt, and the simplification pass planned next.

## Unreleased — real-hardware ROCm evidence

- The ROCm backend is no longer "written from imagination": one run on an AMD Radeon PRO
  W7900 instance (rocm-smi 4.0.0 / ROCM-SMI-LIB 7.8.0) parsed the real CSV into
  49136 MB VRAM / 27 MB used and returned `DEGRADED` with exit 1, as the contract says.
  The raw capture is now a test fixture, so a unit-conversion regression turns red.
- Measured detail worth keeping: `GPU use (%)` moved between 1 and 0 across two samples
  two seconds apart on an idle card. The fixture test therefore pins magnitude, not a
  specific number — pinning jitter makes a test a coin flip.
- `docs/evidence-rocm-w7900-2026-11-01.md` records what this does **not** prove
  (in-use path on AMD, multi-card, MIG, other rocm-smi versions).

## 0.1.1 — 2026-11-01

Packaging only, no behaviour change — but one real defect fixed.

- **Version drift removed**: `__version__` used to be a hard-coded string while
  `pyproject.toml` carried its own `version`; after a bump the CLI would report the old
  number, so users saw a version that was not the one they installed. Both now come from
  one source (`pyproject.toml` when running from a source tree, the installed metadata
  from a wheel), and a test fails if they ever diverge again.
- `MANIFEST.in` puts `examples/`, `docs/` and `tests/` into the sdist, so the source
  distribution can prove itself: `pip install -e ".[dev]" && pytest` passes from the
  extracted sdist (30 tests).
- `license = "MIT"` (SPDX) + `license-files`; the `License :: OSI Approved` classifier is
  gone because setuptools rejects the two together.
- Chinese README gained the install section it never had.

## 0.1.0 — 2026-10-31

First public cut. Scope: single host, OpenAI-compatible endpoint, zero runtime dependencies.

- `gpu`: per-GPU memory/util snapshot; classifies each card `in_use` / `idle`;
  an all-idle result is reported as `DEGRADED` with the "tokens may be on CPU" hint.
- `logs`: separates **fake leads** (parent `Failed core proc(s): {}`) from the
  **child-process exception** that actually explains the failure; a genuine parent
  traceback is classified separately and never reported as a fake lead.
- `budget`: tokens ÷ tok/s × requests with margin → `GO` / `NO_GO`, or `UNKNOWN`
  when throughput was never measured.
- `probe`: `/v1/models` readiness, plus optional single-request throughput from
  `usage.completion_tokens` and wall clock.
- `triage`: runs every available check and emits one Markdown/JSON report.
- Contracts: exit codes `0/1/2/3`, and three-state evidence `OBSERVED / INFERRED / UNKNOWN`.

- `gpu` backend fallback: `nvidia-smi` → `rocm-smi`. ROCm columns are mapped **by name**
  (order varies across `rocm-smi` versions); unrecognised schema returns `UNKNOWN`.

- `postmortem`: findings → incident postmortem. Root cause is a three-state declaration
  (`CONFIRMED` / `SUSPECTED` / `UNRESOLVED`); an unresolved root cause is stated as
  "根因未确定" and the generated text never contains `已修复`; missing verification is
  printed as `未验证` and blocks marking the incident resolved. Evidence lines are copied
  verbatim into an appendix. Ships two CLI-generated examples.

Tests: 29, written before implementation (RED → GREEN), including a live OpenAI stub,
a negative control for the log classifier, a column-reordering counter-proof for the
ROCm parser, and honesty assertions for the postmortem generator.

Not verified: no run on physical AMD hardware yet (stubbed `rocm-smi` only).
