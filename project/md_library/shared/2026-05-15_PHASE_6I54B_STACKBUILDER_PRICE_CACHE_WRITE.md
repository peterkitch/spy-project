# Phase 6I-54b: supervised StackBuilder price-cache writer + write for the 6 ready tickers

**Date:** 2026-05-15
**Base commit (main):** `13a707f` (Phase 6I-54a squash-merge)
**Branch:** `phase-6i-54b-stackbuilder-price-cache-write`
**Status:** **Authorized write completed.** 6 CSV files landed in `price_cache/daily/`. Other 5 production roots unchanged. **Do not merge** until operator approval.

`<PINNED_PYTHON> = C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`

---

## 1. Purpose + outcome

Phase 6I-54a identified 6 of the 25 Phase 6I-52 pilot tickers as `use_existing_signal_cache` candidates — local `cache/results/<TICKER>_precomputed_results.pkl` files exist + manifests verify + `params.price_source="Close"`, so `price_cache/daily/<TICKER>` can be populated **without any network access**. Phase 6I-54b implements that transformation and runs it under the operator's explicit single-key `--write` authorization.

**Outcome:** 6 / 6 ready tickers (SPY, AAPL, JNJ, WMT, HD, MCD) verified + written. `price_cache/daily/` now has 6 CSV files. The five documented production roots (`cache/results`, `cache/status`, `output/research_artifacts`, `output/stackbuilder`, `signal_library/data/stable`) are unchanged. The Phase 6I-53 preflight re-runs as `pass_count=6 / skip_count=19` — exactly the expected flip from the pre-Phase-6I-54b `pass_count=0 / skip_count=25`.

## 2. Authorization framing

`stackbuilder.py` has **NO `--write` flag** and does **NOT use `PRJCT9_AUTOMATION_WRITE_AUTH`** (per Phase 6I-52 amendment-1). The Phase 6H-5 / 6I-25 / 6I-31 two-key gate applies to the Confluence patch writer / signal-library promotion writer / daily-board automation writer — NOT to anything in the StackBuilder layer or its secondary price cache.

Phase 6I-54b's writer therefore uses **single-key `--write` authorization** (the same precedent as the Phase 6E-5 `signal_engine_cache_refresher`). The operator's authorization for this phase came from the explicit prompt directing the supervised write of the 6 ready tickers.

## 3. What was added

### Module

`project/stackbuilder_price_cache_writer.py`

- Public entry: `build_price_cache_write_report(tickers, *, signal_cache_dir, stackbuilder_price_cache_dir, format, write, overwrite, verified_loader, execution_log_path) -> dict`.
- CLI: `--tickers <CSV>` (required), `--signal-cache-dir`, `--stackbuilder-price-cache-dir`, `--format parquet|csv` (default parquet), `--write`, `--overwrite`, `--execution-log <JSONL_PATH>`.
- **Dry-run by default**: without `--write`, no file is created; the verification cascade still runs.
- **Single-key authorization** (`--write`); no `PRJCT9_AUTOMATION_WRITE_AUTH` env var.
- **Atomic write**: writes to `<output_path>.tmp` then `os.replace`'s into place; cleans the temp on failure.
- **No-overwrite default**: existing files skip with `output_already_exists_no_overwrite` issue code; `--overwrite` flips this.
- **Parquet-engine-unavailable handling**: when pandas raises `ImportError` for no pyarrow/fastparquet, the per-ticker row gets `parquet_engine_unavailable` + `wrote_file=False` (does NOT crash). Operator re-runs with `--format csv` to use the CSV branch.
- **Per-ticker verification cascade** (mandatory):
  - source PKL exists, manifest sidecar exists, **verified loader** returns `result.ok=True` (NOT raw `pickle.load`);
  - manifest `params.price_source="Close"`;
  - `data['preprocessed_data']` is a DataFrame with a `Close` column and a `DatetimeIndex`;
  - Close is numeric, non-empty, no nulls; index is sorted ASC.
- **Per-ticker provenance recorded**: `source_producer_engine`, `source_engine_version`. Aggregate `provenance_summary` block.
- **`--execution-log` production-root path guard**: rejects paths inside the 5 OTHER production roots; `price_cache/daily/` is allowed (but discouraged).

### Tests

`project/test_scripts/test_stackbuilder_price_cache_writer.py` — 23 focused tests, all passing.

Coverage:

| # | Test | Pins |
|---|---|---|
| 1 | `test_schema_format_and_issue_codes_are_stable` | Schema + format + 14 stable issue codes. |
| 2 | `test_dry_run_writes_no_file` | Dry-run produces verification report, no output file. |
| 3 | `test_authorized_csv_write_creates_file` | `--write` creates `<T>.csv` with Date,Close header + correct rows. |
| 4 | `test_csv_write_is_atomic_no_temp_left` | No `.tmp` sibling survives a successful write. |
| 5 | `test_no_overwrite_default` | Existing file → `output_already_exists_no_overwrite` blocker; pre-existing content untouched. |
| 6 | `test_overwrite_explicit_allows_replacement` | `--overwrite=True` writes over existing file. |
| 7 | `test_parquet_unavailable_surfaces_clean_issue_code` | No pyarrow/fastparquet → `parquet_engine_unavailable`, no crash. |
| 8 | `test_missing_pkl_skips` | Missing PKL → `source_pkl_missing`. |
| 9 | `test_missing_manifest_skips` | Missing manifest → `manifest_missing`. |
| 10 | `test_non_close_price_source_skips` | `price_source != "Close"` → `price_source_not_close`. |
| 11 | `test_loader_failure_skips` | `result.ok=False` → `verified_loader_failed`. |
| 12 | `test_no_preprocessed_data_skips` | No `preprocessed_data` key → blocker. |
| 13 | `test_no_close_column_skips` | DataFrame without Close → blocker. |
| 14 | `test_non_datetime_index_skips` | Non-DatetimeIndex → blocker. |
| 15 | `test_non_numeric_close_skips` | String Close → blocker. |
| 16 | `test_close_with_nulls_skips` | NaN Close → blocker. |
| 17 | `test_unsorted_index_skips` | Unsorted index → blocker. |
| 18 | `test_mixed_provenance_grouping` | 2 producer/version groups surface separately. |
| 19 | `test_no_forbidden_top_level_imports` | No `pickle` / `subprocess` / `yfinance` / `stackbuilder` / engine / writer imports. |
| 20 | `test_module_source_has_no_raw_pickle_load` | AST-level: no `pickle.load(...)` or `pickle_load_compat(...)` call expression anywhere. |
| 21 | `test_execution_log_rejects_other_production_roots` | `--execution-log` rejects all 5 OTHER production roots. |
| 22 | `test_execution_log_allows_md_library_path` | `md_library/shared/...` paths allowed; JSONL is well-formed. |
| 23 | `test_cli_rejects_empty_tickers` | `--tickers " , "` → rc=2 `no_tickers_supplied`. |

**Combined Phase 6I planner/policy/preflight/rebuild-planner/writer regression: 124 / 124 tests pass** (16 from 6I-50 + 23 from 6I-51 + 23 from 6I-52 + 16 from 6I-53 + 23 from 6I-54a + 23 from 6I-54b).

The Phase 6I-53 production-state smoke + Phase 6I-54a production-state smoke were both updated to be **state-aware**: they now recognize both pre-Phase-6I-54b (no `price_cache/daily/`) and post-Phase-6I-54b (6 CSV files for the ready tickers) states, with appropriate assertions per branch.

## 4. Dry-run verdict

```
<PINNED_PYTHON> stackbuilder_price_cache_writer.py \
    --tickers SPY,AAPL,JNJ,WMT,HD,MCD \
    --signal-cache-dir cache/results \
    --stackbuilder-price-cache-dir price_cache/daily \
    --format csv \
    > md_library/shared/2026-05-15_PHASE_6I54B_DRYRUN.json
```

| Field | Value |
|---|---|
| `write` | `false` |
| `ticker_count` | 6 |
| `verification_pass_count` | 6 |
| `write_count` | 0 |

| Ticker | rows_read | first_date | last_date | provenance |
|---|---|---|---|---|
| SPY | 8380 | 1993-01-29 | 2026-05-14 | `signal_engine_cache_refresher / 6E-5.0.0` |
| AAPL | 11439 | 1980-12-12 | 2026-05-04 | `spymaster / 1.0.0` |
| JNJ | 16200 | 1962-01-02 | 2026-05-14 | `signal_engine_cache_refresher / 6E-5.0.0` |
| WMT | 13533 | 1972-08-25 | 2026-05-04 | `spymaster / 1.0.0` |
| HD | 11244 | 1981-09-22 | 2026-05-04 | `spymaster / 1.0.0` |
| MCD | 15057 | 1966-07-05 | 2026-05-04 | `spymaster / 1.0.0` |

All 6 passed verification. **No file created**; `price_cache/daily/` still missing.

## 5. Authorized write verdict

`pyarrow`/`fastparquet` is not installed in `spyproject2`. The writer detected this via the dry-run's parquet engine attempt and the operator selected `--format csv` for the authorized run.

```
<PINNED_PYTHON> stackbuilder_price_cache_writer.py \
    --tickers SPY,AAPL,JNJ,WMT,HD,MCD \
    --signal-cache-dir cache/results \
    --stackbuilder-price-cache-dir price_cache/daily \
    --format csv \
    --write \
    --execution-log md_library/shared/2026-05-15_PHASE_6I54B_WRITER_EXECUTION_LOG.jsonl \
    > md_library/shared/2026-05-15_PHASE_6I54B_WRITE.json
```

| Field | Value |
|---|---|
| `write` | `true` |
| `ticker_count` | 6 |
| `verification_pass_count` | 6 |
| `write_count` | **6** |

Every ticker wrote successfully. `rows_written = rows_read` for all 6. Each output file uses atomic write (no `.tmp` siblings survived).

### Provenance summary

| Producer engine | Engine version | Ticker count | Tickers |
|---|---|---|---|
| `signal_engine_cache_refresher` | `6E-5.0.0` | 2 | `JNJ, SPY` |
| `spymaster` | `1.0.0` | 4 | `AAPL, HD, MCD, WMT` |

Each file was verified independently via `provenance_manifest.load_verified_pickle_artifact` (NOT raw `pickle.load`); both producers' PKLs pass the cascade and yield clean Close series.

### Sample SPY.csv

```
Date,Close
1993-01-29,43.9375
1993-02-01,44.25
...
2026-05-12,738.1799926757812
2026-05-13,742.3099975585938
2026-05-14,748.1699829101562
```

8381 lines = 1 header + 8380 data rows.

## 6. Production-root accounting

| Root | Pre-run | Post-run | Diff |
|---|---|---|---|
| `cache/results` | 3239 | 3239 | 0 |
| `cache/status` | 1634 | 1634 | 0 |
| `output/research_artifacts` | 35 | 35 | 0 |
| `output/stackbuilder` | 5229 | 5229 | 0 |
| `signal_library/data/stable` | 72899 | 72899 | 0 |
| `price_cache/daily` | missing | **6** | **+6** |
| **Combined (5 documented)** | **83036** | **83036** | **0** |

**`price_cache/daily/` files added** (exactly 6, atomic writes, no `.tmp` siblings):

- `price_cache/daily/AAPL.csv`
- `price_cache/daily/HD.csv`
- `price_cache/daily/JNJ.csv`
- `price_cache/daily/MCD.csv`
- `price_cache/daily/SPY.csv`
- `price_cache/daily/WMT.csv`

## 7. Post-write Phase 6I-53 preflight verdict

```
<PINNED_PYTHON> confluence_stackbuilder_pilot_preflight.py \
    --output md_library/shared/2026-05-15_PHASE_6I54B_POST_WRITE_PREFLIGHT.json
```

| Field | Pre-Phase-6I-54b | Post-Phase-6I-54b |
|---|---|---|
| `price_cache_dir_exists` | `False` | **`True`** |
| `pass_count` | **0** | **6** |
| `skip_count` | **25** | **19** |
| `tickers_passing_preflight` | `[]` | `[AAPL, HD, JNJ, MCD, SPY, WMT]` |
| `tickers_skipped_missing_cache` (first 5) | (all 25) | `[ADBE, AMD, AMZN, AVGO, BRK-B]` |

**The pass_count flipped from 0/25 to 6/25 exactly as the prompt's expected result specified.** The 19 remaining `needs_source_refresh` tickers still classify as `skip_missing_cache_would_fetch_yfinance`.

## 8. Post-write Phase 6I-54a planner verdict

```
<PINNED_PYTHON> stackbuilder_price_cache_rebuild_planner.py \
    --output md_library/shared/2026-05-15_PHASE_6I54B_POST_WRITE_REBUILD_PLAN.json
```

| Recommended action | Pre-Phase-6I-54b | Post-Phase-6I-54b |
|---|---|---|
| `use_existing_signal_cache` | 6 | **0** |
| `manual_review` | 0 | **6** (SPY, AAPL, JNJ, WMT, HD, MCD with blocker `stackbuilder_price_cache_already_present`) |
| `needs_source_refresh` | 19 | 19 |
| `needs_network_fetch` | 0 | 0 |

The 6 written tickers correctly flip from `use_existing_signal_cache` → `manual_review` because the Phase 6I-54a planner sees the `price_cache/daily/<TICKER>.csv` files now present and emits the `stackbuilder_price_cache_already_present` blocker (the operator decides overwrite vs keep). The 19 `needs_source_refresh` tickers are unchanged.

## 9. No production activity outside `price_cache/daily/` (confirmed)

- No `--write` to any of the 5 documented production roots.
- No `PRJCT9_AUTOMATION_WRITE_AUTH` set anywhere.
- No yfinance fetch (`yfinance` not imported; `--write` never reaches `stackbuilder._fetch_secondary_from_yf`).
- No `signal_engine_cache_refresher` invocation.
- No `signal_library_stable_promotion_writer` invocation.
- No `multiwindow_k_confluence_patch_writer` invocation.
- No `confluence_pipeline_runner` invocation.
- No `daily_board_automation_writer/executor` invocation.
- No StackBuilder invocation.
- No OnePass / ImpactSearch / TrafficFlow / Spymaster batch.
- No `subprocess` call (statically enforced).
- No raw `pickle.load` (statically enforced; all PKL reads go via `provenance_manifest.load_verified_pickle_artifact`).

## 10. Files added (8)

- `project/stackbuilder_price_cache_writer.py` — new writer module (~700 lines).
- `project/test_scripts/test_stackbuilder_price_cache_writer.py` — 23 focused tests.
- `project/md_library/shared/2026-05-15_PHASE_6I54B_STACKBUILDER_PRICE_CACHE_WRITE.md` (this doc).
- `project/md_library/shared/2026-05-15_PHASE_6I54B_STACKBUILDER_PRICE_CACHE_WRITE_EVIDENCE.json` — consolidated evidence.
- `project/md_library/shared/2026-05-15_PHASE_6I54B_DRYRUN.json` — raw dry-run JSON.
- `project/md_library/shared/2026-05-15_PHASE_6I54B_WRITE.json` — raw authorized-write JSON.
- `project/md_library/shared/2026-05-15_PHASE_6I54B_POST_WRITE_PREFLIGHT.json` — Phase 6I-53 preflight post-write.
- `project/md_library/shared/2026-05-15_PHASE_6I54B_POST_WRITE_REBUILD_PLAN.json` — Phase 6I-54a planner post-write.
- `project/md_library/shared/2026-05-15_PHASE_6I54B_WRITER_EXECUTION_LOG.jsonl` — writer JSONL execution log.

Two existing tests updated for state-awareness (post-Phase-6I-54b reality):
- `test_scripts/test_confluence_stackbuilder_pilot_preflight.py` — production-state smoke now recognizes pre + post states.
- `test_scripts/test_stackbuilder_price_cache_rebuild_planner.py` — production-state smoke now recognizes pre + post states.

And the 6 CSV files in `price_cache/daily/` (the authorized writes).

## 11. Next step

**Phase 6I-55 — supervised StackBuilder pilot batch (retry) for the 6 ready tickers.** The Phase 6I-53 preflight now reports `pass_count=6` for SPY, AAPL, JNJ, WMT, HD, MCD. The Phase 6I-52 locked StackBuilder commands can run against each of those 6 tickers without falling through to the yfinance fallback. The remaining 19 `needs_source_refresh` tickers still require a separately-authorized Phase 6E-5 source-cache refresh before they can join the rollout.

StackBuilder invocation is its own explicitly-authorized phase; Phase 6I-54b does NOT pre-authorize it. The Phase 6I-52 locked policy + 25-ticker pilot universe + Phase 6I-53 preflight + Phase 6I-54a/b cache-rebuild chain remain valid building blocks. The cache gap for the 6 ready tickers is now closed.
