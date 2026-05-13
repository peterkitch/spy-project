# 2026-05-13 — Phase 6I-14: sprint state + next-run handoff (post Phase 6I-13)

## 0. Purpose

A single short document a future agent (or operator) can read
before deciding whether to attempt the next supervised
SPY writer run. **The previous attempt (Phase 6I-13) did not
fire the writer because the read-only preconditions failed at
the gate.** This handoff codifies the exact checklist that
must pass before any future `--write` invocation, the one
operational condition that opens the gate, and the explicit
"do not run yet" list.

This doc is companion to (not a replacement for)
`project/CLAUDE.md` § 6, which is the source-of-truth sprint
state.

## 1. What is proven (after Phase 6I-13)

Closed by Phase 6I-11's first supervised authorized SPY writer
run + the post-Phase-6I-12 instrumentation:

  - **`real_authorized_writer_run` — CLOSED.** The Phase 6H-5
    two-key gate (`--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=
    phase_6h5_explicit`) was exercised end-to-end against
    production roots; `write_authorized=true`, `dry_run=
    false`, valid JSON to stdout, single well-formed JSONL
    row, `rc=0`, no `SystemExit` leak.
  - **`real_signal_engine_cache_refresher_invocation` — CLOSED.**
    The writer-internal refresher callable
    `signal_engine_cache_refresher.refresh_signal_engine_cache`
    actually ran as an **in-process Python function call**
    (recorded in `functions_executed`; the writer has **no
    subprocess path**) and advanced the SPY cache
    `date_range_end` from `2026-05-11` to `2026-05-12`.
    `commands_executed` is a logical/audit command label;
    `functions_executed` is the runtime proof.
  - **Persist-skip-lag contract works as designed.** When
    the refresher advanced the cache to a date that
    **equals** the cutoff (rather than strictly past it),
    the post-refresh watcher returned
    `pipeline_output_lags_persist_skip` and the writer
    correctly withheld the pipeline. `pipeline_result=null`,
    `contract_validation_result=null`,
    `final_recommended_action="refresh_executed_pipeline_withheld"`,
    `skipped_reason="watcher_blocked_pipeline_after_refresh"`.
    Inventory diff: surgically narrow (3 files in
    `cache/results/` + `cache/status/`; 0 changes across
    `output/research_artifacts/`, `signal_library/data/stable/`,
    `output/stackbuilder/`).
  - **Provider-fetch telemetry is instrumented across four
    surfaces** (Phase 6I-12):
      - refresher result JSON
      - refresher per-ticker status JSON (write runs only)
      - writer stdout JSON
      - writer JSONL execution-log row
    All four carry the same JSON shape (no field drift);
    pinned by regression test.
  - **Flow-audit recommendation-text four-case selector works
    live** (Phase 6I-12 Scope A). The Phase 6I-13 probes
    triggered case 3 ("Do NOT authorize the writer now ...
    No read-only stage failed — this is an operator-action
    signal, not a regression."), confirming the wording fix
    is no longer falsely blaming stages.

## 2. What is still NOT directly proven

Carry-forward from Phase 6I-12 with no change after Phase 6I-13:

  - **`real_confluence_pipeline_runner_write` — STILL OPEN.**
    No production pipeline write has ever fired from the
    writer's path. Closes on a future supervised run where
    `cache_date_range_end > resolved current_as_of_date`
    strictly (see § 4).
  - **`real_post_pipeline_validation_on_writer_path` — STILL
    OPEN.** The Phase 6I-8 post-pipeline contract-validation
    callable cannot fire until the pipeline actually runs;
    closes on the same future condition as the pipeline-runner
    gap.
  - **`real_yfinance_fetch` direct fetch-call telemetry —
    INSTRUMENTED, AWAITING CAPTURE.** The Phase 6I-12
    `ProviderFetchTelemetry` payload is wired through every
    surface, but no fetcher call has fired since the
    instrumentation landed. The next supervised run that
    actually invokes the refresher will populate the
    `provider_fetch_telemetry` block on all four surfaces.
    HTTP-level provider telemetry remains a deliberate
    non-goal of this sprint.

## 3. Read-only preconditions before any future writer authorization

**All five preconditions below must pass on read-only probes
captured immediately before any `--write` attempt.** If any
fails, the attempt halts and produces a docs-only branch
recording the verdict — no `PRJCT9_AUTOMATION_WRITE_AUTH` is
set and no writer is invoked. This is exactly the discipline
Phase 6I-13 followed.

The five probes (run from `project/` with the pinned
`spyproject2` interpreter):

  1. `cache_cutoff_watcher.py --ticker SPY`
  2. `daily_board_supervised_run_gate.py --ticker SPY --top-n 3`
  3. `daily_board_flow_integrity_audit.py --ticker SPY --top-n 3`
  4. `daily_board_automation_writer.py --ticker SPY --dry-run`
  5. `confluence_ranking_contract_validator.py --ticker SPY`

Preconditions:

| # | Required | Probe |
|---|---|---|
| 1 | gate `safe_to_authorize_writer_now == true` | supervised gate |
| 2 | gate `authorization_candidate_tickers` contains the target ticker | supervised gate |
| 3 | writer dry-run `initial_recommended_action` is `run_pipeline_only` OR `refresh_source_cache_then_pipeline` | writer dry-run |
| 4 | flow audit: `all_read_only_checks_passed == true` (all 6 stages pass) AND `production_roots_untouched == true` | flow audit |
| 5 | contract validator: all 7 booleans `true` (cache / stackbuilder / daily_k / mtf / confluence / readiness / board_row); `recommended_next_operator_action` is NOT a manual/blocker action | contract validator |

## 4. The exact condition that opens the gate

```
cache_date_range_end > resolved current_as_of_date
```

**Strictly greater**, not equal. The cutoff resolver
(`confluence_pipeline_readiness.resolve_current_as_of_date`)
returns the most-recent-weekday strictly before UTC now —
i.e. the resolved cutoff trails the wall clock by at least
one trading day, so a fresh authorized refresh that lands
yesterday's close in the cache **does not** open the gate
(cache equals cutoff). What opens the gate is a refresh that
lands a trading day **strictly past the resolved cutoff** —
which in practice means a refresh after the next U.S. market
close advances the cutoff resolver's view of "most recent
weekday strictly before UTC now."

**Wall-clock advance alone does not open the gate.** Two
distinct things must happen, in this order:

  1. The wall clock advances past the next U.S. market close
     so the cutoff resolver returns a new
     `current_as_of_date`.
  2. An authorized refresh advances `cache_date_range_end`
     **strictly past** that new cutoff.

After step 2, all five preconditions in § 3 should evaluate
positively for SPY (assuming no regression in upstream
trio / StackBuilder / signal library state).

## 5. The exact conditions that must remain false

The next supervised attempt must halt — no `--write`, no env
var, no writer invocation — if any of the following is true
at probe time:

  - **Any probe reports manual/blocker status.** E.g. gate
    returns `manual_review_required`, `resolve_stackbuilder_inputs`,
    `fix_upstream_inputs`, `build_missing_downstream_artifacts`,
    or `wait_for_cache_ahead_of_cutoff` instead of
    `authorize_guarded_writer_for_selected_tickers`. The
    last (`wait_for_cache_ahead_of_cutoff`) is the
    current-state-as-of-Phase-6I-13 verdict and the most
    likely block; it is by-design and not a regression.
  - **Unsafe gate.** `gate.safe_to_authorize_writer_now ==
    false` for any reason.
  - **Missing/failing validator contracts.** Any of the
    seven `*_contract_ok` booleans is `false` on the
    contract validator output. Even one `false` halts the
    attempt because the writer's post-pipeline validator
    will then surface
    `ISSUE_POST_PIPELINE_CONTRACT_INVALID` on the run.
  - **Production-root snapshot changes during read-only
    probes.** The flow audit snapshots
    `cache/results/`, `cache/status/`,
    `output/research_artifacts/`, `signal_library/data/stable/`,
    `output/stackbuilder/` before/after the audit and asserts
    `production_roots_untouched == true`. If that flag
    flips `false`, the probes themselves are leaking writes
    — a regression that must be diagnosed before any
    authorized writer run.
  - **`PRJCT9_AUTOMATION_WRITE_AUTH` is already set in the
    shell.** It must be set inside a one-shot launcher
    script with a `try/finally` that removes it after the
    invocation, not persistently in the operator's
    environment.

## 6. Do Not Run Yet — explicit list

Per the Phase 6I-14 spec, the following five rules are the
hard "do not run" list a future agent must honor:

  - **Do not authorize writer** if
    `gate.safe_to_authorize_writer_now` is `false`.
  - **Do not authorize writer** if SPY (or whichever
    ticker the operator targets) is not in
    `gate.authorization_candidate_tickers`.
  - **Do not authorize writer** if writer dry-run
    `initial_recommended_action` is
    `wait_for_cache_ahead_of_cutoff` (or any other
    non-actionable / manual / blocker action).
  - **Do not authorize writer** if flow audit reports
    `production_roots_untouched == false`.
  - **Do not authorize writer** if the contract validator
    reports any of the seven contract booleans (`cache`,
    `stackbuilder`, `daily_k`, `mtf`, `confluence`,
    `readiness`, `board_row`) as `false`.

**Time-passage discipline:** the next supervised writer
attempt must not be initiated merely because the wall clock
advanced. It must be backed by a fresh set of the five
read-only probes captured immediately before the attempt,
with the five-precondition checklist explicitly evaluated
against those probe outputs and recorded in the next
supervised-run evidence doc.

## 7. Reference paths

  - **Source-of-truth sprint state:**
    `project/CLAUDE.md` § 6 (read this first).
  - **Phase 6I-13 evidence note** (most recent attempt;
    preconditions failed):
    `project/md_library/shared/2026-05-13_PHASE_6I13_SUPERVISED_SPY_PIPELINE_VALIDATION_EVIDENCE.md`
  - **Phase 6I-12 instrumentation** (`ProviderFetchTelemetry`
    + flow-audit four-case wording):
    `project/md_library/shared/2026-05-13_PHASE_6I12_PROVIDER_FETCH_TELEMETRY_AND_FLOW_AUDIT_WORDING.md`
  - **Phase 6I-11 first authorized SPY writer run:**
    `project/md_library/shared/2026-05-12_PHASE_6I11_SUPERVISED_SPY_WRITER_EVIDENCE_RUN.md`
  - **Phase 6I-10 flow integrity audit** (evidence matrix +
    the five `known_simulated_or_inferred_steps`):
    `project/md_library/shared/2026-05-12_PHASE_6I10_END_TO_END_FLOW_EVIDENCE_AUDIT.md`
  - **Phase 6H-5 guarded writer foundation** (two-key gate):
    `project/md_library/shared/2026-05-12_PHASE_6H5_GUARDED_WRITE_EXECUTOR_FOUNDATION.md`
  - **Phase 6H-7 production runbook** (operator command
    manifest):
    `project/md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`
  - **Persist-skip-lag contract:**
    `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md` § 6.8
    `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md` § 7
