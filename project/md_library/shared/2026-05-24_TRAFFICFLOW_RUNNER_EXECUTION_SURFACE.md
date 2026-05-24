# TrafficFlow Runner Execution Surface (Phase A Scoping)

## 1. Scope and Non-Goals

This document is the Phase A scoping artifact for a future headless
TrafficFlow runner. It is documentation only. It does not implement,
launch, or modify any TrafficFlow code, test, runner, engine, or
generated artifact.

Scope:

- Cite the already-merged TrafficFlow evidence docs as source of truth.
- Fill targeted gaps not covered by those docs: TrafficFlow's output
  surface, its existing (non-)CLI surface, the runner-relevant input
  surface summary, the mutable-state runner-safety classification, and
  the output atomicity gap.
- Specify the proposed future runner contract: identity, dry-run-first
  shape, CLI flag inventory, locked v1 defaults, upstream
  `selected_build.json` consumption contract, input readiness contract,
  downstream handoff contract, and reporting/monitoring discipline.
- Lock the v1 decisions that the future Phase B implementation must
  inherit.
- Lay out a Phase A through Phase F implementation plan.
- Enumerate deferred work for Phase B and beyond.

Non-goals:

- No runner implementation in this PR.
- No code, test, or engine modifications in this PR.
- No engine, app, runner, production pipeline, benchmark, or test suite
  is run by this task.
- No Dash launch.
- No re-derivation of work already established in the merged evidence
  docs (function inventory, callback inventory, default-value sweep,
  LEGACY-vs-current history, threading-parity history, K1/K2/K3/K4/K6
  performance benchmark, PKL type investigation, max-SMA verification,
  recent change-history audit).
- No finalization of the downstream MTF / Confluence contract.
- No final K-scaling acceleration strategy.

## 2. References

Authoritative merged evidence docs cited throughout this scoping doc:

- `md_library/shared/2026-05-23_TRAFFICFLOW_LEGACY_VS_CURRENT_STRUCTURAL_DIFF.md`
  Established: LEGACY-vs-current file-level diff; function and class
  inventory; Dash callback inventory; Dash component default sweep;
  `ThreadPoolExecutor` parity (7 references in both versions); matrix-
  path code removal; `canonical_scoring` + `provenance_manifest`
  delegation; refresh-callback error surfacing; `SPYMASTER_PKL_DIR`
  move from hard-coded local absolute path to env-var-with-project-
  relative default.
- `md_library/shared/2026-05-23_TRAFFICFLOW_K1_K3_BENCHMARK_EVIDENCE.md`
  Established: `trafficflow.py` safe-import characteristics;
  `build_board_rows(sec, k, run_fence, missing_map)` as the direct
  compute entry point; K filtering at `df['K'] == int(k)`;
  `compute_build_metrics_spymaster_parity` dispatch path;
  `_subset_metrics_spymaster_bitmask` as the bottleneck primitive;
  `PARALLEL_SUBSETS=0` default; `PARALLEL_SUBSETS_MIN_K=4`; K1/K2/K3
  single-threaded under defaults.
- `md_library/shared/2026-05-23_TRAFFICFLOW_READINESS_AND_K_BENCHMARK_EVIDENCE.md`
  Established: TrafficFlow consumes
  `cache/results/<TICKER>_precomputed_results.pkl` (NOT OnePass
  `signal_library/data/stable/` PKLs); `PRJCT9_SPYMASTER_PKL_DIR`
  defaults to `cache/results`; `signal_engine_cache_refresher.py` is
  the bounded CLI tool for missing TrafficFlow PKLs; `--max-sma-day 114`
  must be explicitly passed when generating TrafficFlow PKLs (the
  tool default is 30); 47 of 47 pre-existing required PKLs verified
  at `max_sma_day == 114`; 14 missing PKLs were generated cleanly via
  the refresher with `--max-sma-day 114`; all 40 (secondary, K)
  cells across 8 secondaries x K1/K2/K3/K4/K6 benchmarked
  successfully after cache warming; K=6 sum across 8 secondaries is
  approximately 17.1 s serial / 17.6 s parallel under
  `PARALLEL_SUBSETS=1`; `PARALLEL_SUBSETS=1` produces no useful
  speedup (GIL-bound numerical work); serial vs parallel determinism
  is preserved (all 39 non-empty cell row hashes match);
  `_subset_metrics_spymaster_bitmask` remains the dominant bottleneck
  primitive. The earlier conversation-only Codex PKL-type
  investigation is captured by this doc and is NOT cited as a
  separate standalone evidence document.
- `md_library/shared/2026-05-23_PHASE_6I_79_STACKBUILDER_PRODUCTION_RUN_EVIDENCE.md`
  Established: canonical StackBuilder outputs exist for all 8
  ImpactSearch secondaries (AAPL, AMZN, GOOGL, META, MSFT, NVDA,
  SPY, TSLA); per-secondary `selected_build.json` updated;
  `combo_leaderboard.xlsx` plus per-K `combo_k=N.json` and
  `cohort.xlsx`, `rank_all.xlsx`, `run_manifest.json`, `summary.json`
  artifacts exist under each `selected_run_dir`.

Runner pattern references (read-only inspection, no execution):

- `onepass_workbook_runner.py`
- `impactsearch_workbook_runner.py`
- `stackbuilder_workbook_runner.py`

## 3. Current TrafficFlow Execution Surface

[CITED] Source: PR #300 / PR #301 readiness evidence.

Current TrafficFlow operates as a Dash UI:

- `make_app()` at `trafficflow.py:3079` constructs the Dash app.
- `main()` at `trafficflow.py:3386` calls `make_app()` and then
  `app.run_server(...)` at `trafficflow.py:3406`.
- The `__name__ == "__main__"` guard at `trafficflow.py:3419`
  (`if __name__ == "__main__" and not __TF_ALREADY_STARTED`) is the
  only entry point that launches Dash. Importing `trafficflow.py` as a
  module does NOT launch Dash and does NOT call `make_app()`.
- The compute path `build_board_rows(sec, k, run_fence, missing_map)`
  at `trafficflow.py:2956` is directly callable without any Dash
  construction. PR #300 and PR #301 used exactly this entry point for
  their benchmarks.

User-driven board refresh is implemented as a Dash callback
(`_refresh`, registered inside `make_app`) that invokes
`build_board_rows` and renders the result in the in-memory Dash table.
The board itself is in-memory in the running Dash server; nothing is
persisted to disk as a TrafficFlow "board output" artifact.

## 4. Output Surface Audit

[GAP] Source: targeted static inspection in this task.

Static scan of `trafficflow.py` for file-write call sites
(`.to_csv(`, `.to_excel(`, `.to_parquet(`, `to_pickle(`,
`.write_text(`, `.write_bytes(`, `pickle.dump(`, `json.dump(`,
`joblib.dump(`, `with open(... 'w' / 'a' / 'wb' / 'ab')`) found
exactly five write call sites:

| File:line | Target | Purpose |
|---|---|---|
| `trafficflow.py:165` | `debug_dumps/<NAME>_<TS>.csv` | `_dump_csv` debug helper; only fires if invoked. Not in the normal Dash board flow. |
| `trafficflow.py:907` | `price_cache/daily/<SEC>.parquet` (tmp) | `_persist_cache` price-cache write inside `_yf_fetch_incremental` path. |
| `trafficflow.py:914` | `price_cache/daily/<SEC>.csv` (tmp) | `_persist_cache` price-cache write inside `_yf_fetch_incremental` path. |
| `trafficflow.py:1112` | `price_cache/daily/<SEC>.parquet` (tmp) | `_write_cache_file` price-cache writer. |
| `trafficflow.py:1114` | `price_cache/daily/<SEC>.csv` (tmp) | `_write_cache_file` price-cache writer. |

Findings:

- All five write call sites target either `price_cache/daily/` (price-
  cache helper) or `debug_dumps/` (rarely-invoked debug helper).
- Current TrafficFlow does **not** write durable board/output artifacts
  during normal Dash UI operation. The board lives in the Dash table
  in-memory only.
- Dash refresh callbacks do not persist board state to disk beyond the
  price-cache helper writes above and the
  `_REFRESH_ISSUES_DISPLAY_LIMIT`-capped on-screen issue list.
- `canonical_scoring` and `provenance_manifest` are consumed read-only
  by TrafficFlow's compute path; no provenance-manifest writes occur
  in TrafficFlow's own code path.
- The price-cache writes are bounded to `PRICE_CACHE_DIR` (default
  `price_cache/daily`) and use a `.tmp` -> `replace` atomic pattern
  (see `trafficflow.py:1110-1115` for the `_write_cache_file` shape).

Conclusion: current TrafficFlow is UI-only for board output and
produces no durable TrafficFlow board artifacts. The future runner
must define a new artifact contract from scratch (see section 14
Downstream Handoff Contract).

## 5. Existing CLI Surface

[GAP] Source: targeted static inspection in this task.

Static scan of `trafficflow.py` for `argparse`, `ArgumentParser`,
`add_argument`, and `sys.argv` found **zero** matches.

Current TrafficFlow has no headless CLI. The only command-line shape
exposed by the file is `python trafficflow.py`, which triggers the
Dash server via the `__main__` guard at `trafficflow.py:3419`. There
is no `--ticker`, no `--secondaries`, no `--write`, no `--dry-run`,
no structured JSON stdout, no progress-vs-result separation, and no
write-gate flag.

Conclusion: the future runner must provide the entire CLI surface; no
existing CLI surface in `trafficflow.py` needs to be honored or
preserved (other than not breaking the Dash `__main__` entrypoint by
modifying the module).

## 6. Required Input Surfaces Summary

Synthesized from cited evidence and targeted inspection.

### 6.1 StackBuilder upstream

[CITED] PR #301 readiness evidence + [GAP] static-inspection finding
in this task.

What current TrafficFlow reads today:

- Per secondary, `_find_latest_combo_table(sec)` at
  `trafficflow.py:619` scans `output/stackbuilder/<SEC>/` for all
  subdirectories, picks the most recent run dir by `st_ctime`, and
  looks for `combo_leaderboard.parquet`, then `.xlsx`, then `.csv`.
- The chosen file is read by `_read_table()` at `trafficflow.py:661`
  via pandas. Rows are then filtered to the requested K.
- [GAP] Static substring scan for `selected_build.json` in
  `trafficflow.py` returned **zero hits**. Current TrafficFlow does
  NOT consume the per-secondary `selected_build.json` pointer
  introduced by Phase 6I-79 StackBuilder.
- The implicit "latest by ctime" directory-listing fallback in
  `_find_latest_combo_table` is exactly the silent-fallback pattern
  the future runner must avoid (see section 12).

What the future runner should consume:

- `output/stackbuilder/<SECONDARY>/selected_build.json` per secondary,
  read fresh on every invocation, with no cross-run cache.
- The runner must refuse a secondary when `selected_build.json` is
  missing unless `--explicit-build` is provided.
- The runner must never silently fall back to a latest-by-ctime
  directory scan.

### 6.2 Spymaster / signal-engine PKLs

[CITED] PR #301 readiness evidence.

- TrafficFlow loads
  `<SPYMASTER_PKL_DIR>/<TICKER>_precomputed_results.pkl` per member
  ticker (see `load_spymaster_pkl` at `trafficflow.py:1442` and
  `_processed_signals_from_pkl` at `trafficflow.py:1562`).
- `SPYMASTER_PKL_DIR` is `os.environ.get('PRJCT9_SPYMASTER_PKL_DIR',
  str(_PROJECT_DIR / 'cache' / 'results'))` (`trafficflow.py:86-89`).
- Required schema fields used by the compute path: `preprocessed_data`,
  `active_pairs`, `daily_top_buy_pairs`, `daily_top_short_pairs` (per
  PR #301 section 3.1).
- The `max_sma_day == 114` requirement is enforced by readiness
  classification, not by the PKL loader itself; the loader will accept
  any pickle dict that has the four required fields. The runner must
  apply the max-SMA gate before calling compute, per the readiness
  contract.
- Manifest sidecars at
  `cache/results/<TICKER>_precomputed_results.pkl.manifest.json` may
  carry `max_sma_day` at the top level (legacy schema) or
  `params.max_sma_day` (new schema produced by
  `signal_engine_cache_refresher.py`). The readiness inspector must
  honor both surfaces.

### 6.3 Secondary price cache

[CITED] PR #301 readiness evidence + [GAP] env-var enumeration.

- `_load_secondary_prices` at `trafficflow.py:1117` loads via in-memory
  `_PRICE_CACHE` first, then disk via `_choose_price_cache_path`
  (`trafficflow.py:1065`) under `PRICE_CACHE_DIR` (default
  `price_cache/daily`), then yfinance fetch via
  `_fetch_secondary_from_yf` (`trafficflow.py:1027`) if the cache is
  missing or `_needs_refresh` returns True.
- `refresh_secondary_caches(symbols, force=False)` at
  `trafficflow.py:1157` is the bounded operator-facing refresh helper.
  It writes only to `PRICE_CACHE_DIR` and was used by PR #301's
  bounded repair phase (Phase A) and by the PR #301 amendment.
- The runner must offer an explicit `--refresh-stale-prices` flag that
  routes through this bounded helper; absent the flag, stale or
  missing secondary caches must be classified as `DATA-GATED` and the
  affected cells must be skipped, not silently network-fetched.

### 6.4 Signal-library

[CITED] PR #301 readiness evidence.

TrafficFlow does NOT consume `signal_library/data/stable/` PKLs. This
is a different PKL family produced by OnePass. The runner must not
substitute OnePass stable PKLs for Spymaster precomputed-result PKLs.

### 6.5 Environment variables read by trafficflow.py

[GAP] Static enumeration of `os.environ.get(...)` lines.

| Env var | Line | Default | Purpose / runner relevance |
|---|---|---|---|
| `STACKBUILDER_RUNS_ROOT` | 85 | `output/stackbuilder` | StackBuilder run-root override. Runner should expose `--stackbuilder-root`. |
| `PRJCT9_SPYMASTER_PKL_DIR` | 86 | `<PROJECT_ROOT>/cache/results` | Spymaster PKL dir. Runner should NOT override per-invocation. |
| `PRICE_CACHE_DIR` | 90 | `price_cache/daily` | Price cache dir. Runner should NOT override per-invocation. |
| `TRAFFICFLOW_PORT` | 84 | `8055` | Dash port. Runner does not launch Dash; not applicable. |
| `RISK_FREE_ANNUAL` | 92 | `5.0` | Sharpe rf rate. Runner should expose as advanced toggle (defer). |
| `TF_SHOW_SESSION_SANITY` | 94 | `1` | Enables session-sanity print path. Runner should set `0` to suppress noise in stdout JSON. |
| `PKL_TTL_HOURS` | 417 | `0` | PKL TTL gate. Runner default 0 (disabled). |
| `TF_CACHE_TTL_INDEX_DAYS` / `TF_CACHE_TTL_EQUITY_DAYS` / `TF_CACHE_TTL_CRYPTO_DAYS` / `TF_CACHE_TTL_CURRENCY_DAYS` | 172-175 | `1` / `1` / `0` / `0` | Type-aware cache TTL days. Runner should set high TTL during compute to avoid surprise refreshes. |
| `TF_REFRESH_BACKFILL_DAYS` | 176 | `10` | Backfill window for refresh. Runner should set `0` during compute. |
| `TF_EXCHANGE_BUFFER_MIN` | 177 | `10` | Equity session buffer minutes. |
| `TF_ROLLOVER_VERBOSE` | 197 | `1` | Crypto UTC rollover guard verbosity. Runner should set `0`. |
| `TF_CRYPTO_STRICT_MISSING_DAY` | 199 | `1` | Crypto strict missing-day gate. |
| `_REFRESH_ISSUES_DISPLAY_LIMIT` | 219 | `10` | Module constant; not an env var override. |
| `PARALLEL_SUBSETS` | 241 | `0` | Per-subset thread parallelism gate. Runner should expose `--parallel-subsets`. |
| `PARALLEL_SUBSETS_MIN_K` | 243 | `4` | K floor for subset parallelism. |
| `TRAFFICFLOW_SUBSET_WORKERS` | 244 | `4` | Thread worker count for subset parallelism. Runner should expose `--subset-workers`. |
| `TRAFFICFLOW_PRELOAD_CACHE` | 246 | `0` | Optional preload of all required PKLs at startup. |
| `TF_FORCE_FULL_PRICE_REFRESH_ON_CLICK` | 247 | `0` | UI-only refresh-click behavior. Runner should not set. |
| `TF_POST_INTERSECT_FASTPATH` | 252 | `0` | Subset compute variant; OFF by default. Runner should expose `--tf-post-intersect-fastpath` (deferred). |
| `TF_BITMASK_FASTPATH` | 258 | `1` | Subset compute variant; ON by default. Runner should expose `--tf-bitmask-fastpath`. |
| `TF_AUTO_PRICE_REFRESH_ON_FIRST_LOAD` | 262 | `0` | UI-only auto-refresh behavior. Runner should not set. |
| `TF_CAP_TO_TODAY` | 264 | `1` | Cap-date behavior. Keep default in runner. |
| `TF_AVERAGES_DROP_NONE` | 266 | `0` | Subset-averaging behavior. Keep default in runner. |
| `TF_ASSERT_NO_JITTER` | 3055 | `0` | Dev-only jitter assertion. Runner should not set. |

The runner must surface every env var actually consumed in
`effective_config` so the operator can read the resolved values.

## 7. Mutable State Risk Assessment for Runner

[GAP] Source: static enumeration of module-level state in
`trafficflow.py`.

| State surface | Line | Default | Runner handling |
|---|---|---|---|
| `PRJCT9_SPYMASTER_PKL_DIR` (env) | 86 | resolved at import | LEAVE ALONE as read-only config. Runner inherits whatever value is set in the parent environment at subprocess launch. |
| `PARALLEL_SUBSETS` (env) | 241 | `0` | LEAVE ALONE by default; expose `--parallel-subsets` to set the env var BEFORE importing `trafficflow.py` (it is read at module-import time and cached on the module attribute). |
| `PARALLEL_SUBSETS_MIN_K` (env) | 243 | `4` | LEAVE ALONE; not flagged. |
| `TF_BITMASK_FASTPATH` (env) | 258 | `1` | LEAVE ALONE by default; expose `--tf-bitmask-fastpath` only if the runner needs to opt out. |
| `TRAFFICFLOW_SUBSET_WORKERS` (env) | 244 | `4` | LEAVE ALONE by default; expose `--subset-workers`. |
| `_PKL_CACHE` (module dict) | 288 | empty | ISOLATE PER INVOCATION: the runner is a fresh subprocess per call (Phase B subprocess shape); module-import re-creates the empty dict. No reset needed within a single invocation if it processes a bounded universe. For multi-secondary loops, the dict can grow; the runner should call `_clear_runtime(preserve_prices=True)` (`trafficflow.py:324`) between secondaries to bound RAM, or accept the growth on a small (~8-secondary) workload. |
| `_PRICE_CACHE` (module dict) | 289 | empty | ISOLATE PER INVOCATION same as `_PKL_CACHE`. Preserved by `_clear_runtime(preserve_prices=True)`. |
| `_SIGNAL_SERIES_CACHE` (module dict) | 302 | empty | ISOLATE PER INVOCATION. Not cleared by `_clear_runtime` (intentional - "Keep _SIGNAL_SERIES_CACHE to avoid reprocessing PKLs repeatedly"). Runner inherits this behavior. |
| `_LAST_REFRESH_N` (int) | 303 | -1 | DASH-UI STATE; the runner does not engage the Dash refresh-callback path. LEAVE ALONE. |
| `_FORCE_PRICE_REFRESH` (bool) | 304 | False | DASH-UI STATE; same as above. |
| `_SEC_POSMAP_CACHE` (module dict) | 307 | empty | ISOLATE PER INVOCATION; cleared by `_clear_runtime`. |
| `_POSSET_CACHE` (module dict) | 309 | empty | ISOLATE PER INVOCATION; cleared by `_clear_runtime`. |
| `_MASK_CACHE` (module dict) | 311 | empty | ISOLATE PER INVOCATION; bitmask fastpath cache. Not currently cleared by `_clear_runtime`. RUNNER-SAFETY RISK at scale: a long-running runner that processes many secondaries can grow this dict unbounded. v1 runner runs one invocation per process and exits, so risk is bounded for v1. |
| `_PKL_FRESH_MEMO` (module dict) | 316 | empty | ISOLATE PER INVOCATION; freshness memoization. Cleared by process exit. |
| `_FROZEN_CAP_END` (module dict) | 273 | empty | ISOLATE PER INVOCATION; cleared by `_clear_runtime`. |
| `__TF_ALREADY_STARTED` (module bool) | 3417 | False | DASH-UI STATE; guards `main()` re-entry. Runner does not call `main()`; LEAVE ALONE. |
| Dash global state | 3079-3392 | constructed in `make_app()` | RUNNER MUST NOT CALL `make_app()`. The runner imports `trafficflow.py` and calls `build_board_rows` directly. |
| `@lru_cache` decorators | 0 in current source | n/a | None present; not applicable. |

Summary:

- The v1 runner is one process per invocation. All module-level dicts
  are scoped to the process and disappear at exit. No cross-invocation
  state leak.
- Multi-secondary loops within a single invocation can grow
  `_PKL_CACHE`, `_PRICE_CACHE`, `_SIGNAL_SERIES_CACHE`, and
  `_MASK_CACHE`. The v1 default of `--jobs 1` plus the 8-secondary
  reference universe makes this acceptable for v1.
- The runner MUST NOT touch `_LAST_REFRESH_N`, `_FORCE_PRICE_REFRESH`,
  or `__TF_ALREADY_STARTED` (Dash-UI state).
- The runner MUST NOT call `make_app()` or `app.run_server(...)`.

## 8. Output Atomicity Gap

[GAP] Source: targeted inspection in this task.

Current TrafficFlow does not write durable TrafficFlow board/output
artifacts (section 4). The only writes are price-cache helper writes
under `price_cache/daily/`, and those use a `.tmp` -> `replace`
atomic pattern:

- `_write_cache_file` at `trafficflow.py:1109-1115` writes to
  `<path>.tmp` then `tmp.replace(p)`.
- `_persist_cache` at `trafficflow.py:900-918` follows the same shape.

Consequences for the future runner:

- No current atomicity contract exists for TrafficFlow board/output
  artifacts because no such artifacts exist today.
- The future runner must define an atomic-write and history-retention
  policy from scratch (section 14 Downstream Handoff Contract).
- The runner should mirror the existing `.tmp` -> `replace` pattern
  for its own artifact writes and should not invent ad-hoc partial-
  write recovery.

## 9. Proposed Future Runner Contract

[PROPOSED] Future runner behavior; not implemented.

### 9.1 Runner identity

Proposed name: **`trafficflow_runner.py`**.

Rationale: PR #301 established that current TrafficFlow has no durable
workbook artifact (section 4). The other runners
(`stackbuilder_workbook_runner.py`,
`impactsearch_workbook_runner.py`) are named for the XLSX workbook
they emit. TrafficFlow's v1 runner output is closer to a per-secondary
+ per-K board-row JSON / XLSX bundle than a single workbook; the
generic `_runner.py` suffix is the right Phase A choice. If Phase C
smoke proves a workbook-centered durable output is the right v1
artifact, the file can be renamed at that time. Phase A locks the
name `trafficflow_runner.py` as the proposed identity but does not
implement the file.

State: Phase B will implement the runner. Phase A does not.

### 9.2 Dry-run-first runner contract

[PROPOSED]

- Dry-run is the default. The runner does NOT write any artifact
  unless `--write` is passed explicitly.
- `--write` is required for any artifact write under `--output-dir`.
- Dry-run computes readiness classification, resolves
  `selected_build.json` per secondary, classifies missing/stale
  inputs, and reports the proposed plan in stdout JSON. Heavy compute
  (the per-K `build_board_rows` invocation) does NOT fire in dry-run
  unless explicitly documented and authorized.
- Stdout is structured JSON. Progress, status, warnings, and run-time
  logs go to stderr.
- Isolated output via `--output-dir <SOME_ISOLATED_DIR>` is supported
  for smoke testing; canonical output via the locked default
  `output/trafficflow/` is supported but write-gated.
- The runner performs a process-conflict check before importing the
  TrafficFlow module (see section 15).
- Per-secondary continuation: when one secondary fails, the runner
  records the failure in stdout JSON and continues to the next
  secondary unless `--strict-inputs` is passed.
- Fail closed: missing `selected_build.json` -> the affected secondary
  is REFUSED unless `--explicit-build <RUN_DIR>` is provided. No
  implicit latest-directory fallback.
- No Dash dependency in the runner's import path. The runner imports
  `trafficflow.py` lazily, after the dry-run plan is computed.
- No yfinance / network fetch unless `--allow-network-fetch` is
  explicitly passed AND `--write` is also passed (for cache writes).
- The runner captures or redirects `trafficflow.py` import-time
  stdout/stderr if any leaks (PR #300 / #301 showed clean imports;
  this is a defensive measure).
- The `effective_config` block in stdout JSON contains every resolved
  flag value and every env var the runner read at startup.

## 10. Proposed Future Runner CLI Shape

[PROPOSED] Future runner CLI; not implemented.

Each flag is marked `[PROPOSED]`, `[LOCKED v1]`, or `[DEFERRED]`.
Patterns are informed by `stackbuilder_workbook_runner.py:118-226`,
`onepass_workbook_runner.py`, and `impactsearch_workbook_runner.py`.

Secondary selection:

| Flag | Status | Default | Purpose |
|---|---|---|---|
| `--secondaries` | [LOCKED v1] | none | Comma-separated secondary list (e.g. `AAPL,SPY,...`). At least one of `--secondaries` / `--secondaries-file` required. |
| `--secondaries-file` | [LOCKED v1] | none | File with one secondary per line. |

StackBuilder input source:

| Flag | Status | Default | Purpose |
|---|---|---|---|
| `--stackbuilder-root` | [LOCKED v1] | `output/stackbuilder` | StackBuilder root path (the parent of per-secondary dirs). |
| `--use-selected-build` | [LOCKED v1] | `true` | Consume per-secondary `selected_build.json`. v1 has no alternative. |
| `--explicit-build` | [PROPOSED] | none | Override `selected_run_dir` for a single-secondary smoke run. When set, records `explicit_build_override=true` in stdout JSON. |

TrafficFlow output:

| Flag | Status | Default | Purpose |
|---|---|---|---|
| `--output-dir` | [LOCKED v1] | `output/trafficflow` | TrafficFlow output root. Canonical default; isolated dirs accepted for smoke. |

K / artifact options:

| Flag | Status | Default | Purpose |
|---|---|---|---|
| `--k` | [PROPOSED] | none | Single K override. |
| `--k-range` | [PROPOSED] | `1-12` | Range of K levels to materialize. |
| `--all-selected-k` | [PROPOSED] | `true` | Consume all K levels present in `selected_build.json` / leaderboard. |
| `--board-format` | [PROPOSED] | `xlsx,json` | Format(s) of the per-secondary board artifact. |
| `--artifact-format` | [DEFERRED] | n/a | Reserved for Phase C artifact-bundle work. |

Concurrency:

| Flag | Status | Default | Purpose |
|---|---|---|---|
| `--jobs` | [LOCKED v1] | `1` | Number of concurrent secondaries processed by the runner. v1 is `--jobs 1`. |

TrafficFlow compute toggles:

| Flag | Status | Default | Purpose |
|---|---|---|---|
| `--parallel-subsets` | [LOCKED v1] | `0` | Sets `PARALLEL_SUBSETS` env before importing `trafficflow.py`. v1 default `0` per PR #301 evidence; PARALLEL_SUBSETS=1 produces no useful speedup. |
| `--subset-workers` | [PROPOSED] | `4` | Sets `TRAFFICFLOW_SUBSET_WORKERS`. Only effective when `--parallel-subsets 1`. |
| `--tf-bitmask-fastpath` | [PROPOSED] | `1` | Sets `TF_BITMASK_FASTPATH`. v1 default `1` per evidence. |

PKL readiness / repair:

| Flag | Status | Default | Purpose |
|---|---|---|---|
| `--refresh-missing-pkls` | [LOCKED v1] | `false` | Invoke `signal_engine_cache_refresher.py --ticker <T> --write --cache-dir cache/results --status-dir cache/status --max-sma-day 114` per missing/stale PKL. |
| `--max-sma-day` | [LOCKED v1] | `114` | Passed to every refresher invocation. Locked at 114 for v1 per PR #301 max-SMA-day verification. |

Price cache readiness / repair:

| Flag | Status | Default | Purpose |
|---|---|---|---|
| `--refresh-stale-prices` | [LOCKED v1] | `false` | Call `trafficflow.refresh_secondary_caches([sec], force=False)` per stale or missing secondary cache. |

Network/write gates:

| Flag | Status | Default | Purpose |
|---|---|---|---|
| `--write` | [LOCKED v1] | `false` | Required for any artifact write. Dry-run is the default. |
| `--allow-network-fetch` | [LOCKED v1] | `false` | Required for yfinance fetches via `--refresh-missing-pkls` or `--refresh-stale-prices`. |
| `--duration-budget-minutes` | [PROPOSED] | none | Operator-supplied wall-clock budget; runner aborts gracefully if exceeded. |
| `--operator-budget-label` | [PROPOSED] | none | Free-text label recorded in run log for budget-overrun audits. |

Runner hygiene:

| Flag | Status | Default | Purpose |
|---|---|---|---|
| `--no-progress` | [PROPOSED] | `false` | Suppress progress JSON sampling for CI/test invocations. |
| `--progress-dir` | [PROPOSED] | `output/trafficflow/_progress` | Per-invocation progress sample directory. |
| `--strict-inputs` | [PROPOSED] | `false` | If any secondary is `DATA-GATED` / `PKL-GATED` / `MAX-SMA-GATED` / `STALE-GATED`, the runner refuses the whole invocation. Default behavior is per-secondary skip. |
| `--skip-secondary-on-input-gate` | [PROPOSED] | `true` | Default: skip individual secondaries when their inputs are not ready; record in `per_secondary_results`. |

None of the flags above implies that `trafficflow.py` already
supports them; they are all the future runner's responsibility.

## 11. Locked v1 Defaults

[LOCKED v1] Operator-locked decisions for Phase B unless later
superseded.

- `use_selected_build = true`.
- `stackbuilder_root = output/stackbuilder`.
- `output_dir = output/trafficflow`.
- `jobs = 1`.
- `parallel_subsets = 0`.
- `write = false` by default.
- `allow_network_fetch = false` by default.
- `refresh_missing_pkls = false` by default.
- `refresh_stale_prices = false` by default.
- `max_sma_day = 114` when refreshing PKLs.
- No implicit latest-directory fallback.
- Explicit build override allowed ONLY via `--explicit-build`.

Clarifications:

- These are v1 runner defaults, not proof that they are globally
  optimal.
- Phase C smoke and Phase D measurement may revise operational
  recommendations.
- No "optimized" or "fast" label may be applied to the runner without
  benchmark numbers.
- `PARALLEL_SUBSETS=0` default is evidence-backed by PR #301
  (`PARALLEL_SUBSETS=1` produced no useful speedup; serial / parallel
  hashes match).
- Do not default `PARALLEL_SUBSETS=1` in any future revision without
  new evidence demonstrating measurable speedup on richer workloads.

## 12. Upstream selected_build.json Contract

[PROPOSED] Runner-introduced contract; current TrafficFlow does not
consume `selected_build.json`.

Per-secondary file path:

```
output/stackbuilder/<SECONDARY>/selected_build.json
```

Required fields (from PR #301 readiness evidence + a fresh inspection
of one Phase 6I-79 selected_build.json sample):

- `schema_version`
- `secondary`
- `selected_run_id`
- `selected_run_dir` (repo-relative path)
- `selected_k`
- `selected_metric`
- `total_capture`
- `sharpe_ratio`
- `row_count`
- `created_at`
- `selected_at`
- `selection_policy`
- `operator_pinned`
- `source_manifest_path`
- `runner_version`
- `selection_policy_context`

The runner must:

- Refuse a secondary when `selected_build.json` is missing UNLESS
  `--explicit-build <RUN_DIR>` is provided.
- Never silently fall back to a latest-by-ctime directory listing
  (the pattern in `_find_latest_combo_table` at
  `trafficflow.py:619-659`).
- Surface the consumed `selected_build.json` contents (or at least its
  `selected_run_dir`, `selected_k`, `selection_policy`, and SHA-256)
  under `selected_build_consumed` in stdout JSON, per secondary.
- Re-read `selected_build.json` fresh on each invocation; no
  cross-invocation cache.
- Record `explicit_build_override=true` in stdout JSON when
  `--explicit-build` is used, plus the override target dir.
- Record the consumed `selected_run_dir` repo-relative only (no
  absolute paths).

## 13. Input Readiness Contract

[PROPOSED]

The runner must verify input readiness BEFORE invoking the TrafficFlow
compute path. The classification taxonomy from PR #301 (section 5 of
the readiness doc) is the v1 contract:

- `OK`
- `MISSING`
- `STALE`
- `MISMATCH_MAX_SMA`
- `CONFLICTING_MAX_SMA`
- `UNDETERMINABLE_MAX_SMA`
- `INVALID`
- `UNREADABLE`
- `SCHEMA_MISMATCH`
- `UNKNOWN_USABLE`

Verification steps per secondary, in order:

1. `output/stackbuilder/<SECONDARY>/selected_build.json` exists; load
   and validate schema_version.
2. `selected_run_dir` exists as a directory.
3. `combo_leaderboard.xlsx` (or `.parquet` / `.csv` fallback) exists
   under `selected_run_dir` and contains rows for each requested K.
4. `price_cache/daily/<SECONDARY>.{csv,parquet}` exists; tail date is
   within freshness threshold.
5. For each member ticker in the K rows being benchmarked, verify
   `cache/results/<MEMBER>_precomputed_results.pkl` exists.
6. Verify `max_sma_day == 114` via manifest sidecar (top-level
   `max_sma_day`, legacy schema, or `params.max_sma_day`, new schema)
   AND verify `SMA_114` is present in `preprocessed_data.columns`.

Repair behavior:

- `--refresh-missing-pkls`: invoke
  `signal_engine_cache_refresher.py --ticker <T> --write
  --cache-dir cache/results --status-dir cache/status
  --max-sma-day 114` per missing/stale/MISMATCH/CONFLICTING PKL, one
  ticker at a time. Dry-run before write (matching PR #301 spec).
- `--refresh-stale-prices`: invoke
  `trafficflow.refresh_secondary_caches([sec], force=False)` per
  stale or missing secondary cache.
- Absent the refresh flags: missing/stale inputs are classified and
  the affected cells are skipped, NOT silently network-fetched.

No silent fallback to OnePass `signal_library/data/stable/` PKLs is
permitted.

## 14. Downstream Handoff Contract

[PROPOSED] Future runner output schema; current TrafficFlow does not
write durable artifacts.

v1 runner output, per secondary, under `<output-dir>/<SECONDARY>/`:

- `board_rows.{xlsx,json}` per (sec, K) cell: the `build_board_rows`
  output normalized into deterministic-key form.
- `run_manifest.json`: per-secondary input-readiness summary + the
  `selected_build.json` contents consumed.
- `summary.json`: per-secondary K coverage, row counts, status.
- `_progress/`: per-invocation progress samples (gitignored).

Top-level runner output:

- `output/trafficflow/_run_index.json`: per-run pointer (run id, UTC
  timestamp, secondaries processed, verdict).

Proposed schema fields in the per-secondary `summary.json`:

- `schema_version`
- `secondary`
- `selected_build_consumed` (entire `selected_build.json` payload)
- `input_readiness_summary` (per-input classification)
- `k_levels_materialized`
- `row_counts` per K
- `effective_config` (resolved flag values + env vars)
- `verdict` (ok / partial / failed / refused / dry_run)
- `next_stage_ready` (boolean; true iff all requested cells produced
  rows AND no readiness gate failed)
- `artifacts_written` (repo-relative paths)
- `started_at`, `ended_at`, `elapsed_seconds`

selected_output.json:

- Phase A does NOT lock whether a `selected_output.json` pointer
  (analogous to StackBuilder's `selected_build.json`) belongs at
  the per-secondary level for TrafficFlow. Recommend deferring this
  decision to Phase B / Phase C based on whether downstream MTF /
  Confluence consumption actually needs a pointer file.

Downstream stages (MTF, Confluence):

- Future MTF / Confluence runners should consume the runner's
  documented output contract above, NOT transient Dash UI state.
- The exact MTF / Confluence consumption contract is DEFERRED to a
  later phase. Phase A does not lock that surface.

## 15. Run Reporting and Monitoring Contract

[PROPOSED]

Stdout JSON fields (every invocation):

- `schema_version`
- `stage` (always `"trafficflow_runner"`)
- `run_id` (UTC timestamp + short random suffix)
- `status` (one of: `dry_run`, `ok`, `partial`, `failed`, `timeout`,
  `refused`)
- `started_at`, `ended_at`, `elapsed_seconds`
- `cwd_placeholder` (always rendered as `<PROJECT_ROOT>`)
- `git_head` (short SHA at invocation time)
- `inputs` (`secondaries`, `secondaries_file`, `stackbuilder_root`,
  `output_dir`)
- `effective_config` (every resolved flag, every consumed env var)
- `process_conflict` (boolean + matched cmdlines if any)
- `input_readiness_summary` (per-secondary classification)
- `per_secondary_results` (verdict + row counts + artifact paths
  per secondary)
- `selected_build_consumed` (per-secondary `selected_build.json`
  contents)
- `artifacts_written` (repo-relative paths)
- `progress_path` (repo-relative path to progress sample dir)
- `row_counts` (per-K aggregation)
- `k_level_counts` (per-K cell count)
- `warnings` (list)
- `errors` (list)
- `next_stage_ready` (boolean aggregate across secondaries)
- `verdict` (final overall classification)

Status enum: `dry_run` / `ok` / `partial` / `failed` / `timeout` /
`refused`.

Monitoring session directory:

```
logs/trafficflow_run/<UTC_TIMESTAMP>/
```

Expected files:

- `launch_command.txt` (sanitized command line; no private paths)
- `run.stdout.json`
- `run.stderr.log`
- `pre_run_snapshot.json` (canonical-root SHA-256s + counts)
- `post_run_snapshot.json` (same)
- `monitoring_samples.json`
- `report.md`

Monitoring cadence:

- Sample 0: startup (T+0).
- Sample 1: T+5 minutes.
- Sample 2: T+15 minutes.
- Sample 3: T+30 minutes.
- Sample 4+: every 30 minutes thereafter until the run ends.

Each sample records:

- `timestamp_utc`
- `elapsed_seconds`
- `pid`
- `rss_bytes` / `working_set_bytes`
- `cpu_seconds`
- `effective_cores`
- `stdout_size_bytes`
- `stderr_size_bytes`
- `stderr_tail` (last ~10 lines, sanitized)
- `partial_or_temp_artifact_count`
- `secondary_completion_count`
- `current_secondary` (if inferable from progress dir)

Budget:

- Operator-supplied via `--duration-budget-minutes`.
- For v1 single-secondary smoke, **30 minutes is generous** given the
  PR #301 K=6 benchmark (~3-4 s per cell, 1-row-per-K leaderboards on
  this universe).
- For larger universes, the operator must set the budget explicitly.
- When the budget is exceeded, the runner aborts the next secondary
  gracefully (does not kill in-flight per-subset work mid-compute) and
  emits `status="timeout"`.

## 16. Decisions Locked for v1

[LOCKED v1]

- v1 runner is a wrapper around current TrafficFlow compute behavior.
- v1 does not reimplement TrafficFlow engine logic.
- v1 uses `--jobs 1`.
- v1 keeps `PARALLEL_SUBSETS=0` by default.
- v1 consumes StackBuilder `selected_build.json` per secondary.
- v1 has no implicit directory-listing fallback.
- Operator override via `--explicit-build` is supported and visible in
  stdout JSON.
- v1 PKL refresh uses `signal_engine_cache_refresher.py` with
  `--max-sma-day 114` explicitly passed.
- v1 secondary price refresh uses TrafficFlow's bounded
  `refresh_secondary_caches` function.
- TrafficFlow runner stays separate from a future MultiTimeframe
  runner.
- Orchestration can sequence StackBuilder -> TrafficFlow -> MTF ->
  Confluence later (Phase F).
- RAM-vs-speed tradeoffs are deferred.
- `_subset_metrics_spymaster_bitmask` is the known bottleneck (per
  PR #301), but this doc does not choose the optimization strategy.
- No preemptive cache-sizing defaults in Phase A.
- No "optimized" or "fast" label without benchmark numbers.
- The exact downstream MTF / Confluence contract is DEFERRED.
- No network fetch unless explicitly authorized via
  `--allow-network-fetch` AND `--write`.

## 17. Phase Map: A through F

| Phase | Description | Status |
|---|---|---|
| A | This scoping doc; read-only static inspection + docs-only PR. | **In progress; this PR is the Phase A deliverable.** |
| B | Runner scaffold + tests; dry-run only. Implements every `[LOCKED v1]` decision above. No `--write` execution. | Pending. |
| C | Supervised smoke on 1-2 secondaries using isolated `--output-dir`. Verifies `selected_build.json` consumption and downstream handoff metadata. Does not write under canonical `output/trafficflow/`. | Pending. |
| D | RAM / performance measurement on representative secondaries. Re-visit `PARALLEL_SUBSETS` ONLY if new evidence justifies it. | Pending. |
| E | Operator-authorized canonical write under `output/trafficflow/`. | Pending. |
| F | Orchestrator integration for the StackBuilder -> TrafficFlow -> MTF -> Confluence chain. | Pending. |

Only Phase A is being done now.

## 18. Risks and Open Questions

[DEFERRED] for Phase B and later:

- Runner-level concurrency across secondaries (Phase D will measure).
- True multiprocessing inside the engine targeting
  `_subset_metrics_spymaster_bitmask` (Phase D / later).
- TrafficFlow `selected_output.json` design, if needed (defer to
  Phase B or Phase C).
- Append-mode / historical-output retention strategy.
- Output artifact schema finalization beyond the v1 proposal in
  section 14.
- Cross-pipeline rebuild triggers when StackBuilder
  `selected_build.json` changes (Phase F orchestration).
- Exact MTF / Confluence downstream contract.
- Public website representation of TrafficFlow K artifacts.
- K-level scaling benchmark at a 500-ticker density (the operator's
  informal ~15-minute K=6 / ~500-ticker memory baseline; PR #301
  measured 8 secondaries with 1-row-per-K leaderboards and does not
  contradict that baseline but does not validate it either).
- Whether TrafficFlow should also consume the richer Phase 6I-79
  per-K `combo_k=N.json` artifacts in addition to or instead of
  `combo_leaderboard.xlsx`. Phase 6I-79 wrote both; current
  TrafficFlow only consumes the leaderboard XLSX.
- Whether to create a dedicated TrafficFlow output contract validator
  (analogous to the StackBuilder run-manifest validator).
- Whether process-pool / numba / vectorization work is worth pursuing
  on `_subset_metrics_spymaster_bitmask` after the v1 runner exists.

[GAP] Phase-A-discovered risks worth flagging now:

- `_MASK_CACHE` (`trafficflow.py:311`) is NOT cleared by
  `_clear_runtime` (`trafficflow.py:324`). For multi-secondary loops
  within one runner invocation, this dict grows unbounded. v1
  mitigation: `--jobs 1` + bounded universe + process exit per
  invocation. Phase D may need to revisit if larger universes are
  attempted in-process.
- The `_find_latest_combo_table` directory-listing fallback at
  `trafficflow.py:619-659` is exactly the "silent latest fallback"
  the runner contract forbids. The runner must not call
  `_find_latest_combo_table` directly; it must resolve the run
  directory via `selected_build.json` and then read the leaderboard
  from that resolved path.

## 19. Acceptance Criteria for Phase B

Phase B is accepted when ALL of the following hold:

- A new file `trafficflow_runner.py` exists at the repo root.
- The module does not import `dash`, `from dash`, or any Dash symbol
  at the top level.
- The module does not import `trafficflow.py` at the top level; the
  TrafficFlow import is lazy and gated behind dry-run / write
  decision logic.
- Dry-run is the default invocation behavior; `--write` is required
  for any artifact write.
- Stdout is structured JSON conforming to the schema in section 15.
- `selected_build.json` is consumed per secondary; missing
  `selected_build.json` -> the affected secondary is REFUSED unless
  `--explicit-build` is provided.
- No implicit latest-by-ctime directory-listing fallback exists in
  the runner code path.
- `effective_config` in stdout JSON contains every resolved flag from
  section 10 and every consumed env var from section 6.5.
- Missing / stale inputs are classified per the section 13 readiness
  contract BEFORE the compute path is invoked.
- `--refresh-missing-pkls` invokes `signal_engine_cache_refresher.py`
  with `--max-sma-day 114` explicitly passed in every call.
- `--refresh-stale-prices` invokes
  `trafficflow.refresh_secondary_caches([sec], force=False)` per
  affected secondary.
- Isolated `--output-dir <DIR>` is supported for smoke testing.
- Canonical `--output-dir output/trafficflow` is supported but
  write-gated.
- No network fetch fires unless `--allow-network-fetch` AND `--write`
  are both passed.
- Tests cover: dry-run output shape; input gating per the section 13
  taxonomy; `selected_build.json` consumption (including the explicit
  refusal on missing); `effective_config` completeness; the
  no-implicit-fallback property.
- No generated artifacts are staged in the Phase B PR.

## 20. Appendix: Evidence Table

| Topic | File | Line(s) | Finding |
|---|---|---|---|
| Dash launch guarded by `__main__` | `trafficflow.py` | 3419 | `if __name__ == "__main__" and not __TF_ALREADY_STARTED:` then `main()`. [CITED] PR #300/#301. |
| `main()` calls `make_app()` then `app.run_server` | `trafficflow.py` | 3386, 3392, 3406 | Confirms Dash is only launched inside `main()`. [CITED] PR #300. |
| `make_app()` defined but not import-time invoked | `trafficflow.py` | 3079 | [CITED] PR #300. |
| `build_board_rows` direct compute entry | `trafficflow.py` | 2956 | Signature `(sec, k, run_fence, missing_map)`. K filter at L2986. [CITED] PR #300. |
| `compute_build_metrics_spymaster_parity` dispatch | `trafficflow.py` | 2690 | K=1 fast path; K>=2 enumerates 2^N-1 subsets. [CITED] PR #300. |
| `_subset_metrics_spymaster_bitmask` bottleneck | `trafficflow.py` | 2557 | 87-88% of K=6 wall time. [CITED] PR #301. |
| `PARALLEL_SUBSETS` default | `trafficflow.py` | 241 | Default `0`. [CITED] PR #300/#301. |
| `PARALLEL_SUBSETS_MIN_K` default | `trafficflow.py` | 243 | Default `4`. [CITED] PR #300. |
| `TRAFFICFLOW_SUBSET_WORKERS` default | `trafficflow.py` | 244 | Default `4`. [CITED] PR #300. |
| `TF_BITMASK_FASTPATH` default | `trafficflow.py` | 258 | Default `1`. [CITED] PR #300. |
| `SPYMASTER_PKL_DIR` resolution | `trafficflow.py` | 86-89 | env `PRJCT9_SPYMASTER_PKL_DIR`, default `<PROJECT_ROOT>/cache/results`. [CITED] PR #301. |
| `PRICE_CACHE_DIR` default | `trafficflow.py` | 90 | `price_cache/daily`. [CITED] PR #301. |
| `RUNS_ROOT` default | `trafficflow.py` | 85 | `output/stackbuilder`. [CITED] PR #301. |
| Required PKL schema fields | `trafficflow.py` | 1442, 1480, 1562, 1776 | `preprocessed_data`, `active_pairs`, `daily_top_buy_pairs`, `daily_top_short_pairs`. [CITED] PR #301. |
| `_find_latest_combo_table` (ctime-latest fallback) | `trafficflow.py` | 619-659 | Picks the latest run dir by `st_ctime`; no `selected_build.json` consumption. [GAP] this task. |
| Zero `selected_build.json` references | `trafficflow.py` | n/a | Static substring scan returned zero hits. [GAP] this task. |
| `refresh_secondary_caches` bounded refresh helper | `trafficflow.py` | 1157 | Writes only to `PRICE_CACHE_DIR`. [CITED] PR #301. |
| `_load_secondary_prices` cache path | `trafficflow.py` | 1117 | In-memory `_PRICE_CACHE` -> disk -> yfinance. [CITED] PR #301. |
| `_choose_price_cache_path` resolves under `PRICE_CACHE_DIR` | `trafficflow.py` | 1065 | [CITED] PR #301. |
| File-write call sites | `trafficflow.py` | 165, 907, 914, 1112, 1114 | All target `price_cache/daily/` or `debug_dumps/`. No durable board output. [GAP] this task. |
| `_write_cache_file` atomic shape | `trafficflow.py` | 1109-1115 | `.tmp` then `tmp.replace(p)`. [GAP] this task. |
| Zero argparse/CLI surface | `trafficflow.py` | n/a | Static scan for `argparse`, `ArgumentParser`, `add_argument`, `sys.argv` returned zero hits. [GAP] this task. |
| `_clear_runtime` cache reset helper | `trafficflow.py` | 324 | Clears `_PRICE_CACHE` (optional), `_PKL_CACHE`, `_FROZEN_CAP_END`, `_SEC_POSMAP_CACHE`, `_POSSET_CACHE`. Does NOT clear `_MASK_CACHE`. [GAP] this task. |
| `_MASK_CACHE` not cleared by `_clear_runtime` | `trafficflow.py` | 311, 324-338 | Phase-A-discovered risk for long-running multi-secondary loops. [GAP] this task. |
| Module-level caches inventory | `trafficflow.py` | 273, 288, 289, 302, 307, 309, 311, 316 | `_FROZEN_CAP_END`, `_PKL_CACHE`, `_PRICE_CACHE`, `_SIGNAL_SERIES_CACHE`, `_SEC_POSMAP_CACHE`, `_POSSET_CACHE`, `_MASK_CACHE`, `_PKL_FRESH_MEMO`. [GAP] this task. |
| Dash-UI globals | `trafficflow.py` | 303, 304, 3417 | `_LAST_REFRESH_N`, `_FORCE_PRICE_REFRESH`, `__TF_ALREADY_STARTED`. Runner must not touch. [GAP] this task. |
| `signal_engine_cache_refresher.py` CLI surface | `signal_engine_cache_refresher.py` | 1104-1141 | `--ticker`, `--dry-run`, `--write`, `--cache-dir`, `--status-dir`, `--max-sma-day` (default None -> fallback 30), `--current-as-of-date`. [CITED] PR #301. |
| `DEFAULT_MAX_SMA_DAY` fallback | `signal_engine_cache_refresher.py` | 658 | `30`. Runner MUST pass `--max-sma-day 114` explicitly. [CITED] PR #301. |
| StackBuilder runner CLI pattern reference | `stackbuilder_workbook_runner.py` | 118-226 | argparse pattern: `--secondaries`, `--write`, `--allow-network-fetch`, `--no-progress`, `--progress-dir`, `--duration-budget-minutes`, etc. [GAP] read-only inspection in this task. |
| Phase 6I-79 selected_build.json schema | `output/stackbuilder/<SEC>/selected_build.json` | n/a | Includes `selected_run_dir`, `selected_k`, `selection_policy`, `operator_pinned`, etc. [CITED] PR #301 readiness evidence + Phase 6I-79 production-run evidence. |

## Notes on this scoping task

- This is the Phase A deliverable.
- No runner implementation was performed.
- No code or tests were modified.
- `trafficflow.py` was NOT modified.
- `signal_engine_cache_refresher.py` was NOT modified.
- No engine, app, runner, production pipeline, benchmark, or test
  suite was run.
- No Dash server was launched.
- `trafficflow.py` was NOT imported as a Python module by this task.
  All findings are from static source inspection plus the cited
  merged evidence docs.
- The TrafficFlow LEGACY-vs-current structural diff, the K1/K2/K3
  benchmark evidence, the readiness + K1/K2/K3/K4/K6 benchmark
  evidence, and the Phase 6I-79 production StackBuilder evidence are
  cited verbatim as source-of-truth.
- The Phase-A-discovered findings flagged `[GAP]` in this doc are
  consistent with the cited merged evidence; no contradiction with
  prior evidence was found.
