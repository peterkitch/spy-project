# Phase 6I-9 — Read-only supervised production-run readiness gate

**Status:** read-only gate module + tests + this doc.
**Not** a production-authorization phase: the existing
Phase 6H-5 two-key writer gate is unchanged.

**Last updated:** 2026-05-12.

## 0. Scope statement

  - **No production writes.** No `cache/`, `output/`,
    `signal_library/`, or `stackbuilder/` byte changed.
  - **No writer invocation.** The Phase 6H-5
    `daily_board_automation_writer` is NOT imported by
    this module.
  - **No source refresh / pipeline write / yfinance
    fetch.**
  - **No StackBuilder / OnePass / ImpactSearch /
    TrafficFlow / Spymaster batch execution.**
  - **No subprocess.**
  - **StackBuilder policy unchanged:** saved variants
    durable, multiple per ticker first-class, tied
    newest-mtime blocks, **no age window**.
  - **The gate is a JSON producer.** Output goes to
    stdout. No file is written.

## 1. Purpose

A single operator-facing read-only command that
answers exactly one question:

> "Is it safe to authorize the guarded writer right
> now, for which tickers, and why or why not?"

The gate consolidates the existing Phase 6H / 6I
read-only stack into one composite verdict:

  - `safe_to_authorize_writer_now: bool`
  - `recommended_operator_action: str` (a stable
    enum-like constant)
  - per-bucket ticker lists matching the Phase 6I-6
    queue planner's seven queues
  - the queue planner's advisory writer commands as
    **display-only strings**
  - the Phase 6I-3 ranking tails (both top and bottom
    matter, per Phase 6I-7 contract)
  - explicit `blocking_reasons` so the operator sees
    every concurrent gap, not only the dominant one

The gate consumes only `daily_board_execution_queue_planner.build_execution_queue`
(Phase 6I-6). One call. Everything else is summary +
cascade.

## 2. Public API

```python
from daily_board_supervised_run_gate import (
    SupervisedRunGateReport,
    evaluate_supervised_run_gate,
    main,
    ACTION_AUTHORIZE_GUARDED_WRITER,
    ACTION_WAIT_FOR_CACHE_AHEAD,
    ACTION_RESOLVE_STACKBUILDER_INPUTS,
    ACTION_FIX_UPSTREAM_INPUTS,
    ACTION_BUILD_MISSING_DOWNSTREAM_ARTIFACTS,
    ACTION_ALREADY_CURRENT,
    ACTION_MANUAL_REVIEW,
    BLOCKING_WRITE_READY_QUEUE_TRUNCATED,
    BLOCKING_WAITING_FOR_CACHE_AHEAD_OF_CUTOFF,
    BLOCKING_STACKBUILDER_SELECTION_OR_INPUTS_MANUAL,
    BLOCKING_UPSTREAM_INPUTS_BLOCKED,
    BLOCKING_DOWNSTREAM_ARTIFACTS_MISSING,
    BLOCKING_CONTRACT_INVALID_OR_UNKNOWN,
    BLOCKING_NO_INSPECTED_TICKERS,
)

report = evaluate_supervised_run_gate(
    tickers=["SPY"],
    from_stackbuilder_universe=False,
    max_refresh=None,
    max_pipeline=None,
    top_n=10,
    cache_dir=None, artifact_root=None,
    stackbuilder_root=None, signal_library_dir=None,
    impactsearch_output_dir=None,
    current_as_of_date=None,
)
```

### CLI

```
python daily_board_supervised_run_gate.py --ticker SPY
python daily_board_supervised_run_gate.py --tickers SPY,AAPL,QQQ
python daily_board_supervised_run_gate.py \
    --from-stackbuilder-universe \
    --max-refresh 5 --max-pipeline 5 --top-n 3
```

Three ticker-source flags are mutually exclusive. JSON
to stdout. `rc=0` success / `rc=2` invalid args /
`rc=3` unexpected. `SystemExit` never propagated from
`main()`.

### Test-time injection

`evaluate_supervised_run_gate` accepts an optional
`queue_planner_callable=None` keyword so the test suite
can substitute a fake queue planner. The CLI never uses
the injection point.

## 3. Report schema

`SupervisedRunGateReport`:

  - `generated_at` — UTC ISO-8601 timestamp.
  - `current_as_of_date` — resolved cutoff (echoed from
    the queue planner).
  - `inspected_count`, `discovered_stackbuilder_ticker_count`.
  - **Composite verdict:**
    - `safe_to_authorize_writer_now: bool`.
    - `recommended_operator_action: str` (one of the
      seven `ACTION_*` constants).
  - **Bucket lists** (mirror the queue planner's seven
    queues plus two derived buckets):
    - `authorization_candidate_tickers` — union of
      pipeline_only + refresh_then_pipeline (post-
      truncation), pipeline first.
    - `pipeline_only_tickers`,
      `refresh_then_pipeline_tickers`,
      `wait_for_cache_ahead_tickers`,
      `manual_stackbuilder_tickers`,
      `upstream_blocked_tickers`,
      `downstream_gap_tickers`,
      `current_leader_eligible_tickers`.
    - `contract_invalid_or_unknown_tickers` —
      tickers whose `downstream_contract_verdict` is
      NOT one of `{"contract_valid_no_action",
      "contract_valid_but_not_leader_eligible"}`.
  - **Diagnostics:**
    - `blocking_reasons` — every fired reason
      (operator sees concurrent gaps).
    - `queue_counts`, `queue_truncation` — passed
      through from the queue planner.
  - **Advisory commands:** `advisory_commands` —
    tuple of writer command strings from the
    pipeline_only + refresh queues, in queue order.
    **DISPLAY ONLY.**
  - **Ranking tails:** `positive_tail`, `negative_tail`,
    `low_buy_tail` — passed through from Phase 6I-3 /
    6I-6 verbatim.
  - **Input echoes:** `max_refresh`, `max_pipeline`,
    `top_n`.

`to_json_dict()` returns a fully JSON-serializable
dict.

## 4. Decision cascade

```
1. If inspected_count == 0:
     safe = False
     recommended_action = manual_review_required
     blocking_reasons += no_inspected_tickers

2. selected_write_ready_count =
     selected_pipeline_count + selected_refresh_count
   write_ready_queue_truncated =
     queue_truncation[pipeline_only_queue]
     OR queue_truncation[refresh_source_cache_then_pipeline_queue]

3. If selected_write_ready_count > 0:
     If write_ready_queue_truncated:
       safe = False
       recommended_action = manual_review_required
       blocking_reasons += write_ready_queue_truncated
     Else:
       safe = True
       recommended_action = authorize_guarded_writer_for_selected_tickers

4. If selected_write_ready_count == 0:
     safe = False
     First non-empty queue wins (priority order):
       manual_stackbuilder_queue ->
         resolve_stackbuilder_inputs
       upstream_blocked_queue ->
         fix_upstream_inputs
       downstream_gap_queue ->
         build_missing_downstream_artifacts
       wait_for_cache_ahead_queue ->
         wait_for_cache_ahead_of_cutoff
       current_leader_eligible_queue ->
         already_current_no_writer_needed
     else:
         manual_review_required
```

### Why active fixes beat passive wait

When write-ready is empty but multiple blocked queues
fire, the cascade picks the **most actionable** verb
first (resolve / fix / build) over the passive **wait**.
The rationale: an operator who is wait-only has nothing
to do until cache advances; if any tickers are
actionable today, that's the more useful next-step.
Both reasons appear in `blocking_reasons`.

### Persist-skip-lag is wait, not refresh

When a ticker's only state is
`wait_for_cache_ahead_queue` (cache equal to or behind
cutoff), the cascade routes to
`ACTION_WAIT_FOR_CACHE_AHEAD`, **never** to a refresh
or rerun recommendation. This pins the Phase 6E-2 /
6G-5 persist-skip-lag contract: the operator should
wait for the source cache to advance, not invoke a
refresh.

### Truncation refuses authorization

If `--max-refresh` or `--max-pipeline` truncates either
write-ready queue, the gate refuses to authorize even
when the visible set is non-empty. The operator should
bump the cap and re-inspect first. This avoids the
"authorize 5 visible while 10 more are hidden" footgun.

## 5. Advisory commands are DISPLAY ONLY

The gate carries the Phase 6I-6 queue planner's
advisory writer command strings verbatim, one per
write-ready ticker. The strings are **not executed**.
The Phase 6H-5 writer remains the only path to a
production write, and it still gates on its two-key
authorization (`--write` + `PRJCT9_AUTOMATION_WRITE_AUTH`).
The Phase 6I-8 post-pipeline contract-validation gate
fires on the writer's path; the gate here is the
**pre-decision** screen.

## 6. How the gate relates to the rest of the stack

```
                    (operator)
                        |
                        v
   daily_board_supervised_run_gate.py    <-- Phase 6I-9
                        |
                        v
   daily_board_execution_queue_planner.py <-- Phase 6I-6
                        |
   +--------------------+-------------------+
   v                    v                   v
 daily_board_      upstream_research_   confluence_ranking_
 universe_planner  input_audit.py       emitter.py
 (6I-5)            (6I-4)               (6I-3)
                        |
                        +--> confluence_ranking_
                             contract_validator.py (6I-1)

   spymaster.py / spymaster_master_audit.py  <-- Phase 6I-7
   (consumes the queue planner's report; not by this gate)

   daily_board_automation_writer.py          <-- Phase 6H-5
   (NOT imported by the gate; two-key gate
    + Phase 6I-8 post-pipeline contract
    validation lives there)
```

The gate sits **between the operator and the writer**.
It does NOT replace the Spymaster master-audit surface
(Phase 6I-7) — that surface is a UI consumer of the
queue planner's full report. The gate is a CLI / JSON
endpoint that distills the same data into a single yes
/no verdict.

## 7. Tests

`project/test_scripts/test_daily_board_supervised_run_gate.py`
ships 23 tests covering:

  1. Forbidden-imports static guard (writer / refresher
     / pipeline runner / live engines / yfinance /
     subprocess all blocked).
  2. No-StackBuilder-age-window static guard on the
     module's own source.
  3. Safe=True on `run_pipeline_only` write-ready
     queue.
  4. Safe=True on `refresh_source_cache_then_pipeline`
     write-ready queue.
  5. Safe=False for wait-only / manual /
     upstream-blocked / downstream-gap /
     already-current / empty inspection.
  6. Truncation refuses authorization (safe=False,
     manual_review).
  7. Cascade priority — write-ready wins over blocked
     queues; active fixes win over passive wait.
  8. Advisory commands are strings (not callables).
  9. Ranking tails pass through verbatim.
  10. JSON serialization round-trip.
  11. Contract-invalid-or-unknown tickers surfaced.
  12. CLI rc=0/2/3, no `SystemExit` leak; mutual
      exclusion enforced.
  13. Persist-skip-lag SPY-shaped case routes to wait,
      negatively pinned NOT to authorize / refresh /
      build.
  14. Queue counts + truncation flags echoed.

The tests use a `_FakeQueueReport` dataclass + a
single-function `_planner_returning(report)` so unit
tests never touch the real Phase 6I-6 / 6I-5 / 6I-4 /
6I-3 / 6I-1 stack. The CLI happy-path test uses the
real planner against an empty `tmp_path` so the chain
short-circuits without exercising any production
artifact.

## 8. Validation captured at module land

  - `py_compile` clean on both new files.
  - `test_daily_board_supervised_run_gate.py`:
    **23 passed in 0.98 s**.
  - **Focused 7-way (gate + execution queue planner +
    universe planner + upstream audit + ranking
    emitter + validator + writer): 201 passed in
    62.00 s.**
  - `git diff --check` clean.

## 9. Real-cache smoke output

Both smokes invoked the real Phase 6I-6 → 6I-5 → 6I-4
→ 6I-3 → 6I-1 chain read-only against production
roots. **No file was written.** The only output is
JSON to stdout.

### Single-ticker smoke

```
$ python daily_board_supervised_run_gate.py --ticker SPY

safe_to_authorize_writer_now:        true
recommended_operator_action:         "authorize_guarded_writer_for_selected_tickers"
inspected_count:                     1
discovered_stackbuilder_ticker_count: 248
authorization_candidate_tickers:     ["SPY"]
wait_for_cache_ahead_tickers:        []
contract_invalid_or_unknown_tickers: []
blocking_reasons:                    []
advisory_commands:
  - "python daily_board_automation_writer.py --ticker SPY --write"
queue_counts:
  refresh_source_cache_then_pipeline_queue:  1
  (every other queue: 0)
```

SPY today resolves to a `refresh_source_cache_then_pipeline`
verdict (cache last_date 2026-05-11 is behind the
resolved cutoff). The gate marks it write-ready,
surfaces one advisory writer command (display only),
and reports `safe_to_authorize_writer_now=true`. The
two-key writer gate STILL must be satisfied before
that command actually fires.

### Universe smoke

```
$ python daily_board_supervised_run_gate.py \
    --from-stackbuilder-universe \
    --max-refresh 5 --max-pipeline 5 --top-n 3

safe_to_authorize_writer_now:        true
recommended_operator_action:         "authorize_guarded_writer_for_selected_tickers"
inspected_count:                     248
discovered_stackbuilder_ticker_count: 248
authorization_candidate_tickers:     ["AAPL", "QQQ", "SPY", "TQQQ"]
blocking_reasons:
  - "stackbuilder_selection_or_inputs_manual"
  - "upstream_inputs_blocked"
  - "downstream_artifacts_missing"
  - "contract_invalid_or_unknown"
queue_counts:
  pipeline_only_queue:                       0
  refresh_source_cache_then_pipeline_queue:  4
  wait_for_cache_ahead_queue:                0
  manual_stackbuilder_queue:                62
  upstream_blocked_queue:                  168
  downstream_gap_queue:                     14
  current_leader_eligible_queue:             0
queue_truncation: all False (caps not hit at 5)
advisory_commands count:             4
positive_tail length:                1
low_buy_tail length:                 1
contract_invalid_or_unknown_tickers count: 247
```

Across the saved StackBuilder universe of 248 tickers,
the gate identifies 4 write-ready candidates (AAPL,
QQQ, SPY, TQQQ), surfaces 4 advisory writer command
strings, and reports `safe_to_authorize_writer_now=true`
— while still listing every concurrent blocker
(`manual_stackbuilder`, `upstream_blocked`,
`downstream_gap`, `contract_invalid_or_unknown`) in
`blocking_reasons` so the operator sees the universe
state at a glance. The 4 candidates are well below the
`max-refresh=5` cap, so the truncation flag stays
False and the gate authorizes.

## 10. Confirmation no production writes were run

Five independent checks:

1. **Forbidden-imports static guard** on the module's
   own AST: blocks every writer / refresher / pipeline
   runner / live engine / yfinance / subprocess
   import.
2. **No-age-window static guard** on the module's own
   source.
3. **Real-cache smokes use default production roots
   read-only** — the only output is JSON to stdout. No
   file write.
4. **The gate carries advisory writer commands as
   strings only** (test-pinned); it never invokes
   them.
5. **The Phase 6H-5 writer two-key gate is unchanged**
   — even if the gate says `safe=true`, the operator
   still needs `--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`
   to fire a production write. Phase 6I-8 post-pipeline
   contract validation continues to fire on that path.

## 11. Not a production-authorization phase

  - No CLI flag controls actual authorization. The
    gate is a **verdict producer**, not an authorizer.
  - The two-key writer gate (`--write` flag + env
    var) is the production-authorization layer; it is
    unchanged.
  - The Phase 6I-8 post-pipeline contract-validation
    gate fires on the writer's path; the
    supervised-run gate is the **pre-decision**
    screen.
  - StackBuilder durability is unchanged: saved
    variants durable, multiple per ticker first-class,
    tied newest-mtime blocks, **no age window**.

## 12. Future work (named, not implemented here)

  - **Operational variant** — scheduler / alerting
    layer that consumes the gate's JSON on a cadence,
    pages on `safe_to_authorize_writer_now == false`
    with `wait_for_cache_ahead_of_cutoff`,
    pre-prepares the two-key gate for the supervised
    operator workflow.
  - **Spymaster master-audit surface** — Phase 6I-7
    already consumes the queue planner directly; a
    future enhancement could surface this gate's
    composite verdict in a single card so the
    operator sees `safe_to_authorize` at a glance.
  - **Aggregate Confluence p-value** — explicit
    future gap from Phase 6I-2 § 4.3 / § 6.2 that
    flows into Phase 6I-3 / 6I-6 row schemas and
    therefore into this gate's `contract_invalid_or_unknown`
    bucket.

## 13. Reference paths

### New module + tests + doc (this PR)

  - `project/daily_board_supervised_run_gate.py` (new
    module).
  - `project/test_scripts/test_daily_board_supervised_run_gate.py`
    (23 tests).
  - `project/md_library/shared/2026-05-12_PHASE_6I9_SUPERVISED_RUN_GATE.md`
    (this doc).

### Modules consumed (read-only)

  - `daily_board_execution_queue_planner.build_execution_queue`
    (Phase 6I-6) — the single planning dependency.

### Cross-references

  - Phase 6I-6 execution queue planner:
    `project/md_library/shared/2026-05-12_PHASE_6I6_DAILY_BOARD_EXECUTION_QUEUE_PLANNER.md`.
  - Phase 6I-7 Spymaster master-audit surface:
    `project/md_library/shared/2026-05-12_PHASE_6I7_SPYMASTER_MASTER_AUDIT_SURFACE.md`
    (parallel consumer of the queue planner; the
    Spymaster UI surface vs. this CLI verdict
    surface).
  - Phase 6I-8 writer post-pipeline contract
    validation:
    `project/md_library/shared/2026-05-12_PHASE_6I8_WRITER_POST_PIPELINE_CONTRACT_VALIDATION.md`
    (the writer-side gate this pre-decision screen
    sits in front of).
  - Phase 6H-7 production runbook:
    `project/md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`
    (the operator stack this gate is meant to
    front).
