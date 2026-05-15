# Phase 6I-56 — ImpactSearch workbook execution surface audit + safe runner

**Date:** 2026-05-15
**Branch:** `phase-6i-56-impactsearch-workbook-execution-surface`
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

| Path | Lines | Purpose |
|---|---|---|
| `project/impactsearch_workbook_runner.py` | ~1,180 | Dry-run-by-default operator-facing runner; lazy ImpactSearch import; double-gate authorization (`--write` + `--allow-network-fetch`); atomic XLSX write. |
| `project/test_scripts/test_impactsearch_workbook_runner.py` | ~970 | Focused tests (AST guards, ticker safety, classifier cascade, command manifest, fake-callable execute path inside `tmp_path`, production-root guards, production-state smoke that skips when caches absent). |
| `project/md_library/shared/2026-05-15_PHASE_6I56_IMPACTSEARCH_WORKBOOK_EXECUTION_SURFACE.md` | this file | Evidence doc + code-citation table + 6-ticker verdict + downstream sequence. |
| `project/md_library/shared/2026-05-15_PHASE_6I56_IMPACTSEARCH_WORKBOOK_EXECUTION_SURFACE_EVIDENCE.json` | ~250 | JSON command manifest for the 6 current tickers. |

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

| Suite | Production-present worktree | Audit / cacheless worktree |
|---|---|---|
| Focused (`test_impactsearch_workbook_runner.py`) | **84 passed** in 1.55s | **83 passed / 1 skipped** |
| Combined Phase 6I-50/51/52/53/54a/54b/55/55a/56 regression | **250 passed** in 2.87s | **248 passed / 2 skipped** |

`py_compile` on the new module: clean. `git diff --check`: clean (routine LF→CRLF notice only on the .md / .json files).

The cacheless skip is the new production-state smoke `test_production_state_smoke_skips_when_output_impactsearch_dir_absent`, which skips cleanly in a Codex worktree where `output/impactsearch/` is not staged. The 6I-55a planner adds the other skip in cacheless mode. **These are not functional regressions** — they are honest skips against the production tree.

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
