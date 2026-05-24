# TrafficFlow K1/K2/K3 Benchmark and Bottleneck Audit Evidence

## 1. Scope and Non-Goals

This document is a controlled benchmark and bottleneck audit of the current
TrafficFlow compute path at K=1, K=2, and K=3, against the Phase 6I-79
canonical StackBuilder outputs for the 8 ImpactSearch secondaries.

This document is **NOT**:

- A TrafficFlow headless implementation.
- A behavior or correctness change to `trafficflow.py`.
- A re-run of any production engine (StackBuilder / OnePass / ImpactSearch /
  Spymaster / Confluence / multi_timeframe_builder).
- A change to any canonical StackBuilder artifact under `output/stackbuilder/`.

No TrafficFlow Dash server was launched during this audit. No production code
or tests were modified. No canonical StackBuilder artifacts were modified.

## 2. References

- Phase 6I-79 production StackBuilder run evidence:
  `md_library/shared/2026-05-23_PHASE_6I_79_STACKBUILDER_PRODUCTION_RUN_EVIDENCE.md`
- TrafficFlow LEGACY-vs-current structural diff:
  `md_library/shared/2026-05-23_TRAFFICFLOW_LEGACY_VS_CURRENT_STRUCTURAL_DIFF.md`

The structural diff doc establishes that current TrafficFlow contains the
same 7 references to `ThreadPoolExecutor` as LEGACY did (concurrency surface
unchanged) and consumes `combo_leaderboard.*` files only at the
StackBuilder-artifact contract (no consumption of `selected_build.json`,
`cohort.xlsx`, per-K `combo_k=*.json`, or `rank_*` files).

## 3. Static Preflight Result

Static AST + substring inspection of `trafficflow.py` at current `main`
(file size 145,940 bytes, 3,422 lines, 83 top-level functions, 24 imports).

Module-import-time executable statements (top-level, non-`def`/`class`/
`import`/string-literal):

- `pd.set_option('future.no_silent_downcasting', True)` (benign).
- `try: from dash import Dash, ... except Exception: Dash = None` (benign,
  Dash bound to `None` if unavailable).
- `try: import yfinance as yf except Exception: yf = None` (benign).
- `sys.path.insert(0, 'signal_library')` then optional `from
  shared_market_hours import ...` (sys.path side effect, no execution).
- The canonical entrypoint at the file tail:

  ```
  if __name__ == "__main__" and not __TF_ALREADY_STARTED:
      __TF_ALREADY_STARTED = True
      main()
  ```

  `main()` is gated by `__name__ == "__main__"` and is only invoked when
  `trafficflow.py` is executed as a script. Importing the module does NOT
  call `main()`, does NOT call `make_app()`, and does NOT call
  `app.run_server(...)`.

Concurrency-relevant findings (substring scan):

- `threading`, `multiprocessing`, `asyncio`, `joblib`, `dask`, `ray`,
  `subprocess`: zero hits at module scope or inside the K1/K2/K3 compute path.
- `concurrent.futures` / `ThreadPoolExecutor`: 7 hits across the file, all
  inside function bodies (lines 36 imports, 1271/1272 inside
  `refresh_secondary_caches`, 2798/2800 inside
  `compute_build_metrics_spymaster_parity`, 3249 inside `make_app`).

Network-relevant findings:

- `yfinance` is imported at L20, optionally re-bound at L54; `yf.*` calls
  are inside function bodies (`_yf_fetch_incremental` L919,
  `_fetch_secondary_from_yf` L1027). None at import time.

File-write-relevant findings:

- `.to_csv(` at L165 (`_dump_csv` helper, `debug_dumps/` dir; not called
  in the benchmark path), L914 (cache persist), L1114 (cache persist).
- `to_parquet(` at L907 / L1112 (cache persist).
- `app.run` / `app.run_server` only at L3406 (inside `main()`).

Verdict: **Importing `trafficflow.py` as a Python module is safe.** It does
not launch Dash, does not fetch network data, does not write canonical
artifacts, does not start other engines, and does not start background
threads at import time. A safe in-process benchmark is therefore possible.

## 4. TrafficFlow Computation Path Identified

The board-row producer is `build_board_rows(sec, k, run_fence, missing_map)`
at L2956 (121 lines). For each `(secondary, K)` request it:

1. Resolves the latest StackBuilder run directory under
   `output/stackbuilder/<sec>/` via `_find_latest_combo_table(sec)` (chooses
   the most recently created run dir by `st_ctime`).
2. Reads `combo_leaderboard.parquet | combo_leaderboard.xlsx |
   combo_leaderboard.csv` via `_read_table()`.
3. Normalizes columns and **filters `df[df['K'] == int(k)]`**.
4. Iterates the filtered rows. For each row:
   - `sanitize_members(row['Members'])` strips `[I]/[D]` mode suffixes.
   - `_members_have_pkls(members)` checks whether each member has a
     `<TICKER>_precomputed_results.pkl` file under the Spymaster PKL dir.
     Rows with zero PKL coverage are skipped.
   - `compute_build_metrics_spymaster_parity(sec, members,
     eval_to_date=cap_dt)` returns averaged metrics + a snapshot info dict.
   - `_calculate_signal_mix(members_with_protocol, as_of=None)` computes
     the MIX agreement ratio.
   - The row is JSONified and appended.

`compute_build_metrics_spymaster_parity` is the per-build worker (L2690,
181 lines). For K=1 it short-circuits to a fast path (one
`_subset_metrics_spymaster*` call, no subset enumeration). For K>=2 it
enumerates ALL non-empty subsets of the active member list
(`itertools.combinations` over r=1..N) and averages metrics across all
`2^N - 1` subsets. Subset count grows: K=1: 1, K=2: 3, K=3: 7, K=4: 15,
K=5: 31, K=6: 63, K=12: 4,095.

The per-subset worker is one of three implementations selected by env flags:

- `_subset_metrics_spymaster_bitmask` (L2557, 132 lines) - active when
  `TF_BITMASK_FASTPATH=1` (the default).
- `_subset_metrics_spymaster_fast` (L2440, 116 lines) - active when
  `TF_POST_INTERSECT_FASTPATH=1`.
- `_subset_metrics_spymaster` (L2009, 192 lines) - baseline proven-parity
  implementation, active when neither fastpath flag is set.

`build_board_rows` is reachable directly without constructing the Dash app
and without invoking `main()`.

## 5. Import / Side-Effect Safety Assessment

Confirmed via static inspection:

- No file writes at import time.
- No network calls at import time.
- No subprocess spawning at import time.
- No background threads started at import time.
- No global cache populated at import time (only declared, e.g.
  `_PRICE_CACHE: Dict[str, pd.DataFrame] = {}` at L289 is an empty dict).
- The only top-level executable side effect is
  `pd.set_option('future.no_silent_downcasting', True)` plus the
  `sys.path.insert(0, 'signal_library')` from the optional parity-import
  block.

Importing `trafficflow` as a Python module is therefore acceptable for the
benchmark.

## 6. Threading / Parallelization Assessment

### Current TrafficFlow

Top-level concurrency surfaces in current `trafficflow.py`:

- `from concurrent.futures import ThreadPoolExecutor, as_completed` (L36).
- `refresh_secondary_caches(symbols, force=False)` (L1157) uses a
  `ThreadPoolExecutor` to refresh multiple secondary price caches in
  parallel. This is the price-refresh layer; it is NOT in the K1/K2/K3
  build path measured here.
- `compute_build_metrics_spymaster_parity` (L2690) has an optional per-subset
  threading layer gated by the `PARALLEL_SUBSETS` env var. The gate at
  L2796 is:

  ```
  enable_subset_parallel = PARALLEL_SUBSETS
                          and len(metrics_members) >= PARALLEL_SUBSETS_MIN_K
                          and len(subsets) > 1
  ```

  Defaults (L241-244): `PARALLEL_SUBSETS=0` (OFF),
  `PARALLEL_SUBSETS_MIN_K=4`, `TRAFFICFLOW_SUBSET_WORKERS=4`.

  Therefore, per-subset parallelism is OFF by default AND it would not
  fire at K=1/K=2/K=3 even if enabled, because the K floor is 4.

- `make_app` contains one `ThreadPoolExecutor` reference (L3249) for UI-layer
  callback work. Not in the K1/K2/K3 build path.

**At K=1/K=2/K=3 with default env, the build path is single-threaded.**

### LEGACY TrafficFlow

The structural diff doc (section 13) reports:
`ThreadPoolExecutor references: 7 in both. No new loops over tickers /
windows / builds are visible at the top-level AST surface; the
_subset_metrics_spymaster* family shrank slightly... but the algorithmic
shape (bitmask vs fast vs full) is preserved as three distinct functions
in both versions.`

LEGACY TrafficFlow had the same 7 `ThreadPoolExecutor` references as
current; the concurrency surface is unchanged. Per-subset parallelism gating
on `PARALLEL_SUBSETS_MIN_K=4` predates the current diff window. There is no
LEGACY concurrency that current TrafficFlow has lost.

The operator-memory baseline of "K=6 over ~500 tickers in ~15 minutes"
therefore cannot be explained by a regression in TrafficFlow's
parallelization; that workload's speed is determined by per-subset wall time
times the per-K subset count times the leaderboard-row count per (sec, K),
plus whatever upstream cache warmth or fastpath flags were active.

## 7. K-Level Selection Feasibility

TrafficFlow's K-level selection is **directly supported** by the existing
public function signature. `build_board_rows(sec, k, run_fence, missing_map)`
takes `k: int` as an explicit argument and at L2986 filters the
`combo_leaderboard` table to `df[df['K'] == int(k)]`. K=1, K=2, K=3 are all
selectable without modifying any code and without modifying any canonical
artifact.

Important data-shape finding: in the Phase 6I-79 canonical leaderboards,
each secondary's `combo_leaderboard.xlsx` contains exactly **12 rows total,
one row per K=1..12**. This is the beam-search leaderboard contract under
`--beam-width 12` with the Phase 6I-79 `--top-n 20 --bottom-n 20
--allow-decreasing --k-patience 1` configuration: a single best-build row
per K is retained. Therefore each `(secondary, K)` benchmark cell here
operates on exactly 1 candidate row.

If TrafficFlow is ever pointed at a leaderboard that retains many rows per
K (a top-N-per-K leaderboard), the per-cell wall time scales linearly with
candidate-row count multiplied by the per-build wall time observed here.

## 8. Input and Output Surfaces

### Inputs read by TrafficFlow during a K1/K2/K3 build:

- `output/stackbuilder/<sec>/<run_dir>/combo_leaderboard.{parquet,xlsx,csv}`
  (canonical StackBuilder leaderboard, read-only via pandas).
- `<SPYMASTER_PKL_DIR>/<TICKER>_precomputed_results.pkl` per primary
  member, where `<SPYMASTER_PKL_DIR>` is
  `os.environ.get('PRJCT9_SPYMASTER_PKL_DIR',
  str(_PROJECT_DIR / 'cache' / 'results'))` (per L86-89). Read-only.
- `<PRICE_CACHE_DIR>/<SEC>.{parquet,csv}` per secondary, where
  `<PRICE_CACHE_DIR>` is `os.environ.get('PRICE_CACHE_DIR',
  'price_cache/daily')` (L90). Read-only in the benchmark.
- `signal_library/` (added to `sys.path` for optional `shared_market_hours`
  / `shared_symbols` / `parity_config` imports). No data files read in the
  benchmark path.

### Outputs written by TrafficFlow:

- `<PRICE_CACHE_DIR>/<SEC>.{parquet,csv}` via `_persist_cache` (L900) /
  `_write_cache_file` (L1109) when `_load_secondary_prices` fetches from
  yfinance. **In this benchmark, network fetches were monkey-patched to
  refuse, so no price-cache writes occurred.**
- `debug_dumps/<NAME>_<TS>.csv` via `_dump_csv` (L158). Not invoked in
  the benchmark path.
- TrafficFlow does NOT write canonical engine outputs. The structural diff
  doc (section 11.3) records: `No top-level changes. TrafficFlow does not
  write canonical engine outputs (it is a Dash UI consuming StackBuilder
  outputs and Spymaster PKLs).`

No canonical artifact was modified during this benchmark.

## 9. Benchmark Methodology

Benchmark host is the pinned project interpreter
`<PINNED_INTERPRETER>` running from `<PROJECT_ROOT>`.

Runner (placed under `<SESSION_DIR>/benchmark/runner.py`; the session dir
lives under `logs/` which is gitignored, so the runner is local-only and
not part of the committed evidence):

1. Set env: `TF_CACHE_TTL_INDEX_DAYS=99999`,
   `TF_CACHE_TTL_EQUITY_DAYS=99999`, `TF_CACHE_TTL_CRYPTO_DAYS=99999`,
   `TF_CACHE_TTL_CURRENCY_DAYS=99999`, `TF_REFRESH_BACKFILL_DAYS=0`,
   `TF_BITMASK_FASTPATH=1`, `TF_POST_INTERSECT_FASTPATH=0`,
   `PARALLEL_SUBSETS=0`, `TF_SHOW_SESSION_SANITY=0`. These lock the
   benchmark to the default fastpath, no per-subset parallelism, no
   refresh-triggered network behavior.
2. Import `trafficflow` read-only.
3. Monkey-patch three functions at the `trafficflow` module:
   - `_fetch_secondary_from_yf -> empty DataFrame` (blocks any
     network fetch; counts the symbols that would have hit the net).
   - `_needs_refresh -> False` (trust the on-disk cache even if the last
     cached session is older than the expected session date).
   - `_is_truncated_history -> False` (do not reject merely-stale caches).

   No source files are modified; the patches are in-process attributes
   on the imported module only.

4. Warmup: call `_load_secondary_prices(sec)` once per `sec` in
   `{AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY, TSLA}` so the in-memory
   `_PRICE_CACHE` is populated for the secondaries whose disk cache file
   exists. Record per-secondary `rows`/`from_disk`/`ok` and the network-hit
   count.

5. For each `(sec, K)` cell, call
   `tf.build_board_rows(sec, k=K, run_fence={'global':None, 'by_sec':{}},
   missing_map=None)` and capture `wall_seconds` (perf_counter),
   `cpu_seconds` (process_time), `rss_after_mb` (psutil), `rows`,
   and `row_hash` (blake2b/16 over JSON-canonicalized row payload). If the
   first run is under 30 seconds AND did not error, repeat 2 more times for
   min/median/max.

6. After the matrix completes, cProfile one representative cell
   (`SPY K=2`) and write the top-40 cumulative-time entries to
   `<SESSION_DIR>/profiles/cprofile_top.txt`.

The benchmark used the Phase 6I-79 canonical StackBuilder outputs at
`output/stackbuilder/<sec>/<run_dir>/combo_leaderboard.xlsx` for all 8
secondaries without modification.

## 10. K1/K2/K3 Benchmark Results

Per-(secondary, K) results, wall time in seconds. All cells ran 3
repetitions (every individual run completed in under 30 seconds). `wall_med`
is the median over 3 runs; `wall_min` and `wall_max` are reported in full
in the runner JSON output.

| Sec   | K | rows | wall_min | wall_med | wall_max | cpu_med | rss_after_mb | hash (first 10) |
|-------|--:|-----:|---------:|---------:|---------:|--------:|-------------:|:----------------|
| AAPL  | 1 | 1    | 0.168    | 0.171    | 0.523    | 0.172   | 165.7        | ba4bec78be      |
| AAPL  | 2 | 1    | 0.345    | 0.348    | 0.592    | 0.359   | 190.6        | 91a8ed4101      |
| AAPL  | 3 | 1    | 0.784    | 0.790    | 1.087    | 0.766   | 216.6        | d0e66d7672      |
| AMZN  | 1 | 0    | 0.044    | 0.044    | 0.240    | 0.047   | 233.3        | -               |
| AMZN  | 2 | 0    | 0.089    | 0.090    | 0.553    | 0.094   | 271.0        | -               |
| AMZN  | 3 | 0    | 0.089    | 0.089    | 0.089    | 0.078   | 271.0        | -               |
| GOOGL | 1 | 0    | 0.006    | 0.006    | 0.006    | 0.000   | 271.0        | -               |
| GOOGL | 2 | 0    | 0.056    | 0.057    | 0.304    | 0.063   | 293.2        | -               |
| GOOGL | 3 | 0    | 0.054    | 0.056    | 0.056    | 0.063   | 293.2        | -               |
| META  | 1 | 0    | 0.006    | 0.006    | 0.007    | 0.016   | 293.2        | -               |
| META  | 2 | 0    | 0.044    | 0.044    | 0.240    | 0.047   | 307.5        | -               |
| META  | 3 | 0    | 0.052    | 0.053    | 0.183    | 0.047   | 319.6        | -               |
| MSFT  | 1 | 0    | 0.046    | 0.048    | 0.264    | 0.047   | 338.1        | -               |
| MSFT  | 2 | 0    | 0.090    | 0.090    | 0.319    | 0.078   | 358.7        | -               |
| MSFT  | 3 | 0    | 0.131    | 0.132    | 0.628    | 0.125   | 397.7        | -               |
| NVDA  | 1 | 0    | 0.030    | 0.031    | 0.153    | 0.031   | 405.8        | -               |
| NVDA  | 2 | 0    | 0.055    | 0.055    | 0.300    | 0.047   | 428.6        | -               |
| NVDA  | 3 | 0    | 0.080    | 0.081    | 0.205    | 0.063   | 440.3        | -               |
| SPY   | 1 | 1    | 0.113    | 0.113    | 0.243    | 0.109   | 452.3        | b698e62fe4      |
| SPY   | 2 | 1    | 0.319    | 0.320    | 0.779    | 0.281   | 486.2        | 81501a970f      |
| SPY   | 3 | 1    | 0.656    | 0.657    | 1.044    | 0.625   | 512.0        | e7b01de790      |
| TSLA  | 1 | 0    | 0.041    | 0.043    | 0.221    | 0.031   | 529.1        | -               |
| TSLA  | 2 | 0    | 0.030    | 0.030    | 0.151    | 0.031   | 537.8        | -               |
| TSLA  | 3 | 0    | 0.088    | 0.091    | 0.232    | 0.078   | 549.7        | -               |

Coverage note: 6 of 8 secondaries (AMZN, GOOGL, META, MSFT, NVDA, TSLA) had
**no on-disk price cache file at the default `price_cache/daily/` location**
(verified in the warmup phase: `from_disk=false`, 0 rows). Because the
benchmark refuses network fetches, `_load_secondary_prices` for those
secondaries returns an empty DataFrame; the K-build then short-circuits at
the active-member filter and produces 0 rows. The reported wall times for
those cells therefore reflect the leaderboard-parse + early-exit code path
only (i.e., 30-130 ms cost to read the XLSX, sanitize members, hit
`_members_have_pkls`, attempt the active-member next-signal lookup, and
fall through to the empty-metrics path). The reported wall times for those
cells are upper bounds on the work the current code does to discover
"there is no usable price cache" for that secondary; they are not
representative of a full per-build K2/K3 evaluation.

The 2 secondaries with on-disk price cache files (AAPL, SPY) produced 1
row at every K and exhibit the meaningful timing pattern:

- AAPL: K1 0.171 s, K2 0.348 s, K3 0.790 s (median).
- SPY: K1 0.113 s, K2 0.320 s, K3 0.657 s (median).

Step-over-step ratios (median): AAPL K1->K2: 2.03x, K2->K3: 2.27x.
SPY K1->K2: 2.83x, K2->K3: 2.05x. Subset count grows 1 -> 3 -> 7, so the
per-subset cost is approximately stable; the K2->K3 doubling reflects the
2.33x subset count growth (3 -> 7).

Naive extrapolation to higher K (single-threaded, default fastpath, 1 row
per K, ~100 ms per subset):

- K=4: 15 subsets -> ~1.5 s per build.
- K=6: 63 subsets -> ~6.3 s per build.
- K=12: 4,095 subsets -> ~7 min per build.

If a leaderboard retains many rows per K (e.g. top-20-per-K), per-cell wall
time scales linearly in row count. At 20 rows-per-K and K=6 single-
threaded, 1 secondary is ~2 minutes. Across 8 secondaries it is ~16 min.
This is broadly consistent with the operator-memory baseline of K=6 over
"~500 tickers in ~15 min", though that baseline likely used different
leaderboard density and may have had `PARALLEL_SUBSETS` active.

## 11. Data Shape / Accuracy Checks

- Each Phase 6I-79 `combo_leaderboard.xlsx` contains 12 rows total (one
  per K=1..12). Verified by reading every selected_build's leaderboard
  via pandas read-only.
- 117 unique base member tickers appear across the 8 secondaries' K=1..12
  combinations.
- 82 of those 117 (70%) have a `<TICKER>_precomputed_results.pkl` file
  under `cache/results/` (1,629 total PKL files in that directory).
- 35 of those 117 (30%) have no PKL on this machine - mostly non-US
  international tickers (`.SS`, `.KS`, `.HK`, `.BO`, `.AX`, `.L`, etc.)
  drawn from the master ticker list used by Phase 6I-79.
- Output row hash is stable across 3 runs at every cell that produced a
  row, indicating deterministic output for a given input under the benchmark
  configuration.
- The 0-row outcome for AMZN/GOOGL/META/MSFT/NVDA/TSLA is solely
  attributable to the missing on-disk secondary price cache, not to a
  TrafficFlow logic bug.

## 12. Bottleneck Profiling Summary

cProfile of `SPY K=2` (representative cell, 300,259 calls in 0.369 s,
top of cumulative-time table reproduced in
`<SESSION_DIR>/profiles/cprofile_top.txt`):

- `compute_build_metrics_spymaster_parity` accounts for 0.280 s (76% of
  the total).
- `_subset_metrics_spymaster_bitmask` over 3 subsets: 0.195 s (53%).
  This is the dominant per-K cost surface and grows with subset count.
- `_filter_active_members_by_next_signal`: 0.079 s (21%), driven by
  `_next_signal_from_pkl` (0.156 s nominal across two calls) and
  `_pair_asof`.
- `_calculate_signal_mix`: 0.077 s (21%), apply-over-Series work.
- `_read_table` (XLSX parse via openpyxl): 0.009 s (one-shot per K cell).
- Pandas object construction (`construct_1d_object_array_from_listlike`,
  `sanitize_array`, Series init): ~0.138 s cumulative; this is the
  pandas-overhead surface that any per-subset compute incurs.
- DateTimeIndex iteration (`datetimes.py:__iter__`, 52,188 iterations,
  0.068 s): the per-pair-asof and per-subset reindex/lookup paths drive
  this. This is the hottest non-trivial primitive at K=2.

Bottleneck-category summary (for the K1/K2/K3 path):

1. **StackBuilder artifact discovery/loading**: one stat-walk over
   `output/stackbuilder/<sec>/` per call plus one `_read_table` per call.
   Total <10 ms per cell. Not a bottleneck at K1/K2/K3.
2. **Excel/JSON parsing**: openpyxl-backed `_read_table` <10 ms per cell;
   negligible.
3. **Price data loading**: warmup phase only; one disk read per
   secondary at startup. Negligible during the K-build loop because
   `_PRICE_CACHE` is hot.
4. **Signal library / PKL loading**: dominant per-cell cost path via
   `_next_signal_from_pkl` -> `_pair_asof` and via
   `_processed_signals_from_pkl`. Cached at the `_PROCESSED_SIGNALS_CACHE`
   module level after first hit but the first hit is the expensive one,
   and PKLs are read once per primary member per build.
5. **Per-primary or per-member signal computation**: lives inside the
   subset worker (`_subset_metrics_spymaster_bitmask` here) - this is the
   surface that scales with subset count and thus with K.
6. **DataFrame merge/transform work**: the 52k DateTimeIndex iterations
   and pandas object construction stack are the dominant fixed-cost layer.
7. **Callback/UI formatting overhead**: not invoked in the benchmark
   path; the Dash callbacks (`_refresh`, `update_tooltips`) sit inside
   `make_app` and were not exercised.
8. **Output serialization**: zero (benchmark does not serialize to disk).
9. **Cache misses vs cache hits**: warmup pre-populated price cache for 2/8
   secondaries. Cache misses for the other 6 short-circuit immediately;
   no retry/backoff path is exercised.
10. **Threading/parallel execution**: zero parallelism active at K1/K2/K3
    by default (per section 6).

## 13. Runtime Classification

**FAST** for K=1, K=2, K=3 against Phase 6I-79 leaderboards (1 row per K)
on the secondaries with usable on-disk price cache (AAPL, SPY): every cell
completes in under 1 second median, with clean ~2x per-K scaling driven by
the `2^N - 1` subset count.

The benchmark is therefore consistent with the WATCH expectation around
higher K rather than SLOW: extrapolated K=6 single-threaded per-build wall
time is ~6 seconds for the 1-row leaderboard density, which is operationally
feasible if `PARALLEL_SUBSETS=1` is enabled at K>=4 (a 4-worker thread pool
would knock that toward ~2 s per build on a multi-core machine).

The cells for which 0 rows were produced (6 of 8 secondaries) are NOT
classified as SLOW; they are classified as **DATA-GATED**: the
30-130 ms wall time there is the cost of correctly detecting and short-
circuiting on a missing-price-cache condition.

There is no BLOCKED outcome: a safe in-process benchmark of the K1/K2/K3
compute path was achievable without modifying any production code, without
launching Dash, without invoking any other engine, and without network
fetches.

## 14. Risks and Limitations

- Only 2 of 8 secondaries (AAPL, SPY) produced full-pipeline timing data;
  the other 6 lacked an on-disk price cache. The 6 cells with 0 rows are
  honest measurements of an early-exit code path, not full per-build
  timing. To produce 8/8 full-pipeline timing data, the operator would
  need either: (a) freshly populated price caches via
  `refresh_secondary_caches(...)` against yfinance, or (b) a copy of an
  external prices archive into `price_cache/daily/`.

- The Phase 6I-79 leaderboards retain only 1 row per K (beam-search single
  best-build per K). A general TrafficFlow workload may consume
  leaderboards with many rows per K; per-cell wall time scales linearly
  in row count. The extrapolated K=6 / K=12 numbers in section 10 assume
  the 1-row density.

- The benchmark is single-threaded by default (`PARALLEL_SUBSETS=0`).
  Enabling `PARALLEL_SUBSETS=1` at K>=4 changes the cost shape; this audit
  did not measure that variant because K=1/2/3 never crosses the K>=4
  parallelism gate.

- The cProfile snapshot is one cell (SPY K=2) with one row of input.
  Larger K and larger leaderboards may shift the relative weight of
  `_subset_metrics_spymaster_bitmask` vs the pandas-overhead surface.

- The reported wall times include first-cold-cache effects on the FIRST
  call of every (sec, K) cell - `wall_max` is consistently the first run
  because PKL caches are loaded lazily. `wall_min` and `wall_med` reflect
  warm-PKL-cache behavior.

- No correctness check was performed against legacy TrafficFlow output;
  this audit measures performance and code-path shape only. The structural
  diff doc covers the LEGACY-vs-current behavioral surface.

## 15. Recommendation Before Headless Implementation

The K1/K2/K3 compute path runs in sub-second wall time per (secondary, K)
cell against the Phase 6I-79 1-row-per-K leaderboards, with predictable
~2x per-K scaling that matches the `2^N - 1` subset count. Extrapolated
K=6 single-threaded wall is ~6 s/build and K=12 is ~7 min/build; both are
operationally feasible with the existing default fastpath
(`TF_BITMASK_FASTPATH=1`) and become substantially better with
`PARALLEL_SUBSETS=1` at K>=4.

Recommended next steps before the TrafficFlow headless conversion sprint
begins:

1. **Proceed toward headless conversion.** No fundamental compute-path
   performance blocker has been identified at K=1/2/3. The compute path is
   directly callable as `build_board_rows(sec, k, run_fence, missing_map)`
   without Dash and without launching any engine.

2. **Re-run this benchmark with `PARALLEL_SUBSETS=1` against a fixture
   leaderboard that retains many rows per K** (or against a denser real
   leaderboard if one becomes available). The K=1/2/3 cells cannot
   exercise the per-subset threading gate; a K=4/5/6 measurement IS the
   actionable parallelism evidence for the headless runner.

3. **Pre-populate or warm-fetch secondary price caches before any future
   benchmark.** AMZN/GOOGL/META/MSFT/NVDA/TSLA need their own
   `price_cache/daily/<SEC>.csv` for the build path to produce non-zero
   rows. This is exactly the kind of upstream-data-warmth precondition
   the headless runner CLI will need to declare explicitly.

4. **Treat the structural-diff section 14 "make_app default sweep" item as
   closed.** This benchmark confirms that the Dash UI surface is not on
   the critical compute path and the K1/K2/K3 build can be exercised
   entirely without `make_app`. The headless runner's argparse defaults
   should still be compared against `make_app`'s Dash component defaults
   when the runner is built, per the existing defaults-diff audit pattern.

5. **Capture per-subset timing inside
   `_subset_metrics_spymaster_bitmask`** during the headless conversion;
   it is the dominant per-K cost surface and any future K-scaling work
   will start there.

## Notes on this evidence task

- This is a benchmark/profiling evidence task. No TrafficFlow headless
  implementation was performed.
- No TrafficFlow Dash server was launched at any point.
- No production code was modified.
- No tests were modified.
- No canonical StackBuilder artifact under `output/stackbuilder/` was
  modified.
- No engines (StackBuilder / OnePass / ImpactSearch / Spymaster /
  Confluence / multi_timeframe_builder) were run.
- A controlled, in-process import of `trafficflow` was performed after the
  static preflight confirmed import safety. Three module-level functions
  (`_fetch_secondary_from_yf`, `_needs_refresh`, `_is_truncated_history`)
  were monkey-patched in process to refuse network fetches and trust the
  on-disk cache; no source file was modified.
- K1/K2/K3 sub-second wall times give confidence to proceed toward
  TrafficFlow headless implementation. The bottleneck is the per-subset
  bitmask worker and PKL signal lookup, not Dash or callback overhead.
