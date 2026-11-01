# Changelog

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
