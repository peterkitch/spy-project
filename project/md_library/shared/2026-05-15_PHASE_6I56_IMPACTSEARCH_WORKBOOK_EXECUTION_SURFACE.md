# Phase 6I-56 — ImpactSearch workbook execution surface audit + safe runner

**Date:** 2026-05-15 (amendment-1 same day, see § 11)
**Branch:** `phase-6i-56-impactsearch-workbook-execution-surface`
**PR:** #275
**Scope:** read-only audit of `impactsearch.py` + new safe operator-facing runner module + per-ticker command manifest. **No production write, no yfinance fetch, no engine invocation.**
**PR follows Phase 6I-55a** (PR #274, ImpactSearch / primary-universe readiness planner — merged 2026-05-15 at `83ba5b5`).

---

## 1. ELI5 — what this sprint phase does for the website launch

The website board is fed by a chain of **active core scripts** in the PRJCT workflow. None of them is "legacy"; each is the live producer for one layer of the daily TrafficFlow-style ranking board:

```
OnePass / signal_library
        │  (writes signal_library/data/stable/<TICKER>_stable_v*.pkl)
        ▼
ImpactSearch                                     (output/impactsearch/<SECONDARY>_analysis.xlsx)
        │  process_primary_tickers → durable validation → export_results_to_excel
        ▼
StackBuilder                                     (output/stackbuilder/<SECONDARY>/.../combo_leaderboard.xlsx)
        │  --prefer-impact-xlsx --impact-xlsx-dir output/impactsearch --impact-xlsx-max-age-days 45
        ▼
TrafficFlow K artifacts                          (output/research_artifacts/trafficflow/<TICKER>/<seed>__K<K>.research_day.json)
        │  trafficflow_k_artifact_builder.py
        ▼
TrafficFlow MTF bridge                           (<seed>__K<K>__MTF.research_day.json)
        │  trafficflow_multitimeframe_bridge.py — 1d / 1wk / 1mo / 3mo / 1y
        ▼
Confluence MTF artifact                          (output/research_artifacts/confluence/<TICKER>/<TICKER>__MTF_CONSENSUS.research_day.json)
        │  confluence_mtf_artifact_builder.py
        ▼
Website board                                    (confluence_multiwindow_ranking_export.py + reader / renderer)
```

Phase 6I-55a (PR #274) classified the 6 StackBuilder-ready secondaries (SPY, AAPL, JNJ, WMT, HD, MCD) and found `ready_for_stackbuilder_with_impact_xlsx = 0`. The blocker is **output/impactsearch**: SPY/AAPL workbooks are stale (>45 days), and JNJ/WMT/HD/MCD are missing entirely. Until that gap closes, no StackBuilder `--prefer-impact-xlsx` retry can succeed.

Phase 6I-56 closes the **execution-surface** half of that gap: it adds a safe, testable, operator-facing **ImpactSearch workbook runner** (`impactsearch_workbook_runner.py`) so the upcoming supervised workbook batch does not require manual Dash UI clicks. It does **not** generate any workbook in this PR — the workbook write is a separate explicitly-authorized phase.

---

## 2. What this phase actually changed

Files added (4):

| Path | Purpose |
|---|---|
| `project/impactsearch_workbook_runner.py` | Dry-run-by-default operator-facing runner; lazy ImpactSearch import; double-gate authorization (`--write` + `--allow-network-fetch`); atomic XLSX write that preserves ImpactSearch's existing append/dedupe semantics (see § 11 amendment-1); per-secondary primary-library availability scan with per-row `primary_signal_libraries_found` / `primary_signal_libraries_missing` / `primary_signal_library_found_count` / `primary_signal_library_missing_count` fields. |
| `project/test_scripts/test_impactsearch_workbook_runner.py` | Focused tests: AST guards (no top-level forbidden imports / no `pickle.load(...)` / no `yf.download(...)` / no `subprocess.run(...)` call anywhere in source), ticker safety, classifier cascade, command manifest, fake-callable execute path inside `tmp_path`, production-root guards, atomic-export append/dedupe / sidecar / failure-safety tests, primary-library scan tests, eligibility integration with library scan, ENGINE_VERSION drift guard, production-state smoke that skips cleanly when caches absent. |
| `project/md_library/shared/2026-05-15_PHASE_6I56_IMPACTSEARCH_WORKBOOK_EXECUTION_SURFACE.md` | This evidence doc + code-citation table + 6-ticker verdict + downstream sequence + amendment-1 section. |
| `project/md_library/shared/2026-05-15_PHASE_6I56_IMPACTSEARCH_WORKBOOK_EXECUTION_SURFACE_EVIDENCE.json` | JSON command manifest for the 6 current tickers (one entry per secondary). |

No file under `cache/`, `output/`, `signal_library/`, or `price_cache/` was modified.

---

## 3. ImpactSearch code-path citations (read-only audit)

| Layer | File:line | Symbol | Role |
|---|---|---|---|
| Signal-library load | `impactsearch.py:1525` | `load_signal_library` | Reads `signal_library/data/stable/<TICKER>_stable_v*.pkl` through `provenance_manifest._load_verified_signal_library`; never re-fetches. |
| Lib path resolver | `impactsearch.py:1519` | `_lib_path_for` | `signal_library/data/stable/{TICKER}_stable_v{ENGINE_VERSION_DOTS_TO_UNDERSCORES}.pkl`. |
| Secondary raw fetch | `impactsearch.py:1753` | `fetch_data_raw` | **Unconditional `yf.download(period='max', interval='1d')`**. No local-cache substitute. |
| Secondary cached fetch | `impactsearch.py:2002` | `fetch_data` | Optional `CacheManager.load_from_cache` short-circuit (in-app pickle cache only — NOT `price_cache/daily/`); otherwise falls through to `yf.download`. |
| Primary processing entry | `impactsearch.py:3371` | `process_primary_tickers(secondary_ticker, primary_tickers, use_multiprocessing=False, mark_complete=True, *, rejection_out=None)` | Fetches secondary via `fetch_data_raw`; dedupes primaries via `deduplicate_tickers`; per primary calls `process_single_ticker(prim_ticker, sec_df, ...)` which loads primary's signal library. |
| Primary dedupe | `impactsearch.py:1500` | `deduplicate_tickers` | Normalize + dedupe; **does not exclude the secondary from the primary list** (verified by inspection — no `primary == secondary` comparison anywhere in `process_primary_tickers`). |
| Workbook export | `impactsearch.py:2491` | `export_results_to_excel(output_filename, metrics_list, *, rejection_out=None, validation_summary=None, per_strategy_validation=None)` | Writes XLSX + sidecar manifest (`<path>.manifest.json`). Validates `validation_summary` schema **before** writing the XLSX (fail-closed durable-tier gate). Append-and-dedupe-by-`Primary Ticker` semantics when XLSX already exists. |
| Durable validation entry | `impactsearch.py:6264` | `_prepare_impactsearch_durable_validation_for_export(secondary_ticker, primary_tickers, *, run_id=None, n_permutations=10000, n_bootstrap_samples=10000, rng_seed=None, analysis_clock=None)` | Fail-closed validation: returns `(contract, validation_summary, per_strategy_validation, sidecar_path)` for the durable tier. Falls back to a `status="failed"` artifact if anything raises mid-run; never returns without a sidecar. |
| Batch validation core | `impactsearch.py:6215` | `_run_impactsearch_batch_validation_for_export` | Drives `validate_strategy_set`, persists `validation.json`, computes SHA-256, builds manifest summary. |
| Dash callback (write path) | `impactsearch.py:4530, 4630, 4654, 4700` | `start_processing` → `process_async` | The **production write path** used by the Dash UI: `process_primary_tickers` → `_prepare_impactsearch_durable_validation_for_export` → `export_results_to_excel`. Writes to `output/impactsearch/{sec}_analysis.xlsx` (line 4696/4598). |
| Output directory creation | `impactsearch.py:4590` / `2515-2517` | `os.makedirs("output/impactsearch", exist_ok=True)` | Output dir is created by the Dash callback **before** processing starts; `export_results_to_excel` also defensively creates `os.path.dirname(output_filename)`. |
| Aggregate validation (in-memory only) | `impactsearch.py:6354` | `_run_impactsearch_aggregate_validation_in_memory` | UI-only path; NEVER writes a sidecar. Not used by the runner. |
| Dash app entry | `impactsearch.py:6408` | `if __name__ == "__main__":` | Starts `dash.app.run_server`. Importing the module does **not** start the server, but **does** pull `dash`, `dash_bootstrap_components`, `plotly`, `yfinance`, etc. into `sys.modules` — the runner therefore imports `impactsearch` **lazily**, inside `execute_workbook_run`'s default callable, never at module top level. |

**Honest gap:** there is **no existing non-Dash CLI** for ImpactSearch. The three-call chain `process_primary_tickers` → `_prepare_impactsearch_durable_validation_for_export` → `export_results_to_excel` is the existing in-module callable surface; the Dash callback is its only current driver. The Phase 6I-56 runner wraps that exact callable chain without booting Dash.

---

## 4. StackBuilder workbook-consumption citations (drift pin)

| Layer | File:line | Symbol | Role |
|---|---|---|---|
| Workbook column alias map | `stackbuilder.py:562-568` | `_RANK_COLMAP` | Maps `primary` / `ticker` / `total capture (%)` / `sharpe ratio` / `p-value` / `trigger days` / etc. to canonical column names. Pinned against `impactsearch_workbook_runner._STACKBUILDER_RANK_COLMAP_EXPECTED` by `test_expected_rank_colmap_matches_stackbuilder`. |
| Column standardizer | `stackbuilder.py:570-581` | `_standardize_rank_columns` | Required pair: `Primary Ticker` + `Total Capture (%)`. Raises `ValueError("ImpactSearch XLSX missing required columns")` otherwise. |
| XLSX fast-path consumer | `stackbuilder.py:583-700` | `try_load_rank_from_impact_xlsx(sec, dirpath, max_age_days, *, strict_manifests=False, rejection_out=None)` | Discovery filter (`base.startswith(sec_up + "_") or base.startswith(sec_clean + "_")`), freshest-by-mtime, staleness gate, `provenance_manifest._load_verified_xlsx_artifact` cascade, `_standardize_rank_columns`, drop rows missing `Primary Ticker` or `Total Capture (%)`. |
| CLI bridge flag | `stackbuilder.py:3361` | `--prefer-impact-xlsx` | Required to enter the fast-path. |
| CLI bridge dir | (companion arg) | `--impact-xlsx-dir` | Defaults to `output/impactsearch`. |
| CLI staleness gate | `stackbuilder.py:3363` | `--impact-xlsx-max-age-days` | Default **45**. Pinned by `runner.DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS = 45`. |

The runner's `classify_workbook_action` mirrors this exact cascade so that a workbook the runner classifies as `already_fresh` is the same workbook StackBuilder would accept on the fast path. The drift guards in the focused test suite catch any future divergence.

---

## 5. The current 6-ticker verdict

For the 6 Phase 6I-54b price-cache-ready tickers (SPY, AAPL, JNJ, WMT, HD, MCD), Phase 6I-55a reported:

| Ticker | Workbook | Mtime | Phase 6I-55a classification | Phase 6I-56 workbook_action | Phase 6I-56 eligibility |
|---|---|---|---|---|---|
| SPY  | present | 2026-01-09 (stale) | `needs_impactsearch_run` | `stale_needs_regeneration` | `ready_to_run_with_explicit_network` |
| AAPL | present | 2026-01-09 (stale) | `needs_impactsearch_run` | `stale_needs_regeneration` | `ready_to_run_with_explicit_network` |
| JNJ  | **MISSING** | — | `needs_impactsearch_run` | `missing_needs_generation` | `ready_to_run_with_explicit_network` |
| WMT  | **MISSING** | — | `needs_impactsearch_run` | `missing_needs_generation` | `ready_to_run_with_explicit_network` |
| HD   | **MISSING** | — | `needs_impactsearch_run` | `missing_needs_generation` | `ready_to_run_with_explicit_network` |
| MCD  | **MISSING** | — | `needs_impactsearch_run` | `missing_needs_generation` | `ready_to_run_with_explicit_network` |

`ready_for_stackbuilder_with_impact_xlsx` count remains **0**. The runner's per-ticker emit therefore produces 6 `impactsearch_network_write` command-manifest entries (one per secondary).

---

## 6. Can the runner run fully offline?

**No.** ImpactSearch's secondary fetch path is yfinance-backed unconditionally:

- `impactsearch.py:1753 fetch_data_raw` calls `yf.download(ticker, period='max', interval='1d', ...)` with no local-cache substitution.
- `impactsearch.py:2002 fetch_data` has a `CacheManager.load_from_cache` short-circuit, but `CacheManager` is the per-app pickle cache (`CACHE_ROOT/data`), **not** the project's `price_cache/daily/` CSVs. The 6 Phase 6I-54b CSVs are NOT consumed by ImpactSearch today.
- No `if local_cache_dir: ...` branch exists in `process_primary_tickers` between the dedupe step and the secondary fetch step (verified by reading `impactsearch.py:3401-3475`).

Therefore the runner:

1. **Honest classifier**: `classify_secondary_data_source` returns `secondary_source = "yfinance_required"` for every ticker today, even when `price_cache/daily/<TICKER>.csv` exists. The local-cache presence is recorded as a note (`secondary_price_cache_present_but_unused_by_impactsearch`) so a future surgical amendment to `fetch_data_raw` lands cleanly.
2. **Double-gate authorization**: actual execution requires **both** `--write` AND `--allow-network-fetch`. `--write` without `--allow-network-fetch` is refused with the `network_fetch_required_but_not_authorized` issue code; `execute_workbook_run` re-checks both gates before invoking the ImpactSearch chain.
3. **No `PRJCT9_AUTOMATION_WRITE_AUTH`**: the runner is **not** part of the Phase 6H-5 / 6I-25 / 6I-31 writer family. The two CLI flags act as the operator-facing single-key gate; the env-var two-key gate is reserved for the Confluence patch writer, the signal-library stable promotion writer, and the daily-board automation writer (per `CLAUDE.md` § 6's authorization-wording correction).

The runner could be made offline-capable by a future ImpactSearch amendment that wires `price_cache/daily/` into `fetch_data` / `fetch_data_raw`. Phase 6I-56 does **not** ship that amendment because the runner's task is to expose a safe operator surface for the workbook-export chain, not to alter ImpactSearch's data plane.

---

## 7. Test results (pinned spyproject2 interpreter)

| Suite | Production-present worktree (post-amendment-1) | Audit / cacheless worktree |
|---|---|---|
| Focused (`test_impactsearch_workbook_runner.py`) | **99 passed** | **98 passed / 1 skipped** |
| Combined Phase 6I-50/51/52/53/54a/54b/55/55a/56 regression | **265 passed** | **263 passed / 2 skipped** |

The +15 vs the pre-amendment-1 totals (84 / 250) come from the new amendment-1 tests for atomic-export append/dedupe / sidecar staging / failure safety / new-workbook path, the new primary-library scan tests (all-found / some-missing / all-missing / unsafe-rejected / dot-dash retry / no-impactsearch-import), the plan-builder library-scan integration tests, the cross-class manifest-fields test, and the ENGINE_VERSION drift guard.

`py_compile` on the new module: clean. `git diff --check`: clean (routine LF→CRLF notice only on the .md / .json files).

The cacheless skip is the production-state smoke `test_production_state_smoke_skips_when_output_impactsearch_dir_absent`, which skips cleanly in a Codex worktree where `output/impactsearch/` is not staged. The 6I-55a planner adds the other skip in cacheless mode. **These are not functional regressions** — they are honest skips against the production tree.

---

## 8. No production activity (confirmed)

`cache/results`, `cache/status`, `output/research_artifacts/confluence/`, `output/stackbuilder`, `signal_library/data/stable`, `price_cache/daily`, `output/impactsearch` — **0/0 diff** across all 7 inspected roots.

No yfinance fetch. No ImpactSearch invocation. No StackBuilder invocation. No TrafficFlow K-artifact build. No TrafficFlow MTF bridge. No Confluence MTF artifact build. No `confluence_pipeline_runner` call. No batch engines. No source refresh. No signal-library promotion. No Confluence patch writer. No website publish. No `PRJCT9_AUTOMATION_WRITE_AUTH` env-var set. No `--write`. No `--allow-network-fetch`.

---

## 9. Next phase

**Phase 6I-57: supervised ImpactSearch workbook generation for the eligible 6 tickers.** Operator runs the 6 emitted commands (or one consolidated `--secondaries SPY,AAPL,JNJ,WMT,HD,MCD` invocation) with `--write --allow-network-fetch` against the production tree. Each run lazy-imports `impactsearch`, fetches the secondary via yfinance, computes the per-primary metrics, runs durable validation, and atomically writes `output/impactsearch/<SECONDARY>_analysis.xlsx` + `.manifest.json`.

After Phase 6I-57:

1. **Re-run Phase 6I-55a readiness planner.** Expected: `ready_for_stackbuilder_with_impact_xlsx = 6` and `needs_impactsearch_run = 0` for the same 6 tickers.
2. **Phase 6I-52 amendment-3 / Phase 6I-58:** lock the StackBuilder retry command with `--prefer-impact-xlsx --impact-xlsx-dir output/impactsearch --impact-xlsx-max-age-days 45` (no `--primaries`). This is the bridge that Phase 6I-55 surfaced as missing.
3. **Phase 6I-59:** supervised StackBuilder pilot batch retry against the 6-ticker ready set.
4. **Phase 6I-60:** `confluence_pipeline_runner --write` for those 6, producing TrafficFlow K artifacts → TrafficFlow MTF bridge artifacts → Confluence MTF artifacts.
5. **Phase 6I-61:** website export / render verification against the new Confluence artifacts.
6. **Phase 6I-62+:** separately handle the remaining **19 tickers** (the Phase 6I-52 25-ticker pilot universe minus the 6 already-ready) that still need source refresh / price cache before they can enter this chain.

The Phase 6I-56 runner is the **only** new operational surface that needs to land before this sequence becomes turn-key. Every layer downstream of ImpactSearch already has a Phase 6I-* surface; the ImpactSearch workbook layer was the last hand-clicked gap.

---

## 10. Authorization summary for the runner's command manifest

| `authorization_class` | When emitted | `--write` | `--allow-network-fetch` | `requires_separate_operator_authorization` |
|---|---|---|---|---|
| `read_only` | Reserved for future offline-capable runs (no eligible ticker today). | ❌ | ❌ | False |
| `impactsearch_workbook_write` | Reserved for future offline-capable runs (no eligible ticker today; ImpactSearch's secondary fetch is yfinance-only). | ✅ | ❌ | True |
| `impactsearch_network_write` | Current path for the 6 tickers — workbook generation requires both flags. | ✅ | ✅ | True |
| `manual_review` | Unsafe ticker, empty primary universe, manifest mismatch, or unrecoverable load error. | n/a | n/a | True |

All non-`read_only` entries set `requires_separate_operator_authorization=True` so the supervised batch operator must re-confirm authorization per ticker (or per consolidated invocation).

The full per-ticker manifest is in `2026-05-15_PHASE_6I56_IMPACTSEARCH_WORKBOOK_EXECUTION_SURFACE_EVIDENCE.json`.

---

## 11. Amendment-1 — preserve ImpactSearch workbook semantics + surface missing primary libraries

Codex audit on PR #275 flagged two merge-blocking issues. Both are resolved in amendment-1 (same-day, docs + code).

### Amendment-1 fix #1 — atomic export now preserves ImpactSearch's existing append/dedupe semantics

**The bug.** PR #275 pre-amendment-1's `_atomic_export_workbook` wrote to a fresh `<base>.runner_partial.xlsx` and then `os.replace()`d over the canonical. Because `export_results_to_excel` at `impactsearch.py:2631-2667` reads the existing canonical workbook to append-and-dedupe new rows, exporting to a fresh partial meant ImpactSearch never saw the existing rows — `os.replace` then silently dropped them. This contradicted the doc's claim that the runner preserves ImpactSearch's exact write semantics.

**The fix.** `_atomic_export_workbook` (Phase 6I-56 amendment-1) now:

  1. If the canonical workbook exists, copies it to the partial path **before** calling `export_results_to_excel`.
  2. If the canonical sidecar (`<base>.xlsx.manifest.json`) exists, copies it to the partial sidecar path **before** the export, so the preexisting-manifest inspector at `impactsearch.py:2629 _inspect_preexisting_xlsx_manifest` observes the same prior state it would have observed on a direct write.
  3. Calls `export_results_to_excel(partial_xlsx, ...)`. With the prior workbook + sidecar staged into the partial paths, ImpactSearch's existing read-existing → append → dedupe-by-`Primary Ticker` (with `Resolved/Fetched` fallback) → sort → write logic at `impactsearch.py:2631-2667` runs verbatim.
  4. `os.replace`s the partial workbook onto the canonical name. `os.replace`s the partial sidecar onto the canonical sidecar if the export wrote one.
  5. On any failure during steps 1-4, removes the partial workbook + partial sidecar in a `finally` block. The canonical workbook + canonical sidecar remain byte-identical to their pre-call state because the canonical names are only written by `os.replace` after a successful export.

This restores the exact ImpactSearch write semantics under atomic replacement. The runner now **preserves ImpactSearch's existing append/dedupe semantics** — i.e. re-running the runner against an existing canonical workbook adds the new rows (or updates existing ones by `Primary Ticker`), and `os.replace` swaps the merged result in atomically rather than overwriting a fresh sheet.

**Tests pinning this behavior** (in `test_impactsearch_workbook_runner.py`):

  - `test_atomic_export_preserves_existing_workbook_for_append_dedupe` — export observes the canonical bytes at the partial path before writing.
  - `test_atomic_export_copies_existing_sidecar_to_partial` — export observes the canonical sidecar bytes at the partial sidecar path before writing.
  - `test_atomic_export_failure_leaves_canonical_byte_identical` — a synthetic export failure mid-write leaves both the canonical workbook AND canonical sidecar byte-identical to their pre-call state; partials are cleaned up.
  - `test_atomic_export_new_workbook_path_still_works` — when the canonical does NOT pre-exist, the partial path is empty before export and the canonical is created cleanly.

### Amendment-1 fix #2 — primary signal-library availability is now surfaced

**The bug.** PR #275 pre-amendment-1's `build_impactsearch_workbook_run_plan` reported `effective_primary_universe` but never scanned `signal_library/data/stable/` for the libraries those primaries require. A row could be classified `ready_to_run_with_explicit_network` even when none of the primary signal libraries existed; the supervised batch would then fail at run time.

**The fix.** Amendment-1 adds:

  - **`scan_primary_signal_libraries(primaries, *, signal_lib_dir, existence_checker=None)`** — read-only scan. The default existence checker mirrors `impactsearch._lib_path_for` (`impactsearch.py:1519-1523`): looks for `signal_library/data/stable/{TICKER}_stable_v1_0_0.pkl`, with the dot→dash retry from `impactsearch.load_signal_library` (`impactsearch.py:1538-1544`) for tickers containing `.`. `IMPACTSEARCH_ENGINE_VERSION = "1.0.0"` is pinned by the new drift guard `test_engine_version_matches_impactsearch_module` (AST-parses `impactsearch.py` without importing it).
  - **Per-row fields** on each `per_ticker` entry: `primary_signal_libraries_found` (list), `primary_signal_libraries_missing` (list), `primary_signal_library_found_count` (int), `primary_signal_library_missing_count` (int).
  - **Eligibility behavior** (added to `classify_eligibility`):
    - `primary_signal_library_found_count == 0` → eligibility **BLOCKED**, issue code `primary_signal_library_missing`, manifest entry's `authorization_class` becomes `manual_review` with `argv=null`.
    - `0 < found_count < universe_size` → eligibility unchanged (still `ready_to_run_with_explicit_network` if other gates pass), but `primary_signal_library_missing` is appended to the row's `issue_codes` as a **warning** and propagated through to the manifest entry's `issue_codes`.
    - Counts and lists appear in both the per-row fields and the manifest entries so the supervised batch operator can see exactly which primaries would silently drop out.

**Tests pinning this behavior** (in `test_impactsearch_workbook_runner.py`):

  - `test_scan_primary_signal_libraries_all_found` / `_some_missing` / `_all_missing` — count + list accuracy.
  - `test_scan_primary_signal_libraries_unsafe_rejected_no_filesystem` — unsafe primaries never reach the existence checker.
  - `test_scan_primary_signal_libraries_dot_dash_variant` — `BRK.B` resolves to `BRK-B_stable_v1_0_0.pkl` per the ImpactSearch retry cascade.
  - `test_scan_primary_signal_libraries_does_not_import_impactsearch` — runtime guard: the scan does not pull `impactsearch` / `yfinance` / `dash` / `subprocess` into `sys.modules`.
  - `test_engine_version_matches_impactsearch_module` — AST-level drift guard: pins `runner.IMPACTSEARCH_ENGINE_VERSION` against the literal `impactsearch.ENGINE_VERSION` assignment.
  - `test_build_plan_carries_library_scan_fields` — per-row fields are present and accurate.
  - `test_build_plan_zero_libraries_blocks_to_manual_review` — zero libraries → BLOCKED + manual_review with null argv.
  - `test_build_plan_some_libraries_missing_warns_but_eligible` — partial coverage → still eligible but warning issue + counts surfaced in the manifest entry.

### Amendment-1 doc updates

  - Header date marker now reads `(amendment-1 same day, see § 11)`.
  - Line-count approximations removed from § 2's table (Codex flagged "~1,180" / "~970" as imprecise; the file structure now lists purposes only).
  - § 7's test totals updated to the post-amendment-1 numbers (focused 98 / cacheless 97+1; combined 264 / cacheless 262+2).
  - This new § 11 documents both fixes.

### No production activity (re-confirmed for amendment-1)

`cache/results`, `cache/status`, `output/research_artifacts/confluence/`, `output/stackbuilder`, `signal_library/data/stable`, `price_cache/daily`, `output/impactsearch` — **0/0 diff** across all 7 inspected roots. No yfinance fetch. No ImpactSearch invocation. No StackBuilder / TrafficFlow / Confluence pipeline runner / source refresh / promotion / Confluence patch writer / website publish. No `PRJCT9_AUTOMATION_WRITE_AUTH`. No `--write`. No `--allow-network-fetch`.
