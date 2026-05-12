# Phase 6G — Daily Signal Board design-review baseline

**Status:** baseline frozen. The Daily Signal Board MVP is
technically stable, the public-meaning + information-
hierarchy pass has merged, and SPY is rendering as the
first current full-pipeline pilot. The next deliverable is
a design/product review against the live screenshots; no
further pipeline writes are currently authorized.

**Last updated:** 2026-05-11.

## 1. Commit baseline

| Field | Value |
|---|---|
| Branch | `main` |
| HEAD | `24990f0` |
| Title | *Phase 6G-1: Daily Signal Board public meaning + information hierarchy (#207)* |
| Smoke / freeze | Phase 6G-2 read-only smoke: 104/104 focused tests, 0 console errors, 0 server errors, no production writes. PR #207 audit baseline: 1,192/1,192 full regression, 60 pre-existing pandas fragmentation warnings. |

Re-confirm before assuming this baseline is still live:

```
git log -10 --oneline main
python source_freshness_preflight.py --ticker SPY
python board_launch_readiness_audit.py --tickers SPY --no-dry-run
```

## 2. What is live on the public Daily Signal Board

`project/daily_signal_board.py` ships seven sections,
top-to-bottom:

  1. **Today's Board Status** — `section-current-pilot`.
     Hero card built from the rank-1 leader-eligible row.
     Carries the pilot's Confluence consensus framing and
     Signal Engine state side-by-side. Non-directional
     copy (no "Buy"/"Short" framing) when consensus is
     `None`.
  2. **Town Hall Scoreboard** — `section-scoreboard`.
     Only leader-eligible rows in the default visible
     section. Column header reads "Consensus" (not the
     legacy "Signal"). Visible cell for `signal=None`
     renders "No consensus"; `data-signal` on the row
     still carries the canonical `"None"` / `"Buy"` /
     `"Short"` value so audit tooling is unchanged.
  3. **Saved Research Archive** —
     `section-archive` / `section-archive-details`.
     A `<details>` collapsible (`open=false`) wrapping a
     second scoreboard table; holds the long
     alphabetical tail of `coverage=Partial` / `Stale` /
     `Under review` rows. `data-archive-row-count` on
     the section reports the count (currently `1628`).
  4. **Featured High Score** — `section-featured`.
     Signal Engine chart + headline numbers for the
     selected ticker. A new italic
     `featured-two-signal-explainer` Div sits between
     the confluence status and the chart and defuses the
     "scoreboard says No consensus but Featured says
     Short" confusion. The Featured confluence status
     wording is now `"{active} of {total} alignment
     checks active"` (60 = 12 K-builds × 5 timeframes,
     not 60 timeframes).
  5. **Evidence Trail** — `section-evidence-trail`.
     Seven station cards: **Seed Field /
     Trading Post / Workshop / Rail Yard /
     Calendar House / Town Hall / Watchtower**. Prefixed
     by `evidence-trail-intro` explaining that stale
     upstream stations are historical reference and
     don't block the current leader gate unless flagged.
  6. **What PRJCT9 Is** — `section-what-prjct9-is`.
  7. **What It Is Not** — `section-what-it-is-not`.

The Phase 6C-8 no-current-leaders banner inside
`section-scoreboard` continues to fire (`data-leader-count`
attribute on the banner Div) when zero rows pass the
leader gate.

Mobile layout (≤ ~390 px wide) uses contained internal
horizontal scroll inside `scoreboard-table-wrapper`
(Phase 6F-7). The page itself never grows horizontal
scroll. The COVERAGE and AS OF columns are one swipe
away rather than wrapping into broken fragments.

## 3. What SPY means publicly today

SPY is the only ticker currently passing the Confluence-
leader gate on the live local artifacts. The user-facing
copy was deliberately scoped to avoid implying a trade.

Live state at the baseline:

| Field | Value |
|---|---|
| Signal Engine cache `date_range.end` | `2026-05-11` |
| Confluence MTF consensus `last_date` | `2026-05-08` |
| `data-rank` | `"1"` |
| `data-leader-eligible` | `"true"` |
| `data-ranking-blocked-reason` | `""` |
| `data-signal` | `"None"` (canonical) |
| Visible scoreboard Consensus cell | "No consensus" |
| Featured `current_signal` | `"Short"` |
| Featured current SMA pair | `Short 11,5` |
| Featured confluence status | `"7 of 60 alignment checks active"` |
| Today's Board Status copy | "SPY is the current full-pipeline pilot." / "Board consensus: No directional consensus today." / "Signal Engine state: Short 11,5." / "As of 2026-05-08 (board consensus) / 2026-05-11 (Signal Engine cache)." |
| Town Hall (Evidence Trail) station | `present`, `data-as-of="2026-05-08"` |
| Trading Post (Evidence Trail) station | `stale`, `data-as-of="2026-01-21"` (legacy ImpactSearch research_day artifact; shown as stale evidence; does not block the current Phase 6C-8 leader gate) |

The scoreboard cell and the Featured panel reflect two
distinct signal contracts:

  - **Scoreboard "Consensus"** = the Confluence consensus
    across 12 K-builds × 5 timeframes (60 alignment
    checks). SPY's today: 7 active checks; below the
    agreement threshold, so the consensus is `None`.
  - **Featured "Current Signal"** = the standalone
    Signal Engine SMA-pair readout from the saved cache.
    SPY's today: `Short 11,5`.

Both are honest. The two-signal explainer next to the
Featured chart names both contracts explicitly.

## 4. Reference paths

  - Screenshots captured during the Phase 6G-2 baseline
    smoke (desktop 1365×768 + mobile 390×844, plus the
    full `/_dash-layout` JSON and the server.log):
    `C:\Users\sport\AppData\Local\Temp\phase_6g_2_audit\`
  - Public board module:
    `project/daily_signal_board.py`
  - Public board tests:
    `project/test_scripts/test_daily_signal_board.py`
  - Phase 6F SPY pilot doc:
    `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md`
    (Phase 6E + 6F train, including § 6.7 Phase 6E-5
    wiring and the SMA-optimizer extraction sequence)
  - Phase 6C-8 readiness + leader-gate contract:
    `project/md_library/shared/2026-05-11_PHASE_6C8_CONFLUENCE_PIPELINE_CONTRACT.md`

## 5. Remaining limitations

  - **Only SPY is production-pilot current.** Every
    other ticker in the discovered universe is
    `coverage=Partial / signal=None` (saved-research-only
    archive rows).
  - **Broader-universe refresh + pipeline automation is
    unbuilt.** Single-ticker tooling exists
    (`signal_engine_cache_refresher.py`,
    `confluence_pipeline_runner.py`) with explicit
    `--ticker` and no universe sweep. A future phase
    needs a scheduler / orchestrator before more tickers
    can join SPY on the board.
  - **ImpactSearch / StackBuilder day artifacts may
    remain legacy / stale.** They are dated
    `research_day` evidence stations and may render
    stale or current. Under the current Phase 6C-8
    leader gate, their staleness does not block the
    Confluence leader verdict. (The StackBuilder
    *leaderboard directory* is presence-only; the
    research_day day artifact is not.)
  - **Mobile scoreboard uses contained internal
    horizontal scroll.** Page-level horizontal scroll is
    impossible on the board; the table content scrolls
    inside `scoreboard-table-wrapper`. This is an
    intentional Phase 6F-7 design and is documented in
    the test suite as
    `data-mobile-overflow="contained"`.

## 6. Next phase options (no data writes unless authorized)

  - **Design / product review** against the Phase 6G-2
    screenshots. Owner: design lead. Output: a chosen
    visual direction (e.g. cozy notice-board, hand-drawn
    village map, soft palette shift) plus a sprint-sized
    visual-polish backlog.
  - **Visual / cozy notice-board polish.** Once the
    direction is chosen, ship one focused PR for the
    polish items (palette / typography / station
    illustrations / sectional rhythm). Strictly
    layout/CSS/markup; data semantics frozen.
  - **Public copy review.** A copywriter pass over the
    visible strings (BOARD_COPY, current-pilot card,
    Featured / Evidence Trail intros). All edits route
    through the existing BOARD_COPY dict so the
    copy-centralization test catches them.
  - **Universe automation scoping.** Out of band of the
    current MVP polish track. Phase 5D-2 / 5D-3 territory
    (distributed compute + scheduler).
  - **Accessibility + performance pass.** WCAG AA
    contrast, `prefers-reduced-motion` honoring, and a
    Lighthouse mobile pass with the polish-direction
    asset bundle.

No further pipeline writes are authorized until an
explicit prompt enables them. The current SPY pilot state
is the design-review baseline and should remain stable
through the review.

## 7. Phase 6G-5 persist-skip-lag cutoff caveat

The frozen baseline above describes SPY's state on the day
the design-review was captured, when
`current_as_of_date=2026-05-08` and the saved Confluence
artifact matched. That alignment was time-bounded; this
section documents what an operator sees today and what the
correct operator response is.

### 7.1 What changes under an unpinned boot

Once UTC advances past the trading day the saved pipeline
tree was written for, `current_as_of_date` rolls forward
while the persisted artifacts stay where they are. With the
SPY cache still being refreshed daily, the gap manifests as
a one-trading-bar lag in every persisted stage:

| Stage | Live last_date (today) | Why |
|---|---|---|
| Signal Engine cache | `2026-05-11` | Spymaster refresh covers the most recent trading-day close. |
| daily K artifacts (Phase 6D-1, `persist_skip_bars=1`) | `2026-05-08` | The persistence safety trims `cache.last_date` by one trading bar so the saved tree never carries yfinance's provisional same-day data. |
| MTF K artifacts (Phase 6D-2, `persist_skip_bars=0`) | `2026-05-08` | Inherits from daily K. The Phase 6F-4 double-trim fix is still in place; only the Phase 6D-1 trim applies. |
| Confluence MTF (Phase 6D-3) | `2026-05-08` | Inherits from MTF K. |
| Readiness `current_as_of_date` (unpinned) | `2026-05-11` | `confluence_pipeline_readiness.resolve_current_as_of_date` resolves to "most recent weekday strictly before UTC now". UTC has rolled past 2026-05-08. |

Because Confluence's last_date is structurally
`cache.last_date - persist_skip_bars` trading bars and the
cache currently equals `current_as_of_date`, the readiness
layer's strict `last_date >= current_as_of_date` rule
reports `stale_confluence_day_artifact` and demotes SPY out
of the leader gate. **A bare production boot today should
not be expected to show SPY as `data-leader-eligible="true"`
under the current pipeline contract.** No regression has
landed; this is the honest behavior of the Phase 6D-1
persistence safety + the Phase 6C-8 readiness contract
working as designed.

### 7.2 Reproducing the frozen design-review baseline

To re-show the SPY-as-rank-#1 / leader-eligible state from
the screenshots in § 4 against the on-disk artifacts, pin
the readiness cutoff to the day the artifacts were written:

```
PRJCT9_PUBLIC_READ_ONLY=1 \
  PRJCT9_BOARD_PORT=8061 \
  PRJCT9_RESEARCH_AS_OF_DATE=2026-05-08 \
  python daily_signal_board.py
```

`confluence_pipeline_readiness.resolve_current_as_of_date`
honors `PRJCT9_RESEARCH_AS_OF_DATE` before falling back to
the UTC-now resolver. Under the pin, every saved Confluence
/ MTF K / daily K artifact is `last_date == cutoff`, the
readiness layer clears `stale_confluence_day_artifact`, and
SPY renders as the rank-1 leader-eligible row matching the
baseline screenshots. This is for design-review reproducibility
only; no production cache or pipeline write is performed.

### 7.3 Correct unpinned recommendation

Phase 6G-5 adds a new stable verdict to the launch
readiness audit and the source-freshness preflight so the
operator-facing answer for SPY (and any future ticker in
the same shape) is honest. The pre-Phase-6G-5 behavior
falsely claimed a rerun would close the gap.

| Tool | Pre-Phase-6G-5 verdict | Post-Phase-6G-5 verdict |
|---|---|---|
| `board_launch_readiness_audit` `recommended_action` | `ready_for_pipeline_write` | `pipeline_output_lags_persist_skip` |
| `board_launch_readiness_audit` `likely_after_run_issue_codes` | `()` *(lied: implied the rerun cleared `stale_confluence_day_artifact`)* | `("stale_confluence_day_artifact",)` |
| `board_launch_readiness_audit` pilot manifest | SPY listed as pilot-ready | SPY NOT in `recommended_pilot_tickers` |
| `source_freshness_preflight` `recommended_next_action` | `run_pipeline_after_refresh` | `pipeline_output_lags_persist_skip` |
| `source_freshness_preflight` `safe_to_attempt_refresh` | `True` | `False` |
| `source_freshness_preflight` `safe_to_run_pipeline_after_refresh` | `True` | `False` |

See § 6.8 of
`project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md`
for the full preflight contract under the new action.

### 7.4 What the operator should do

Until the cache carries a trading day strictly past
`current_as_of_date`, no refresh + rerun cycle will produce
a current Confluence verdict. The correct operator response
is simply to wait for the next trading-day rollover:

  1. Wait for the next NY market close (so the cache picks
     up that day's bar; SPY's Spymaster refresh path will
     populate this).
  2. Wait for UTC to roll past that close (the resolver
     uses UTC, not US/Eastern, so a market-close-time refresh
     does NOT immediately move the cutoff).
  3. Once the cache has a trading day strictly after
     `current_as_of_date`, the Phase 6D-1 trim drops back
     to the cutoff and a pipeline rerun produces a current
     Confluence verdict. The recommendation flips from
     `pipeline_output_lags_persist_skip` back to
     `ready_for_pipeline_write`.

Until then, nothing actionable. No production write is
authorized to "fix" the gap; the gap is the contract.
