# Phase 6I-20: True multi-window K engine gap audit + contract harness

Sprint date: **2026-05-13**.
Branch: `phase-6i-20-multiwindow-k-engine-gap-contract`.
Module: `project/multiwindow_k_engine_gap_audit.py`.
Tests: `project/test_scripts/test_multiwindow_k_engine_gap_audit.py`.
Doc: this file.

This phase does **not** build the true multi-window K engine.
It builds a read-only audit module that prevents the engine
from being claimed as built before it actually is.

---

## 0. Scope

A read-only audit that, for any ticker (or the discovered
StackBuilder universe), emits a structured JSON gap report
distinguishing three layers:

1. **Daily K artifacts** — Phase 6D-1 outputs at
   `output/research_artifacts/trafficflow/<TICKER>/<run>__K<n>.research_day.json`.
   Daily-window K signals only.
2. **Existing MTF bridge / Confluence projection
   artifacts** — Phase 6D-2 bridge (`__MTF.research_day.json`)
   plus Phase 6D-3 Confluence builder
   (`output/research_artifacts/confluence/<TICKER>/...`).
   These project daily signals onto resampled windows via
   `pandas.resample().last() + ffill`; they are projection
   artifacts, not per-window K evaluations.
3. **True future per-window K evaluation artifacts** —
   **NOT YET BUILT**. Recognized only when the Confluence
   artifact already carries the future-shape fields
   `per_window_k_metrics` AND `build_wide_window_alignment`
   (this audit names the exact shape).

**The audit explicitly does NOT** invoke the writer, the
refresher, the pipeline runner, `yfinance`, or any batch
engine. It reads existing artifacts via the Phase 6I-1
contract validator + the on-disk Confluence artifact and
emits one structured JSON report.

---

## 1. The corrected old manual workflow

The legacy manual workflow operators ran daily to ask
"what's our best buy / short candidate today?" was
(operator-confirmed at the Phase 6I-19 PR #236 amendment
review):

1. delete cached PKLs;
2. open TrafficFlow and let it surface a missing-PKL list;
3. run the Spymaster batch process to refill those PKLs;
4. return to TrafficFlow;
5. enter a K value (e.g. K=6);
6. export / inspect that single daily K table;
7. paste the table into an AI prompt and ask for a
   pattern read / ranking / confidence call before the
   next market close.

### 1.1 Why this was daily / next-24-hour only

The K table TrafficFlow exported at step 6 was a single-
window (daily) view. The operator's pattern read at step 7
had no multi-window context. Every signal in that flow
collapsed to "what does the daily K signal say about the
next 24 hours?"

The workflow was therefore essentially **daily / next-
24-hour only**, even though the operator's mental model
implicitly weighed weekly / monthly / quarterly / yearly
behaviour.

### 1.2 The implicit operator intent

What the operator wanted at step 7 was: for the current
StackBuilder K build, evaluate K behaviour across the five
canonical windows (1d / 1wk / 1mo / 3mo / 1y), and call
out when every member of the build was firing across
every available window. That intent has never been
encoded in code; the AI-prompt step was a workaround for
the missing engine.

---

## 2. What exists now (layer-by-layer)

### 2.1 Phase 6D-1 — daily K TrafficFlow artifacts

`output/research_artifacts/trafficflow/<TICKER>/<run>__K<n>.research_day.json`.
One file per K value (K = 1..12) per ticker per
StackBuilder run. Each file carries daily-window pressure
signals + capture math. Pinned by
`confluence_ranking_contract_validator._check_daily_k_contract`
(K coverage check is strict 1..12).

### 2.2 Phase 6D-2 — multi-timeframe bridge

`output/research_artifacts/trafficflow/<TICKER>/<run>__K<n>__MTF.research_day.json`.
Projects the daily pressure signal onto `1wk / 1mo / 3mo /
1y` via `pandas.resample(<freq>).last() + ffill`. The
projection sees the **previous closed** period's signal
on each daily row; the current period contributes only on
its closing day. **No future-period leak.** Pinned by
`_check_mtf_contract`.

**Important: this is a projection, not an evaluation.**
The bridge takes the existing daily K signal and asks
"what would the weekly / monthly / quarterly / yearly
signal look like if I resample the daily one?" It does
**not** re-evaluate K behaviour against a weekly /
monthly / quarterly / yearly bar series.

### 2.3 Phase 6D-3 — Confluence artifact

`output/research_artifacts/confluence/<TICKER>/<TICKER>[__<run_id>].research_day.json`.
Aggregates the Phase 6D-2 `__MTF` artifacts into a
single `engine="confluence"` artifact with per-day
combined pressure signal + per-(K, window) vote map +
per-day capture path. Pinned by
`_check_confluence_contract`. Carries `timeframes` and
`K_values` tuples on each row.

### 2.4 Phase 6I-1 — contract validator

`confluence_ranking_contract_validator.validate_confluence_ranking_contract`.
Returns seven per-ticker contract booleans (cache /
stackbuilder / daily_k / mtf / confluence / readiness /
board_row) plus `daily_k_coverage` / `mtf_k_coverage` /
`confluence_last_date`. The audit consumes this verdict
verbatim through an injectable callable.

### 2.5 Phase 6I-3 — ranking emitter

`confluence_ranking_emitter.emit_confluence_ranking`.
Per-ticker ranking row carrying Group A signal-breadth
+ Group B performance-quality + the row's `timeframes`
/ `K_values` tuples as already populated upstream. The
audit does NOT consume this directly (the validator
covers the on-disk presence checks) but the Phase 6I-19
brief does.

### 2.6 Phase 6I-19 — decision-brief adapter (NOT an engine)

`confluence_decision_brief.evaluate_confluence_decision_brief`.
Per the Phase 6I-19 amendment, this is a **presentation
adapter** — it surfaces the existing `timeframes` /
`K_values` tuples if and only if upstream artifacts
already contain them; it never creates the missing MTF
data. The brief's `_DEFAULT_REMAINING_LIMITATIONS` names
the missing future engine verbatim.

---

## 3. What does NOT exist yet

**The true TrafficFlow-style multi-window K engine.**

A true engine would, for each StackBuilder K build:

- Evaluate K capture / Sharpe / trigger-day metrics per
  `(K, window)` cell across `1d / 1wk / 1mo / 3mo / 1y`
  **independently** (not by resampling the daily signal).
- Aggregate build-wide so an operator can see at a glance
  whether every member of the build is firing across
  every available window.
- Write the resulting fields to the Confluence artifact
  so downstream presenters (decision brief, Daily Signal
  Board, Spymaster master-audit panel) can render the
  build-wide alignment view directly.

The Phase 6D-2 bridge looks similar at a glance but is
not the same thing. The bridge **resamples** the daily K
signal onto weekly / monthly / quarterly / yearly bars
via `ffill`; the true engine would **re-evaluate** K
behaviour against those bar series in their own right.

### 3.1 Why Phase 6I-19 was only a decision-brief adapter

The Phase 6I-19 PR (#236) opened with module-docstring
and doc-section wording that read as if the legacy
workflow was fully replaced and the multi-window
generation problem was solved (`"That chain is now
obsolete"` / `"How this replaces the old manual
workflow"` / `"Phase 6I-1 / 6I-3 / 6I-5 fixed all
three"`). The operator product correction amendment
(`d881aa1`) dropped those overclaims, restored the
operator-confirmed 7-step legacy workflow, and added a
dedicated `_DEFAULT_REMAINING_LIMITATIONS` entry naming
the missing engine. The Phase 6I-19 module is a
presentation layer over the existing ranking emitter
output; it does not generate any data the upstream
pipeline did not already produce.

---

## 4. The contract the future engine must satisfy

The audit pins exactly two future-shape fields on the
Confluence artifact. Both must be present and well-typed
for `has_true_multiwindow_k_engine_outputs` to return
`True`.

### 4.1 `per_window_k_metrics` (top-level on the Confluence artifact)

A non-empty list of mappings. Each entry covers one
`(K, window)` cell:

```jsonc
{
  "K": 6,
  "window": "1wk",
  "total_capture_pct": 18.42,
  "sharpe_ratio": 0.073,
  "trigger_days": 240
}
```

Required keys (see
`_REQUIRED_PER_WINDOW_K_METRIC_FIELDS` in the audit):

- `K` (int)
- `window` (non-empty str — one of `1d / 1wk / 1mo / 3mo / 1y`)
- `total_capture_pct` (int | float)
- `sharpe_ratio` (int | float)
- `trigger_days` (int | float)

A daily-only entry list (every entry has `window == "1d"`)
is **rejected** as a true multi-window engine. The whole
point of the field is the multi-window cross-section.

### 4.2 `build_wide_window_alignment` (top-level on the Confluence artifact)

A mapping that carries **one entry per canonical window**
(all five: `1d / 1wk / 1mo / 3mo / 1y`):

```jsonc
{
  "1d":  {"all_members_firing": true,  "firing_member_count": 12, "total_member_count": 12},
  "1wk": {"all_members_firing": true,  "firing_member_count": 12, "total_member_count": 12},
  "1mo": {"all_members_firing": false, "firing_member_count": 10, "total_member_count": 12},
  "3mo": {"all_members_firing": false, "firing_member_count": 8,  "total_member_count": 12},
  "1y":  {"all_members_firing": true,  "firing_member_count": 12, "total_member_count": 12}
}
```

Required keys per entry (see
`_REQUIRED_BUILD_WIDE_ALIGNMENT_FIELDS`):

- `all_members_firing` (bool)
- `firing_member_count` (int)
- `total_member_count` (int)

If even one canonical window is missing from the mapping,
or any entry omits a required key or has the wrong type,
the audit reports
`has_build_wide_all_members_all_windows_signal=False`.

### 4.3 Both fields required

`has_true_multiwindow_k_engine_outputs` is `True` iff
`has_per_window_k_metrics` AND
`has_build_wide_all_members_all_windows_signal`. A
half-built engine (one field but not the other) does
**not** count.

---

## 5. How Confluence eventually shows "all members firing across all windows"

When the future engine writes both fields, the operator-
facing surface becomes:

- The Confluence artifact's `build_wide_window_alignment["1mo"].all_members_firing`
  is the load-bearing bool.
- A future Confluence / decision-brief rendering can
  collapse the per-window flags into a single line per
  build: `"SPY build: 1d ✓ / 1wk ✓ / 1mo ✗ / 3mo ✗ / 1y ✓"`.
- A build whose alignment dict reports
  `all_members_firing=true` for every canonical window is
  the operator's "wow, this whole build is aligned across
  windows" moment.
- The Phase 6I-19 decision brief becomes the natural
  rendering layer once the data is present; until then
  the brief surfaces the absence honestly via
  `mtf_breadth` / `k_coverage_complete` and the
  remaining-limitations entry.

The audit does **not** prescribe a single rendering. It
prescribes the data shape so any rendering layer
(decision brief, Daily Signal Board card, Spymaster
master-audit panel, future Confluence engine UI) can
read it directly.

---

## 6. Public API

```python
from multiwindow_k_engine_gap_audit import (
    audit_multiwindow_k_engine_gap,
    audit_multiwindow_k_engine_gaps,
    MultiWindowKEngineGapState,
    MultiWindowKEngineGapReport,
    CANONICAL_WINDOWS,
    CANONICAL_K_VALUES,
    # stable missing-capability codes:
    MISSING_DAILY_K_ARTIFACTS,
    MISSING_MTF_BRIDGE_ARTIFACTS,
    MISSING_CONFLUENCE_ARTIFACT,
    MISSING_TRUE_MULTIWINDOW_K_ENGINE,
    MISSING_PER_WINDOW_K_METRICS,
    MISSING_BUILD_WIDE_WINDOW_ALIGNMENT_FIELDS,
    INCOMPLETE_K_COVERAGE,
    INCOMPLETE_TIMEFRAME_COVERAGE,
)

state = audit_multiwindow_k_engine_gap(
    "SPY",
    cache_dir=None, artifact_root=None,
    stackbuilder_root=None, signal_library_dir=None,
    current_as_of_date=None,
    validator_callable=None,                    # default: Phase 6I-1
    confluence_artifact_inspector_callable=None,  # default: on-disk reader
)

report = audit_multiwindow_k_engine_gaps(
    tickers=None,
    from_stackbuilder_universe=True,
    top_n=25,
)
```

`MultiWindowKEngineGapState` carries (the operator
checklist):

- `stackbuilder_run_count` (int)
- `stackbuilder_k_coverage` (tuple[int, ...])
- `daily_k_artifacts_present` (bool)
- `mtf_bridge_artifacts_present` (bool)
- `confluence_artifact_present` (bool)
- `observed_timeframes` (tuple[str, ...])
- `observed_k_values` (tuple[int, ...])
- `has_true_multiwindow_k_engine_outputs` (bool)
- `has_per_window_k_metrics` (bool)
- `has_build_wide_all_members_all_windows_signal` (bool)
- `missing_capabilities` (tuple[str, ...])
- `recommended_next_build_step` (str)
- plus stackbuilder / validator pass-through fields and
  `contract_issue_codes`.

---

## 7. CLI

```
python multiwindow_k_engine_gap_audit.py --ticker SPY
python multiwindow_k_engine_gap_audit.py --tickers SPY,QQQ,SQQQ
python multiwindow_k_engine_gap_audit.py --from-stackbuilder-universe --top-n 25
```

Three ticker-source flags mutually exclusive. JSON to
stdout. `rc=0` / `rc=2` (invalid args) / `rc=3`
(unexpected). No `SystemExit` leak.

---

## 8. Strictly read-only

- No `daily_board_automation_writer` import.
- No `signal_engine_cache_refresher` import.
- No `confluence_pipeline_runner` import.
- No `yfinance` / `dash` import.
- No `trafficflow` / `spymaster` / `impactsearch` /
  `onepass` / `confluence` / `cross_ticker_confluence` /
  `daily_signal_board` import.
- No `subprocess`.

Pinned by `test_audit_module_has_no_forbidden_imports`.

The Phase 6I-1 contract validator (read-only by contract)
IS allowed. The Phase 6I-5 universe planner helper is
**lazy-imported** only when
`--from-stackbuilder-universe` is set; tests never
exercise that path against a live planner.

---

## 9. Tests (19 pinned contracts)

`project/test_scripts/test_multiwindow_k_engine_gap_audit.py`:

1. Forbidden-imports static guard.
2. Daily-K-only fixture does NOT pass as true engine.
3. MTF bridge + Confluence artifact alone does NOT pass.
4. Daily-only `per_window_k_metrics` (every entry has
   `window=="1d"`) rejected.
5. Full future-shaped fixture (all K=1..12 + all five
   canonical windows + valid `per_window_k_metrics` +
   valid `build_wide_window_alignment`) PASSES.
6. Incomplete K coverage detected.
7. Incomplete timeframe coverage detected.
8. Missing `build_wide_window_alignment` fields detected.
9. Missing canonical window in alignment mapping
   rejected.
10. `recommended_next_build_step` names the future
    engine + the specific artifact field
    `per_window_k_metrics` + "does NOT exist yet".
11. `to_json_dict()` round-trips through `json.dumps`.
12. Aggregate buckets
    (`tickers_with_true_multiwindow_k_engine` /
    `tickers_missing_true_multiwindow_k_engine`)
    partition the inspected set correctly.
13-16. CLI rc=0 / rc=2 / rc=3 / no `SystemExit` leak.
17. CLI happy path emits JSON with the audit's contract
    fields.
18. Every `ALL_MISSING_CAPABILITY_CODES` constant is
    exported as a named module attribute.
19. `CANONICAL_WINDOWS` and `CANONICAL_K_VALUES` are
    pinned to the canonical tuples.

All tests use fakes / `monkeypatch`; no `yfinance` / live
engine / production-write touch.

---

## 10. What this audit does NOT do

- Does **not** build the true multi-window K engine.
- Does **not** invoke any writer / refresher / pipeline
  runner / batch engine / `yfinance` call.
- Does **not** generate `per_window_k_metrics` or
  `build_wide_window_alignment` on any artifact.
- Does **not** authorize a writer run.
- Does **not** close any of the Phase 6I-16 / 6I-17 /
  6I-18 evidence gaps (`real_confluence_pipeline_runner_write`,
  `real_post_pipeline_validation_on_writer_path`,
  writer-surface provider telemetry).
- Does **not** change the Phase 6H-5 two-key writer gate,
  the Phase 6I-9 supervised gate, the Phase 6I-10
  production-root snapshot strategy, the Phase 6I-12
  ProviderFetchTelemetry four-surface contract, or the
  Phase 6I-15 source-availability advisory contract.

---

## 11. Reference paths

- Module: `project/multiwindow_k_engine_gap_audit.py`.
- Tests: `project/test_scripts/test_multiwindow_k_engine_gap_audit.py`.
- Phase 6I-19 brief: `project/confluence_decision_brief.py`
  + `project/md_library/shared/2026-05-13_PHASE_6I19_MTF_CONFLUENCE_DECISION_BRIEF.md`.
- Phase 6I-1 validator:
  `project/confluence_ranking_contract_validator.py`.
- Phase 6D-2 bridge:
  `project/trafficflow_multitimeframe_bridge.py`.
- Phase 6D-3 Confluence builder:
  `project/confluence_mtf_artifact_builder.py`.
- Phase 6I-18 next-probe handoff (operational state /
  source-wait):
  `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md`.
- CLAUDE.md sec 6 — current sprint state.
