# TrafficFlow runner Phase E PR Delta: first real canonical-write smoke (SPY, AAPL)

**Evidence-only PR.** No code changes. No test changes. This document
records the result of the first real canonical-write smoke through the
Phase E runner + orchestrator stack delivered by PRs #317 / #318 /
#319 / #320 and their amendments.

The smoke targets exactly SPY and AAPL at K=1..6 only. A single
orchestrator invocation drove two real worker subprocesses through
`trafficflow_runner.py --canonical-write`, each of which executed the
real lazy `trafficflow.build_board_rows` compute path. Real canonical
artifacts landed under `output/trafficflow/runs/<UTC_TIMESTAMP>/` and
`output/trafficflow/selected_output.json` was created at the canonical
location.

## 1. Scope and Non-Goals

**In scope.**

- Two secondaries only: SPY and AAPL.
- K=1..6 only.
- One orchestrator invocation.
- `--workers 2`.
- Fresh canonical run root: `output/trafficflow/runs/<UTC_TIMESTAMP>/`.
- Real worker subprocesses invoked by `trafficflow_canonical_orchestrator.py`.
- Real `trafficflow_runner.py --canonical-write` workers.
- Real `trafficflow.build_board_rows` compute via the lazy worker path.
- Pre/post canonical safety snapshots covering every gated tree.
- Privacy scan across all 19 canonical JSON outputs.

**Out of scope.**

- The other six PRJCT9 secondaries (AMZN, GOOGL, META, MSFT, NVDA, TSLA).
  Deferred to PR Epsilon (all-8-secondary K=1..6 canonical smoke).
- K > 6 and heavy-stage compute. Deferred to Phase F+.
- Code or test changes.
- Multiple orchestrator invocations.
- `--resume` behavior.
- `--allow-partial-publish` behavior.
- Any amendment implementation. If a runner or orchestrator bug
  surfaces it is documented as a finding here; the amendment is a
  follow-up PR.

## 2. References

- PR #317 Phase E canonical-write contract scoping.
- PR #318 Phase E PR Alpha CLI guardrails.
- PR #319 Phase E PR Beta canonical writer mechanics + amendment.
- PR #320 Phase E PR Gamma orchestrator/finalizer module + amendment.
- PR #316 at-scale performance inference (used to bound per-cell
  timing sanity).
- PR #308 network/cache-write block (must hold through canonical
  writes; verified end-to-end here by canonical safety pre/post).

## 3. Test Suite Re-Run Confirmation

Pre-smoke baseline, on `main` at the PR #320 merge commit:

```
<PINNED_INTERPRETER> -m pytest test_scripts/test_trafficflow_runner.py -q
96 passed in 3.58s

<PINNED_INTERPRETER> -m pytest test_scripts/test_trafficflow_canonical_orchestrator.py -q
35 passed in 1.25s
```

131 targeted tests pass before the smoke begins.

## 4. Pre-Run Canonical Safety Snapshot

Snapshot saved at `<SESSION_DIR>/preflight/pre_run_snapshot.json`.

Key directory file counts captured before the smoke:

| Tree | File count |
| --- | --- |
| `output/stackbuilder/` | 5388 |
| `output/impactsearch/` | 16 |
| `output/onepass/` | 2 |
| `output/trafficflow/` | absent |
| `output/validation/` | 0 |
| `signal_library/data/stable/` | 71980 |
| `cache/results/` | 3305 |
| `cache/status/` | 1667 |
| `price_cache/daily/` | 12 |

`output/trafficflow/` did not exist pre-run. This is the first real
canonical write; there is no prior run root and no prior
`selected_output.json`.

Pre-run SHA-256 captured for: SPY and AAPL `selected_build.json`;
both `combo_leaderboard.xlsx`; all 12 `combo_k=1..6.json` files
(6 per secondary); `output/onepass/onepass.xlsx`; SPY/AAPL
`price_cache/daily/*.csv`; the union of 15 unique member PKLs
referenced by SPY/AAPL K=1..6 combos and their `.manifest.json`
sidecars.

15 member PKLs were present and OK. Preflight returned verdict
`ALL_ELIGIBLE_DRY_RUN` for SPY/AAPL K=1..6 with
`would_refresh_pkls=[]` and `would_refresh_prices=[]`.

## 5. Smoke Invocation

Sanitized command shape:

```
<PINNED_INTERPRETER> trafficflow_canonical_orchestrator.py \
    --secondaries SPY,AAPL \
    --k-range 1,2,3,4,5,6 \
    --stackbuilder-root output/stackbuilder \
    --output-dir output/trafficflow/runs/<UTC_TIMESTAMP> \
    --workers 2 \
    --runner trafficflow_runner.py
```

**No** `--resume`, `--allow-partial-publish`, `--heavy-stage`,
`--explicit-build`, `--refresh-missing-pkls`, `--refresh-stale-prices`,
or `--allow-network-fetch` was passed. `PARALLEL_SUBSETS` was not set.

Run root: `output/trafficflow/runs/<UTC_TIMESTAMP>/`.

| | UTC |
| --- | --- |
| Orchestrator start | 2026-05-25T05:43:18Z |
| Orchestrator end | 2026-05-25T05:43:38Z |

| Field | Value |
| --- | --- |
| Wall-clock elapsed | 20.17 s |
| Exit code | 0 |
| Stdout | parses cleanly as the orchestrator summary JSON |
| Stderr | 0 lines |

Stdout and stderr captured under
`<SESSION_DIR>/orchestrator_run/`.

## 6. Orchestrator Output Summary

From the orchestrator's final stdout summary (sanitized,
JSON-parsed):

- `schema_version` = `trafficflow_canonical_orchestrator_v1`
- `run_status` = `complete`
- `totals` = `{total_secondaries: 2, pending: 0, in_progress: 0,
  complete: 2, failed: 0, skipped_resume: 0}`
- `selected_output_updated` = `true`
- `elapsed_seconds` = 20.17
- `artifacts_written` contains:
  - `output/trafficflow/runs/<UTC_TIMESTAMP>/progress.json`
  - `output/trafficflow/runs/<UTC_TIMESTAMP>/run_status.json`
  - `output/trafficflow/runs/<UTC_TIMESTAMP>/run_manifest.json`
  - `output/trafficflow/selected_output.json`

## 7. Per-Secondary Artifact Verification Table

| Secondary | Files in `<SEC>/` | `.done` zero-byte | `.quarantine/<SEC>/` |
| --- | --- | --- | --- |
| SPY | 14 (12 board_rows + secondary_manifest + .done) | yes | absent |
| AAPL | 14 (12 board_rows + secondary_manifest + .done) | yes | absent |

The 14 files per secondary are: `board_rows_k=1..6.json`,
`board_rows_k=1..6.csv`, `secondary_manifest.json`, and zero-byte
`.done`. No extra files, no `.tmp` siblings, no `.quarantine`
directory anywhere under `<RUN_ROOT>`.

## 8. Per-K Board Row Verification Table

Each cell produced 1 board row. JSON and CSV row counts match
(CSV row count excludes the header).

| Secondary | K | JSON rows | CSV rows | elapsed_seconds |
| --- | --- | --- | --- | --- |
| SPY  | 1 | 1 | 1 | 0.304 |
| SPY  | 2 | 1 | 1 | 0.749 |
| SPY  | 3 | 1 | 1 | 1.009 |
| SPY  | 4 | 1 | 1 | 2.005 |
| SPY  | 5 | 1 | 1 | 3.201 |
| SPY  | 6 | 1 | 1 | 7.124 |
| AAPL | 1 | 1 | 1 | 0.446 |
| AAPL | 2 | 1 | 1 | 0.577 |
| AAPL | 3 | 1 | 1 | 1.060 |
| AAPL | 4 | 1 | 1 | 2.437 |
| AAPL | 5 | 1 | 1 | 3.827 |
| AAPL | 6 | 1 | 1 | 8.742 |

Sums: SPY K=1..6 = 14.39 s. AAPL K=1..6 = 17.09 s. Total serial
work = 31.48 s. Observed orchestrator wall-clock = 20.17 s with
`--workers 2`. Speedup ratio = 1.56x, well within the bound of the
two-worker fan-out (effective parallelism limited by the K=6
tail-latency cell on each secondary).

PR #314 K=1..6 per-secondary mean was about 12.66 s. Both SPY and
AAPL here fall within the 3x bound used as the timing-sanity gate;
no timing finding raised.

## 9. Run-Level File Verification

Run-level files present and parseable at `<RUN_ROOT>`:

- `progress.json` (`trafficflow_canonical_orchestrator_v1`)
- `run_status.json` (`trafficflow_canonical_orchestrator_v1`)
- `run_manifest.json` (`trafficflow_runner_phase_e_v1`)

Verification checks (80 total, 0 failed; full results at
`<SESSION_DIR>/verification/run_verification.json`):

- `progress.json` shows both SPY and AAPL at `status=complete`,
  `done_marker_present=true`, `quarantine_present=false`,
  `k_completed=[1,2,3,4,5,6]`, `k_failed=null`, `failure_kind=null`.
- `progress.json.totals` = `{total_secondaries: 2, complete: 2,
  failed: 0, pending: 0, in_progress: 0, skipped_resume: 0}`.
- `run_status.json.run_status=complete`,
  `secondaries_complete={SPY, AAPL}`, `secondaries_failed=[]`,
  `secondaries_skipped_resume=[]`.
- `run_manifest.json.run_status=complete`, `inputs.secondaries`
  contains SPY and AAPL, `inputs.k_range=[1,2,3,4,5,6]`,
  `inputs.workers=2`, `inputs.resume=false`,
  `inputs.heavy_stage=false`, `canonical_artifacts_referenced`
  has both SPY and AAPL entries, `per_secondary_summary` has 2
  complete entries, `quarantined_secondaries=[]`.
- `run_manifest.json.artifacts_written` contains all four of
  `progress.json`, `run_status.json`, `run_manifest.json`, and
  `selected_output.json` (PR #320 Amendment 4 holding under real
  smoke).

## 10. selected_output.json Verification

`output/trafficflow/selected_output.json` is new (no prior file
existed). It contains:

- `schema_version` = `trafficflow_canonical_orchestrator_v1`
- `selected_run_root_path` = `output/trafficflow/runs/<UTC_TIMESTAMP>`
  (sanitized repo-relative)
- `selected_run_id` = `<UTC_TIMESTAMP>`
- `run_status` = `complete`
- `totals` = `{total_secondaries: 2, complete: 2, failed: 0,
  pending: 0, in_progress: 0, skipped_resume: 0}`

No `selected_output.json.tmp` sibling remained anywhere under
`output/trafficflow/`; the write completed atomically.

## 11. Selected-Build Provenance Verification

`run_manifest.canonical_artifacts_referenced` carries the
selected-build provenance for each secondary. SHA-256 of the
worker-recorded `selected_build_sha256` matches the pre-run
snapshot SHA-256 of the on-disk `selected_build.json` exactly for
both SPY and AAPL. The worker-written
`<SEC>/secondary_manifest.json.selected_build_sha256` also matches.
`explicit_build_override` is `false` for both. `selected_build_path`
and `selected_run_dir` are sanitized repo-relative POSIX strings.

## 12. Canonical Safety Pre/Post Verification

Full pre/post diff at
`<SESSION_DIR>/verification/canonical_safety_diff.json`.

Every gated directory is byte-identical pre/post:

| Tree | Pre file count | Post file count | Unchanged |
| --- | --- | --- | --- |
| `output/stackbuilder/` | 5388 | 5388 | yes |
| `output/impactsearch/` | 16 | 16 | yes |
| `output/onepass/` | 2 | 2 | yes |
| `output/validation/` | 0 | 0 | yes |
| `signal_library/data/stable/` | 71980 | 71980 | yes |
| `cache/results/` | 3305 | 3305 | yes |
| `cache/status/` | 1667 | 1667 | yes |
| `price_cache/daily/` | 12 | 12 | yes |

Every tracked input SHA-256 is unchanged: SPY/AAPL
`selected_build.json`, both `combo_leaderboard.xlsx`, all 12
`combo_k=1..6.json` files, `output/onepass/onepass.xlsx`, all 15
member PKLs, and `price_cache/daily/SPY.csv` and
`price_cache/daily/AAPL.csv` (byte-identical SHA + size + mtime).

The only delta in the entire repo working tree (outside session
evidence under `<SESSION_DIR>`) is in `output/trafficflow/`:

- `output/trafficflow/runs/<UTC_TIMESTAMP>/` is new (28 files under
  per-secondary directories + 3 run-level files = 31 files).
- `output/trafficflow/selected_output.json` is new.

PR #308 network/cache-write block held end-to-end. The runner did
not write to `price_cache/`, `cache/results/`, `cache/status/`, or
`signal_library/`. The runner did not fetch from the network.

## 13. Atomic Write Pattern Verification

- No `*.tmp` files found anywhere under
  `output/trafficflow/runs/<UTC_TIMESTAMP>/`.
- No `*.tmp` files found anywhere under `output/trafficflow/`.
- Both per-secondary `.done` files are zero-byte and were written
  after all `board_rows_k=*.json/.csv` plus `secondary_manifest.json`
  per the PR #319 contract.

## 14. Privacy Sanitization Verification Across 19 JSON Files

Scanned files (19 total):

- `<RUN_ROOT>/progress.json`
- `<RUN_ROOT>/run_status.json`
- `<RUN_ROOT>/run_manifest.json`
- `output/trafficflow/selected_output.json`
- `<RUN_ROOT>/SPY/secondary_manifest.json`
- `<RUN_ROOT>/AAPL/secondary_manifest.json`
- `<RUN_ROOT>/SPY/board_rows_k=1..6.json` (6 files)
- `<RUN_ROOT>/AAPL/board_rows_k=1..6.json` (6 files)
- `<SESSION_DIR>/orchestrator_run/orchestrator_stdout.json`

Scanned for: drive-letter pattern `[A-Za-z]:[\/]`, absolute local
paths, and the project denylist tokens (case-insensitive).

Result: 0 leaks across 19 files. Every path-typed value is either
a repo-relative POSIX path or the redaction sentinel; no embedded
absolute paths and no denylist tokens. The orchestrator's
`sanitize_for_json` layer and the worker's
`_scrub_embedded_absolute_paths` gate are both functioning as
designed under the real canonical write path.

## 15. Findings

No critical findings. No amendment-blocking findings. No
performance findings.

Observed values worth recording for the next phase:

- Wall-clock 20.17 s for SPY+AAPL K=1..6 at `--workers 2`. This is
  consistent with the at-scale model in PR #316 evidence and
  scales the orchestrator pool model to two-worker fan-out cleanly.
- Per-cell timings within the historical PR #314 / PR #315 band;
  no cell exceeded the 3x sanity bound.
- Every PR #319 amendment ((1) pre-loop eligibility validation,
  (2) fail-closed process-conflict semantics for canonical writes,
  (3) failure-message path scrubbing) is exercised at compile time
  here even though no failure path was taken in this smoke. The
  worker contract delivered the expected on-disk state for both
  successful secondaries.
- Every PR #320 amendment ((1) resume retry quarantine clearing,
  (2) unreadable-progress fail-closed refusal, (3) worker
  failure_kind propagation, (4) complete `artifacts_written`
  inventory) is checked indirectly here. Amendment 4 in particular
  is directly verified: the on-disk
  `run_manifest.json.artifacts_written` contains all four
  expected entries, and matches the orchestrator stdout summary
  exactly.

## 16. Recommendation

**PASS.** First real canonical-write smoke through the Phase E
runner + orchestrator stack succeeded with zero findings.

Phase E may proceed to **PR Epsilon**: the all-8-secondary
(AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY, TSLA) K=1..6 canonical
smoke. The runner and orchestrator behavior under this two-
secondary case is exactly what the PR #317 contract and the
PR #319/#320 amendments specified. No code changes are needed
before broadening to all 8 secondaries.

PR Epsilon should keep the same shape (one orchestrator
invocation, `--canonical-write` worker subprocesses, real
`build_board_rows` compute, pre/post canonical safety snapshots,
privacy scan across the per-secondary canonical JSON set) and
extend only the secondary set. `--workers` should remain at 2
unless the PR #316 efficiency model is re-validated for higher
worker counts on this machine.

All session evidence in this PR is under `<SESSION_DIR>` which is
gitignored; only this evidence document is committed. The real
canonical run root at `output/trafficflow/runs/<UTC_TIMESTAMP>/`
and the updated `output/trafficflow/selected_output.json` remain
on disk but are also gitignored.
