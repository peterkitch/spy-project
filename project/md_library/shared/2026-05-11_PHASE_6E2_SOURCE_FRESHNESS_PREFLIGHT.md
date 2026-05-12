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
  - **Non-interactive refresh tool**: Phase 6E-3 (see
    § 6.5) shipped the probe + write guard; Phase 6E-4
    (§ 6.6) extracted the SMA optimizer; Phase 6E-5
    (§ 6.7) wires them together so the refresher's
    `--write` path now produces a real `optimizer_v1`
    cache. The defensive `data_only_v1` guard remains in
    place for any payload that bypasses the optimizer
    path. The Spymaster Dash app is no longer the only
    writer that can produce a current Spymaster-shaped
    Signal Engine cache.
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

## 6.5 Phase 6E-3 — Source-data refresh probe +
       cache-shape builder

**Phase 6E-3 is NOT a production-safe Signal Engine cache
refresher.** It is the first half of the eventual operator
path: the source-data fetch + cache-shape build, paired
with a hard guard that refuses to write the resulting
payload over a real Spymaster cache. The Codex audit on PR
#202 flagged that an unguarded `--write` would have
replaced `current_signal=Buy/Short` caches with
`current_signal=None` caches — a strict regression for the
Daily Signal Board and the Primary Signal Engine front
door. The guard is the contract.

The module is `project/signal_engine_cache_refresher.py`;
the test pin is
`project/test_scripts/test_signal_engine_cache_refresher.py`.

### 6.5.1 What this phase actually does

  - Fetches fresh OHLC price data from a pluggable data
    source (default: yfinance via a lazy import inside the
    default fetcher; tests inject their own callable so the
    network is never touched).
  - Builds a `preprocessed_data` DataFrame with `Close`
    plus `SMA_1` … `SMA_<max_sma_day>` columns so the cache
    shape matches what the Signal Engine loader and
    downstream engines expect.
  - Builds the rest of the Spymaster cache payload
    structure (self-check tokens, MAX-SMA sentinel
    `top_buy_pair`/`top_short_pair`, `last_close`, …).
  - Populates `active_pairs` as a placeholder list of
    `"None"` strings, one per row, because the Spymaster
    SMA pair optimizer has not been extracted into a
    non-interactive helper yet.
  - Stamps the payload with the scope marker
    `signal_engine_cache_refresher_scope = "data_only_v1"`.
  - Reports `old_cache_date_range_end`,
    `new_cache_date_range_end`,
    `stale_before`, and `current_after` — the same
    arithmetic the Phase 6E-2 preflight uses.

### 6.5.2 The data-only write guard (Codex amendment)

Before any disk write, the refresher checks the payload's
`signal_engine_cache_refresher_scope` marker. While that
marker is `data_only_v1` (i.e. until the SMA optimizer is
extracted in a future sub-phase), every `write=True` call
is refused:

  - `refreshed = False`
  - `issue_codes` contains
    `"data_only_write_blocked"`
  - No cache PKL is written.
  - No status JSON is written.
  - No provenance manifest sidecar is written.
  - An existing valid Spymaster cache for the same ticker
    is preserved byte-for-byte; nothing on disk changes.
  - The result still reports `old_cache_date_range_end`,
    `new_cache_date_range_end`, `stale_before`, and
    `current_after` so the operator can see what a future
    write would have advanced to.

The CLI keeps `--write` so the contract is exercised end
to end, but it is functionally a no-op until the next
sub-phase ships. The atomic-write helper, manifest
plumbing, and status-write helper are preserved in the
module behind the guard for that future work.

### 6.5.3 Cutoff resolver

`current_as_of_date` is resolved through
`confluence_pipeline_readiness.resolve_current_as_of_date`,
the same helper the Phase 6 readiness / preflight tools
use. Absent an explicit override, the cutoff is the most
recent weekday strictly before UTC now (Monday
`2026-05-11` resolves to Friday `2026-05-08`). This keeps
the refresher's `stale_before` / `current_after` flags
consistent with everything else in the launch-readiness
stack.

### 6.5.4 Explicit non-goals

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
    scope. **This extraction is the next required
    sub-phase before production cache writes are allowed.**
  - **Produce a production-safe refreshed Signal Engine
    cache.** The data-only guard refuses every `--write`
    while the SMA optimizer is unavailable, so the on-disk
    state remains controlled by the Spymaster Dash app
    (current operator practice).
  - **Reuse `spymaster.save_precomputed_results`
    directly.** Same reason: importing `spymaster` pulls in
    `dash` / `plotly` and instantiates the Dash app. The
    refresher reproduces the writer's atomic-write
    semantics (`tempfile.NamedTemporaryFile` →
    `pickle.dump` → `flush` → `os.fsync` → `os.replace`)
    using stdlib only, plus the `provenance_manifest`
    helpers for the sidecar — but those helpers are
    behind the data-only guard and unreachable while the
    payload scope is `data_only_v1`.
  - **Multi-ticker mode.** The CLI has `--ticker`
    (singular) only.
  - **Web-tier callability.** The refresher must never be
    imported from `daily_signal_board.py` or any other
    web-tier module.

### 6.5.5 Public API

```python
refresh_signal_engine_cache(
    ticker: str,
    *,
    cache_dir: Path | str | None = None,
    status_dir: Path | str | None = None,
    write: bool = False,
    max_sma_day: int | None = None,           # default 30
    data_fetcher: Callable | None = None,     # default yfinance
    current_as_of_date: str | None = None,    # default via
                                              # resolve_current_as_of_date
) -> SignalEngineRefreshResult
```

`SignalEngineRefreshResult` fields (also exposed via
`to_json_dict()` for the CLI):

  - `ticker`, `write`
  - `cache_path`, `manifest_path`, `status_path`
  - `old_cache_date_range_end`,
    `new_cache_date_range_end`
  - `refreshed` — True only if `write=True` AND the
    data-only guard is no longer in force AND the writer
    landed. While the SMA optimizer is unavailable, this
    is always `False`.
  - `stale_before`, `current_after`
  - `issue_codes` — stable string set:
    `invalid_ticker`, `data_fetch_failed`,
    `data_no_close_column`, `data_empty`, `dry_run_only`,
    `already_current`, `provenance_manifest_failed`,
    `data_only_write_blocked`.
  - `elapsed_seconds`

### 6.5.6 CLI contract

  - `python signal_engine_cache_refresher.py --ticker SPY --dry-run`
  - `python signal_engine_cache_refresher.py --ticker SPY --write`
    (refused under the data-only guard; see § 6.5.2)
  - `--ticker` is required; no `--tickers` (multi-ticker)
    flag exists.
  - Default is dry-run (`--dry-run` and `--write` are
    mutually exclusive; absent both = dry-run).
  - `--cache-dir` and `--status-dir` accepted for
    operator / test control.
  - JSON to stdout. Exit codes:
      0  refresh completed (dry-run or guarded write)
      2  invalid CLI arguments (parser SystemExit is
         trapped and converted)
      3  unexpected unhandled exception

### 6.5.7 Updated pilot ticker flow (replaces § 3, step 3)

  1. `python source_freshness_preflight.py --ticker SPY` —
     confirm `recommended_next_action=refresh_source_cache`
     and `safe_to_attempt_refresh=True`.
  2. `python signal_engine_cache_refresher.py --ticker SPY
     --dry-run` — confirm the fetched
     `new_cache_date_range_end` advances past the existing
     `old_cache_date_range_end`. **This is the only Phase
     6E-3 invocation an operator should run today.** No
     writes are produced; `--write` is refused under the
     data-only guard.
  3. **Wait for the next sub-phase: SMA-optimizer
     extraction.** Spymaster's SMA pair optimization
     (`spymaster.py:5050-5117`) must be extracted into a
     non-interactive helper before a production cache
     write is allowed. Until that ships, the only safe
     path to refresh a Signal Engine cache remains the
     Spymaster Dash app (the original Phase 6E-2 § 3
     manual flow).
  4. Once that sub-phase ships, the operator flow becomes:
     `--write` produces the cache + manifest + status,
     then `source_freshness_preflight --ticker SPY` flips
     to `run_pipeline_after_refresh`, then
     `confluence_pipeline_runner --ticker SPY --write`,
     then `board_launch_readiness_audit --tickers SPY`
     confirms `already_leader_eligible`.

### 6.5.8 Next required phase: SMA-optimizer extraction (historical)

*This section captures the Phase 6E-3 forward look. The
follow-up phases have since landed — see § 6.6 (Phase 6E-4,
the SMA optimizer extraction) and § 6.7 (Phase 6E-5, the
wiring PR that makes `--write` produce real
`optimizer_v1` caches). The data-only guard is preserved
inside the new write helper as a defensive check; the
refresher's happy path no longer hits it.*

The original Phase 6E-3 callout: *"The Phase 6E-3
data-only guard is a hold, not a fix. The next required
sub-phase extracts Spymaster's daily best-buy / best-short
SMA-pair optimizer (`spymaster.py:5050-5117`) into a
non-interactive helper that the refresher can call. At
that point the payload scope marker flips off
`data_only_v1`, the guard releases, and `--write`
becomes the operator's production refresh path. The
atomic-write helper, manifest sidecar, status JSON, and
CLI surface in Phase 6E-3 are already in place for that
work — the SMA extraction is the only blocking piece."*

## 6.6 Phase 6E-4 — SMA optimizer extraction (isolated)

Phase 6E-4 lifts the Spymaster daily best-buy /
best-short SMA-pair optimizer out of the Dash callback and
into a pure, offline, importable helper at
`project/signal_engine_sma_optimizer.py`. The test pin is
`project/test_scripts/test_signal_engine_sma_optimizer.py`.
The follow-up wiring sub-phase is § 6.7 (Phase 6E-5).

**This PR does NOT release the Phase 6E-3 data_only_v1
write guard.** It only extracts and validates the
optimizer. Wiring the optimizer into
`signal_engine_cache_refresher.py` (and thereby releasing
the `--write` guard) is left to a follow-up PR so the
extraction can be audited in isolation.

### 6.6.1 Public surface

```python
optimize_signal_engine_sma_pairs(
    preprocessed_data: pd.DataFrame,
    *,
    ticker: Optional[str] = None,
    max_sma_day: int = 30,
) -> SignalEngineSmaOptimizationResult
```

`SignalEngineSmaOptimizationResult` carries every field
the refresher-wiring sub-phase (§ 6.7, Phase 6E-5) uses to
build a production-safe Signal Engine cache payload:
`preprocessed_data`,
`daily_top_buy_pairs`, `daily_top_short_pairs`,
`cumulative_combined_captures`, `active_pairs`,
`top_buy_pair` / `top_short_pair` /
`top_buy_capture` / `top_short_capture`,
`last_processed_date`, `existing_max_sma_day`,
`issue_codes`.

Stable issue codes:
`invalid_preprocessed_data`,
`insufficient_history`,
`invalid_max_sma_day`.

### 6.6.2 Spymaster behaviors preserved

The optimizer is a port — not a rewrite — of the math
that has been Spymaster's regression baseline since the
Phase 1 baseline lock. Each preserved behavior cites the
exact Spymaster source line(s) so a future audit can
follow the trail:

  - **SMA construction**:
    `Close.rolling(window=j, min_periods=j, center=False).mean()`
    (`spymaster.py:4929`).
  - **Returns vector**:
    `Close.pct_change(fill_method=None)` with `±inf -> NaN
    -> 0` (`spymaster.py:4972-4976`).
  - **Pair enumeration order**: every ordered `(i, j)` with
    `1 <= i, j <= max_sma_day` and `i != j`, in the
    exact `pc_global` walk Spymaster uses
    (`spymaster.py:5036-5042`).
  - **Right-most tie-break on equal cumulative capture**
    (`spymaster.py:5076-5092`).
  - **`(0, 0)` -> MAX-SMA sentinel back-fill**
    (`spymaster.py:5100-5111`).
  - **`_align_pairs_to_calendar` semantics**
    (`spymaster.py:7576`).
  - **`calculate_cumulative_combined_capture` per-day
    rule** (`spymaster.py:7649-7710`).

### 6.6.3 Parity result (SPY)

Refitting the optimizer against the existing saved SPY
cache (`project/cache/results/SPY_precomputed_results.pkl`)
reproduces Spymaster's published output:

  - `top_buy_pair`: `(11, 5)` — exact match.
  - `top_short_pair`: `(11, 5)` — exact match.
  - last `active_pair`: `"Short 11,5"` — exact match.
  - final `cumulative_combined_capture`: `201.1422` —
    matches Spymaster's cached value within
    `1e-9` rel-tol / `1e-6` abs-tol.
  - full `active_pairs` sequence: `8372/8372` positions
    match exactly.

Runtime: about 2.5 seconds for the full SPY cache
(8,372 days × 12,882 pairs at `max_sma_day=114`).

### 6.6.4 Remaining gap (closed in Phase 6E-5)

*At Phase 6E-4 time this section described the still-open
wiring work. Phase 6E-5 (§ 6.7) is that wiring PR; the
gap is now closed. The historical text follows.*

*"The Phase 6E-3 refresher's `--write` path is still
refused under the `data_only_v1` guard. A follow-up PR
will:*

  1. *Import and call `optimize_signal_engine_sma_pairs`
     from inside `refresh_signal_engine_cache`.*
  2. *Replace the placeholder `active_pairs = ["None", ...]`
     with the optimizer's real
     `result.active_pairs`.*
  3. *Flip the payload scope marker off `data_only_v1`.*
  4. *Re-enable the existing (currently dead) atomic-write
     + manifest + status branch.*

*Phase 6E-4 deliberately stops short of that wiring so
the optimizer's parity and contract can be audited
without changing any production-affecting behavior."*

### 6.6.5 Hard rules pinned in Phase 6E-4 tests

  - No `spymaster`, `dash`, `plotly`, `yfinance`,
    `daily_signal_board`, or other web-tier import in
    `signal_engine_sma_optimizer.py`.
  - The optimizer runs without network and without
    writing to `cache/results/` or
    `output/research_artifacts/`.
  - A negative-control test snapshots the production
    cache directory before and after the call and
    asserts byte-identical state.

## 6.7 Phase 6E-5 — wire optimizer into refresher

Phase 6E-5 imports the Phase 6E-4 optimizer from
`signal_engine_cache_refresher.py` and replaces the
placeholder `active_pairs = ["None", ...]` path with the
real optimizer-backed payload. This is the PR that
RELEASES the Phase 6E-3 data_only_v1 write guard for the
happy path while keeping the defensive guard wired for any
payload that does not carry the `optimizer_v1` scope.

### 6.7.1 What changes

  - The refresher imports
    `signal_engine_sma_optimizer.optimize_signal_engine_sma_pairs`
    and calls it after the data fetch.
  - On optimizer success, the cache payload carries the
    new scope marker
    `signal_engine_cache_refresher_scope = "optimizer_v1"`
    plus the real `daily_top_buy_pairs`,
    `daily_top_short_pairs`,
    `cumulative_combined_captures`, `active_pairs`,
    `top_buy_pair`, `top_short_pair`,
    `top_buy_capture`, `top_short_capture`,
    `existing_max_sma_day`, and
    `last_processed_date` from the optimizer result.
  - On optimizer failure (e.g. `insufficient_history`),
    the refresher returns
    `issue_codes=(optimizer_failed, <optimizer codes>)`
    and writes nothing.
  - The write guard now sits in a small helper
    (`_write_optimizer_payload_or_block`) that refuses
    any payload not stamped `OPTIMIZER_V1_SCOPE`.
    `DATA_ONLY_V1_SCOPE` payloads are still explicitly
    blocked with `data_only_write_blocked`; the legacy
    helper `_build_data_only_v1_payload` is retained so
    tests can exercise that branch directly.

### 6.7.2 `max_sma_day` default

If an existing cache for the ticker exposes a usable
`existing_max_sma_day` field (e.g. SPY's `114`), the
refresher reuses that value by default so a `--write`
never silently downgrades a 114-wide cache to the module
default of 30. An explicit `--max-sma-day` argument
overrides both, validated as `>= 2`. Tests pin both
branches.

### 6.7.3 Cutoff resolver

Unchanged — still
`confluence_pipeline_readiness.resolve_current_as_of_date`.
A fresh fetch ending on the resolved cutoff reports
`current_after=True`.

### 6.7.4 Updated pilot ticker flow

Steps 3 and 4 from § 6.5.7 now collapse into a single
authorized `--write` invocation:

  1. `python source_freshness_preflight.py --ticker SPY` —
     confirm `recommended_next_action=refresh_source_cache`.
  2. `python signal_engine_cache_refresher.py --ticker SPY
     --dry-run` — confirm
     `new_cache_date_range_end` advances and the optimizer
     would produce a real verdict.
  3. (Authorized only) `python
     signal_engine_cache_refresher.py --ticker SPY
     --write` — produces a real
     `optimizer_v1` cache PKL + manifest sidecar + status
     JSON.
  4. `python source_freshness_preflight.py --ticker SPY` —
     verify the recommendation now reads
     `run_pipeline_after_refresh`.
  5. (Authorized only) `python confluence_pipeline_runner.py
     --ticker SPY --write` — produces the Phase 6D-4
     artifacts.
  6. `python board_launch_readiness_audit.py --tickers SPY`
     — verify `already_leader_eligible`.

### 6.7.5 Temp-dir SPY parity smoke (Phase 6E-5)

Re-feeding the existing saved SPY cache's
`preprocessed_data` through the refresher with
`write=True` against a temp `cache_dir` / `status_dir`
(and `max_sma_day=114` to match the cached width)
produces:

  - `refreshed=True`, `issue_codes=()`.
  - cache PKL + manifest sidecar + status JSON all
    written under the supplied temp dirs only.
  - Loaded payload via
    `primary_signal_engine.load_primary_signal_engine_payload`:
    `available=True`, `current_signal="Short"`,
    `current_active_pair_raw="Short 11,5"`,
    `current_sma_pair=[11, 5]`, `signal_days=8256`,
    `total_capture_pct=201.14223933187364`.
  - Parity vs the cached SPY:
    `top_buy_pair=(11, 5)` (exact),
    `top_short_pair=(11, 5)` (exact),
    last `active_pair="Short 11,5"` (exact),
    `existing_max_sma_day=114` (preserved).
  - Runtime: ~2.9 s.

### 6.7.6 Real-cache SPY dry-run (Phase 6E-5)

`python signal_engine_cache_refresher.py --ticker SPY
--dry-run --max-sma-day 5` against the real SPY cache and
the real yfinance feed returns:

  - `write=false`, `refreshed=false`,
    `issue_codes=["dry_run_only"]`.
  - `old_cache_date_range_end="2026-05-04"`,
    `new_cache_date_range_end="2026-05-11"`.
  - `stale_before=true`, `current_after=true`.
  - Zero changes to `cache/results/` or `cache/status/`
    (verified by `git status` after the smoke).

## 6.8 Phase 6G-5 — persist-skip-lag honest recommendation

Phase 6G-5 closes the operator-honesty gap exposed when SPY's
saved pipeline tree advanced to `last_date=2026-05-08` (the
Phase 6F-5 production write) while the readiness resolver
later rolled forward to `current_as_of_date=2026-05-11`. The
saved tree did not regress; the Phase 6D-1
`persist_skip_bars=1` safety means Confluence is structurally
one trading bar behind the source cache, so once the cache
caught up to the resolver's cutoff, Confluence stayed
one bar shy. A pipeline rerun cannot close that gap until the
cache acquires a trading day strictly past `current_as_of_date`.

Pre-Phase-6G-5 the launch audit and the preflight both still
claimed an operator action would clear the staleness, which
was misleading. Phase 6G-5 adds a stable verdict that names
the structural lag explicitly.

### 6.8.1 New stable action constant

`pipeline_output_lags_persist_skip` — added to both:

  - `board_launch_readiness_audit.RECOMMENDED_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP`
    (also in `RECOMMENDED_ACTIONS`).
  - `source_freshness_preflight.ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP`
    (also in `PREFLIGHT_ACTIONS`).

Both modules emit the same literal string so an operator
scanning either tool's JSON sees the same answer.

### 6.8.2 Meaning

The source cache is **not stale** (`cache.last_date >=
current_as_of_date`), the full upstream chain is in place
(Spymaster cache + StackBuilder leaderboard + multi-timeframe
libraries), and at least one daily K TrafficFlow artifact has
been written. The only thing keeping Confluence from being
"current" by the strict `last_date >= current_as_of_date`
contract is the Phase 6D-1 `persist_skip_bars=1` trim — and
that trim is a load-bearing safety policy, not a bug. A
pipeline write today will reproduce the same one-trading-bar
lag.

Concretely, the audit fires this verdict when all of these
hold for the ticker:

  - `has_signal_engine_cache=True`,
  - `has_stackbuilder_run=True`,
  - `has_multitimeframe_libraries=True`,
  - `stale=False` (source cache is fresh),
  - `parsed(cache.last_date) == parsed(current_as_of_date)`
    (the source cache equals the resolver's cutoff exactly,
    i.e. the cache has no trading day strictly past the
    cutoff that would allow the persist trim to leave
    Confluence at-cutoff).

Under those conditions, the audit overrides what would have
been `RECOMMENDED_READY_FOR_PIPELINE_WRITE` and emits the new
verdict instead. The preflight passes the audit verdict
straight through to its own
`ACTION_PIPELINE_OUTPUT_LAGS_PERSIST_SKIP`.

### 6.8.3 Honest `likely_after_run_issue_codes`

When the predicate fires, the audit's prediction of "what
issue codes would persist after a pipeline rerun" keeps
`stale_confluence_day_artifact` in the post-run set. The old
behavior unconditionally dropped that code (treating it as
"runner-owned and therefore cleared"), which lied about the
rerun's effect. The new behavior is honest: a rerun will
clear `missing_confluence_day_artifact` (because a fresh
artifact WILL be written) but the readiness re-emits
`stale_confluence_day_artifact` on the very next inspection
because the rewrite still lands one trading bar behind the
cutoff.

### 6.8.4 Operator action

The correct operator response under
`pipeline_output_lags_persist_skip` is:

  1. **Wait** for the next trading-day market close. (For
     SPY that is the next weekday's Yahoo / NYSE close.)
  2. **Wait** for UTC to roll past that close.
     `confluence_pipeline_readiness.resolve_current_as_of_date`
     uses UTC, not US/Eastern, so the cutoff does not move
     immediately at market close.
  3. Once the cache has a trading day strictly after
     `current_as_of_date`, refresh the Signal Engine cache
     if not already current (Phase 6E-5 refresher,
     `--write`), then rerun the pipeline
     (`confluence_pipeline_runner --ticker <T> --write`).
     The persist trim now leaves Confluence at the cutoff
     exactly, so the recommendation flips back to
     `run_pipeline_after_refresh` and then to
     `already_leader_eligible` after the rerun.

No production cache write and no pipeline rerun will close
the gap until step 3's "trading day strictly after cutoff"
condition holds. Attempting them earlier produces
byte-identical persist-trimmed output.

### 6.8.5 Safety flag contract

`source_freshness_preflight` exposes two operator-facing
boolean safety flags. Under the new action:

  - `safe_to_attempt_refresh = False` — a refresh today
    will not produce a fresher cache than the one already
    on disk (today's bar is already in the cache).
  - `safe_to_run_pipeline_after_refresh = False` — a
    pipeline rerun today cannot clear the readiness
    blocker. The flag is False to keep the operator from
    being misled into running for cosmetic re-stamping.

Both flags reading `False` is the truthful signal: there is
no action available to the operator today that closes the
gap. The next-trading-day rollover is the only thing that
moves it.

### 6.8.6 What did NOT change

The Phase 6C-8 readiness contract is unchanged. The strict
`current = stage.last_date >= current_as_of_date` definition
is still load-bearing for the leader gate; loosening it would
mask the Phase 6D-1 persistence safety. The fix lives at the
audit/preflight prediction layer, where operator-facing
recommendations belong. `confluence_pipeline_runner.py` and
`confluence_pipeline_readiness.py` are untouched.

### 6.8.7 Reference paths

  - Launch readiness audit (the recommended-action source of
    truth): `project/board_launch_readiness_audit.py`.
  - Source-freshness preflight (the mirror):
    `project/source_freshness_preflight.py`.
  - Phase 6G-5 audit tests:
    `project/test_scripts/test_board_launch_readiness_audit.py`
    (search for `persist_skip_lag`).
  - Phase 6G-5 preflight tests:
    `project/test_scripts/test_source_freshness_preflight.py`
    (search for `persist_skip_lag`).
  - Daily Signal Board baseline doc § 7 (sibling caveat):
    `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md`.

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
  - Phase 6E-4 optimizer:
    `project/signal_engine_sma_optimizer.py`.
  - Phase 6E-4 optimizer tests:
    `project/test_scripts/test_signal_engine_sma_optimizer.py`.
  - Phase 6E-5 wiring (this phase, in the existing
    refresher module): `project/signal_engine_cache_refresher.py`.
  - Phase 6E-5 wiring tests:
    `project/test_scripts/test_signal_engine_cache_refresher.py`.
