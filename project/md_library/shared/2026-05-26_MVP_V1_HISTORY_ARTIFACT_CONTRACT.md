# PRJCT9 MVP v1 History Artifact Contract

**Date:** 2026-05-26

**Status:** Authoritative for Phase 3a implementation. Defines the per-secondary daily history artifact required by MVP v1 ranking math. Does NOT initiate implementation; the Phase 3a implementation PR cites this contract and emits the artifact specified here.

**Anchor documents:**

- 2026-05-25 MVP Ranking Contract (PR #325).
- 2026-05-26 MVP Ranking Contract Display Contract amendment (PR #330).
- 2026-05-26 React Migration Declaration and Frontend Contract (PR #329).
- 2026-05-25 TrafficFlow K-Artifact Producer Reconciliation audit.
- 2026-05-25 TrafficFlow Runner Phase E Canonical Write Contract evidence (PRs #321, #322).
- 2026-05-26 MVP v0 ranking engine (PR #326).

---

## Purpose

This document specifies the contract for the MVP v1 per-secondary daily history artifact. The artifact is the data input required by MVP v1 ranking math, specifically Steps v1.2 through v1.8 in the MVP Ranking Contract.

The contract exists because the v1 ranking math depends on per-bar five-timeframe signal state and per-bar close prices that Phase E does not currently emit. The v1 history artifact fills that gap.

This artifact is an intermediate engine input. The v1 ranking engine consumes it directly. The v1 Dash front-end and the eventual React app consume the v1 ranking artifact emitted by the v1 ranking engine, not this history artifact directly.

This document does NOT implement the artifact. It specifies what the Phase 3a implementation PR must produce.

---

## Naming Convention Note

The artifact is referred to throughout this document as the "MVP v1 history artifact" or simply "v1 history artifact."

The filename and on-disk location are specified in the Artifact Identity section.

The "v1" in the filename refers to MVP v1, not the schema version. Schema versioning is handled by the `schema_version` field inside the file.

---

## Relationship To Existing Contracts

This document extends, but does not modify, the MVP Ranking Contract. The MVP Ranking Contract specifies what v1 ranking math computes. This document specifies the input artifact the v1 ranking engine reads to do that computation.

This document is independent of the React Migration Declaration. The React app consumes a frontend-facing ranking artifact emitted by the v1 ranking engine. It does not consume this per-secondary history artifact directly.

This document does not change MVP v0. v0 reads only the existing Phase E canonical output and is unaffected.

---

## Diagnostic Findings

The findings below were collected via read-only inspection of the repository at main HEAD `a42a000` (PR #330 merge). No code was modified and no pipeline component was run.

**Existing Phase E write structure.** `trafficflow_runner.py` runs each secondary's canonical-write path inside `_execute_isolated_write` (approximately lines 1941 onward). The per-K loop writes `<RUN_ROOT>/<SEC>/board_rows_k=<K>.json` and `<RUN_ROOT>/<SEC>/board_rows_k=<K>.csv` (lines 2320 to 2346), followed on the success path by `<RUN_ROOT>/<SEC>/secondary_manifest.json` (lines 2371 to 2405) and a zero-byte `<RUN_ROOT>/<SEC>/.done` (lines 2412 to 2414). The orchestrator (`trafficflow_canonical_orchestrator.py`) fans these per-secondary workers out as subprocesses and owns run-level `progress.json`, `run_status.json`, `run_manifest.json`, and the global `output/trafficflow/selected_output.json` pointer. The orchestrator's module docstring (lines 9 to 27) enumerates exactly this set of artifacts.

**Atomic-write and sanitization patterns.** `trafficflow_runner.py` defines `_atomic_write_bytes(path, data)` (line 1899) and `_atomic_write_json(path, payload)` (line 1908). Both write a `.tmp` sibling and use `os.replace` to swap into place. Privacy sanitization uses `sanitize_for_json(value, *, project_root)` (line 538) and `_scrub_embedded_absolute_paths(value)` (line 588). The orchestrator imports `_atomic_write_bytes`, `_atomic_write_json`, `sanitize_for_json`, `_scrub_embedded_absolute_paths`, and `path_for_output` directly from the runner (orchestrator lines 52 to 57), so a Phase 3a writer that lives in either module already has the helpers in scope.

**Per-secondary manifest patterns.** `secondary_manifest.json` is built inline in the runner's success path as a single dict with `schema_version`, `secondary`, `invocation_id`, timestamps, elapsed, the resolved `selected_build_path` and `selected_build_sha256`, `k_requested`, `per_k_summary`, and `artifacts_written`. The schema constant `PHASE_E_RUN_MANIFEST_SCHEMA = "trafficflow_runner_phase_e_v1"` lives at line 130. The manifest is written before `.done` so that downstream consumers only see a self-contained provenance record on completion.

**Signal library storage and signal encoding.** `signal_library/multi_timeframe_builder.py` writes per-(ticker, interval) signal libraries as pickle files under `SIGNAL_LIBRARY_DIR = os.environ.get('SIGNAL_LIBRARY_DIR', 'signal_library/data/stable')` (line 63). The daily file is `<TICKER>_stable_v1_0_0.pkl`; non-daily intervals use `<TICKER>_stable_v1_0_0_<INTERVAL>.pkl` (lines 868 to 871), where INTERVAL is one of `1wk`, `1mo`, `3mo`, `1y`. The internal encoding observed in the bridge (see below) uses the canonical strings `Buy`, `Short`, `None`, and `missing` for the projected signal state.

**Multi-timeframe bridge conventions.** `trafficflow_multitimeframe_bridge.py` defines the canonical multi-timeframe set `DEFAULT_TIMEFRAMES = ("1d", "1wk", "1mo", "3mo", "1y")` (line 159) and the signal vocabulary `PRESSURE_SIGNAL_BUY = "Buy"`, `PRESSURE_SIGNAL_SHORT = "Short"`, `PRESSURE_SIGNAL_NONE = "None"`, `PRESSURE_SIGNAL_MISSING = "missing"` (lines 164 to 167). It exposes `project_signal_to_timeframes(daily_dates, daily_signals, timeframes)` (line 295) which resamples a per-day daily signal series onto each timeframe with `resample(<freq>).last()` and reindexes back onto the daily grid such that the current (open) period inherits the previous closed period's signal. This bridge is a reference for how a per-day per-timeframe signal history can be projected; whether Phase 3a invokes it directly or reads the per-interval signal libraries is the implementation PR's choice. The bridge is not a runtime dependency of the existing Phase E canonical-write path.

**Price cache format and access pattern.** `stackbuilder_price_cache_writer.py` documents the canonical layout for daily close prices: `price_cache/daily/<TICKER>.parquet`, with a `.csv` fallback (lines 9, 142, 173 to 174). The default relative path is `DEFAULT_STACKBUILDER_PRICE_CACHE_DIR_RELATIVE = "price_cache/daily"`. This is the existing source of truth for daily close-price bars used by StackBuilder, and is the natural source the Phase 3a artifact builder will read.

**Discovery pointer.** `output/trafficflow/selected_output.json` is written by the orchestrator's `_maybe_update_selected_output` and references the run root for the most recent canonical run. Because the v1 history artifact lives inside the same per-secondary directory as `board_rows_k=*.{json,csv}` and `secondary_manifest.json`, no additional discovery pointer is required.

**Gaps the Phase 3a implementation PR must close.**

1. There is no existing per-secondary daily history artifact under any Phase E run root. The artifact path specified in the Artifact Identity section below does not yet exist on disk for any run.
2. There is no Phase E write path that emits per-bar per-timeframe signal history. The runner's per-secondary loop terminates at `board_rows_k=*` plus the secondary manifest and `.done`.
3. There is no existing helper that maps the per-interval signal library encoding (as written by `signal_library/multi_timeframe_builder.py`) to the BUY / SHORT / NONE / UNAVAILABLE vocabulary specified by this contract. Phase 3a must either reuse the bridge's `project_signal_to_timeframes` projection on top of a daily signal source, or read the per-interval signal libraries directly and project them onto the daily grid. Either approach must distinguish "explicit no-signal at this date" (NONE) from "no coverage exists at this date" (UNAVAILABLE).
4. There are no tests for the v1 history artifact's schema, date coverage, signal encoding, or Phase E integration. Phase 3a must add them.

This section describes observed repo facts. It does not begin implementation.

---

## Artifact Identity

The v1 history artifact is a per-secondary JSON file written under the canonical Phase E run root.

**Path:**

```
output/trafficflow/runs/<UTC_TS>/<SEC>/v1_history.json
```

**Filename:** `v1_history.json`.

The artifact lives in the same per-secondary directory as `board_rows_k=6.json` and `secondary_manifest.json`. This co-location means the artifact is discoverable through the existing `selected_output.json` pointer mechanism without adding a new discovery pointer.

The artifact is emitted once per secondary per Phase E run. It is not appended to or modified after creation. Each new Phase E run produces a complete `v1_history.json` for each successful secondary.

The "v1" in the filename refers to MVP v1, not the schema version. Schema versioning is handled by the `schema_version` field inside the file.

---

## Schema

The artifact is a single JSON object.

### Required top-level fields

- `schema_version` (string): exact value `"mvp_v1_history_v1"`.
- `secondary` (string): the secondary ticker symbol. Must match the per-secondary directory name.
- `generated_at_utc` (string): UTC ISO 8601 timestamp recording when the artifact was written.
- `trafficflow_run_id` (string): the Phase E run id that produced this artifact.
- `trafficflow_run_root` (string): repo-relative path to the Phase E run root, sanitized through the existing Phase E sanitization helpers.
- `effective_evaluation_date_utc` (string): UTC ISO 8601 date used as the upper bound for the artifact. Format `YYYY-MM-DD`.
- `date_range_start_utc` (string): first included bar date. Format `YYYY-MM-DD`.
- `date_range_end_utc` (string): last included bar date. Format `YYYY-MM-DD`.
- `timeframes_covered` (array of strings): exactly `["1d", "1wk", "1mo", "3mo", "1y"]`, in that order.
- `bar_count` (integer): number of records in `bars`. Must equal `len(bars)`.
- `bars` (array of objects): per-bar history records, sorted ascending by `date_utc`.
- `issues` (array of objects): data-quality or construction issues. Empty array if none.

### Per-bar record fields

Each element of `bars` is an object with these fields:

- `date_utc` (string): UTC ISO 8601 date for this bar. Format `YYYY-MM-DD`.
- `close` (number): secondary close price on this date. Emitted as a JSON number, not a string.
- `signals` (object): exactly five keys, `"1d"`, `"1wk"`, `"1mo"`, `"3mo"`, `"1y"`. Each value is one of `"BUY"`, `"SHORT"`, `"NONE"`, `"UNAVAILABLE"`.

### Signal value semantics

- `"BUY"`: the timeframe's signal at this date is a long-direction signal.
- `"SHORT"`: the timeframe's signal at this date is a short-direction signal.
- `"NONE"`: the timeframe explicitly emitted no signal at this date. This is a deliberate no-position state and is distinct from missing coverage.
- `"UNAVAILABLE"`: the timeframe's signal could not be determined because upstream coverage does not exist for this date.

For match-rule purposes, `NONE` and `UNAVAILABLE` are both wildcard-equivalent. For audit purposes, they must remain distinct in the artifact.

Bars are sorted ascending because Phase 3b computes next-bar capture by walking from each bar to the next included bar in the array. The "next bar" in Step v1.5 of the MVP Ranking Contract means the next included trading bar in this array, not necessarily the next calendar day.

### Issues array record fields

Each element of `issues` is an object with these fields:

- `error_code` (string): finite issue code from the allowed set below, or a narrowly-scoped addition documented by the Phase 3a implementation PR.
- `timeframe` (string, optional): affected timeframe, if applicable. One of `"1d"`, `"1wk"`, `"1mo"`, `"3mo"`, `"1y"`.
- `date_range` (array of two strings, optional): affected date range, if applicable. Each entry is a `YYYY-MM-DD` UTC date.
- `message_sanitized` (string): privacy-sanitized human-readable description. Sanitized through the same Phase E sanitization helpers used by the rest of the Phase E artifact set.

### Initial allowed issue codes

- `signal_library_partial_coverage`
- `signal_library_missing`
- `signal_encoding_unrecognized`
- `price_cache_gap`
- `price_cache_missing`
- `price_close_unusable`

The Phase 3a implementation PR may add narrowly-scoped issue codes if diagnostics show a missing case. Any added code must be documented in the PR body and pinned by tests.

---

## Date Range Rule

The artifact's `bars` array is built from daily close-price bars for the secondary.

A date is included if and only if all of the following are true:

1. The date has a usable daily close price for the secondary.
2. The date is at or before `effective_evaluation_date_utc`.
3. At least one of the five timeframe signal sources has coverage for that date.

The date range is therefore the usable daily close-price range, bounded above by the Phase E effective evaluation date, intersected with the union of the five timeframe signal coverage ranges.

If some timeframes have coverage on an included date and others do not, the bar is included. Timeframes without coverage on that date are encoded as `UNAVAILABLE`.

If a timeframe has coverage and explicitly emits no signal on the date, encode `NONE`.

If the close price is missing or unusable for a date, omit that date from `bars`. If the gap is significant, record a `price_cache_gap` issue. The Phase 3a implementation PR defines and tests the threshold for "significant".

Bars in `bars` are sorted strictly ascending by `date_utc`.

---

## Producer Integration

The v1 history artifact is produced by Phase E. It is not produced by a separate standalone producer path.

The Phase 3a implementation PR chooses one of these integration paths:

### Option A: Direct extension of the Phase E runner / orchestrator

The artifact emission is added to the existing per-secondary Phase E execution path inside `trafficflow_runner.py` (and, where applicable, `trafficflow_canonical_orchestrator.py`). It writes `v1_history.json` under the same per-secondary run directory as `board_rows_k=6.json` and `secondary_manifest.json`. It reuses the existing `_atomic_write_json`, `_atomic_write_bytes`, `sanitize_for_json`, and `_scrub_embedded_absolute_paths` helpers, and shares the Phase E success-vs-failure semantics.

### Option B: Phase E companion writer module

A new narrowly-scoped companion writer module is added and invoked by the existing Phase E runner / orchestrator per secondary. The companion writer constructs the history artifact and writes `v1_history.json` under the canonical Phase E run root. It reuses the existing Phase E atomic-write and privacy-sanitization helpers, imported from `trafficflow_runner`.

Option B is the default unless Option A is clearly simpler and does not create scope creep.

Either option must preserve these rules:

- The canonical invocation path is Phase E. There is no parallel standalone canonical producer.
- The artifact is written under `output/trafficflow/runs/<UTC_TS>/<SEC>/`.
- The artifact is written atomically using the `.tmp` + `os.replace` pattern.
- The artifact is privacy-sanitized using the existing Phase E sanitization pattern. No absolute filesystem paths may appear in the emitted JSON.
- The artifact is schema-stamped with `schema_version = "mvp_v1_history_v1"`.
- Discovery remains `output/trafficflow/selected_output.json` plus the canonical Phase E run root. No new selected-output pointer is introduced.

The implementation may add helper functions to read or normalize per-interval signal libraries and daily close prices if no suitable helper exists today. That is acceptable only if the helpers are invoked through the Phase E artifact-emission path and do not become a new parallel canonical producer.

---

## Backward Compatibility

Phase E runs produced before Phase 3a will not have `v1_history.json` files. This is expected.

No backfill of `v1_history.json` for historical Phase E runs is required by Phase 3a.

The v1 ranking engine in Phase 3b must fail closed per secondary when `v1_history.json` is missing: record an issue, exclude that secondary from the v1 ranking, and continue with the remaining secondaries. If all requested secondaries lack usable v1 history artifacts, the v1 ranking engine fails the run.

Older runs remain v0-only. MVP v0 continues to read only the existing Phase E canonical output (`board_rows_k=6.json` + `secondary_manifest.json` + `selected_output.json`) and is unaffected by this artifact.

---

## Implementation Status

This document does not implement the artifact. Phase 3a is a separate implementation PR scoped after this contract lands.

### What exists today

- TrafficFlow Phase E produces `board_rows_k=*.{json,csv}`, `secondary_manifest.json`, run-level `progress.json`, `run_status.json`, `run_manifest.json`, and the global `output/trafficflow/selected_output.json` pointer.
- Signal libraries exist for the relevant timeframes or can be generated by existing signal-library tooling under `signal_library/data/stable/`.
- Daily close prices exist through `price_cache/daily/<TICKER>.parquet` (with a `.csv` fallback), maintained by the StackBuilder price-cache writer path.
- Atomic-write and sanitization helpers exist in `trafficflow_runner.py` (`_atomic_write_bytes`, `_atomic_write_json`, `sanitize_for_json`, `_scrub_embedded_absolute_paths`, `path_for_output`) and are already imported by the orchestrator.
- The multi-timeframe bridge defines the canonical timeframe set and a per-day projection from a daily signal series onto each timeframe.

### What does not exist today

- The `v1_history.json` artifact.
- The Phase E emission path for `v1_history.json`.
- A helper that maps per-interval signal-library encodings to the BUY / SHORT / NONE / UNAVAILABLE vocabulary specified by this contract.
- Tests for `v1_history.json` schema, date coverage, signal normalization, issue handling, and Phase E integration.

### What Phase 3a must do

1. Choose Option A or Option B from the Producer Integration section.
2. Implement `v1_history.json` emission for each successful Phase E secondary.
3. Emit atomic, privacy-sanitized, schema-stamped JSON matching this contract.
4. Add tests using fake Phase E-style inputs under `pytest` `tmp_path`. No live pipeline invocation, no Dash launch.
5. Preserve `selected_output.json` discovery semantics.
6. Avoid any v1 ranking, sign-flipping, BUY / SHORT recommendation, match-rule scoring, CCC, Dash UI, or React implementation.

### What Phase 3a must not do

- Implement the v1 ranking engine.
- Implement the v1 Dash UI additions.
- Implement sign-applied captures, BUY / SHORT recommendation logic, match-rule scoring, or CCC computation.
- Modify MVP v0 ranking or Dash surfaces.
- Begin React work.

---

## Open Questions Deferred To The Implementation PR

The contract intentionally defers only implementation-specific details that do not change the JSON schema:

- The exact threshold at which a `price_cache_gap` issue is recorded instead of silently omitting unusable close-price dates.
- The exact mapping from each upstream signal-library internal encoding to BUY, SHORT, NONE, and UNAVAILABLE.
- Whether the writer is implemented as Option A or Option B.
- The exact tests used to pin the chosen helper boundaries.

The implementation PR resolves each question, documents the choice in its final report and PR body, and adds tests that pin the behavior.

The following are not open questions and must not be changed by Phase 3a without a new contract amendment:

- Filename `v1_history.json`.
- `schema_version` value `"mvp_v1_history_v1"`.
- Top-level field names.
- Use of `secondary` rather than `ticker` as the canonical identifier.
- `bars` sorted ascending.
- `close` emitted as a JSON number.
- Signal values limited to `"BUY"`, `"SHORT"`, `"NONE"`, `"UNAVAILABLE"`.
- `selected_output.json` remains the discovery pointer; no new pointer is introduced.

---

## Forbidden Behaviors

The Phase 3a implementation PR must not:

- Produce `v1_history.json` from a standalone canonical path outside Phase E.
- Create a new top-level output directory parallel to `output/trafficflow/`.
- Create a new discovery pointer parallel to `selected_output.json`.
- Modify the `board_rows_k=*.{json,csv}`, `secondary_manifest.json`, `run_manifest.json`, or `selected_output.json` schemas.
- Modify `mvp_ranking_v0.py` or `mvp_signal_board.py`.
- Modify the MVP Ranking Contract or the React Migration Declaration.
- Implement v1 ranking math.
- Implement sign-applied captures.
- Implement BUY / SHORT recommendation logic.
- Implement match-rule scoring.
- Compute CCC.
- Add a CCC chart.
- Add React work.
- Skip atomic writes or privacy sanitization.
- Emit absolute filesystem paths.

If Phase 3a finds it needs a field not specified here, it must stop and request a contract amendment rather than quietly extending the artifact.

---

## References

- 2026-05-04 PRJCT9 North Star.
- 2026-04-30 PRJCT9 Sprint Plan.
- 2026-05-25 Confluence Terminology Glossary (PR #323).
- 2026-05-25 Known Bugs Log (PR #324).
- 2026-05-25 MVP Ranking Contract (PR #325).
- 2026-05-26 MVP v0 ranking engine (PR #326).
- 2026-05-26 MVP v0 Dash front-end (PR #327).
- 2026-05-26 MVP v0 Dash live operator fixes (PR #328).
- 2026-05-26 React Migration Declaration and Frontend Contract (PR #329).
- 2026-05-26 MVP Ranking Contract Display Contract amendment (PR #330).
- 2026-05-25 TrafficFlow K-Artifact Producer Reconciliation audit.
- 2026-05-25 TrafficFlow Runner Phase E Canonical Write Contract.

End of file.
