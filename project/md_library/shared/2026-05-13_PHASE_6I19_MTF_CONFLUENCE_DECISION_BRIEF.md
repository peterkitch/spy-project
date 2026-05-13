# 2026-05-13 — Phase 6I-19: multi-timeframe Confluence decision brief

## 0. Scope

A new read-only module `confluence_decision_brief.py`
(+ CLI + tests + this doc) that **adapts** the existing
Phase 6I-3 ranking emitter / Phase 6I-5 universe planner
outputs into a structured operator-facing brief. The
brief consumes whatever those upstream surfaces already
produce — it does **not** rebuild ranking math, and it
does **not** build the still-missing TrafficFlow-style
multi-window K engine (§ 1 and § 9 below). It is a
**presentation adapter**, not a replacement for any
runtime engine.

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

## 1. The old manual workflow and what's still missing

### 1.1 The actual legacy flow (operator-confirmed)

The daily decision workflow operators were running to
ask "what's our best buy / short candidate today?" was:

  1. delete cached PKLs;
  2. open TrafficFlow and let it surface a missing-PKL
     list;
  3. run the Spymaster batch process to refill those
     PKLs;
  4. return to TrafficFlow;
  5. enter a K value (e.g. `K=6`);
  6. export / inspect that single daily K table;
  7. paste the table into an AI prompt and ask for a
     pattern read / ranking / confidence call before
     the next market close.

### 1.2 The key limitation of that flow

The K table TrafficFlow exported at step 6 was
**single-window**: it was a daily / next-24-hour
ranking only. The pattern read at step 7 had no
multi-window context — the operator could not see at a
glance whether a candidate that looked strong at K=6
daily was also strong at K=6 weekly, monthly, quarterly,
or yearly. The exported table simply didn't carry that
information.

### 1.3 The long-term target — NOT YET BUILT

The future-work goal is a TrafficFlow-style
**multi-window** engine that, for each StackBuilder K
build, evaluates K behavior across the five canonical
windows `1d / 1wk / 1mo / 3mo / 1y` and writes the
resulting artifacts so Confluence can display whether
*every* ticker in a build is firing across *every*
available window. The operator's North Star phrasing
is: "look at the Confluence view and say *wow, this
whole build is aligned across windows.*"

**That engine does not exist in this repo yet.** It is
the load-bearing future-work item that this Phase
6I-19 PR explicitly does NOT build (see § 9 below).

### 1.4 What Phase 6I-19's brief actually is

This module is a **read-only presentation adapter** on
top of the existing Phase 6I-3 ranking emitter / Phase
6I-5 universe planner. It consumes whatever those
upstream surfaces already produce — including whatever
`timeframes` / `K_values` tuples those surfaces already
contain — and emits a structured JSON brief that
arranges them by tail (positive / negative / low-buy),
adds three small presentation annotations per row
(`mtf_breadth`, `k_count`, `k_coverage_complete`), and
attaches inverse-pair annotations when the inspected
set happens to include both sides of a known inverse /
leveraged-inverse pair.

**The brief does NOT:**

  - generate TrafficFlow-style K metrics across the
    five canonical windows;
  - create or populate any missing multi-timeframe
    artifacts;
  - replace TrafficFlow, StackBuilder, Spymaster, or
    Confluence as runtime engines;
  - decide what to trade;
  - close any of the Phase 6I-16 / 6I-17 evidence gaps
    (real_confluence_pipeline_runner_write /
    real_post_pipeline_validation_on_writer_path /
    writer-surface provider telemetry).

If the upstream artifacts for a ticker do not yet
contain multi-timeframe K data, the brief reflects
that absence honestly: `timeframes` may be daily-only
or empty, `mtf_breadth` will be `daily_only` or
`none`, and `k_coverage_complete` may be `False`. The
brief surfaces the absence; it does not manufacture
data to fill it.

### 1.5 What the brief replaces — and what it does NOT replace

The brief replaces **step 7 of the legacy flow** (the
"paste a daily K table into an AI prompt and ask for a
ranking / pattern read" step) with a deterministic
JSON output shape that has no hallucination surface.
It does this against whatever data already exists
upstream — including, in the multi-window case,
whatever the upstream artifacts already carry.

The brief does **not** replace steps 1–6 of the
legacy flow (the deliberate cache invalidation +
TrafficFlow missing-PKL list + Spymaster batch
process + K-table export). Those steps are still the
runtime path that produces the underlying data, and
none of them have been superseded by this PR. A future
phase that builds the missing multi-window engine
(§ 1.3) is what would eventually displace them.

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

## 5. Multi-timeframe annotations (presentation only)

**Important:** the brief does NOT generate
multi-timeframe data. It surfaces whatever the
upstream artifacts already contain. If the upstream
artifacts for a given ticker only carry a daily
window, the brief faithfully reports `daily_only`; it
does not invent the missing weekly / monthly /
quarterly / yearly columns. Building the engine that
*would* populate those columns is the load-bearing
future-work item in § 1.3 / § 9.

What the Phase 6I-3 emitter already exposes per row:

  - `timeframes` — a tuple naming whichever windows
    are present in the upstream artifact. The
    canonical aspiration is `(1d, 1wk, 1mo, 3mo, 1y)`
    but the actual contents depend entirely on what
    the upstream pipeline / TrafficFlow path wrote.
  - `K_values` — a tuple of K values the upstream
    artifact carries (aspiration: `K=1..12`).
  - `expected_cell_count` — the implied 12 × 5 = 60
    alignment cell count when full MTF coverage is
    present.

What Phase 6I-19 adds (presentation annotations only):

  - `mtf_breadth ∈ {none, daily_only, mixed, broad_multi_timeframe}`
    classifies the row's `timeframes` tuple against
    the canonical set:
      - `none` — empty / no overlap.
      - `daily_only` — exactly `{"1d"}`.
      - `broad_multi_timeframe` — 3 or more of the
        canonical set are present.
      - `mixed` — anything in between.
  - `k_count` — `len(K_values)`.
  - `k_coverage_complete` — `True` only when
    `set(K_values) == {1, 2, ..., 12}`.

These three derived fields are the brief's contribution
to the multi-window surface. They are **observation
labels on existing upstream data**, not new MTF data.
When the upstream artifacts lack MTF coverage today
(the common case until the missing engine described in
§ 1.3 lands), the brief will faithfully label each row
`daily_only` (or `none`), and the operator will see at
a glance that the multi-window picture is not yet
populated.

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
tuple. **Seven** items as of the Phase 6I-19 review
amendment, with the load-bearing missing-engine item
named first:

  - **True TrafficFlow-style multi-window K evaluation
    is NOT built by this brief.** Each StackBuilder K
    build still needs a future engine / path that
    evaluates and writes `1d / 1wk / 1mo / 3mo / 1y`
    artifacts so Confluence can display whether every
    ticker in a build is firing across every available
    window. This brief is a presentation adapter — it
    surfaces the existing `timeframes` / `K_values`
    tuples if and only if upstream artifacts already
    contain them, and it never creates the missing MTF
    data. See § 1.3 for the long-term target framing.
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
