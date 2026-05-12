# Phase 6H-3 — Daily Signal Board automation preflight contract

**Status:** plan-only. This phase ships a read-only
orchestration contract for the future daily automation; it
does **not** automate any production write.

**Last updated:** 2026-05-12.

This doc explains the proposed daily automation sequence,
which stages are daily vs. stable/manual, why StackBuilder
outputs are treated as saved stack variants rather than as
an expiring daily input, and how multiple stacks per ticker
are surfaced today. The Phase 6H-3 module is
`project/daily_board_automation_preflight.py`; tests at
`project/test_scripts/test_daily_board_automation_preflight.py`.

## 1. What this phase delivers

**Code (read-only):**

  - `project/daily_board_automation_preflight.py` — new
    module that joins
    `cache_cutoff_watcher.evaluate_cache_cutoff_state` and
    `confluence_pipeline_readiness.inspect_ticker_pipeline`
    with a fresh StackBuilder inventory probe and a stable
    decision tree, and emits a `DailyBoardAutomationPlan`
    per operator-supplied ticker list.
  - `project/test_scripts/test_daily_board_automation_preflight.py`
    — 25 tests pinning every branch of the decision tree,
    the StackBuilder inventory + selection policy contract,
    the advisory-only `would_run_commands` contract, the
    aggregate plan's counts and `ready_for_pipeline_tickers`
    partition, the full CLI matrix, and the
    no-yfinance/no-dash/no-pipeline-writer static guard.

**Explicit non-goals:**

  - No source refresh writes.
  - No Phase 6D pipeline writes.
  - No StackBuilder runs.
  - No OnePass runs.
  - No multi-timeframe library rebuilds.
  - No edits to `daily_signal_board.py`, the StackBuilder
    engine, OnePass, the TrafficFlow builder, the Confluence
    builder, or any production cache / output /
    signal_library file.

The PR emits a plan. Any operator who reads the plan can
choose to run the advisory commands manually; the module
itself never executes them.

## 2. The proposed daily automation sequence

Stages a future automation should run *every trading day*,
in this order:

  1. **Cache-vs-cutoff watcher** (`cache_cutoff_watcher.py`,
     Phase 6H-2). Read-only. Determines whether
     `cache.last_date > current_as_of_date`.
  2. **Source refresh** (`signal_engine_cache_refresher.py
     --ticker <T> --write`, Phase 6E-5). **Only when
     needed.** Skipped when the watcher already reports
     `ready_for_pipeline_write`. Skipped under
     `pipeline_output_lags_persist_skip` because a refresh
     today cannot close that gap.
  3. **Phase 6D pipeline write**
     (`confluence_pipeline_runner.py --ticker <T> --write`,
     Phase 6D-4). **Only when the cache is strictly ahead of
     the cutoff.** Persist-trims still apply, but with cache
     ahead, the trim lands Confluence at-cutoff and the
     ticker becomes leader-eligible after the write.
  4. **Board readiness audit**
     (`board_launch_readiness_audit.py --tickers <T>
     --no-dry-run`, Phase 6E-1 / 6F-4 / 6G-5). Read-only
     verification that the post-write tree is leader-
     eligible. **A future phase will wire this as a
     post-write check; this PR does not run it.**

Phase 6H-3 (this PR) wraps stages 1–4 into one read-only
preflight that says, per ticker, *which* of those stages
the automation should run and which exact commands map to
each. The preflight stops short of executing anything.

## 3. Stages that are NOT daily

These inputs are stable / manual / research-frequency and
must NOT be on the daily automation hook:

  - **OnePass** — research workflow that produces signal-
    library and multi-timeframe inputs. Long-running and
    parameter-driven; not a daily orchestration concern.
  - **StackBuilder generation** — produces saved stack
    *variants* (combo leaderboard plus per-K rows). Heavy,
    parameter-driven, and an operator decision; see § 4.
  - **Manual StackBuilder parameter selection** — choosing
    which stack to use for a target. Today the existing
    pipeline default is newest-mtime; an explicit selection
    contract is a future-phase concern (see § 5).
  - **Multi-timeframe library rebuilds** — produced by
    `signal_library/multi_timeframe_builder.py`. Daily
    automation does not touch these; it only blocks when
    they are missing and surfaces
    `refresh_multitimeframe_libraries_manual` so an
    operator can rebuild them.

## 4. Why StackBuilder is "saved stack variants", not a 30-day expiring input

Earlier sprint discussions floated a "30-day stale
StackBuilder" window. **This phase rejects that design**
for the following reasons:

  - StackBuilder consumes many user-set variables originally
    sourced from OnePass. The choice of stack composition,
    K range, member protocols, seed-run identifier, and
    leaderboard sort order are all research decisions.
    Re-running StackBuilder daily is not just heavy —
    it would silently rewrite the research stack, which is
    not what an automation layer should do.
  - A given ticker may legitimately have **multiple saved
    stack variants**, generated from different OnePass
    studies or different parameter sweeps. None of them
    "expire" by age; they each remain a valid saved variant
    until the operator decides otherwise.
  - The Phase 6D pipeline already has a default-selection
    policy (newest-mtime seed-run directory, per
    `trafficflow_k_artifact_builder.discover_latest_stackbuilder_run`).
    That policy is fine as a *current default*, but it is
    not load-bearing for automation correctness — the
    automation should surface which run the pipeline would
    pick, NOT regenerate the stack.
  - A 30-day window would conflate "old" with "wrong",
    which is the opposite of how research artifacts work
    in this repo.

The cleaner contract: StackBuilder runs are saved stack
variants. The automation reads the inventory, names which
variant the existing pipeline default would pick, and warns
the operator when multiple variants exist so a future phase
can ship an explicit stack selection contract.

## 5. How multiple stacks per ticker are represented

Per Phase 6H-3, every `TickerAutomationReadiness` carries:

  - `stackbuilder_present: bool` — at least one usable
    saved stack variant exists for this ticker (has either
    `combo_leaderboard.xlsx` or `combo_k=*.json`).
  - `stackbuilder_run_count: int` — number of usable
    variants.
  - `stackbuilder_run_ids: tuple[str, ...]` — variant
    directory names, sorted by mtime descending (newest
    first).
  - `selected_stackbuilder_run_id: str | None` — the
    variant the existing pipeline default would pick today,
    or `None` if no deterministic default applies.
  - `stackbuilder_selection_policy: str` — stable string,
    one of:
      * `no_stack_available` (zero variants on disk)
      * `single_available_stack` (one variant)
      * `latest_mtime_existing_pipeline_default` (multi-
        variant, newest-mtime wins deterministically)
      * `ambiguous_tied_mtime` (multi-variant, ≥2 variants
        share the newest mtime; cannot deterministically
        pick a winner under the current pipeline default)
  - `stackbuilder_selection_warning: str | None` — human-
    readable note when multiple variants exist or when
    selection is ambiguous.

### Current default: preserved

For now the preflight intentionally **preserves the existing
pipeline default** (newest mtime). This avoids any change
in pipeline-write behavior across Phase 6H-3.

When multiple variants exist with a clear newest-mtime
winner, the preflight reports
`latest_mtime_existing_pipeline_default`, names the chosen
variant, and emits a warning that future automation should
ship an explicit stack selection contract. **Stack age
alone is NEVER a block.** A 6-month-old variant that is the
only saved variant for a ticker is treated identically to
a newly-generated variant.

### Future required contract

When automation needs to choose among multiple saved
research stacks, a later phase should:

  - Ship a `stackbuilder_run_selection_policy` config (per-
    ticker or per-universe) that the pipeline runner reads
    instead of newest-mtime.
  - Surface that policy in the automation preflight via a
    new value of `stackbuilder_selection_policy` (e.g.
    `explicit_pinned_run_id`).
  - Keep the preflight's existing failure mode for
    ambiguous-tied-mtime as a structural blocker.

That is a separate scope; this PR explicitly does not
implement it.

## 6. Why TrafficFlow daily-K → MTF-K → Confluence is the reliable saved-output path

The public Daily Signal Board consumes
`output/research_artifacts/confluence/<TICKER>/<TICKER>__MTF_CONSENSUS.research_day.json`.
That artifact is produced by the Phase 6D-3 builder, which
reads Phase 6D-2 MTF K artifacts, which read Phase 6D-1
daily K artifacts, which read the StackBuilder leaderboard
chosen by the current pipeline default.

Each stage:

  - Is offline / read-only relative to the network (no
    yfinance import in the builders).
  - Produces a `research_day_v1` artifact with a deterministic
    filename and a stable last-row date contract.
  - Trims the source cache by `persist_skip_bars=1` at the
    daily-K stage so the saved tree never carries today's
    still-revising bar (Phase 6D-1 policy).
  - Inherits the trimmed last_date through MTF K and
    Confluence (Phase 6F-4 verified there is exactly one
    trim across the chain).

That is why the daily automation focus is the TrafficFlow
daily K → MTF K → Confluence write path. Other engines
(ImpactSearch, raw TrafficFlow, OnePass) produce useful
saved research artifacts but they are not on the leader-
gate critical path; the readiness layer treats them as
optional reference stations.

## 7. Decision tree (stable order)

For each ticker the preflight applies the rules below in
order; the first matching rule wins.

  1. **Already leader-eligible** (`readiness.leader_eligible
     == True`) → `no_action_already_current`. Empty
     blocking reasons, no advisory commands.
  2. **Health report blocks the ticker** (the readiness
     layer's `ISSUE_HEALTH_REPORT_BLOCKED` is present) →
     `blocked_manual_review` + `health_report_blocked`.
  3. **Cache missing** (the watcher returns
     `missing_cache`) → `blocked_manual_review` +
     `cache_missing`.
  4. **Cache manual-review** (the watcher returns
     `manual_review` due to unparseable / unreadable cache
     metadata) → `blocked_manual_review` +
     `manual_review_required`.
  5. **No StackBuilder run** (no usable saved stack variant
     on disk) →
     `select_or_create_stackbuilder_stack_manual` +
     `stackbuilder_missing`.
  6. **StackBuilder selection ambiguous** (multiple
     variants tied for newest mtime) →
     `select_or_create_stackbuilder_stack_manual` +
     `stackbuilder_selection_ambiguous`.
  7. **Multi-timeframe libraries missing** (`STAGE_MULTITIMEFRAME_LIBRARIES`
     not present) →
     `refresh_multitimeframe_libraries_manual` +
     `multitimeframe_libraries_missing`.
  8. **Cache equal to cutoff** (the watcher returns
     `pipeline_output_lags_persist_skip`) →
     `wait_for_cache_ahead_of_cutoff` +
     `cache_equal_cutoff_persist_skip`. No advisory
     commands because no operator action today closes the
     gap.
  9. **Cache behind cutoff** (the watcher returns
     `refresh_source_cache`) →
     `refresh_source_cache_then_pipeline` +
     `cache_behind_cutoff`. Advisory commands:
     `signal_engine_cache_refresher.py --ticker <T> --write`
     then `confluence_pipeline_runner.py --ticker <T> --write`.
  10. **Cache ahead of cutoff** (the watcher returns
      `ready_for_pipeline_write`) →
      `run_pipeline_only`. Advisory command:
      `confluence_pipeline_runner.py --ticker <T> --write`.

## 8. Advisory `would_run_commands`

The preflight populates a `would_run_commands: tuple[str,
...]` field per ticker. Strings are operator-facing copy-
paste commands the future automation could run. The
preflight itself **never executes them**. Two of the seven
recommendation values populate `would_run_commands` (the
two that map to real `--write` operations); the other five
return an empty tuple because there is no safe non-
interactive command for them.

| Recommendation | would_run_commands |
|---|---|
| `no_action_already_current` | (empty) |
| `wait_for_cache_ahead_of_cutoff` | (empty) |
| `refresh_source_cache_then_pipeline` | two commands: refresher `--write`, then pipeline runner `--write` |
| `run_pipeline_only` | one command: pipeline runner `--write` |
| `select_or_create_stackbuilder_stack_manual` | (empty) |
| `refresh_multitimeframe_libraries_manual` | (empty) |
| `blocked_manual_review` | (empty) |

A test pins both shapes (empty for manual / blocked
actions; populated `--write` strings for the two automated
recommendations) and an additional test asserts that the
preflight does **not** write anything when populating those
strings.

## 9. Why this PR does not automate writes

Wiring an actual daily orchestrator that calls the
advisory commands requires:

  - A scheduler (cron / Task Scheduler / cloud cron) and
    the operational decisions that come with one
    (timezone, retry policy, alert routing).
  - A guarantee that yfinance access is in-scope at the
    chosen run-time (Phase 5G data-licensing gate is
    parked; commercial framing is not yet authorized).
  - An audit trail for every executed `--write` invocation
    that the existing Phase 5C validation framework can
    pick up.
  - An on-failure path: the persist-skip-lag verdict will
    keep firing for hours per UTC day, and an automation
    that re-fires every 10 minutes inside that window is
    just expensive noise.

None of those exist yet, and shipping the decision tree
before the policy decisions are made would be premature.
Phase 6H-3 stops at the contract.

## 10. Real-cache SPY preflight (captured at PR open, read-only)

`python daily_board_automation_preflight.py --ticker SPY`
against the real on-disk artifacts:

```
generated_at                          2026-05-12T05:23:09+00:00
current_as_of_date                    2026-05-11
ticker                                SPY
cache_cutoff_action                   pipeline_output_lags_persist_skip
source_cache_date                     2026-05-11
stackbuilder_present                  true
stackbuilder_run_count                1
stackbuilder_run_ids                  ["seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D"]
selected_stackbuilder_run_id          "seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D"
stackbuilder_selection_policy         single_available_stack
stackbuilder_selection_warning        null
multitimeframe_libraries_present      true
trafficflow_daily_k_present           true
trafficflow_mtf_k_present             true
confluence_present                    true
current_leader_eligible               false
recommended_automation_action         wait_for_cache_ahead_of_cutoff
blocking_reasons                      ["cache_equal_cutoff_persist_skip"]
would_run_commands                    []
```

The cache and the cutoff resolver both sit at `2026-05-11`,
so the persist-skip trim cannot make Confluence current
under the existing contract. Automation today has nothing
to do for SPY beyond wait; the preflight names that
explicitly.

## 11. Reference paths

  - Automation preflight (this phase):
    `project/daily_board_automation_preflight.py`
  - Automation preflight tests:
    `project/test_scripts/test_daily_board_automation_preflight.py`
  - Phase 6H-2 cache-vs-cutoff watcher:
    `project/cache_cutoff_watcher.py`
  - Phase 6E-2 freshness preflight:
    `project/source_freshness_preflight.py`
  - Phase 6E-1 launch readiness audit:
    `project/board_launch_readiness_audit.py`
  - Phase 6C-8 readiness:
    `project/confluence_pipeline_readiness.py`
  - Phase 6D-4 pipeline runner:
    `project/confluence_pipeline_runner.py`
  - Phase 6E-5 source refresher:
    `project/signal_engine_cache_refresher.py`
  - Phase 6H-1 launch / design handoff doc:
    `project/md_library/shared/2026-05-12_PHASE_6H_DAILY_SIGNAL_BOARD_LAUNCH_HANDOFF.md`
  - Phase 6G-5 persist-skip-lag contract:
    `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md`
    § 6.8 and
    `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md`
    § 7.
