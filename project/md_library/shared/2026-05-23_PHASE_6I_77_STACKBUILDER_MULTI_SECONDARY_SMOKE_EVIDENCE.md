# Phase 6I-77 StackBuilder Multi-Secondary Smoke Evidence

## 1. Scope and Non-Goals

Phase C supervised multi-secondary StackBuilder smoke against the
post-Phase-6I-76 engine. Replaces the failed Phase 6I-74 smoke.

This is an evidence-only docs PR. No engine, runtime, or test files
are modified. Phase D benchmark and Phase E canonical write remain
separate. Phase 5C validation-scope work remains separate.

## 2. Engine State Reference

`main` HEAD at smoke start: post-Phase-6I-76 squash.

- Phase 6I-73 (merged): Sharpe removed as a StackBuilder selection
  criterion; inverse rescoring bounded; `rank_inverse` artifact
  removed from output schema.
- Phase 6I-75 (merged): StackBuilder consumer-only signal-library
  loader; `--skip-durable-validation` opt-in CLI flag.
- Phase 6I-76 (merged): vectorized fast combine path in StackBuilder
  phase3 (`_combine_signals_fast`); synthetic K-search hot path
  measured at ~8.4 ms/combo, ≤ 18 ms/combo target.

## 3. Operator Search Plan and Overrides

- K=1 through K=6: exhaustive enumeration over the 40-row cohort.
- K=7 through K=12: beam search with `beam_width=12`.
- `top_n=20`, `bottom_n=20` (40-row cohort).
- LEGACY runtime baseline: ~20 minutes per secondary for full
  K=1..K=12 in this configuration.

Required search overrides (used identically in dry-run and smoke):

```
--exhaustive-k 6
--k-max 12
--search beam
--beam-width 12
--top-n 20
--bottom-n 20
```

These intentionally override the engine defaults of `--exhaustive-k 4`
and `--k-max 6`.

## 4. Combination Count Reference (40-row cohort)

| K | Count | Mode |
|---:|---:|---|
| 1 | 40 | singleton signal score |
| 2 | 780 | exhaustive |
| 3 | 9,880 | exhaustive |
| 4 | 91,390 | exhaustive |
| 5 | 658,008 | exhaustive |
| 6 | 3,838,380 | exhaustive |
| K=1..K=6 sum | 4,598,478 | exhaustive |
| 7..12 | beam | bounded by `beam_width × remaining cohort members` per level |

`--search beam` does not beam-prune K ≤ `exhaustive_k`. The engine
exhaustively searches K ≤ 6, then uses beam for K > 6.

Engine traversal-stop behavior in this smoke is driven by **two
parameters acting together**, with one of them being the primary
mismatch versus LEGACY Dash:

- **Primary mismatch**: the smoke command did not pass
  `--allow-decreasing`, so the runner CLI default kept
  `allow_decreasing=False`. With that default, the
  `phase3_build_stacks` monotone Total Capture gate refuses to
  accept any K=N+1 candidate whose Total Capture does not strictly
  improve on K=N's best. The LEGACY Dash UI checkbox for
  allow-decreasing was enabled by default, so the LEGACY user
  experience effectively ran with `allow_decreasing=True`.
- **Secondary factor**: the runner's
  `build_stackbuilder_args_namespace` pins `k_patience = 1`, which
  controls how many non-improving / no-valid K levels the
  `phase3_build_stacks` patience stop logic tolerates before
  stopping. The runner CLI on `main` at smoke time did not expose
  `--k-patience`, so the smoke could not override this.

Either parameter alone could have produced a different traversal
depth; together they produced the K-2-accepted, stop-at-K=3
behavior observed in §11 / §14. The primary configuration mismatch
versus the LEGACY Dash traversal is the missing
`--allow-decreasing`, not the secondary `k_patience=1` setting. The
engine behaved correctly for the parameters that were actually
passed.

## 5. Pre-Checks

- cwd: `<PROJECT_ROOT>`.
- Starting branch: `main`. Starting HEAD: Phase 6I-76 squash.
- `main` contains `Phase 6I-76: optimize StackBuilder combine hot
  path` squash.
- Working tree clean.
- All required files / directories present:
  `stackbuilder_workbook_runner.py`, `stackbuilder.py`,
  `output/impactsearch/`, `output/onepass/onepass.xlsx`,
  `md_library/shared/2026-05-20_PHASE_6I_69_STACKBUILDER_RUNNER_EXECUTION_SURFACE.md`.
- Process-conflict probe: no engine processes active before launch.
- ImpactSearch workbook freshness window from runner default:
  45 days. Max discovered workbook age: 3 days. All within window.

## 6. Session Evidence Path

`<SESSION_DIR> = logs/phase_6i77_stackbuilder_smoke/<UTC_TIMESTAMP>/`

Subdirectories:

- `<SESSION_DIR>/dry_run/`
- `<SESSION_DIR>/smoke/`
- `<SESSION_DIR>/snapshots/`
- `<SESSION_DIR>/output/stackbuilder/` (isolated StackBuilder output
  root; canonical `output/stackbuilder/` not written)

Session-internal evidence is not staged.

## 7. Secondaries Inventoried

Sorted deterministically: `AAPL,AMZN,GOOGL,META,MSFT,NVDA,SPY,TSLA`.
Count: 8 (exactly matches expected). No `~$` Excel temp/lock files,
no orphan XLSX, no orphan manifests.

Each `<SECONDARY>_analysis.xlsx` had a matching
`<SECONDARY>_analysis.xlsx.manifest.json` sidecar at smoke launch.
All XLSX SHA256 + sidecar SHA256 + mtimes recorded in
`<SESSION_DIR>/snapshots/pre_snapshot.json`.

## 8. Commands Run

Dry-run command (no `--write`, no `--allow-network-fetch`):

```
<PINNED_INTERPRETER> stackbuilder_workbook_runner.py \
    --secondaries <SECONDARY_LIST> \
    --primary-source impact_xlsx \
    --impact-xlsx-dir output/impactsearch \
    --outdir <SESSION_DIR>/output/stackbuilder \
    --jobs 1 \
    --exhaustive-k 6 \
    --k-max 12 \
    --search beam \
    --beam-width 12 \
    --top-n 20 \
    --bottom-n 20 \
    --duration-budget-minutes 45 \
    --operator-budget-label phase-6i-77-dry-run \
    --skip-durable-validation \
    --update-selected \
    --no-progress \
    1> <SESSION_DIR>/dry_run/run.stdout.json \
    2> <SESSION_DIR>/dry_run/run.stderr.log
```

Smoke command (authorized write):

```
<PINNED_INTERPRETER> stackbuilder_workbook_runner.py \
    --secondaries <SECONDARY_LIST> \
    --primary-source impact_xlsx \
    --impact-xlsx-dir output/impactsearch \
    --outdir <SESSION_DIR>/output/stackbuilder \
    --jobs 1 \
    --exhaustive-k 6 \
    --k-max 12 \
    --search beam \
    --beam-width 12 \
    --top-n 20 \
    --bottom-n 20 \
    --write \
    --allow-network-fetch \
    --duration-budget-minutes 45 \
    --operator-budget-label phase-6i-77-smoke \
    --skip-durable-validation \
    --update-selected \
    --no-progress \
    1> <SESSION_DIR>/smoke/run.stdout.json \
    2> <SESSION_DIR>/smoke/run.stderr.log
```

Both commands were invoked via a PowerShell wrapper `.ps1` under the
session dir so the parent shell's command line did not embed the
runner script name (caret-ticker safety + runner process-conflict
self-detection avoidance).

## 9. Dry-Run Plan Result

`status = dry_run`. `would_call_engine = false`. `write_requested = false`.
`network_authorized = false`. `preflight_issues = []`.
`process_conflict.status = ok`, 0 conflicts.

`secondaries_resolution.secondaries` = exact 8-secondary sorted list.
`per_secondary_plan` length = 8. Every planned progress path under
`<SESSION_DIR>/output/stackbuilder/_progress/`. None under canonical
`output/stackbuilder/_progress`.

`effective_config` confirmed (matches operator overrides):

| Key | Value |
|---|---|
| `exhaustive_k` | 6 |
| `k_max` | 12 |
| `search` | beam |
| `beam_width` | 12 |
| `top_n` | 20 |
| `bottom_n` | 20 |
| `jobs` | 1 |
| `skip_durable_validation` | true |
| `primary_source` | impact_xlsx |
| `impact_xlsx_dir` | output/impactsearch |
| `outdir` | `<SESSION_DIR>/output/stackbuilder` |

Dry-run side-effect check: 0 files under
`<SESSION_DIR>/output/stackbuilder` after dry-run. Canonical
`output/stackbuilder` file count unchanged (5228).

## 10. Smoke Run Result

`status = ok`. `summary = {ok: 8, error: 0, total: 8}`.
`per_secondary_results` length = 8. Aggregate wall-clock: **6498.9 s
(~108.3 min / ~1h48m)**. Total budget ceiling 6 hours not hit. All 8
secondaries completed cleanly under their per-secondary 45-min
budgets.

Smoke stdout is a single parseable JSON object. Smoke stderr: 6,666
bytes, 80 lines (engine progress + completion lines).

## 11. Per-Secondary Smoke Run Results

Column meanings:

- **Last accepted K**: the highest K level whose winner was
  published to the leaderboard, evidenced by the highest
  `combo_k=<K>.json` artifact in the run directory. For every
  secondary in this smoke the only `combo_k` artifacts present are
  `combo_k=1.json` and `combo_k=2.json`, so the last accepted K is
  **K=2** for all 8.
- **Last attempted K**: source-implied by the `[PHASE3] Stopping at
  K=3` stderr line. The `phase3_build_stacks` patience stop logic
  emits "Stopping at K=N" after the next-K (K=N+1) attempt fails
  with patience already exhausted by K=N. With `k_patience=1`,
  K=3's failed attempt consumed patience and K=4's failed attempt
  then triggered the stop. So the last attempted K is **K=4** for
  all 8.

The "K accepted / K attempted" columns below reflect this. The
"stopped at K=3" notation in the K-attempted column quotes the
engine's stderr message verbatim.

| Secondary | status | elapsed (s) | wall-clock | last accepted K | last attempted K (stderr: "Stopping at K=3") | K=1 winner | K=2 members | Best Total Capture (%) |
|---|---|---:|---:|---:|---:|---|---|---:|
| AAPL | ok | 1140.9 | 19m01s | K=2 | K=4 | WEN[D] | HD[D], WEN[D] | 1148.7443 |
| AMZN | ok | 810.4 | 13m30s | K=2 | K=4 | AIRT[D] | CLDN.L[D], CML.L[I] | 1325.1187 |
| GOOGL | ok | 676.3 | 11m16s | K=2 | K=4 | ACO-X.TO[D] | MDD.F[I], TFF.PA[D] | 664.6505 |
| META | ok | 532.4 | 8m52s | K=2 | K=4 | SMMU[I] | KA8.DE[D], MALJF[D] | 665.2521 |
| MSFT | ok | 1052.2 | 17m32s | K=2 | K=4 | IMO[I] | IMO[I], UDR[D] | 950.0831 |
| NVDA | ok | 788.9 | 13m09s | K=2 | K=4 | ALK-B.CO[D] | APR.F[I], CGLO[D] | 1317.7099 |
| SPY | ok | 909.8 | 15m10s | K=2 | K=4 | SBSI[D] | AWR[D], PRGO[D] | 450.3807 |
| TSLA | ok | 572.4 | 9m32s | K=2 | K=4 | EGY.AX[D] | PGH.L[D], TCU.F[D] | 1066.7417 |

## 12. Per-Secondary Timing Decomposition Through K=12

**Critical caveat on timing source.** The Phase 6I-77 artifacts
alone do not establish per-K runtime cleanly: `combo_k=N.json` is
written only when K=N is accepted into the leaderboard, and several
other artifacts (`rank_all.xlsx`, `rank_direct.xlsx`,
`cohort.xlsx`, `combo_leaderboard.xlsx`) are written during the
finalize phase, not at K-accept time. The engine's stderr does not
timestamp K-level transitions either. Per-K wall-clock therefore
cannot be fully recovered from this smoke's evidence alone; the
table below reports artifact-mtime deltas for the K levels that
were accepted (K=1, K=2) and the aggregate per-secondary
`elapsed_seconds` reported by the runner.

| Secondary | Phase1+rank_all artifact (s) | cohort artifact (s) | combo_k=1.json (s) | combo_k=2.json (s) | combo_leaderboard+finalize (s) | Total elapsed (s) | Last accepted K | Last attempted K |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| AAPL | 9.5 | +2.4 | +6.2 | +6.5 | +0.05 | 1140.9 | K=2 | K=4 |
| AMZN | 8.5 | +2.3 | +5.7 | +4.4 | +0.05 | 810.4 | K=2 | K=4 |
| GOOGL | 8.6 | +2.3 | +5.4 | +3.7 | +0.05 | 676.3 | K=2 | K=4 |
| META | 8.6 | +2.3 | +5.4 | +2.9 | +0.05 | 532.4 | K=2 | K=4 |
| MSFT | 8.5 | +2.3 | +5.9 | +5.7 | +0.05 | 1052.2 | K=2 | K=4 |
| NVDA | 8.6 | +2.4 | +5.5 | +4.3 | +0.05 | 788.9 | K=2 | K=4 |
| SPY | 8.6 | +2.1 | +5.8 | +4.9 | +0.05 | 909.8 | K=2 | K=4 |
| TSLA | 8.6 | +2.1 | +5.7 | +3.1 | +0.05 | 572.4 | K=2 | K=4 |

**Runtime-driver interpretation.** The Phase 6I-78 SPY follow-up
smoke (run on the same engine `main` HEAD, with explicit
`--allow-decreasing` and `--k-patience 1` overrides) measured
current-engine per-K timings directly via per-K-accept artifact
mtimes:

| Phase | Duration (Phase 6I-78 SPY) |
|---|---:|
| Phase 1 + Phase 2 rank_all | 9.5 s |
| Phase 2 cohort assembly | 2.4 s |
| K=1 exhaustive (40) | 5.7 s |
| K=2 exhaustive (780) | 5.3 s |
| K=3 exhaustive (9,880) | 78.9 s |
| K=4 exhaustive (91,390) | 806.5 s |

In other words, on Phase 6I-78's measurement of the post-Phase-6I-76
engine, just Phase 1 + Phase 2 + K=1 + K=2 + K=3 + the K=4 exhaustive
attempt sums to ~908 s — within seconds of Phase 6I-77's measured
SPY total of 909.8 s. That alignment makes the **K-search work
through the failed K=4 attempt** the most plausible runtime driver
for this smoke, not Phase 2 ImpactSearch ranking. (Earlier wording
attributing the runtime primarily to Phase 2 has been corrected.)

The same per-K shape — small Phase 1 + Phase 2 setup, fast K=1 and
K=2, K=3 exhaustive sub-2 minutes, K=4 exhaustive ~13 minutes — fits
the spread of Phase 6I-77 per-secondary totals (532 s to 1,141 s)
without invoking any other phase dominating the wall-clock. The
spread itself is consistent with per-secondary variation in K=3 /
K=4 candidate-iteration cost, not a Phase 2 anomaly.

Comparison vs LEGACY baseline (~20 min/secondary for full
K=1..K=12):

- AAPL 19.0 min, MSFT 17.5 min, SPY 15.2 min, AMZN 13.5 min,
  NVDA 13.1 min, GOOGL 11.3 min, TSLA 9.5 min, META 8.9 min.
- All 8 secondaries finished under the LEGACY ~20-min single-
  secondary baseline. They did so without exploring K=5..K=12,
  which means the comparison is not a like-for-like proof against
  the LEGACY traversal depth; it does establish that K=1..K=2
  accepted + K=3 / K=4 attempted fits inside the legacy time
  budget on this engine for these secondaries.

## 13. K=1 Fast-Path Assertion Results

For every successful secondary: `combo_k=1.json` present, K==1,
exactly one member, Total Capture present, Sharpe present (display
only). K=1 timing measured as `(combo_k=1.json mtime) − (cohort.xlsx
mtime)`; this is finalize-write delta, not true K=1 search wall-
clock, but the spec accepts the artifact boundary when exact phase2/
cohort completion cannot be recovered.

| Secondary | Δ(combo_k=1.json − cohort.xlsx) (s) | K=1 verdict |
|---|---:|---|
| AAPL | 6.2 | PASS (≤30s) |
| AMZN | 5.7 | PASS |
| GOOGL | 5.4 | PASS |
| META | 5.4 | PASS |
| MSFT | 5.9 | PASS |
| NVDA | 5.5 | PASS |
| SPY | 5.8 | PASS |
| TSLA | 5.7 | PASS |

Aggregate K=1 fast-path: **8/8 PASS** under the 30-second threshold.
Inverse K=1 winners (mode suffix `[I]`): META (SMMU[I]) and MSFT
(IMO[I]) — 2/8.

## 14. K=1..K=6 Exhaustive Verification

K-by-K status for every secondary:

- **K=1 accepted**: `combo_k=1.json` present for every secondary,
  one member, Total Capture recorded. 40 singleton scores ran to
  completion.
- **K=2 accepted**: `combo_k=2.json` present for every secondary,
  two members, Total Capture recorded. 780 exhaustive combinations
  ran to completion.
- **K=3 attempted, not accepted**: 9,880 exhaustive combinations
  were enumerated for each secondary, but the
  `phase3_build_stacks` monotone Total Capture gate (active because
  the smoke did not pass `--allow-decreasing`) rejected every
  candidate that did not strictly improve on K=2's best Total
  Capture. No K=3 candidate cleared the gate, so no
  `combo_k=3.json` was written. The patience-stop logic then
  consumed `k_patience=1`.
- **K=4 attempted, not accepted**: source-implied by the
  `[PHASE3] Stopping at K=3` stderr line — that message is emitted
  while the K=4 candidate evaluation fails after patience was
  already exhausted at K=3. No `combo_k=4.json` written.
- **K=5..K=6 not entered**: the stop fired before K=5.

The root cause of stopping at K=4 attempted (K=2 last accepted) is
the runner CLI default `allow_decreasing=False`, which enforces the
monotone Total Capture improvement gate inside
`phase3_build_stacks`. The LEGACY Dash UI ran with the allow-
decreasing checkbox enabled by default, so the LEGACY user
experience effectively ran with `allow_decreasing=True` and would
not have hit this gate. `k_patience=1` was a secondary contributing
factor: it bounded how many non-improving K levels the patience-
stop logic tolerates before stopping, but it did not by itself
force the monotone gate.

The Phase 6I-76 fast combine path is the reason K=3's 9,880-combo
and K=4's 91,390-combo exhaustive scans complete inside the
per-secondary budget — see §12 for the Phase 6I-78 SPY measurement
that aligns K=4 exhaustive at ~806.5 s with Phase 6I-77's SPY total
of 909.8 s.

## 15. K=7..K=12 Beam Verification

K=7..K=12 were not entered for any secondary because the engine
stopped after attempting K=3 (patience consumed) and then K=4
(stop fired). No beam-stage timing evidence is available for this
smoke. K=12 was not reached for any secondary. The K-depth
shortfall is a configuration mismatch (missing
`--allow-decreasing`, plus the `k_patience=1` runner default) and
not a Phase 6I-73 / 6I-75 / 6I-76 engine regression.

## 16. `selected_build.json` Behavior per Secondary

`<SESSION_DIR>/output/stackbuilder/<SECONDARY>/selected_build.json`
written for all 8 secondaries. None under canonical
`output/stackbuilder/`. Each entry:

- `schema_version` = 1.
- `secondary` matches the secondary.
- `selected_run_dir` exists under `<SESSION_DIR>/output/
  stackbuilder/<SECONDARY>/` and matches the per-secondary run-dir
  named after the K=2 winners (e.g.
  `seedTC__HD-D_WEN-D` for AAPL).
- `selected_k` = 12 for all 8 — note: this is the runner's
  *configured `k_max`*, not the engine's *actual reached K*. Spec
  precondition "`selected_k` present and ≤ 12" is satisfied.
- `total_capture` matches the per-secondary best capture.
- `sharpe_ratio` present (display only).
- `selection_policy` = `v2.total_capture_then_latest`.
- `operator_pinned` = false.
- `source_manifest_path` points at the same secondary's
  `run_manifest.json` under the session dir.

## 17. Progress-Path Isolation Verification

`effective_progress_dir` (dry-run plan + smoke run) resolved to
`<SESSION_DIR>/output/stackbuilder/_progress`. Eight runtime progress
JSON files were written there (one per secondary). Canonical
`output/stackbuilder/_progress/` file count and latest mtime
unchanged between pre-snapshot and post-snapshot (Phase 6I-71
progress-path isolation contract holds).

## 18. Bounded Cohort and `rank_inverse` Absence Verification

- `cohort.xlsx` row count for every secondary: **40** (= top_n 20 +
  bottom_n 20). No secondary exceeded the bounded-cohort contract.
- `combo_leaderboard.xlsx` row count for every secondary: **2** (K=1
  + K=2 winners).
- `rank_inverse.*` artifacts: **0** per secondary (Phase 6I-73
  contract preserved).

## 19. Durable Validation Skip Verification

For every per-secondary `run_manifest.json`:

| Field | Value |
|---|---|
| `cli_args.skip_durable_validation` | true |
| `durable_validation_status` | `skipped` |
| `durable_validation_skip_reason` | `operator_flag` |
| `validation_status` | `skipped` |
| `validation_artifact_path` | null |
| `validation_artifact_hash` | null |

No durable-validation sidecar written for any secondary. Engine
emitted `[VALIDATION] Validation: SKIPPED (operator_flag). No durable
validation sidecar was written.` 8 times in smoke stderr (once per
secondary). Phase 5C fail-closed default is unchanged on `main`; the
skip here is the explicit operator opt-in introduced in Phase 6I-75.

## 20. Canonical Artifact Safety

Pre-snapshot and post-snapshot diff (recorded at
`<SESSION_DIR>/snapshots/pre_snapshot.json` and
`<SESSION_DIR>/snapshots/post_snapshot.json`):

| Path | Pre | Post | Verdict |
|---|---|---|---|
| `output/stackbuilder/` | 5228 files, latest mtime 2026-05-15T01:12:12 | identical | unchanged |
| `output/stackbuilder/_progress/` | 648 files | identical | unchanged |
| `output/impactsearch/` | 16 files (8 XLSX + 8 sidecar) | identical | unchanged |
| `output/impactsearch/*.xlsx` SHA256 | 8 hashes | identical | unchanged |
| `output/impactsearch/*.manifest.json` SHA256 | 8 hashes | identical | unchanged |
| `output/onepass/onepass.xlsx` SHA256 | recorded | identical | unchanged |
| `output/validation/` | does not exist | does not exist | unchanged |
| `signal_library/data/stable/` | 71,980 files | identical | unchanged |
| `price_cache/daily/` | 6 files, latest mtime 2026-05-15T03:11:07 | identical | unchanged |

No canonical artifact was touched. `--allow-network-fetch` was
authorized but the engine apparently did not need to refresh any
secondary's `price_cache/daily` (all 6 existing files unchanged) —
likely because the existing secondary price data already covered the
analysis window. No unexpected `price_cache/daily` writes occurred.

git working tree at end of smoke: no tracked code/test files
modified. logs/ remains untracked.

**Canonical artifact safety verdict: PASS.**

## 21. stderr / Warning Scan Summary

| Pattern | Count |
|---|---:|
| `[ONEPASS:` | **0** |
| `Forcing rebuild` | **0** |
| `Traceback` | **0** |
| `[STACKBUILDER:library_missing]` | 0 |
| `[STACKBUILDER:library_invalid]` | 0 |
| `[STACKBUILDER:library_unreadable]` | 0 |
| `[STACKBUILDER:library_manifest_mismatch]` | 0 |
| `Failed download` | 0 |
| `No data` | 0 |
| `Error:` | 0 |
| `Exception` | 0 |
| `Stopping at K` | 8 (one per secondary, all at K=3) |
| `No candidate improves Total Capture` | 8 |
| `K=12` | **0** (no secondary reached K=12) |
| `[COMPLETE]` | 8 |
| `[RESULT]` | 8 |
| `[OUTPUT]` | 8 |
| `[VALIDATION] Validation: SKIPPED` | 8 |

Forbidden counts (`[ONEPASS:`, `Forcing rebuild`, `Traceback`) are
all zero. Consumer-loader diagnostic family is all zero (every
primary library loaded cleanly). The 8 × `Stopping at K` / `No
candidate improves Total Capture` / 0 × `K=12` pattern is the
configuration-driven early-stop documented in §4 / §11 / §14 —
the smoke omitted `--allow-decreasing`, so the
`phase3_build_stacks` monotone Total Capture gate refused every
K=3 candidate, the `k_patience=1` budget was consumed, and the
K=4 attempt triggered the stop. The engine produced these lines
correctly for the parameters that were actually passed.

## 22. Total Smoke Wall-Clock

**6,498.9 s ≈ 108.3 min ≈ 1h48m** for 8 secondaries.

- Per-secondary budget: 45 min — none exceeded.
- Per-secondary LEGACY baseline ~20 min — none exceeded.
- Total hard ceiling: 6 hours — not hit.
- Runner exited normally with exit code 0.

## 23. Verdict per Secondary

The per-secondary verdicts below separate the axes the smoke
**did** validate (runner / safety / K=1 / consumer loader / durable-
validation skip / canonical artifact safety) from the axis it did
**not** validate (K-depth traversal to `k_max=12`). The K-depth
axis is **INCOMPLETE** for every secondary because the smoke
parameters did not match LEGACY Dash traversal behavior — the
smoke omitted `--allow-decreasing`, which is the primary
configuration mismatch (see §4 / §14). It is not an engine failure.

| Secondary | Runner / safety | K=1 fast-path | Consumer loader / no rebuild storm | Durable validation skip | Canonical artifact safety | K-depth traversal |
|---|---|---|---|---|---|---|
| AAPL | PASS | PASS | PASS | PASS | PASS | INCOMPLETE (config mismatch) |
| AMZN | PASS | PASS | PASS | PASS | PASS | INCOMPLETE (config mismatch) |
| GOOGL | PASS | PASS | PASS | PASS | PASS | INCOMPLETE (config mismatch) |
| META | PASS | PASS | PASS | PASS | PASS | INCOMPLETE (config mismatch) |
| MSFT | PASS | PASS | PASS | PASS | PASS | INCOMPLETE (config mismatch) |
| NVDA | PASS | PASS | PASS | PASS | PASS | INCOMPLETE (config mismatch) |
| SPY | PASS | PASS | PASS | PASS | PASS | INCOMPLETE (config mismatch) |
| TSLA | PASS | PASS | PASS | PASS | PASS | INCOMPLETE (config mismatch) |

"INCOMPLETE (config mismatch)" reads as: the engine behaved
correctly for the parameters that were passed, but those
parameters did not exercise the K=12 traversal path the spec was
trying to validate. A follow-up smoke that explicitly passes
`--allow-decreasing` (and, in the same spirit, exposes
`--k-patience` on the runner so the operator can pin it) is the
right way to supply K=12 evidence.

## 24. Aggregate Verdict

**SUSPECT — configuration-incomplete smoke, not an engine failure.**

The per-axis aggregate reads:

- **Runner / safety invariants: PASS.** `status=ok`, all required
  per-secondary artifacts present, runner exited normally inside
  budget, no failures.
- **K=1 fast-path: PASS.** 8/8 secondaries cleared the K=1
  ≤30 s assertion.
- **Consumer loader / no rebuild storm: PASS.** Zero
  `[ONEPASS:`, zero `Forcing rebuild`, zero
  `[STACKBUILDER:library_*]` diagnostics — Phase 6I-75's consumer-
  loader contract held.
- **Durable validation skip: PASS.** Every per-secondary
  `run_manifest.json` carries `durable_validation_status=skipped`
  + `skip_reason=operator_flag`; no sidecar written; Phase 5C
  fail-closed default unchanged.
- **Canonical artifact safety: PASS.** Pre/post snapshot diff
  empty across `output/stackbuilder`,
  `output/stackbuilder/_progress`, `output/impactsearch`,
  `output/onepass.xlsx`, `output/validation`,
  `signal_library/data/stable`, `price_cache/daily`.
- **K-depth traversal validation: INCOMPLETE.** The smoke omitted
  `--allow-decreasing`, so the `phase3_build_stacks` monotone
  Total Capture gate refused every K=3 candidate and the patience
  stop fired during the K=4 attempt. The smoke therefore cannot
  serve as K=12 evidence; it can only serve as K=2-accepted /
  K=4-attempted evidence under the parameters that were actually
  passed.

The aggregate is **SUSPECT** in the sense that the K-depth axis
is incomplete. It is explicitly **not** an engine failure: every
other axis passed cleanly, the engine behaved correctly for the
parameters that were actually passed, and the K-stop is wholly
explained by the `--allow-decreasing` omission (primary) and the
`k_patience=1` runner default (secondary). Earlier wording that
suggested the engine produced bad builds, or that Phase 2 ranking
dominated the wall-clock, has been corrected.

The Phase 6I-76 fast combine path is operating correctly: combine
wall-clock is no longer the dominant per-combo cost (see Phase
6I-78 SPY measurement quoted in §12), and the K-search work that
the smoke actually ran (K=1 + K=2 accepted, K=3 + K=4 attempted)
fits comfortably inside the per-secondary budget for all 8
secondaries.

## 25. Follow-Ups

- **Phase D benchmark** remains a separate authorized task. Not
  started by this PR.
- **Phase E canonical write** (promoting smoke outputs to canonical
  `output/stackbuilder/`) remains a separate authorized task. Not
  started by this PR.
- **Phase 5C validation-scope** work remains separate. Durable
  validation default behavior is unchanged on `main` — the skip
  used here is the operator-explicit opt-in from Phase 6I-75.
- **Recommended next**: a follow-up SPY smoke that explicitly
  passes `--allow-decreasing` (the primary configuration mismatch
  versus LEGACY Dash here), plus runner-side exposure of
  `--k-patience` so the operator can pin patience from the CLI.
  With those two parameters in place a future smoke can supply
  K=12 evidence on the same engine `main` HEAD.
- StackBuilder-direct call paths (`stackbuilder.py` as an entry
  point) are out of scope — the smoke used
  `stackbuilder_workbook_runner.py` exclusively.

---

### Constraints verified by this PR

- This was Phase C supervised multi-secondary smoke against the
  post-Phase-6I-76 engine.
- All 8 valid ImpactSearch XLSX secondaries discovered in
  `output/impactsearch` were included.
- Operator plan: K=1..K=6 exhaustive, K=7..K=12 beam; overrides
  `--exhaustive-k 6 --k-max 12 --search beam --beam-width 12
  --top-n 20 --bottom-n 20`.
- Output root was isolated under `logs/`.
- No canonical `output/stackbuilder` write was authorized.
- The dry-run produced a JSON plan and did not call the engine.
- The smoke used the authorized runner write path with `--write`,
  `--allow-network-fetch`, and `--skip-durable-validation`.
- StackBuilder was run only through
  `stackbuilder_workbook_runner.py`.
- Per-secondary timing was reported through the K-range the engine
  actually exercised: K=1 + K=2 accepted (artifacts published),
  K=3 + K=4 attempted (no leaderboard improvement; stop fired
  with `[PHASE3] Stopping at K=3`). K=5..K=12 were not entered.
  The primary cause of stopping at K=4 attempted (K=2 last
  accepted) is the missing `--allow-decreasing` flag, which left
  the runner CLI default `allow_decreasing=False` in place and
  enforced the `phase3_build_stacks` monotone Total Capture gate;
  `k_patience=1` was a secondary contributing factor.
- K=1 fast-path assertion passed for all 8 secondaries.
- Progress-path isolation under `<outdir>/_progress` held for all 8.
- Durable validation was explicitly skipped; manifest carries
  `durable_validation_status=skipped` and
  `durable_validation_skip_reason=operator_flag`.
- `selected_build.json` was written only in the isolated logs
  output root.
- `rank_inverse` artifacts were absent.
- Cohort row count was 40 (= `top_n + bottom_n`) for every
  secondary.
- No canonical output was touched.
- No OnePass / ImpactSearch / TrafficFlow / Spymaster / Confluence
  / MTF run was launched.
- Phase D benchmark and Phase E canonical write remain separate.
- Phase 5C validation-scope work remains separate.
- Smoke evidence under `logs/` is not staged.
- No engine/runtime code changed.
- No tests changed.
