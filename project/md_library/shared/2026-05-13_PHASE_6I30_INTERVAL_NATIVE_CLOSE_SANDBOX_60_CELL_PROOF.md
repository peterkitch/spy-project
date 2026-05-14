# Phase 6I-30: interval-native `close` series for multi-window signal libraries + sandbox 60-cell SPY proof

Sprint date: **2026-05-13** (evidence captured **2026-05-14** UTC).
Branch: `phase-6i-30-interval-native-close-sandbox-proof`.
Doc: this file.

Phase 6I-29 (PR #246) closed the daily-window blocker via exact-date member alignment; the chain landed on 12 of 60 cells prepared and 48 non-daily cells still skipping with `target_close_join_incomplete`. Phase 6I-30 closes the non-daily blocker the right way: instead of teaching the adapter to invent bar-end semantics, **extend the signal-library builder so every interval library carries its own native `close` series aligned 1:1 with the library's own `dates` axis**. The builder already owns interval construction (it's the layer that calls `df.resample('YE-DEC').last()` etc); the per-interval `close` belongs there.

The sandbox proof harness then runs the full read-only chain against a temp-built set of interval libraries with native `close`. **The result is the first observation in this sprint of `prepared_cell_count=60`, `payload_ready=true`, and planner `patch_ready=true`.**

---

## 0. TL;DR

| Check | Result |
|---|---|
| Production roots mutated | **No** — 0/0/0 added/removed/changed across all 5 roots (83,025 files) |
| Sandbox SPY can prepare the full canonical 60-cell grid | **Yes** — `prepared_cell_count=60`, `skipped_cell_count=0`, `can_evaluate_full_60_cell_grid=true` |
| Phase 6I-29 `target_close_join_incomplete` blocker | **Resolved** for the sandbox build — non-daily libraries now carry native `close` |
| Phase 6I-28 close-source fallback consulted | **No** — every cell used its library's native close (the fallback is silent when native close is present) |
| Payload builder `payload_ready` | **true** |
| Planner `patch_ready` | **true** (first time in the sprint) |
| Writer dry-run `planner_patch_ready` | **true** — but `wrote_artifact=false` because `--write` was not passed |
| Phase 6I-22 strict full-member-coverage gate | **Unchanged** (pinned by 10 new tests) |
| Phase 6I-25 writer-mutation contract | **Unchanged** — gates #1 / #2 still block this dry-run; #3 / #4 / #5 are now reachable for a future authorized run |
| Future production write phase | **Yes — newly required.** A reviewed signal-library rebuild against production data is the next step before any authorized writer run |
| Gap audit `has_true_multiwindow_k_engine_outputs` | **false** before AND after (no production artifact written) |

---

## 1. What changed

### 1.1 Production builder (`signal_library/multi_timeframe_builder.py`)

Two surgical changes:

**(a) Persisted `close` field.** Every library produced by `generate_signals_for_interval` now carries:

```python
'close': df['Close'].tolist(),
```

aligned 1:1 with `dates` / `signals`. This is the same `Close` column the builder already used to compute SMAs and signals — no fabrication, no recomputation, just persisted instead of discarded. The multi-window K input adapter's existing `_extract_target_close` helper already recognises `close` / `target_close` / `Close`, so no adapter change was needed.

**(b) Optional `df=` injection seam.** `generate_signals_for_interval(ticker, interval, *, df=None)` now accepts an injected OHLCV DataFrame. When supplied, the builder uses it directly and skips the yfinance call:

```python
if df is None:
    df = fetch_interval_data(ticker, interval)
```

This seam exists so the Phase 6I-30 sandbox builder can drive the same SMA / signal / close pipeline from a local-cache source. The default behaviour for production callers (`df=None`) is unchanged.

### 1.2 New sandbox builder (`signal_library/multi_timeframe_sandbox_builder.py`)

Strictly sandbox-only proof harness:

- Reads daily OHLCV from `cache/results/<TICKER>_precomputed_results.pkl` via `provenance_manifest.load_verified_pickle_artifact` — no raw `pickle.load` is added.
- Resamples to each interval using the **same pandas frequency contract the production builder already uses**: `W-MON` for `1wk`, `MS` for `1mo`, `QS` for `3mo`, `YE-DEC` for `1y`. Daily passes through unchanged. The resample lives inside the builder layer (where calendar logic belongs); the adapter remains exact-date / no-projection.
- Calls `generate_signals_for_interval(ticker, interval, df=interval_df)` so the saved library has the full SMA / signal / native-close shape.
- Writes to an explicit sandbox `--output-dir` that the script REFUSES to point under `signal_library/data/stable` (path-suffix check; rc=2 if violated).
- Supports an `--end-date <YYYY-MM-DD>` cutoff applied to every ticker's daily DataFrame before resampling, so a heterogeneous production-cache state can still produce a common-cutoff sandbox snapshot.
- No yfinance / dash / subprocess imports (AST-verified by tests). No network.

The sandbox builder is reachable as `python -m signal_library.multi_timeframe_sandbox_builder`.

### 1.3 Why interval-native close belongs in the builder, not the adapter

| Concern | Builder | Adapter |
|---|---|---|
| Owns interval calendar (W-MON / MS / QS / YE-DEC) | **Yes** — already calls `df.resample(...)` | **No** — strictly exact-date / no-projection |
| Already has access to per-interval `Close` series | **Yes** — fetches OHLCV and computes SMAs from it | **No** — only loads precomputed signal libraries |
| Persisting `close` is a 1-line schema addition | **Yes** | N/A |
| Inventing bar-end → daily-cache-date map would be projection | N/A | **Yes** — forbidden |

The Phase 6I-29 evidence doc § 7 already framed option (a) "signal-library builder extension" as the cleanest fix; Phase 6I-30 implements that option.

### 1.4 Tests added (10 new)

A new focused test file `test_scripts/test_multi_timeframe_builder_interval_close.py` pins:

1. Generated library includes native `close` aligned 1:1 with `dates` / `signals`.
2. Persisted `close` matches the source DataFrame's `Close` column values byte-for-byte.
3. Adapter prefers native `close` over the Phase 6I-28 fallback — the fallback loader is NEVER called when native close is present (pinned by a `close_loader` that raises if invoked).
4. Sandbox builder refuses to write under `signal_library/data/stable` (rc=2; output dir does NOT get created).
5. Sandbox builder missing-args returns rc=2.
6. Sandbox builder unknown-flag returns rc=2.
7. Sandbox builder end-to-end: monkey-patched local-cache load → produces 5 sandbox interval libraries, each with native close aligned to its dates / signals.
8. Sandbox builder's interval-frequency map matches the production builder's `fetch_interval_data` choices (static text check — surfaces drift immediately).
9. Sandbox builder has no raw `pickle.load` (AST-scanned).
10. Sandbox builder has no `yfinance` / `dash` / `subprocess` imports (AST-scanned).

The repo-wide B12 raw-pickle static regression guard continues to pass without an allowlist entry.

---

## 2. Repo state

```
Branch: phase-6i-30-interval-native-close-sandbox-proof
Main HEAD (at branch creation): 1e07fae (Phase 6I-29, PR #246)
```

---

## 3. Test results

```
Phase 6I-30 builder/sandbox tests : 10 passed
Adapter                           : 46 passed (unchanged)
Diagnostic                        : 22 passed (unchanged)
Core                              : 38 passed
Builder                           : 31 passed
Planner                           : 32 passed
Writer                            : 39 passed
Gap audit                         : 23 passed
Static regression                 :  9 passed (incl. B12 raw-pickle guard)
                                  -----
Focused 9-way sweep               : 250 passed in 5.97 s

Full repo regression              : 1,837 passed in 5:48 (0 failures)

Warnings                          : 60 pre-existing pandas fragmentation +
                                    105 SMA-loop fragmentation from the
                                    new Phase 6I-30 tests (the production
                                    builder always emits these when it
                                    builds 114 SMAs; cosmetic, not a
                                    regression)
py_compile                        : clean across all changed Python files
git diff --check                  : clean
```

---

## 4. Sandbox 60-cell SPY proof

### 4.1 Temp evidence directory

```
C:\Users\sport\AppData\Local\Temp\phase_6i30_interval_close_sandbox_60_cell_proof\
├── sandbox_libs/               (75 PKL files = 15 tickers × 5 intervals)
├── 00_snapshot_before.json
├── 01_diagnostic_spy.json
├── 02_gap_audit_before.json + 02_payload_builder_spy.json
├── 03_planner_spy.json
├── 04_writer_dry_run.json + 04b_writer_execution_log.jsonl
├── 05_gap_audit_after.json
├── 99_snapshot_after.json
├── 99b_snapshot_diff.json
├── inspect_member_dates.py     (one-off sandbox inspector)
├── diff_helper.py              (copied from Phase 6I-26)
└── snapshot_helper.py          (copied from Phase 6I-26)
```

Pinned interpreter: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

### 4.2 Sandbox builder run

The SPY K=1..12 leaderboard requires 14 unique member tickers: `AROW, AWR, CLH, CP, EXPO, FCFS, GBCI, HCSG, JNJ, LLY, MO, PRA, PRGO, TEF`. SPY + 14 members × 5 intervals = **75 sandbox libraries** built from local cache; **zero failures**.

Production cache PKL last-dates are heterogeneous: SPY ends 2026-05-12, TEF ends 2026-01-28, the other 13 tickers end 2026-05-04. The Phase 6I-22 strict full-member-coverage gate correctly refuses to align when any target date is missing from a member's axis, so a common cutoff is required. The sandbox builder's `--end-date 2026-01-28` flag trims every ticker's daily DataFrame to TEF's last-date before resampling — yielding a sandbox snapshot where every member library covers every target date.

Command:

```
"<pinned-interp>" -m signal_library.multi_timeframe_sandbox_builder \
  --tickers SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO,TEF \
  --intervals 1d,1wk,1mo,3mo,1y \
  --cache-dir cache/results \
  --output-dir "<TEMP>/sandbox_libs" \
  --end-date 2026-01-28
# rc=0; "written=75 failures=0"
```

### 4.3 Adapter diagnostic against sandbox

```
"<pinned-interp>" multiwindow_k_input_adapter_diagnostic.py \
  --ticker SPY \
  --stackbuilder-root output/stackbuilder \
  --signal-library-dir "<TEMP>/sandbox_libs" \
  --cache-dir cache/results
# rc=0
```

Top-level result:

```json
{
  "ticker": "SPY",
  "prepared_cell_count": 60,
  "skipped_cell_count": 0,
  "can_evaluate_full_60_cell_grid": true,
  "adapter_issue_codes": [],
  "counts_by_skipped_reason": {},
  "dominant_skipped_reason": null,
  "recommended_next_action": "adapter_ready_for_writer_evidence_run"
}
```

**First observation of `can_evaluate_full_60_cell_grid=true` in the sprint.**

### 4.4 Payload builder

```
"<pinned-interp>" multiwindow_k_engine_payload_builder.py --ticker SPY \
  --stackbuilder-root output/stackbuilder \
  --signal-library-dir "<TEMP>/sandbox_libs" \
  --cache-dir cache/results
# rc=0
```

| Field | Value |
|---|---|
| `payload_ready` | **true** |
| `cell_count` | **60** |
| `issue_codes` | `[]` |

### 4.5 Planner dry-run

```
"<pinned-interp>" multiwindow_k_confluence_patch_planner.py --ticker SPY \
  --artifact-root output/research_artifacts \
  --stackbuilder-root output/stackbuilder \
  --signal-library-dir "<TEMP>/sandbox_libs" \
  --cache-dir cache/results \
  --current-as-of-date 2026-05-13
# rc=0
```

| Field | Value |
|---|---|
| `patch_ready` | **true** |
| `payload_summary.payload_ready` | true |
| `issue_codes` | `[]` |
| `recommended_next_action` | `ready_for_reviewed_artifact_write` |
| `fields_to_add` | `[per_window_k_metrics, build_wide_window_alignment, multiwindow_k_engine_payload_metadata]` |

### 4.6 Writer dry-run (NO `--write`)

```
"<pinned-interp>" multiwindow_k_confluence_patch_writer.py --ticker SPY \
  --artifact-root output/research_artifacts \
  --stackbuilder-root output/stackbuilder \
  --signal-library-dir "<TEMP>/sandbox_libs" \
  --cache-dir cache/results \
  --execution-log "<TEMP>/04b_writer_execution_log.jsonl"
# rc=0
```

| Field | Value |
|---|---|
| `write_requested` | **false** |
| `write_authorized` | **false** |
| `planner_patch_ready` | **true** (first time observed in the sprint) |
| `wrote_artifact` | **false** |
| `issue_codes` | `[write_not_requested]` |
| `recommended_next_action` | `dry_run_review_patch_plan` |
| `pre_write_sha256 == post_write_sha256` | **Yes** (`db10e089…` unchanged) |

The Phase 6I-25 writer-mutation contract is intact. Gates #1 (`--write`) and #2 (`PRJCT9_AUTOMATION_WRITE_AUTH`) BOTH block this dry-run — exactly as required. Gates #3 (`planner_patch_ready`), #4 (`artifact_path` resolves), and #5 (`_writer_plan_payload_is_consistent(plan)` accepts the plan) are NOW reachable for a future supervised authorized run. None of those three gates were weakened by Phase 6I-30; only their precondition (full-60-cell grid availability) is now satisfiable in sandbox.

### 4.7 Gap audit before / after

| Field | Before | After |
|---|---|---|
| `states[0].has_true_multiwindow_k_engine_outputs` | `false` | `false` |

Unchanged on both probes — no production artifact was written.

---

## 5. Production-root diff (0/0/0)

```json
{
  "cache/results":              {"added": 0, "removed": 0, "changed": 0},
  "cache/status":               {"added": 0, "removed": 0, "changed": 0},
  "output/research_artifacts":  {"added": 0, "removed": 0, "changed": 0},
  "output/stackbuilder":        {"added": 0, "removed": 0, "changed": 0},
  "signal_library/data/stable": {"added": 0, "removed": 0, "changed": 0},
  "TOTAL":                      {"added": 0, "removed": 0, "changed": 0}
}
```

| Root | Files | Added | Removed | Changed |
|---|---|---|---|---|
| `cache/results` | 3,239 | 0 | 0 | 0 |
| `cache/status` | 1,634 | 0 | 0 | 0 |
| `output/research_artifacts` | 35 | 0 | 0 | 0 |
| `output/stackbuilder` | 5,218 | 0 | 0 | 0 |
| `signal_library/data/stable` | 72,899 | 0 | 0 | 0 |
| **TOTAL** | **83,025** | **0** | **0** | **0** |

Zero added / zero removed / zero changed across all 83,025 files in all five production roots.

---

## 6. Strict-coverage / no-projection invariants preserved

| Invariant | Status |
|---|---|
| Phase 6I-22 strict full-member coverage (K=6 with one missing member ≠ K=5) | Unchanged. Pinned by amended adapter tests. |
| Adapter contains no `.resample()` / `.ffill()` calls | Unchanged. AST-scanned by `test_close_source_helpers_make_no_projection_calls` and the broader adapter no-projection test. |
| No raw `pickle.load` outside the central provenance loader | Unchanged. B12 static regression guard still passes without an allowlist entry. The sandbox builder uses `load_verified_pickle_artifact`. |
| Phase 6I-28 close-source fallback contract | Unchanged. The fallback is only consulted when native close is absent; for sandbox libraries that carry native close, the fallback loader is never called. |
| Phase 6I-29 exact-date member alignment contract | Unchanged. Slow-path alignment fires only when member length disagrees AND the alignment helper succeeds only when every target date is present on the member axis. |
| Phase 6I-25 writer-mutation contract | Unchanged. Two-key authorization gate (`--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`) plus the three downstream gates (`planner_patch_ready`, `artifact_path` resolution, `_writer_plan_payload_is_consistent`). |
| Builder resample of daily → non-daily | The Phase 6I-30 sandbox builder's resample is the ONLY resample site touched by this phase, and it lives inside the builder layer — the adapter still does no resampling. |

---

## 7. No-production-activity confirmation

| Activity | Performed? |
|---|---|
| Writer `--write` invocation | **No** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` set | **No** (not even via PowerShell-scoped `$env:`) |
| Authorized launcher script created | **No** |
| Source refresh (`signal_engine_cache_refresher`) | **No** |
| `yfinance` fetch | **No** (the sandbox builder explicitly reads from local cache) |
| `confluence_pipeline_runner` invocation | **No** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster / Confluence batch execution | **No** |
| Production data write | **No** (0/0/0 across 83,025 files) |
| Subprocess invocations from production modules | **No** |
| Production signal-library rebuild (writes to `signal_library/data/stable`) | **No** — the sandbox builder REFUSES that path; safety pinned by `test_sandbox_builder_refuses_production_stable_dir` |
| Execution-log writes to `output/automation_logs/` | **No** (writer's `--execution-log` argument pointed at the temp evidence dir) |

---

## 8. Operational state carried forward

- Cache state: `cache_date_range_end=2026-05-12`; `current_as_of_date=2026-05-13` (rolled at Phase 6I-26 evidence pass). Note that the sandbox proof's `--end-date 2026-01-28` cutoff is a sandbox-only construct; it does NOT change production cache or cutoff state.
- STATE 4 / cache-behind-cutoff (per Phase 6I-17 4-state list).
- Production `has_true_multiwindow_k_engine_outputs` — still `false` for SPY (gap audit, before AND after).
- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still open.
- Writer-surface provider telemetry — still pending.

---

## 9. Next required step: production signal-library rebuild

The sandbox proof demonstrates that **if** every interval library has native `close`, **then** the multi-window K chain reaches `patch_ready=true` for SPY. The production signal libraries at `signal_library/data/stable/<TICKER>_stable_v1_0_0[_<interval>].pkl` currently lack the `close` field; the Phase 6I-30 builder change will populate that field for every library produced by future rebuilds.

A future supervised production rebuild phase is now the next required step. It should:

1. Run `multi_timeframe_builder` against current OHLCV for every ticker in the universe (NOT in this phase — yfinance is forbidden here).
2. Verify each rebuilt library carries the Phase 6I-30 native `close` aligned 1:1 with `dates` / `signals`. The existing tests pin this contract.
3. Stage the rebuild in a sandbox directory first and run this same chain (diagnostic / payload builder / planner / writer dry-run) against it to confirm `patch_ready=true`.
4. Only then promote to `signal_library/data/stable/` under supervised authorization.

After the production rebuild, an authorized writer run becomes possible (gates #1 / #2 of the Phase 6I-25 contract require explicit operator action; gates #3 / #4 / #5 are already passable).

---

## 10. Validation

- `git diff --check`: clean.
- `git diff --stat`: 5 files touched — 1 production module modified (`signal_library/multi_timeframe_builder.py`, +20 lines), 1 new production module added (`signal_library/multi_timeframe_sandbox_builder.py`, +330 lines), 1 new test file added (`test_scripts/test_multi_timeframe_builder_interval_close.py`, +280 lines), 1 new Markdown evidence doc added (this file).
- Pinned interpreter on every Python invocation: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.
- Focused 9-way: **250 passed in 5.97s**.
- Full repo regression: **1,837 passed in 5:48**; 0 failures; pre-existing 60 pandas fragmentation warnings unchanged. The 105 new fragmentation warnings observed during the Phase 6I-30 builder tests are cosmetic — the production builder always emits them when building 114 SMAs; they reflect a long-standing pandas dataframe-fragmentation pattern in the SMA loop, not a Phase 6I-30 regression.

---

## 11. Reference paths

- Production builder: `project/signal_library/multi_timeframe_builder.py` (Phase 6I-30: `close` persisted + `df=` injection seam).
- Sandbox builder: `project/signal_library/multi_timeframe_sandbox_builder.py` (Phase 6I-30 new; sandbox-only).
- Builder tests: `project/test_scripts/test_multi_timeframe_builder_interval_close.py` (Phase 6I-30 new; 10 tests).
- Phase 6I-29 evidence (predecessor): `project/md_library/shared/2026-05-13_PHASE_6I29_SPY_DAILY_MEMBER_ALIGNMENT_EVIDENCE.md` § 7.1 documents option (a) "signal-library builder extension" that Phase 6I-30 implements.
- Phase 6I-28 evidence (close-source fallback contract that Phase 6I-30 leaves untouched): `project/md_library/shared/2026-05-13_PHASE_6I28_SPY_CLOSE_JOIN_PATCH_READINESS_DRY_RUN.md`.
- Phase 6I-25 writer Codex amendment: `_writer_plan_payload_is_consistent(plan)` in `project/multiwindow_k_confluence_patch_writer.py` (unchanged by Phase 6I-30).
- Phase 6I-22 adapter strict contract: `project/md_library/shared/2026-05-13_PHASE_6I22_MULTIWINDOW_K_INPUT_ADAPTER.md`.
- Temp evidence directory: `C:\Users\sport\AppData\Local\Temp\phase_6i30_interval_close_sandbox_60_cell_proof\` (OUTSIDE production roots, OUTSIDE the repo; nothing in it is committed).
- CLAUDE.md § 6 — current sprint state.
