# Phase 6I-31: guarded signal-library stable promotion path + SPY staged rebuild evidence

Sprint date: **2026-05-13** (evidence captured **2026-05-14** UTC).
Branch: `phase-6i-31-signal-library-stable-promotion-path`.
Doc: this file.

Phase 6I-30 (PR #247) proved that a **sandbox** rebuild of every interval signal library with native `close` lets the multi-window K chain reach `prepared_cell_count=60`, `payload_ready=true`, `planner patch_ready=true`, and `writer dry-run planner_patch_ready=true` for SPY — but the production stable directory still carries the legacy libraries that lack `close`, so `has_true_multiwindow_k_engine_outputs=false` for SPY in production.

Phase 6I-31 builds the **guarded promotion path** that a future supervised authorization will use to move staged libraries (Phase 6I-30 sandbox output) into production stable. **This phase does NOT perform the production promotion.** It ships the read-only planner + dry-run writer with full authorization gates, focused tests, and a real staged SPY evidence pass against production roots that confirms the entire chain works end-to-end without touching production stable.

---

## 0. TL;DR

| Check | Result |
|---|---|
| Production roots mutated | **No** — 0/0/0 added/removed/changed across all 5 roots (83,026 files) |
| Production stable promotion executed | **No** (Phase 6I-31 scope is the *path*, not the *act*) |
| Promotion planner `plan_ready` | **true** for 75 staged SPY-K=1..12 libraries (15 tickers × 5 intervals) |
| Promotion writer dry-run | `write_requested=false`, `write_authorized=false`, `wrote_files=false`, `plan_ready=true`, `issue_codes=["write_not_requested"]` |
| Multi-window K adapter against staged dir | `prepared_cell_count=60`, `can_evaluate_full_60_cell_grid=true` |
| Multi-window K payload builder | `payload_ready=true`, `cell_count=60` |
| Multi-window K patch planner | `patch_ready=true`, `fields_to_add=[per_window_k_metrics, build_wide_window_alignment, multiwindow_k_engine_payload_metadata]` |
| Multi-window K patch writer dry-run | `planner_patch_ready=true`, `wrote_artifact=false`, pre/post artifact SHA equal (`db10e089…`) |
| Gap audit `has_true_multiwindow_k_engine_outputs` | **false** before AND after (no production artifact written) |
| Phase 6I-22 / 6I-25 / 6I-28 / 6I-29 / 6I-30 invariants | All preserved |

---

## 1. What the promotion planner does

`project/signal_library_stable_promotion_planner.py` (new, ~500 lines) is a read-only SCREEN. Inputs: `tickers`, `staged_dir`, `production_stable_dir`, `intervals` (default `1d / 1wk / 1mo / 3mo / 1y`). For each `(ticker, interval)` pair it:

1. Resolves staged + production filenames using the canonical
   `<TICKER>_stable_v1_0_0[_<interval>].pkl` naming pattern.
2. Loads the staged artifact via `provenance_manifest.load_verified_signal_library` (no raw `pickle.load`).
3. Schema-checks the loaded library: `dates`, `signals`, `close` (or aliases) all present AND `len(dates) == len(signals) == len(close)`.
4. Computes a SHA-256 over the staged PKL bytes.
5. Compares hashes to classify the outcome as `add`, `replace`, or `unchanged`.
6. Detects an optional `.pkl.manifest.json` sidecar next to the staged PKL.

Aggregates a `SignalLibraryStablePromotionPlan` with `plan_ready` set true iff every required staged file is present + schema-valid AND the production-stable path guard passes. Issue codes: `staged_file_missing`, `staged_file_unreadable`, `staged_file_schema_invalid`, `staged_file_load_failed`, `staged_file_provenance_mismatch`, `unexpected_production_root`.

The planner **never writes**.

## 2. What the promotion writer does

`project/signal_library_stable_promotion_writer.py` (new, ~480 lines). Default is dry-run. Mutation requires the full Phase 6I-31 authorization cascade:

1. **`--write` CLI flag** (or `write=True` kwarg).
2. **`PRJCT9_AUTOMATION_WRITE_AUTH == "phase_6h5_explicit"`** environment variable (the same two-key contract used by the Phase 6H-5 / 6I-25 writers).
3. **Re-derived planner `plan_ready=true`** from the writer's OWN call to `plan_signal_library_stable_promotion(...)`. The writer NEVER trusts an externally-supplied plan object.
4. **Production target path constrained** to a directory whose resolved tail components are `signal_library/data/stable` (the path guard).
5. **Writer-side re-validation** of every staged file: each library is re-loaded via the central provenance loader AND re-schema-checked. A stale plan whose staged file was tampered with between planning and writing is caught here.

When all five gates pass AND `write=True`, the writer runs
the staged-to-production copy as a **transactional batch**:

- Each staged PKL is copied atomically (`<filename>.tmp` then `os.replace`) onto the production target. Before each copy, the target's prior bytes are snapshotted in memory (or `None` for ADD targets that did not previously exist).
- The optional `<filename>.manifest.json` sidecar is copied the same way; its prior bytes are snapshotted too.
- If ANY copy fails mid-batch (PKL OR sidecar, for ANY library), the writer walks the touched-target log in reverse and **restores every prior target to its exact pre-run state**: newly-added targets are unlinked, replaced targets are restored from their captured prior-bytes payload via an atomic `<filename>.restore_tmp` + `os.replace` pattern. The `files_added` / `files_replaced` / `sidecars_copied` accumulators are zeroed so the result surface reports zero net writes truthfully.
- One JSONL row is appended to `--execution-log` per invocation (best-effort).
- The result surface records pre-write and post-write SHA-256 per touched production file, so an operator can independently verify which files actually changed.

The Phase 6I-31 amendment-1 PR pins this transactional contract by five rollback regression tests (§ 4 below): sidecar-copy-failure-after-PKL-copy rolls back the PKL; multi-library failure rolls back all prior copies; replaced files are restored to their exact original bytes; newly-added files are unlinked on rollback; successful runs still copy PKL + sidecar.

When any gate fails, the writer surfaces structured issue codes and refuses to mutate. **On-disk production state is byte-for-byte unchanged in both the all-gates-blocked case AND the mid-batch-copy-failure case.**

## 3. Why this is NOT the production promotion yet

Phase 6I-31 explicitly does not perform the production promotion. Reasons:

| Reason | Detail |
|---|---|
| **Operator approval still required** | The two-key authorization gate is intentionally unfulfilled in this phase: no `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` is set, no `--write` is passed. |
| **Staged libraries are sandbox-derived from local cache, not production OHLCV** | The Phase 6I-30 sandbox builder reads daily OHLCV from `cache/results/<TICKER>_precomputed_results.pkl` and resamples inside the builder. The signal pairs (Buy/Short SMA pairs) are computed from cache data with a `--end-date 2026-01-28` cutoff; production-grade values from fresh yfinance fetches will differ. A future authorized promotion should use libraries built from a **fresh** OHLCV pass against current data, not the Phase 6I-30 sandbox snapshot. |
| **Sandbox cutoff is TEF-binding** | The sandbox proof uses `--end-date 2026-01-28` because TEF's cache PKL stops at that date. A production rebuild should refresh all caches to a common cutoff first. |
| **No source refresh has run** | Cache `date_range_end=2026-05-12` is still behind `current_as_of_date=2026-05-13` (STATE 4 / cache-behind-cutoff). The Phase 6I-15 source-availability advisory still applies. |
| **Codex audit unfinished** | This PR is open for Codex review; promotion-path safety claims need to be independently audited before any production write. |

## 4. Tests added (26 new — 21 original + 5 rollback amendment)

`project/test_scripts/test_signal_library_stable_promotion.py` (new, ~870 lines) pins:

| # | Test | Pins |
|---|---|---|
| 1 | planner ready when all staged present | happy-path `plan_ready=True` |
| 2 | planner blocks missing interval | required interval absent → `plan_ready=False` + `staged_file_missing` |
| 3 | planner blocks missing close | Phase 6I-30 schema requirement (`close` mandatory) |
| 4 | planner blocks length mismatch | `len(dates) == len(signals) == len(close)` invariant |
| 5 | planner blocks unloadable artifact | corrupt PKL surfaces `staged_file_unreadable` / `staged_file_load_failed` |
| 6 | planner path guard | `production_stable_dir` NOT under `signal_library/data/stable` → `unexpected_production_root` |
| 7 | writer dry-run never mutates | `write=False` → no production file written |
| 8 | writer `--write` without env refuses | gate #1 OK, gate #2 fails |
| 9 | writer env without `--write` refuses | gate #2 OK, gate #1 fails |
| 10 | writer plan-not-ready blocks even with full auth | gate #3 fails |
| 11 | writer production-path guard blocks even with full auth | gate #4 fails |
| 12 | authorized writer copies PKL + sidecar atomically into tmp_path stable root | full cascade satisfied, atomic copy verified |
| 13 | writer-side revalidation blocks tampered staged file | gate #5 catches stale plan |
| 14 | execution log appends one JSONL row per invocation | best-effort logging contract |
| 15-16 | planner + writer have no raw `pickle.load` | B12 scope |
| 17-18 | planner + writer have no yfinance / dash / subprocess / live engine imports | strictly bounded |
| 19-21 | CLI rc=2 (missing args) and rc=0 (dry-run JSON output) | operator-surface sanity |
| 22 | sidecar-copy-failure after PKL-copy rolls back the PKL | transactional rollback: ADD-target case |
| 23 | multi-library failure rolls back all prior PKLs + sidecars | transactional rollback: cascade |
| 24 | replaced files restored to original bytes on failure | transactional rollback: REPLACE-target restoration |
| 25 | newly-added files removed on rollback | transactional rollback: ADD-target unlink |
| 26 | successful authorized promotion still copies PKL + sidecars | rollback path does NOT regress the success path |

Tests 22-26 were added in the Phase 6I-31 amendment-1 PR after Codex audit identified a per-library copy ordering issue (PKL copied → sidecar copy failure → PKL left in place violated the writer/doc contract). The amendment refactored the writer into a transactional batch: each successful copy is tracked in a touched-target log carrying the per-target prior bytes (or `None` for ADD targets); on ANY copy failure mid-batch, the touched log is walked in reverse and every entry is restored. The `files_added` / `files_replaced` / `sidecars_copied` accumulators are zeroed so the result surface reports zero net writes truthfully.

The repo-wide B12 raw-pickle static regression guard continues to pass without an allowlist entry.

---

## 5. Repo state

```
Branch: phase-6i-31-signal-library-stable-promotion-path
Main HEAD (at branch creation): 9421bfe (Phase 6I-30, PR #247)
```

---

## 6. Test results

```
Phase 6I-31 promotion tests       :  26 passed (21 original + 5 rollback)
Phase 6I-30 builder tests         :  10 passed
Adapter / diagnostic / core /
  builder / planner / writer /
  gap audit / static regression   : 240 passed
                                  -----
Focused 10-way sweep              : 276 passed in 11.04 s
                                    (21 -> 26 promotion tests added)

py_compile                        : clean across all new Python files
git diff --check                  : clean
```

Full repo regression after the amendment-1 transactional
refactor: re-run on demand; the amendment is scoped to one
production module (the promotion writer) and one test file,
so a focused 10-way sweep is the load-bearing signal.

---

## 7. Staged SPY evidence run

### 7.1 Temp evidence directory

```
C:\Users\sport\AppData\Local\Temp\phase_6i31_signal_library_promotion_path\
├── staged_libs/                 (75 PKLs + 75 .manifest.json sidecars)
├── 00_snapshot_before.json
├── 01_promotion_planner.json
├── 02_promotion_writer_dry_run.json + 02b_promotion_writer_execution_log.jsonl
├── 03_gap_audit_before.json
├── 04_diagnostic_spy.json
├── 05_payload_builder_spy.json
├── 06_patch_planner_spy.json
├── 07_patch_writer_dry_run.json + 07b_patch_writer_execution_log.jsonl
├── 08_gap_audit_after.json
├── 99_snapshot_after.json
├── snapshot_helper.py            (copied from Phase 6I-26)
└── diff_helper.py                (copied from Phase 6I-26)
```

The production-root diff in § 8 below is computed in-process
by ``diff_helper.py`` and printed to stdout; in this
evidence pass the diff JSON is captured inline in this doc
rather than persisted to a separate ``99b_snapshot_diff.json``
file. Re-running ``diff_helper.py 00_snapshot_before.json
99_snapshot_after.json`` against the same inputs reproduces
the 0/0/0 result.

Pinned interpreter: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

### 7.2 Staged library regeneration

Re-ran the Phase 6I-30 sandbox builder for SPY + the 14 K=1..12 members (`AROW, AWR, CLH, CP, EXPO, FCFS, GBCI, HCSG, JNJ, LLY, MO, PRA, PRGO, TEF`) across all 5 intervals — 75 libraries built into the staged dir; zero failures, zero yfinance fetches, end-date cutoff `2026-01-28`.

### 7.3 Promotion planner

Command:

```
"<pinned-interp>" signal_library_stable_promotion_planner.py \
  --tickers SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO,TEF \
  --staged-dir "<TEMP>/staged_libs"
# rc=0
```

| Field | Value |
|---|---|
| `plan_ready` | **true** |
| `expected_file_count` | 75 |
| `staged_files_found` | 75 |
| `staged_files_missing` | 0 |
| `libraries_to_add` | **48** (interval libraries not yet in production stable) |
| `libraries_to_replace` | **27** (existing production stable libraries that would be replaced) |
| `libraries_unchanged` | 0 |
| `issue_codes` | `[]` |

Every staged library loaded successfully via the central provenance-verified loader and passed the Phase 6I-30 schema check (`len(dates) == len(signals) == len(close)`).

### 7.4 Promotion writer dry-run (NO `--write`)

```
"<pinned-interp>" signal_library_stable_promotion_writer.py \
  --tickers SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO,TEF \
  --staged-dir "<TEMP>/staged_libs" \
  --execution-log "<TEMP>/02b_promotion_writer_execution_log.jsonl"
# rc=0
```

| Field | Value |
|---|---|
| `write_requested` | **false** |
| `write_authorized` | **false** |
| `plan_ready` | **true** |
| `wrote_files` | **false** |
| `files_added` / `files_replaced` / `sidecars_copied` | 0 / 0 / 0 |
| `issue_codes` | `[write_not_requested]` |
| `recommended_next_action` | `dry_run_review_promotion_plan` |

The promotion writer correctly refused mutation. Per-gate
status as observed in this dry-run:

- **Gate #1 (`--write`)**: **NOT authorized** — the flag was absent. This alone is sufficient to block.
- **Gate #2 (`PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`)**: **NOT authorized** — the env var was never set. This too would have blocked on its own.
- **Gate #3 (planner `plan_ready=true`)**: observed `true` (the planner found 75/75 staged files schema-valid).
- **Gate #4 (production-path guard)**: observed satisfied (default `--production-stable-dir` resolved to `project/signal_library/data/stable`).
- **Gate #5 (writer-side staged-file revalidation)**: **NOT REACHED** in this dry-run, because the authorization gates (#1, #2) failed before the writer entered the copy-and-revalidate phase. Gate #5 remains enforced for any future authorized run, and its contract is exercised by the focused tests.

The single observed `issue_codes=[write_not_requested]` reflects gate #1 alone because the writer surfaces issue codes for `--write`-absence OR env-var-absence in an `if/elif` (the `--write` absence takes precedence in the surfaced reason). The structural truth is that BOTH gates #1 AND #2 were unauthorized; either alone is sufficient to block.

### 7.5 Multi-window K chain against staged dir

The downstream multi-window K chain ran against the staged dir (NOT against production stable) and confirmed end-to-end readiness identical to Phase 6I-30:

| Stage | Verdict |
|---|---|
| Adapter diagnostic | `prepared_cell_count=60`, `skipped_cell_count=0`, `can_evaluate_full_60_cell_grid=true` |
| Payload builder | `payload_ready=true`, `cell_count=60`, `issue_codes=[]` |
| Patch planner | `patch_ready=true`, `fields_to_add=[per_window_k_metrics, build_wide_window_alignment, multiwindow_k_engine_payload_metadata]` |
| Patch writer dry-run (NO `--write`) | `write_requested=false`, `write_authorized=false`, `planner_patch_ready=true`, `wrote_artifact=false`, pre/post artifact SHA equal (`db10e089…`) |

The Phase 6I-25 writer-mutation contract is intact: gates #1 (`--write`) and #2 (`PRJCT9_AUTOMATION_WRITE_AUTH`) both block the patch writer; gates #3 / #4 / #5 are reachable.

### 7.6 Gap audit before / after

| Field | Before | After |
|---|---|---|
| `states[0].has_true_multiwindow_k_engine_outputs` | `false` | `false` |

Unchanged on both probes — no production artifact was written.

---

## 8. Production-root diff (0/0/0)

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
| `output/stackbuilder` | 5,219 | 0 | 0 | 0 |
| `signal_library/data/stable` | 72,899 | 0 | 0 | 0 |
| **TOTAL** | **83,026** | **0** | **0** | **0** |

Zero added / zero removed / zero changed across all 83,026 files in all five production roots.

---

## 9. No-production-activity confirmation

| Activity | Performed? |
|---|---|
| Writer `--write` invocation | **No** (Phase 6I-25 patch writer AND Phase 6I-31 promotion writer both dry-run) |
| `PRJCT9_AUTOMATION_WRITE_AUTH` set | **No** (not even via PowerShell-scoped `$env:`) |
| Authorized launcher script created | **No** |
| Source refresh (`signal_engine_cache_refresher`) | **No** |
| `yfinance` fetch | **No** (staged libraries built from local cache only) |
| `confluence_pipeline_runner` invocation | **No** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster / Confluence batch execution | **No** |
| Production data write | **No** (0/0/0 across 83,026 files) |
| Subprocess invocations from production modules | **No** |
| Production signal-library write to `signal_library/data/stable/` | **No** |
| Execution-log writes to `output/automation_logs/` | **No** (both writers' `--execution-log` arguments pointed at the temp evidence dir) |

The Phase 6H-5 two-key writer gate, Phase 6I-9 supervised gate, Phase 6I-10 production-root snapshot strategy, Phase 6I-22 strict full-member-coverage gate, Phase 6I-25 patch-writer contract (including `_writer_plan_payload_is_consistent`), Phase 6I-28 close-source fallback contract, Phase 6I-29 exact-date member alignment, and Phase 6I-30 interval-native close persistence are all unchanged in runtime contract.

---

## 10. Operational state carried forward

- Cache state: `cache_date_range_end=2026-05-12`; `current_as_of_date=2026-05-13`. STATE 4 / cache-behind-cutoff.
- Production `has_true_multiwindow_k_engine_outputs` — still `false` for SPY.
- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still open.
- Writer-surface provider telemetry — still pending.

---

## 11. Exact next step

A future supervised production promotion run is now the next required step. **Candidate command sequence** (NOT executed by Phase 6I-31):

```
# 1. (Optional, recommended) refresh source cache to a common cutoff:
"<pinned-interp>" signal_engine_cache_refresher.py --ticker SPY ...

# 2. Rebuild staged libraries from FRESH OHLCV (NOT the sandbox builder):
"<pinned-interp>" -m signal_library.multi_timeframe_builder \
  --ticker SPY  --intervals 1wk,1mo,3mo,1y
# (and the same for every K-row member; daily libraries are
#  protected by the builder's --allow-daily / --force-overwrite
#  guard.)

# 3. Re-run the Phase 6I-31 planner to confirm plan_ready:
"<pinned-interp>" signal_library_stable_promotion_planner.py \
  --tickers SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO,TEF \
  --staged-dir <freshly-built-staged-dir>

# 4. Re-run the Phase 6I-31 writer in dry-run mode for review:
"<pinned-interp>" signal_library_stable_promotion_writer.py \
  --tickers <same as above> \
  --staged-dir <freshly-built-staged-dir> \
  --execution-log <temp-log>

# 5. Operator review + Codex audit.

# 6. (After approval) authorized promotion run:
$env:PRJCT9_AUTOMATION_WRITE_AUTH="phase_6h5_explicit"
"<pinned-interp>" signal_library_stable_promotion_writer.py \
  --tickers <same as above> \
  --staged-dir <freshly-built-staged-dir> \
  --write \
  --execution-log <persistent-log>

# 7. Re-run the full multi-window K chain against PRODUCTION stable
#    (no --signal-library-dir override) to re-confirm patch_ready=true
#    against the now-updated production state.

# 8. Operator review + Codex audit before authorizing the Phase 6I-25
#    patch writer with --write (a separate two-key gate).
```

**This candidate is documentation only. Do not run any of steps 1, 2, 6, or 8 without explicit operator authorization in a later prompt.**

---

## 12. Validation

- `git diff --check`: clean.
- `git diff --stat`: 4 files touched at this PR — 2 new production modules (`signal_library_stable_promotion_planner.py`, `signal_library_stable_promotion_writer.py`), 1 new test file (`test_scripts/test_signal_library_stable_promotion.py`), 1 new Markdown evidence doc (this file). The amendment-1 commit modifies the writer + test files in place.
- Pinned interpreter on every Python invocation: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.
- Focused 10-way after the amendment-1 transactional refactor: **276 passed in 11.04 s** (21 → 26 promotion tests).
- Full repo regression at the original PR commit: **1,858 passed in 5:52**; 0 failures.

---

## 13. Reference paths

- Promotion planner: `project/signal_library_stable_promotion_planner.py` (new).
- Promotion writer: `project/signal_library_stable_promotion_writer.py` (new).
- Promotion tests: `project/test_scripts/test_signal_library_stable_promotion.py` (new; 21 tests).
- Phase 6I-30 evidence (predecessor sandbox proof): `project/md_library/shared/2026-05-13_PHASE_6I30_INTERVAL_NATIVE_CLOSE_SANDBOX_60_CELL_PROOF.md`.
- Phase 6I-30 production builder (the source of the `close` field that Phase 6I-31 promotes): `project/signal_library/multi_timeframe_builder.py`.
- Phase 6I-30 sandbox builder (used to regenerate the staged libraries for this evidence pass): `project/signal_library/multi_timeframe_sandbox_builder.py`.
- Phase 6I-25 writer Codex amendment: `_writer_plan_payload_is_consistent(plan)` in `project/multiwindow_k_confluence_patch_writer.py` (unchanged by Phase 6I-31).
- Temp evidence directory: `C:\Users\sport\AppData\Local\Temp\phase_6i31_signal_library_promotion_path\` (OUTSIDE production roots, OUTSIDE the repo; nothing in it is committed).
- CLAUDE.md § 6 — current sprint state.
