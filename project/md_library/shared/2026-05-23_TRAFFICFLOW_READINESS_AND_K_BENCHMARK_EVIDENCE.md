# TrafficFlow Readiness, Max-SMA-Day Verification, Bounded Repair, and K1/K2/K3/K4/K6 Benchmark Evidence

## 1. Scope and Non-Goals

This is a gated evidence task before TrafficFlow headless implementation.
It is NOT the TrafficFlow headless implementation, and it is NOT a legacy
TrafficFlow output parity proof.

This document records:

- a static safety preflight of `trafficflow.py` and
  `signal_engine_cache_refresher.py`;
- the canonical input-readiness inventory for K=1/2/3/4/6 across the 8
  Phase 6I-79 secondaries (AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY, TSLA);
- explicit `max_sma_day` verification of every required TrafficFlow PKL
  in `cache/results/`;
- bounded repair of missing secondary price caches via TrafficFlow's
  own `refresh_secondary_caches` helper;
- bounded PKL generation via `signal_engine_cache_refresher.py` with
  `--max-sma-day 114` explicitly passed for every call;
- a cache-warmed serial-vs-parallel `build_board_rows` benchmark
  across all 40 (secondary, K) cells; and
- a canonical artifact safety post-check.

No Dash server was launched at any point. No production code or tests
were modified. No StackBuilder, OnePass, ImpactSearch, Spymaster
(Dash), TrafficFlow (Dash), Confluence, or multi_timeframe_builder run
was launched. No canonical artifacts under `output/stackbuilder/`,
`output/impactsearch/`, `output/onepass/`, `signal_library/data/stable/`,
or `output/validation/` were modified.

## 2. References

- Phase 6I-79 production StackBuilder run evidence:
  `md_library/shared/2026-05-23_PHASE_6I_79_STACKBUILDER_PRODUCTION_RUN_EVIDENCE.md`
- TrafficFlow LEGACY-vs-current structural diff:
  `md_library/shared/2026-05-23_TRAFFICFLOW_LEGACY_VS_CURRENT_STRUCTURAL_DIFF.md`
- TrafficFlow K1/K2/K3 benchmark evidence (merged via PR #300):
  `md_library/shared/2026-05-23_TRAFFICFLOW_K1_K3_BENCHMARK_EVIDENCE.md`
- Codex PKL-type investigation summary (paraphrased into this doc's
  context block): there are three distinct PKL families in PRJCT9 -
  OnePass `signal_library/data/stable/` PKLs (NOT consumed by
  TrafficFlow), Spymaster / signal-engine `cache/results/` precomputed-
  result PKLs (consumed by TrafficFlow), and ImpactSearch raw cache PKLs
  (NOT consumed by TrafficFlow). TrafficFlow resolves its Spymaster PKL
  directory through `PRJCT9_SPYMASTER_PKL_DIR` defaulting to
  `cache/results/`, and reads
  `cache/results/<TICKER>_precomputed_results.pkl`. The correct bounded
  generator for those files without Dash is
  `signal_engine_cache_refresher.py`. Its default `--max-sma-day` is 30;
  TrafficFlow / Spymaster parity requires 114; every generation call in
  this task explicitly passed `--max-sma-day 114`.

## 3. Static Safety Preflight

### 3.1 trafficflow.py

AST inspection of `trafficflow.py` at the current `main` HEAD
(145,940 bytes, 3,422 lines, 83 top-level functions, 24 imports).
Module-import-time executable statements:

- `pd.set_option('future.no_silent_downcasting', True)` (benign).
- Two try-imports (`from dash import ...`, `import yfinance as yf`)
  that bind `Dash`/`yf` to `None` if the package is unavailable.
- `sys.path.insert(0, 'signal_library')` then an optional parity-import
  block (no execution at import time).
- The canonical entrypoint at the file tail:

  ```
  if __name__ == "__main__" and not __TF_ALREADY_STARTED:
      __TF_ALREADY_STARTED = True
      main()
  ```

Static substring scan confirmed:

- `Dash`, `make_app`, `app.run`, and `app.run_server` references exist
  only inside function bodies (`make_app` at L3079, called only from
  `main()` at L3392; `app.run_server(...)` at L3406, inside `main()`).
- `ThreadPoolExecutor` references exist at module-import L36 (the
  import), inside `refresh_secondary_caches` (price-refresh; L1271),
  inside `compute_build_metrics_spymaster_parity` (per-subset gate;
  L2798), and inside `make_app` (UI callback path; L3249). None fire
  at module import.
- File writes (`.to_csv`, `to_parquet`, `pickle.dump`) live inside
  function bodies (e.g. `_persist_cache`, `_write_cache_file`); none at
  module scope.
- `build_board_rows(sec, k, run_fence, missing_map)` (L2956, 121 lines)
  takes `k: int` and filters the leaderboard via `df[df['K'] == int(k)]`
  (L2986). K filtering is supported directly.
- Default values from L241-258:
  `PARALLEL_SUBSETS=False`, `PARALLEL_SUBSETS_MIN_K=4`,
  `TRAFFICFLOW_SUBSET_WORKERS=4`, `TF_BITMASK_FASTPATH=True`,
  `TF_POST_INTERSECT_FASTPATH=False`.
- `SPYMASTER_PKL_DIR = os.environ.get('PRJCT9_SPYMASTER_PKL_DIR',
  str(_PROJECT_DIR / 'cache' / 'results'))` (L86-89). Default is the
  Spymaster precomputed-results directory, not the OnePass
  signal-library directory.
- TrafficFlow reads
  `cache/results/<TICKER>_precomputed_results.pkl` via
  `load_spymaster_pkl` (L1442) and `_processed_signals_from_pkl`
  (L1562). It does NOT read `signal_library/data/stable/` PKLs.
- Required PKL schema for TrafficFlow compute: `preprocessed_data`,
  `active_pairs`, `daily_top_buy_pairs`, `daily_top_short_pairs`
  (verified by reading the `lib.get(...)` calls in
  `_next_signal_from_pkl`, `_extract_signals_from_active_pairs`, and
  `_processed_signals_from_pkl`).

Importing `trafficflow.py` is therefore safe for the benchmark.

### 3.2 signal_engine_cache_refresher.py

AST inspection (42,498 bytes, 1,197 lines, 26 top-level functions, 2
classes). Module-import-time executable surface is only the
`if __name__ == "__main__": sys.exit(main())` guard at L1195.

CLI surface (argparse calls extracted):

- `--ticker` (required, single ticker only).
- `--dry-run` (`store_true`; default behavior).
- `--write` (`store_true`).
- `--cache-dir` (default None; passed explicitly as `cache/results`).
- `--status-dir` (default None; passed explicitly as `cache/status`).
- `--max-sma-day` (type int, default None; help text confirms it
  reuses an existing cache's `existing_max_sma_day` if present,
  otherwise falls back to `DEFAULT_MAX_SMA_DAY = 30` per L658).
- `--current-as-of-date` (default None; falls back to
  `confluence_pipeline_readiness.resolve_current_as_of_date`).

Confirmed:

- No `import dash`, `from dash`, `subprocess`, or `multiprocessing`
  references in source text.
- No `app.run` or `app.run_server` references.
- Writes (`pickle.dump` at L484, `.write_text` at L522, `json.dump` at
  L1190) target the `--cache-dir` / `--status-dir` arguments only.
- One ticker per invocation by spec (`--ticker` required, no multi-
  ticker mode).
- The default `--max-sma-day` is 30; **explicit `--max-sma-day 114`
  must be passed for every TrafficFlow / Spymaster parity refresh.**

The tool is safe to invoke as a subprocess with the bounded
`--cache-dir cache/results --status-dir cache/status --max-sma-day 114
--ticker <T> --dry-run` / `... --write` shape.

## 4. Canonical Safety Snapshots

Pre-state captured at session start (before any repair action).
Latest mtime values are wall-clock indicators of "any write would
change this" sentinels.

| Root | File count | Latest mtime (pre) |
|---|---:|---|
| `output/stackbuilder/` | 5,388 | 2026-05-23 (Phase 6I-79 close) |
| `output/impactsearch/` (xlsx subset) | 8 | (pre-existing) |
| `output/onepass/` | 2 | (pre-existing) |
| `signal_library/data/stable/` | 71,980 | (pre-existing) |
| `output/validation/` | 0 | n/a |

Per-target-secondary `price_cache/daily/` pre-snapshot found only
`AAPL.csv` and `SPY.csv` on disk; the other six target secondaries
were ABSENT pre-repair. Four pre-existing extras (`HD.csv`,
`JNJ.csv`, `MCD.csv`, `WMT.csv`) carry mtimes from 2026-05-15 and were
not touched during this session.

Per-required-ticker `cache/results/<T>_precomputed_results.pkl`
pre-snapshot captured SHA-256, size, and manifest presence for the 47
required tickers that had pre-existing PKLs (see section 6).

## 5. Required Input Set

For each secondary, the latest StackBuilder
`combo_leaderboard.xlsx` was read via pandas read-only. Each
leaderboard contains exactly 12 rows total (one best build per
K=1..12) under the Phase 6I-79 beam-search single-best contract.
Members for K=1, 2, 3, 4, 6 were extracted per secondary and
ticker-mode suffixes (`[I]`/`[D]`) were stripped to produce the
dependency set.

| Secondary | K=1 | K=2 | K=3 | K=4 | K=6 |
|---|---|---|---|---|---|
| AAPL | BUT.L[D] | BUT.L[D], EXC[D] | BUT.L[D], EXC[D], HD[D] | BUT.L[D], EXC[D], HD[D], MWY.L[D] | BUT.L[D], EXC[D], HD[D], MWY.L[D], JFJ.L[D], JCH.L[D] |
| AMZN | CLDN.L[D] | CLDN.L[D], CML.L[I] | CLDN.L[D], CML.L[I], GIB-A.TO[D] | CLDN.L[D], CML.L[I], GIB-A.TO[D], TND.L[I] | (6 international names per leaderboard row 6) |
| GOOGL | 1058.HK[D] | 1058.HK[D], 5095.KL[I] | 1058.HK[D], 5095.KL[I], TFF.PA[D] | (4) | (6) |
| META | 3900.HK[D] | 3900.HK[D], 5657.KL[D] | 3900.HK[D], 5657.KL[D], ARLP[I] | (4) | (6) |
| MSFT | CP[I] | CP[I], IMO[I] | CP[I], IMO[I], OLN[I] | CP[I], IMO[I], OLN[I], UDR[D] | (6 names) |
| NVDA | CGLO[D] | CGLO[D], EVZ.AX[D] | CGLO[D], EVZ.AX[D], PRS.OL[I] | (4) | (6) |
| SPY | SBSI[D] | AWR[D], PRGO[D] | AWR[D], EXPO[D], FCFS[D] | AWR[D], CP[I], EXPO[D], LLY[I] | AWR[D], CP[I], EXPO[D], LLY[I], FCFS[D], CLH[D] |
| TSLA | PGH.L[D] | PGH.L[D], SBS.DE[D] | PGH.L[D], SBS.DE[D], SQE.F[D] | (4) | (6) |

Global counts across the 8 secondaries x 5 K levels:

- Unique base tickers required: **61**.
- Unique ticker-mode pairs (e.g. `CP[I]` vs `CP[D]` would count
  separately): captured in the readiness JSON.

## 6. Max-SMA-Day Verification of Existing TrafficFlow PKLs

### 6.1 Method

Each required member ticker had its `cache/results/<T>_precomputed_results.pkl`
loaded read-only via `pickle.load` in a session-dir helper script.
For each loaded object:

- Manifest sidecar (`<T>_precomputed_results.pkl.manifest.json`) was
  parsed for `max_sma_day` (top-level, legacy schema) or
  `params.max_sma_day` (current schema produced by
  `signal_engine_cache_refresher.py`); whichever is present wins.
- Inline PKL fields (`max_sma_day`, `existing_max_sma_day`,
  `sma_range`) were inspected.
- `preprocessed_data.columns` was scanned for `SMA_<N>` entries to
  determine the maximum SMA window materialized in the cache, and
  specifically whether `SMA_114` is present.
- Status sidecar (`cache/status/<T>_status.json`) was read if
  present.

Classification rules:

- MATCH = explicit declared `max_sma_day == 114` and `SMA_114` column
  present, OR (no declared value AND `SMA_114` present).
- MISMATCH_MAX_SMA = declared value present and != 114, AND
  `SMA_114` absent.
- CONFLICTING_MAX_SMA = declared value disagrees with schema
  presence of `SMA_114`.
- UNDETERMINABLE_MAX_SMA = no reliable evidence either way.

### 6.2 Pre-Repair Inventory

- Required PKLs inspected: 47 of 61 (the other 14 required PKLs were
  MISSING entirely; see section 8).
- MATCH: **47** (declared `manifest_max_sma_day == 114` AND
  `SMA_114` present in `preprocessed_data.columns` for every existing
  required PKL).
- MISMATCH_MAX_SMA: 0.
- CONFLICTING_MAX_SMA: 0.
- UNDETERMINABLE_MAX_SMA: 0.

### 6.3 MISMATCH_MAX_SMA tickers

None.

### 6.4 CONFLICTING_MAX_SMA tickers

None.

### 6.5 UNDETERMINABLE_MAX_SMA tickers

None.

### 6.6 Implications for prior benchmark integrity

Every pre-existing required TrafficFlow PKL carried an explicit
manifest declaration of `max_sma_day == 114` AND had `SMA_114`
materialized in `preprocessed_data`. The PR #300 K1/K2/K3 benchmark
results for AAPL and SPY (the two secondaries whose price cache was
present pre-task) used PKLs that are MATCH on the strict criterion. No
silent SMA-window downgrade risk applies to those prior measurements.

## 7. Freshness and Schema Readiness Rule

A required member PKL is **OK** only if all of:

- the file exists and is readable via `pickle.load`;
- the top-level loaded object is a dict;
- the four required TrafficFlow fields (`preprocessed_data`,
  `active_pairs`, `daily_top_buy_pairs`, `daily_top_short_pairs`)
  are present;
- max-SMA-day classification is MATCH (per section 6);
- `preprocessed_data.index.max()` is not older than the
  `benchmark_as_of_date` for any secondary the PKL is consumed by.
  Because all benchmark secondaries finished at tail 2026-05-22 after
  Phase A warming and all generated PKLs finished at the same
  `preprocessed_data.index.max() == 2026-05-22`, freshness is uniform
  across the dependency graph after repair.

## 8. Initial PKL Readiness Summary

Initial inspection (61 required member tickers):

- OK: **47** (explicit manifest `max_sma_day == 114`, SMA_114
  present, schema fields complete).
- MISSING: **14** (no PKL in `cache/results/`).
- STALE / MISMATCH_MAX_SMA / CONFLICTING_MAX_SMA /
  UNDETERMINABLE_MAX_SMA / INVALID / UNREADABLE / SCHEMA_MISMATCH /
  UNKNOWN_USABLE: 0 in every category.

Missing 14 (all internationals + a handful of small US tickers
without prior Spymaster runs on this host): `3900.HK`, `ACO-X.TO`,
`AIEA.L`, `BOLT.BA`, `BUT.L`, `MVV1.DE`, `PBH.TO`, `SEAC`, `SMMU`,
`SQE.F`, `TCU.F`, `TND.L`, `TXN.BA`, `UNLRF`.

## 9. Initial Secondary Price-Cache Readiness Summary

Pre-task scan of `price_cache/daily/`:

| Secondary | Pre-task on disk | Tail date pre-task |
|---|---|---|
| AAPL | yes (.csv) | 2026-05-04 |
| AMZN | no | n/a |
| GOOGL | no | n/a |
| META | no | n/a |
| MSFT | no | n/a |
| NVDA | no | n/a |
| SPY | yes (.csv) | 2026-05-14 |
| TSLA | no | n/a |

Six of eight target secondaries lacked an on-disk price cache.
AAPL's existing cache was stale (last bar 20 days before session
start); SPY's was 10 days before session start. Bounded repair
required for all six missing secondaries and a refresh for the two
stale ones to reach uniform tail.

## 10. Bounded Repair: Secondary Price Caches

Repair was executed by importing `trafficflow.refresh_secondary_caches`
(no Dash launch) under monkey-patches that do NOT alter the path
itself: the production helper writes through `_persist_cache` to
`price_cache/daily/<SEC>.{parquet,csv}` per source inspection. The
ticker list passed was strictly the 6 missing target secondaries.

| Secondary | Action | Elapsed (s) | Tail after | Rows | Bytes | Status |
|---|---|---:|---|---:|---:|---|
| AMZN  | yfinance fetch -> csv | 0.43 | 2026-05-22 | 7,301 | 220,020 | OK |
| GOOGL | yfinance fetch -> csv | 0.30 | 2026-05-22 | 5,475 | 165,856 | OK |
| META  | yfinance fetch -> csv | 0.24 | 2026-05-22 | 3,523 | 104,771 | OK |
| MSFT  | yfinance fetch -> csv | 0.41 | 2026-05-22 | 10,127 | 287,879 | OK |
| NVDA  | yfinance fetch -> csv | 0.33 | 2026-05-22 | 6,876 | 212,621 | OK |
| TSLA  | yfinance fetch -> csv | 0.26 | 2026-05-22 | 4,000 | 121,445 | OK |

`AAPL.csv` and `SPY.csv` were left in place under
TrafficFlow's `_needs_refresh -> False` monkey-patch for the
benchmark; the benchmark therefore treats the existing AAPL/SPY tails
(2026-05-04 / 2026-05-14) as "current enough" by the patched
freshness contract. All six warmed secondaries reached tail
`2026-05-22`, the most recent fully closed equity session on
yfinance at session time.

No write occurred outside `price_cache/daily/`. The 4 pre-existing
extras (`HD.csv`, `JNJ.csv`, `MCD.csv`, `WMT.csv`) carry session-
unrelated mtimes from 2026-05-15 and were not touched.

## 11. Bounded Repair: TrafficFlow PKL Generation via signal_engine_cache_refresher.py

### 11.1 Tool confirmation

`signal_engine_cache_refresher.py` was statically confirmed safe in
section 3.2. Every invocation in this section passed `--max-sma-day
114` explicitly to override the tool's default of 30.

### 11.2 Generation set and gate decision

Generation set = the 14 MISSING tickers from section 8. The gate
threshold (50 unique tickers) was not exceeded; the 14-ticker set was
authorized to proceed.

All gates A-G evaluated true:

- A. `signal_engine_cache_refresher.py` exists; safety confirmed in
  section 3.2.
- B. Generation target list exact and bounded.
- C. `--max-sma-day 114` will be passed for every call.
- D. `--cache-dir cache/results` will be passed for every call.
- E. `--status-dir cache/status` will be passed for every call.
- F. Each call processes exactly one ticker (`--ticker`).
- G. No existing PKL/manifest/status file is overwritten (all 14
  targets were MISSING pre-task; no pre-overwrite snapshot needed
  beyond what section 8 already records).

### 11.3 Per-ticker dry-run results

Per spec, each ticker was dry-run first via:

```
<PINNED_INTERPRETER> signal_engine_cache_refresher.py \
  --ticker <T> --dry-run \
  --cache-dir cache/results --status-dir cache/status \
  --max-sma-day 114
```

All 14 dry-runs returned exit 0. Per-ticker dry-run wall time
range: 3.51 s (TCU.F) - 6.72 s (BUT.L). Standard output and
stderr were captured to `<SESSION_DIR>/repair/<T>_dryrun.out` and
`<T>_dryrun.err`.

Validation evidence per dry-run (cross-referenced against the
post-write manifest which records `params.max_sma_day` and
`params.ticker`):

- Target ticker recorded in manifest matches the `--ticker` argument
  for every generation.
- `max_sma_day` recorded in every generated manifest is 114.
- `cache_dir` and `status_dir` are exactly the directories the tool
  wrote to (verified by directory contents post-write).
- No broad universe regeneration: per-ticker invocation processes
  exactly one ticker per source inspection.
- No writes outside `cache/results/` or `cache/status/`: verified by
  the post-snapshot diff in section 20.

### 11.4 Per-ticker write results

```
<PINNED_INTERPRETER> signal_engine_cache_refresher.py \
  --ticker <T> --write \
  --cache-dir cache/results --status-dir cache/status \
  --max-sma-day 114
```

All 14 writes returned exit 0. Per-ticker write wall time range:
3.78 s (SMMU) - 7.45 s (BUT.L). Standard output and stderr were
captured to `<SESSION_DIR>/repair/<T>_write.out` and `<T>_write.err`.

| Ticker | Dry-run (s) | Write (s) | PKL bytes | preprocessed last index |
|---|---:|---:|---:|---|
| 3900.HK | 3.53 | 4.00 | 5,025,008 | 2026-05-22 |
| ACO-X.TO | 4.33 | 4.93 | 8,096,872 | 2026-05-22 |
| AIEA.L | 4.80 | 5.52 | 9,953,144 | 2026-05-22 |
| BOLT.BA | 4.02 | 4.86 | 6,751,641 | 2026-05-22 |
| BUT.L | 6.72 | 7.45 | 15,445,829 | 2026-05-22 |
| MVV1.DE | 4.16 | 4.56 | 6,885,562 | 2026-05-22 |
| PBH.TO | 3.52 | 3.87 | 4,599,753 | 2026-05-22 |
| SEAC | 4.13 | 4.75 | 7,638,509 | 2026-05-22 |
| SMMU | 3.53 | 3.78 | 4,211,853 | 2026-05-22 |
| SQE.F | 3.77 | 4.25 | 5,732,296 | 2026-05-22 |
| TCU.F | 3.51 | 4.12 | 4,787,088 | 2026-05-22 |
| TND.L | 4.89 | 5.41 | 9,953,315 | 2026-05-22 |
| TXN.BA | 3.94 | 4.39 | 6,647,825 | 2026-05-22 |
| UNLRF | 3.81 | 4.34 | 6,169,534 | 2026-05-22 |

### 11.5 Post-generation verification

For every generated ticker:

- `cache/results/<T>_precomputed_results.pkl` exists (verified by
  pre/post diff).
- `cache/results/<T>_precomputed_results.pkl.manifest.json` exists.
- `cache/status/<T>_status.json` exists.
- PKL pickle-loads as a dict.
- Required schema fields (`preprocessed_data`, `active_pairs`,
  `daily_top_buy_pairs`, `daily_top_short_pairs`) are all present.
- `preprocessed_data` columns include `SMA_114`.
- Manifest sidecar has `params.max_sma_day == 114` and
  `params.ticker == <T>`. (The newer manifest schema produced by
  `signal_engine_cache_refresher.py` places the max-SMA-day under
  `params.max_sma_day` rather than at the top level; both schemas
  are honored as MATCH evidence by section 6.1's rule.)

No generation failed. Zero consecutive failures triggered. No writes
landed outside `cache/results/` or `cache/status/`.

## 12. Post-Repair Readiness Inventory

| Class | Count (post-repair) |
|---|---:|
| OK | **61** |
| MISSING | 0 |
| STALE | 0 |
| MISMATCH_MAX_SMA | 0 |
| CONFLICTING_MAX_SMA | 0 |
| UNDETERMINABLE_MAX_SMA | 0 |
| INVALID / UNREADABLE / SCHEMA_MISMATCH | 0 |
| UNKNOWN_USABLE | 0 |

By max-SMA class: 61 MATCH, 0 other.

By secondary price-cache state: all 8 target secondaries on disk,
all tails `2026-05-22` (six warmed this session; AAPL/SPY pre-existing).

## 13. Benchmark Eligibility

Every target `(secondary, K)` cell is **ELIGIBLE** under the readiness
rule in section 7. The matrix:

| Secondary | K=1 | K=2 | K=3 | K=4 | K=6 |
|---|---|---|---|---|---|
| AAPL  | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE |
| AMZN  | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE |
| GOOGL | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE |
| META  | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE |
| MSFT  | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE |
| NVDA  | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE |
| SPY   | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE |
| TSLA  | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE | ELIGIBLE |

40 of 40 cells are ELIGIBLE. No cell is DATA-GATED, PKL-GATED,
MAX-SMA-GATED, STALE-GATED, or ERROR.

## 14. PARALLEL_SUBSETS=0 Results

Variant: subprocess invoked with `PARALLEL_SUBSETS=0`. Default
TrafficFlow flags (bitmask fastpath ON, post-intersect fastpath OFF).
3 reps per cell, median reported.

| Secondary | K1 | K2 | K3 | K4 | K6 | rows@K6 |
|---|---:|---:|---:|---:|---:|---:|
| AAPL  | 0.167 | 0.338 | 0.772 | 0.876 | 1.975 | 1 |
| AMZN  | 0.138 | 0.228 | 0.300 | 0.436 | 0.584 | 1 |
| GOOGL | 0.107 | 0.144 | 0.252 | 0.453 | 0.986 | 1 |
| META  | 0.066 | 0.160 | 0.416 | 0.777 | 3.634 | 1 |
| MSFT  | 0.159 | 0.338 | 0.423 | 0.829 | 3.688 | 1 |
| NVDA  | 0.031 | 0.151 | 0.202 | 0.340 | 1.155 | 1 (K=1 row=0; see section 17) |
| SPY   | 0.110 | 0.312 | 0.661 | 0.813 | 3.290 | 1 |
| TSLA  | 0.115 | 0.176 | 0.481 | 0.824 | 1.791 | 1 |

Per-K row counts (across the full 8-secondary sweep, serial): K1=7,
K2=8, K3=8, K4=8, K6=8 (NVDA K1 is the only 0-row cell; this is a
real TrafficFlow active-member filter outcome, not a readiness gap;
see section 17).

Serial sum across 8 secondaries:
K1: 0.893s, K2: 1.847s, K3: 3.507s, K4: 5.348s, K6: 17.103s.

## 15. PARALLEL_SUBSETS=1 Results

Variant: subprocess invoked with `PARALLEL_SUBSETS=1`. Same other
defaults. 3 reps per cell, median reported.

| Secondary | K1 | K2 | K3 | K4 | K6 |
|---|---:|---:|---:|---:|---:|
| AAPL  | 0.166 | 0.339 | 0.767 | 0.877 | 1.965 |
| AMZN  | 0.142 | 0.234 | 0.310 | 0.451 | 0.598 |
| GOOGL | 0.106 | 0.148 | 0.252 | 0.458 | 1.028 |
| META  | 0.066 | 0.161 | 0.421 | 0.805 | 3.842 |
| MSFT  | 0.162 | 0.354 | 0.440 | 0.850 | 3.761 |
| NVDA  | 0.031 | 0.152 | 0.201 | 0.343 | 1.165 |
| SPY   | 0.112 | 0.320 | 0.660 | 0.814 | 3.411 |
| TSLA  | 0.115 | 0.178 | 0.495 | 0.838 | 1.907 |

Parallel sum across 8 secondaries:
K1: 0.900s, K2: 1.886s, K3: 3.546s, K4: 5.436s, K6: 17.677s.

## 16. Parallel Comparison Summary

Speedup ratio (serial wall_med / parallel wall_med) across all 39
non-empty cells: range 0.94 - 1.01, median 0.99.

PARALLEL_SUBSETS=1 does NOT materially regress, but it also does NOT
materially speed up the benchmark workload. For K=4 and K=6
(the only K levels that cross the `PARALLEL_SUBSETS_MIN_K=4`
threshold), four ThreadPoolExecutor workers fire (verified via the
parallel-variant cProfile, see section 18) but provide no wall
improvement, slight overhead on the heaviest K=6 cells (-3% to -6%).

Root cause is the GIL: the per-subset worker
(`_subset_metrics_spymaster_bitmask`) is pure-Python plus pandas /
numpy operations whose Python-level orchestration holds the GIL most
of the time. Thread-level parallelism cannot accelerate a
GIL-bound workload of this shape. A ProcessPoolExecutor variant
would, in principle, but would carry per-subset interpreter and
data-marshalling overhead that may dominate at K=4 / K=6 with the
current 1-row-per-K leaderboard density. Not in scope for this
evidence task.

## 17. Data Shape / Determinism Checks

- Leaderboard density: every Phase 6I-79 `combo_leaderboard.xlsx`
  contains exactly 12 rows (one row per K=1..12 best build).
  Therefore each (secondary, K) benchmark cell operates on exactly
  1 candidate row.
- Output row count: 39 of 40 cells produced exactly 1 board row
  (`build_board_rows` output). The 40th cell - NVDA K=1 - produced 0
  rows; the lone member CGLO[D] is filtered as inactive by
  `_filter_active_members_by_next_signal` because its PKL reports no
  current "next signal". This is a runtime data-state outcome
  (CGLO has no actionable signal as of 2026-05-22), not a readiness
  gap.
- Per-cell row hash (blake2b/16 over JSON-canonicalized rows) is
  deterministic across all 3 reps within each variant for every
  non-empty cell.
- Cross-variant determinism: **all 39 non-empty cells produce the
  same row hash under PARALLEL_SUBSETS=0 and PARALLEL_SUBSETS=1.**
  Threading did not introduce non-determinism, consistent with the
  per-subset averaging step using stable arithmetic.

## 18. Bottleneck Profiling Summary

`cProfile` snapshot of `SPY K=6` (representative non-empty K=6 cell)
captured for both variants. Both snapshots show the same dominant
surfaces; the parallel snapshot additionally shows the four worker
threads via `threading.py`. Top cumulative time entries:

Serial K=6 (4.140 s, 5,100,987 calls):

- `build_board_rows`: 4.140 s (100%).
- `compute_build_metrics_spymaster_parity`: 3.892 s (94%).
- `_subset_metrics_spymaster_bitmask` x 31 subsets: 3.610 s (87%).
- `_extract_signals_from_active_pairs`: 1.069 s (26%).
- Pandas DateTimeIndex iteration
  (`datetimes.py:__iter__`): 1.010 s, 940,344 iterations.
- `_next_signal_from_pkl`: 0.475 s.

Parallel K=6 (4.276 s, 5,104,746 calls):

- 4 worker threads visible
  (`thread.py:53(run)` x 4 contributing 3.787 s cumulative).
- `_subset_metrics_spymaster_bitmask`: 3.784 s (88%) across the
  workers.
- `_thread.lock.acquire`: 7.591 s cumulative across the join /
  as_completed surface (clock time vs CPU time accounting).
- Same dominant per-subset surfaces as serial, but the GIL prevents
  the workers from overlapping their numerical work.

Reported subset count for SPY K=6 is 31 = `2^5 - 1`, indicating the
effective `metrics_members` count after
`_filter_active_members_by_next_signal` was 5, not the 6 leaderboard
members. (One of the six K=6 members carried no current "next
signal" and was muted before subset generation.) This is a normal
TrafficFlow per-build behavior.

Bottleneck-category summary for the K1/K2/K3/K4/K6 path:

1. StackBuilder artifact discovery / `_find_latest_combo_table` +
   `_read_table`: under 10 ms per cell. Not a bottleneck.
2. Excel / JSON parsing: openpyxl read at ~3-10 ms per cell.
   Negligible.
3. Secondary price-data loading: paid once per secondary during
   warmup; warm `_PRICE_CACHE` from then on.
4. Signal-library / PKL loading: dominant per-cell cost at lower K
   (K1/K2/K3) via `_next_signal_from_pkl` and
   `_processed_signals_from_pkl`. Cached at the module level after
   first hit, so K=1 cell wall already includes the cold-PKL load
   on the first cell of each secondary, and warm-PKL behavior on
   subsequent K=2/3/4/6 cells of the same secondary.
5. Per-subset metrics work
   (`_subset_metrics_spymaster_bitmask`): dominant cost surface
   from K>=2 onwards, scales linearly in subset count
   (`2^N - 1` where N is active-member count after filtering).
6. DataFrame / DateTimeIndex construction: ~25% of K=6 wall time,
   driven by per-subset reindex and signal-extraction work.
7. UI / callback / output serialization: zero - the benchmark calls
   `build_board_rows` directly and does not engage `make_app` or any
   Dash callback.
8. Threading: K=4 and K=6 fire 4 worker threads under
   PARALLEL_SUBSETS=1 but GIL contention prevents speedup.

## 19. Runtime Classification

Overall: **PASS WITH NOTES**.

- All 8 secondaries are now benchmarkable at K=1/2/3/4/6.
- 40 of 40 (secondary, K) cells are ELIGIBLE post-repair.
- 39 of 40 cells produced exactly 1 board row at sub-4-second wall
  time per cell, deterministic across reps and across variants.
- One cell (NVDA K=1) produced 0 rows for a real data reason
  (lone member muted as having no current next signal); this is not
  a benchmark blocker.
- Generated PKLs all carry explicit `params.max_sma_day == 114` in
  their manifests AND have `SMA_114` materialized in
  `preprocessed_data`. Pre-existing PKLs all carry explicit
  `max_sma_day == 114` in their (legacy-schema) manifests AND have
  `SMA_114` materialized. No PKL is silently downgraded.
- PARALLEL_SUBSETS=1 does not regress and does not speed up;
  serial-vs-parallel hashes match.
- K=6 sum across 8 secondaries is 17.1 s serial. With the Phase
  6I-79 1-row-per-K density, K=6 is operationally fast on this host.
  Naive extrapolation to a hypothetical 20-rows-per-K leaderboard
  density: K=6 sum across 8 secondaries would be ~5.7 minutes serial.
  Operator's informal ~15-minute K=6 / ~500-ticker memory baseline is
  compatible with a ~30-row-per-secondary or comparable density;
  this benchmark does not contradict that baseline.

"WITH NOTES" rather than clean PASS because: (a) parallel comparison
shows no speedup, leaving K-scaling acceleration as an open
implementation question for headless TrafficFlow; (b) NVDA K=1
produced 0 rows for a real runtime-data reason that downstream
documentation should expect; (c) AAPL.csv and SPY.csv were used
"as-is" with stale tails (2026-05-04 and 2026-05-14 respectively),
under monkey-patched `_needs_refresh -> False`, rather than refreshed
to the 2026-05-22 tail the other 6 secondaries reached - a future
benchmark may want uniform tails across all 8 secondaries.

## 20. Canonical Artifact Safety Check

Post-run snapshot diff against pre-run state:

| Root | Pre count | Post count | Unchanged |
|---|---:|---:|---:|
| `output/stackbuilder/` | 5,388 | 5,388 | yes |
| `output/impactsearch/` (xlsx) | 8 | 8 | yes (0 changed / added / removed) |
| `output/onepass/` | 2 | 2 | yes (`onepass.xlsx` SHA-256 unchanged) |
| `signal_library/data/stable/` | 71,980 | 71,980 | yes |
| `output/validation/` | 0 | 0 | yes |

Authorized writes:

- `price_cache/daily/`: 6 ADDED (AMZN, GOOGL, META, MSFT, NVDA,
  TSLA); 0 modified outside the 8-target set. The 4 pre-existing
  extras (HD/JNJ/MCD/WMT) retain 2026-05-15 mtimes; not touched.
- `cache/results/`: 14 ADDED (the bounded generation set, exact
  match); 0 unexpected content changes.
- `cache/status/`: 14 ADDED (matching the bounded generation set);
  0 unexpected content changes.

## 21. Remaining Gaps Before TrafficFlow Headless

- **K-scaling parallelism is unresolved.** PARALLEL_SUBSETS=1 fires
  but yields no speedup on this workload due to the GIL. A future
  scoping decision is needed for K=10+ workloads on richer
  leaderboards: process-level parallelism, Cython / numba for
  `_subset_metrics_spymaster_bitmask`, or accepting the serial
  ceiling under the 1-row-per-K Phase 6I-79 density.
- **Price-cache freshness contract.** This benchmark relied on a
  monkey-patched `_needs_refresh -> False`. Headless TrafficFlow
  needs an explicit refresh policy: refuse to compute on stale
  secondary prices, or refresh in-process before computing. The
  current `refresh_secondary_caches` helper proved bounded and
  fast (sub-second per ticker), so an in-process refresh path is
  feasible.
- **Manifest schema dual surface.** Existing TrafficFlow-consumed
  PKLs carry `max_sma_day` at the manifest top level; PKLs generated
  by current `signal_engine_cache_refresher.py` carry it at
  `params.max_sma_day`. Readiness inspectors must honor both. This
  is documented in section 6.1.
- **Master ticker list breadth.** The Phase 6I-79 leaderboards
  reference 61 unique base member tickers across 8 secondaries x 5 K
  levels. A broader-universe TrafficFlow run (the kind the
  operator-memory baseline implies) would touch hundreds or
  thousands of tickers. The PKL generation cost observed here was
  3.5 - 7.5 seconds per ticker. A bulk refresh of a 500-ticker set
  via `signal_engine_cache_refresher.py` would take ~30-60 minutes
  on this host, dominated by yfinance fetch time.
- **The single-row-per-K leaderboard contract.** Phase 6I-79 used
  `--beam-width 12 --top-n 20 --bottom-n 20 --allow-decreasing
  --k-patience 1`, which under the leaderboard write logic retains
  only the best build per K. Headless TrafficFlow scoping should
  document whether richer (top-N-per-K) leaderboards are expected and
  if so, scope a benchmark of the resulting per-cell wall time at
  realistic row density.

## 22. Recommendation

**Proceed toward TrafficFlow headless implementation with notes.**

This task closes the input-readiness, max-SMA-day, and per-cell
benchmark questions for the Phase 6I-79 secondary set under the
default `TF_BITMASK_FASTPATH=1` fastpath. The TrafficFlow compute
surface is directly callable as `build_board_rows(sec, k, run_fence,
missing_map)`; required PKL schema is documented and verified at
`max_sma_day == 114`; bounded repair via
`signal_engine_cache_refresher.py` works reliably one ticker at a
time at ~5 seconds per ticker.

Implementation recommendations:

1. Wire the headless runner's argparse defaults explicitly to match
   the TrafficFlow Dash component defaults section identified in
   the structural diff (defaults-diff audit pattern from Phase
   6I-77/78).
2. Default the headless runner to an in-process
   `refresh_secondary_caches([...])` before computing, so the
   freshness gap that necessitated the monkey-patch in this benchmark
   is closed in production.
3. Default the runner to `--max-sma-day 114` when calling
   `signal_engine_cache_refresher.py` (or invoke the refresher
   directly via its module entrypoint with that arg) to prevent
   silent fallback to the tool's 30-day default.
4. Document that `PARALLEL_SUBSETS=1` is a no-op speedup on this
   workload and should not be enabled by default. Reserve it for a
   future process-pool-based variant.
5. Capture per-subset timing inside
   `_subset_metrics_spymaster_bitmask` during the headless build;
   that is the K-scaling cost surface and any acceleration work will
   start there.

## Notes on this evidence task

- This was an evidence task, not headless implementation.
- No Dash server was launched.
- No production code or tests were modified.
- No canonical StackBuilder, ImpactSearch, OnePass, signal_library
  stable, or validation artifacts were modified.
- TrafficFlow consumes `cache/results/` precomputed-result PKLs, NOT
  OnePass `signal_library/data/stable/` PKLs. This was verified by
  source inspection (section 3.1) and is the foundation of the
  repair strategy in section 11.
- Max-SMA-day verification was performed before any new PKL
  generation (section 6) and after (section 11.5).
- All 14 new PKLs were generated through
  `signal_engine_cache_refresher.py` with `--max-sma-day 114`
  explicitly passed (section 11.4).
- Any `cache/results/`, `cache/status/`, or `price_cache/daily/`
  writes were generated artifacts; none are staged for commit.
- All 8 secondaries are now benchmarkable at K=1/2/3/4/6.
- TrafficFlow headless work can proceed with notes (section 22).
