# Phase 6H-6 — Live-writer root plumbing + temp-dir authorized integration rehearsal

**Status:** plumbing landed. The Phase 6H-5 guarded write
path now exposes every refresher / pipeline output root
the operator might want to redirect, and a temp-dir
authorized integration rehearsal proves the redirection
works end-to-end. **No production writes were performed in
this PR.**

**Last updated:** 2026-05-12.

## 1. Why this phase

After Phase 6H-5 shipped the two-key authorized write
path, an audit flagged a subtle root-plumbing gap: the
writer was passing `cache_dir` to
`signal_engine_cache_refresher.refresh_signal_engine_cache`
but **not** `status_dir`. The refresher has three output
paths:

  - `cache_path` (under `cache_dir`)
  - `manifest_path` (co-located with `cache_path`, so also
    under `cache_dir`)
  - **`status_path` (under `status_dir`)** ← not redirectable

Without a `status_dir` override the refresher's status JSON
falls back to `project/cache/status/`. That means even a
temp-dir authorized rehearsal would have leaked a real
`<TICKER>_status.json` file into the production status
directory, defeating the point of the temp redirection.

Phase 6H-6 closes the gap and then proves it with a live
integration rehearsal that exercises the REAL refresher
(with a fake yfinance fetcher) against temp roots and
confirms no production path is touched.

## 2. Audit of every live-writer output root

### `signal_engine_cache_refresher.refresh_signal_engine_cache`

| Output | Redirected via | Pre-6H-6 | Post-6H-6 |
|---|---|---|---|
| Cache PKL (`<TICKER>_precomputed_results.pkl`) | `cache_dir` | ✓ passed through | ✓ |
| Manifest sidecar (`<TICKER>_precomputed_results.pkl.manifest.json`) | co-located with cache PKL, so `cache_dir` | ✓ implicit | ✓ implicit |
| **Status JSON (`<TICKER>_status.json`)** | **`status_dir`** | ✗ **NOT passed; refresher defaulted to production** | **✓ explicit override threaded** |
| Provenance helpers | n/a — pure metadata, no separate disk write | ✓ | ✓ |

### `confluence_pipeline_runner.run_confluence_pipeline_for_ticker`

| Output | Redirected via | Status |
|---|---|---|
| Phase 6D-1 daily K artifacts (`<artifact_root>/trafficflow/<TICKER>/...K<K>.research_day.json`) | `artifact_root` | ✓ passed through |
| Phase 6D-2 MTF K artifacts (`...K<K>__MTF.research_day.json`) | `artifact_root` | ✓ passed through |
| Phase 6D-3 Confluence MTF artifact (`<artifact_root>/confluence/<TICKER>/<TICKER>__MTF_CONSENSUS.research_day.json`) | `artifact_root` | ✓ passed through |
| Read-only inputs | `cache_dir`, `stackbuilder_root`, `signal_library_dir` | ✓ pass-through; never written by the runner |

The pipeline runner does **not** have any additional write
target. Its `stackbuilder_root` and `signal_library_dir`
inputs are read-only by design (the StackBuilder seed runs
and the multi-timeframe libraries are upstream research
artifacts the pipeline consumes; never modifies). These are
documented as read-only inputs and require no plumbing
change in this phase.

### Other writers — not touched by the Phase 6H-5 / 6H-6 contract

  - The Phase 6H-3 / 6H-4 read-only planners and the
    Phase 6H-2 watcher do not write to disk at all.
  - StackBuilder, OnePass, multi-timeframe library
    builders are explicitly out of scope for daily
    automation and are not invoked by the writer.

## 3. Implementation changes

`project/daily_board_automation_writer.py`:

  - `execute_daily_board_automation(...)` gains a new
    keyword argument `status_dir: Optional[Any] = None`,
    forwarded to `_execute_ticker(...)`.
  - `_execute_ticker(...)` gains the same keyword and
    forwards it as `status_dir=status_dir` in the
    refresher call inside the
    `refresh_source_cache_then_pipeline` branch.
  - CLI gains `--status-dir <path>` with a help string
    naming the Phase 6H-6 purpose. `argparse` resolves it
    to `args.status_dir`, which the CLI hands straight to
    `execute_daily_board_automation`.
  - Backward compatibility: when the operator omits
    `--status-dir`, the writer passes `status_dir=None` to
    the refresher, which the refresher interprets as its
    existing production default (`project/cache/status/`).
    No existing caller breaks.

No change to:

  - the two-key write authorization gate (`--write` + env
    var unchanged),
  - the refresh → recheck → pipeline sequencing,
  - the watcher-exception structured-outcome amendment from
    the Phase 6H-5 PR,
  - the StackBuilder multi-stack policy (saved variants are
    first-class; no age-based stale window; preserved
    verbatim from Phase 6H-3),
  - the dry-run default,
  - the forbidden-imports static guard,
  - any production module (`signal_engine_cache_refresher`,
    `confluence_pipeline_runner`, `cache_cutoff_watcher`,
    `confluence_pipeline_readiness`,
    `daily_board_automation_preflight`,
    `daily_signal_board`, etc.).

## 4. Test additions

`project/test_scripts/test_daily_board_automation_writer.py`:

Seven new tests pin the Phase 6H-6 surface (29 → 36 total):

  - `test_status_dir_is_forwarded_to_refresher` —
    explicit override reaches the refresher's call kwargs.
  - `test_status_dir_none_passes_none_to_refresher` —
    backward compat: omitted override forwards as `None`.
  - `test_watcher_exception_amendment_still_holds_with_status_dir`
    — Phase 6H-5 amendment regression (with `status_dir`
    threaded, the watcher-exception structured-outcome
    contract still holds).
  - `test_run_pipeline_only_path_unaffected_by_status_dir`
    — `status_dir` is a refresher-only knob; the pipeline
    runner's kwargs must not see it.
  - `test_status_dir_does_not_leak_into_dry_run` — dry-run
    path remains read-only even when `status_dir` is
    supplied.
  - `test_cli_status_dir_flag_round_trips` — full
    CLI → `execute_daily_board_automation` →
    `_execute_ticker` → refresher kwarg flow with
    `--status-dir`.
  - `test_authorized_integration_rehearsal_uses_temp_roots`
    — see § 5.

## 5. Temp-dir authorized integration rehearsal

`test_authorized_integration_rehearsal_uses_temp_roots`
exercises the **real**
`signal_engine_cache_refresher.refresh_signal_engine_cache`
(with a fake yfinance fetcher injected via the refresher's
existing `data_fetcher` knob) plus the **real**
`cache_cutoff_watcher.evaluate_cache_cutoff_state` plus a
**fake** pipeline runner that writes a sentinel artifact
to `artifact_root`. The rehearsal runs with
`write_authorized=True` and all four roots
(`cache_dir`, `status_dir`, `artifact_root`,
`execution_log_path`) pointing at `tmp_path`.

### Setup

  - Temp roots created via the standard `_layout` helper
    plus an explicit `tmp_path / "status"`.
  - Stale SPY cache pre-populated at
    `temp_cache_dir/SPY_precomputed_results.pkl` with
    `_last_date=2024-01-31` so the preflight emits
    `refresh_source_cache_then_pipeline`.
  - StackBuilder + multi-timeframe library fixtures so the
    preflight does not block.
  - Fake yfinance fetcher returns a 200-day business-day
    `DataFrame` ending `2026-05-12` (strictly past
    `current_as_of_date=2026-05-08`).
  - Fake pipeline runner writes
    `temp_artifact_root/confluence/SPY/SPY__MTF_CONSENSUS.research_day.json`
    with a `_phase_6h6_rehearsal_sentinel: true` payload.

### Live sequence exercised

  1. **Real refresher** is called with the temp `cache_dir`,
     the temp `status_dir`, and the fake `data_fetcher`.
     Runs the actual SMA optimizer over the fake 200-day
     dataset. Writes the cache PKL, the manifest sidecar,
     and the status JSON to the **temp roots only**.
  2. **Real watcher** is called with the temp `cache_dir`.
     Reads the freshly-written cache (last_date
     `2026-05-12`) and returns
     `ready_for_pipeline_write` because
     `2026-05-12 > current_as_of_date 2026-05-08`.
  3. **Fake pipeline runner** is called with the temp
     `artifact_root` (plus `cache_dir`,
     `stackbuilder_root`, `signal_library_dir` for context).
     Writes the sentinel artifact to the temp tree and
     returns a leader-eligible result.

### Assertions

  - `cache_pkl.exists()` at
    `temp_cache_dir/SPY_precomputed_results.pkl`.
  - `manifest.exists()` at
    `temp_cache_dir/SPY_precomputed_results.pkl.manifest.json`.
  - `status_json.exists()` at
    `temp_status_dir/SPY_status.json` — the load-bearing
    Phase 6H-6 assertion (this file would have leaked to
    production without the plumbing).
  - `sentinel_artifact.exists()` at
    `temp_artifact_root/confluence/SPY/SPY__MTF_CONSENSUS.research_day.json`.
  - Execution log JSONL has exactly one row with
    `final_recommended_action ==
    refresh_then_pipeline_executed`.
  - Pipeline call kwargs name the temp `artifact_root`,
    `cache_dir`, `stackbuilder_root`, `signal_library_dir`
    and `write=True`.
  - Five production roots (`project/cache/results/`,
    `project/cache/status/`,
    `project/output/research_artifacts/`,
    `project/signal_library/data/stable/`,
    `project/output/stackbuilder/`) had identical file
    inventories and mtimes before and after the rehearsal.
    The test snapshots all five and diffs added / removed /
    changed sets; any leak fails the assertion with a
    detailed message naming the offending root.

### Runtime

The rehearsal is bounded by the real SMA optimizer's
single-pass runtime over a 200-day dataset at the module
default `max_sma_day=30`. Empirically ~26 seconds on the
pinned `spyproject2` interpreter. The rehearsal is a
single pytest function with deterministic fixtures; it
runs as part of the writer's focused suite without
network or production access.

## 6. No production writes were performed

Verified during validation:

  - The rehearsal test snapshots five production roots
    before and after and asserts byte-identical state.
  - The fake `data_fetcher` never invokes `yfinance`; the
    real refresher imports `yfinance` only inside its
    default-fetcher resolver, which is bypassed when a
    `data_fetcher` is injected.
  - The fake pipeline runner writes only to the temp
    `artifact_root` supplied by the test.
  - No CLI invocation of `daily_board_automation_writer.py`
    with `--write` and the env var was performed against
    production paths during validation.
  - `git status` is clean after the focused 9-way and
    full regression runs.

## 7. CLI

```
python daily_board_automation_writer.py --ticker SPY \
    --dry-run

# Temp-dir authorized rehearsal recipe (manual / not run
# in this PR):
$ export PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit
$ python daily_board_automation_writer.py --ticker SPY \
    --write \
    --cache-dir   /tmp/spy_rehearsal/cache    \
    --status-dir  /tmp/spy_rehearsal/status   \
    --artifact-root /tmp/spy_rehearsal/output \
    --stackbuilder-root  project/output/stackbuilder \
    --signal-library-dir project/signal_library/data/stable \
    --execution-log /tmp/spy_rehearsal/exec_log.jsonl \
    --current-as-of-date 2026-05-08
```

Note that the operator MAY point `--stackbuilder-root` and
`--signal-library-dir` at the production trees because
those are read-only inputs the pipeline runner consumes
but never modifies. The cache PKL, manifest, status JSON,
and confluence artifact all land under the explicit temp
roots.

## 8. Remaining blockers before scheduler / production automation

None of the following are blocked by Phase 6H-6 itself;
they are the still-open items from Phase 6H-5 § 10 carried
forward verbatim:

  - **Scheduler + retry policy.** UTC-aware; idempotent
    retries against the cache-vs-cutoff gate; back-off
    when the watcher repeatedly returns
    `pipeline_output_lags_persist_skip` (which is normal
    inside the persist-skip window every UTC day).
  - **Alerting.** Recurring
    `refresh_executed_pipeline_withheld` across more than
    one trading day for the same ticker should page
    operations (suggests yfinance access problems).
  - **Data licensing (Phase 5G).** Pre-launch gate;
    parked. Required before any commercial framing of
    automated daily writes.
  - **Validation integration (Phase 5C).** Authorized
    writes should feed the `validation_contract_v1`
    sidecar pipeline that `honest_validation_ledger.py`
    aggregates, so the audit trail is mechanically
    cross-checked.
  - **First authorized production rehearsal.** Even with
    Phase 6H-6 plumbing, the first production-roots run
    should be a deliberate operator-supervised execution,
    not a scheduler invocation.

## 9. Reference paths

  - Writer module (extended this phase):
    `project/daily_board_automation_writer.py`
  - Writer tests (extended this phase):
    `project/test_scripts/test_daily_board_automation_writer.py`
  - Phase 6H-5 guarded-write executor doc:
    `project/md_library/shared/2026-05-12_PHASE_6H5_GUARDED_WRITE_EXECUTOR_FOUNDATION.md`
  - Phase 6H-4 dry-run executor doc:
    `project/md_library/shared/2026-05-12_PHASE_6H4_DAILY_BOARD_AUTOMATION_DRY_RUN_EXECUTOR.md`
  - Phase 6H-3 automation preflight doc:
    `project/md_library/shared/2026-05-12_PHASE_6H3_DAILY_BOARD_AUTOMATION_PREFLIGHT.md`
  - Phase 6E-5 source refresher:
    `project/signal_engine_cache_refresher.py`
  - Phase 6D-4 pipeline runner:
    `project/confluence_pipeline_runner.py`
  - Phase 6H-2 cache-vs-cutoff watcher:
    `project/cache_cutoff_watcher.py`
