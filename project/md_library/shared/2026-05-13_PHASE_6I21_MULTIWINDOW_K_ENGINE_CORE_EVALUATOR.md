# Phase 6I-21: True multi-window K engine — core evaluator (first real slice)

Sprint date: **2026-05-13**.
Branch: `phase-6i-21-multiwindow-k-engine-core-evaluator`.
Module: `project/multiwindow_k_engine_core.py`.
Tests: `project/test_scripts/test_multiwindow_k_engine_core.py`.
Doc: this file.

This phase ships the **first real read-only core evaluator**
for the future TrafficFlow-style multi-window K engine. It is
not yet wired into the production pipeline; it does not yet
write any on-disk artifact; it does not yet make the
Phase 6I-20 gap audit pass against production. It proves the
math and the data shape so later phases can write the
canonical 60-cell `per_window_k_metrics` field on the
Confluence artifact.

---

## 0. Scope

A read-only pure-Python module that exposes:

- `evaluate_cell(...)` — evaluates one `(K, window)` cell
  from in-memory inputs and returns a `PerWindowKCell`
  dataclass.
- `evaluate_grid(...)` — evaluates many cells across many
  windows in one call; emits exactly 60 cells when supplied
  the canonical 12 K values × 5 canonical windows with
  complete inputs.
- `cells_to_per_window_k_metrics_payload(cells)` — converts
  cells into the Phase 6I-20-defined `per_window_k_metrics`
  contract shape (list of dicts each carrying the canonical
  five required fields plus extras).

The audit's load-bearing assertion `has_true_multiwindow_k_engine_outputs=True`
needs both `per_window_k_metrics` AND
`build_wide_window_alignment` on the Confluence artifact.
**This phase ships only the per-window-K side**, and only
the core math — not the on-disk persistence path. A later
phase will (a) feed the core from real upstream data and
(b) write `per_window_k_metrics` + `build_wide_window_alignment`
to the artifact.

---

## 1. What this module IS

A pure-Python read-only evaluator. For one `(K, window)` cell,
the caller supplies in-memory:

- `target_ticker` — symbol the build targets;
- `K` — minimum agreeing active-member threshold;
- `window` — the bar frequency of the inputs (a label, used
  on the returned cell only);
- `dates` — sequence aligned 1-to-1 with `target_close`;
- `target_close` — close prices already at the chosen
  window's bar frequency, in chronological order;
- `member_signal_columns` — mapping of member ticker to a
  sequence of pre-protocol `Buy / Short / None / "missing"`
  strings, one per bar;
- `member_protocols` (optional) — mapping of member ticker
  to `"D"` (Direct, default) or `"I"` (Inverse).

The core then:

1. Applies the Direct/Inverse protocol per member per bar.
2. Combines per bar via `research_artifacts.combine_member_signals`
   (canonical K-thresholded strict-unanimity rule).
3. Computes per-bar capture (Buy = `+pct_change(target_close)*100`,
   Short = `-pct_change(target_close)*100`, None = 0).
4. Aggregates over trigger bars: total_capture_pct,
   avg_daily_capture_pct, wins, losses, sharpe_ratio
   (sample std, ddof=1; returns 0 when fewer than 2 trigger
   bars or std is zero).
5. Records the final-bar per-member signal counts
   (`latest_buy_count` / `latest_short_count` /
   `latest_none_count` / `latest_missing_count` and the
   combined signal at the latest bar).
6. Returns a `PerWindowKCell` dataclass.

The grid helper applies `evaluate_cell` across the Cartesian
product of the supplied K values × windows; missing windows
or windows with incomplete input blocks are silently skipped
(never fabricated).

---

## 2. What this module IS NOT

- **NOT a projection / bridge.** The Phase 6D-2
  `trafficflow_multitimeframe_bridge` is a different module
  that projects daily signals onto longer windows via
  `pandas.resample().last() + ffill`. This module never
  imports the bridge, never calls `.resample()` or
  `.ffill()`, and does not import `pandas` or `numpy` at
  top level. Every window's inputs must arrive *already at
  that window's bar frequency*; the caller is responsible
  for getting them there.
- **NOT a Confluence presentation adapter.** The Phase 6I-19
  `confluence_decision_brief` is a presentation layer over
  the existing Phase 6I-3 ranking emitter. This module
  does not import either.
- **NOT wired into the production pipeline.** No writer,
  no refresher, no pipeline runner, no batch engine
  execution. The core takes lists, returns dataclasses;
  callers decide where to plug it in.
- **Does NOT yet write `per_window_k_metrics` to disk.**
  `cells_to_per_window_k_metrics_payload` returns the
  contract shape in memory; persisting it to the Confluence
  artifact is a later phase's job.
- **Does NOT yet make production `has_true_multiwindow_k_engine_outputs=True`.**
  The Phase 6I-20 gap audit will continue to report `False`
  against the existing on-disk Confluence artifact until a
  later phase actually writes the 60-cell field to it.
- **Does NOT close the carry-forward evidence gaps.**
  `real_confluence_pipeline_runner_write` /
  `real_post_pipeline_validation_on_writer_path` /
  writer-surface provider telemetry remain open. Operational
  state remains **STATE C / WAIT** (cache 2026-05-12 ==
  cutoff 2026-05-12).
- **No production-authorization activity.** No `--write`
  writer invocation, no `PRJCT9_AUTOMATION_WRITE_AUTH`,
  no source refresh, no `yfinance` fetch, no subprocess.

---

## 3. Semantics (reused from the existing engine family)

### 3.1 Direct / Inverse protocol

- **Direct** (default): raw `Buy / Short / None` passes
  through. Anything not recognized collapses to `None`.
  Empty / `"missing"` raw values map to `SIGNAL_MISSING`
  and are returned to the caller for the combine to
  filter out before agreement counting.
- **Inverse** (`"I"`): `Buy <-> Short` swap; anything else
  maps to `None`. Empty / `"missing"` raw values still map
  to `SIGNAL_MISSING`.

The rule is identical to the private
`research_artifacts._apply_protocol` helper; the core
re-derives it locally so it does not depend on a private
symbol.

### 3.2 Combine

Delegated verbatim to the public function
`research_artifacts.combine_member_signals(member_signals, K)`:

- Members marked `"missing"` are filtered out *before*
  combine — they don't count toward the agreement check.
- Active members must agree strictly on `Buy` or `Short`;
  any mixed Buy + Short collapses to `None`.
- Below-threshold agreement (`active_buy_count < K` for a
  Buy candidate, `active_short_count < K` for a Short
  candidate) collapses to `None`.
- No active members → `None`.

### 3.3 Capture math

- `target_return_pct[i] = (close[i] - close[i-1]) / close[i-1] * 100`
  (the first bar's return is `0.0` by convention).
- Per bar: `Buy → +target_return_pct`,
  `Short → -target_return_pct`, `None → 0` and not a trigger
  day.
- `trigger_days = count(trigger bars)`.
- `total_capture_pct = sum(trigger captures)`.
- `avg_daily_capture_pct = total / trigger_days`.
- `wins / losses = count(positive / negative trigger captures)`.
- `sharpe_ratio = avg / sample_std(trigger captures, ddof=1)`
  when `trigger_days > 1` AND `std > 0`; otherwise `0.0`.

### 3.4 No persist-skip in the core

The Phase 6D-1 daily-K builder already trims one bar by
default; the Phase 6F-4 MTF contract pins MTF artifacts to
`persist_skip_bars=0`. The engine core operates on the bars
it is given — any T-1 trimming is the caller's
responsibility. This keeps the core deterministic per the
inputs supplied.

---

## 4. Public API

```python
from multiwindow_k_engine_core import (
    evaluate_cell,
    evaluate_grid,
    cells_to_per_window_k_metrics_payload,
    PerWindowKCell,
    CANONICAL_WINDOWS,   # ("1d", "1wk", "1mo", "3mo", "1y")
    CANONICAL_K_VALUES,  # tuple(range(1, 13))
    PROTOCOL_DIRECT,     # "D"
    PROTOCOL_INVERSE,    # "I"
    SIGNAL_BUY,          # "Buy"
    SIGNAL_SHORT,        # "Short"
    SIGNAL_NONE,         # "None"
    SIGNAL_MISSING,      # "missing"
)

cell = evaluate_cell(
    target_ticker="SPY",
    K=6,
    window="1wk",
    dates=[...],
    target_close=[...],
    member_signal_columns={
        "AAPL": ["Buy", "Buy", "None", ...],
        "QQQ":  ["Buy", "Buy", "Buy",  ...],
    },
    member_protocols={"AAPL": "D", "QQQ": "D"},
)
# cell: PerWindowKCell with K=6, window="1wk",
#       total_capture_pct, avg_daily_capture_pct,
#       sharpe_ratio, trigger_days, wins, losses,
#       latest_combined_signal, latest_buy_count,
#       latest_short_count, latest_none_count,
#       latest_missing_count, member_count.

cells = evaluate_grid(
    target_ticker="SPY",
    K_values=CANONICAL_K_VALUES,
    windows=CANONICAL_WINDOWS,
    per_window_inputs={
        "1d":  {"dates": ..., "target_close": ..., "member_signal_columns": ...},
        "1wk": {"dates": ..., "target_close": ..., "member_signal_columns": ...},
        "1mo": {"dates": ..., "target_close": ..., "member_signal_columns": ...},
        "3mo": {"dates": ..., "target_close": ..., "member_signal_columns": ...},
        "1y":  {"dates": ..., "target_close": ..., "member_signal_columns": ...},
    },
    member_protocols=None,
)
# cells: tuple of 60 PerWindowKCell when fully populated.

payload = cells_to_per_window_k_metrics_payload(cells)
# payload: list[dict] in the Phase 6I-20 contract shape.
```

`PerWindowKCell` exposes the canonical Phase 6I-20-required
five fields (`K` / `window` / `total_capture_pct` /
`sharpe_ratio` / `trigger_days`) plus extras
(`avg_daily_capture_pct` / `wins` / `losses` /
`latest_combined_signal` / `latest_buy_count` /
`latest_short_count` / `latest_none_count` /
`latest_missing_count` / `member_count`). The Phase 6I-20
gap audit's `_per_window_k_metrics_are_valid` accepts the
output as a valid `per_window_k_metrics` payload when
canonical 60-cell coverage is present (tests pin this).

---

## 5. Strictly read-only

- No `daily_board_automation_writer` import.
- No `signal_engine_cache_refresher` import.
- No `confluence_pipeline_runner` import.
- No `yfinance` / `dash` import.
- No `trafficflow` / `spymaster` / `impactsearch` /
  `onepass` / `confluence` / `cross_ticker_confluence` /
  `daily_signal_board` import.
- No `subprocess`.
- **No `trafficflow_multitimeframe_bridge` import** (the
  projection / bridge module). Pinned by
  `test_core_module_has_no_forbidden_imports`.
- No `pandas` / `numpy` import at top level. Pinned by
  `test_core_is_not_projection_no_pandas_or_resample`.
- No call to `.resample()` or `.ffill()` anywhere in code.
  Pinned by the same test via AST walk over `ast.Call`
  nodes (the module docstring is allowed to mention the
  words because the test scans call sites, not strings).

---

## 6. Tests (29 pinned contracts)

`project/test_scripts/test_multiwindow_k_engine_core.py`:

1. Forbidden-imports static guard.
2. Not a projection: no `pandas` / `numpy` import; no
   `.resample()` / `.ffill()` call.
3-6. Direct / Inverse protocol: Direct pass-through; Inverse
   `Buy → Short`; Inverse `Short → Buy`; unknown protocol
   string defaults to Direct.
7-8. K threshold: below-threshold collapses to None;
   meets-threshold emits the signal.
9-13. Combine rule: all-Buy → Buy; all-Short → Short;
   mixed Buy + Short → None; all-None → None; `"missing"`
   members ignored in agreement count.
14-17. Capture math: Buy = +pct_change; Short = -pct_change;
   None = 0 (no trigger day); single-trigger Sharpe = 0.
18. Sharpe positive when trigger captures have positive
    mean and non-zero std (sample std, ddof=1).
19-20. Grid evaluator: 60 canonical cells emitted from full
    inputs; payload helper carries the Phase 6I-20-required
    five fields on every cell.
21-24. Grid evaluator: missing window silently skipped;
    incomplete per-window input block silently skipped;
    empty `K_values` yields no cells; empty `windows`
    yields no cells.
25-26. Length-alignment safety: mismatched `target_close`
    raises `ValueError`; mismatched member column raises
    `ValueError`.
27. Latest signal counts mirror the final bar (including
    Inverse-protocol flip on a per-member basis).
28. `to_dict()` round-trip carries all 14 fields.
29. Canonical constants pinned to their expected values.

All tests use in-memory inputs only — no `tmp_path` is
needed because nothing is read from / written to disk. No
yfinance / live engine / production-write touch.

---

## 7. What this audit does NOT close yet

- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still
  open.
- Writer-surface provider telemetry (writer stdout /
  JSONL / per-ticker status JSON) — still pending.
- Production `has_true_multiwindow_k_engine_outputs=True` —
  the Phase 6I-20 audit will still report `False` against
  every production ticker until a later phase writes the
  `per_window_k_metrics` field to the on-disk Confluence
  artifact.
- Operational state remains **STATE C / WAIT** (cache
  `2026-05-12` == cutoff `2026-05-12`); the predicate-first
  discipline is preserved.

---

## 8. Reference paths

- Module: `project/multiwindow_k_engine_core.py`.
- Tests: `project/test_scripts/test_multiwindow_k_engine_core.py`.
- Phase 6I-20 gap audit (consumes future
  `per_window_k_metrics`):
  `project/multiwindow_k_engine_gap_audit.py`
  + `project/md_library/shared/2026-05-13_PHASE_6I20_MULTIWINDOW_K_ENGINE_GAP_CONTRACT.md`.
- Phase 6I-19 decision-brief adapter (presentation layer
  over existing daily artifacts; explicitly NOT the engine):
  `project/confluence_decision_brief.py`
  + `project/md_library/shared/2026-05-13_PHASE_6I19_MTF_CONFLUENCE_DECISION_BRIEF.md`.
- Phase 6D-2 bridge (projection / resample, NOT the engine):
  `project/trafficflow_multitimeframe_bridge.py`.
- Phase 6D-3 Confluence builder (aggregation over MTF
  projections, NOT the engine):
  `project/confluence_mtf_artifact_builder.py`.
- Combine + protocol primitives reused:
  `project/research_artifacts.py` (functions
  `combine_member_signals` (public) and
  `_apply_protocol` (private; rule re-derived locally in
  the core)).
- Phase 6I-18 next-probe handoff (operational state /
  source-wait):
  `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md`.
- CLAUDE.md sec 6 — current sprint state.
