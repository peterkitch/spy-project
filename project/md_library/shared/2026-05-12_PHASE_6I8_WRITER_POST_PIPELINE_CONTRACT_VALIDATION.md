# Phase 6I-8 — Writer post-pipeline contract validation

**Status:** writer module amended + tests extended + this
doc. **Not** a production-authorization phase: the
existing Phase 6H-5 two-key writer gate is unchanged.

**Last updated:** 2026-05-12.

## 0. Scope statement

  - **No production writes.** No `cache/`, `output/`,
    `signal_library/`, or `stackbuilder/` byte changed.
  - **No source refresh** against production paths.
  - **No production pipeline write.**
  - **No StackBuilder / OnePass / ImpactSearch /
    TrafficFlow / Spymaster batch execution.**
  - **No yfinance fetch.**
  - **No writer CLI invocation with production roots.**
  - Temp-dir authorized rehearsals are allowed only with
    fake injected callables and temp roots; the new
    rehearsal added by this phase uses a fake pipeline
    runner but invokes the **real** Phase 6I-1
    validator against tmp_path roots only, and
    snapshots production roots before/after to assert
    byte-identical state.
  - StackBuilder policy carried forward verbatim: saved
    variants durable, multiple stacks allowed, no
    age-based stale rule. The Phase 6I-4 audit's static
    guard against age substrings continues to enforce
    the contract at the audit layer.

## 1. Why this exists

Phase 6H-5 / 6H-6 / 6H-7 shipped the guarded write
executor. The pipeline runner's `return` was the
end-of-live-path; downstream consumers (operator audit,
JSONL log, ranking emitter) had to re-validate the saved
artifacts themselves.

Phase 6I-1 shipped
`confluence_ranking_contract_validator.validate_confluence_ranking_contract`
— a read-only seven-contract checker that walks the
saved cache → StackBuilder → daily K → MTF → Confluence
→ readiness → board-row chain and reports whether the
data shape is intact.

Phase 6I-8 wires the validator as the **default
post-pipeline gate**: after every authorized pipeline
write that returns normally, the writer invokes the
validator read-only, captures the verdict, and
downgrades the execution's `final_recommended_action`
when the contract chain is not intact. The pipeline side
effect is preserved on disk; the operator-facing
routing surfaces the contract failure so the next
consumer doesn't treat the ticker as ready.

This is not a production-authorization phase. The
existing two-key gate (`--write` flag +
`PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` env
var) is untouched. The writer still requires both keys
before any refresh / pipeline call fires. This phase
only adds an extra **read-only check** at the end of
the live path.

## 2. What landed

### 2.1 New module additions (`daily_board_automation_writer.py`)

  - **`ContractValidationOutcome` dataclass** — carries
    every field the spec required:
    - `attempted`, `succeeded`.
    - Seven Phase 6I-1 contract booleans:
      `cache_contract_ok`,
      `stackbuilder_contract_ok`,
      `daily_k_contract_ok`, `mtf_contract_ok`,
      `confluence_contract_ok`,
      `readiness_contract_ok`, `board_row_contract_ok`.
    - Verdict fields: `leader_eligible`,
      `ranking_blocked_reason`,
      `recommended_next_operator_action`,
      `issue_codes`, `blocking_reasons`,
      `confluence_last_date`, `daily_k_coverage`,
      `mtf_k_coverage`, `elapsed_seconds`.
  - **New stable issue codes:**
    - `ISSUE_POST_PIPELINE_CONTRACT_INVALID = "post_pipeline_contract_invalid"`.
    - `ISSUE_POST_PIPELINE_CONTRACT_VALIDATION_EXCEPTION = "post_pipeline_contract_validation_exception"`.
  - **New final-action constants:**
    - `FINAL_PIPELINE_EXECUTED_CONTRACT_INVALID = "pipeline_executed_contract_invalid"`.
    - `FINAL_REFRESH_THEN_PIPELINE_EXECUTED_CONTRACT_INVALID = "refresh_then_pipeline_executed_contract_invalid"`.
  - **Lazy default resolver:**
    `_default_contract_validator_callable()` lazily
    imports
    `confluence_ranking_contract_validator.validate_confluence_ranking_contract`.
    Resolved ONLY on the live post-pipeline validation
    path.
  - **New helper functions:**
    - `_contract_validation_outcome_from_validation(validation, elapsed)`
      maps a Phase 6I-1 verdict onto the outcome
      dataclass; `succeeded` is True iff every contract
      is OK; `issue_codes` prepends the writer-side
      `post_pipeline_contract_invalid` code on failure.
    - `_contract_validation_outcome_for_exception(elapsed)`
      builds the exception outcome with
      `attempted=True, succeeded=False, issue_codes=(post_pipeline_contract_validation_exception,)`.
    - `_run_post_pipeline_contract_validation(base, ...)`
      mutates the per-ticker execution in place:
      invokes the validator, builds the outcome,
      updates `issue_codes`, downgrades
      `final_recommended_action` when contract-invalid
      or exception.

### 2.2 API changes

`execute_daily_board_automation` and `_execute_ticker`
gain a `contract_validator: Optional[Callable[..., Any]] = None`
parameter. Defaults to `None`; resolved lazily inside
`_run_post_pipeline_contract_validation` via the
default-resolver helper. Tests inject fakes.

`TickerWriteExecution` gains a
`contract_validation_result: Optional[ContractValidationOutcome]`
field. `None` everywhere the validator did NOT run
(dry-run / unauthorized / waiting / manual / blocked /
watcher-blocked / pipeline-exception).

`DailyBoardWriteExecutionReport` gains two aggregate
bucket fields:

  - `contract_validated_tickers` — tickers whose
    validator ran AND every contract passed.
  - `contract_invalid_tickers` — tickers whose
    validator ran AND at least one contract failed OR
    the validator itself raised.

JSON serialization is updated:

  - Per-execution `to_json_dict` carries a
    `contract_validation_result` key (or `null`).
  - Aggregate `to_json_dict` carries
    `contract_validated_tickers` and
    `contract_invalid_tickers` lists.
  - Append-only JSONL execution log rows carry the
    same `contract_validation_result` key.

### 2.3 Invocation contract

The validator fires **if and only if**:

  - The write authorization succeeded
    (`write_authorized=True`).
  - The initial planner verdict is one of the two
    actionable paths (`run_pipeline_only` or
    `refresh_source_cache_then_pipeline`).
  - For the refresh+pipeline path: the watcher recheck
    returned `ready_for_pipeline_write`.
  - The pipeline runner returned WITHOUT raising
    (tracked via a `pipeline_returned_cleanly` boolean
    in `_execute_ticker`).

The validator does NOT fire on:

  - Dry-run / unauthorized paths.
  - Skipped paths: `already_current`, `waiting`,
    `manual`, `blocked`.
  - Watcher-blocked-after-refresh (refresh wrote;
    pipeline withheld).
  - Pipeline exception (no on-disk state to validate).

### 2.4 Outcome routing

  - **Contract passes** (every one of the seven
    booleans `True`):
    - `contract_validation_result.succeeded = True`.
    - `final_recommended_action` is the canonical
      `pipeline_executed` / `refresh_then_pipeline_executed`.
    - Ticker surfaces under
      `contract_validated_tickers`.
  - **Contract returns invalid** (at least one False):
    - `contract_validation_result.succeeded = False`.
    - `final_recommended_action` downgrades to
      `pipeline_executed_contract_invalid` /
      `refresh_then_pipeline_executed_contract_invalid`.
    - `issue_codes` gains
      `post_pipeline_contract_invalid` (plus the Phase
      6I-1 validator's per-row codes via the outcome's
      `issue_codes` field).
    - Ticker surfaces under `contract_invalid_tickers`.
    - **Refresh / pipeline side effects are NOT
      reverted.** The pipeline ran; the artifacts are
      on disk; the routing carries the contract gap
      forward for operator inspection.
  - **Validator raises:**
    - `contract_validation_result.attempted = True`,
      `succeeded = False`.
    - `ranking_blocked_reason = "validation_exception"`.
    - `recommended_next_operator_action = "manual_review_required"`.
    - `issue_codes = (post_pipeline_contract_validation_exception,)`.
    - Final action downgraded the same way as
      contract-invalid.
    - Ticker surfaces under `contract_invalid_tickers`.
    - The validator NEVER throws out of the writer.

## 3. Tests (`test_daily_board_automation_writer.py`)

47 tests total (36 carried + 11 new Phase 6I-8 tests).
Three previously-passing tests gained an injected
`contract_validator=_passing_validator()` so their
pre-Phase-6I-8 `final_recommended_action` assertions
still pin the pipeline-orchestration contract without
being polluted by the new validator-default behavior.

### 3.1 New helper fakes

  - `_FakeValidation` mirrors the Phase 6I-1 verdict
    shape; defaults to all-OK.
  - `_validator_factory(recorder, result=None)` returns
    a fake that records calls and returns the given
    `_FakeValidation`.
  - `_passing_validator()` returns a silent all-OK
    validator for tests that don't care about
    validator behavior.

### 3.2 New Phase 6I-8 tests

  1. `test_phase_6i8_dry_run_does_not_call_validator`
     — dry-run path: validator NEVER resolved or
     invoked. A raise-on-call validator is injected to
     pin this.
  2. `test_phase_6i8_unauthorized_write_does_not_call_validator`
     — `write_authorized=False` with an actionable
     plan: validator NEVER resolved or invoked.
  3. `test_phase_6i8_run_pipeline_only_calls_pipeline_then_validator`
     — pipeline_only authorized path: call ordering
     pin: `[pipeline_runner, contract_validator]`.
  4. `test_phase_6i8_refresh_then_pipeline_call_order`
     — refresh+pipeline authorized path: call ordering
     pin: `[refresher, watcher, pipeline_runner,
     contract_validator]`.
  5. `test_phase_6i8_watcher_blocked_after_refresh_skips_validator`
     — watcher non-ready verdict: pipeline withheld,
     validator NEVER fires.
  6. `test_phase_6i8_pipeline_exception_skips_validator`
     — pipeline runner raises: validator NEVER fires.
  7. `test_phase_6i8_contract_invalid_validator_result_is_structured`
     — Confluence-contract failure from the validator:
     verdict captured, final action downgraded to
     `pipeline_executed_contract_invalid`, issue codes
     include both `post_pipeline_contract_invalid` and
     the Phase 6I-1 per-row code, JSON serialization
     round-trips.
  8. `test_phase_6i8_validator_exception_is_structured`
     — validator raises: outcome carries
     `post_pipeline_contract_validation_exception`
     issue code; final action downgraded; ticker in
     `contract_invalid_tickers`.
  9. `test_phase_6i8_execution_log_jsonl_includes_validation`
     — JSONL execution log row carries the
     `contract_validation_result` payload with every
     subfield.
  10. `test_phase_6i8_writer_module_does_not_top_level_import_validator`
      — AST scan of `daily_board_automation_writer.py`
      top-level imports. Blocks
      `confluence_ranking_contract_validator`,
      `signal_engine_cache_refresher`,
      `confluence_pipeline_runner`, `yfinance`,
      `subprocess`. Confirms the validator default
      resolves only through the lazy default-resolver
      path inside the function body.
  11. `test_phase_6i8_temp_dir_authorized_rehearsal_with_real_validator`
      — temp-dir authorized integration rehearsal.
      Drives the writer against tmp_path roots with a
      fake pipeline runner but the **real** Phase 6I-1
      validator. The tmp_path fixture is missing daily
      K / MTF / Confluence chains, so the real
      validator returns contract-invalid; the writer
      captures the verdict, downgrades the action,
      surfaces the ticker under
      `contract_invalid_tickers`. Snapshots production
      roots (`cache/results/`, `cache/status/`,
      `output/research_artifacts/`,
      `signal_library/data/stable/`,
      `output/stackbuilder/`) before and after; asserts
      byte-mtime-identical state — proving no
      production path is mutated.

### 3.3 Adapted carry-forward tests

  - `test_refresh_then_pipeline_runs_pipeline_when_watcher_ready`
    — injects `contract_validator=_passing_validator()`
    so the pre-6I-8 `final_recommended_action` assertion
    against `FINAL_REFRESH_THEN_PIPELINE_EXECUTED`
    remains valid.
  - `test_run_pipeline_only_executes_pipeline_once`
    — same pattern.
  - `test_authorized_integration_rehearsal_uses_temp_roots`
    (the Phase 6H-6 rehearsal) — same pattern: the
    fake pipeline writes only a sentinel artifact so
    the real validator would otherwise fail the full-
    chain check; the all-OK fake validator keeps the
    test focused on Phase 6H-6's writer / refresher /
    watcher / pipeline sequencing.

## 4. Validation captured at module land

  - `py_compile` clean on
    `daily_board_automation_writer.py` and the test
    file.
  - **`test_daily_board_automation_writer.py`: 47
    passed in 53.34 s** (36 carried + 11 new).
  - **Focused 5-way (writer + validator + pipeline
    runner + automation preflight + queue planner):
    152 passed in 61.38 s.**
  - `git diff --check` clean.

## 5. Confirmation no production writes were run

Four independent checks:

1. **Top-level import guard.** The writer module's
   top-level AST imports only `cache_cutoff_watcher`,
   `confluence_pipeline_readiness`,
   `daily_board_automation_preflight`. The validator
   is imported lazily inside
   `_default_contract_validator_callable`, which only
   resolves on the live path. The static guard test
   pins this.
2. **No-call assertion fakes.** Five tests inject a
   raise-on-call validator (or pipeline runner) on
   paths where neither should fire (dry-run /
   unauthorized / watcher-blocked / pipeline-
   exception). Any future regression that fires the
   validator on those paths immediately fails the
   test.
3. **Temp-dir rehearsal snapshots.** The Phase 6I-8
   rehearsal snapshots every production root before
   and after the test; asserts byte-mtime-identical.
4. **Default writer CLI behavior unchanged.** The
   existing two-key auth gate (`--write` +
   `PRJCT9_AUTOMATION_WRITE_AUTH`) is untouched; the
   validator only fires when the gate is already
   satisfied AND the pipeline returned cleanly.

## 6. This is not a production authorization phase

  - The two-key gate is unchanged.
  - The Phase 6H-7 production runbook is unchanged.
  - No new CLI flag controls validator execution; it
    is automatic ON the live path, and never fires
    elsewhere.
  - The writer's no-yfinance / no-subprocess / no-live-
    engine contract is unchanged.
  - StackBuilder durability is unchanged: saved
    variants are durable; multiple variants per ticker
    are first-class; tied newest-mtime blocks; **no
    age window**.
  - The Phase 6H-7 runbook + Phase 6H-5 writer + two-
    key auth gate remain the only path to a production
    write. Phase 6I-8 is a **read-only post-condition
    check** on top of that path.

## 7. Future work (named, not implemented here)

  - **Phase 6I-9 or operational variant** — supervised
    first authorized production run with the new
    contract-validation gate engaged. Phase 5G data
    licensing + Phase 5C validation integration +
    scheduler + alerting on `contract_invalid_tickers`
    + `post_pipeline_contract_validation_exception`.
  - **Spymaster master-audit UI integration** (Phase
    6I-7 backend is unchanged; the audit panel will
    surface the new `contract_validation_result` on
    refresh).
  - **Aggregate Confluence p-value** — explicit
    future gap from Phase 6I-2 § 4.3 / § 6.2.

## 8. Reference paths

### Modified / new files (this PR)

  - `project/daily_board_automation_writer.py`
    (modified; +~250 lines: dataclass + helpers +
    invocation wiring + lazy resolver + serializer
    updates).
  - `project/test_scripts/test_daily_board_automation_writer.py`
    (modified; +~750 lines: 11 new tests + helper
    fakes + minimal adaptation of 3 carry-forward
    tests).
  - `project/md_library/shared/2026-05-12_PHASE_6I8_WRITER_POST_PIPELINE_CONTRACT_VALIDATION.md`
    (this doc).

### Modules consumed (read-only)

  - `confluence_ranking_contract_validator` (Phase
    6I-1) — `validate_confluence_ranking_contract`
    only, lazily imported.

### Cross-references

  - Phase 6I-1 validator:
    `project/md_library/shared/2026-05-12_PHASE_6I1_CONFLUENCE_RANKING_CONTRACT_VALIDATOR.md`.
  - Phase 6H-7 production runbook:
    `project/md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`
    (the operator stack the new gate fits into; the
    two-key writer authorization is unchanged).
  - Phase 6I-7 Spymaster master-audit surface:
    `project/md_library/shared/2026-05-12_PHASE_6I7_SPYMASTER_MASTER_AUDIT_SURFACE.md`
    (downstream consumer; sees the new
    `contract_validation_result` in the report's
    execution rows once the writer is invoked
    authorized).
