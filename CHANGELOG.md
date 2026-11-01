# Changelog

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
