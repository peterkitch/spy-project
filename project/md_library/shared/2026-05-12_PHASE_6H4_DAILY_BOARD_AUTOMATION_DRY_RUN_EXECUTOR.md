# Phase 6H-4 — Daily Signal Board automation dry-run executor

**Status:** dry-run only. This phase ships an executor that
sequences the Phase 6H-3 automation plan into ordered
steps, **but does not perform any production write.** No
source refresh, no Phase 6D pipeline write, no StackBuilder
run, no OnePass run, no subprocess.

**Last updated:** 2026-05-12.

The Phase 6H-3 preflight already answers "what should
happen for this ticker?". Phase 6H-4 answers "in what
order, with what safety re-checks?". The pivotal safety
contract: `refresh_source_cache_then_pipeline` must
**split** into refresh → recheck → pipeline, and the
pipeline write command must NOT be emitted until the
cache-vs-cutoff watcher confirms `ready_for_pipeline_write`
after the real refresh.

## 1. What this phase delivers

**Code (read-only):**

  - `project/daily_board_automation_executor.py` — new
    dry-run executor module. Consumes the Phase 6H-3
    `TickerAutomationReadiness` per ticker and translates
    it into an ordered `AutomationExecutionStep` sequence
    on a `TickerAutomationExecution`. Aggregates the
    per-ticker results into a
    `DailyBoardAutomationExecutionReport` with stable
    counts and partitions. Never runs anything; the only
    "executed" commands are the empty tuple
    `executed_commands = ()` on every execution record.
  - `project/test_scripts/test_daily_board_automation_executor.py`
    — 23 tests pinning every per-action branch, the
    refresh → recheck → pipeline sequencing safety,
    aggregate counts + ready/blocked partitions, the
    `--write` rejection path, and the static no-yfinance /
    no-dash / no-pipeline-runner / no-refresher /
    **no-subprocess** import guard.

**Explicit non-goals:**

  - No source refresh writes.
  - No Phase 6D pipeline writes.
  - No StackBuilder runs.
  - No OnePass runs.
  - No multi-timeframe library rebuilds.
  - **No subprocess.** The executor never shells out to
    anything. The future-automation work that actually
    invokes the advisory commands is deferred to a phase
    that ships an explicit write-authorization flag.

## 2. This is a dry-run executor, not live automation

Every public API path returns the same kind of result
regardless of the input: a JSON-serializable record that
*describes* what the future automation would do. The
record carries `dry_run=True` at the report level and
`write_authorized=False` plus `executed_commands=()` at
every per-ticker level. Tests pin all of those values.

The module's import set proves the same thing structurally.
The static `test_executor_module_has_no_forbidden_imports`
test rejects any import of:

  - `yfinance`, `trafficflow`, `spymaster`, `impactsearch`,
    `onepass`, `confluence`, `cross_ticker_confluence`,
    `dash`, `daily_signal_board` (live engines + web tier)
  - `signal_engine_cache_refresher`,
    `confluence_pipeline_runner` (production writers
    referenced only as CLI strings)
  - `subprocess` (so the module cannot start a process even
    by accident)

The only sibling imports are `cache_cutoff_watcher`,
`confluence_pipeline_readiness`, and
`daily_board_automation_preflight` — three read-only planner
layers from Phase 6H-2 / Phase 6C-8 / Phase 6H-3.

## 3. Why `refresh_source_cache_then_pipeline` must split

The Phase 6H-3 preflight returns the verdict
`refresh_source_cache_then_pipeline` when the cache is
strictly behind `current_as_of_date`. A naive automation
would read that and immediately run:

```
python signal_engine_cache_refresher.py --ticker SPY --write
python confluence_pipeline_runner.py --ticker SPY --write
```

That is **wrong** under the Phase 6D-1 `persist_skip_bars=1`
contract. After the real refresh, the cache's new
`last_date` is whatever yfinance just returned. It may or
may not strictly exceed `current_as_of_date`:

  - If the refresh fetched a trading day strictly after the
    cutoff: the persist trim of the cache by 1 trading bar
    lands Confluence at the cutoff exactly, and the
    pipeline write is useful (`ready_for_pipeline_write`).
  - If the refresh did **not** acquire a strictly-after
    trading day (yfinance returned only the in-progress or
    already-known bar): the cache may now equal the cutoff,
    and the persist trim still lands Confluence one
    trading bar behind. Running the pipeline produces
    byte-identical persist-trimmed Confluence; the operator
    has burned a pipeline cycle for nothing
    (`pipeline_output_lags_persist_skip` reappears).

The watcher's strict cache-vs-cutoff inequality is the
gate. The Phase 6H-4 executor encodes the discipline:

  1. **Refresh step.** `would_run=True`,
     `command="python signal_engine_cache_refresher.py
     --ticker <T> --write"`,
     `post_action="recheck_cache_cutoff_after_refresh"`.
  2. **Recheck step.** `would_run=False`, `command=None`,
     `pre_action="refresh_source_cache"`,
     `post_action="run_pipeline"`. The reason field
     explains that automation must re-run
     `cache_cutoff_watcher` and only emit the pipeline
     write when the watcher returns
     `ready_for_pipeline_write`. **No pipeline command is
     present in the dry-run step list.**

A dedicated test
(`test_refresh_then_pipeline_does_not_emit_pipeline_command`)
scans every step in the refresh-then-pipeline case and
asserts that no step carries
`confluence_pipeline_runner.py` as a runnable command. This
is the load-bearing safety property the phase ships.

The `final_recommended_action` for this case is the new
stable string `awaiting_recheck_after_refresh` so a
downstream consumer (a future scheduler, an audit dashboard)
sees the dry-run state explicitly named, not
indistinguishable from the original
`refresh_source_cache_then_pipeline` plan.

## 4. Why the pipeline command is withheld until the watcher passes

This is the operator-honesty consequence of § 3. If the
executor pre-emitted the pipeline write command, a future
automation reading the dry-run output as a script could:

  - Run the refresher.
  - Skip the watcher recheck.
  - Run the pipeline based on the stale verdict.
  - Land Confluence one trading bar behind the cutoff.
  - Repeat byte-identical pipeline writes for the rest of
    the UTC day every time the watcher fires.

Withholding the pipeline command is a structural rejection
of that pattern. The recheck step's `reason` field carries
the exact watcher action name (`ready_for_pipeline_write`)
the future automation must observe before emitting the
pipeline command on its own — making the contract
testable from JSON.

## 5. StackBuilder remains a manual / stable input

The Phase 6H-3 contract (StackBuilder outputs are saved
stack variants, not a 30-day expiring input) carries forward
verbatim. The executor handles the StackBuilder-related
plan verdicts identically:

  - `select_or_create_stackbuilder_stack_manual` (no saved
    variants OR multiple variants with tied newest mtime)
    → zero steps, `would_write=False`,
    `skipped_reason="manual"`, ticker appears in
    `blocked_tickers`.

The executor never runs StackBuilder, never picks among
saved variants, never modifies the existing pipeline
default. Stack selection is research work; the executor
hands the operator the blocking verdict and stops.

## 6. Per-action step expansion (reference)

| Initial preflight action | Final executor action | Steps | `would_write` | `safe_to_execute_pipeline_after_recheck` | `skipped_reason` |
|---|---|---|---|---|---|
| `no_action_already_current` | same | 0 | False | False | None |
| `wait_for_cache_ahead_of_cutoff` | same | 0 | False | False | `waiting_for_cache_ahead_of_cutoff` |
| `select_or_create_stackbuilder_stack_manual` | same | 0 | False | False | `manual` |
| `refresh_multitimeframe_libraries_manual` | same | 0 | False | False | `manual` |
| `blocked_manual_review` | same | 0 | False | False | `blocked` |
| `run_pipeline_only` | same | 1 (pipeline) | True | True | `dry_run_only` |
| `refresh_source_cache_then_pipeline` | `awaiting_recheck_after_refresh` | 2 (refresh + recheck) | True | **False** | `awaiting_recheck_after_refresh` |

## 7. Future live-automation extension path

A later phase can build on this dry-run executor to ship
real automation. The shape:

  1. **Add a write-authorization flag.** A new positional
     or env-based authorization (`PRJCT9_AUTOMATION_WRITE=1`
     and an explicit CLI `--authorize-writes` flag) that
     flips `write_authorized` to `True` for the run.
  2. **Execute the refresh step** for tickers whose
     `would_write` is `True` and `final_recommended_action`
     is `awaiting_recheck_after_refresh`. The phase that
     ships this work must call
     `signal_engine_cache_refresher.py --write` (or the
     module's `refresh_signal_engine_cache` function) via
     a permitted execution path. Subprocess is acceptable
     in that phase; it is forbidden here.
  3. **Re-run the cache-vs-cutoff watcher.** Call
     `cache_cutoff_watcher.evaluate_cache_cutoff_state`
     against the post-refresh cache. Only proceed if the
     verdict is `ready_for_pipeline_write`. If the verdict
     is `pipeline_output_lags_persist_skip`, abandon the
     pipeline write and emit a clean exit code so the
     scheduler can decide whether to retry on the next
     trading-day rollover.
  4. **Execute the pipeline only if the watcher passes.**
     `confluence_pipeline_runner.py --write` for the
     authorized ticker.
  5. **Run the launch readiness audit after the pipeline.**
     `board_launch_readiness_audit.py --tickers <T>
     --no-dry-run`. Persist the audit's JSON output and
     diff against the pre-run state.
  6. **Persist an execution log.** Append-only JSON
     entries with the per-ticker decision, the
     pre-/post-refresh cache last_date, the post-watcher
     verdict, the pipeline runner's exit status, and the
     audit's `current_leader_eligible` result. This is the
     audit trail the Phase 5C validation framework should
     ingest.

None of those steps land in Phase 6H-4. The dry-run
executor is the stable read-only contract those future
steps will hang off.

## 8. CLI

```
python daily_board_automation_executor.py --ticker SPY
python daily_board_automation_executor.py --tickers SPY,AAPL
python daily_board_automation_executor.py --ticker SPY --dry-run
```

- `--ticker` and `--tickers` mutually exclusive.
- `--dry-run` is included for explicitness; it is the only
  supported mode in this phase.
- `--write` is parsed solely so the CLI can reject it
  explicitly. Passing `--write` exits `rc=2` with a clear
  stderr message: `production_writes_not_authorized`.
- `rc=0` on success, `rc=2` on invalid args / `--write`,
  `rc=3` on unexpected exception.
- `main(argv=None)` traps argparse `SystemExit` and
  converts to `rc=2`; no `SystemExit` leak.

## 9. Real-cache SPY dry-run (captured at PR open)

```
$ python daily_board_automation_executor.py --ticker SPY
```

```json
{
  "current_as_of_date":                       "2026-05-11",
  "dry_run":                                  true,
  "executions": [{
    "ticker":                                 "SPY",
    "initial_recommended_action":             "wait_for_cache_ahead_of_cutoff",
    "final_recommended_action":               "wait_for_cache_ahead_of_cutoff",
    "steps":                                  [],
    "would_write":                            false,
    "write_authorized":                       false,
    "executed_commands":                      [],
    "skipped_reason":                         "waiting_for_cache_ahead_of_cutoff",
    "safe_to_execute_pipeline_after_recheck": false
  }],
  "counts_by_final_recommended_action": {
    "wait_for_cache_ahead_of_cutoff": 1
  },
  "would_write_tickers":                      [],
  "blocked_tickers":                          ["SPY"]
}
```

SPY's live cache equals the cutoff (persist-skip-lag case),
so the executor emits zero step commands and names the wait
verdict explicitly. No file under `cache/`, `output/`,
`signal_library/`, or `stackbuilder/` changed during the
smoke.

## 10. Reference paths

  - Dry-run executor (this phase):
    `project/daily_board_automation_executor.py`
  - Dry-run executor tests:
    `project/test_scripts/test_daily_board_automation_executor.py`
  - Phase 6H-3 automation preflight (planner input):
    `project/daily_board_automation_preflight.py`
  - Phase 6H-3 doc:
    `project/md_library/shared/2026-05-12_PHASE_6H3_DAILY_BOARD_AUTOMATION_PREFLIGHT.md`
  - Phase 6H-2 cache-vs-cutoff watcher (recheck gate):
    `project/cache_cutoff_watcher.py`
  - Phase 6G-5 persist-skip-lag contract:
    `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md`
    § 6.8 and
    `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md`
    § 7.
  - Phase 6H-1 launch / design handoff doc:
    `project/md_library/shared/2026-05-12_PHASE_6H_DAILY_SIGNAL_BOARD_LAUNCH_HANDOFF.md`
