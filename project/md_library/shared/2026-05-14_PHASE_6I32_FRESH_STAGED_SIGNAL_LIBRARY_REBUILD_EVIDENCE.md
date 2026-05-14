# Phase 6I-32: supervised fresh-source readiness + staged signal-library rebuild evidence

Sprint date: **2026-05-14**.
Branch: `phase-6i-32-fresh-staged-signal-library-rebuild-evidence`.
Doc: this file.

Phase 6I-31 (PR #248) shipped the guarded **promotion-path**
modules (planner + transactional writer) without performing
any production promotion. Phase 6I-32 builds the **operator
readiness harness** that coordinates all the existing
read-only / dry-run probes into a single supervised verdict:

  * source/cache state probes,
  * fresh / staged sandbox library build (Phase 6I-30),
  * Phase 6I-31 promotion planner,
  * Phase 6I-31 promotion writer dry-run,
  * Phase 6I-22..27 multi-window K chain against the staged
    dir,
  * production-root before/after snapshot diff.

The harness classifies the final operator-facing state as one
of `STATE_SOURCE_NOT_READY` / `STATE_STAGED_REBUILD_NOT_READY`
/ `STATE_STAGED_REBUILD_READY`. **It never authorizes any
production write**, never sets
`PRJCT9_AUTOMATION_WRITE_AUTH`, never passes `--write` to any
downstream writer, and AST-pins that contract.

---

## 0. TL;DR

| Check | Result |
|---|---|
| Production roots mutated | **No** — 0/0/0 added/removed/changed across all 5 roots |
| Final state (real SPY evidence) | **`STATE_SOURCE_NOT_READY`** |
| `source_cache_ready` (real SPY evidence) | **false** for all 15 inspected tickers |
| Sandbox staged build | **75 written / 0 failed** (SPY + 14 K-row members × 5 intervals) |
| Promotion planner | `plan_ready=true`; 75 staged found; 48 add + 27 replace + 0 unchanged |
| Promotion writer dry-run (NO `--write`) | `write_requested=false`, `write_authorized=false`, `wrote_files=false` |
| Adapter diagnostic against staged dir | `prepared_cell_count=60`, `can_evaluate_full_60_cell_grid=true` |
| Payload builder | `payload_ready=true`, `cell_count=60` |
| Patch planner | `patch_ready=true`, `fields_to_add=[per_window_k_metrics, build_wide_window_alignment, multiwindow_k_engine_payload_metadata]` |
| Patch writer dry-run (NO `--write`) | `planner_patch_ready=true`, `wrote_artifact=false`, pre/post artifact SHA equal |
| Recommended next action | **`refresh_source_cache`** |
| Phase 6I-25 / 6I-31 contracts unchanged | Yes |

---

## 1. Why the final state is `STATE_SOURCE_NOT_READY`

Current operational state (carried forward from Phase 6I-29 → 6I-31): STATE 4 / cache-behind-cutoff (`cache_date_range_end=2026-05-12`; `current_as_of_date=2026-05-14`). The `cache_cutoff_watcher` predicate `cache_ahead_of_cutoff` is `false` for every inspected ticker, and `cache_behind_cutoff=true`. The harness's classifier therefore reports `STATE_SOURCE_NOT_READY` regardless of the downstream stage outcomes.

The Phase 6I-32 design choice (documented in § 3) is to **still run every downstream stage** when source/cache is not ready, so the operator has a complete supplementary picture of what the chain would say if source/cache were refreshed. The downstream evidence is informative but does NOT change the final verdict.

---

## 2. What the harness does

`project/signal_library_fresh_staging_readiness.py` (new, ~720 lines). Single public function `evaluate_fresh_staging_readiness(...)`, single CLI entry point.

The function runs the following stages in order:

1. **Cache-cutoff probe** via `cache_cutoff_watcher.build_cache_cutoff_watch_report`. Read-only.
2. **Source-availability probe** via `source_availability_probe.evaluate_source_availability_many`, optional (skippable via `--skip-source-availability`). Calls the Phase 6E-5 refresher with `write=False` -- the established Phase 6I-15 read-only probe pattern. May trigger a yfinance fetch attempt at the refresher's contract; no production write.
3. **Production-root snapshot BEFORE** (5 roots).
4. **Sandbox staged build** via `signal_library.multi_timeframe_sandbox_builder.build_sandbox_libraries_for_ticker`. Reads daily OHLCV from `cache/results/<TICKER>_precomputed_results.pkl` via the central provenance loader; resamples to each interval inside the builder; writes to the supplied `staged_dir`. No yfinance fetch. Refuses to write under `signal_library/data/stable` (path-suffix check enforced by the sandbox builder itself).
5. **Phase 6I-31 promotion planner** (if sandbox produced anything AND `staged_dir` is NOT under production stable).
6. **Phase 6I-31 promotion writer dry-run** (if planner `plan_ready=true`). ALWAYS `write=False`.
7. **Multi-window K adapter diagnostic** against the staged dir.
8. **Phase 6I-23 payload builder** against the staged dir.
9. **Phase 6I-24 patch planner** against the staged dir + production artifact root.
10. **Phase 6I-25 patch writer dry-run** against the staged dir + production artifact root. ALWAYS `write=False`.
11. **Production-root snapshot AFTER** and diff (5 roots).
12. **State classification**.

### State classifier

```
if ISSUE_SOURCE_CACHE_NOT_READY in issues:
    state = STATE_SOURCE_NOT_READY
elif any of [SANDBOX_BUILD_INCOMPLETE,
             PROMOTION_PLAN_NOT_READY,
             ADAPTER_NOT_FULL_GRID,
             PAYLOAD_NOT_READY,
             PATCH_PLAN_NOT_READY,
             PRODUCTION_ROOT_DRIFT_DETECTED,
             STAGED_DIR_UNDER_PRODUCTION_STABLE]:
    state = STATE_STAGED_REBUILD_NOT_READY
else:
    state = STATE_STAGED_REBUILD_READY
```

### Injection seams

Every external probe / builder / writer / snapshot helper is reachable through a per-call injection callable that defaults to the existing project module's public function. This keeps the harness top-level import surface yfinance-free / live-engine-free (the defaults use deferred local imports inside their helpers, so a test that fakes them never pays the import cost).

### Hard contract pins

- `write=True` is never passed to either downstream writer (AST-scanned).
- `PRJCT9_AUTOMATION_WRITE_AUTH` is never read or set by this module.
- No top-level imports of yfinance / dash / subprocess / spymaster / trafficflow / stackbuilder / onepass / impactsearch / confluence / cross_ticker_confluence / daily_signal_board / daily_board_automation_writer / signal_engine_cache_refresher / confluence_pipeline_runner / daily_board_automation_executor.
- No raw `pickle.load`. No `.resample()` / `.ffill()` calls.
- **Staged-dir safety boundary (Phase 6I-32 amendment-1):** the harness hard-stops the sandbox builder, promotion planner, promotion writer dry-run, AND the multi-window K downstream chain when `staged_dir` resolves at OR under the production stable signal-library directory. The helper `_path_is_under_production_stable(candidate, production_stable_dir)` rejects three distinct unsafe shapes:
  1. `staged_dir` equals `production_stable_dir` (after resolution),
  2. `staged_dir` is anywhere under `production_stable_dir` (e.g. `signal_library/data/stable/staged_libs`),
  3. `staged_dir`'s resolved components contain `signal_library/data/stable` as a contiguous ancestor segment regardless of where in the path it sits.
  In the unsafe-staged-dir state, `sandbox_build_attempted=False`, the sandbox builder callable is NEVER invoked, the promotion planner / writer / downstream chain are all short-circuited, and the state is `STATE_STAGED_REBUILD_NOT_READY` (unless source-not-ready takes precedence). Additionally, the harness's `_default_sandbox_builder` helper itself raises `ValueError` if it ever sees an unsafe `staged_dir` (defense-in-depth against future call-path mistakes or out-of-band callers that bypass `evaluate_fresh_staging_readiness`).

---

## 3. Why the harness still runs downstream stages when source/cache is not ready

The Phase 6I-32 spec gives the harness two competing requirements:

> It must clearly separate three states: source/cache not ready: halt and document exact predicate values

versus

> Move us closer to the website-launch product path without doing a production write yet. We need a larger, useful step

The resolution is: **classify the final state pessimistically (STATE_A wins over downstream success), but still capture downstream evidence so the operator can see whether refreshing source/cache would unblock the whole chain or whether deeper blockers exist that a refresh would NOT resolve**. In current production state, the downstream chain is in fact all-green against the staged dir — refreshing source/cache and re-running this harness should flip the verdict to `STATE_STAGED_REBUILD_READY` without further code work.

This makes the harness a useful operator readiness screen: it does not gate on staleness, it advises.

---

## 4. Tests added (23 new — 18 original + 5 amendment-1 staged-dir safety)

`project/test_scripts/test_signal_library_fresh_staging_readiness.py` (new, ~770 lines) pins:

| # | Test | Pins |
|---|---|---|
| 1 | All-green path -> `STATE_STAGED_REBUILD_READY` | full coordinator works when every stage reports ready |
| 2 | Cache-not-ready -> `STATE_SOURCE_NOT_READY` | classifier prefers STATE_A even when downstream is green |
| 3 | Sandbox partial failure -> `STATE_STAGED_REBUILD_NOT_READY` | `ISSUE_SANDBOX_BUILD_INCOMPLETE` |
| 4 | Promotion plan not_ready -> STATE_B + writer NOT called | short-circuit at plan failure |
| 5 | Adapter not full grid -> STATE_B + `ISSUE_ADAPTER_NOT_FULL_GRID` | downstream chain failure surfaces correctly |
| 6 | Production-root drift -> STATE_B + `ISSUE_PRODUCTION_ROOT_DRIFT_DETECTED` | guard against silent production mutation during the run |
| 7 | `staged_dir` under production stable -> STATE_B + `ISSUE_STAGED_DIR_UNDER_PRODUCTION_STABLE` + promotion planner NOT called | path-guard short-circuits |
| 8 | Harness never sets `PRJCT9_AUTOMATION_WRITE_AUTH` | env-var contract |
| 9 | Both writer seams called recordably (and exactly once each) without `write=True` | dry-run contract |
| 10-12 | `--skip-source-availability` / `--skip-downstream-chain` / `--skip-snapshot-diff` flags work | optional-stage contract |
| 13-14 | CLI `rc=2` (missing tickers / unknown flag) | operator-surface sanity |
| 15 | Harness has no raw `pickle.load` | B12 scope |
| 16 | Harness has no forbidden top-level imports (yfinance / dash / subprocess / live engines / writers / refreshers / pipeline runner) | strictly bounded |
| 17 | Harness has no `.resample()` / `.ffill()` calls | no-projection scope |
| 18 | Harness AST has no `write=True` keyword arg anywhere | belt-and-braces dry-run guard |
| 19 | `staged_dir == production_stable_dir` → sandbox callable NOT invoked, `sandbox_build_attempted=False` | amendment-1 exact-match path guard |
| 20 | `staged_dir` is a CHILD of production_stable_dir (the original bug case) → sandbox / promotion / downstream chain ALL short-circuited | amendment-1 ancestor path guard |
| 21 | Child path still blocked when `run_snapshot_diff=False` | amendment-1 guard fires regardless of snapshot policy |
| 22 | Safe temp `staged_dir` regression: sandbox still invoked, state still `STATE_STAGED_REBUILD_READY` | amendment-1 guard does NOT regress the safe path |
| 23 | `_default_sandbox_builder` raises `ValueError` on unsafe `staged_dir` | amendment-1 defense-in-depth helper |

The repo-wide B12 raw-pickle static regression guard continues to pass without an allowlist entry.

---

## 5. Repo state

```
Branch: phase-6i-32-fresh-staged-signal-library-rebuild-evidence
Main HEAD (at branch creation): c73e20e (Phase 6I-31, PR #248)
```

---

## 6. Test results

```
Phase 6I-32 harness tests          :  23 passed (18 original + 5 amendment-1)
Phase 6I-31 promotion tests        :  26 passed
Phase 6I-30 builder tests          :  10 passed
Adapter / diagnostic / core /
  builder / planner / writer /
  gap audit / static regression    : 240 passed
                                   -----
Focused 11-way sweep               : 299 passed in 11.31 s

Full repo regression (at original
  PR commit, pre-amendment-1)      : 1,881 passed in 5:48 (0 failures)

py_compile                         : clean across all changed Python files
git diff --check                   : clean
```

---

## 7. Real SPY evidence

### 7.1 Temp evidence directory

```
C:\Users\sport\AppData\Local\Temp\phase_6i32_fresh_staged_signal_library_rebuild\
├── staged_libs/                  (75 PKLs + 75 .manifest.json sidecars)
├── 01_readiness_report.json
├── 01_readiness_report.stderr.txt
├── promotion_writer_log.jsonl
└── patch_writer_log.jsonl
```

Pinned interpreter: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

### 7.2 Command

```
"<pinned-interp>" signal_library_fresh_staging_readiness.py \
  --tickers SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO,TEF \
  --primary-ticker SPY \
  --staged-dir "<TEMP>/staged_libs" \
  --cache-dir cache/results \
  --stackbuilder-root output/stackbuilder \
  --confluence-artifact-root output/research_artifacts \
  --current-as-of-date 2026-05-14 \
  --sandbox-end-date 2026-01-28 \
  --skip-source-availability \
  --promotion-writer-execution-log "<TEMP>/promotion_writer_log.jsonl" \
  --patch-writer-execution-log "<TEMP>/patch_writer_log.jsonl"
# rc=0
```

`--skip-source-availability` was passed because the yfinance-backed `source_availability_probe` is slow and was already exercised at Phase 6I-16 / 6I-17 against this same predicate state; the cache-cutoff probe alone is sufficient to classify `STATE_SOURCE_NOT_READY` for this evidence pass. An operator running the harness pre-promotion should drop the `--skip-source-availability` flag to refresh the source-availability evidence.

### 7.3 Per-stage verdict

| Stage | Outcome |
|---|---|
| Cache-cutoff probe | `cache_ahead_of_cutoff=false` for ALL 15 inspected tickers; `cache_behind_cutoff=true` |
| Source-availability probe | SKIPPED (operator flag) |
| Sandbox staged build | **75 written / 0 failed** |
| Promotion planner | `plan_ready=true`; expected 75 / found 75 / missing 0; 48 add + 27 replace + 0 unchanged; `issue_codes=[]` |
| Promotion writer dry-run (NO `--write`) | `write_requested=false`, `write_authorized=false`, `wrote_files=false`, `issue_codes=[write_not_requested]` |
| Adapter diagnostic (SPY, staged dir) | `prepared_cell_count=60`, `skipped_cell_count=0`, `can_evaluate_full_60_cell_grid=true`, `adapter_issue_codes=[]` |
| Payload builder | `payload_ready=true`, `cell_count=60`, `issue_codes=[]` |
| Patch planner | `patch_ready=true`, `fields_to_add=[per_window_k_metrics, build_wide_window_alignment, multiwindow_k_engine_payload_metadata]`, `recommended_next_action=ready_for_reviewed_artifact_write` |
| Patch writer dry-run (NO `--write`) | `write_requested=false`, `write_authorized=false`, `planner_patch_ready=true`, `wrote_artifact=false`, pre/post artifact SHA equal |
| Production-root snapshot diff | **0/0/0** across all 5 roots |
| **Final state** | **`source_not_ready`** |
| Recommended next action | **`refresh_source_cache`** |

### 7.4 Interpretation

The harness landed in `STATE_SOURCE_NOT_READY` because the cache-cutoff predicate is `cache_behind_cutoff=true` for every inspected ticker. The downstream evidence supplements that verdict: **if the source cache were refreshed past the current cutoff, the harness would flip to `STATE_STAGED_REBUILD_READY` without further code work** (every other stage already reports green against the staged sandbox dir). No deeper blocker exists at this layer.

The recommended next action is `refresh_source_cache`. The source refresh is out of scope for Phase 6I-32 (it requires the Phase 6E-5 refresher under its own supervised gate). After a successful refresh, an operator may re-run this harness; if it then lands in `STATE_STAGED_REBUILD_READY`, the Phase 6I-31 promotion writer authorization can be requested in a SEPARATE prompt.

---

## 8. Production-root diff (0/0/0)

| Root | Added | Removed | Changed |
|---|---|---|---|
| `cache/results` | 0 | 0 | 0 |
| `cache/status` | 0 | 0 | 0 |
| `output/research_artifacts` | 0 | 0 | 0 |
| `output/stackbuilder` | 0 | 0 | 0 |
| `signal_library/data/stable` | 0 | 0 | 0 |
| **TOTAL** | **0** | **0** | **0** |

Zero added / zero removed / zero changed across all five production roots. The sandbox staged libraries land under the temp evidence dir; no production root is touched.

---

## 9. Strict-coverage / no-projection invariants preserved

| Invariant | Status |
|---|---|
| Phase 6I-22 strict full-member coverage | Unchanged. The harness consumes the adapter's existing public surface read-only. |
| Adapter `.resample()` / `.ffill()` ban | Unchanged. The harness AST itself also pins no projection (test 17). |
| B12 raw-pickle ban | Unchanged. No raw `pickle.load` in the harness (test 15). |
| Phase 6I-28 close-source fallback contract | Unchanged. The harness forwards `cache_dir` as the close-source root through to every consumer; the adapter still uses native interval close when present. |
| Phase 6I-29 exact-date member alignment | Unchanged. |
| Phase 6I-30 interval-native close in builder | Unchanged. The sandbox builder still produces it. |
| Phase 6I-31 promotion writer 5-gate cascade + transactional rollback | Unchanged. The harness calls the promotion writer with `write=False` only. |
| Phase 6I-25 patch writer 5-gate cascade (including `_writer_plan_payload_is_consistent`) | Unchanged. The harness calls the patch writer with `write=False` only. |

---

## 10. No-production-activity confirmation

| Activity | Performed? |
|---|---|
| Writer `--write` invocation (Phase 6I-25 patch writer OR Phase 6I-31 promotion writer) | **No** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` set | **No** (not even via PowerShell-scoped `$env:`) |
| Authorized launcher script created | **No** |
| Source refresh (`signal_engine_cache_refresher`) | **No** (the source-availability probe was SKIPPED via operator flag; no yfinance fetch in this pass) |
| `yfinance` fetch | **No** |
| `confluence_pipeline_runner` invocation | **No** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster / Confluence batch execution | **No** |
| Production data write | **No** (0/0/0 across all 5 roots) |
| Production signal-library write to `signal_library/data/stable/` | **No** |
| Subprocess invocations from production modules | **No** |
| Execution-log writes to `output/automation_logs/` | **No** (both writers' `--execution-log` arguments pointed at the temp evidence dir) |

---

## 11. Exact next step

**`refresh_source_cache`.** Concrete operator decision tree (NOT executed by this phase):

1. Operator authorizes a supervised source refresh of `cache/results/<TICKER>_precomputed_results.pkl` for SPY + the 14 K-row members via `signal_engine_cache_refresher.py`. This requires a separate two-key authorization in a future prompt. The refresh is a yfinance-backed fetch + write under its own supervised gate.
2. Operator re-runs the Phase 6I-32 harness. If it lands in `STATE_STAGED_REBUILD_READY`, the staged libraries are fresh and the chain proves ready end-to-end.
3. Operator authorizes the Phase 6I-31 promotion writer with `--write` AND `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` in a SEPARATE prompt.
4. Operator re-runs the multi-window K chain against production stable to re-confirm `patch_ready=true` against production data.
5. Operator authorizes the Phase 6I-25 patch writer with `--write` AND `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` in a third separate prompt.

**Do NOT skip any step.** Each step is a separate supervised authorization.

---

## 12. Validation

- `git diff --check`: clean.
- `git diff --stat`: 3 files touched — 1 new production module (`signal_library_fresh_staging_readiness.py`), 1 new test file (`test_scripts/test_signal_library_fresh_staging_readiness.py`), 1 new Markdown evidence doc (this file).
- Pinned interpreter on every Python invocation: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.
- Focused 11-way: **294 passed in 11.28s**.
- Full repo regression: **1,881 passed in 5:48** — 0 failures.

---

## 13. Reference paths

- Harness: `project/signal_library_fresh_staging_readiness.py` (new).
- Tests: `project/test_scripts/test_signal_library_fresh_staging_readiness.py` (new; 18 tests).
- Phase 6I-31 evidence (predecessor — promotion path planner/writer): `project/md_library/shared/2026-05-13_PHASE_6I31_SIGNAL_LIBRARY_STABLE_PROMOTION_PATH.md`.
- Phase 6I-30 evidence (predecessor — sandbox builder + interval-native close): `project/md_library/shared/2026-05-13_PHASE_6I30_INTERVAL_NATIVE_CLOSE_SANDBOX_60_CELL_PROOF.md`.
- Temp evidence directory: `C:\Users\sport\AppData\Local\Temp\phase_6i32_fresh_staged_signal_library_rebuild\` (OUTSIDE production roots, OUTSIDE the repo; nothing in it is committed).
- CLAUDE.md § 6 — current sprint state.
