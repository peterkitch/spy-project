# Phase 6I-57 — Full-master ImpactSearch baseline preparation

**Verdict:** Implementation + tests + Gate 1 + Gate 2 **PASS**. The
full-master SPY checkpoint is **COMPLETE and audited PASS** (see
*SPY checkpoint — audited baseline* below). The remaining-five-
secondaries run is **NOT YET LAUNCHED** and is intentionally
deferred pending separate operator authorization.

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

## SPY checkpoint — audited baseline

Launched 2026-05-19T02:19:01Z, completed 2026-05-19T03:39:47Z.
Threaded `--use-multiprocessing` execution at 8 workers.

### Artifact
| field | value |
|---|---|
| artifact path | `output/impactsearch/SPY_analysis.xlsx` |
| artifact size | 5,569,554 bytes (5.31 MiB) |
| artifact sha256 | `d3c538452f9345902ba546e5f370e3857a5d155a8e14d3e80af353567c450b56` |
| manifest path | `output/impactsearch/SPY_analysis.xlsx.manifest.json` |
| manifest size | 2,478 bytes |
| HEAD used | `4663561` |

### Row + skip accounting
| field | value |
|---|---|
| rows produced | 35,576 |
| skipped (inferred) | 414 |
| stable-library universe | 35,990 |
| accounting | **35,576 + 414 = 35,990** ✓ |
| skip rate | 1.15 % (matches the 600-primary engine-vs-wrapper diagnostic) |
| skip reason class | `incomplete_calendar:insufficient` fastpath fallbacks, correctly suppressed by the zero-primary-yfinance gate |

### Workbook shape
| field | value |
|---|---|
| column count | 19 |
| column schema | standard ImpactSearch columns (`Primary Ticker, Resolved/Fetched, Library Source, Trigger Days, Wins, Losses, Win Ratio (%), Std Dev (%), Sharpe Ratio, t-Statistic, p-Value, Significant 90%, Significant 95%, Significant 99%, Avg Daily Capture (%), Total Capture (%), Requested Ticker, Data Source, Secondary Ticker`) |
| Data Source distribution | **FASTPATH only** (`{FASTPATH: 35,576}`) |
| Secondary Ticker distribution | `{SPY: 35,576}` |
| duplicate Primary Tickers | 0 |
| literal `NA` / `NAN` rows | both present and distinct under strict (`keep_default_na=False`) readback |

### Safety + validation surface
| field | value |
|---|---|
| `primary_yfinance_fetch_count` | **0** (`primary_yfinance_fetches=[]`) |
| `secondary_yfinance_fetch_count` | 1 (the single SPY secondary fetch) |
| runner stderr | 0 bytes |
| provenance mismatch warning count | 0 |
| cache-race / error count | 0 |
| `validation_mode` | `legacy_fast` |
| `durable_validation_ran` | **false** |
| `validation_status` | `not_run_manual_spymaster_audit` |
| `validation_sidecar_path` | `null` (no sidecar written) |
| runtime | **80.80 min** wall (`per-secondary elapsed_seconds = 4,839.70`); effective cores held ~0.98 throughout (ThreadPoolExecutor path saturates near one core, consistent with the scaling-ladder diagnostic) |

### Explicit validation note

**Durable validation was intentionally bypassed for this baseline.**
The stage-isolation diagnostic localized the prior terminal SPY
checkpoint's 20-hour pathology to durable validation fold/candidate
evaluation (Category B). Skipping that surface restores the fast
workbook-generation path. **Manual Spymaster review is the
validation surface for this baseline**; no
`validation_contract_v1` sidecar is written and the workbook
metadata records this state explicitly
(`validation_status = not_run_manual_spymaster_audit`).

### Diagnostic chain summary

  1. **Single-row SPY-vs-SPY check** — passed (1 row, primary YF 0).
  2. **50-primary canonical-output check** — passed
     (50 rows, primary YF 0, ~12 s wall).
  3. **Scaling ladder** — passed; threaded path delivers no
     parallel CPU speedup (eff cores ≤ 1.0) but per-primary cost
     at scale is ~0.14 s/primary, projecting full-universe to
     ~82 min via either runner or direct engine. Worker count
     (4 / 8 / 16) and snapshot env vars are not meaningful
     throttles.
  4. **Engine-vs-wrapper diagnostic** (600-primary representative
     random sample, seed=42) — passed; wrapper/export overhead
     measured at **~3.5 %** (82.42 s wrapper vs 79.64 s engine
     alone), classified **negligible**. Run 3 quarantine-overwrite
     test PASS (Run-2 workbook quarantined byte-identical; final
     canonical workbook fresh).
  5. **Full SPY production run** — passed with `primary_yfinance_fetch_count = 0` exactly, FASTPATH-only Data Source, and full row+skip accounting.

### Closeout note

The three superseded test-run quarantine folders
(`_quarantine_20260519T013811Z/`, `_quarantine_20260519T014052Z/`,
`_quarantine_20260519T021901Z/`) under `output/impactsearch/` were
removed during closeout after each quarantined `.xlsx` was
verified by SHA256 to be a non-match to the canonical full-baseline
`d3c538452...`. The canonical workbook and its manifest sidecar
were not touched.

The Phase 6I-57 benchmark scripts (`benchmark_impactsearch_fastpath.py`,
`benchmark_impactsearch_stage_isolation.py`,
`benchmark_impactsearch_scaling_ladder.py`) are now organized
under `test_scripts/benchmarks/` with a `README.md` describing
each script's question, command, output shape, and concurrency
safety.

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
