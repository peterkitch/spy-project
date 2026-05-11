# Phase 6C-8 Confluence Pipeline Contract

**Status:** contract / readiness layer. Does **not** complete the
pipeline. Establishes the rules under which the public Daily
Signal Board may award a top-3 leader badge.

**Last updated:** 2026-05-11.

## 1. Intended pipeline

PRJCT9 is, in its long-term shape, a saved-research factory whose
public surface is a daily leaderboard of confluence-current
tickers. The intended end-to-end pipeline, in three phases:

### Phase 1 — Foundation (per-ticker saved research)

  - **Signal Engine / OnePass**: saves the Spymaster
    `<TICKER>_precomputed_results.pkl` cache plus the ticker's
    daily-interval `signal_library/data/stable/<TICKER>_stable_v1_0_0.pkl`
    library. This is the seed of every downstream stage.
  - **ImpactSearch**: runs single-signal-source studies for the
    target and persists the `research_day_v1` artifact under
    `output/research_artifacts/impactsearch/<TICKER>/`.
  - **StackBuilder**: builds multi-member stacks and persists
    leaderboard / combo / cohort files under
    `output/stackbuilder/<TICKER>/<seed_run>/` (XLSX leaderboards
    plus `combo_k=1.json` … `combo_k=12.json`). A `research_day_v1`
    StackBuilder artifact is written per `(target, seed, K)` to
    `output/research_artifacts/stackbuilder/<TICKER>/`.

### Phase 2 — Expansion (cross-K, cross-timeframe)

  - **TrafficFlow over all K-builds**: TrafficFlow runs against
    each StackBuilder K-build (K=1 … K=12) and persists a
    `research_day_v1` artifact per `(target, seed, K)` under
    `output/research_artifacts/trafficflow/<TICKER>/`.
  - **Multi-timeframe projection**: each TrafficFlow/K-build
    output is projected onto the canonical multi-timeframe set
    (1wk / 1mo / 3mo / 1y) so the signals can be compared across
    time windows.

### Phase 3 — Output (aggregate verdict and public surface)

  - **Confluence**: consumes the multi-timeframe TrafficFlow/K-build
    outputs, computes the per-day per-timeframe signal, and
    persists a `research_day_v1` confluence artifact per target
    under `output/research_artifacts/confluence/<TICKER>/`.
  - **Daily Signal Board**: reads only saved artifacts and ranks
    tickers by current confluence agreement.

## 2. Current implementation reality

Phase 1 is in place per-ticker for the tickers covered by saved
runs. Phase 3 (Confluence) produces durable artifacts, but the
inputs are not what Phase 2 calls for. The gap:

  - **StackBuilder run dirs** exist with `combo_k=1.json` …
    `combo_k=12.json`. Full K coverage at the seed-run level is
    available.
  - **`research_day_v1` StackBuilder artifacts** are sparse:
    typically one artifact per ticker, top-row / K1-focused.
  - **`research_day_v1` TrafficFlow artifacts** are also sparse:
    typically one artifact per ticker, K=1, single-timeframe
    (`timeframes` field is null on disk).
  - **`multi_timeframe_builder.py`** builds ticker-native interval
    libraries (`<TICKER>_stable_v1_0_0_<INTERVAL>.pkl`). It does
    **not** project TrafficFlow / K-build outputs into multi-timeframe
    libraries.
  - **`confluence_analyzer.py`** consumes the ticker-native
    interval libraries built by `multi_timeframe_builder.py`. It
    does **not** consume per-K TrafficFlow outputs.
  - **`confluence.py`** has an interactive / manual multi-primary
    bridge but no durable saved-artifact bridge from
    StackBuilder + TrafficFlow into Confluence.

In other words: the **multi-timeframe TrafficFlow / K-build to
Confluence bridge is missing**. Confluence works against
ticker-native multi-timeframe libraries today, which is honest
research but is not what Phase 2 specifies.

This Phase 6C-8 PR documents that gap and refuses to present
its consequences as "current leaders" on the public board. It
does **not** build the missing bridge.

## 3. Pipeline stage labels

The readiness module
(`project/confluence_pipeline_readiness.py`) inspects the
following stages, in order, for each ticker. Each stage is a
read-only filesystem probe — no engine import, no yfinance, no
disk writes.

| Stage id | Source |
| --- | --- |
| `signal_engine_cache` | `project/cache/results/<TICKER>_precomputed_results.pkl` |
| `impactsearch_artifact` | newest `*.research_day.json` under `output/research_artifacts/impactsearch/<TICKER>/` |
| `stackbuilder_leaderboard` | newest seed-run dir under `output/stackbuilder/<TICKER>/` containing `combo_leaderboard.xlsx` or `combo_k=*.json` |
| `stackbuilder_day_artifact` | newest `*.research_day.json` under `output/research_artifacts/stackbuilder/<TICKER>/` |
| `trafficflow_day_artifacts` | every `*.research_day.json` under `output/research_artifacts/trafficflow/<TICKER>/`; K-coverage and timeframe coverage measured |
| `multitimeframe_libraries` | non-daily libraries `<TICKER>_stable_v1_0_0_{1wk,1mo,3mo,1y}.pkl` under `signal_library/data/stable/` |
| `confluence_day_artifact` | newest `*.research_day.json` under `output/research_artifacts/confluence/<TICKER>/` |
| `catalogue_health` | the on-disk `output/research_artifacts/catalogue_health_report.json` health entry for the ticker |

## 4. Issue codes

The readiness module emits one or more issue codes per ticker
when stages are missing, stale, or inconsistent. The codes are
stable strings so the Daily Signal Board and audit tooling can
switch on them without translation:

  - `missing_signal_engine_cache`
  - `missing_impactsearch_artifact`
  - `missing_stackbuilder_leaderboard`
  - `missing_stackbuilder_day_artifact`
  - `missing_trafficflow_day_artifacts`
  - `insufficient_trafficflow_k_coverage` — saved TrafficFlow
    artifacts exist but cover only a subset of K=1..12.
  - `missing_multitimeframe_libraries` — fewer than two of
    `{1wk, 1mo, 3mo, 1y}` saved for the ticker.
  - `missing_multitimeframe_trafficflow_bridge` — no saved
    artifact represents TrafficFlow / K-build outputs projected
    onto the multi-timeframe set. **This is the architectural
    gap Section 2 describes; today every ticker carries this
    issue code.** Phase 6C-8 audit-tighten: this code now BLOCKS
    leader eligibility. A ticker-native Confluence verdict is no
    longer sufficient for a public podium spot - the public
    pipeline must include the multi-timeframe TrafficFlow /
    K-build bridge for the ticker to be a "current leader". Until
    the bridge artifact contract ships, the public board awards
    zero podium badges and surfaces the "no current leaders"
    banner.
  - `missing_confluence_day_artifact`
  - `stale_confluence_day_artifact` — confluence exists but its
    last daily-row date is older than the current expected
    as-of date.
  - `confluence_agreement_unavailable` — confluence artifact
    exists but `active_count` or `available_count` is missing /
    unparseable.
  - `health_report_blocked` — the catalogue health report flags
    this ticker as blocked in one or more engines.

## 5. "Current leader" vs "saved research row"

The Daily Signal Board renders two distinct populations of
ticker rows:

  - **Saved research row** — any ticker for which a Signal
    Engine cache file exists. These rows are visible on the
    scoreboard so the public surface stays honest about what
    PRJCT9 has studied. Cache-only rows show `Signal=None`,
    `Agreement=Unavailable`, `Coverage=Partial`, and may also be
    `Stale` or `Under review`. Saved rows carry
    `data-leader-eligible="false"` and never receive a top-3
    rank badge.
  - **Current leader** — a ticker that has a present, current
    confluence artifact with usable agreement fields and is not
    flagged in the catalogue health report. Only current
    leaders are eligible for the `data-rank="1|2|3"` podium
    badges. The board's section data attribute
    `data-ranking-method` advertises this gate:
    `current_confluence_leaders_only_then_agreement_desc_then_ticker_asc`.

## 6. Leader-eligibility gate

A ticker is `leader_eligible` if and only if **all** of:

  1. `confluence_day_artifact` is **present** for the ticker.
  2. `confluence_day_artifact` is **current** — its last daily-row
     date is at or after the resolved current-as-of date.
  3. The confluence artifact carries usable agreement fields:
     `active_count` and either `available_count` or a non-empty
     `timeframes` list. Otherwise
     `confluence_agreement_unavailable` is raised.
  4. The catalogue health report does **not** list the ticker
     under `engines_blocked` for any engine. Otherwise
     `health_report_blocked` is raised.
  5. **(Audit-tighten 2026-05-11)** The multi-timeframe
     TrafficFlow / K-build bridge is in place — at least one
     saved TrafficFlow `research_day_v1` artifact for the ticker
     declares a non-empty `timeframes` list with two or more
     entries. Otherwise `missing_multitimeframe_trafficflow_bridge`
     is raised and the gate fails.
  6. **(Audit-tighten 2026-05-11)** TrafficFlow K-coverage spans
     the documented K range (K=1..12). Otherwise
     `insufficient_trafficflow_k_coverage` is raised and the
     gate fails.

The current-as-of date is resolved in this order:

  1. Explicit `current_as_of_date` argument to
     `inspect_ticker_pipeline` / `inspect_universe_pipeline`.
  2. `PRJCT9_RESEARCH_AS_OF_DATE` environment variable
     (`YYYY-MM-DD`).
  3. **Conservative fallback**: the most recent weekday strictly
     before UTC today. This is a closed-form helper
     (`default_research_as_of_date`) — no network access, no
     market-calendar dependency, and fully tested.

Notably, a fresh TrafficFlow or StackBuilder artifact does **not**
mask a stale Confluence artifact. The leader gate is on
Confluence specifically; upstream freshness without aggregation
is not a current verdict.

## 6.1 Coverage reconciliation (Daily Signal Board)

The visible `Coverage` column on the public board must not
contradict the readiness verdict. The board applies the following
overrides after computing the stand-alone
`coverage_status_for_ticker` value:

  | Blocked reason | Visible coverage |
  | --- | --- |
  | `health_report_blocked` | `Under review` |
  | `stale_confluence_day_artifact` | `Stale` |
  | `missing_multitimeframe_trafficflow_bridge` | `Pipeline incomplete` |
  | `insufficient_trafficflow_k_coverage` | `Pipeline incomplete` |
  | _(none)_ | _(no override)_ |

`Pipeline incomplete` is a Phase 6C-8 addition to the documented
coverage labels. It slots between `Stale` and `Full` in
`COVERAGE_PRIORITY` so a row with a current confluence artifact
but a missing bridge / incomplete K coverage is never visibly
ranked "Full".

## 6.2 Phase 6D-1 progress note (2026-05-11)

Phase 6D-1 added `project/trafficflow_k_artifact_builder.py`: a
read-only / offline builder that walks a saved StackBuilder seed
run, loads its `combo_leaderboard.xlsx`, and materializes one
TrafficFlow `research_day_v1` artifact per K row (K=1..12 by
default). Artifacts persist at the K-distinguished path
`output/research_artifacts/trafficflow/<SAFE_TARGET>/<SAFE_RUN>__K<K>.research_day.json`
so artifact-path uniqueness is guaranteed across K values for
the same seed run.

What this closes:

  - `insufficient_trafficflow_k_coverage` clears for targets
    whose builder pass succeeded for every K in the expected
    range.

What this does NOT close:

  - `missing_multitimeframe_trafficflow_bridge`. The Phase 6D-1
    artifacts are single-timeframe (`timeframes` field stays
    empty). The multi-timeframe TrafficFlow / K-build projection
    is Phase 6D-2 and remains the gating issue between
    Confluence and public leader eligibility.

Public leaderboard eligibility therefore stays at zero until
Phase 6D-2 ships, even after a Phase 6D-1 sweep across the cache.

## 7. Out of scope for this PR

The following changes are **not** part of Phase 6C-8:

  - Building the multi-timeframe TrafficFlow / K-build to
    Confluence bridge. Phase 6C-8 documents the gap and emits
    the `missing_multitimeframe_trafficflow_bridge` issue code;
    a future Phase 6D / 6E PR is expected to ship the bridge
    itself.
  - Rewriting `confluence.py` or `confluence_analyzer.py`.
  - Touching `phase6_research_preview.py` (operator dashboard).
  - Touching `.bat` launchers.
  - Any styling / design work.
  - Daily automation or scheduled jobs.
  - Network calls (`yfinance` and friends).

## 8. Reference paths

  - Spec doc (this file):
    `project/md_library/shared/2026-05-11_PHASE_6C8_CONFLUENCE_PIPELINE_CONTRACT.md`
  - Readiness module:
    `project/confluence_pipeline_readiness.py`
  - Daily Signal Board (gate consumer):
    `project/daily_signal_board.py`
  - Readiness tests:
    `project/test_scripts/test_confluence_pipeline_readiness.py`
  - Board tests:
    `project/test_scripts/test_daily_signal_board.py`
