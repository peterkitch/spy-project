# Phase 6I-69 - StackBuilder Runner Execution Surface

**Scope:** read-only Phase A scoping for a future `stackbuilder_workbook_runner.py`. No code modified, no engines run, no commit on `main`.
**Candidate path:** `md_library/shared/2026-05-20_PHASE_6I_69_STACKBUILDER_RUNNER_EXECUTION_SURFACE.md`
**Lineage:**
- OnePass Phase A: `md_library/shared/2026-05-20_PHASE_6I_63_ONEPASS_RUNNER_EXECUTION_SURFACE.md`
- ImpactSearch runner: `impactsearch_workbook_runner.py`
- OnePass runner: `onepass_workbook_runner.py`
- Current StackBuilder engine: `stackbuilder.py` (3,426 lines as of audit)

> Actual local paths live in `CLAUDE.md` and are intentionally not committed to tracked docs. This doc uses `<PROJECT_ROOT>` and `<PINNED_INTERPRETER>` placeholders.

---

## 1. Scope and Non-Goals

**In scope (Phase A, this doc):**
- Static audit of `stackbuilder.py` end-to-end.
- Mapping of current execution surface, mutable state, ContextVars, env vars, and artifacts.
- Proposed future headless runner contract.
- Locked v1 defaults for Phase B.
- Canonical build selection policy.
- Benchmark sweep methodology (Phase D).
- Phase map A → F.

**Out of scope (deferred to later phases):**
- Implementing `stackbuilder_workbook_runner.py` (Phase B).
- Running StackBuilder, ImpactSearch, OnePass, or any other engine.
- Running the benchmark sweep (Phase D).
- Editing `stackbuilder.py` or any engine/runtime file.
- Authorizing canonical writes (Phase E).
- Downstream TrafficFlow / Confluence / website integration (Phase F).

---

## 2. Source Files Inspected

Read-only static inspection only — no module was imported:

- `stackbuilder.py` (full file, 3,427 lines)
- `provenance_manifest.py` (central verified-loader / manifest-cache surface)
- `trafficflow.py` (downstream Spymaster PKL consumer check only)
- `onepass_workbook_runner.py` (existence + CLI surface for lineage parity)
- `impactsearch_workbook_runner.py` (existence for lineage parity)
- `md_library/shared/2026-05-20_PHASE_6I_63_ONEPASS_RUNNER_EXECUTION_SURFACE.md` (Phase A pattern)
- `CLAUDE.md` (pinned interpreter + operational rules; private path tokens not copied)

Every line reference below is repo-relative in the form `stackbuilder.py:<line>`.

---

## 3. Current StackBuilder Execution Surface

### 3.1 Module imports and top-level side effects

| Layer | File:line | Behavior |
|---|---|---|
| Stdlib + numpy/pandas/scipy imports | `stackbuilder.py:6-21` | Standard. `pd.set_option('future.no_silent_downcasting', True)` at module-import time (line 19). |
| tqdm optional | `stackbuilder.py:22-25` | Falls back to `None` if not installed. |
| Canonical scoring | `stackbuilder.py:27-31` | Imports `combine_consensus_signals`, `score_captures`, `metrics_to_legacy_dict` from `canonical_scoring`. |
| Provenance manifest | `stackbuilder.py:32-40` | Imports `verify_manifest`, `load_verified_signal_library`, `build_output_manifest`, `file_sha256`, `load_verified_xlsx_artifact`, schema/kind constants. |
| Validation engine | `stackbuilder.py:41-62` | Imports the full Phase 5C surface (`FoldContext`, `StrategyCandidate`, `validate_strategy_set`, `write_validation_sidecar`, etc.). |
| yfinance lazy | `stackbuilder.py:192-195` | `try: import yfinance as yf` with `None` fallback. Loaded at module import time. |
| Optional project imports | `stackbuilder.py:197-215` | Tries `signal_library.shared_symbols` → `shared_symbols` → trivial fallback for `resolve_symbol` / `detect_ticker_type`. Tries `from onepass import load_signal_library`; falls back to `None`. **Importing this module imports `onepass` if available**, which transitively imports Dash, yfinance, and logging at OnePass import time. |
| Logger | `stackbuilder.py:65` | `logging.getLogger("stackbuilder.validation")` at import time. |
| ContextVar collector | `stackbuilder.py:82-84` | `_INPUT_COLLECTOR_VAR` ContextVar declared at module scope. Default `None`. |

### 3.2 Constants and configurable defaults

| Constant | File:line | Default / source |
|---|---|---|
| `DEFAULT_SIGNAL_LIB_DIR` | `stackbuilder.py:221-224` | `os.environ.get('SIGNAL_LIBRARY_DIR', <PROJECT_DIR>/signal_library/data/stable)` |
| `DEFAULT_PRICE_CACHE_DIR` | `stackbuilder.py:225` | `os.environ.get('PRICE_CACHE_DIR', 'price_cache/daily')` |
| `MASTER_TICKERS_PATH` | `stackbuilder.py:226` | `YF_MASTER_TICKERS_PATH` env → `MASTER_TICKERS_PATH` env → `global_ticker_library/data/master_tickers.txt` |
| `RUNS_ROOT` | `stackbuilder.py:228` | `'output/stackbuilder'` |
| `RISK_FREE_ANNUAL` | `stackbuilder.py:229` | `5.0` (percent) |
| `FLOAT_DTYPE` | `stackbuilder.py:230` | `np.float64` |
| `DEFAULT_IMPACT_XLSX_DIR` | `stackbuilder.py:232-235` | `os.environ.get('PRJCT9_IMPACT_XLSX_DIR', <PROJECT_DIR>/output/impactsearch)` |
| `OUTPUT_FORMAT` | `stackbuilder.py:237` | `os.environ.get('STACKBUILDER_OUTPUT_FORMAT', 'xlsx').lower()` — **module global, mutated by `main` and `run_for_secondary`** |
| `DEFAULT_GRACE_DAYS` | `stackbuilder.py:238` | `int(os.environ.get('IMPACT_CALENDAR_GRACE_DAYS', '10') or 10)` |
| `SIGNAL_LIB_DIR_RUNTIME` | `stackbuilder.py:255` | Starts as `DEFAULT_SIGNAL_LIB_DIR`; mutated by `main` from `--signal-lib-dir`. |
| `PROGRESS_ROOT` | `stackbuilder.py:258` | `output/stackbuilder/_progress` |
| `COMBINE_INTERSECTION` | `stackbuilder.py:261` | `False` at module scope; flipped to `(args.combine_mode == 'intersection')` in `main` and `run_for_secondary`. |
| `VERBOSE` | `stackbuilder.py:262` | `False`; mutated by `main` and `run_for_secondary`. |

### 3.3 IO helpers and JSON safety

| Function | File:line | Behavior |
|---|---|---|
| `_write_progress` | `stackbuilder.py:264-289` | Atomic file-backed progress (`temp + os.replace`). Preserves `started_ts` and prior keys; tolerates missing dir / read failure. Swallows all exceptions. |
| `ensure_dir` | `stackbuilder.py:292` | `Path(p).mkdir(parents=True, exist_ok=True)`. |
| `write_json` | `stackbuilder.py:295-298` | Pretty-printed dump; non-atomic (no temp + replace). |
| `_stable_cli_args_subset` | `stackbuilder.py:302-324` | Subset of `args` used for manifest fingerprint: `alpha`, `max_k`, `top_n`, `bottom_n`, `min_trigger_days`, `sharpe_eps`, `seed_by`, `search`, `combine_mode`, `grace_days`. |
| `_output_artifact_entry` | `stackbuilder.py:327-377` | Probes ext order `xlsx, csv, parquet, json`; returns name/filename/format/sha256/produced_at/size_bytes (+ row/column schema for CSV). |
| `_build_output_artifacts` | `stackbuilder.py:380-396` | Enumerates `rank_all`, `rank_direct`, `rank_inverse`, `cohort`, `combo_leaderboard` plus `summary.json`, `search_stats.json`. |
| `write_table` | `stackbuilder.py:398-413` | Writes basepath + ext per `OUTPUT_FORMAT`; xlsx → falls back to CSV on error; parquet → falls back to CSV on error. **Not atomic.** |

### 3.4 Master / primary universe and data loading

| Function | File:line | Behavior |
|---|---|---|
| `load_master_universe` | `stackbuilder.py:456-461` | Reads `MASTER_TICKERS_PATH`, splits on `[\s,]+`, uppercases. Returns `[]` if file missing. |
| `discover_from_signal_library` | `stackbuilder.py:463-477` | Globs `*_stable_v*.pkl` under `DEFAULT_SIGNAL_LIB_DIR` (flat + 2-letter sharded). |
| `primary_universe` | `stackbuilder.py:479-502` | `specified_tickers is not None` → use as-is (uppercased, dedup); else fall back to master list then library discovery. **Empty universe is allowed at this layer; phase1 enforces.** |
| `_fetch_secondary_from_yf` | `stackbuilder.py:506-528` | Live `yf.download(period='max', auto_adjust=False)`. Raises if `yf is None` or empty. |
| `load_secondary_prices` | `stackbuilder.py:530-556` | Tries parquet/csv under `DEFAULT_PRICE_CACHE_DIR` for `SEC`, `SEC_clean`, and `SEC/daily.parquet`; falls back to yfinance. **StackBuilder is therefore not zero-network when the price cache misses a secondary.** |

### 3.5 ImpactSearch XLSX fast-path

| Function | File:line | Behavior |
|---|---|---|
| `_standardize_rank_columns` | `stackbuilder.py:570-581` | Lowercase header → canonical name via `_RANK_COLMAP`; requires `Primary Ticker` and `Total Capture (%)`. |
| `try_load_rank_from_impact_xlsx` | `stackbuilder.py:583-700` | Scans `impact_xlsx_dir` for `{SEC}_*.xlsx`; picks freshest matching file; applies staleness gate against `max_age_days`; routes through `load_verified_xlsx_artifact`; under `strict_manifests`, missing/legacy/mismatched manifests reject the fast-path; populates `rejection_out` with `reason`/`path`/`age_days`/`max_age_days` for `"stale"` rejections. |

### 3.6 Signal-library loaders

| Function | File:line | Behavior |
|---|---|---|
| `list_signal_library_candidates` | `stackbuilder.py:702-706` | Globs flat `{ticker}_stable_v*.pkl` and 2-letter sharded `{prefix}/{ticker}_signal_library.pkl` under `SIGNAL_LIB_DIR_RUNTIME`. |
| `fallback_load_signal_library` | `stackbuilder.py:708-741` | Routes each candidate through `load_verified_signal_library(requested_params={'price_source': 'Close'})`. Legacy → warn + return; mismatch → warn + skip. |
| `load_lib_or_none` | `stackbuilder.py:743-754` | Prefers `onepass.load_signal_library`; falls back to `fallback_load_signal_library`. **Every successful load is recorded via `_record_input_lib` against the per-run collector** (provenance pinning). |

### 3.7 Signal application, scoring, metrics

| Function | File:line | Behavior |
|---|---|---|
| `apply_signals_to_secondary` | `stackbuilder.py:757-825` | ImpactSearch-parity alignment with grace-window padding; honors explicit `grace_days` (0 = strict, `None` = `DEFAULT_GRACE_DAYS=10`). Optional `(captures, trigger_mask)` tuple return. Reads `IMPACT_DEBUG_ALIGN` env (line 808) for diagnostic prints. |
| `metrics_from_captures` | `stackbuilder.py:827-859` | Delegates to `canonical_scoring.score_captures`; `RISK_FREE_ANNUAL=5.0`, `periods_per_year=252`, `ddof=1`. Single-arg fallback documented as deprecated at line 832. |
| `_combine_signals` | `stackbuilder.py:1445-1452` | Combine to one signal series (intersection / union per `COMBINE_INTERSECTION`). |
| `_captures_from_signals` | `stackbuilder.py:1454-1460` | Reuses combined signal → captures Series. |
| `_combined_metrics_signals` | `stackbuilder.py:1462-1485` | Build combined-mask metrics dict for K-member stacks. |

### 3.8 Validation integration (Phase 5C)

| Surface | File:line | Behavior |
|---|---|---|
| `_LOCKED_VALIDATION_SUMMARY_KEYS` | `stackbuilder.py:2606-2617` | 10 required manifest keys per locked Phase 5C-1 contract. |
| `_validate_stackbuilder_validation_summary` | `stackbuilder.py:2620-2632` | Raises `ValueError` naming the missing key. |
| `StackBuilderValidationAdapter` | `stackbuilder.py:2768+` | Phase 5C-2c walk-forward fold adapter that re-runs `phase2_rank_all` + `phase3_build_stacks` with `data_available_through=context.selection_cutoff` and `args2.prefer_impact_xlsx=False` (full-history XLSX is forbidden inside fold selection, locked contract). |
| `_prepare_stackbuilder_durable_validation` | `stackbuilder.py:3090+` | Run inside `run_for_secondary` before the final manifest write. Failure of the fallback-write propagates so a complete StackBuilder run directory is never produced without locked validation summary keys. |

### 3.9 Upstream dependency audit

| Dependency | Evidence | Current behavior / runner implication |
|---|---|---|
| ImpactSearch XLSX fast path | `stackbuilder.py:232-235`, `stackbuilder.py:583-700`, `stackbuilder.py:1099-1128` | Default root is `output/impactsearch` unless `PRJCT9_IMPACT_XLSX_DIR` / `--impact-xlsx-dir` overrides it. `try_load_rank_from_impact_xlsx` scans for the freshest `{SECONDARY}_*.xlsx`, rejects stale or manifest-mismatched workbooks, standardizes rank columns, and returns the ranking frame used by `phase2_rank_all`. This is the MVP primary path for the runner. |
| Central verified signal-library load | `stackbuilder.py:32-40`, `stackbuilder.py:708-754`, `provenance_manifest.py:1036-1119` | StackBuilder's fallback library path uses `load_verified_signal_library(..., requested_params={'price_source': 'Close'})`, which routes load/type/manifest verification through the central loader. The loader uses the manifest content-hash cache when `cache=True`, so the PR #278 / Phase 6I-57 LRU sizing lesson is inherited for this fallback path. |
| OnePass loader preference | `stackbuilder.py:210-215`, `stackbuilder.py:743-754` | If `onepass.load_signal_library` imports successfully, `load_lib_or_none` tries that first, then falls back to StackBuilder's central verified loader. The future runner should not assume the fallback path is always used; it should report effective signal-library source / warnings in the run JSON. |
| Secondary price data | `stackbuilder.py:225`, `stackbuilder.py:506-556` | StackBuilder reads `price_cache/daily` first and falls through to `yf.download` on cache miss. The future runner must preserve the explicit `--allow-network-fetch` gate and should surface price-cache misses before execution where practical. |
| Spymaster PKLs | `stackbuilder.py` static search; downstream evidence `trafficflow.py:86-87`, `trafficflow.py:1442-1458` | StackBuilder does **not** read `cache/results/*_precomputed_results.pkl` today. The Spymaster PKL default lives in TrafficFlow, which loads those PKLs downstream. StackBuilder output is therefore upstream of the Spymaster-PKL-consuming TrafficFlow stage, not a direct Spymaster PKL reader. |

### 3.10 Recent change-history audit

Read-only `git log --since=2026-05-01 --numstat -- stackbuilder.py` found 11 commits touching `stackbuilder.py`. Aggregate numstat: **+1,884 / -232**, net **+1,652**. Current file length is 3,427 lines versus 1,775 lines at the most recent pre-May-1 baseline (`7a27947`), matching the net growth.

| Commit | Date | Lines | Summary | Risk classification |
|---|---:|---:|---|---|
| `7406886` | 2026-05-01 | +62 / -85 | Adj Close removal in stale_check + signal library, `ddof` fix. | Correctness: yes (price basis / statistics); runner-safety: low. |
| `0768355` | 2026-05-01 | +43 / -15 | Backlog cleanup covering logs, dedupe, cache keys, grace, sentinels, closure, outdir. | Correctness: maybe; runner-safety: maybe (output/logging surfaces). |
| `1629645` | 2026-05-02 | +30 / -7 | Parity suites + `_score_primary` fix + hardening. | Correctness: yes; artifact risk: low. |
| `df8a456` | 2026-05-02 | +302 / -60 | Grace plumbing + `rank_inverse` structural fix. | Correctness: yes; runner defaults must preserve grace semantics. |
| `667ce6d` | 2026-05-03 | +32 / -1 | Signal-library provenance manifests. | Artifact/contract: yes. |
| `838009f` | 2026-05-03 | +27 / -29 | Manifest performance cache + central loader + B12 tightening. | Performance: yes; runner must preserve verified-loader path. |
| `e31a0c7` | 2026-05-03 | +280 / -3 | Output manifest helper + StackBuilder run manifests + Spymaster PKLs. | Artifact/contract: yes; manifest readback is required. |
| `8081f73` | 2026-05-03 | +126 / -13 | XLSX upsert manifests + strict-mode CLI + Phase 3 close. | Artifact/contract: yes; strict manifest behavior affects ImpactSearch fast path. |
| `271312f` | 2026-05-04 | +34 / -1 | Post-Phase-3 UI / regression cleanup. | Correctness: maybe; runner-safety: low. |
| `522bf70` | 2026-05-05 | +51 / -1 | Deprecated vestigial StackBuilder CLI flags. | Runner-safety: yes; future runner should not surface deprecated knobs as meaningful. |
| `fe46aa5` | 2026-05-07 | +897 / -17 | Phase 5C-2c validation integration: full-refit walk-forward, candidate collector hook, run-manifest summary. | Correctness / artifact contract: high; Phase B must not bypass validation summary generation. |

Specific risk classes requested for this audit:

- Manifest verification: added / tightened across `667ce6d`, `838009f`, `e31a0c7`, and `8081f73`; future runner must validate `run_manifest.json` and output artifact presence after execution.
- Canonical scoring delegation: current scoring path delegates to `canonical_scoring` (`stackbuilder.py:27-31`, `stackbuilder.py:827-859`); no new scoring algorithm should be introduced in the runner.
- Rejection / diagnostic plumbing: ImpactSearch XLSX rejection detail lives in `try_load_rank_from_impact_xlsx` (`stackbuilder.py:583-700`) and is surfaced by `phase2_rank_all` (`stackbuilder.py:1099-1252`); runner JSON should preserve these reasons.
- Refactor indirection: the largest current indirection is Phase 5C validation (`fe46aa5`, `StackBuilderValidationAdapter` at `stackbuilder.py:2768+`); runner scaffold tests should prove dry-run classification does not accidentally import or execute this heavy path.

### 3.11 Progress and concurrency

| Surface | File:line | Behavior |
|---|---|---|
| `_INPUT_COLLECTOR_VAR` ContextVar | `stackbuilder.py:82-84` | One collector per `run_for_secondary` invocation; survives concurrent Dash launches. |
| `_start_input_manifest_collection` / `_finalize_input_manifest_collection` | `stackbuilder.py:87-177` | Token-based set / reset of the ContextVar; finalize returns `{input_manifest_hashes, input_legacy_count, input_missing_manifest_count}`. |
| `_submit_with_context` | `stackbuilder.py:180-190` | Wraps `executor.submit(ctx.run, fn, *args, **kwargs)` so worker threads see the submitter's ContextVar. Used inside `phase2_rank_all`. |

### 3.12 `__main__` and Dash launch

`stackbuilder.py:3426` is `if __name__ == '__main__':` which simply calls `main()`. `main` parses args, mutates the four module globals (`OUTPUT_FORMAT`, `SIGNAL_LIB_DIR_RUNTIME`, `VERBOSE`, `COMBINE_INTERSECTION`), creates `args.outdir`, and:

- If neither `--secondaries` nor `--secondary` is set → `run_dash(args.outdir, port=args.port)` (`stackbuilder.py:3399`). **The bare-invocation default starts a Dash server.**
- Else, builds a list of secondaries and runs them through `run_for_secondary` either sequentially or via `ProcessPoolExecutor(max_workers=...)` when `--jobs > 1` and more than one secondary (`stackbuilder.py:3417-3422`).
- If `--serve`, opens Dash on the last run's output dir after all secondaries complete (`stackbuilder.py:3423-3424`).

---

## 4. Current Dash Worker Path

`run_dash` lives at `stackbuilder.py:1892-2307`. It is a lazy import (`from dash import ...`) inside the function so the module itself does not unconditionally import Dash at the top level.

### 4.1 Layout and defaults (`stackbuilder.py:1904-1992`)

| Widget | id | Default value |
|---|---|---|
| Secondary ticker(s) | `secondary-input` | empty |
| Primary tickers textarea | `primaries-input` | empty placeholder |
| Top N | `topn` | `20` |
| Bottom N | `bottomn` | `20` |
| Max K | `maxk` | `6` |
| Exhaustive K | `exk` | `4` |
| alpha | `alpha` | `0.05` |
| Min Trigger Days | `min-trigger-days` | `30` |
| Sharpe ε | `sharpe-eps` | `1e-6` |
| Seed by | `seed-by` | `total_capture` |
| Optimize by | `optimize-by` | `auto` (mapped to seed-by) |
| Allow decreasing | `allow-decreasing` | checked (`'y'`) |
| Prefer ImpactSearch xlsx | `prefer-xlsx` | checked (`'y'`) |
| ImpactSearch folder | `xlsx-dir` | `DEFAULT_IMPACT_XLSX_DIR` |
| Run button | `run-btn` | — |
| Progress / batch tables / intervals | `progress-wrap`, `batch-progress-wrap`, `progress-interval`, `jobs-interval` | progress polling at 1 Hz |

### 4.2 Run callback `_run` (`stackbuilder.py:2001-2127`)

- Parses `secondary` on `,` (uppercased; deduped via `if s.strip()` filter).
- Parses `primaries` on `[,\s]+` (uppercased; filtered).
- Validates `xdir` exists when `prefer_fast` is set.
- Fail-closed: empty primaries AND `prefer_fast=False` → status message error, no run.
- For every secondary, writes an initial `running/preflight` progress entry under `PROGRESS_ROOT` and starts a daemon `threading.Thread` that calls `run_for_secondary` with a per-job `SimpleNamespace`.
- Per-job args namespace fields (line 2072-2088):
  - `top_n`, `bottom_n`, `max_k`, `alpha`, `min_trigger_days`, `sharpe_eps`, `seed_by`, `optimize_by`, `prefer_impact_xlsx`, `impact_xlsx_dir`, `allow_decreasing` — from the UI inputs.
  - `min_marginal_capture=0.0`, `threads='auto'`, `outdir=<dash outdir>`, `fail_on_missing_cache=False`, `serve=False`, `port=8054`, `impact_xlsx_max_age_days=45`, `search='beam'`, `beam_width=12`, `exhaustive_k=int(exk or 4)`, `both_modes=False`, `k_patience=1`, `progress_path=ppath`.
- **Primaries snapshot is captured per job (line 2097)** to avoid late-binding closure bugs across loop iterations.
- Failures inside the thread are caught and written to the progress file with `status='failed'`.

### 4.3 Progress polling (`stackbuilder.py:2129-2178+`)

Two intervals at 1 Hz: per-job `_poll_progress` and batch `_poll_jobs`. Progress files are read on every tick; final leaderboard is read from `<outdir>/combo_leaderboard.<ext>` once `status=='complete'`.

---

## 5. Core Engine Call Path

### 5.1 `run_for_secondary` (`stackbuilder.py:2309-2599`)

**Inputs:** `args` (Namespace or SimpleNamespace), `secondary` (str), `specified_primaries` (Optional[List[str]]), keyword `grace_days`.

**Side effects:**
- Mutates the four module globals (`OUTPUT_FORMAT`, `SIGNAL_LIB_DIR_RUNTIME`, `VERBOSE`, `COMBINE_INTERSECTION`) on every call (`stackbuilder.py:2311-2318`).
- Resolves `effective_grace` from explicit kwarg → `args.grace_days` → `DEFAULT_GRACE_DAYS` (`stackbuilder.py:2330-2333`).
- Calls `phase1_preflight` → loads secondary frame, computes `sec_rets`, parses primaries.
- Creates `temp_outdir = <output_root>/<sec_clean>/temp_<ts>_<pid>` and starts a fresh ContextVar collector via `_start_input_manifest_collection()` (`stackbuilder.py:2365`).
- Writes initial `run_manifest.json` with provenance fields from `_build_output_manifest` (`stackbuilder.py:2393-2404`).
- Calls `phase2_rank_all` with `grace_days=effective_grace`; writes `rank_all`, `rank_direct`, `rank_inverse`.
- Calls `phase3_build_stacks` with `progress_cb=_k_progress`; writes `cohort`, `combo_k=<K>.json` per K, and `combo_leaderboard.<ext>`.
- Calls `_prepare_stackbuilder_durable_validation` BEFORE publishing (locked 5C-1 §3 fail-closed contract, `stackbuilder.py:2445-2472`).
- Constructs `final_name` from final stack members (`stackbuilder.py:2474-2495`) and `final_outdir = <secondary_parent>/<final_name minus secondary prefix>`.
- **`shutil.rmtree(final_outdir, ignore_errors=True)` on existing directory** (`stackbuilder.py:2501-2502`) followed by `shutil.move(temp_outdir, final_outdir)`. **Existing same-name run directories are deleted, not pointer-replaced.**
- Writes `summary.json` and the final enriched `run_manifest.json` (with `output_artifacts`, `input_manifest_hashes`, locked 10 validation keys).
- Prints `[COMPLETE]`, `[RESULT]`, `[OUTPUT]`, `[VALIDATION]` lines to stdout.

**Failure modes (visible from code):**
- `phase1_preflight` raises `RuntimeError` or `SystemExit("[FATAL] No primary tickers provided...")`.
- `phase2_rank_all` raises `SystemExit` if `--strict-manifests` rejects XLSX without caller primaries (`stackbuilder.py:1121-1127`), or `SystemExit("[FATAL] No primaries produced valid metrics.")` if every primary returns `None` (`stackbuilder.py:1326`).
- `phase3_build_stacks` raises `SystemExit("[FATAL] No single candidate passed the min Trigger Days gate.")` if no single survives `min_td` (`stackbuilder.py:1628`).
- `_prepare_stackbuilder_durable_validation` propagates fallback-write failures so the manifest gate fail-closes.
- The outer `except Exception as e:` at `stackbuilder.py:2588` removes `temp_outdir` and finalizes the ContextVar collector, then re-raises.

**Safe for a future headless runner?** Yes for a single secondary; the call is the natural orchestration entry. The runner must avoid relying on or mutating the four module globals across processes, and must take responsibility for output-dir isolation when running with multiple secondaries in parallel (the current code already supports it via `ProcessPoolExecutor`).

### 5.2 `phase1_preflight` (`stackbuilder.py:862-892`)

- `resolve_symbol(secondary)` → vendor symbol.
- `load_secondary_prices(vendor_secondary)` → secondary frame; **raises `RuntimeError` on empty**.
- `pct_returns(sec_df['Close'])` → secondary returns Series.
- If `specified_primaries is not None`: `primary_universe(specified_primaries)` (uppercased, dedup, preserves order); if the resulting list is empty AND `prefer_impact_xlsx=False`, raises `SystemExit`.
- Returns `(primaries_df, sec_rets, vendor_secondary)`.

### 5.3 `phase2_rank_all` (`stackbuilder.py:1081-1354`)

- Fast-path (ImpactSearch XLSX) only fires when `prefer_impact_xlsx=True` AND `data_available_through is None` (validation folds always skip).
- Strict-manifests + no caller primaries + XLSX rejected → `SystemExit` (`stackbuilder.py:1121-1127`).
- XLSX fast-path: filters to caller primaries cohort; rebuilds `rank_inverse` from real inverse-mode signals via `_score_primary_from_signals` (`stackbuilder.py:1149-1196`); writes `rank_all`, `rank_direct`, `rank_inverse`; emits progress.
- Slow path: `ThreadPoolExecutor(max_workers=args.threads or auto)` running `_score_primary_both_modes` per primary; `_submit_with_context` wraps each submit for ContextVar propagation; uses `tqdm` if available (otherwise simple counter); writes `rank_all/rank_direct/rank_inverse` and rolls progress 25 % → 60 %.
- Raises `SystemExit("[FATAL] No primaries produced valid metrics.")` when every primary fails (`stackbuilder.py:1326`).

### 5.4 `phase3_build_stacks` (`stackbuilder.py:1487-1889`)

- Cohort: `top_n` (mode `D`) + `bottom_n` (mode `I`) from `rank_direct` / `rank_inverse`; auto-enables `both_modes` when duplicates exist (`stackbuilder.py:1580-1582`).
- Precomputes `_signals_aligned_and_mask` per `(ticker, mode)` into `sig_cache`.
- K=1 seed selected by `seed_by` (`sharpe` / `total_capture`).
- K=2..max_k: `search='exhaustive' or K <= exhaustive_k` uses `exhaustive_k(K)` (enumerate all `(ticker, mode)` tuples); otherwise beam expansion with `beam_width`.
- Monotone improvement gate: requires `current > previous + sharpe_eps` along the chosen `optimize_by` metric, unless `allow_decreasing` is set.
- `k_patience` (Dash default 1, CLI default 0) allows N non-improving K's before terminating.
- Writes `cohort.<ext>`, `combo_k=<K>.json` per K, and `combo_leaderboard.<ext>`. Writes `search_stats.json` when `--save-stats` or `VERBOSE`.
- Optional `validation_collector` receives one canonical-deduped record per scored candidate (Phase 5C-2c).

### 5.5 Atomicity helpers

- `_write_progress` (`stackbuilder.py:264-289`) — atomic via temp + `os.replace`.
- `write_json` (`stackbuilder.py:295-298`) — **not atomic**.
- `write_table` (`stackbuilder.py:398-413`) — **not atomic** (writes directly to `<basepath>.<ext>`).
- Run directory finalization (`stackbuilder.py:2500-2505`) — temp dir is `shutil.move`d to final name; existing same-name target is `shutil.rmtree`d first. The temp→final rename is single-OS-call where the platform allows; behavior is OS-specific.

---

## 6. Mutable State, Globals, ContextVars, and Env Vars

| Surface | File:line | Default | Process-global? | Runner action |
|---|---|---|---|---|
| `OUTPUT_FORMAT` | `stackbuilder.py:237`, mutated `:3384, :2313` | `'xlsx'` (env override) | Yes (module global) | Runner must set per-call OR fork a subprocess; in-process repeated calls must restore. |
| `SIGNAL_LIB_DIR_RUNTIME` | `stackbuilder.py:255`, mutated `:3385, :2314` | `DEFAULT_SIGNAL_LIB_DIR` | Yes | Same as above. |
| `VERBOSE` | `stackbuilder.py:262`, mutated `:3386, :2315` | `False` | Yes | Runner sets/restores. |
| `COMBINE_INTERSECTION` | `stackbuilder.py:261`, mutated `:3387, :2316` | `False` | Yes | Runner sets/restores. |
| `PROGRESS_ROOT` | `stackbuilder.py:258` | `output/stackbuilder/_progress` | Yes (constant) | Read-only; runner may supply progress paths instead. |
| `RUNS_ROOT` | `stackbuilder.py:228` | `'output/stackbuilder'` | Yes (constant) | Runner overrides via `args.outdir`. |
| `DEFAULT_SIGNAL_LIB_DIR` | `stackbuilder.py:221-224` | env or `<PROJECT_DIR>/signal_library/data/stable` | Yes (constant) | Runner respects env / `--signal-lib-dir`. |
| `DEFAULT_PRICE_CACHE_DIR` | `stackbuilder.py:225` | env or `price_cache/daily` | Yes (constant) | Runner cannot easily change; would need monkey-patch or new flag. |
| `DEFAULT_IMPACT_XLSX_DIR` | `stackbuilder.py:232-235` | env or `<PROJECT_DIR>/output/impactsearch` | Yes (constant) | Runner overrides via `--impact-xlsx-dir`. |
| `DEFAULT_GRACE_DAYS` | `stackbuilder.py:238` | env or 10 | Yes (constant) | Runner threads explicit `grace_days` (Phase 2B-2B amendment). |
| `MASTER_TICKERS_PATH` | `stackbuilder.py:226` | env or `global_ticker_library/data/master_tickers.txt` | Yes (constant) | Runner respects env. |
| `_INPUT_COLLECTOR_VAR` (ContextVar) | `stackbuilder.py:82-84` | `None` | Per-Context | Runner must NOT pre-set this. `run_for_secondary` opens its own token. |
| Progress JSON files | `<PROGRESS_ROOT>/<sec>_<pid>_<ts>.json` | — | Filesystem | Runner can choose explicit `args.progress_path` to keep evidence isolated. |
| Temp dirs | `<output_root>/<sec_clean>/temp_<ts>_<pid>/` | — | Filesystem | Runner can isolate by setting `--outdir`. |
| `IMPACT_DEBUG_ALIGN` env | `stackbuilder.py:808` | `'0'` | Per-process env | Read at alignment time. |
| `STACKBUILDER_OUTPUT_FORMAT` env | `stackbuilder.py:237` | `'xlsx'` | Per-process env | Default for `--output-format`. |
| `STACKBUILDER_THREADS` env | `stackbuilder.py:3355` | `'auto'` | Per-process env | Default for `--threads`. |
| `STACKBUILDER_JOBS` env | `stackbuilder.py:3377` | `'1'` | Per-process env | Default for `--jobs`. |
| `yfinance` import | `stackbuilder.py:192-195` | optional | Process-imported | **StackBuilder is not zero-network** because `load_secondary_prices` falls through to `_fetch_secondary_from_yf` when the price cache misses. |
| OnePass `load_signal_library` import | `stackbuilder.py:210-214` | optional | Process-imported | When present, importing `stackbuilder` transitively imports `onepass`, which loads Dash + yfinance + logging at OnePass import time. The future runner must lazy-import this to keep startup clean. |
| `provenance_manifest` helpers | `stackbuilder.py:32-40` | required | Process-imported | LRU + `load_verified_*` used per Phase 6I-59 lessons. |

---

## 7. Current Artifacts and Atomicity

Per-secondary output rooted at `<args.outdir>/<sec_clean>/`. A successful run produces these files under `<final_outdir>` (a renamed `temp_<ts>_<pid>`):

| Artifact | File:line evidence | Notes |
|---|---|---|
| `rank_all.<ext>` | `stackbuilder.py:1200, 1351` | All primaries × direct-mode scores. Direct write via `write_table`. |
| `rank_direct.<ext>` | `stackbuilder.py:1201, 1352` | Direct-mode sorted desc by Total Capture. |
| `rank_inverse.<ext>` | `stackbuilder.py:1202, 1353` | Real inverse-mode scores (Phase 2B-2B). |
| `cohort.<ext>` | `stackbuilder.py:1595` | Top-N + Bottom-N cohort with mode column. |
| `combo_k=<K>.json` per K | `stackbuilder.py:1659, 1865` | One JSON per surviving K level. |
| `combo_leaderboard.<ext>` | `stackbuilder.py:1874` | Final K-by-K leaderboard. |
| `search_stats.json` | `stackbuilder.py:1881` | Conditional on `--save-stats` or `VERBOSE`. |
| `summary.json` | `stackbuilder.py:2544` | Run-level metrics summary. |
| `run_manifest.json` | `stackbuilder.py:2404, 2580` | Two writes per run: initial (`status='running'`) then final (`status='complete'`) with `output_artifacts`, `input_manifest_hashes`, locked 10 validation keys. **Not atomic.** |
| Progress JSON | `<PROGRESS_ROOT>/<sec_clean>_<pid>_<ts>.json` | Atomic via temp + `os.replace` (`stackbuilder.py:283-287`). |

**Atomicity guarantees and gaps:**

- Progress file writes are atomic.
- `write_table` and `write_json` are NOT atomic; a SIGKILL mid-write can leave partial files.
- The temp → final rename uses `shutil.move`, which is single-OS-call on most platforms when source and target share a volume.
- **There is no `<stem>.runner_partial.<ext>` + `os.replace` discipline equivalent to OnePass / ImpactSearch runners**; the future StackBuilder runner must add it for the canonical output paths it controls (selected-build pointer, etc.).
- `shutil.rmtree` of existing same-name final directories is destructive (`stackbuilder.py:2501-2502`). Historical runs with different names are preserved because the directory name encodes the final stack members.

Validation sidecars and validation summaries are emitted inside `_prepare_stackbuilder_durable_validation` (`stackbuilder.py:3090+`) before the manifest write; locked 10 keys are gated by `_validate_stackbuilder_validation_summary`.

**Canonical / selected pointer files do NOT exist today.** Cannot determine from static code that any "which build is showing" pointer is currently produced. Each run leaves its own named directory under `output/stackbuilder/<SEC>/<final_name>/`; downstream consumers presumably enumerate by glob.

---

## 8. Existing CLI / Headless Surface

`parse_args` lives at `stackbuilder.py:3311-3379`. The following flags are currently parsed (current code default in parentheses):

| Flag | Default | Notes |
|---|---|---|
| `--secondary` | None | Single secondary |
| `--secondaries` | None | Comma-separated list |
| `--signal-lib-dir` | `DEFAULT_SIGNAL_LIB_DIR` | Mutates `SIGNAL_LIB_DIR_RUNTIME` |
| `--primaries` | None | Comma-separated; otherwise master list or library discovery |
| `--top-n` | `20` | |
| `--bottom-n` | `20` | |
| `--max-k` | `6` | |
| `--alpha` | `0.05` | **Deprecated** (`stackbuilder.py:3267-3274`); accepted as metadata, no effect on selection |
| `--min-marginal-capture` | `0.0` | **Deprecated**; no effect |
| `--min-trigger-days` | `30` | |
| `--sharpe-eps` | `1e-6` | |
| `--seed-by` | `total_capture` | choices: `sharpe`, `total_capture` |
| `--optimize-by` | `None` (→ seed-by) | choices: `sharpe`, `total_capture` |
| `--allow-decreasing` | `False` | |
| `--grace-days` | `None` (→ `DEFAULT_GRACE_DAYS=10`) | `0` = strict |
| `--search` | `beam` | choices: `greedy`, `beam`, `exhaustive` |
| `--beam-width` | `12` | |
| `--exhaustive-k` | `4` | |
| `--both-modes` | `False` | Auto-enables on cohort duplicates |
| `--k-patience` | `0` | (Dash sets `1`) |
| `--combine-mode` | `intersection` | Mutates `COMBINE_INTERSECTION` |
| `--verbose` | `False` | Mutates `VERBOSE` |
| `--no-progress` | `False` | |
| `--save-stats` | `False` | |
| `--threads` | env `STACKBUILDER_THREADS` or `'auto'` | |
| `--outdir` | `RUNS_ROOT` (`output/stackbuilder`) | |
| `--fail-on-missing-cache` | `False` | **Deprecated**; no effect |
| `--serve` | `False` | Open Dash after run |
| `--port` | `8054` | |
| `--prefer-impact-xlsx` | `False` | |
| `--impact-xlsx-dir` | `DEFAULT_IMPACT_XLSX_DIR` | |
| `--impact-xlsx-max-age-days` | `45` | |
| `--strict-manifests` | `False` | |
| `--output-format` | env `STACKBUILDER_OUTPUT_FORMAT` or `xlsx` | choices: `xlsx`, `parquet`, `csv` |
| `--jobs` | env `STACKBUILDER_JOBS` or `'1'` | |

**What's missing for a clean dry-run-first headless contract:**

- No `--write` / `--allow-network-fetch` two-key gate. `--secondary` plus any other args writes immediately.
- No dry-run plan output. The current `parse_args` either runs immediately or launches Dash; there is no way to ask "what would you do".
- No structured JSON stdout. The engine prints `[COMPLETE]`, `[RESULT]`, `[OUTPUT]`, `[VALIDATION]`, `[PHASE2]`, `[PHASE3]`, `[FASTPATH]`, `[CLEANUP]`, `[ERROR]`, `[STRICT]`, `[WARN]`, `[INFO]` lines to stdout, mixed with tqdm progress when present. **Stdout is not parseable JSON.**
- Progress to stderr vs stdout discipline is absent. `tqdm` (when present) goes to stderr by default; the structured `print(...)` lines go to stdout.
- No process-conflict check.
- No fail-closed gate against unintended canonical writes.
- No `runner_partial` → `os.replace` discipline for the canonical paths the future runner introduces (e.g. `selected_build.json`).
- Bare invocation (no `--secondary*`) launches a Dash server — a future runner's import path must not have this side effect.

---

## 9. Gaps a Future Runner Must Fill

1. **Dry-run-first**: default to plan-only output; require explicit `--write` for any disk write outside the session log.
2. **Stdout/stderr split**: structured JSON to stdout; progress, conflict notices, tracebacks to stderr. Wrap `run_for_secondary` calls in `contextlib.redirect_stdout(io.StringIO())` so the engine's `print` lines never bleed into the runner's JSON contract.
3. **Lazy import** of `stackbuilder` from the runner module: avoid the module-import side effects (Dash if any caller imports `run_dash`, yfinance, and OnePass).
4. **Process-conflict check** mirroring OnePass / ImpactSearch runners. Must exclude `os.getpid()` and prefer `psutil` → `Get-CimInstance` → `ps` fallback.
5. **Per-secondary continuation**: each secondary runs in its own scope; per-secondary failures must not abort the batch. The engine raises `SystemExit` from inside `phase1` / `phase2` / `phase3`, which the runner must convert to per-secondary `status="error"` entries.
6. **Atomic canonical writes** for runner-controlled artifacts (e.g. `selected_build.json`).
7. **Explicit budget**: every invocation must carry a `--duration-budget-minutes` and an `--operator-budget-label`; no hidden full-exhaustion fallback.
8. **No Dash dependency** in the runner import path.
9. **Network policy**: StackBuilder can call yfinance when the price cache misses. The runner must gate this behind `--allow-network-fetch` (off by default).
10. **Selection pointer**: produce a `selected_build.json` so downstream consumers can read one canonical pointer per secondary without enumerating directories.

---

## 10. Proposed Future Runner Contract

> Phase B will implement this contract in a new file. This Phase A doc does not.

### 10.1 Runner identity

Proposed file: **`stackbuilder_workbook_runner.py`** at the project root. Name chosen for parity with `onepass_workbook_runner.py` and `impactsearch_workbook_runner.py`.

### 10.2 Dry-run-first contract

- Default mode: dry-run plan emitted to stdout as one JSON object; **no writes**, no engine call beyond preflight classification (and never beyond `phase1_preflight` for any secondary that requires it).
- Explicit `--write` required for output writes.
- `--allow-network-fetch` required separately when StackBuilder may hit yfinance (price-cache miss path); both flags must be present for full execution.
- Structured JSON to stdout (exactly one object per invocation).
- Progress / warnings / tracebacks to stderr.
- Isolated `--outdir` supported (Phase C smoke), canonical `--outdir` supported (Phase E).
- Process-conflict check is mandatory at startup; runs into `status="blocked_process_conflict"` on hit.
- Per-secondary continuation: per-secondary errors recorded as `status="error"` entries; batch-level setup/export errors abort.
- Fail-closed for ambiguous canonical writes (e.g. multiple competing builds without a selection policy resolution).
- No hidden full-universe fallback. If `--primaries` and `--primaries-file` are both empty AND `--prefer-impact-xlsx` is not set, the runner refuses to run.
- No Dash dependency in the runner import path.
- Lazy import of `stackbuilder` inside `contextlib.redirect_stdout` so module-import side effects don't reach runner stdout.

### 10.3 Proposed CLI shape

Flags marked **(proposed)** are NOT supported by today's `stackbuilder.py` CLI and would be added in the runner; everything else mirrors current engine flags.

| Flag | Default | Notes |
|---|---|---|
| `--secondaries` | required | Comma-separated list (or single) |
| `--primary-source` | `impact_xlsx` (proposed) | choices: `explicit_csv`, `file`, `impact_xlsx`, `signal_library_dir`. Disambiguates the three current paths. |
| `--primaries` | None | Comma-separated explicit list |
| `--primaries-file` | None | Path to plain-text universe |
| `--impact-xlsx-dir` | `DEFAULT_IMPACT_XLSX_DIR` | |
| `--prefer-impact-xlsx` | `True` (v1) | |
| `--outdir` | `output/stackbuilder` | |
| `--output-format` | `xlsx` | choices: `xlsx`, `parquet`, `csv` |
| `--k-max` | `6` | |
| `--top-n` | `20` | |
| `--bottom-n` | `20` | |
| `--exhaustive-k` | `4` | |
| `--search` | `beam` | choices: `beam`, `exhaustive` |
| `--beam-width` | `12` | |
| `--min-trigger-days` | `30` | |
| `--sharpe-eps` | `0.01` (proposed v1 lock; engine default is `1e-6`) | See §11 |
| `--seed-by` | `total_capture` | |
| `--optimize-by` | `seed_by` unless explicitly set | |
| `--allow-decreasing` | `False` | |
| `--grace-days` | `None` (engine `DEFAULT_GRACE_DAYS=10`) | |
| `--write` (proposed) | `False` | Required to write any output. |
| `--allow-network-fetch` (proposed) | `False` | Required for yfinance fallback in `load_secondary_prices`. |
| `--duration-budget-minutes` (proposed) | required at runtime | No default; runner refuses without it. |
| `--operator-budget-label` (proposed) | required at runtime | Free-text label recorded in the runner report. |
| `--strict-manifests` | `False` | Engine flag passthrough. |
| `--impact-xlsx-max-age-days` | `45` | Engine flag passthrough. |
| `--jobs` | `1` | Engine flag passthrough. |
| `--no-progress` | `False` | Engine flag passthrough. |
| `--save-stats` | `False` | Engine flag passthrough. |

---

## 11. Locked v1 Defaults

Operator-locked decisions for Phase B unless a later phase explicitly revises them.

| Knob | v1 default | Notes |
|---|---|---|
| `prefer_impact_xlsx` | **true** | MVP path; consumes ImpactSearch canonical XLSX for primaries cohort. |
| `k_max` | `6` | Matches current Dash default. |
| `top_n` | `20` | |
| `bottom_n` | `20` | |
| `search` | `beam` | |
| `beam_width` | `12` | |
| `exhaustive_k` | `4` | Used only when `search=beam` falls back to exhaustive for K ≤ this value (`stackbuilder.py:1791`). |
| `min_trigger_days` | `30` | |
| `sharpe_eps` | `0.01` | Tighter than the engine's `1e-6` default; matches Dash widget value (`stackbuilder.py:2045`). |
| `seed_by` | `total_capture` | |
| `optimize_by` | `seed_by` unless explicitly set | |
| `allow_decreasing` | `false` | |
| `output_format` | current code default (`xlsx`) unless operator overrides | |
| `impact_xlsx_max_age_days` | current code default (`45`) unless operator overrides | |
| `grace_days` | current code default (`None` → `DEFAULT_GRACE_DAYS=10`) unless operator overrides | |

Clarifications:
- These are **v1 runner defaults**, not proof they are globally optimal. Phase D may revise operational recommendations.
- **Full exhaustion is never the default.** `exhaustive_k` does not mean "exhaustive search is the default"; it only applies when `search=beam` falls back to exhaustive at K ≤ exhaustive_k (`stackbuilder.py:1791`). To request true exhaustive search the operator must set `--search exhaustive` with an explicit budget.

### Timeout / budget policy

- No unbounded StackBuilder runs. Every future runner invocation must carry an explicit budget.
- Phase B dry-run must not perform heavy compute (no `phase2_rank_all` beyond preflight classification).
- Phase C smoke must use small controlled inputs (1–2 secondaries).
- Phase D benchmark cells must use explicit budgets.
- Full holy-grail exhaustive search is an operator-selected budget class, not a default.

For Phase C single-secondary smoke only, this doc recommends a fallback ceiling of **240 minutes**. Benchmark and full runs (Phases D / E) must use operator-supplied budgets — no hidden default.

---

## 12. Canonical Build Selection Policy

> Proposed future runner behavior. The current `stackbuilder.py` does NOT produce a selection pointer.

### Goal

StackBuilder may produce multiple builds per secondary over time. The runner needs a deterministic pointer so downstream stages consume the intended build without deleting historical runs.

### Locked v1 policy

1. **Same K, multiple dates:** latest successful completed run wins.
2. **Different K, same secondary:** highest `Total Capture (%)` wins.
3. **Within configured tolerance on Total Capture:** higher `Sharpe Ratio` wins.
4. **Still tied:** latest successful run wins.
5. **Operator pin overrides** automatic selection.
6. Build provenance is preserved. **Nothing is deleted.** Selection is pointer/manifest based.

### Selection manifest

Path: `output/stackbuilder/<SECONDARY>/selected_build.json`

Proposed minimum schema:

```json
{
  "schema_version": 1,
  "secondary": "<SECONDARY>",
  "selected_run_id": "<sec>-<ts>-<pid>",
  "selected_run_dir": "output/stackbuilder/<SECONDARY>/<final_name>",
  "selected_k": 4,
  "selected_metric": "total_capture",
  "total_capture": 0.0,
  "sharpe_ratio": 0.0,
  "row_count": 0,
  "created_at": "2026-05-21T00:00:00Z",
  "selected_at": "2026-05-21T00:00:00Z",
  "selection_policy": "v1.total_capture_then_sharpe_then_latest",
  "operator_pinned": false,
  "source_manifest_path": "output/stackbuilder/<SECONDARY>/<final_name>/run_manifest.json",
  "runner_version": "0.1.0"
}
```

> **The selection manifest is the source of truth for which build is showing.** Downstream stages (TrafficFlow, Confluence, website) must read this pointer, not enumerate run directories.

Atomic write: produce `selected_build.json.runner_partial.json` and `os.replace` into `selected_build.json` only after the new pointer is fully serialized. Operator pin: a sibling `selected_build.pinned.json` (operator-curated) takes precedence; runner refuses to overwrite it without `--unpin`.

---

## 13. Benchmark Sweep Methodology (Phase D)

> This section scopes the benchmark only. Phase A does not run it.

### 13.1 Purpose

Find runtime-vs-build-quality curves per representative secondary class. The benchmark output is a decision aid; it must not auto-select one universal default.

### 13.2 Representative secondary classes

Use classes, not hard-coded irreversible tickers:

- high-liquidity index / ETF
- mega-cap single equity
- volatile single equity
- ETF / sector
- sparse / international / limited-history ticker

Concrete examples should be chosen at Phase D launch time, grounded in existing `output/impactsearch/*.xlsx` artifacts and operator selection. **No irreversible benchmark universe is hard-coded in this doc.**

### 13.3 Sweep dimensions

| Dimension | Cells |
|---|---|
| `k_max` | 3, 6, 9, 12 |
| `top_n` | 10, 20, 40 |
| `bottom_n` | 10, 20, 40 |
| `search` | beam always |
| `exhaustive` | only with explicit duration budget |
| `beam_width` | 8, 12, 24 |
| `prefer_impact_xlsx` | True for MVP; False as a control if feasible |
| duration budget | operator-selected per cell |

Budget examples (not defaults):

- 30 min
- 2 hr
- overnight
- holy-grail

### 13.4 Required outputs per benchmark cell

- `elapsed_seconds`
- CPU / RSS if measured
- build count / row count by K level
- candidate count if available
- best Sharpe by K
- best Total Capture by K
- selected build under v1 policy
- output artifact paths
- warnings / errors
- progress samples
- `next_stage_ready`
- whether budget was exhausted
- whether timeout occurred

### 13.5 Benchmark verdict

Each cell must classify into one of:

- `viable_default`
- `viable_with_operator_budget`
- `too_expensive_for_mvp_default`
- `failed`
- `inconclusive`

---

## 14. Run Reporting and Monitoring Contract

Every future runner invocation must emit one JSON object to stdout with this shape:

```json
{
  "schema_version": 1,
  "stage": "phase_b_dry_run | phase_c_smoke | phase_d_benchmark | phase_e_canonical",
  "run_id": "<sec_or_batch>-<ts>-<pid>",
  "status": "ok | dry_run | partial | failed | timeout | refused | blocked_process_conflict",
  "started_at": "<utc_iso>",
  "ended_at": "<utc_iso>",
  "elapsed_seconds": 0.0,
  "cwd": "<PROJECT_ROOT>",
  "git_head": "<sha>",
  "inputs": { /* resolved --secondaries / --primaries / etc. */ },
  "effective_config": { /* v1 defaults + operator overrides */ },
  "per_secondary_results": [
    {
      "secondary": "<SEC>",
      "status": "ok | error | skipped",
      "run_dir": "output/stackbuilder/<SEC>/<final_name>",
      "selected_build": { /* selection manifest snapshot */ },
      "row_counts": { "rank_all": 0, "rank_direct": 0, "rank_inverse": 0, "cohort": 0, "leaderboard": 0 },
      "k_level_counts": { "1": 0, "2": 0, "3": 0 },
      "warnings": [],
      "error": "<type: msg or null>"
    }
  ],
  "artifacts_written": ["..."],
  "progress_path": "logs/phase_6i_XX_*/<ts>/progress.json",
  "process_conflict": "no_python_conflicts | blocked_process_conflict",
  "next_stage_ready": true,
  "verdict": "PASS | PASS WITH NOTES | FAIL | TIMEOUT | REFUSED"
}
```

Status semantics:

- `ok` — every requested secondary completed `status="ok"` and wrote a usable run directory.
- `dry_run` — plan emitted; no `--write` / `--allow-network-fetch`; no engine calls beyond preflight classification.
- `partial` — at least one secondary `status="error"`; batch survived.
- `failed` — batch-level setup or export error; not survivable per-secondary.
- `timeout` — operator budget reached; final samples captured.
- `refused` — pre-launch gate refused (missing budget, missing primaries, etc.).
- `blocked_process_conflict` — another protected process is running; runner did not touch state.

---

## 15. Phase Map: A through F

| Phase | Scope | Deliverable |
|---|---|---|
| **A (this doc)** | Read-only audit + scoping | docs-only PR |
| **B** | Runner scaffold + tests | `stackbuilder_workbook_runner.py` skeleton, dry-run only, AST guard against `dash` / `yfinance` / `stackbuilder` at top level, JSON-stdout contract test, fake-engine continuation test. No real StackBuilder engine call. |
| **C** | Supervised smoke | 1–2 secondaries, isolated `--outdir` under `logs/phase_6i_XX_smoke_run/<ts>/output_dir/`. Verify first `selected_build.json` behavior and same-K latest-wins behavior. |
| **D** | Benchmark sweep | Representative secondary classes; cell-level verdicts; verify selection across different K levels. |
| **E** | Canonical authorization | Operator-authorized write on selected secondaries to canonical `output/stackbuilder/<SEC>/...` + atomic `selected_build.json` update. |
| **F** | Downstream integration | TrafficFlow / Confluence / website handoff against the `selected_build.json` pointer. |

**Only Phase A is being done in this PR.**

---

## 16. Risks and Open Questions

| Risk | Notes |
|---|---|
| StackBuilder is not zero-network | `load_secondary_prices` falls through to `yf.download` on cache miss. Runner must gate this behind `--allow-network-fetch`. |
| Importing `stackbuilder` imports `onepass` | When `from onepass import load_signal_library` succeeds (line 211), OnePass's import-time side effects (Dash, yfinance, logging, stdout print) follow. Future runner must wrap `import stackbuilder` in `contextlib.redirect_stdout`. |
| Module globals mutated per call | `OUTPUT_FORMAT`, `SIGNAL_LIB_DIR_RUNTIME`, `VERBOSE`, `COMBINE_INTERSECTION` are mutated by every `run_for_secondary` invocation. In-process repeated calls must isolate / restore; subprocess (per-secondary `ProcessPoolExecutor`) avoids this entirely. |
| `_DEPRECATED_CLI_FLAGS` | `--alpha`, `--min-marginal-capture`, `--fail-on-missing-cache` are recorded as metadata only; the runner should not surface them. |
| Existing same-name final directory deleted | `shutil.rmtree(final_outdir, ignore_errors=True)` at `stackbuilder.py:2501-2502` deletes prior content when the new run produces the same `final_name`. Historical preservation relies on `final_name` differing (it encodes members and seed policy). Runner should not introduce naming collisions. |
| Non-atomic `write_table` / `write_json` | A SIGKILL mid-run can leave partial files. Runner-controlled canonical writes (e.g. `selected_build.json`) must use temp + `os.replace`. |
| Engine `SystemExit` semantics | `phase2` / `phase3` raise `SystemExit` on cohort or trigger failures. Runner must convert these to per-secondary `status="error"` rather than terminating the batch. |
| Stdout contamination | The engine prints `[PHASE2]`, `[PHASE3]`, `[COMPLETE]`, `[RESULT]`, `[OUTPUT]`, `[VALIDATION]`, `[FASTPATH]`, `[CLEANUP]`, `[ERROR]`, `[STRICT]`, `[WARN]`, `[INFO]`, plus tqdm. Runner must wrap engine calls in `contextlib.redirect_stdout(io.StringIO())` and emit one JSON object to its own stdout at the very end. |
| Network fetch parallelism | `_fetch_secondary_from_yf(threads=True)` (`stackbuilder.py:510`) uses yfinance's internal threading. Combined with `--jobs > 1`, network behavior may be non-deterministic. Runner should warn when `--jobs > 1` and `--allow-network-fetch=True`. |
| `MASTER_TICKERS_PATH` env override | `YF_MASTER_TICKERS_PATH` and `MASTER_TICKERS_PATH` both override. Runner should report effective path in the JSON envelope. |
| Phase 5C-2c locked contract | Validation must run BEFORE the manifest write. Runner must not bypass `_prepare_stackbuilder_durable_validation`; failure of the fallback-write path propagates and the temp dir is removed. |

Open questions for Phase B / C:
- How to thread `--operator-budget-label` into `run_manifest.json` for audit?
- Should `selected_build.json` updates be opt-in via `--update-selected` separately from `--write`?
- How does the runner reconcile a Dash-launched run's `selected_build.json` with a CLI-launched run's? Operator policy may require Dash runs to be quarantined.
- Should the runner refuse to launch when `output/stackbuilder/_progress` already contains a `running` job for the same secondary?

---

## 17. Acceptance Criteria for Phase B

Phase B (runner scaffold + tests) is accepted only if:

- New file `stackbuilder_workbook_runner.py` exists and is the only added Python file.
- New test file under `test_scripts/` covers, at minimum:
  - AST guard: no top-level `import stackbuilder`, `import dash`, `import yfinance`, `import plotly`, `import dash_bootstrap_components`, `import impactsearch`, `import pandas` (except where the runner uses pandas itself; if so, document the exception and add a top-of-file justification).
  - `parse_args` returns defaults matching the locked v1 values.
  - `resolve_secondaries` and `resolve_primaries` preserve operator intent (no hidden full-universe fallback).
  - `--write` requires `--allow-network-fetch` (or document the precise gate semantics).
  - Process-conflict guard.
  - Stdout cleanliness despite `stackbuilder` import-time prints.
  - Per-secondary continuation under a fake engine callable.
  - Atomic `selected_build.json` write (temp + `os.replace`).
  - Refusal to launch without `--duration-budget-minutes` and `--operator-budget-label`.
- No tracked production file (`stackbuilder.py`, `onepass.py`, `impactsearch.py`, `provenance_manifest.py`, `validation_engine.py`, `canonical_scoring.py`) modified.
- All tests pass on the pinned interpreter.
- Canonical artifact SHA-256 fingerprints (the post-Phase-6I-67 `output/onepass/onepass.xlsx` and the unchanged `output/impactsearch/SPY_analysis.xlsx`) remain unchanged.

---

## 18. Appendix: Evidence Table

| Topic | File | Line(s) | Finding |
|---|---|---|---|
| ContextVar collector declaration | `stackbuilder.py` | 82–84 | `_INPUT_COLLECTOR_VAR` default `None`, one per Context. |
| Collector lifecycle (start / record / finalize) | `stackbuilder.py` | 87–177 | Token-based set / reset; finalize returns hashes/legacy/missing counts. |
| Context propagation into executor | `stackbuilder.py` | 180–190 | `_submit_with_context` uses `contextvars.copy_context().run`. |
| yfinance lazy import | `stackbuilder.py` | 192–195 | `yf=None` fallback; not zero-network. |
| OnePass `load_signal_library` import | `stackbuilder.py` | 210–214 | Module-level import triggers OnePass side effects if present. |
| Project dir anchor | `stackbuilder.py` | 220 | `Path(__file__).resolve().parent` is the project root. |
| Master ticker env overrides | `stackbuilder.py` | 226 | `YF_MASTER_TICKERS_PATH` / `MASTER_TICKERS_PATH`. |
| `OUTPUT_FORMAT` global | `stackbuilder.py` | 237 | `STACKBUILDER_OUTPUT_FORMAT` env; mutated by `main` and `run_for_secondary`. |
| `DEFAULT_GRACE_DAYS` | `stackbuilder.py` | 238 | `IMPACT_CALENDAR_GRACE_DAYS` env or 10. |
| Effective grace resolver | `stackbuilder.py` | 241–251 | `None` → `DEFAULT_GRACE_DAYS`; explicit int honored (including 0). |
| Atomic progress write | `stackbuilder.py` | 264–289 | Temp + `os.replace`; preserves prior keys. |
| Non-atomic JSON / table write | `stackbuilder.py` | 295–298, 398–413 | Plain writes; xlsx → csv fallback on error. |
| Output artifact enumeration | `stackbuilder.py` | 380–396 | Names: `rank_all`, `rank_direct`, `rank_inverse`, `cohort`, `combo_leaderboard`, `summary.json`, `search_stats.json`. |
| Master universe loader | `stackbuilder.py` | 456–461 | Read + split + uppercase; `[]` on missing file. |
| Library discovery | `stackbuilder.py` | 463–477 | Flat + 2-letter sharded glob. |
| `primary_universe` semantics | `stackbuilder.py` | 479–502 | Explicit list (incl. empty) honored; `None` → master / discovery. |
| `_fetch_secondary_from_yf` | `stackbuilder.py` | 506–528 | `period='max'`, `auto_adjust=False`. |
| Secondary price loader | `stackbuilder.py` | 530–556 | Cache-first; yfinance fallback. |
| ImpactSearch XLSX rejection contract | `stackbuilder.py` | 583–700 | strict/non-strict matrix; staleness gate; populates `rejection_out`. |
| Central verified signal-library loader | `provenance_manifest.py` | 1036–1119 | `load_verified_signal_library` uses manifest verification and cache-enabled content hash path. |
| `load_lib_or_none` provenance pin | `stackbuilder.py` | 743–754 | Records every successful load. |
| Spymaster PKL non-dependency | `stackbuilder.py`, `trafficflow.py` | `stackbuilder.py` static search; `trafficflow.py:86–87`, `trafficflow.py:1442–1458` | StackBuilder does not read `cache/results` Spymaster PKLs; TrafficFlow does downstream. |
| Apply-signals grace plumbing | `stackbuilder.py` | 757–825 | `_effective_grace_days(grace_days)`; `IMPACT_DEBUG_ALIGN` env. |
| `metrics_from_captures` canonical delegation | `stackbuilder.py` | 827–859 | `risk_free=5.0`, `periods=252`, `ddof=1`. |
| `phase1_preflight` | `stackbuilder.py` | 862–892 | Fatal SystemExit on empty primaries unless `prefer_impact_xlsx`. |
| `phase2_rank_all` XLSX fast-path skip on cutoff | `stackbuilder.py` | 1099 | `data_available_through is None` requirement. |
| Strict-manifests + no primaries fatal | `stackbuilder.py` | 1116–1127 | Engine raises `SystemExit`. |
| `phase2_rank_all` slow path | `stackbuilder.py` | 1253–1354 | ThreadPoolExecutor with `_submit_with_context`. |
| `phase3_build_stacks` | `stackbuilder.py` | 1487–1889 | Beam / exhaustive; monotone gate; patience; validation collector. |
| Cohort auto-both-modes | `stackbuilder.py` | 1580–1582 | Triggered when ticker appears in top and bottom. |
| `exhaustive_k` semantics | `stackbuilder.py` | 1683–1772, 1791 | Falls back to exhaustive when K ≤ exhaustive_k. |
| `run_dash` Dash UI | `stackbuilder.py` | 1892–2307 | Lazy import; widget defaults documented in §4. |
| `run_dash` per-secondary thread | `stackbuilder.py` | 2099–2114 | daemon thread; primaries_snapshot captured per iteration. |
| `run_for_secondary` orchestration | `stackbuilder.py` | 2309–2599 | Mutates globals; opens / closes ContextVar collector; writes initial + final manifest. |
| Existing final dir deletion | `stackbuilder.py` | 2501–2502 | `shutil.rmtree(final_outdir, ignore_errors=True)`. |
| Locked validation summary keys | `stackbuilder.py` | 2606–2617 | 10 keys gated before manifest write. |
| Validation adapter | `stackbuilder.py` | 2768+ | XLSX fast-path forced off; cutoff threaded through. |
| `_prepare_stackbuilder_durable_validation` | `stackbuilder.py` | 3090+ | Runs before manifest publish; fallback-write failures propagate. |
| Recent change-history audit | `git log` | since 2026-05-01 | 11 commits, aggregate +1,884 / -232, net +1,652 lines. |
| Deprecated CLI flags | `stackbuilder.py` | 3267–3288 | `--alpha`, `--min-marginal-capture`, `--fail-on-missing-cache`. |
| `parse_args` | `stackbuilder.py` | 3311–3379 | Full CLI surface. |
| `main` global mutations | `stackbuilder.py` | 3383–3387 | Sets `OUTPUT_FORMAT`, `SIGNAL_LIB_DIR_RUNTIME`, `VERBOSE`, `COMBINE_INTERSECTION`. |
| Bare invocation → Dash | `stackbuilder.py` | 3394–3400 | Default behavior when no `--secondary*` is supplied. |
| `--jobs > 1` parallelism | `stackbuilder.py` | 3417–3422 | `ProcessPoolExecutor(max_workers=...)`. |
| `__main__` guard | `stackbuilder.py` | 3426 | `if __name__ == '__main__': main()`. |

---

## Phase 6I-73 Policy Update: Sharpe Removal + Bounded Inverse Rescoring

Operator-locked policy update applied as Phase B hardening before the Phase C smoke retry. Affects the engine, the runner, and the ImpactSearch export sort. No durable validation surface, no Phase 5C contract files, and no other engine were modified.

### 1. Sharpe is no longer a selection criterion

- **Total Capture is the only supported selection criterion** across StackBuilder's seed, optimize, rank, sort, K=1 winner, beam/exhaustive K>=2 ordering, monotone gate, and selected-build tiebreakers.
- **Sharpe Ratio remains displayed** wherever the engine already computes it (workbook columns, `combo_leaderboard`, `summary.json`, manifest `output_schema`). Sharpe must not drive selection in any new code path.
- Engine CLI: `--seed-by` and `--optimize-by` accept only `total_capture` (legacy `sharpe` values raise an argparse error at parse time). The Dash UI radio is collapsed to a single `Total Capture` option.
- Runner CLI: `stackbuilder_workbook_runner.py --seed-by sharpe` and `--optimize-by sharpe` are refused at parse time; `--optimize-by auto` resolves deterministically to `total_capture`.
- Selected-build policy (`SELECTION_POLICY` = `v2.total_capture_then_latest`): operator pin → highest Total Capture (with tolerance) → latest. **No Sharpe tiebreaker.** The prior `v1.total_capture_then_sharpe_then_latest` label is retired.

### 2. Bounded inverse rescoring

- The XLSX fast-path full-universe inverse rescore loop is **removed**. Phase 2 no longer calls `_score_primary_from_signals(..., mode="I")` per primary on the XLSX cohort.
- The slow path's `_score_primary_both_modes` is replaced with direct-only scoring; the inverse cohort is derived afterward from `rank_direct`.
- The bottom_n inverse candidate cohort is now derived directly from the **most-negative `Total Capture (%)` rows** of `rank_direct` via the new helper `_build_bounded_inverse_cohort`. Sign-flipped Total Capture / Avg Daily Capture present as positive inverse-candidate magnitudes; `Sharpe Ratio` and `p-Value` are **NaN** at the cohort layer.
- K=1 winner selection consumes Total Capture only. **At most one** `_score_primary_from_signals(..., mode="I")` call may occur per build, only if a future caller explicitly requests an accurate inverse rescore for the K=1 winner; phase3's existing `_combined_metrics_signals` path already produces accurate K=1 metrics from aligned inverse signals without that call.
- K>=2 stack rows continue to compute accurate combined Sharpe / p-Value from combined signal series (unchanged behavior in `phase3_build_stacks` → `_combined_metrics_signals`).

### 3. `rank_inverse.*` is no longer a produced artifact

- `_build_output_artifacts` enumerates only `rank_all`, `rank_direct`, `cohort`, `combo_leaderboard` (plus `summary.json`, `search_stats.json`).
- `run_for_secondary` no longer writes a `rank_inverse` entry into the manifest `outputs` map.
- `phase2_rank_all` writes only `rank_all` and `rank_direct` to disk. The bounded inverse cohort remains an in-memory frame passed to `phase3_build_stacks`.
- The summarize-run-dir helper (`stackbuilder_workbook_runner.summarize_stackbuilder_run_dir`) no longer enumerates `rank_inverse` candidates.

### 4. ImpactSearch export sort

- `impactsearch.export_results_to_excel` (and the corresponding "no preexisting" branch) now sort the output workbook by `Total Capture (%)` descending. The prior `Sharpe Ratio` sort is removed.
- StackBuilder retains a defensive `Total Capture (%)` re-sort on XLSX load, so existing workbooks remain consumable.

### 5. Cohort display NaN policy

Bottom_n inverse cohort display rows may carry `NaN` Sharpe / `NaN` p-Value because the cohort is composed of placeholder rows derived from negative direct rows, not from accurate inverse-mode metrics. The K=1 leaderboard row picks up accurate Sharpe / p-Value via `_combined_metrics_signals` regardless.

### 6. Out-of-scope

- Durable validation scope (Phase 5C contract files) is **not** touched in this PR. A future PR may revisit validation surfaces if needed.
- Phase C smoke retry will land in a separate PR after Phase 6I-73 merges.
- Phase D benchmark remains a separate authorized task.

## Phase 6I-75 Runtime Recovery Update

Recovery follow-up to the Phase 6I-74 supervised smoke timeout. Three
runtime boundaries are restored or added; no Phase 6I-73 scoring policy
is touched (Total Capture remains the sole selection criterion; Sharpe
remains display-only; the Phase 5C fail-closed durable-validation
contract remains the default).

### A. Consumer-only signal-library loader

StackBuilder is a consumer engine. The Phase 3B-1 path routed library
loads through `onepass.load_signal_library`, which in turn called
`load_verified_signal_library` with manifest verification; provenance
mismatches emitted a "Forcing rebuild" notice that in the Phase 6I-74
smoke ran ~229,201 times across the 4-secondary run.

Phase 6I-75 removes the OnePass call path from StackBuilder entirely:

- `from onepass import load_signal_library` is gone; the
  `_try_import()` slot is permanently bound to `None`.
- `load_signal_library_for_stackbuilder(ticker)` is the new consumer
  loader. It globs the stable signal-library directory, opens the
  first readable PKL with a direct `pickle.load`, validates the
  minimal payload StackBuilder needs (1D `primary_signals` of the same
  length as `dates`), and returns the dict.
- Loads are memoized per-run on both success and failure in
  `_CONSUMER_LOADER_CACHE`, keyed by
  `(ticker, candidate_path, mtime_ns, size)`. Repeated calls for the
  same ticker do not re-read the PKL.
- Diagnostics use the `[STACKBUILDER:library_missing]`,
  `[STACKBUILDER:library_unreadable]`, and
  `[STACKBUILDER:library_invalid]` prefix family. No `[ONEPASS:*]`
  tag, no "Forcing rebuild" string, no rebuild side effect.
- Manifest-level mismatches no longer reject a readable library in
  consumer mode (the payload is what matters; manifest provenance is
  recorded by `_record_input_lib` but does not gate the load).
- `fallback_load_signal_library` and `load_lib_or_none` are thin
  aliases over `load_signal_library_for_stackbuilder`. Every existing
  call site (the K-search hot path, `_load_primary_signals`,
  `_signals_aligned_and_mask`, `_captures_for`) still works without
  modification.

The loader does not fetch from yfinance, write to `signal_library/`,
modify any manifest, or call any other engine. A unit test
(`test_consumer_loader_does_not_write_to_signal_lib_dir`) verifies the
file-stat invariants on every load attempt, including missing-library
and invalid-payload paths.

### B. Phase3 hot-path decomposition (measurement-only)

Spec Part 2 authorized implementing a `_metrics_from_captures_fast`
helper only if a synthetic measurement showed ≤18 ms per combo with
semantic parity. The harness lives at
`test_scripts/bench_phase_6i75_hotpath_decomposition.py` and times each
component of `_combined_metrics_signals` independently against a
30-year synthetic daily series with K=4 members.

Result (median over 30 repetitions, pinned spyproject2 interpreter):

| Component | Median ms / combo | Share of full pipeline |
|---|---|---|
| `_combine_signals` (canonical consensus) | 180.83 | 97.76% |
| `_captures_from_signals` | 1.28 | 0.69% |
| `trigger_mask = comb_sig.isin(...)` | 0.16 | 0.08% |
| `metrics_from_captures` (full) | 0.73 | 0.40% |
| `_canonical_score_captures` only | 0.68 | 0.37% |
| `metrics_to_legacy_dict` only | 0.002 | 0.001% |
| `_combined_metrics_signals` total | 184.98 | 100% |

`metrics_from_captures` already runs in well under 1 ms per call (≈24×
faster than the 18 ms/combo target). Implementing
`_metrics_from_captures_fast` would save < 0.5% of per-combo time and
introduce semantic-parity risk for no operational benefit. The fast
helper is therefore **not** implemented.

The dominant cost (~98%) is the canonical consensus combine
(`combine_consensus_signals`). That helper is out of scope for Part 2
under the spec; a future authorized phase may revisit it. The
measurement JSON (with per-sample timings + verdict block) is
preserved at
`logs/phase_6i75_stackbuilder_runtime_recovery/<SESSION_DIR>/phase_6i75_hotpath_decomposition.json`.

### C. `--skip-durable-validation` flag

A new operator-explicit CLI flag (default off) lets a supervised
operator skip the Phase 5C durable validation surface on a per-run
basis. Default behavior — and therefore the locked Phase 5C
fail-closed contract — is unchanged when the flag is absent.

Engine surface (`stackbuilder.py`):

- `parse_args` exposes `--skip-durable-validation`
  (`store_true`, default `False`).
- `_stable_cli_args_subset` records the flag so the manifest
  fingerprint distinguishes skip from no-skip re-runs.
- In `run_for_secondary`, when the flag is set the call to
  `_prepare_stackbuilder_durable_validation` is skipped entirely:
  no walk-forward folds, no `evaluate_candidate` loop, no sidecar
  write. The function `_build_skipped_validation_summary` returns
  a complete locked-10 summary with
  `validation_status="skipped"`, `validation_artifact_path=None`,
  `validation_artifact_hash=None`, and the other numeric/path
  fields `None`. The locked-keys gate
  (`_validate_stackbuilder_validation_summary`) continues to pass.
- The manifest carries two additional top-level keys to make the
  skip unambiguous on the consumer side:
  `durable_validation_status` is `"skipped"` (with the flag) or
  `"ran"` (default), and `durable_validation_skip_reason` is
  `"operator_flag"` on the skip path or `None` otherwise.
  Fabricating `validation_artifact_path` or
  `validation_artifact_hash` on the skip path is forbidden.
- The completion line emits `Validation: SKIPPED (operator_flag).
  No durable validation sidecar was written.` on the skip path.

Runner surface (`stackbuilder_workbook_runner.py`):

- `parse_args` exposes `--skip-durable-validation`
  (`store_true`, default `False`).
- `_effective_config` records the flag.
- `per_secondary_plan` entries carry the flag for per-secondary
  visibility in the dry-run JSON.
- `build_stackbuilder_args_namespace` threads
  `skip_durable_validation` into the `SimpleNamespace` handed to
  `stackbuilder.run_for_secondary`.

### D. What changed and what didn't

Changed:

- `stackbuilder.py` — consumer-only loader, skip-validation gate,
  CLI flag.
- `stackbuilder_workbook_runner.py` — runner CLI plumbing for the
  skip flag.
- `test_scripts/test_stackbuilder_phase_6i75_consumer_loader.py` —
  24 new focused tests covering loader semantics, memoization,
  diagnostics, skip-summary schema, and runner plumbing.
- `test_scripts/bench_phase_6i75_hotpath_decomposition.py` —
  synthetic measurement harness.

Unchanged:

- Phase 6I-73 selection policy: Total Capture only; Sharpe
  display-only.
- Phase 5C durable-validation default (fail-closed remains the
  default; the skip is opt-in only).
- Validation engine (`validation_engine.py`) and the Phase 5C
  contract files.
- StackBuilder phase3 hot-path code (no `_metrics_from_captures_fast`
  was added; measurement did not support it).
- ImpactSearch XLSX provenance verification path (still strict).
- Output-manifest schema beyond the two new explicit skip-marker
  keys.
