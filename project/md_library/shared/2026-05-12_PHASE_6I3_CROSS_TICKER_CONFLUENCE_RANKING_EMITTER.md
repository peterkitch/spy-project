# Phase 6I-3 — Cross-ticker Confluence ranking emitter

**Status:** read-only emitter module + tests + this doc.
No production data refreshed. No production code modified.

**Last updated:** 2026-05-12.

## 0. Scope statement

  - **No production writes.** No `cache/`, `output/`,
    `signal_library/`, or `stackbuilder/` byte changed.
  - **No source refresh.** No yfinance fetch.
  - **No Phase 6D pipeline write.**
  - **No StackBuilder run, no OnePass run, no Spymaster
    batch processing.**
  - No `subprocess`. No Dash. No live engine import.
  - The emitter is a structured-output successor to the
    Phase 6I-2 migration map's named gap (§ 6.2 of
    `2026-05-12_PHASE_6I2_MANUAL_WORKFLOW_MIGRATION_MAP.md`).
    It does NOT replace the operator's interpretation of
    the ranking — it ships the structured table.

## 1. Why this exists

The pre-Phase-6 manual workflow ended with: "paste the
single-K TrafficFlow K=6 metrics table into an external
AI assistant and ask it to highlight market-wide
opportunities, weighting higher Sharpe, lower p-values,
capture quality, trigger counts, etc."

That step had three load-bearing problems:

  1. **Single-K only.** The TrafficFlow table ranked
     combo rows inside ONE K (e.g. K=6). The multi-K /
     multi-timeframe Confluence layer (Phase 6D-3) is
     ignored.
  2. **Two ranking inputs collapsed into one.** Phase
     6I-2 § 4.2 / § 4.3 spell out that *signal breadth*
     (how many K × timeframe cells agree?) and
     *performance quality* (Sharpe / p-value / capture)
     are orthogonal ranking inputs. The AI paste step
     weighted them together opaquely; an
     `agreement_ratio`-only successor would lose
     performance quality entirely.
  3. **One tail only.** The AI summarization step
     surfaced "top opportunities" implicitly favoring
     long candidates. A QQQ-vs-SQQQ-style inverse-
     confirmation read (QQQ Buy-heavy, SQQQ Short-heavy
     or low-buy) requires the *bottom* of the ranking
     too.

Phase 6I-3 closes all three:

  - **Multi-timeframe is load-bearing.** Each row carries
    `K_values = [1, 2, ..., 12]` × `timeframes = ["1d",
    "1wk", "1mo", "3mo", "1y"]` → `expected_cell_count =
    60`.
  - **Both ranking input groups are exposed per row.**
    Signal-breadth fields (`agreement_ratio`,
    `signed_vote_score`, vote ratios) AND performance-
    quality fields (`total_capture_pct`, `sharpe_ratio`,
    `trigger_days`, `wins`, `losses`, `p_value`).
  - **Three tails, not one.** `positive_tail`,
    `negative_tail`, AND `low_buy_tail`.

## 2. Public API

```python
from confluence_ranking_emitter import (
    ConfluenceRankingRow,
    ConfluenceRankingReport,
    LOW_BUY_RATIO_THRESHOLD,
    emit_confluence_ranking,
    main,
)

report = emit_confluence_ranking(
    ["SPY", "AAPL", "QQQ", "SQQQ"],
    artifact_root=None,        # production default
    cache_dir=None,            # production default
    stackbuilder_root=None,    # production default
    signal_library_dir=None,   # production default
    current_as_of_date=None,   # resolves to UTC today
    top_n=10,
)

print(report.positive_tail)
print(report.negative_tail)
print(report.low_buy_tail)
```

### CLI

```
python confluence_ranking_emitter.py \
    --tickers SPY,AAPL,QQQ,SQQQ \
    --top-n 10
```

Emits a JSON-serialized `ConfluenceRankingReport` to
stdout. Exit codes:

  - `0` ranking emitted.
  - `2` invalid CLI arguments (no tickers, unknown flag).
  - `3` unexpected unhandled exception.

`SystemExit` is never propagated from `main()`; argparse
errors are converted to `rc=2`.

## 3. Row schema

Each `ConfluenceRankingRow` carries:

  - **Contract verdict (from Phase 6I-1 validator):**
    `ticker`, `contract_valid`, `issue_codes`,
    `recommended_next_operator_action`, `rank_eligible`,
    `ranking_blocked_reason`, `confluence_last_date`.
  - **Signal-breadth fields (from `board_row_preview` +
    Confluence last daily row):**
    `consensus_signal`, `consensus_signal_value`,
    `agreement_active`, `agreement_total`,
    `agreement_ratio`, `buy_votes`, `short_votes`,
    `none_votes`, `missing_votes`, `active_count`,
    `available_count`, `buy_ratio`, `short_ratio`,
    `none_ratio`, `missing_ratio`, `signed_vote_score`,
    `zero_buy_flag`, `timeframes`, `K_values`,
    `expected_cell_count`.
  - **Performance-quality fields (from Confluence
    artifact's `summary` block):**
    `total_capture_pct`, `avg_daily_capture_pct`,
    `sharpe_ratio`, `trigger_days`, `wins`, `losses`,
    `p_value`.

### Derived fields

  - `buy_ratio = buy_votes / available_count` (None
    when `available_count` is 0 or missing).
  - `short_ratio = short_votes / available_count`.
  - `none_ratio = none_votes / available_count`.
  - `missing_ratio = missing_votes / expected_cell_count`
    (missing cells are excluded from `available_count`
    by definition; the denominator here is
    `expected_cell_count`, not `available_count`).
  - `signed_vote_score = (buy_votes - short_votes) /
    available_count`. Range `[-1.0, +1.0]`. Positive =
    net Buy pressure; negative = net Short pressure.
  - `zero_buy_flag = (buy_votes == 0)`. Surfaces the
    "no-long-support" case independent of `signed_vote_score`.

### p_value handling

The Confluence summary block currently emits `p_value =
None` (Phase 6D-3 emits the shape but does not yet
aggregate per-K/timeframe p-values into a Confluence-
level p-value). Phase 6I-2 § 4.3 documented this as an
explicit future gap. The emitter consumes `p_value`
**defensively**: sort keys handle `None` without
raising, and the JSON serialization preserves `None`.
The field is present in the row schema so that, when
aggregate p-value lands in a future phase, the emitter
already exposes it.

## 4. Sort contract

Each tail's sort key is documented in
`confluence_ranking_emitter._positive_sort_key /
_negative_sort_key / _low_buy_sort_key` and is also
spelled out here so the operator can audit the ranking
rationale without reading code.

### 4.1 `positive_tail` (Buy-leaning candidates)

Filter: `contract_valid AND signed_vote_score > 0`.

Sort priority (lexicographic ascending tuple; missing
values rank worst):

  1. `signed_vote_score` descending — positive net
     pressure first.
  2. `consensus_signal == "Buy"` first (rows whose
     strict-unanimity confluence_signal is Buy beat
     rows with the same signed score but a None /
     mixed consensus).
  3. `agreement_ratio` descending — stronger breadth.
  4. `total_capture_pct` descending — stronger
     performance.
  5. `sharpe_ratio` descending.
  6. `ticker` ascending — deterministic tie-break.

### 4.2 `negative_tail` (Short-leaning candidates)

Filter: `contract_valid AND signed_vote_score < 0`.

Sort priority:

  1. `signed_vote_score` ascending — most-negative net
     pressure first.
  2. `consensus_signal == "Short"` first.
  3. `buy_ratio` ascending — low Buy ratio = stronger
     short candidate.
  4. `short_ratio` descending — more Shorts = stronger
     evidence.
  5. `total_capture_pct` descending.
  6. `ticker` ascending — deterministic tie-break.

### 4.3 `low_buy_tail` (no-long-support candidates)

Filter: `contract_valid AND (buy_votes == 0 OR buy_ratio
<= LOW_BUY_RATIO_THRESHOLD)`. `LOW_BUY_RATIO_THRESHOLD =
0.10` (very low; roughly "10% or fewer of voted cells
favor Buy"). The constant is exposed as a module-level
attribute so the threshold can be audited and, if a
future product decision needs to adjust it, the change is
a single line.

Sort priority:

  1. `buy_ratio` ascending — zero / lowest first.
  2. `short_ratio` descending — stronger Short signal.
  3. `none_ratio - missing_ratio` descending — prefer
     rows where the absence is *voted* (high
     `none_ratio`) rather than a data gap (high
     `missing_ratio`).
  4. `ticker` ascending — deterministic tie-break.

### 4.4 Why both tails

A ticker can appear in **both** `positive_tail` AND
`low_buy_tail` simultaneously. The real-cache SPY smoke
illustrates this: SPY's vote shape today is `buy=5,
short=2, none=53` (60 cells), giving
`signed_vote_score=0.05` and `buy_ratio=0.083`. SPY
appears in `positive_tail` (net Buy, however slim) AND
in `low_buy_tail` (≤ 10% of voted cells favor Buy). The
tails are not mutually exclusive by design — a ticker
that is *technically* net-positive but with very few Buy
cells deserves to surface in both contexts, and the
operator's interpretation (or downstream AI consumer's
interpretation) can compare them.

## 5. Report schema

`ConfluenceRankingReport` carries:

  - `generated_at` — UTC ISO-8601 timestamp.
  - `current_as_of_date` — the resolved cutoff (mirrors
    the Phase 6C-8 readiness layer's convention).
  - `inspected_count` — number of rows in `rows`.
  - `tickers` — tuple of inputs (upper-cased, blanks
    stripped).
  - `top_n` — clamp value applied to each tail (`max(0,
    --top-n)`).
  - `rows` — every inspected ticker, in input order.
    Includes contract-invalid rows so they remain
    auditable.
  - `positive_tail` / `negative_tail` / `low_buy_tail` —
    sorted, sliced to `top_n`.
  - `counts_by_contract_validity` — `{"valid": N,
    "invalid": M}`.
  - `counts_by_consensus_signal` — `{"Buy": ..., "Short":
    ..., "None": ..., "unknown": ...}`.

`to_json_dict()` returns a fully JSON-serializable dict.

## 6. Coupling

The emitter imports exactly two modules from the project:

  - `confluence_ranking_contract_validator` (Phase 6I-1)
    — per-ticker contract validation, board_row_preview
    derivation, and the read-only artifact loader the
    emitter mirrors for last-row + summary extraction.
  - `confluence_pipeline_readiness` (Phase 6C-8) —
    cutoff-date resolution helper (`resolve_current_as_of_date`),
    nothing else.

Static guard test
(`test_emitter_has_no_forbidden_imports`) blocks any
import whose top-level package matches:

  - `yfinance`, `dash`
  - `trafficflow`, `spymaster`, `impactsearch`, `onepass`,
    `stackbuilder`
  - `daily_signal_board`
  - `signal_engine_cache_refresher` (Phase 6E-5)
  - `confluence_pipeline_runner` (Phase 6D-4)
  - `daily_board_automation_writer` (Phase 6H-5)
  - `subprocess`

A future PR that tries to wire the emitter into a writer
or a fetch path fails this test on first run.

## 7. Test coverage

`project/test_scripts/test_confluence_ranking_emitter.py`
ships 17 tests across the following surfaces:

  1. Forbidden-imports static guard.
  2. Full valid 12 × 5 fixture → `expected_cell_count =
     60` and rank_eligible=True.
  3. Positive-tail ordering: BUYHI > BUYMD > BUYLO.
  4. Negative-tail ordering: SQQQ (Short-heavy) above
     MIXED (small net negative); QQQ in positive tail
     simultaneously.
  5. Low-buy tail surfaces buy_votes=0 row whose
     `consensus_signal == "None"` (the QQQ-vs-SQQQ-style
     inverse-confirmation pattern).
  6. `p_value = None` does not crash sort.
  7. Deterministic tie-break: three identical fixtures
     emit `["AAAA", "BBBB", "CCCC"]` in alpha order.
  8. Contract-invalid row appears in `rows` with its
     issue codes; excluded from every tail.
  9. CLI: blank ticker → `rc=2`.
  10. CLI: no arg → `rc=2`.
  11. CLI: unknown flag → `rc=2` (no `SystemExit` leak).
  12. No-writes guard: `tmp_path` byte-identical before
      and after.
  13. `top_n` clamp: 5 rows + top_n=2 → tail length 2,
      rows still 5.
  14. `top_n=0` → empty tails, rows still emitted.
  15. `counts_by_consensus_signal` correct across mixed
      fixture.
  16. CLI happy path emits valid JSON, rc=0,
      `expected_cell_count=60`.
  17. `to_json_dict()` round-trips through `json.dumps /
      loads` with `p_value=None` preserved as `null`.

## 8. Validation captured at module land

  - `py_compile` clean on both new files.
  - `test_confluence_ranking_emitter.py`: 17 passed in
    3.15 s.
  - Focused validator + emitter suite: 59 passed in
    5.62 s (42 validator carried forward + 17 new).
  - `git diff --check` clean (LF→CRLF normalization
    warnings only; identical to every other repo file
    pattern).

Real-cache SPY smoke (production artifact tree, read-
only):

```
$ python confluence_ranking_emitter.py --ticker SPY --top-n 5

rows[0]:
  ticker                     SPY
  contract_valid             true
  rank_eligible              false
  ranking_blocked_reason     "stale_confluence_day_artifact"
  confluence_last_date       "2026-05-08"
  consensus_signal           "None"
  agreement_active           7
  agreement_total            60
  agreement_ratio            0.1166...
  buy_votes                  5
  short_votes                2
  none_votes                 53
  missing_votes              0
  active_count               7
  available_count            60
  buy_ratio                  0.0833...
  short_ratio                0.0333...
  none_ratio                 0.8833...
  signed_vote_score          0.05
  zero_buy_flag              false
  expected_cell_count        60
  total_capture_pct          42.44
  avg_daily_capture_pct      0.0488
  sharpe_ratio               0.0342
  trigger_days               870
  wins                       437
  losses                     418
  p_value                    null

positive_tail = ["SPY"]
negative_tail = []
low_buy_tail  = ["SPY"]
counts_by_contract_validity = {valid: 1, invalid: 0}
counts_by_consensus_signal  = {Buy: 0, Short: 0, None: 1, unknown: 0}
```

SPY's slim net-Buy posture (5 buy, 2 short, 53 none)
surfaces in **both** `positive_tail` (signed > 0) and
`low_buy_tail` (`buy_ratio = 0.083 <= 0.10`). The
operator sees the dual classification and decides
whether the thin Buy edge is meaningful or whether the
overwhelming None vote dominates.

`rank_eligible = false` is the documented persist-skip-
lag verdict (unpinned cutoff 2026-05-11, Confluence
last_date 2026-05-08). The emitter does NOT filter on
`rank_eligible`; it surfaces both the contract verdict
and the ranking inputs so the operator can decide
whether to interpret a contract-valid-but-not-leader
row.

## 9. Confirmation no production writes were run

  - **Forbidden-imports static guard.** Module's top-
    level AST does not import any writer / refresher /
    pipeline runner / live engine.
  - **No-writes test.** Snapshots every file under a
    `tmp_path` before and after `emit_confluence_ranking`;
    asserts byte-identical state.
  - **Real-cache smoke uses default production roots
    read-only.** The only outputs are JSON to stdout. No
    file write occurs.
  - **Tests use `tmp_path` fixtures exclusively** for
    any write path. The real refresher / runner /
    StackBuilder / OnePass are never invoked.

## 10. What this does NOT do (deliberate scope cuts)

  - **No "top 3 moves" opinionated text.** The emitter
    outputs structured data only. Interpretation
    (whether to take a position, how to size it, whether
    the negative tail's inverse-confirmation pattern is
    actionable) is downstream of this module.
  - **No universe discovery.** The emitter requires an
    explicit ticker list; it does NOT walk
    `cache/results/`, the StackBuilder leaderboard tree,
    or any other source to enumerate tickers. Phase
    6I-4 (Phase 6I-2 § 6.1) is the read-only universe-
    coverage replacement and remains the next gap.
  - **No execution.** The emitter never invokes the
    Phase 6H-5 writer or any refresher. It is a pure
    reader of saved-research artifacts.
  - **No aggregate p-value yet.** `p_value` is a pass-
    through from the Confluence summary block, which
    currently emits `None`. Aggregate cross-K/timeframe
    Confluence p-value is the future gap named in
    Phase 6I-2 § 4.3.
  - **No scheduler / no automation hookup.** This
    emitter is meant to be invoked by an operator (or a
    future scheduler) read-only between authorized
    writer runs.

## 11. Proposed downstream phases (named, not implemented here)

  - **Phase 6I-4** — read-only universe-coverage report
    (walks StackBuilder leaderboards; emits a structured
    pilot ticker list the emitter can be fed).
  - **Phase 6I-5** — execution-log audit dashboard
    (reads Phase 6H-5 writer's JSONL).
  - **Phase 6I-6** — explicit StackBuilder selection
    contract (no age window).
  - **Aggregate Confluence p-value** (no phase letter
    yet) — the explicit future gap from Phase 6I-2 §
    4.3 / § 6.2. When it lands, the emitter's
    `p_value` field is already wired.

## 12. Reference paths

### New module + tests + doc (this PR)

  - `project/confluence_ranking_emitter.py` (new module).
  - `project/test_scripts/test_confluence_ranking_emitter.py`
    (17 tests).
  - `project/md_library/shared/2026-05-12_PHASE_6I3_CROSS_TICKER_CONFLUENCE_RANKING_EMITTER.md`
    (this doc).

### Modules consumed (read-only)

  - `project/confluence_ranking_contract_validator.py`
    (Phase 6I-1).
  - `project/confluence_pipeline_readiness.py`
    (Phase 6C-8 — `resolve_current_as_of_date` only).

### Cross-references

  - Phase 6I-2 migration map:
    `project/md_library/shared/2026-05-12_PHASE_6I2_MANUAL_WORKFLOW_MIGRATION_MAP.md`
    (§ 4.2 / § 4.3 / § 6.2 — the "two ranking inputs,
    not one" framing and the cross-ticker emitter gap
    description this module closes).
  - Phase 6I-1 validator:
    `project/md_library/shared/2026-05-12_PHASE_6I1_CONFLUENCE_RANKING_CONTRACT_VALIDATOR.md`
    (the seven contract checks and the board_row_preview
    contract this emitter consumes).
  - Phase 6H-7 production runbook:
    `project/md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`
    (the operator stack the emitter is meant to sit
    alongside as a read-only ranking surface).
