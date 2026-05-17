# Phase 6I-57 — Full-master ImpactSearch baseline preparation

**Verdict:** Implementation + tests + Gate 1 + Gate 2 **PASS**. The SPY
checkpoint and the remaining-five-secondaries run are **NOT YET
LAUNCHED**, awaiting separate operator authorization.

## What shipped on this branch

Branch: `phase-6i-57-spy-full-master-impactsearch-generation`.
Pinned interpreter:
`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

### Code changes

  0. **Fastpath-fallback skip when zero-yf gate is armed**
     in `impactsearch.py:_impactsearch_primary_signal_series_for_secondary`.
     When the zero-primary-yfinance gate
     (`IMPACT_REQUIRE_ZERO_PRIMARY_YF=1`) is armed and
     `get_primary_signals_fast` returns
     `(None, fp_reason)` (e.g. `incomplete_calendar:...`
     on a delisted/stale-calendar ticker that has a
     signal-library `.pkl` but whose library_end is too
     far before the secondary effective end-date), the
     helper now **skips** the primary BEFORE the slow
     path would call `fetch_data_raw` and populates
     `rejection_out` with the new structured reason
     `PROCESS_FASTPATH_FALLBACK_SKIPPED_ZERO_YF_GATE`.
     The primary produces no row for this secondary;
     the threaded worker propagates the rejection
     dict to `_record_recent_error(_format_rejection(...))`.
     Existing slow-path behavior is preserved
     byte-identical when the gate is **not** armed.
     This is what unblocks the SPY checkpoint: ASHTF /
     U1P.F / ZYN-style stale tickers no longer try to
     fall back to a yfinance fetch the gate would then
     kill mid-run.
  1. **Manifest verification fix** in
     `signal_library/impact_fastpath.py` and `impactsearch.py`.
     The fastpath previously passed
     `requested_params={'engine_version': ENGINE_VERSION, ...}` to
     `provenance_manifest.load_verified_signal_library`, which makes
     the verifier look for `manifest.params.engine_version`. The fresh
     OnePass manifests store `engine_version` at the **top level**
     (alongside `content_hash` / `build_timestamp`), not inside
     `manifest.params`. The verifier therefore rejected every fresh
     library with `[('params.engine_version', '<missing>', '1.0.0')]`
     and the fastpath disabled itself for every primary, falling back
     to yfinance. Top-level engine_version is still checked
     downstream by `_is_compatible` in `signal_library/impact_fastpath.py`
     against `lib["engine_version"]`, so the integrity guard is
     preserved. `MAX_SMA_DAY` and `price_source` DO live inside
     `manifest.params` per the OnePass writer, so those stay.
     **Note (follow-up):** `onepass.py:1377` has the same buggy
     loader-call shape; per scope it is left unchanged in this
     phase.
  2. **Thread-safe yfinance role attribution** in `impactsearch.py`.
     New `_YfRoleContext` (a threading-local context manager),
     `_YF_CALL_RECORDS` (lock-protected list), `_YF_LOCK`,
     `reset_yf_records()`, and `get_yf_records()`. The existing
     `_wrapped_download` instrumentation now calls a new
     `_record_yf_call(ticker_arg)` that reads the per-thread role
     and appends a record under `_YF_LOCK`. The wrapper installs
     whenever **either** `IMPACT_INSTRUMENT_YF_CALLS=1` **or**
     `IMPACT_REQUIRE_ZERO_PRIMARY_YF=1` is set (was previously
     gated by `IMPACT_INSTRUMENT_YF_CALLS` only). Primary fetch
     sites at `process_single_ticker` and secondary fetch sites
     at `process_primary_tickers`, `process_primary_tickers_aggregate_mode`,
     and `fetch_data_with_close` are wrapped with the role
     context.
  3. **Hard zero-primary-yf gate**
     (`IMPACT_REQUIRE_ZERO_PRIMARY_YF=1`). When armed, any
     primary-role yfinance fetch raises `RuntimeError` at the
     impactsearch wrapper, with the offending ticker + stage in
     the message. The runner also enforces the same gate at the
     per-secondary boundary: if it observes any primary fetch
     records and the env flag is set, the result is downgraded to
     `status="failed"` with first-offender tickers listed in the
     `reason` field.
  4. **Runner instrumentation extensions**
     (`impactsearch_workbook_runner.py`):
       - Per-secondary `primary_yfinance_fetch_count`,
         `secondary_yfinance_fetch_count`,
         `primary_yfinance_fetches` (list of role-attributed
         records).
       - Per-secondary `workbook_row_count` and
         `workbook_unique_primary_count` derived from a strict
         read (`keep_default_na=False, na_values=[]`) so the
         operator-curated `NA` / `NAN` tickers survive readback.
       - Top-level `run_started_at_utc`, `run_ended_at_utc`,
         `run_elapsed_seconds`, `run_elapsed_minutes`.
       - Per-secondary timing fields (`start_timestamp`,
         `end_timestamp`, `elapsed_seconds`, `elapsed_minutes`,
         `workbook_size_bytes`, `manifest_size_bytes`).
       - `quarantine_report` field on each per-secondary result
         describing whether existing `<SECONDARY>_analysis*`
         files were moved.
  5. **Clean-write protection** in
     `impactsearch_workbook_runner.quarantine_existing_outputs_for_secondary()`.
     Before each per-secondary write, moves any pre-existing
     workbook + sidecar + runner-partial artifacts for that
     secondary into a sibling `_quarantine_<YYYYmmddTHHMMSSZ>/`
     folder under the output dir. Unsafe / empty secondaries are
     refused. Unrelated tickers' artifacts are untouched.
  6. **`master_tickers_file` primary source** (already on this
     branch pre-prompt): adds enum value, resolver branch with
     raw-string parse preserving `NA` / `NAN`, CLI flag
     `--primary-tickers-file`, default
     `global_ticker_library/data/master_tickers.txt`, and
     wires the path through the plan policy + command manifest.

### New tests

`test_scripts/test_impactsearch_workbook_runner.py` (existing
file; 125 tests pass) gains:

  - `test_quarantine_helper_moves_existing_outputs`
  - `test_quarantine_helper_noop_when_no_existing_outputs`
  - `test_quarantine_helper_rejects_unsafe_secondary`
  - `test_execute_workbook_run_quarantines_existing_outputs`
  - `test_runner_passes_through_zero_primary_yfinance_when_records_empty`
  - `test_runner_records_primary_and_secondary_fetches_via_impactsearch_seam`
  - `test_runner_zero_yf_gate_marks_failure_when_primary_fetch_observed`

`test_scripts/test_phase_6i57_fastpath_and_yf_gate.py` (new
file; 11 tests pass — 8 original + 3 for the
fastpath-fallback skip amendment):

  - `test_fastpath_loads_fresh_spy_library`
  - `test_is_compatible_rejects_wrong_top_level_engine_version`
  - `test_is_compatible_rejects_missing_top_level_engine_version`
  - `test_yf_role_context_sets_thread_local`
  - `test_yf_role_context_thread_local_isolated_under_concurrency`
  - `test_require_zero_primary_yf_raises_on_primary_call`
  - `test_require_zero_primary_yf_raises_under_threaded_workers`
  - `test_reset_yf_records_clears_aggregate`
  - `test_fastpath_fallback_skipped_when_zero_yf_gate_armed`
  - `test_fastpath_fallback_not_skipped_when_zero_yf_gate_disarmed`
  - `test_threaded_fastpath_fallback_skip_no_primary_yf_records`

Total focused tests: **136 passed.**

## Gate 1 — direct fastpath sampler

Read-only sampler. Calls `get_primary_signals_fast(ticker,
secondary_calendar)` directly for 317 representative tickers
drawn from `signal_library/data/stable/` (35,990-ticker
universe). Does NOT call yfinance. Reproducer:
`C:/Users/sport/AppData/Local/Temp/gate1_sampler_6i57.py`.

| Metric | Value |
|---|---|
| Sample size | 317 |
| Successes | 314 |
| Fastpath successes | 314 |
| Failures | 3 |
| Pass percentage | 99.05% |
| Elapsed seconds | 30.58 |
| `NA` in sample, succeeded | yes |
| `NAN` in sample, succeeded | yes |

Failures are all `incomplete_calendar` (legitimate — those
tickers' libraries end >40 days before the secondary's effective
end-date and would normally trigger the slow-path yfinance
fetch). Tickers: `ASHTF` (lib_end `2026-03-19`), `U1P.F`
(lib_end `2026-02-20`), `ZYN` (lib_end `2025-12-31`).

Sample composition: 8 pilots (SPY,AAPL,JNJ,WMT,HD,MCD,NA,NAN) +
first 25 alphabetically + last 25 alphabetically + ~200 evenly
spaced across the folder + foreign-suffix examples
(`.KS,.HK,.KL,.NS,.L,.F,.DE,.BO`) + U/V/W-range examples from
the stalled-run area.

Evidence JSONL:
`project/logs/phase_6i57_baseline/gate1_sample_1779058675.jsonl`
(gitignored).

**Gate 1 PASS.**

## Gate 2 — isolated threaded smoke write (amended)

Real ImpactSearch run, threaded (`--use-multiprocessing` plus
8+ primaries triggers impactsearch's `len(primary_tickers) > 3`
`ThreadPoolExecutor` branch at `impactsearch.py:3711`).
Secondary: SPY. Output: **isolated** `logs/phase_6i57_baseline/smoke_<ts>/`
(NOT `output/impactsearch/`). Env: `IMPACT_REQUIRE_ZERO_PRIMARY_YF=1`,
`IMPACT_INSTRUMENT_YF_CALLS=1`, `IMPACT_TRUST_LIBRARY=1`,
`IMPACT_TRUST_MAX_AGE_HOURS=720`, `IMPACT_CALENDAR_GRACE_DAYS=30`.

### Secondary YF fetch count is `2` by design (this phase)

The pass condition `secondary_yfinance_fetch_count == 2` for this
phase is **expected and documented**:

  - **one role=secondary fetch** in
    `process_primary_tickers` (`impactsearch.py:3442`) — provides
    `sec_df` for the primary loop.
  - **one role=secondary fetch** in
    `_prepare_impactsearch_durable_validation_for_export` via
    `fetch_data_with_close` (`impactsearch.py:5657`) — provides the
    secondary returns frame for the validation-prep stage that
    writes the sidecar manifest.

Both are role=secondary. The safety-critical invariant is
`primary_yfinance_fetch_count == 0` **exactly**. Sharing the
secondary fetch across the two stages would be a separate
optimization phase (out of scope here).

### Round 1 — pilot-only (smoke_20260517T225825Z)

Primaries: `SPY,AAPL,JNJ,WMT,HD,MCD,NA,NAN`.

| Metric | Value | Pass |
|---|---|---|
| `primary_yfinance_fetch_count` | **0** | ✓ |
| `primary_yfinance_fetches` | `[]` | ✓ |
| `secondary_yfinance_fetch_count` | 2 | ✓ (expected, see above) |
| `workbook_row_count` | 8 | ✓ |
| `workbook_unique_primary_count` | 8 | ✓ |
| `NA` / `NAN` survive strict readback as distinct rows | yes / yes | ✓ |
| `output/impactsearch/` untouched | yes | ✓ |
| Elapsed seconds | 29.19 | — |

### Round 2 — pilot + 3 fastpath-fallback stale-calendar tickers (smoke_20260517T232033Z)

Primaries:
`SPY,AAPL,JNJ,WMT,HD,MCD,NA,NAN,ASHTF,U1P.F,ZYN`.

The 3 stale tickers (`ASHTF`, `U1P.F`, `ZYN`) trigger
`get_primary_signals_fast` to return
`(None, "incomplete_calendar:insufficient (lib_end=YYYY-MM-DD + 30d
< sec_eff_end=YYYY-MM-DD)")`. With
`IMPACT_REQUIRE_ZERO_PRIMARY_YF=1` armed, the helper at
`_impactsearch_primary_signal_series_for_secondary`
(`impactsearch.py:2906`) now skips the primary **before** the slow
path would call `fetch_data_raw`, populates `rejection_out` with
the structured reason
`PROCESS_FASTPATH_FALLBACK_SKIPPED_ZERO_YF_GATE`, and returns
`(None, rejection)`. The threaded worker propagates the rejection
to `_record_recent_error(_format_rejection(...))` in
`process_primary_tickers`. The primary produces no row in the
output workbook.

| Metric | Value | Pass |
|---|---|---|
| `primary_yfinance_fetch_count` | **0** | ✓ |
| `primary_yfinance_fetches` | `[]` | ✓ |
| `secondary_yfinance_fetch_count` | 2 | ✓ (expected) |
| `workbook_row_count` | 8 | ✓ |
| `workbook_unique_primary_count` | 8 | ✓ |
| Healthy primaries (8) produce rows | yes | ✓ |
| `ASHTF` absent from workbook (skipped) | yes | ✓ |
| `U1P.F` absent from workbook (skipped) | yes | ✓ |
| `ZYN` absent from workbook (skipped) | yes | ✓ |
| Skip reasons recorded in `rejection_out` -> `recent_errors` | yes | ✓ |
| `NA` / `NAN` survive strict readback as distinct rows | yes / yes | ✓ |
| `output/impactsearch/` untouched | yes | ✓ |
| Elapsed seconds | 29.02 | — |

Reproducer (from `project/`):

    IMPACT_REQUIRE_ZERO_PRIMARY_YF=1 IMPACT_INSTRUMENT_YF_CALLS=1 \
    IMPACT_TRUST_LIBRARY=1 IMPACT_TRUST_MAX_AGE_HOURS=720 \
    IMPACT_CALENDAR_GRACE_DAYS=30 \
      "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
      impactsearch_workbook_runner.py \
        --secondaries SPY \
        --primary-source explicit_csv \
        --primaries "SPY,AAPL,JNJ,WMT,HD,MCD,NA,NAN,ASHTF,U1P.F,ZYN" \
        --output-dir logs/phase_6i57_baseline/smoke_<ts> \
        --write --allow-network-fetch --use-multiprocessing

**Gate 2 amended PASS.** Primary zero-yf invariant verified under
threaded execution with both healthy and fastpath-fallback
primaries in the same run.

### Threaded test coverage of the skip path

  - `test_threaded_fastpath_fallback_skip_no_primary_yf_records`
    spawns 8 worker threads calling
    `_impactsearch_primary_signal_series_for_secondary`
    concurrently with the gate armed and the fastpath stubbed
    to return `(None, "incomplete_calendar:...")`. Every call
    produces a structured rejection, no
    `fetch_data_raw` call is attempted, and no role=primary
    record appears in `get_yf_records()`. Synthetic stand-in
    for the threaded production path; no real network.

## SPY checkpoint command (NOT YET LAUNCHED)

Awaiting separate operator authorization. From `project/`:

    IMPACT_INSTRUMENT_YF_CALLS=1 IMPACT_REQUIRE_ZERO_PRIMARY_YF=1 \
    IMPACT_TRUST_LIBRARY=1 IMPACT_TRUST_MAX_AGE_HOURS=720 \
    IMPACT_CALENDAR_GRACE_DAYS=30 \
      "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
      impactsearch_workbook_runner.py \
        --secondaries SPY \
        --primary-source signal_library_dir \
        --signal-library-dir signal_library/data/stable \
        --write --allow-network-fetch --use-multiprocessing \
        > logs/phase_6i57_baseline/SPY_checkpoint_<ts>.stdout.json \
        2> logs/phase_6i57_baseline/SPY_checkpoint_<ts>.stderr.log

Notes:

  - The runner will automatically quarantine any
    pre-existing `output/impactsearch/SPY_analysis*` files
    into `output/impactsearch/_quarantine_<ts>/` immediately
    before the write, so a stale-Jan workbook will NOT
    contaminate the fresh write.
  - The hard zero-primary-yf gate is armed in this command;
    any primary fetch will fail the run loudly rather than
    silently writing a contaminated workbook.

## What stayed out of scope

Per the operator's narrowed-scope instruction:

  - Full progress JSONL streaming. The runner already writes
    a full per-secondary result JSON to stdout; per-primary
    progress JSONL streaming is deferred.
  - Broad evidence polish.
  - Cache-race regression beyond the existing snapshot
    discipline. The smoke run did NOT surface a
    `dictionary changed size during iteration` error
    (stderr is 0 bytes), so the existing snapshot path on
    the cache writer appears safe for the SPY shape; if
    larger-universe runs surface the race a follow-up phase
    will land a focused fix.
  - `onepass.py` left unchanged (same loader-call shape at
    `onepass.py:1377` recorded as a follow-up finding).
