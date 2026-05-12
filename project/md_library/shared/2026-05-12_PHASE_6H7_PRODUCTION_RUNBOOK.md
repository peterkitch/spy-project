# Phase 6H-7 — Production-readiness runbook for the Daily Signal Board automation chain

**Status:** operator-facing runbook. This is the human
companion to the static
`2026-05-12_PHASE_6H7_OPERATOR_COMMAND_MANIFEST.json`
sibling. **This PR does not execute production writes.**

**Last updated:** 2026-05-12.

The Phase 6H train (#211 → #216) shipped a complete
read-only → planning → dry-run → guarded-write foundation
with temp-dir-isolated authorized rehearsal proof. This
runbook tells an operator how to actually use it without
breaking production state, and pairs the prose with a
machine-readable command manifest that downstream tools
can consume.

## 1. Operator command stack (in order)

Run these in this order when investigating "what would
happen if we ran automation for ticker T today?". Every
step is read-only by default; only the writer can be
authorized to perform writes via the two-key gate (§ 3).

  1. **`cache_cutoff_watcher.py --ticker <T>`** (Phase 6H-2)
     — read cache `last_date` vs `current_as_of_date`.
     Reports `recommended_operator_action` from the
     {`ready_for_pipeline_write` /
     `pipeline_output_lags_persist_skip` /
     `refresh_source_cache` / `missing_cache` /
     `manual_review`} namespace. No network. No writes.

  2. **`daily_board_automation_preflight.py --ticker <T>`**
     (Phase 6H-3) — full orchestration plan with
     StackBuilder inventory + selected variant +
     multi-timeframe library presence + per-stage
     `would_run_commands` advisory. No writes. No network.

  3. **`daily_board_automation_executor.py --ticker <T>`**
     (Phase 6H-4) — dry-run executor that names the
     refresh → recheck → pipeline step sequence per
     ticker. The pipeline write command is intentionally
     withheld in dry-run; the recheck gate is named but
     not executed. `--write` is rejected with `rc=2` by
     design. No writes. No subprocess. No network.

  4. **`daily_board_automation_writer.py --ticker <T>`**
     (Phase 6H-5 + 6H-6) — guarded write-capable
     executor. Default mode is dry-run / read-only. The
     live write path requires both `--write` AND the
     `PRJCT9_AUTOMATION_WRITE_AUTH` env var; see § 3.

  5. **`source_freshness_preflight.py --ticker <T>`**
     (Phase 6E-2 + 6G-5) — refresh-focused per-ticker
     classification (refresh / pipeline_after_refresh /
     pipeline_output_lags_persist_skip / etc.). Read-only.

  6. **`board_launch_readiness_audit.py --tickers <T>
     --no-dry-run`** (Phase 6E-1 + 6F-4 + 6G-5) — full
     per-stage readiness verdict with optional pipeline
     runner dry-run. Read-only.

Optional heavier probe:

  7. **`signal_engine_cache_refresher.py --ticker <T>
     --dry-run`** (Phase 6E-3 + 6E-5) — peeks at yfinance
     to project the would-be new `cache_date_range_end`
     without writing anything. Only command in the stack
     that touches the network. Read-only.

## 2. Decision tree

For each ticker the watcher / preflight will return one of
the verdicts below. The runbook's response per verdict:

| Verdict | Operator response | Authorized command? |
|---|---|---|
| `no_action_already_current` | Do nothing. The ticker is already leader-eligible. | None. |
| `wait_for_cache_ahead_of_cutoff` | Wait. The cache equals the cutoff; persist-skip-lag is in force; no operator action will move the verdict until the source cache acquires a trading day strictly past `current_as_of_date`. | None. Re-probe later with the watcher. |
| `select_or_create_stackbuilder_stack_manual` | Stop. The ticker has zero usable StackBuilder runs or tied newest-mtime ambiguity. Pick a stack variant out of band. | None. |
| `refresh_multitimeframe_libraries_manual` | Stop. Run the (manual) multi-timeframe library rebuild out of band. | None. |
| `blocked_manual_review` | Stop. Health-blocked or otherwise structurally blocked. | None. |
| `refresh_source_cache_then_pipeline` | **Authorized refresh → watcher recheck → pipeline.** Run the writer with both auth keys. Only the watcher recheck enables the pipeline. | `authorized_writer` (manifest § "authorized_writer_command"). |
| `run_pipeline_only` | **Authorized pipeline.** Cache already strictly ahead of cutoff. Run the writer with both auth keys; no refresh needed. | `authorized_writer` (no refresher invocation). |

## 3. Two-key write authorization contract

Two independent keys are required before any live writer
call fires:

  1. **CLI flag:** `--write`
  2. **Environment variable:**
     `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`
     (exact string match)

Outcome matrix:

| `--write` | Env var | Result | Exit |
|---|---|---|---|
| absent | absent or anything | Default dry-run. No writes. | `rc=0` |
| present | absent | Refused. Stderr `write_authorization_failed` JSON. No writes. | **`rc=2`** |
| present | wrong value | Refused. Stderr `write_authorization_failed` JSON. No writes. | **`rc=2`** |
| present | `phase_6h5_explicit` | Authorized. Live writes fire per § 5–§ 6. | `rc=0` |
| absent | `phase_6h5_explicit` | Still dry-run. Env alone is NOT the live path. | `rc=0` |

The CLI emits a JSON error to stderr when the gate
refuses:

```json
{
  "error": "write_authorization_failed",
  "detail": "--write requested but environment variable PRJCT9_AUTOMATION_WRITE_AUTH does not equal the required sentinel value 'phase_6h5_explicit'; both keys are required for live writes",
  "required_env_var": "PRJCT9_AUTOMATION_WRITE_AUTH",
  "required_env_value": "phase_6h5_explicit"
}
```

## 4. Required root flags for authorized commands

The Phase 6H-6 plumbing requires every authorized
`--write` invocation to supply ALL of the following root
flags explicitly. The CLI does not block omissions, but
operating without an explicit root pointer means the
underlying writer falls back to its production default —
which is exactly the failure mode the Phase 6H-6 plumbing
was added to prevent.

| Flag | Purpose | Underlying writer |
|---|---|---|
| `--cache-dir <path>` | Where the refresher writes the cache PKL + manifest sidecar. Pipeline runner READS this root. | Refresher + pipeline runner |
| `--status-dir <path>` | Where the refresher writes the per-ticker status JSON. **Added in Phase 6H-6.** | Refresher |
| `--artifact-root <path>` | Where the pipeline runner writes daily K + MTF K + Confluence artifacts. | Pipeline runner |
| `--stackbuilder-root <path>` | Where the pipeline runner READS the saved StackBuilder seed-run dir(s). Read-only input. | Pipeline runner |
| `--signal-library-dir <path>` | Where the pipeline runner READS the multi-timeframe library PKLs. Read-only input. | Pipeline runner |
| `--execution-log <path>` | Where the writer appends one JSONL row per ticker per invocation. Append-only. | Writer itself |

Additional non-root flag every authorized run should
supply:

  - `--current-as-of-date <YYYY-MM-DD>` — pin the readiness
    cutoff for reproducibility. Defaults to the resolver's
    UTC-derived value, which can drift between operator
    invocations across a UTC midnight boundary.

The exact PowerShell incantation (Windows / pinned
`spyproject2` interpreter):

```powershell
$env:PRJCT9_AUTOMATION_WRITE_AUTH = 'phase_6h5_explicit'
& 'C:\Users\sport\AppData\Local\NVIDIA\MiniConda\envs\spyproject2\python.exe' `
    daily_board_automation_writer.py `
    --ticker <TICKER> `
    --write `
    --cache-dir <CACHE_DIR> `
    --status-dir <STATUS_DIR> `
    --artifact-root <ARTIFACT_ROOT> `
    --stackbuilder-root <STACKBUILDER_ROOT> `
    --signal-library-dir <SIGNAL_LIBRARY_DIR> `
    --execution-log <EXECUTION_LOG_PATH> `
    --current-as-of-date <YYYY-MM-DD>
```

The bash / POSIX equivalent (see the JSON manifest's
`authorized_writer_command.command_template`).

## 5. Refresh → watcher recheck → pipeline rule

For the `refresh_source_cache_then_pipeline` plan verdict
the writer enforces a three-step sequence under
authorized writes:

  1. **Refresh** — calls
     `signal_engine_cache_refresher.refresh_signal_engine_cache(
     ticker, cache_dir=..., status_dir=..., write=True,
     current_as_of_date=...)`. Writes the cache PKL +
     manifest sidecar (under `--cache-dir`) and the status
     JSON (under `--status-dir`).

  2. **Watcher recheck** — calls
     `cache_cutoff_watcher.evaluate_cache_cutoff_state(
     ticker, cache_dir=..., current_as_of_date=...)`
     against the post-refresh cache. Reads only; returns
     the new `recommended_operator_action`.

  3. **Pipeline (gated)** — calls
     `confluence_pipeline_runner.run_confluence_pipeline_for_ticker(
     ticker, ..., write=True)` **only if** the watcher
     returns `ready_for_pipeline_write`.

**Any other watcher verdict — `pipeline_output_lags_persist_skip`,
`refresh_source_cache`, anything else — withholds the
pipeline write.** The structured outcome records
`final_recommended_action = refresh_executed_pipeline_withheld`
and `skipped_reason = watcher_blocked_pipeline_after_refresh`.
A failed watcher recheck (exception) is handled the same
way with an additional `watcher_exception` issue code in
the per-ticker outcome.

The `run_pipeline_only` verdict skips steps 1 and 2 and
calls the pipeline runner directly with `write=True`. The
cache is already strictly ahead of the cutoff; the
persist-skip trim will land Confluence at-cutoff.

## 6. Persist-skip-lag rule

Phase 6D-1's `persist_skip_bars=1` safety trims the final
trading bar from every persisted artifact. Therefore:

> **A pipeline write is useful AFTER a refresh only when
> `cache_date_range_end` is strictly greater than
> `current_as_of_date` (strict inequality, not `==`).**

If they are equal, the persist-skip trim lands Confluence
one trading bar behind the cutoff and the pipeline write
produces byte-identical persist-trimmed output — a waste
cycle that re-fires `pipeline_output_lags_persist_skip` on
the next watcher pass.

The cache-vs-cutoff watcher's `recommended_operator_action ==
"ready_for_pipeline_write"` verdict is the load-bearing
strict-inequality check. The writer trusts that verdict
verbatim; an operator should not bypass it.

## 7. StackBuilder policy

Saved stack variants under
`output/stackbuilder/<TICKER>/<seed_run>/` are **durable**
inputs to the daily automation. They are NOT daily-rebuilt
outputs. Carry-forward from Phase 6H-3 (verbatim):

  - **No monthly stale window. No 30-day age threshold.
    Stack age alone is never a block.**
  - A single saved variant is OK; the executor uses it
    directly (`stackbuilder_selection_policy =
    single_available_stack`).
  - Multiple variants with a clear newest-mtime are OK;
    the existing pipeline default
    (`discover_latest_stackbuilder_run` in
    `trafficflow_k_artifact_builder.py`) picks the newest
    by directory mtime. The executor preserves this
    behavior and reports
    `stackbuilder_selection_policy =
    latest_mtime_existing_pipeline_default`. A warning
    flags the multi-variant case for future operator
    review.
  - Tied newest-mtime is `ambiguous_tied_mtime` and
    blocks automation. The operator must pick a variant
    out of band. The writer refuses to choose; the planner
    returns `select_or_create_stackbuilder_stack_manual`.
  - **No StackBuilder run is ever invoked from the daily
    automation chain.** StackBuilder generation is a
    research-frequency manual workflow; same for OnePass.

## 8. Rollback / recovery guidance

Production cache, output, and signal-library trees are
NOT tracked by git. `git checkout`, `git reset`, and
`git revert` will NOT recover from a bad write.

  - **`project/cache/results/`** — Spymaster precomputed
    cache PKLs + manifest sidecars. Gitignored.
  - **`project/cache/status/`** — per-ticker status JSONs.
    Gitignored.
  - **`project/output/research_artifacts/`** — Phase 6D
    daily K, MTF K, and Confluence artifacts. Gitignored.
  - **`project/output/stackbuilder/`** — StackBuilder
    seed-run dirs. Gitignored.
  - **`project/signal_library/data/stable/`** —
    multi-timeframe library PKLs. Gitignored.

**Before any authorized run**, snapshot the four
operator-output roots that the writer can modify:
`--cache-dir`, `--status-dir`, `--artifact-root`, plus the
`--execution-log` file. A simple file copy or tarball is
sufficient. Keep the snapshot until the run's verdict has
been audited.

**After any authorized run**, read the execution log JSONL
row for the ticker. Confirm:

  - `refresh_result.succeeded == true` (if a refresh ran).
  - `post_refresh_watcher_action == "ready_for_pipeline_write"`
    (if a refresh ran AND the pipeline ran).
  - `pipeline_result.leader_eligible == true` (if the
    pipeline ran).
  - The new Confluence artifact's `last_date` matches the
    expected cutoff.

If anything looks wrong: **restore the snapshot before any
further write attempts.** Do not chain a "fix-up" rerun on
top of a suspect state.

**Do not attempt universe rollback.** Even a small
wildcard rerun can rewrite dozens of artifacts. There is
no batch rollback. Operate one ticker at a time.

## 9. Do-not-run warnings

  - **Never invoke any automation command with
    `--all-tickers`, `--universe`, or any wildcard
    pattern.** There is no universe sweep facility today
    by design. A wildcard rewrite of the saved catalogue
    is the single highest-blast-radius mistake the
    operator can make on this stack.

  - **Never invoke `daily_board_automation_writer.py
    --write` against production paths without the
    corresponding env var.** The CLI returns `rc=2` if
    you try, but treat the prompt itself as suspect if
    you find yourself attempting it.

  - **Never invoke
    `daily_board_automation_writer.py --write` without
    `--status-dir`, `--cache-dir`, `--artifact-root`,
    `--execution-log` all explicitly supplied.** Default
    paths point at production; the Phase 6H-6 plumbing
    exists precisely so these can be operator-named.

  - **Never run StackBuilder or OnePass as part of the
    daily automation.** They are stable manual research
    inputs. Re-running them silently rewrites
    operator-pinned research stacks.

  - **Never rebuild multi-timeframe libraries from the
    automation chain.** The planner reports
    `refresh_multitimeframe_libraries_manual` when they
    are missing; handle that out of band.

  - **Never schedule automated daily writes without first
    wiring the Phase 5C `validation_contract_v1` sidecar
    integration.** Phase 6H-6 doc § 8 names this as a
    remaining blocker.

## 10. Machine-readable companion

Every command template, every required env var, every
required flag, every prohibited pattern, and every expected
output path documented in this runbook is also encoded in
the sibling JSON manifest at:

```
project/md_library/shared/2026-05-12_PHASE_6H7_OPERATOR_COMMAND_MANIFEST.json
```

The manifest's `schema` is `phase_6h7_operator_command_manifest_v1`.
Downstream tools (future scheduler, audit dashboard) may
load it programmatically. The manifest is static; this
PR does not ship a runtime helper that reads it.

The Phase 6H-7 test
`test_scripts/test_phase_6h7_operator_command_manifest.py`
pins:

  - the JSON parses cleanly;
  - the schema label is correct;
  - the authorized writer command template contains every
    required root flag plus `--status-dir`;
  - the env var name and required value match the Phase
    6H-5 constants exactly;
  - the prohibited commands list flags StackBuilder
    execution, OnePass execution, multi-timeframe library
    rebuilds, and universe-sweep patterns;
  - no manifest command template references StackBuilder
    or OnePass execution paths.

If any of those drift the test breaks and the runbook +
manifest must be updated together.

## 11. Remaining blockers before scheduler / production automation

Carried verbatim from Phase 6H-6 § 8 (none of these are
blocked by Phase 6H-7 itself):

  - **Scheduler + retry policy.** UTC-aware; idempotent
    retries against the cache-vs-cutoff gate; back-off
    when the watcher repeatedly returns
    `pipeline_output_lags_persist_skip` (which is normal
    inside the persist-skip window every UTC day).
  - **Alerting.** Recurring
    `refresh_executed_pipeline_withheld` across more than
    one trading day for the same ticker should page
    operations (suggests yfinance access problems).
  - **Data licensing (Phase 5G).** Pre-launch gate; parked.
  - **Validation integration (Phase 5C).** Authorized
    writes should feed the `validation_contract_v1`
    sidecar pipeline that `honest_validation_ledger.py`
    aggregates.
  - **First authorized production rehearsal.** Even with
    Phase 6H-6 plumbing + Phase 6H-7 runbook, the first
    production-roots run should be a deliberate
    operator-supervised execution, not a scheduler
    invocation.

## 12. Reference paths

  - Machine-readable command manifest (this phase):
    `project/md_library/shared/2026-05-12_PHASE_6H7_OPERATOR_COMMAND_MANIFEST.json`
  - Manifest validation tests (this phase):
    `project/test_scripts/test_phase_6h7_operator_command_manifest.py`
  - Phase 6H-6 root plumbing doc:
    `project/md_library/shared/2026-05-12_PHASE_6H6_LIVE_WRITER_ROOT_PLUMBING.md`
  - Phase 6H-5 guarded write executor doc:
    `project/md_library/shared/2026-05-12_PHASE_6H5_GUARDED_WRITE_EXECUTOR_FOUNDATION.md`
  - Phase 6H-4 dry-run executor doc:
    `project/md_library/shared/2026-05-12_PHASE_6H4_DAILY_BOARD_AUTOMATION_DRY_RUN_EXECUTOR.md`
  - Phase 6H-3 automation preflight doc:
    `project/md_library/shared/2026-05-12_PHASE_6H3_DAILY_BOARD_AUTOMATION_PREFLIGHT.md`
  - Phase 6H-1 launch / design handoff doc:
    `project/md_library/shared/2026-05-12_PHASE_6H_DAILY_SIGNAL_BOARD_LAUNCH_HANDOFF.md`
  - Phase 6G-5 persist-skip-lag contract:
    `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md`
    § 6.8 and
    `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md`
    § 7.
  - Production code under audit (no changes in this PR):
    `project/daily_board_automation_writer.py`,
    `project/daily_board_automation_executor.py`,
    `project/daily_board_automation_preflight.py`,
    `project/cache_cutoff_watcher.py`,
    `project/signal_engine_cache_refresher.py`,
    `project/confluence_pipeline_runner.py`,
    `project/board_launch_readiness_audit.py`,
    `project/source_freshness_preflight.py`,
    `project/confluence_pipeline_readiness.py`.
