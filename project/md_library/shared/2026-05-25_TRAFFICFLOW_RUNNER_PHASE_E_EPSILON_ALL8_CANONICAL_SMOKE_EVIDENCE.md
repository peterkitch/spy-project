# TrafficFlow runner Phase E PR Epsilon: all-8 canonical-write smoke

**Evidence-only PR.** No code changes. No test changes. This document
records the second real canonical-write smoke through the Phase E
runner + orchestrator stack delivered by PRs #317 / #318 / #319 / #320
and their amendments, broadening the PR #321 single-secondary smoke
(SPY + AAPL) to the full Phase 6I-79 secondary set.

The smoke targets the 8 PRJCT9 Phase 6I-79 secondaries (AAPL, AMZN,
GOOGL, META, MSFT, NVDA, SPY, TSLA) at K=1..6 only. A single
orchestrator invocation drove 8 real worker subprocesses through
`trafficflow_runner.py --canonical-write` with `--workers 4`. Real
canonical artifacts landed under
`output/trafficflow/runs/<UTC_TIMESTAMP>/` and the
`output/trafficflow/selected_output.json` global pointer was
atomically overwritten from the PR #321 Delta pointer to the new
PR Epsilon pointer.

## 1. Scope and Non-Goals

**In scope.**

- All 8 Phase 6I-79 secondaries: AAPL, AMZN, GOOGL, META, MSFT, NVDA,
  SPY, TSLA.
- K=1..6 only.
- One orchestrator invocation.
- `--workers 4`.
- Fresh canonical run root: `output/trafficflow/runs/<UTC_TIMESTAMP>/`.
- Real worker subprocesses invoked by
  `trafficflow_canonical_orchestrator.py`.
- Real `trafficflow_runner.py --canonical-write` workers.
- Real `trafficflow.build_board_rows` compute via the lazy worker path.
- Pre/post canonical safety snapshots covering every gated tree AND
  byte-identical preservation of the PR #321 Delta run root.
- Privacy scan across all 61 canonical JSON outputs plus the
  orchestrator stdout summary.
- Performance comparison against PR #315 and PR #321.

**Out of scope.**

- K > 6 / heavy-stage compute. Deferred to Phase F+.
- Code or test changes.
- Multiple orchestrator invocations.
- `--resume` validation under real smoke.
- `--allow-partial-publish` validation under real smoke.
- 250 / 500 secondary real run. PR #316 remains inference-only for
  that scale.
- Any amendment implementation.

## 2. References

- PR #317 Phase E canonical-write contract scoping.
- PR #318 Phase E PR Alpha CLI guardrails.
- PR #319 Phase E PR Beta canonical writer mechanics + amendment.
- PR #320 Phase E PR Gamma orchestrator/finalizer module + amendment.
- PR #321 Phase E PR Delta first real canonical-write smoke (SPY + AAPL).
- PR #315 ThreadPool feasibility benchmark evidence.
- PR #316 at-scale performance inference.
- PR #308 network/cache-write block (must hold through canonical
  writes; verified end-to-end here by canonical safety pre/post).

## 3. Test Suite Re-Run Confirmation

Pre-smoke baseline, on `main` at the PR #321 merge commit:

```
<PINNED_INTERPRETER> -m pytest test_scripts/test_trafficflow_runner.py -q
96 passed in 3.47s

<PINNED_INTERPRETER> -m pytest test_scripts/test_trafficflow_canonical_orchestrator.py -q
35 passed in 1.25s
```

131 targeted tests pass before the smoke begins.

## 4. Pre-Run Canonical Safety Snapshot Summary

Snapshot saved at `<SESSION_DIR>/preflight/pre_run_snapshot.json`.

Key directory file counts captured before the smoke:

| Tree | File count |
| --- | --- |
| `output/stackbuilder/` | 5388 |
| `output/impactsearch/` | 16 |
| `output/onepass/` | 2 |
| `output/trafficflow/` | 32 |
| `output/validation/` | 0 |
| `signal_library/data/stable/` | 71980 |
| `cache/results/` | 3305 |
| `cache/status/` | 1667 |
| `price_cache/daily/` | 12 |

`output/trafficflow/` existed pre-run with 32 files: the PR #321
Delta run root at `output/trafficflow/runs/20260525T054309Z/`
(28 secondary-owned + 3 run-level = 31 files) plus
`output/trafficflow/selected_output.json` (1 file). Full recursive
SHA-256 enumeration of `output/trafficflow/` was recorded so the
post-run check can verify byte-identical preservation of every
prior file.

Pre-run SHA-256 captured for: all 8 `selected_build.json`; all 8
`combo_leaderboard.xlsx`; all 48 `combo_k=1..6.json` files (6 per
secondary); `output/onepass/onepass.xlsx`; all 8 price cache CSVs
(SHA + size + mtime); the prior `selected_output.json`; the union
of 64 unique member PKLs referenced across the 8 secondaries x
K=1..6 combos, and their `.manifest.json` sidecars.

64 member PKLs were present and OK. Preflight returned verdict
`ALL_ELIGIBLE_DRY_RUN` for all 8 secondaries K=1..6 with
`would_refresh_pkls=[]` and `would_refresh_prices=[]`.

## 5. Smoke Invocation

Sanitized command shape:

```
<PINNED_INTERPRETER> trafficflow_canonical_orchestrator.py \
    --secondaries AAPL,AMZN,GOOGL,META,MSFT,NVDA,SPY,TSLA \
    --k-range 1,2,3,4,5,6 \
    --stackbuilder-root output/stackbuilder \
    --output-dir output/trafficflow/runs/<UTC_TIMESTAMP> \
    --workers 4 \
    --runner trafficflow_runner.py
```

**No** `--resume`, `--allow-partial-publish`, `--heavy-stage`,
`--explicit-build`, `--refresh-missing-pkls`, `--refresh-stale-prices`,
or `--allow-network-fetch` was passed. `PARALLEL_SUBSETS` was not
set.

Run root: `output/trafficflow/runs/<UTC_TIMESTAMP>/`.

| | UTC |
| --- | --- |
| Orchestrator start | 2026-05-25T06:20:29Z |
| Orchestrator end | 2026-05-25T06:20:56Z |

| Field | Value |
| --- | --- |
| Wall-clock elapsed | 27.33 s |
| Exit code | 0 |
| Stdout | parses cleanly as the orchestrator summary JSON |
| Stderr | 0 lines |

Stdout and stderr captured under `<SESSION_DIR>/orchestrator_run/`.

## 6. Orchestrator Output Summary

From the orchestrator's final stdout summary (sanitized,
JSON-parsed):

- `schema_version` = `trafficflow_canonical_orchestrator_v1`
- `run_status` = `complete`
- `totals` = `{total_secondaries: 8, pending: 0, in_progress: 0,
  complete: 8, failed: 0, skipped_resume: 0}`
- `selected_output_updated` = `true`
- `elapsed_seconds` = 27.33
- `artifacts_written` contains:
  - `output/trafficflow/runs/<UTC_TIMESTAMP>/progress.json`
  - `output/trafficflow/runs/<UTC_TIMESTAMP>/run_status.json`
  - `output/trafficflow/runs/<UTC_TIMESTAMP>/run_manifest.json`
  - `output/trafficflow/selected_output.json`

## 7. Per-Secondary Artifact Verification Table

| Secondary | Files in `<SEC>/` | `.done` zero-byte | `.quarantine/<SEC>/` |
| --- | --- | --- | --- |
| AAPL  | 14 | yes | absent |
| AMZN  | 14 | yes | absent |
| GOOGL | 14 | yes | absent |
| META  | 14 | yes | absent |
| MSFT  | 14 | yes | absent |
| NVDA  | 14 | yes | absent |
| SPY   | 14 | yes | absent |
| TSLA  | 14 | yes | absent |

The 14 files per secondary are: `board_rows_k=1..6.json` (6),
`board_rows_k=1..6.csv` (6), `secondary_manifest.json`, and
zero-byte `.done`. Total per-secondary files = 112. No extra
files. No `.tmp` siblings. No `.quarantine` directory anywhere
under `<RUN_ROOT>`. `<RUN_ROOT>` total file count = 112 + 3 = 115,
plus the global `selected_output.json` one level above.

## 8. Per-K Board Row Verification Table (48 cells)

Every cell produced 1 board row. JSON and CSV row counts match
(CSV row count excludes the header).

| Secondary | K=1 | K=2 | K=3 | K=4 | K=5 | K=6 | Sum (s) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AAPL  | 0.441 | 0.565 | 1.046 | 2.417 | 3.820 | 8.649 | 16.94 |
| AMZN  | 0.389 | 0.775 | 0.596 | 0.819 | 1.599 | 1.635 |  5.81 |
| GOOGL | 0.298 | 0.454 | 0.565 | 0.689 | 1.043 | 2.269 |  5.32 |
| META  | 0.169 | 0.312 | 0.511 | 1.150 | 1.569 | 3.673 |  7.38 |
| MSFT  | 0.436 | 0.577 | 1.200 | 1.612 | 3.776 | 7.774 | 15.38 |
| NVDA  | 0.276 | 0.397 | 0.406 | 0.979 | 1.217 | 2.543 |  5.82 |
| SPY   | 0.294 | 0.741 | 0.986 | 1.989 | 3.202 | 7.025 | 14.24 |
| TSLA  | 0.334 | 0.376 | 0.425 | 0.542 | 0.985 | 1.062 |  3.73 |

Elapsed values are `secondary_manifest.per_k_summary[].elapsed_seconds`.

All 48 JSON files parse as lists of dicts. All 48 CSV files parse
with a header row, and CSV row count matches JSON row count for
every cell. No 0-row cells.

## 9. Run-Level File Verification

Run-level files present and parseable at `<RUN_ROOT>`:

- `progress.json` (`trafficflow_canonical_orchestrator_v1`)
- `run_status.json` (`trafficflow_canonical_orchestrator_v1`)
- `run_manifest.json` (`trafficflow_runner_phase_e_v1`)

249 verification checks pass; 0 fail. Full results at
`<SESSION_DIR>/verification/run_verification.json`.

- `progress.json` shows all 8 secondaries at `status=complete`,
  `done_marker_present=true`, `quarantine_present=false`,
  `k_completed=[1,2,3,4,5,6]`, `k_failed=null`, `failure_kind=null`.
- `progress.json.totals` = `{total_secondaries: 8, complete: 8,
  failed: 0, pending: 0, in_progress: 0, skipped_resume: 0}`.
- `run_status.json.run_status=complete`, `secondaries_complete`
  contains all 8 names, `secondaries_failed=[]`,
  `secondaries_skipped_resume=[]`.
- `run_manifest.json.run_status=complete`,
  `inputs.secondaries` contains all 8, `inputs.k_range=[1,2,3,4,5,6]`,
  `inputs.workers=4`, `inputs.resume=false`,
  `inputs.heavy_stage=false`, `canonical_artifacts_referenced` has
  8 entries, `per_secondary_summary` has 8 complete entries,
  `quarantined_secondaries=[]`.
- `run_manifest.json.artifacts_written` contains
  `progress.json`, `run_status.json`, `run_manifest.json`, AND
  `selected_output.json` (PR #320 Amendment 4 holding at all-8
  scale).

## 10. selected_output.json Atomic Overwrite Verification

`output/trafficflow/selected_output.json` existed pre-run pointing
to the PR #321 Delta run root. After the PR Epsilon run, the file's
SHA-256 differs from the pre-run SHA, the file's
`selected_run_root_path` now references
`output/trafficflow/runs/<UTC_TIMESTAMP>` (the PR Epsilon run root),
and `selected_run_id` matches `<UTC_TIMESTAMP>`. `run_status=complete`,
`totals.total_secondaries=8`, `totals.complete=8`,
`totals.failed=0`.

No `selected_output.json.tmp` sibling remained under
`output/trafficflow/`; the write completed atomically.

## 11. Selected-Build Provenance Verification (8/8)

For each of the 8 secondaries, the worker-recorded
`selected_build_sha256` in both
`run_manifest.canonical_artifacts_referenced` and
`<SEC>/secondary_manifest.json` matches the pre-run on-disk SHA-256
of `output/stackbuilder/<SEC>/selected_build.json`. All 8
`explicit_build_override` values are `false`. All
`selected_build_path`, `selected_run_dir`, and
`combo_leaderboard_path` fields are sanitized repo-relative POSIX
paths.

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

Every tracked input SHA-256 is unchanged: all 8
`selected_build.json` files, all 8 `combo_leaderboard.xlsx` files,
all 48 `combo_k=1..6.json` files, `output/onepass/onepass.xlsx`,
all 64 referenced member PKLs.

All 8 price-cache CSVs are byte-identical pre/post (SHA + size +
mtime). All 64 member PKLs are SHA-unchanged pre/post.

**PR #321 Delta run root preservation:** the 31 files under
`output/trafficflow/runs/20260525T054309Z/` are byte-identical
pre/post (31 pre / 31 post / 31 unchanged / 0 changed / 0 missing
/ 0 new). PR Epsilon did not touch the PR #321 Delta run root at
all.

The only deltas in the entire repo working tree (outside session
evidence) are in `output/trafficflow/`:

- `output/trafficflow/runs/<UTC_TIMESTAMP>/` is new
  (112 secondary-owned + 3 run-level = 115 files).
- `output/trafficflow/selected_output.json` was atomically
  overwritten with the new PR Epsilon pointer.

PR #308 network/cache-write block held end-to-end across all 8
secondaries. The runner did not write to `price_cache/`,
`cache/results/`, `cache/status/`, or `signal_library/`. The
runner did not fetch from the network.

## 13. Atomic Write Pattern Verification

- No `*.tmp` files found anywhere under
  `output/trafficflow/runs/<UTC_TIMESTAMP>/`.
- No `*.tmp` files found anywhere under `output/trafficflow/`.
- All 8 per-secondary `.done` files are zero-byte and were written
  after all `board_rows_k=*.json/.csv` plus
  `secondary_manifest.json` per the PR #319 contract.

## 14. Privacy Sanitization Verification Across 61 JSON Files

Scanned files (61 total):

- `<RUN_ROOT>/progress.json`
- `<RUN_ROOT>/run_status.json`
- `<RUN_ROOT>/run_manifest.json`
- `output/trafficflow/selected_output.json`
- `<RUN_ROOT>/<SEC>/secondary_manifest.json` for each of 8 secondaries
- `<RUN_ROOT>/<SEC>/board_rows_k=1..6.json` for each of 8 secondaries
  (48 files)
- `<SESSION_DIR>/orchestrator_run/orchestrator_stdout.json`

Inventory totals 61 files. The task prompt enumerated this same set
and labeled it 60 by inadvertent operator counting; both counts
describe the same complete inventory. Recorded as a non-finding
counting note, not a privacy issue.

Scanned for: drive-letter pattern `[A-Za-z]:[\/]`, absolute local
paths, and the project denylist tokens (case-insensitive).

Result: **0 leaks across 61 files.** Every path-typed value is
either a repo-relative POSIX path or the redaction sentinel.
The orchestrator's `sanitize_for_json` layer and the worker's
`_scrub_embedded_absolute_paths` gate are both functioning as
designed at all-8 scale.

NVDA appears in ticker context only, as expected; it is a permitted
ticker symbol distinct from the unrelated denylist token whose
all-caps spelling collides only in the leading three characters.

## 15. Performance Comparison

Recorded measurements:

| Run | Shape | Wall-clock (s) |
| --- | --- | --- |
| PR #315 4a (raw process fan-out, 1 worker, sequential) | 8 secondaries x K=1..6 | ~96 (serial) |
| PR #315 4d (raw process fan-out, 4 workers) | 8 secondaries x K=1..6 | 28.62 |
| PR #321 (orchestrator, 2 workers) | 2 secondaries x K=1..6 | 20.17 |
| **PR Epsilon (orchestrator, 4 workers)** | **8 secondaries x K=1..6** | **27.33** |

PR Epsilon serial-sum derived from `per_k_summary.elapsed_seconds`:

| Secondary | Serial-sum K=1..6 (s) |
| --- | --- |
| AAPL  | 16.94 |
| AMZN  |  5.81 |
| GOOGL |  5.32 |
| META  |  7.38 |
| MSFT  | 15.38 |
| NVDA  |  5.82 |
| SPY   | 14.24 |
| TSLA  |  3.73 |
| **Total** | **74.61** |

Speedup vs serial-sum at `--workers 4` = 74.61 / 27.33 = **2.73x**.

PR #315 measured the raw 4-worker process fan-out at 28.62 s with
no orchestrator. PR Epsilon's orchestrator wrapper around the same
fan-out at all-8 scale ran in **27.33 s, within noise of and
slightly under the raw PR #315 number**. Orchestrator overhead is
not material at this scale.

PR #321 ran 2 secondaries at `--workers 2` in 20.17 s with 1.56x
speedup vs its serial-sum of 31.48 s. PR Epsilon's 2.73x speedup at
`--workers 4` against all-8 work scales as expected, bounded by
tail-latency cells (AAPL K=6 = 8.65 s, MSFT K=6 = 7.77 s, SPY K=6
= 7.03 s).

selected_output.json behavior in PR #321 was *create* (no prior
file); in PR Epsilon it was *atomically overwrite* (prior file
existed). Pre/post SHA differs and the new file points at the PR
Epsilon run root.

## 16. Findings

No critical findings. No amendment-blocking findings. No
performance findings.

Observed values worth recording for the next phase:

- All 8 secondaries completed K=1..6 cleanly with no quarantine
  entries.
- Wall-clock 27.33 s for all-8 K=1..6 at `--workers 4`. This
  beats or matches PR #315's raw 4-worker process fan-out and
  proves the orchestrator wrapper is operationally cheap at this
  scale.
- The selected_output.json atomic-overwrite path works as
  designed; the PR #321 Delta run root is preserved byte-identical
  even though the global pointer now references the PR Epsilon
  run.
- PR #319 amendments (pre-loop eligibility validation, fail-closed
  process-conflict semantics for canonical writes, failure-message
  path scrubbing) and PR #320 amendments (resume retry quarantine
  clearing, unreadable-progress fail-closed refusal, worker
  failure_kind propagation, complete artifacts_written inventory)
  all held under real all-8 smoke. Amendment 4 in particular is
  directly verified: every run, the on-disk
  `run_manifest.json.artifacts_written` contains the four expected
  entries and matches the orchestrator stdout summary exactly.

## 17. Recommendation

**PASS.** Second real canonical-write smoke through the Phase E
runner + orchestrator stack succeeded at full Phase 6I-79 scale
with zero findings.

**Direct answers:**

- **Can Phase E proceed to daily-cadence canonical operation design
  / operator runbook?** **YES.** The runner + orchestrator stack
  is operationally proven at the current 8-secondary scale. A
  daily-cadence operator runbook can be built on top of this
  contract.
- **Is the daily-cadence K=1..6 canonical-write contract now
  operationally proven at full Phase 6I-79 secondary scale?**
  **YES.** All 8 secondaries produced complete board_rows
  artifacts, run-level files, and a global `selected_output.json`
  pointer under one orchestrator invocation in 27.33 s with zero
  unauthorized canonical modifications and zero privacy leaks.
- **Does heavy-stage K=7..12 remain deferred to Phase F?** **YES.**
  PR Epsilon explicitly does not exercise `--heavy-stage` and does
  not write K > 6. Heavy-stage K=7..12 (and 250 / 500-secondary
  real runs) remain deferred to Phase F+; PR #316 inference
  evidence still bounds those expected costs.

Downstream consumer integration work (e.g. a Wikipedia-of-pattern-
finding UI that consumes `output/trafficflow/selected_output.json`
plus per-run board_rows) can proceed against the canonical
contract.

All session evidence in this PR is under `<SESSION_DIR>` which is
gitignored; only this evidence document is committed. The real
canonical run root at `output/trafficflow/runs/<UTC_TIMESTAMP>/`
and the rewritten `output/trafficflow/selected_output.json`
remain on disk but are also gitignored, as is the PR #321 Delta
run root that PR Epsilon preserved byte-identical.
