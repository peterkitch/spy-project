# Phase 6E-2 — Source Freshness Preflight + Controlled Refresh Plan

**Status:** preflight / safety tooling. Does **not** refresh
source caches. Does **not** start automation. Documents the
existing refresh code paths and defines the read-only
preflight an operator should run before any
production-affecting refresh.

**Last updated:** 2026-05-11.

## 1. Why this phase

After Phase 6E-1, the launch-readiness audit reports that
SPY / AAPL / SNOW are all blocked by
`needs_fresh_source_cache` (source PKL last-date
`2026-05-04` < cutoff `2026-05-08`). Under the Phase 6C-8
contract, the public Daily Signal Board cannot award a
podium spot until those tickers carry a current Confluence
verdict, and the Phase 6D-4 runner cannot produce a
current Confluence verdict from a stale source cache.

Before any production write — even a single-ticker pilot —
the team needs a clear, audited answer to:

> **How do we safely refresh source cache for a tiny
> pilot list, then run the completed Phase 6D pipeline
> without creating misleading current leaders?**

Phase 6E-2 is that audit + the read-only operator tool
that surfaces its result per ticker.

## 2. Existing source-refresh code paths

This section is the result of a `Grep` audit of the
working tree at `d7369c8` (post Phase 6E-1 merge). The
relevant findings:

### 2.1 What writes Signal Engine / Spymaster cache PKLs

The Spymaster `<TICKER>_precomputed_results.pkl` is the
authoritative on-disk Signal Engine cache. The single
writer in the repo is:

  - `project/spymaster.py:4607` — `save_precomputed_results(ticker, results)`.
    - Per-ticker file lock.
    - Atomic temp-file write + `os.replace` into
      `project/cache/results/<TICKER>_precomputed_results.pkl`.
    - Attaches a provenance manifest at write time.
    - Guards against `(0, 0)` SMA-pair contamination,
      mismatched `_ticker` field, and "light copy"
      incomplete results.

It is only called from one site inside Spymaster's main
processing loop (`spymaster.py:5247`), at the end of a
full SMA optimization for a single ticker.

`onepass.py` writes **signal library** PKLs
(`signal_library/data/stable/<TICKER>_stable_v1_0_0.pkl`)
via three `pickle.dump` sites (lines 698, 1148, 1314), but
none of them produce the Spymaster Signal Engine cache.
Likewise `signal_library/multi_timeframe_builder.py`
writes the multi-timeframe (`_1wk.pkl`, `_1mo.pkl`, etc.)
libraries, not the precomputed-results PKL.

### 2.2 Does it use yfinance?

Yes. Spymaster's data fetch path is:

  - `project/spymaster.py:3577, 3626, 3832, 3836, 3903, 4088` —
    direct `yf.download(...)` calls inside the same
    Spymaster module that owns the cache writer. The fetch
    is consumed in-process by the SMA optimization that
    eventually writes the PKL.

yfinance is also imported by:

  - `project/spymaster.py`, `project/stackbuilder.py`,
    `project/impactsearch.py`, `project/trafficflow.py`,
    `project/onepass.py`,
    `project/signal_library/multi_timeframe_builder.py`,
    `project/stale_check.py`,
    `project/global_ticker_library/validator_yahoo.py`,
    `project/global_ticker_library/tools/diagnose_rate_limits.py`.

Per Phase 5G, yfinance remains the sprint data source
until the data-licensing pre-launch gate closes. There is
no alternative provider wired in.

### 2.3 Is there an existing CLI refresh script?

**No.** A repo-wide search produced no `refresh*.py`
module and no `save_precomputed_results` caller outside
`spymaster.py:5247`. The only path that produces a fresh
PKL today is:

  1. `python spymaster.py` — starts the Dash app on
     port 8050.
  2. Open the browser, enter the ticker in one of the
     seven ticker-input locations.
  3. Spymaster downloads via `yf.download`, runs the SMA
     optimization, and writes the PKL at the end.

`project/controlled_compute.py` is the Phase 5D-1
orchestrator. It can subprocess job specs but Spymaster
does not expose a non-interactive entry point, so
controlled_compute cannot be pointed at Spymaster today
to refresh a single ticker.

`project/stale_check.py` is a yfinance-querying staleness
diagnostic — it reports which Yahoo symbols have a
stale last close. It does **not** write a cache PKL.

### 2.4 What files would be written in a real refresh?

A real Spymaster cache refresh for ticker `T` produces:

  - `project/cache/results/<T>_precomputed_results.pkl` —
    Spymaster precomputed results plus embedded
    provenance manifest.
  - `project/cache/results/<T>_precomputed_results.pkl.manifest.json` —
    sidecar provenance manifest produced after the
    pickle write.
  - `project/cache/status/<T>_status.json` — status
    tracking (`status: complete`, `progress: 100`,
    `cache_status: fresh`).

If the operator opens the launch-readiness audit (Phase
6E-1) or the Phase 6D-4 runner after that, the audit's
staleness check passes for `T` and the downstream
pipeline runner can produce a current Confluence
artifact.

## 3. Minimum safe pilot ticker flow

Until a controlled, non-interactive refresh tool exists,
the **minimum safe pilot flow** is:

  1. Run the Phase 6E-2 preflight against an explicit
     short ticker list (e.g. `SPY,AAPL,SNOW`). Confirm
     each ticker's `recommended_next_action`,
     `cache_date_range_end`, and `safe_to_attempt_refresh`
     flag.
  2. **Halt** if any ticker in the pilot list is
     `blocked_by_health_report` or `missing_stackbuilder_run`.
     Refreshing those tickers does not produce a board
     leader; the operator should fix the upstream block
     first.
  3. For the chosen single-ticker pilot, hand-launch
     Spymaster (`python spymaster.py`) and submit only
     that ticker through the UI. Verify the new
     `_precomputed_results.pkl` last-date is current.
  4. Re-run the launch-readiness audit (Phase 6E-1) and
     verify the ticker now reports
     `recommended_action=ready_for_pipeline_write` and
     `can_run_pipeline_now=True`.
  5. Run `python confluence_pipeline_runner.py --ticker
     <T> --write` (Phase 6D-4) for that single ticker.
  6. Re-run the launch audit and confirm
     `current_leader_eligible=True` for that ticker.

This flow is intentionally manual. It does not introduce
a new writer, does not call yfinance from this module,
and keeps every production write gated by an explicit
operator action.

## 4. What must NOT happen from the web tier

The public Daily Signal Board MVP is read-only against
saved artifacts (Phase 6C-3). Even after Phase 6E-2 the
web tier is still strictly read-only:

  - The web tier MUST NOT fetch yfinance data.
  - The web tier MUST NOT trigger Spymaster cache writes.
  - The web tier MUST NOT trigger the Phase 6D-4 runner
    in `write=True` mode.
  - The web tier MUST NOT call any code path that opens a
    cache PKL for writing.

The Phase 6E-1 launch audit and the Phase 6E-2 preflight
are operator tools, not user-facing surfaces.

## 5. Exact command an operator should run for SPY only

When (and only when) the operator has been explicitly
authorized to refresh SPY's source cache:

  - **Step A (manual, today)**:
    `python spymaster.py` from the project working
    directory, then in the browser enter only `SPY` into
    the Spymaster ticker input. Wait for the run to
    complete (status → `complete`, `cache_status: fresh`).
    Confirm the SPY PKL last-date is current.
  - **Step B**:
    `python source_freshness_preflight.py --ticker SPY`
    — confirm `recommended_next_action=run_pipeline_after_refresh`
    and `safe_to_run_pipeline_after_refresh=True`.
  - **Step C**:
    `python confluence_pipeline_runner.py --ticker SPY --write`
    — produces the per-K and Confluence artifacts.
  - **Step D**:
    `python board_launch_readiness_audit.py --tickers SPY`
    — verify SPY is now `already_leader_eligible`.

**Do not skip Step B.** The preflight is the explicit
confirmation that the source refresh actually moved the
ticker into a runnable state.

## 6. Risks remaining before a production write

The pilot flow above is safe enough for a single
explicitly-named ticker, but the following risks still
need to be acknowledged before any production write:

  - **yfinance variance**: Yahoo Finance Adj/Close
    revisions are common. The Phase 5C honest-validation
    framework explicitly does not pin to a frozen vendor
    snapshot. A refresh today may shift earlier history
    by small amounts; this is in scope for Phase 5G data
    licensing.
  - **No non-interactive refresh tool**: Spymaster's
    Dash app is the only writer (partially closed by
    Phase 6E-3 — see § 6.5. The Phase 6E-3 CLI handles the
    data-fetch + cache-write portion non-interactively
    but does NOT run Spymaster's SMA-pair optimization, so
    the resulting cache carries placeholder `active_pairs`.
    A future sub-phase still needs to extract the SMA
    optimizer for a full non-interactive pilot).
  - **`controlled_compute` cannot orchestrate Spymaster
    today**: it can run job specs, but Spymaster lacks
    the CLI surface it would call. Wiring this is a
    separate phase.
  - **Multi-timeframe libraries** are out of scope here.
    A ticker that has fresh source + StackBuilder but no
    multi-timeframe library stack will surface as
    `manual_review` in the preflight; building those
    libraries is a separate (existing) workflow.
  - **Health report**: catalogue health blocks must be
    resolved upstream; refreshing source for a
    health-blocked ticker does nothing for the public
    board.

## 6.5 Phase 6E-3 — Non-interactive Signal Engine cache
       refresh CLI

Phase 6E-3 adds the missing non-interactive operator CLI
called out in § 6 as the "no non-interactive refresh tool"
risk. The new module is
`project/signal_engine_cache_refresher.py`; the test pin is
`project/test_scripts/test_signal_engine_cache_refresher.py`.

### 6.5.1 Scope (intentionally minimum-viable)

The Phase 6E-3 refresher is intentionally scoped to what
can ship in one PR without refactoring the Spymaster Dash
app. It:

  - Fetches fresh OHLC price data from a pluggable data
    source (default: yfinance via a lazy import inside the
    default fetcher; tests inject their own callable so the
    network is never touched).
  - Builds a `preprocessed_data` DataFrame with `Close`
    plus `SMA_1` … `SMA_<max_sma_day>` columns so the cache
    shape matches what the Signal Engine loader and
    downstream engines expect.
  - Populates `active_pairs` as a placeholder list of
    `"None"` strings, one per row, so the cache is
    **loadable** by
    `primary_signal_engine.load_primary_signal_engine_payload`
    but honestly reports `signal: None`.
  - Writes the cache atomically (temp file +
    `os.replace`) and emits the provenance manifest sidecar
    using the central `provenance_manifest` helpers — the
    same Phase 3 contract Spymaster uses for its own
    cache writes.
  - Writes the corresponding status JSON only when
    `write=True`.

### 6.5.2 Explicit non-goals

What Phase 6E-3 deliberately does **not** do:

  - **Run Spymaster's daily best-buy / best-short SMA-pair
    optimization.** That logic is a closure inside a Dash
    callback at `project/spymaster.py:5050-5117`. Reusing it
    from a CLI would require either importing the entire
    14k-line Spymaster module (which would import `dash` /
    `plotly` / instantiate the Dash app object at
    `spymaster.py:2811` as a module-level side effect) or
    refactoring Spymaster to expose the math as a
    standalone function. Neither fits the Phase 6E-3 PR
    scope. The refresher therefore writes placeholder
    `active_pairs` and leaves signal computation to the
    Spymaster Dash app (current operator practice) or to a
    future sub-phase that extracts the SMA optimizer.
  - **Reuse `spymaster.save_precomputed_results`
    directly.** Same reason: importing `spymaster` pulls in
    `dash` / `plotly` and instantiates the Dash app. The
    refresher reproduces the writer's atomic-write
    semantics (`tempfile.NamedTemporaryFile` →
    `pickle.dump` → `flush` → `os.fsync` → `os.replace`)
    using stdlib only, plus the `provenance_manifest`
    helpers for the sidecar.
  - **Multi-ticker mode.** The CLI has `--ticker`
    (singular) only. A multi-ticker mode would require a
    dedicated scheduler / rate-limit story and is out of
    scope.
  - **Web-tier callability.** The refresher must never be
    imported from `daily_signal_board.py` or any other
    web-tier module. The test
    `test_daily_signal_board_is_not_imported_by_refresher`
    pins the absence of the relevant symbol in the
    refresher's source.

### 6.5.3 Public API

```python
refresh_signal_engine_cache(
    ticker: str,
    *,
    cache_dir: Path | str | None = None,
    status_dir: Path | str | None = None,
    write: bool = False,
    max_sma_day: int | None = None,           # default 30
    data_fetcher: Callable | None = None,     # default yfinance
    current_as_of_date: str | None = None,    # default today UTC
) -> SignalEngineRefreshResult
```

`SignalEngineRefreshResult` fields (also exposed via
`to_json_dict()` for the CLI):

  - `ticker`, `write`
  - `cache_path`, `manifest_path`, `status_path`
  - `old_cache_date_range_end`,
    `new_cache_date_range_end`
  - `refreshed` (True only if `write=True` and the writer
    landed)
  - `stale_before`, `current_after`
  - `issue_codes` — stable string set:
    `invalid_ticker`, `data_fetch_failed`,
    `data_no_close_column`, `data_empty`, `dry_run_only`,
    `already_current`, `provenance_manifest_failed`
  - `elapsed_seconds`

### 6.5.4 CLI contract

  - `python signal_engine_cache_refresher.py --ticker SPY --dry-run`
  - `python signal_engine_cache_refresher.py --ticker SPY --write`
  - `--ticker` is required; no `--tickers` (multi-ticker)
    flag exists.
  - Default is dry-run (`--dry-run` and `--write` are
    mutually exclusive; absent both = dry-run).
  - `--cache-dir` and `--status-dir` accepted for
    operator / test control.
  - JSON to stdout. Exit codes:
      0  refresh completed (dry-run or write)
      2  invalid CLI arguments (parser SystemExit is
         trapped and converted)
      3  unexpected unhandled exception

### 6.5.5 Updated pilot ticker flow (replaces § 3, step 3)

  1. `python source_freshness_preflight.py --ticker SPY` —
     confirm `recommended_next_action=refresh_source_cache`
     and `safe_to_attempt_refresh=True`.
  2. `python signal_engine_cache_refresher.py --ticker SPY
     --dry-run` — confirm the fetched
     `new_cache_date_range_end` advances past the existing
     `old_cache_date_range_end`. **No writes.**
  3. (Authorized only) `python
     signal_engine_cache_refresher.py --ticker SPY --write`
     — produces the fresh cache PKL, manifest sidecar, and
     status JSON.
  4. `python source_freshness_preflight.py --ticker SPY` —
     confirm the recommendation now moves to
     `run_pipeline_after_refresh` (no longer
     `refresh_source_cache`).
  5. (Authorized only) `python confluence_pipeline_runner.py
     --ticker SPY --write` — Phase 6D-4 runner produces the
     per-K and Confluence artifacts.
  6. `python board_launch_readiness_audit.py --tickers SPY`
     — verify SPY is now `already_leader_eligible`.

Step 4 is the explicit confirmation that the non-interactive
refresh actually moved the ticker into a runnable state.
**Do not skip Step 4.**

### 6.5.6 Residual gaps the refresher does NOT close

Even after a `write=True` refresh, the cache produced by
Phase 6E-3 carries:

  - `active_pairs` = placeholder `"None"` strings (no
    daily best buy / best short pair computed). The Signal
    Engine view will show `signal: None`.
  - `top_buy_pair` / `top_short_pair` set to MAX-SMA
    sentinels so Spymaster's writer-guard convention is
    satisfied but the cache carries no leader-pair
    insight.

For a full Spymaster-equivalent cache the operator still
needs to run the Spymaster Dash app, OR a future
Phase 6E-4 (or similar) sub-phase that extracts the
SMA-pair optimizer as a non-interactive helper. The current
phase deliberately leaves that work out so the refresh CLI
can ship and be audited in isolation.

## 7. Out of scope for Phase 6E-2

  - Writing a non-interactive Spymaster refresh CLI.
  - Wiring controlled_compute to drive Spymaster.
  - Touching the web tier.
  - Scheduler / daily automation.
  - Universe-wide sweeps.
  - Production writes to `cache/results/` or
    `output/research_artifacts/`.
  - Data provider / licensing decisions.
  - `.bat` launcher changes.

## 8. Reference paths

  - Spymaster cache writer:
    `project/spymaster.py:4607` (`save_precomputed_results`).
  - Spymaster cache write call site:
    `project/spymaster.py:5247`.
  - Spymaster yfinance fetch sites:
    `project/spymaster.py:3577,3626,3832,3836,3903,4088`.
  - Signal Engine cache reader:
    `project/primary_signal_engine.py:487`
    (`load_primary_signal_engine_payload`).
  - Launch readiness audit:
    `project/board_launch_readiness_audit.py`.
  - Pipeline runner:
    `project/confluence_pipeline_runner.py`.
  - Source freshness preflight (this phase):
    `project/source_freshness_preflight.py`.
  - Preflight tests:
    `project/test_scripts/test_source_freshness_preflight.py`.
  - Phase 6E-3 refresher:
    `project/signal_engine_cache_refresher.py`.
  - Phase 6E-3 refresher tests:
    `project/test_scripts/test_signal_engine_cache_refresher.py`.
