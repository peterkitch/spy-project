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
    Dash app is the only writer. A future Phase 6E-3 (or
    similar) sub-phase needs to extract a non-interactive
    `refresh_signal_engine_cache(ticker)` helper and a
    CLI that calls it, so the pilot flow can be audited
    end-to-end without browser interaction.
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
