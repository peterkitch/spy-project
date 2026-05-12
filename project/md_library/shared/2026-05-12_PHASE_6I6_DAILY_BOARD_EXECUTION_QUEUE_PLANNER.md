# Phase 6I-6 — Daily Signal Board execution queue planner

**Status:** read-only planner module + tests + this doc.
No production data refreshed. No production code modified.
No writer invocation.

**Last updated:** 2026-05-12.

## 0. Scope statement

  - **No production writes.** No `cache/`, `output/`,
    `signal_library/`, or `stackbuilder/` byte changed.
  - **No source refresh. No yfinance fetch.**
  - **No engine execution.** OnePass, ImpactSearch,
    StackBuilder, TrafficFlow, Spymaster, Confluence
    runner are NOT imported.
  - **No writer import.** Phase 6H-5
    `daily_board_automation_writer` is NOT imported.
    The queue planner emits **advisory command
    strings only**; those strings are never executed
    by this module. The writer itself remains the
    only path that actually performs a refresh /
    pipeline write, and it still gates on its two-key
    authorization (`--write` + the
    `PRJCT9_AUTOMATION_WRITE_AUTH` env var).
  - **No subprocess. No Dash. No Spymaster edit.**
  - The planner consumes the Phase 6I-5 universe
    planner (which internally consults Phase 6I-4 /
    6H-3 / 6I-3 / 6I-1). Every layer is read-only;
    contracts carry forward.

## 1. Why this exists

Phase 6I-5 produced a per-ticker plan classified into
seven aggregate bucket lists. Operators still needed a
**bounded, queue-shaped preview** that says exactly:

  - Which tickers would be refreshed if the operator
    invokes the writer? (And in what order?)
  - Which would be pipelined?
  - Which are blocked, and why?
  - What advisory command would the operator paste
    next?

This phase replaces the old "copy/paste ticker batch
into Spymaster" step with a JSON queue contract that
Spymaster (or a future scheduler) can consume directly.

## 2. Public API

```python
from daily_board_execution_queue_planner import (
    ExecutionQueueItem,
    ExecutionQueueReport,
    ADVISORY_COMMAND_TEMPLATE,
    ALL_QUEUE_NAMES,
    QUEUE_NAME_PIPELINE_ONLY,
    QUEUE_NAME_REFRESH_SOURCE_CACHE_THEN_PIPELINE,
    QUEUE_NAME_WAIT_FOR_CACHE_AHEAD,
    QUEUE_NAME_MANUAL_STACKBUILDER,
    QUEUE_NAME_UPSTREAM_BLOCKED,
    QUEUE_NAME_DOWNSTREAM_GAP,
    QUEUE_NAME_CURRENT_LEADER_ELIGIBLE,
    build_execution_queue,
    main,
)

report = build_execution_queue(
    tickers=["SPY", "AAPL"],          # explicit, OR
    from_stackbuilder_universe=True,  # discover, OR both
    max_refresh=None,
    max_pipeline=None,
    include_blocked=True,
    top_n=10,
    cache_dir=None, artifact_root=None,
    stackbuilder_root=None, signal_library_dir=None,
    impactsearch_output_dir=None,
    current_as_of_date=None,
)
```

### CLI

```
python daily_board_execution_queue_planner.py --ticker SPY
python daily_board_execution_queue_planner.py --tickers SPY,AAPL,QQQ
python daily_board_execution_queue_planner.py \
    --from-stackbuilder-universe \
    --max-refresh 5 --max-pipeline 5 --top-n 3
```

Sizing / safety flags:

  - `--max-refresh N` truncates the
    `refresh_source_cache_then_pipeline_queue` to N
    items and sets `queue_truncation[<name>] = True`.
  - `--max-pipeline N` truncates the
    `pipeline_only_queue` similarly.
  - `--include-blocked` / `--no-include-blocked`
    (default `True`): when False, the four "blocked"
    queues (wait_for_cache_ahead / manual_stackbuilder /
    upstream_blocked / downstream_gap) are emitted as
    empty tuples so an operator focused on write-ready
    work sees a short report. The dataclass schema is
    unchanged.
  - `--top-n N` (default 10) passes through to the
    Phase 6I-5 universe planner for the three ranking
    tails.

JSON to stdout. `rc=0` success / `rc=2` invalid args
(no ticker source, unknown flag) / `rc=3` unexpected.
`SystemExit` is never propagated from `main()`.

**No default write authorization.** The planner only
plans; the writer itself still requires the two-key
gate.

## 3. Per-queued-item schema

`ExecutionQueueItem`:

  - `ticker` — upper-cased symbol.
  - `queue_name` — one of the seven `QUEUE_NAME_*`
    strings.
  - `recommended_action` — the Phase 6H-3 canonical
    automation action.
  - `advisory_command` — `"python
    daily_board_automation_writer.py --ticker <T>
    --write"` for the two write-ready queues; `None`
    otherwise.
  - `write_requires_env_var` — `True` for the two
    write-ready queues; `False` otherwise. Acts as a
    reminder that the writer itself still gates on
    `PRJCT9_AUTOMATION_WRITE_AUTH`.
  - `upstream_primary_blocker` — Phase 6I-5 sanitized
    upstream blocker (downstream-gap stripped).
  - `primary_blocker` — Phase 6I-5 composite blocker
    (upstream if non-empty; else
    `downstream_artifact_gap` when downstream invalid;
    else `""`).
  - `automation_blocking_reasons` — tuple of strings
    from the preflight.
  - `upstream_issue_codes` — tuple from the Phase 6I-4
    audit.
  - `cache_cutoff_action`, `source_cache_date` — from
    the preflight.
  - `downstream_contract_verdict` — from the Phase 6I-1
    validator (via 6I-4).
  - `current_leader_eligible` — preflight bool.
  - `ranking_blocked_reason` — from the Phase 6I-3
    ranking row (or `""` when no row).
  - Ranking surface (when available; `None` otherwise):
    `consensus_signal`, `agreement_ratio`,
    `signed_vote_score`, `total_capture_pct`,
    `sharpe_ratio`, `p_value`. **Not
    `agreement_ratio`-only.** Carries both signal-
    breadth and performance-quality fields per the
    Phase 6I-3 / 6I-5 contract.

## 4. Queue classification cascade

Each per-ticker state lands in **exactly one** queue.
The cascade order:

  1. **Upstream / input blockers.** If
     `upstream_primary_blocker` is non-empty:
     - StackBuilder-related blocker → `manual_stackbuilder_queue`:
         `upstream_trio_missing_stackbuilder_run`,
         `upstream_trio_ambiguous_stackbuilder_selection`,
         `upstream_trio_unreadable_stackbuilder_leaderboard`,
         `upstream_trio_insufficient_stackbuilder_k_coverage`,
         `upstream_trio_unparseable_stackbuilder_members`.
       (Saved-variant durability carried forward verbatim —
       these are operator selection / config problems,
       not staleness; **no 30-day stale window**.)
     - Otherwise (missing target / member cache,
       missing OnePass target / member library,
       missing OnePass target library) →
       `upstream_blocked_queue`.
  2. **Action-first routing** (the Phase 6H-3
     preflight already chose the canonical operator
     instruction):
     - `run_pipeline_only` → `pipeline_only_queue`.
     - `refresh_source_cache_then_pipeline` →
       `refresh_source_cache_then_pipeline_queue`.
     - `wait_for_cache_ahead_of_cutoff` →
       `wait_for_cache_ahead_queue`.
     - `no_action_already_current` →
       `current_leader_eligible_queue`.
  3. **Fallback** (action did not match the
     actionable set: `blocked_manual_review` or
     `refresh_multitimeframe_libraries_manual`):
     - `current_leader_eligible` flag still `True` →
       `current_leader_eligible_queue` (defensive).
     - `primary_blocker == "downstream_artifact_gap"`
       → `downstream_gap_queue`.
     - Otherwise → `upstream_blocked_queue`
       (operator-review catch-all).

**Action-first vs primary_blocker-first.** Action-first
is deliberate: when the preflight emits an actionable
verdict (`run_pipeline_only`, `refresh_*`, `wait_*`,
`no_action_*`), the operator should route on that
verdict. The composite `primary_blocker` remains on
the row for inspection. A ticker whose Confluence
chain has not been built yet but whose cache is fresh
enough to pipeline NOW carries `primary_blocker ==
"downstream_artifact_gap"` AND `action ==
"run_pipeline_only"` — the right routing is the
pipeline queue, not the downstream-gap queue.

## 5. Ordering / sort contract

  - **Operational queues preserve universe input
    order** (which is itself input-order from the
    operator's `--tickers` argument or alphabetical
    from `discover_stackbuilder_universe`).
  - **Refresh / pipeline queues** sort by:
    1. Upstream clean before upstream blocked
       (irrelevant within these queues — upstream-
       blocked rows are routed away — but pinned in
       the sort for defensive determinism).
    2. Ticker alphabetical.
  - **Pipeline_only is listed first in the dataclass**
    so the action-priority "`run_pipeline_only` before
    `refresh_source_cache_then_pipeline`" sequencing is
    observable in the report layout itself.
  - **`agreement_ratio` alone is NOT a sort key.**
    Ranking tails (positive / negative / low_buy) come
    through unchanged from Phase 6I-3 / 6I-5; both
    top and bottom tails matter; tails carry both
    signal-breadth and performance-quality fields;
    `p_value=None` preserved through JSON.

## 6. Aggregate report schema

`ExecutionQueueReport`:

  - `generated_at`, `current_as_of_date`,
    `inspected_count`,
    `discovered_stackbuilder_ticker_count`.
  - `max_refresh`, `max_pipeline`, `top_n`,
    `include_blocked` — echo of the inputs.
  - `queue_counts: dict[str, int]` — keyed by queue
    name; reflects post-truncation + post-
    include_blocked-suppression sizes.
  - `queue_truncation: dict[str, bool]` — `True` for
    the two write-ready queues when their cap trimmed
    rows; `False` for every other queue (none of them
    are truncated by this PR).
  - `selected_refresh_count`, `selected_pipeline_count`
    — emitted refresh / pipeline queue lengths.
  - Seven queue arrays (in the order listed in § 4):
    `pipeline_only_queue`,
    `refresh_source_cache_then_pipeline_queue`,
    `wait_for_cache_ahead_queue`,
    `manual_stackbuilder_queue`,
    `upstream_blocked_queue`,
    `downstream_gap_queue`,
    `current_leader_eligible_queue`.
  - Three ranking tails pass-through from Phase 6I-5
    (`positive_tail`, `negative_tail`, `low_buy_tail`)
    carrying full Phase 6I-3 row JSON.

## 7. StackBuilder durability contract carried forward

The planner inherits the Phase 6H-3 / 6I-4 / 6I-5
contract verbatim:

  - Saved variants are durable.
  - Multiple variants per ticker are first-class.
  - Tied newest-mtime is `ambiguous_tied_mtime` and
    routes to `manual_stackbuilder_queue` — an
    operator selection / config problem, **not
    staleness**.
  - **No age-based stale rule. No 30-day window. No
    `STACKBUILDER_AGE_DAYS` constant.** The planner
    does NOT introduce any new mtime threshold. The
    Phase 6I-4 audit's static guard against age
    substrings continues to enforce the contract at
    the audit layer.

## 8. Coupling

The planner imports exactly three project modules
(all read-only):

  - `daily_board_universe_planner` (Phase 6I-5) — the
    single dependency for per-ticker plan + ranking
    tails.
  - `daily_board_automation_preflight` (Phase 6H-3) —
    for the `RECOMMENDED_*` action string constants
    only (no inspection / runtime use; just compares
    state strings against canonical names).
  - `upstream_research_input_audit` (Phase 6I-4) —
    for the `BLOCKER_*` constant set the cascade
    keys on.

Forbidden-imports static guard test
(`test_queue_planner_has_no_forbidden_imports`) blocks
any future import whose top-level package matches:
`yfinance`, `dash`, `spymaster`, `onepass`,
`impactsearch`, `stackbuilder`, `trafficflow`,
`confluence`, `cross_ticker_confluence`,
`daily_signal_board`, `signal_engine_cache_refresher`,
`confluence_pipeline_runner`,
`daily_board_automation_writer`,
`daily_board_automation_executor`, `subprocess`.

## 9. Test coverage

`project/test_scripts/test_daily_board_execution_queue_planner.py`
ships 20 tests:

  1. Forbidden-imports static guard.
  2. Empty universe + no ticker source → empty queues.
  3. Explicit ticker list routes correctly.
  4. `--from-stackbuilder-universe` discovers + routes.
  5. `--max-refresh` truncates refresh queue + sets
     truncation flag; sort is ticker alphabetical;
     advisory commands + `write_requires_env_var=True`
     fire on the surviving items.
  6. `--max-pipeline` truncates pipeline queue + sets
     truncation flag.
  7. Blocked queues carry NO advisory command on any
     row.
  8. `wait_for_cache_ahead_queue` carries no command;
     `cache_cutoff_action = pipeline_output_lags_persist_skip`
     surfaced.
  9. Missing target cache → `upstream_blocked_queue`
     (NOT `downstream_gap_queue`; Phase 6I-5 Codex
     amendment carried forward).
  10. Upstream-clean + downstream-missing + cache==cutoff
      fixture → action-first routing places ticker in
      `wait_for_cache_ahead_queue`; row's `primary_blocker
      = "downstream_artifact_gap"` still surfaces for
      operator inspection.
  11. `downstream_gap_queue` catches the residual
      `blocked_manual_review` path (covered via direct
      `_classify_queue` exercise with a stub state so
      the test does not require a contrived on-disk
      catalogue-health construction).
  12. Leader-eligible / already-current routing.
  13. Ranking tails pass through unchanged (compared
      against the Phase 6I-5 universe planner's tails).
  14. `to_json_dict()` round-trips; queue counts match
      emitted array lengths.
  15. No-writes guard: `tmp_path` byte-identical
      before/after.
  16. `--no-include-blocked` suppresses the four
      blocked queues; counts reflect the suppression.
  17. CLI no ticker source → `rc=2`.
  18. CLI unknown flag → `rc=2`.
  19. CLI happy path emits valid JSON, `rc=0`.
  20. CLI `--no-include-blocked` flag echoes through
      to the report's `include_blocked = False`.

## 10. Validation captured at module land

  - `py_compile` clean on both new files.
  - `test_daily_board_execution_queue_planner.py`:
    20 passed in 3.43 s.
  - Focused 5-way (queue planner + universe planner +
    audit + emitter + preflight): 109 passed in
    10.30 s.
  - `git diff --check` clean (LF→CRLF normalization
    warnings only; identical to every other repo
    pattern).

Real-cache SPY smoke (production tree, read-only;
JSON to stdout only):

```
$ python daily_board_execution_queue_planner.py --ticker SPY

discovered: 248
inspected: 1
queue_counts:
  wait_for_cache_ahead_queue: 1
  (all others: 0)
wait_for_cache_ahead_queue:
  SPY  action=wait_for_cache_ahead_of_cutoff  advisory=None
```

Real-cache universe smoke (read-only):

```
$ python daily_board_execution_queue_planner.py \
    --from-stackbuilder-universe \
    --max-refresh 5 --max-pipeline 5 --top-n 3

discovered: 248
inspected: 248
queue_counts:
  pipeline_only_queue:                       0
  refresh_source_cache_then_pipeline_queue:  3
  wait_for_cache_ahead_queue:                1
  manual_stackbuilder_queue:                62
  upstream_blocked_queue:                  168
  downstream_gap_queue:                     14
  current_leader_eligible_queue:             0
queue_truncation: all False (no caps hit at 5)
selected_refresh_count: 3
selected_pipeline_count: 0
positive_tail length: 1
low_buy_tail length: 1
```

The bucket arithmetic is consistent:
`0 + 3 + 1 + 62 + 168 + 14 + 0 = 248` (every ticker in
exactly one queue). The 62 `manual_stackbuilder` rows
correspond to the Phase 6I-5
`upstream_trio_insufficient_stackbuilder_k_coverage`
population; the 168 `upstream_blocked` rows are the
158 + 8 + 2 cache / library gaps; the 14
`downstream_gap` rows are the `blocked_manual_review`
+ `refresh_multitimeframe_libraries_manual` catch-all
catchments; the 3 `refresh_source_cache_then_pipeline`
rows correspond to the canonical operator-write target
under the current cutoff. Compared to Phase 6I-5 raw
buckets (230 upstream-blocked, 17 downstream-gap), the
queue planner's action-first cascade reshuffles 62
StackBuilder-K rows into `manual_stackbuilder` and 3
cache-behind rows from `downstream_gap` into the
actionable `refresh` queue.

## 11. Confirmation no production writes were run

Four independent checks:

1. **Forbidden-imports static guard.** Module's top-
   level AST imports only the three read-only project
   modules listed in § 8.
2. **No-writes test.** Snapshots every file under
   `tmp_path` before and after `build_execution_queue`;
   asserts byte-identical state.
3. **Real-cache smoke uses default production roots
   read-only.** The only output is JSON to stdout. No
   file write.
4. **No writer invocation.** The planner emits
   advisory command strings only; those strings are
   never executed. The writer itself remains the only
   path that performs a write, and it still gates on
   the two-key authorization (`--write` flag +
   `PRJCT9_AUTOMATION_WRITE_AUTH` env var).

## 12. Future Spymaster integration

A future Spymaster master-audit UI surface will
consume this planner's JSON. Integration path:

  - Spymaster invokes `build_execution_queue(...)` via
    in-process import (no subprocess) and renders:
    - Seven queue panels keyed by queue_name.
    - Per-item rows with the advisory command + a
      "Run via writer" button that the operator must
      click to dispatch (button click invokes the
      Phase 6H-5 writer with the two-key gate).
    - Three ranking tails as collapsible panels.
    - Leader-eligible spotlight.
  - The Spymaster surface remains read-only by
    default. Write dispatch always routes through the
    Phase 6H-5 writer with the two-key gate; the queue
    planner does NOT bypass that gate.

**Out of scope for this PR.** This doc names the
contract; the UI work is a later UI-layer change.

## 13. Scope cuts (deliberate)

  - **No file output.** JSON to stdout only.
  - **No writer invocation.** Advisory command
    strings only.
  - **No default write authorization.** The two-key
    gate on the writer is untouched.
  - **No Spymaster UI integration in this PR.**
  - **No new StackBuilder age window.**
  - **No truncation on non-write queues.** Only the
    refresh / pipeline queues honor `--max-*`. The
    other queues are emitted in full so the operator
    can audit the universe.
  - **No aggregate p-value yet.** The field passes
    through `p_value = None` from the Phase 6I-5
    plan; aggregate cross-K/timeframe p-value remains
    a named future gap (Phase 6I-2 § 4.3).

## 14. Reference paths

### New module + tests + doc (this PR)

  - `project/daily_board_execution_queue_planner.py`
    (new).
  - `project/test_scripts/test_daily_board_execution_queue_planner.py`
    (20 tests).
  - `project/md_library/shared/2026-05-12_PHASE_6I6_DAILY_BOARD_EXECUTION_QUEUE_PLANNER.md`
    (this doc).

### Modules consumed (read-only)

  - `project/daily_board_universe_planner.py`
    (Phase 6I-5; the single planning dependency).
  - `project/daily_board_automation_preflight.py`
    (Phase 6H-3; `RECOMMENDED_*` action strings only).
  - `project/upstream_research_input_audit.py`
    (Phase 6I-4; `BLOCKER_*` constants only).

### Cross-references

  - Phase 6I-5 universe planner:
    `project/md_library/shared/2026-05-12_PHASE_6I5_DAILY_BOARD_UNIVERSE_PLANNER.md`.
  - Phase 6H-7 production runbook:
    `project/md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`
    (the operator stack the queue planner is meant
    to sit alongside as the queue-shaped JSON
    backend).
