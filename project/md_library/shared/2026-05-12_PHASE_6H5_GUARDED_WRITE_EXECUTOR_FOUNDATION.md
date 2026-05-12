# Phase 6H-5 — Guarded write-capable automation executor foundation

**Status:** write-capable executor wired and validated, but
**no production writes were run in this PR.** All
validation used dependency-injected fakes and temp-dir
fixtures.

**Last updated:** 2026-05-12.

This phase turns the Phase 6H-4 dry-run executor into a
real execution path that can call the live refresher and
the live pipeline runner — but only when an operator
explicitly authorizes the write via TWO independent keys.
The same Phase 6H-4 refresh → recheck → pipeline
sequencing is enforced live: the pipeline write fires only
when the cache-vs-cutoff watcher returns
`ready_for_pipeline_write` after the post-refresh re-check.

## 1. What this phase delivers

**Code:**

  - `project/daily_board_automation_writer.py` — new
    module. Public surface:
      * `WriteAuthorization` dataclass +
        `resolve_write_authorization` helper.
      * `RefreshOutcome`, `WatcherRecheckOutcome`,
        `PipelineOutcome`, `ReadinessOutcome`,
        `TickerWriteExecution`,
        `DailyBoardWriteExecutionReport` dataclasses.
      * `execute_daily_board_automation(tickers, *, ...)
        -> DailyBoardWriteExecutionReport`.
      * `main(argv=None) -> int` CLI.
    Lazy imports for `signal_engine_cache_refresher` and
    `confluence_pipeline_runner` mean the top-level
    import set stays minimal and the dry-run path never
    materializes either writer.

  - `project/test_scripts/test_daily_board_automation_writer.py`
    — 28 tests. Includes a forbidden-imports static guard
    that rejects `yfinance`, `dash`,
    `daily_signal_board`, `subprocess`, **and** any
    top-level reference to
    `signal_engine_cache_refresher` /
    `confluence_pipeline_runner`. Both writer imports
    must be lazy.

  - `project/md_library/shared/2026-05-12_PHASE_6H5_GUARDED_WRITE_EXECUTOR_FOUNDATION.md`
    — this doc.

**Explicit non-goals (none performed in this PR):**

  - No production source-cache refresh.
  - No production Phase 6D pipeline write.
  - No StackBuilder run.
  - No OnePass run.
  - No multi-timeframe library rebuild.
  - No subprocess.
  - No yfinance fetch.

The real refresher and the real pipeline runner are
*resolvable* via lazy imports if a future operator chooses
to fire the live path. They were not fired during testing;
all tests inject fakes.

## 2. Two-key write authorization

The CLI requires BOTH:

  1. `--write` (CLI flag)
  2. `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`
     (environment variable, exact string match)

Outcome matrix (live-confirmed by tests):

| `--write` | env var | Result | Exit |
|---|---|---|---|
| absent | absent or anything | Dry-run mode. No writes. | `rc=0` |
| present | absent | Refused. No writes. | `rc=2` |
| present | wrong value | Refused. No writes. | `rc=2` |
| present | `phase_6h5_explicit` | Authorized. Live writes fire per the sequencing contract. | `rc=0` |
| absent | `phase_6h5_explicit` | Still dry-run (env alone is not the live path). | `rc=0` |

When `--write` is requested but the env value is missing or
wrong, the CLI emits a stderr JSON error:

```json
{
  "error": "write_authorization_failed",
  "detail": "--write requested but environment variable PRJCT9_AUTOMATION_WRITE_AUTH does not equal the required sentinel value 'phase_6h5_explicit'; both keys are required for live writes",
  "required_env_var": "PRJCT9_AUTOMATION_WRITE_AUTH",
  "required_env_value": "phase_6h5_explicit"
}
```

The exact authorization-rejection behavior is verified by
`test_cli_write_without_env_returns_2` and
`test_cli_write_with_wrong_env_value_returns_2`.

## 3. Refresh → recheck → pipeline sequence (live)

This is the Phase 6H-4 sequencing contract executed live
under authorized writes. Per-ticker, when the planner's
initial verdict is
`refresh_source_cache_then_pipeline`:

  1. **Refresh** — call
     `signal_engine_cache_refresher.refresh_signal_engine_cache(
     ticker, cache_dir=..., write=True,
     current_as_of_date=...)`. Capture the result into
     `RefreshOutcome` (attempted, succeeded,
     old/new cache last_date, stale_before, current_after,
     issue codes, elapsed seconds).
  2. **Recheck** — call
     `cache_cutoff_watcher.evaluate_cache_cutoff_state(
     ticker, cache_dir=..., current_as_of_date=...)`
     against the post-refresh cache. Capture into
     `WatcherRecheckOutcome` (`cache_date_range_end`,
     `current_as_of_date`,
     `recommended_operator_action`,
     `ready_for_pipeline` flag).
  3. **Pipeline (gated)** — only if
     `watcher.recommended_operator_action ==
     cache_cutoff_watcher.ACTION_READY_FOR_PIPELINE_WRITE`,
     call
     `confluence_pipeline_runner.run_confluence_pipeline_for_ticker(
     ticker, ..., write=True)`. Capture into
     `PipelineOutcome` and extract the embedded
     `ReadinessOutcome` from `PipelineRunResult.readiness`.

**Any non-ready watcher verdict — including
`pipeline_output_lags_persist_skip` and
`refresh_source_cache` — withholds the pipeline write.** A
dedicated test
(`test_refresh_then_pipeline_withholds_pipeline_when_watcher_blocks`)
injects a watcher fake that returns persist-skip-lag and
asserts the pipeline_runner fake was never called. A
second test
(`test_refresh_then_pipeline_withholds_when_watcher_says_refresh_again`)
covers the "refresh did not actually advance the cache"
case with the watcher still returning
`refresh_source_cache`.

For the `run_pipeline_only` plan verdict (cache strictly
ahead of cutoff), the executor skips the refresh + recheck
and calls the pipeline runner once directly with `write=True`.

For every other planner verdict (already-current, waiting,
manual, blocked), no writer is called. The per-ticker
record's `skipped_reason` names the structural cause and
`commands_executed` / `functions_executed` stay empty.

## 4. Why the pipeline is withheld unless the watcher passes

The Phase 6D-1 `persist_skip_bars=1` policy keeps the saved
Confluence one trading bar behind the source cache by
design. After a real refresh:

  - If the refresh fetched a trading day strictly after the
    cutoff: the persist trim leaves Confluence at-cutoff,
    so the pipeline write is useful and the watcher
    returns `ready_for_pipeline_write`.
  - If the refresh did NOT advance the cache past the
    cutoff (yfinance returned no new bar, network failed,
    rate-limited, etc.): the persist trim still lands
    Confluence one trading bar behind, and the watcher
    returns `pipeline_output_lags_persist_skip` (or
    `refresh_source_cache` for the no-advance case). A
    pipeline write here would silently produce
    byte-identical persist-trimmed Confluence; the
    executor's gate prevents that waste cycle.

The gate is read from the cache-vs-cutoff watcher's strict
inequality verdict, not from any wall-clock assumption.
This is the same safety predicate the launch audit + the
freshness preflight + the Phase 6H-4 dry-run executor have
been ratifying since Phase 6G-5.

## 5. StackBuilder multi-stack policy (unchanged)

The Phase 6H-3 contract carries forward verbatim:

  - StackBuilder outputs are saved stack variants, not a
    30-day expiring input.
  - Multiple variants per ticker are first-class.
  - The current pipeline default (newest mtime, via
    `trafficflow_k_artifact_builder.discover_latest_stackbuilder_run`)
    is preserved and surfaced as
    `latest_mtime_existing_pipeline_default`.
  - Tied newest-mtime is `ambiguous_tied_mtime` and blocks
    automation (`select_or_create_stackbuilder_stack_manual`).
  - Stack age alone is never a block.

The executor never invokes StackBuilder. The
StackBuilder-related plan verdicts
(`select_or_create_stackbuilder_stack_manual` /
`refresh_multitimeframe_libraries_manual`) drop straight
into the no-writes manual branch and the per-ticker record
sets `skipped_reason = "manual"`. Two tests pin this:
`test_manual_stackbuilder_action_executes_no_writes` and
`test_ambiguous_stackbuilder_action_executes_no_writes`.

## 6. Temp-dir-only validation

Every test uses `tmp_path` fixtures for `cache_dir`,
`artifact_root`, `stackbuilder_root`, and
`signal_library_dir`. The default-resolver helpers in the
production module return the real `project/cache/results/`,
`project/output/research_artifacts/`,
`project/output/stackbuilder/`, and
`project/signal_library/data/stable/` paths, but tests
override every root via explicit kwargs so no production
path is ever read or written.

A specific test
(`test_dry_run_writes_nothing_to_any_root`) snapshots every
operator-supplied tmp root before and after a dry-run and
asserts byte-identical state. Another
(`test_execution_log_absent_when_no_path_provided`)
asserts that without `--execution-log` the tmp tree gains
no new file. The execution-log tests
(`test_execution_log_is_appended_jsonl`,
`test_execution_log_records_stage_sequence`) only write to
tmp_path-derived JSONL files.

## 7. Execution log (append-only JSONL)

`--execution-log <path>` enables an append-only JSONL log.
One JSON object per ticker per invocation. Each line
carries a `logged_at` UTC timestamp on top of the full
per-execution payload (`commands_executed`,
`functions_executed`, all outcome dataclasses serialized,
`skipped_reason`, etc.).

The executor opens the file with `mode="a"`, never
truncates, never rewrites. A second invocation appends a
second line; a long-running operator using the same log
across days accumulates a chronological audit trail. Tests
verify both append behavior and stage-sequence ordering.

## 8. CLI contract

```
python daily_board_automation_writer.py --ticker SPY
python daily_board_automation_writer.py --tickers SPY,AAPL
python daily_board_automation_writer.py --ticker SPY --dry-run
PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit \
    python daily_board_automation_writer.py --ticker SPY --write \
    --execution-log /tmp/exec_log.jsonl
```

- `--ticker` and `--tickers` mutually exclusive.
- `--dry-run` and `--write` mutually exclusive at argparse
  level. (`--dry-run` is the default mode; the flag is
  accepted for explicitness.)
- `--execution-log <path>` optional; default is no log.
- All four roots (`--cache-dir`, `--artifact-root`,
  `--stackbuilder-root`, `--signal-library-dir`) and
  `--current-as-of-date` are accepted as overrides.
- `rc=0` on success, `rc=2` on invalid args / failed
  authorization, `rc=3` on unexpected exception.
- `main(argv=None)` traps argparse `SystemExit` and
  converts to `rc=2`; no `SystemExit` leak.

## 9. Comparison with Phase 6H-4

The Phase 6H-4 dry-run executor remains the simpler
dry-run-only tool. Its forbidden-imports guard explicitly
rejects `signal_engine_cache_refresher`,
`confluence_pipeline_runner`, and `subprocess` even as
strings inside `import` statements. Phase 6H-5 relaxes
those exclusions to **lazy imports only** — the writer
modules are reachable when the live path actually
executes, but the top-level AST still must not reference
them. This is structurally tested in
`test_writer_module_has_no_forbidden_top_level_imports`.

| Property | Phase 6H-4 (`daily_board_automation_executor.py`) | Phase 6H-5 (`daily_board_automation_writer.py`) |
|---|---|---|
| Default mode | Dry-run only | Dry-run only |
| `--write` flag | Rejected (rc=2) | Accepted; gated by env var |
| Env-var gate | n/a | `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` |
| Live refresher call | Never | Only when both keys present + plan verdict requires it |
| Live pipeline call | Never | Only when watcher returns `ready_for_pipeline_write` (after live refresh) OR plan verdict is `run_pipeline_only` |
| Subprocess | Forbidden | Forbidden |
| Top-level writer imports | Forbidden | Forbidden (lazy only) |
| Execution log | n/a | Optional JSONL via `--execution-log` |

Both modules can coexist. The Phase 6H-4 dry-run executor
is the safe, simpler operator tool for "what would
happen?" inspection; the Phase 6H-5 writer is the
foundation for the future automation workflow once an
operator decides to take the live path.

## 10. What still has to land before production automation

The two-key gate is the **structural** check. Operational
work remaining before this module can be wired into a
real scheduler:

  - **Scheduler + retry policy** (UTC-aware; idempotent
    retries against the cache-vs-cutoff gate).
  - **Alerting** for `refresh_executed_pipeline_withheld`
    outcomes that recur for more than one trading day for
    a given ticker (suggests yfinance access issues).
  - **Data licensing (Phase 5G)** sign-off before any
    public-facing automation framing.
  - **Validation integration** (Phase 5C) — every
    authorized write should feed the
    `validation_contract_v1` sidecar pipeline that
    `honest_validation_ledger.py` aggregates.

None of those are in scope for this PR. They are the
operational pieces the user explicitly named in the Phase
6H-4 doc § 7 future-extension path.

## 11. Reference paths

  - Writer module (this phase):
    `project/daily_board_automation_writer.py`
  - Writer tests:
    `project/test_scripts/test_daily_board_automation_writer.py`
  - Phase 6H-4 dry-run executor (predecessor):
    `project/daily_board_automation_executor.py`
  - Phase 6H-4 doc:
    `project/md_library/shared/2026-05-12_PHASE_6H4_DAILY_BOARD_AUTOMATION_DRY_RUN_EXECUTOR.md`
  - Phase 6H-3 automation preflight (planner input):
    `project/daily_board_automation_preflight.py`
  - Phase 6H-2 cache-vs-cutoff watcher (recheck gate):
    `project/cache_cutoff_watcher.py`
  - Phase 6E-5 source refresher (live writer):
    `project/signal_engine_cache_refresher.py`
  - Phase 6D-4 pipeline runner (live writer):
    `project/confluence_pipeline_runner.py`
  - Phase 6G-5 persist-skip-lag contract:
    `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md`
    § 6.8 and
    `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md`
    § 7.
