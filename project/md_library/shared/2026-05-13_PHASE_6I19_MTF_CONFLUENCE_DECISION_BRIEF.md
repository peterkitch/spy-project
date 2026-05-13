# 2026-05-13 — Phase 6I-19: multi-timeframe Confluence decision brief

## 0. Scope

A new read-only module `confluence_decision_brief.py`
(+ CLI + tests + this doc) that **replaces the old
manual TrafficFlow K=6 + AI-prompt workflow** with a
structured operator-facing brief. The brief consumes
the existing Phase 6I-3 ranking emitter / Phase 6I-5
universe planner outputs — it does **not** rebuild
ranking math.

**Strict no-write contract.** No writer `--write`. No
`PRJCT9_AUTOMATION_WRITE_AUTH` env var. No source
refresh. No production pipeline write. No StackBuilder /
OnePass / ImpactSearch / TrafficFlow / Spymaster batch
execution. No `yfinance` fetch (the brief delegates to
the Phase 6I-3 emitter, which itself is read-only by
contract; the emitter's downstream contract validator +
artifact loader never call yfinance). Forbidden-imports
static guard blocks the writer / refresher / pipeline
runner / live engines / yfinance / dash / subprocess at
the brief module's top level.

## 1. How this replaces the old manual workflow

The old flow operators used to answer "what's our best
buy / short candidate today?" was:

  1. delete cached PKLs (so TrafficFlow would notice
     them as missing);
  2. let TrafficFlow build a "missing" list;
  3. run the Spymaster batch to refill them;
  4. inspect TrafficFlow's K=6 confluence table by eye;
  5. paste the table into an AI prompt and ask for a
     ranking + pattern read.

Three problems with that flow:

  - **Destructive prereq** — step 1 is a deliberate
    cache invalidation; the system has been deliberately
    moving toward never-delete since Phase 5C-1.
  - **K=6 only** — step 4 cherry-picks one K value out
    of K=1..12; the ranking signal can sit at K=2 or
    K=11 and be completely missed.
  - **Single-timeframe ambiguity** — the manual table
    didn't distinguish a daily-only confluence from a
    broad multi-timeframe alignment, so the AI prompt
    couldn't either.

Phase 6I-1 / 6I-3 / 6I-5 fixed all three:

  - The Phase 6I-1 validator is read-only against the
    on-disk artifacts (no cache invalidation needed).
  - The Phase 6I-3 emitter aggregates per-ticker
    ranking inputs across **all 12 K values and up to 5
    timeframes** (12 × 5 = 60 alignment cells).
  - The Phase 6I-3 emitter pre-sorts three operator-
    facing tails: positive (buy / long), negative
    (short / sell), and low-buy (near-zero buy
    support).

Phase 6I-19's brief is the thin adapter that surfaces
that data in the shape operators were previously using
the AI prompt for: top buy candidates, top short
candidates, low-buy candidates, multi-timeframe context
per row, and inverse-pair annotations when the
inspected set happens to include both sides of a known
inverse / leveraged-inverse pair.

The brief **does not** decide what to trade. It surfaces
the same evidence the AI prompt was being asked to
ingest, in a deterministic JSON shape with no
hallucination surface.

## 2. Public API

```python
from confluence_decision_brief import (
    evaluate_confluence_decision_brief,
    DecisionBriefReport,
    DecisionBriefRow,
    InverseConfirmationNote,
    KNOWN_INVERSE_PAIRS,
    MTF_BREADTH_DAILY_ONLY,
    MTF_BREADTH_MIXED,
    MTF_BREADTH_BROAD,
    MTF_BREADTH_NONE,
)

report: DecisionBriefReport = evaluate_confluence_decision_brief(
    tickers=["SPY", "QQQ", "SQQQ"],
    top_n=10,
    artifact_root=None,        # default = production
    cache_dir=None,
    stackbuilder_root=None,
    signal_library_dir=None,
    current_as_of_date=None,   # default = resolved cutoff
    ranking_callable=None,     # test injection point
    universe_discovery_callable=None,  # test injection point
)
```

`evaluate_confluence_decision_brief` accepts either
explicit `tickers=` or `from_stackbuilder_universe=True`
(discovers via `daily_board_universe_planner.discover_stackbuilder_universe`,
lazily imported only when needed). When both are
supplied, the explicit list wins.

`ranking_callable` defaults to
`confluence_ranking_emitter.emit_confluence_ranking`;
tests inject fakes via this seam.

## 3. CLI

```
python confluence_decision_brief.py --tickers SPY,QQQ,SQQQ --top-n 10
python confluence_decision_brief.py --from-stackbuilder-universe --top-n 10
python confluence_decision_brief.py --ticker SPY
```

Three ticker-source flags mutually exclusive
(`--ticker` / `--tickers` / `--from-stackbuilder-universe`).
Optional root flags: `--artifact-root`, `--cache-dir`,
`--stackbuilder-root`, `--signal-library-dir`,
`--current-as-of-date`, `--top-n`. JSON to stdout;
`rc=0` success; `rc=2` invalid args; `rc=3` unexpected;
no `SystemExit` leak.

## 4. Output JSON shape

```json
{
  "generated_at": "2026-05-13T...+00:00",
  "current_as_of_date": "2026-05-12",
  "inspected_count": 3,
  "top_n": 10,
  "top_positive_candidates": [...],
  "top_negative_candidates": [...],
  "low_buy_candidates": [...],
  "inverse_confirmation_notes": [...],
  "blocked_or_unrankable_summary": {
    "stale_confluence_day_artifact": 1
  },
  "blocked_or_unrankable_tickers": ["AAPL"],
  "missing_data_summary": {
    "missing_target_signal_engine_cache": 2
  },
  "remaining_limitations": [...]
}
```

Per-row shape (each entry in the three tail arrays):

```json
{
  "ticker": "SPY",
  "contract_valid": true,
  "rank_eligible": true,
  "issue_codes": [],
  "recommended_next_operator_action": "contract_valid_no_action",
  "ranking_blocked_reason": "",
  "confluence_last_date": "2026-05-08",

  /* Group A: signal-breadth / agreement (Phase 6I-3 pass-through) */
  "consensus_signal": "Buy",
  "consensus_signal_value": 1,
  "agreement_active": 21,
  "agreement_total": 60,
  "agreement_ratio": 0.35,
  "buy_votes": 20,
  "short_votes": 1,
  "none_votes": 39,
  "missing_votes": 0,
  "signed_vote_score": 0.3167,
  "timeframes": ["1d", "1wk", "1mo", "3mo", "1y"],
  "K_values": [1,2,3,4,5,6,7,8,9,10,11,12],

  /* Group B: performance-quality (Phase 6I-3 pass-through) */
  "total_capture_pct": 53.21,
  "avg_daily_capture_pct": 0.0612,
  "sharpe_ratio": 0.041,
  "trigger_days": 900,
  "wins": 470,
  "losses": 420,
  "p_value": 0.03,

  /* Phase 6I-19 derived MTF annotations */
  "mtf_breadth": "broad_multi_timeframe",
  "k_count": 12,
  "k_coverage_complete": true
}
```

Inverse-confirmation note shape:

```json
{
  "primary": "QQQ",
  "inverse": "SQQQ",
  "primary_consensus_signal": "Buy",
  "inverse_consensus_signal": "Short",
  "primary_agreement_ratio": 0.40,
  "inverse_agreement_ratio": 0.42,
  "note": "QQQ (consensus='Buy', agreement_ratio=0.40) and SQQQ (consensus='Short', agreement_ratio=0.42) are a known inverse / leveraged-inverse pair. Operator reads confirmation vs contradiction directly from the surfaced consensus signals and agreement ratios; this brief draws no conclusion."
}
```

## 5. Why multi-timeframe is the key upgrade

The Phase 6I-3 emitter aggregates ranking inputs across
all 12 K values for up to 5 timeframes (1d / 1wk / 1mo /
3mo / 1y; `expected_cell_count = 12 × 5 = 60`). Phase
6I-19's brief surfaces this directly on each per-row
output as three derived fields:

  - `mtf_breadth` ∈ {`daily_only`, `mixed`,
    `broad_multi_timeframe`, `none`}.
  - `k_count` — how many K values contributed.
  - `k_coverage_complete` — `true` only when the row's
    `K_values` exactly equals `{1, 2, ..., 12}`.

`broad_multi_timeframe` requires the row's
`timeframes` tuple to include at least 3 of the
canonical set `{1d, 1wk, 1mo, 3mo, 1y}`. A row that
fires only at the daily level is `daily_only`; a row
that fires across just two of the canonical
timeframes is `mixed`. This single field lets an
operator distinguish at a glance between a thin
daily-only consensus and a deep multi-timeframe
confluence — exactly the discrimination the old K=6
table could not provide.

## 6. Why both top and bottom tails matter

The brief carries **three** tails:

  - `top_positive_candidates` — strongest buy / long
    candidates (positive `signed_vote_score`; sorted
    favoring Buy consensus by Phase 6I-3 logic).
  - `top_negative_candidates` — strongest short / sell
    candidates (negative `signed_vote_score`; sorted
    favoring Short consensus by Phase 6I-3 logic).
  - `low_buy_candidates` — tickers with near-zero buy
    support (`buy_votes == 0` OR
    `buy_ratio <= 0.10` per the Phase 6I-3 contract).

The bottom of the ranking is **never hidden**. Two
operational reasons:

  1. **Short / sell candidacy.** Tickers with strong
     negative consensus + non-trivial agreement ratios
     are short candidates in their own right.
  2. **Inverse confirmation.** A short signal on the
     primary (e.g. SPY consensus=Short) is more
     credible when an inverse on the same underlying
     (e.g. SH consensus=Buy) also agrees. The brief's
     `inverse_confirmation_notes` (§ 7) surface this
     pairing automatically when both sides are in the
     inspected set.

The Phase 6I-3 emitter and Phase 6I-19 brief both
encode "both top and bottom matter" as a hard contract.

## 7. Inverse-confirmation annotations

A static, conservative mapping
(`KNOWN_INVERSE_PAIRS` in `confluence_decision_brief.py`)
covers the most common public ETF inverse / leveraged-
inverse relationships:

  - SPY ↔ SH, SDS, SPXU
  - QQQ ↔ PSQ, QID, SQQQ
  - IWM ↔ RWM, TWM, SRTY
  - DIA ↔ DOG, DXD, SDOW
  - TLT ↔ TBT, TMV

When both sides of a pair are present in the inspected
set AND both have ranking rows, the brief emits an
`InverseConfirmationNote` carrying the primary +
inverse's `consensus_signal` and `agreement_ratio`. The
note text spells out that the brief **draws no
conclusion** — confirmation vs. contradiction is the
operator's read.

When only one side is in the set, **no annotation is
emitted** — the brief does not guess a relationship
that can't be observed. Future expansion of the
mapping should add only well-documented inverse ETFs
or explicit operator-provided configs.

## 8. Exact ranking fields consumed (pass-through from Phase 6I-3)

Group A — signal-breadth / agreement (no transformation
beyond the verbatim pass-through):

  - `consensus_signal` (`"Buy"` / `"Short"` / `"None"`
    / `null` / `"unknown"`)
  - `consensus_signal_value` (1 / -1 / 0 / null)
  - `agreement_active` / `agreement_total` /
    `agreement_ratio`
  - `buy_votes` / `short_votes` / `none_votes` /
    `missing_votes`
  - `signed_vote_score`
  - `timeframes` / `K_values`

Group B — performance-quality (verbatim pass-through):

  - `total_capture_pct` / `avg_daily_capture_pct`
  - `sharpe_ratio`
  - `trigger_days` / `wins` / `losses`
  - `p_value`

Verdict / contract fields (verbatim pass-through):

  - `contract_valid` / `rank_eligible`
  - `issue_codes` / `recommended_next_operator_action`
    / `ranking_blocked_reason`
  - `confluence_last_date`

Phase 6I-19 derives **only three** additional fields
from the pass-through:

  - `mtf_breadth` from `timeframes`.
  - `k_count` from `K_values`.
  - `k_coverage_complete` from `K_values`.

It does NOT re-rank, re-weight, or re-aggregate the
Phase 6I-3 inputs.

## 9. What remains unfinished

`remaining_limitations` is emitted on the JSON output
verbatim from the module's `_DEFAULT_REMAINING_LIMITATIONS`
tuple. Six items as of Phase 6I-19:

  - **`real_confluence_pipeline_runner_write`** still
    open (closes on a future supervised run where
    `cache_date_range_end > resolved current_as_of_date`
    strictly).
  - **`real_post_pipeline_validation_on_writer_path`**
    still open (same future condition).
  - **Provider telemetry on writer stdout / JSONL /
    status JSON surfaces** still pending. Probe-surface
    captures landed in Phase 6I-16 and re-captured in
    Phase 6I-17; writer-surface captures await a future
    supervised writer run.
  - **Aggregate Confluence p_value across MTF** is NOT
    computed by this brief. Per-ticker p_value is
    passed through from the Phase 6I-3 emitter (sourced
    from the Confluence artifact's summary block) but
    not aggregated across the timeframe axis. A multi-
    timeframe aggregate p_value would need a multiple-
    comparisons correction (BH / Bonferroni per the
    Phase 5C-1 validation methodology) and is out of
    scope for this read-only adapter.
  - **Inverse-confirmation notes are pair annotations
    only.** The brief never concludes that an observed
    inverse signal confirms or contradicts the primary;
    that judgment stays with the operator.
  - **Read-only by contract.** Never invokes the
    writer, the refresher, the pipeline runner,
    yfinance, or any batch engine. To advance the
    evidence chain (e.g. populate writer-surface
    provider telemetry), follow the Phase 6I-18 next-
    probe handoff at
    `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md`.

## 10. Tests

`test_confluence_decision_brief.py` (19 tests):

  - Forbidden-imports static guard (no writer /
    refresher / pipeline runner / live engines /
    yfinance / dash / subprocess at top level).
  - Both top AND bottom tails surfaced (positive +
    negative + low-buy).
  - Group A + Group B fields pass through verbatim.
  - MTF-breadth classification: broad / daily_only /
    mixed / none.
  - K-coverage flag (`k_count` + `k_coverage_complete`).
  - Inverse-confirmation note fires when both sides
    present; omitted when one side missing; no
    duplicate when both sides have their own
    `KNOWN_INVERSE_PAIRS` entry.
  - Blocked-or-unrankable summary aggregates by
    `ranking_blocked_reason` (with `contract_invalid`
    fallback).
  - Missing-data summary aggregates `issue_codes`.
  - `remaining_limitations` names pipeline-runner-
    write / post-pipeline-validation / writer-surface
    telemetry / aggregate p_value gaps.
  - `to_json_dict()` round-trips through `json.dumps`.
  - CLI rc=0 / rc=2 / no `SystemExit` leak.

```
test_scripts/test_confluence_decision_brief.py                  19 passed in 0.95 s
Focused 4-way (brief + emitter + validator + universe planner):
                                                                100 passed in 10.84 s
```

`py_compile` clean on the new module + test file.
`git diff --check` clean.

## 11. Reference paths

  - Source-of-truth sprint state:
    `project/CLAUDE.md` § 6.
  - Phase 6I-18 next-probe handoff (current operator
    discipline + future writer-script trigger):
    `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md`
  - Phase 6I-17 SPY source-ready recheck (binding
    evidence of the current closed state):
    `project/md_library/shared/2026-05-13_PHASE_6I17_SPY_SOURCE_READY_RECHECK.md`
  - Phase 6I-3 Confluence ranking emitter (the
    upstream surface this brief consumes):
    `project/confluence_ranking_emitter.py`
  - Phase 6I-1 contract validator (called by the
    emitter):
    `project/confluence_ranking_contract_validator.py`
  - Phase 6I-5 universe planner (for
    `--from-stackbuilder-universe`):
    `project/daily_board_universe_planner.py`
  - This brief module:
    `project/confluence_decision_brief.py`
  - This brief's tests:
    `project/test_scripts/test_confluence_decision_brief.py`
