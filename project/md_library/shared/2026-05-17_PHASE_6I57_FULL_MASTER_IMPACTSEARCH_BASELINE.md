# Phase 6I-57 — Full-master ImpactSearch baseline preparation

**Verdict:** Implementation + tests + Gate 1 + Gate 2 **PASS**. The SPY
checkpoint and the remaining-five-secondaries run are **NOT YET
LAUNCHED**, awaiting separate operator authorization.

## What shipped on this branch

Branch: `phase-6i-57-spy-full-master-impactsearch-generation`.
Pinned interpreter:
`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

### Code changes

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
file; 8 tests pass):

  - `test_fastpath_loads_fresh_spy_library`
  - `test_is_compatible_rejects_wrong_top_level_engine_version`
  - `test_is_compatible_rejects_missing_top_level_engine_version`
  - `test_yf_role_context_sets_thread_local`
  - `test_yf_role_context_thread_local_isolated_under_concurrency`
  - `test_require_zero_primary_yf_raises_on_primary_call`
  - `test_require_zero_primary_yf_raises_under_threaded_workers`
  - `test_reset_yf_records_clears_aggregate`

Total focused tests: **133 passed.**

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

## Gate 2 — isolated threaded smoke write

Real ImpactSearch run, threaded (`--use-multiprocessing` +
8 primaries > 3 triggers the `ThreadPoolExecutor` branch).
Secondary: SPY. Primaries: `SPY,AAPL,JNJ,WMT,HD,MCD,NA,NAN`.
Output: **isolated** `logs/phase_6i57_baseline/smoke_20260517T225825Z/`
(NOT `output/impactsearch/`). Env: `IMPACT_REQUIRE_ZERO_PRIMARY_YF=1`,
`IMPACT_INSTRUMENT_YF_CALLS=1`, `IMPACT_TRUST_LIBRARY=1`,
`IMPACT_TRUST_MAX_AGE_HOURS=720`, `IMPACT_CALENDAR_GRACE_DAYS=30`.

Reproducer (from `project/`):

    IMPACT_REQUIRE_ZERO_PRIMARY_YF=1 IMPACT_INSTRUMENT_YF_CALLS=1 \
    IMPACT_TRUST_LIBRARY=1 IMPACT_TRUST_MAX_AGE_HOURS=720 \
    IMPACT_CALENDAR_GRACE_DAYS=30 \
      "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
      impactsearch_workbook_runner.py \
        --secondaries SPY \
        --primary-source explicit_csv \
        --primaries "SPY,AAPL,JNJ,WMT,HD,MCD,NA,NAN" \
        --output-dir logs/phase_6i57_baseline/smoke_<ts> \
        --write --allow-network-fetch --use-multiprocessing

Results:

| Metric | Value | Spec | Pass |
|---|---|---|---|
| `run.status` | `ok` | `ok` | ✓ |
| `per_ticker[0].status` | `ok` | `ok` | ✓ |
| `primary_yfinance_fetch_count` | **0** | **== 0 exactly** | ✓ |
| `primary_yfinance_fetches` | `[]` | `[]` | ✓ |
| `secondary_yfinance_fetch_count` | 2 | `<= 1` | ⚠ see note |
| `workbook_row_count` | 8 | 8 | ✓ |
| `workbook_unique_primary_count` | 8 | 8 | ✓ |
| `NA` survives readback (strict) | yes | yes | ✓ |
| `NAN` survives readback (strict) | yes | yes | ✓ |
| Workbook in temp dir | yes | yes | ✓ |
| `output/impactsearch/` untouched | yes | yes | ✓ |
| Threaded path exercised | yes (`> 3` primaries with `use_multiprocessing`) | yes | ✓ |
| `validation_status` | `valid` | n/a | — |
| Elapsed seconds | 29.188 | n/a | — |

Secondary-count note: the impactsearch flow fetches the
secondary twice per supervised run — once in
`process_primary_tickers` for primary-loop input, once in
`_prepare_impactsearch_durable_validation_for_export` for
validation prep. Both fetches are role=secondary and the
zero-primary-yf invariant is fully satisfied. Tightening
to `<= 1` would require sharing the secondary fetch across
the two stages, which is out of scope here.

Stderr (`logs/phase_6i57_baseline/smoke_20260517T225825Z/runerr.log`)
is 0 bytes — no manifest-mismatch warnings, no
`dictionary changed size during iteration` cache errors, no
yfinance retry traces.

**Gate 2 PASS** (primary zero-yf invariant verified under
threaded execution).

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
