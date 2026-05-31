# Phase 6H-1 — Daily Signal Board launch / design handoff

> **Status banner (added post K=6 MTF MVP launch):** This
> doc is preserved as **historical / parallel-rail
> operating record**. The Phase 6H / 6I daily-board
> automation chain described below is preserved, but it is
> **NOT** the current website-launch path and is **NOT**
> the active sprint cursor. The current launch surface is
> the **K=6 MTF MVP board** (`mvp_signal_board.py`
> rendering `k6_mtf_ranking_v1`). For the current sprint
> cursor read: `<PROJECT_DIR>/CLAUDE.md` Section 6;
> `<PROJECT_DIR>/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`;
> `<PROJECT_DIR>/md_library/shared/2026-05-26_REACT_MIGRATION_DECLARATION_AND_FRONTEND_CONTRACT.md`.

**Status:** launch-readiness handoff. The board is in the
best practical state for design/product review and is
operationally honest about its only current-leader gap. No
production data writes are authorized in this phase.

**Last updated:** 2026-05-12.

This doc is the one-page-ish operator handoff that pairs
with the current Daily Signal Board on `main`. It tells a
reviewer (or a future agent) how to run the board, what to
expect, what is safe to ship a review against, and what is
not yet launch-safe.

## 1. Current repo + head baseline

| Field | Value |
|---|---|
| Repository | `C:\Users\sport\Documents\PythonProjects\spy-project` |
| Branch | `main` |
| HEAD | `576b676` — *Phase 6G-5: SPY currentness gap audit + persist-skip-lag honest recommendation (#210)* |
| Code baseline / handoff baseline | **current `main` at `576b676`** — this is the authoritative review target for both UI and operator-tool surfaces. |
| Visual review baseline | **current `main` at `576b676`** (the Phase 6G-4 Town Notice Board polish + the Phase 6G-5 persist-skip-lag honesty are both live). Phase 6G-2 screenshots are historical / pre-polish; Phase 6G-4 screenshots are the closest existing screenshot reference for the current visual direction; current `main` booted with the pinned cutoff is the source of truth. |
| Semantic / public-meaning baseline | `24990f0` (Phase 6G-1 merge) — the seven-section hierarchy and "Consensus / No consensus / Saved Research Archive" public framing locked in here. This is the **historical** semantic anchor, not the current visual review target. |
| Data cutoff for reproducing SPY rank-1 | `PRJCT9_RESEARCH_AS_OF_DATE=2026-05-08` (against the on-disk artifacts; see § 2). |
| Full regression | **1,213 passed**, 60 pre-existing pandas fragmentation warnings only. |
| Pinned Python | `C:\Users\sport\AppData\Local\NVIDIA\MiniConda\envs\spyproject2\python.exe` |

Re-confirm before assuming this is still current:

```
git log -10 --oneline main
```

## 2. How to run the current UI review baseline (pinned cutoff)

The current visual review baseline is **current `main` at
`576b676`** — the Phase 6G-4 Town Notice Board polish and
the Phase 6G-5 persist-skip-lag honesty are both live on
this commit. SPY reproduces as the rank-1 leader-eligible
row only when readiness is pinned to the trading day the
SPY pipeline tree was written for (`2026-05-08`). Use the
pinned interpreter and a read-only env:

```powershell
$env:PRJCT9_PUBLIC_READ_ONLY = '1'
$env:PRJCT9_BOARD_PORT = '8061'
$env:PRJCT9_RESEARCH_AS_OF_DATE = '2026-05-08'
& 'C:\Users\sport\AppData\Local\NVIDIA\MiniConda\envs\spyproject2\python.exe' daily_signal_board.py
```

Then open `http://127.0.0.1:8061`. Expected:

  - SPY renders as the rank-1 leader-eligible row in the
    Town Hall Scoreboard.
  - Today's Board Status hero card identifies SPY as the
    current pilot with the Phase 6G-4 mustard pin + neon
    `current-pilot-chip` accent.
  - Featured High Score shows the Signal Engine chart and
    `Short 11,5` headline.
  - Saved Research Archive `<details>` is closed with
    `data-archive-row-count="1628"`.
  - Evidence Trail station glyphs render
    (SF/TP/WK/RY/CH/TH/WT).

This is the current UI review baseline: live code from
current `main` rendered against the on-disk artifacts under
the pinned cutoff. The closest existing screenshot
reference is the Phase 6G-4 audit set
(`C:\Users\sport\AppData\Local\Temp\phase_6g_4_audit\`),
but those are an audit-time snapshot; the rendered board on
current `main` is the source of truth. Phase 6G-2
screenshots are pre-polish and are NOT the current visual
target.

A read-only verification (no Dash boot) reproduces the
pinned verdict directly through the readiness layer:

```powershell
$env:PRJCT9_RESEARCH_AS_OF_DATE = '2026-05-08'
& 'C:\Users\sport\AppData\Local\NVIDIA\MiniConda\envs\spyproject2\python.exe' -c "from confluence_pipeline_readiness import inspect_ticker_pipeline; r = inspect_ticker_pipeline('SPY', fast_path_when_no_confluence=False); print('leader_eligible:', r.leader_eligible, 'issue_codes:', r.issue_codes, 'current_as_of_date:', r.current_as_of_date)"
```

Expected output:

```
leader_eligible: True issue_codes: () current_as_of_date: 2026-05-08
```

## 3. How to run the board unpinned and what to expect

A bare unpinned boot resolves `current_as_of_date` to the
"most recent weekday strictly before UTC now". On any day
after `2026-05-08`, the resolver advances ahead of where
the saved pipeline tree sits, and SPY demotes to the Saved
Research Archive:

```powershell
$env:PRJCT9_PUBLIC_READ_ONLY = '1'
$env:PRJCT9_BOARD_PORT = '8061'
Remove-Item env:PRJCT9_RESEARCH_AS_OF_DATE -ErrorAction SilentlyContinue
& 'C:\Users\sport\AppData\Local\NVIDIA\MiniConda\envs\spyproject2\python.exe' daily_signal_board.py
```

Expected on a bare unpinned boot today:

  - SPY does NOT appear in the Town Hall Scoreboard
    (`recommended_pilot_tickers = []`).
  - The Town Hall Scoreboard renders the Phase 6C-8
    no-current-leaders banner.
  - SPY appears in the Saved Research Archive with
    `data-ranking-blocked-reason="stale_confluence_day_artifact"`.
  - The Today's Board Status hero card carries the
    no-current-pilot copy.

This is the **honest behavior of the existing contract**,
not a regression. The board surfaces an empty leader gate
rather than fabricating one. The persist-skip-lag contract
is documented in
`2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md` § 7
and `2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md`
§ 6.8.

## 4. What `pipeline_output_lags_persist_skip` means in plain English

The Phase 6D-1 pipeline is a saved-research engine, not a
real-time feed. By policy it trims the final bar off every
persisted artifact (`persist_skip_bars=1`) so the saved
tree never carries today's still-revising data from
yfinance. That means the saved Confluence is always one
trading bar behind the source cache.

When the source cache catches up to the readiness
resolver's `current_as_of_date`, Confluence ends up one
trading bar shy of that cutoff and the strict
`current = stage.last_date >= current_as_of_date` rule
reports stale. **A pipeline rerun today will not close the
gap**, because the rerun applies the same trim and lands
Confluence in the same place.

That is what the audit and the preflight now say:

> `pipeline_output_lags_persist_skip`
>
> The pipeline output structurally lags the cache by
> `persist_skip_bars` trading days. The cache is fresh,
> the upstream chain is in place, the pipeline is healthy
> — but no refresh + rerun cycle today can advance
> Confluence to `current_as_of_date`. Wait until the
> source cache acquires a trading day strictly past
> `current_as_of_date` (a cache-vs-cutoff inequality check,
> not a wall-clock event), then run the authorized refresh
> + pipeline cycle.

The gate to watch is therefore the strict inequality
`cache.last_date > current_as_of_date`, not "wait for UTC
midnight" or any clock event. The cheapest read-only probe
is the Phase 6H-2 watcher (added in #212), which neither
fetches yfinance nor touches the pipeline runner:

```powershell
& 'C:\Users\sport\AppData\Local\NVIDIA\MiniConda\envs\spyproject2\python.exe' cache_cutoff_watcher.py --ticker SPY
```

The watcher emits a JSON `CacheCutoffWatchReport` with
`cache_ahead_of_cutoff` / `cache_equal_to_cutoff` /
`cache_behind_cutoff` flags, the same
`pipeline_output_lags_persist_skip` action constant the
launch audit + freshness preflight use, and a top-level
`ready_tickers` list scoped to the strict-inequality wins
only. That `ready_tickers` list is the gate a future daily
automation can hang off without producing a persist-skip-lag
verdict. If a fresh yfinance peek is also desired (it
fetches network data), the older heavier probe is still
available via `signal_engine_cache_refresher.py
--ticker SPY --dry-run`.

## 5. What is safe to review now

Everything design-review-relevant is in place against the
pinned baseline. Specifically:

  - **Visual direction.** The Phase 6G-4 Town Notice Board
    palette is the current visual language: warm-dark page,
    paper section cards, mustard pin accent, sage primary
    green, and the legacy neon `#80ff00` reserved
    exclusively for the current-leader accent (SPY row left
    border + `current-pilot-chip`). All color literals
    route through `DESIGN_TOKENS` and the centralization
    test enforces it.
  - **Information hierarchy.** Phase 6G-1's seven-section
    layout (Today's Board Status → Town Hall Scoreboard →
    Saved Research Archive → Featured High Score →
    Evidence Trail → What PRJCT9 Is → What It Is Not)
    is the right shape for a public reader; the design
    review can ratify or challenge it as-is.
  - **Mobile contract.** Phase 6F-7's contained internal
    scroll inside `scoreboard-table-wrapper` keeps the
    page-level scroll axis vertical-only on 390×844 and
    similar widths. `data-mobile-overflow="contained"` is
    test-pinned.
  - **Copy contract.** All visible strings route through
    `BOARD_COPY`; a copy review can ship as a single
    focused PR without re-implementing layout.
  - **Operator honesty.** The launch audit and the
    freshness preflight now name the persist-skip-lag
    situation explicitly via
    `pipeline_output_lags_persist_skip`. A reviewer can
    trust the recommendation strings to reflect what the
    pipeline can actually do.
  - **Public read-only mode.** `PRJCT9_PUBLIC_READ_ONLY=1`
    is the contract for any public-facing boot; the web
    tier has no write paths to yfinance, Spymaster, or the
    Phase 6D pipeline runner.

## 6. What is not yet launch-safe

Things that should NOT be implied as ready for a public
launch event:

  - **Daily currentness automation.** The unpinned board
    demotes SPY any time `current_as_of_date` has rolled
    past the saved pipeline tree's last trading day. There
    is no scheduler / orchestrator that refreshes the cache
    + reruns the pipeline once the strict inequality
    `cache.last_date > current_as_of_date` opens. Until
    that exists, a public launch would either need:
      - a documented "as of 2026-05-08" framing that
        matches the pinned baseline, OR
      - operator commitment to manually run the refresh +
        pipeline cycle inside the post-market-close window
        every trading day.
    Neither is automation; both are honest stopgaps.
  - **Universe coverage beyond SPY.** Every other ticker in
    the discovered universe is `coverage=Partial /
    signal=None`. The board is publicly honest about this
    (Saved Research Archive labelled accordingly) but it is
    still a one-ticker-current product.
  - **Data licensing (Phase 5G).** yfinance remains the
    sprint data source pending the pre-launch licensing
    gate. A real public launch needs the Phase 5G decision
    record before any commercial framing.
  - **Validation surfacing.** Phase 5C's `validation_ledger_v1`
    aggregates honest validation sidecars but is not wired
    into the public Daily Signal Board. The public surface
    today is research framing only; a "validated leader"
    badge is not on the board.
  - **Health-report-blocked tickers.** A ticker flagged by
    the catalogue health report demotes from the leader
    gate, but no public-facing UI explains the block. For
    the design review this is acceptable because SPY is
    not blocked; for launch it should be documented.

## 7. Read-only verification (captured this phase)

Both checks were run from `main` at `576b676`, against the
real on-disk SPY artifacts. No production writes.

### 7.1 `source_freshness_preflight.py --ticker SPY` (unpinned)

```json
{
  "generated_at": "2026-05-12T02:11:34+00:00",
  "current_as_of_date": "2026-05-11",
  "inspected_count": 1,
  "candidates": [
    {
      "ticker": "SPY",
      "cache_exists": true,
      "cache_date_range_end": "2026-05-11",
      "current_as_of_date": "2026-05-11",
      "stale": false,
      "has_stackbuilder_run": true,
      "board_launch_recommended_action": "pipeline_output_lags_persist_skip",
      "safe_to_attempt_refresh": false,
      "safe_to_run_pipeline_after_refresh": false,
      "recommended_next_action": "pipeline_output_lags_persist_skip"
    }
  ],
  "counts_by_recommended_action": {
    "pipeline_output_lags_persist_skip": 1
  },
  "notes": []
}
```

### 7.2 `board_launch_readiness_audit.py --tickers SPY --no-dry-run` (unpinned)

```json
{
  "generated_at": "2026-05-12T02:11:41+00:00",
  "current_as_of_date": "2026-05-11",
  "inspected_count": 1,
  "candidates": [
    {
      "ticker": "SPY",
      "has_signal_engine_cache": true,
      "has_stackbuilder_run": true,
      "has_daily_k_trafficflow_artifacts": true,
      "has_mtf_k_trafficflow_artifacts": true,
      "has_confluence_artifact": true,
      "current_readiness_issue_codes": ["stale_confluence_day_artifact"],
      "current_leader_eligible": false,
      "current_ranking_blocked_reason": "stale_confluence_day_artifact",
      "runner_dry_run_issue_codes": [],
      "can_run_pipeline_now": true,
      "likely_after_run_issue_codes": ["stale_confluence_day_artifact"],
      "latest_known_date": "2026-05-11",
      "stale": false,
      "recommended_action": "pipeline_output_lags_persist_skip"
    }
  ],
  "recommended_pilot_tickers": [],
  "counts_by_recommended_action": {
    "pipeline_output_lags_persist_skip": 1
  },
  "counts_by_blocker": {
    "stale_confluence_day_artifact": 1
  },
  "notes": []
}
```

### 7.3 Pinned-cutoff readiness probe (`PRJCT9_RESEARCH_AS_OF_DATE=2026-05-08`)

```
leader_eligible: True issue_codes: () current_as_of_date: 2026-05-08
```

The same readiness layer that demotes SPY under the bare
boot puts SPY back on the rank-1 podium under the pin. The
on-disk artifacts and the readiness code are unchanged;
only the cutoff differs.

## 8. Next recommended workstreams

In rough priority order, **none of which require data
writes**:

  1. **Design / product review.** Pinned-cutoff boot of
     **current `main` (`576b676`)** is the authoritative
     review target. Run the board with
     `PRJCT9_RESEARCH_AS_OF_DATE=2026-05-08` (recipe in
     § 2) and review the live UI. Phase 6G-4 screenshots
     (`C:\Users\sport\AppData\Local\Temp\phase_6g_4_audit\`)
     are the closest existing screenshot reference for the
     current Town Notice Board visual direction if a
     static artifact is needed; Phase 6G-2 screenshots are
     pre-polish historical and should not be used as the
     visual target. Output: chosen visual direction
     confirmation (or change request against current main)
     + prioritized polish backlog. Owner: design lead.
  2. **Public-copy polish (optional).** All visible
     strings route through `BOARD_COPY`. A copywriter pass
     can ship as one focused PR with the centralization
     test catching every edit. Suggested scope: the
     no-current-pilot copy (what the user sees on the
     unpinned bare boot), the disclaimer phrasing in the
     Featured panel, and the Saved Research Archive intro.
  3. **Launch framing decision.** Choose between
     "as of 2026-05-08" public framing (no automation
     needed) and an automation Phase 6H-2+ scope
     (scheduler + post-market-close refresher + pipeline
     runner). The persist-skip-lag contract makes the
     trade-off explicit.
  4. **Phase 6H-2 (delivered, #212): cache-vs-cutoff
     inequality watcher.** The read-only watcher lives at
     `project/cache_cutoff_watcher.py`. It emits a
     `CacheCutoffWatchReport` JSON with per-ticker
     `cache_ahead_of_cutoff` / `cache_equal_to_cutoff` /
     `cache_behind_cutoff` flags, a stable
     `recommended_operator_action` string drawn from the
     same namespace the launch audit + preflight use, and a
     top-level `ready_tickers` list scoped to the
     strict-inequality wins only. The watcher imports
     neither yfinance, dash, the Phase 6D pipeline runner,
     nor any artifact writer; it reads only the saved
     Signal Engine cache PKL's date metadata. This is the
     gate a future daily automation can hang off, and it
     ships with zero production write surface.
  5. **Data licensing (Phase 5G).** Pre-launch gate.
     Currently parked; required before a commercial
     framing of "current leader."
  6. **Universe automation scoping (Phase 5D-2 / 5D-3).**
     Out of band of the MVP polish track; needs scheduler
     + scoping doc before implementation.

The MVP polish track is essentially done. The next moves
are about deciding what to ship and how to frame the
currentness gap publicly, not about more code in the
board.

## 9. Reference paths

  - Public board module:
    `project/daily_signal_board.py`
  - Public board tests:
    `project/test_scripts/test_daily_signal_board.py`
  - Launch readiness audit:
    `project/board_launch_readiness_audit.py`
  - Source freshness preflight:
    `project/source_freshness_preflight.py`
  - Cache-vs-cutoff watcher (Phase 6H-2):
    `project/cache_cutoff_watcher.py`
  - Cache-vs-cutoff watcher tests:
    `project/test_scripts/test_cache_cutoff_watcher.py`
  - Phase 6G semantic / public-meaning baseline doc:
    `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md`
    (the historical Phase 6G-1 anchor; § 7 is the
    persist-skip-lag contract that is still in effect on
    current main).
  - Phase 6E-2 preflight doc:
    `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md`
    (§ 6.8 documents the new
    `pipeline_output_lags_persist_skip` action.)
  - Phase 6G-2 audit screenshots (historical /
    pre-Town-Notice-Board polish; NOT the current visual
    target):
    `C:\Users\sport\AppData\Local\Temp\phase_6g_2_audit\`
  - Phase 6G-4 audit screenshots (closest existing
    screenshot reference for the current Town Notice
    Board visual direction; current main is the source
    of truth):
    `C:\Users\sport\AppData\Local\Temp\phase_6g_4_audit\`
  - Phase 6C-8 leader-gate contract:
    `project/md_library/shared/2026-05-11_PHASE_6C8_CONFLUENCE_PIPELINE_CONTRACT.md`
