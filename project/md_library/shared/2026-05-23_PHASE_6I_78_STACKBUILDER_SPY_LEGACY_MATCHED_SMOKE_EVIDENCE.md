# Phase 6I-78 StackBuilder SPY LEGACY-Matched Smoke Evidence

> **Framing correction (added by amendment).** The branch name and
> PR title preserve the phrase "LEGACY-matched" as a historical
> artifact of the operator's stated intent at the time the smoke
> was launched. Later inspection of the engine source clarified
> that the LEGACY Dash UI used `exhaustive_k=4` by default (not
> `exhaustive_k=6`), so the actual LEGACY-default traversal was
> K=1..K=4 exhaustive plus K=5..K=12 beam. This smoke was launched
> with `--exhaustive-k 6` and is therefore a **deeper-than-LEGACY
> exhaustive stress smoke**, not an apples-to-apples LEGACY-default
> traversal. The K-depth and runner/safety verdicts below are
> unaffected; the runtime-vs-LEGACY framing has been corrected. The
> recommended apples-to-apples follow-up smoke (using
> `--exhaustive-k 4`) is documented in §22 / §24.

## 1. Scope and Non-Goals

SPY-only controlled StackBuilder smoke against the post-Phase-6I-76
current engine. Replaces the K-depth-incomplete Phase 6I-77 evidence
(all 8 secondaries there stopped at K=3 because `--allow-decreasing`
was not passed and the runner did not expose `--k-patience`).

This evidence proves end-to-end K=12 traversal under the
**K=1..K=6 exhaustive stress configuration** the smoke was launched
with. It is not an apples-to-apples runtime comparison against the
operator's ~20-minute LEGACY SPY baseline, because the LEGACY Dash
defaults use `exhaustive_k=4` rather than `exhaustive_k=6` (see the
framing correction above and the per-combo reconciliation in §22).

Non-goals: Phase D benchmark, Phase E canonical write, Phase 5C
validation-scope changes. No engine code modified.

## 2. Engine State Reference

`main` HEAD at smoke start: `302db19` (Phase 6I-76 squash).

- Phase 6I-73 (merged): Sharpe removed as a StackBuilder selection
  criterion; inverse rescoring bounded; `rank_inverse` artifact
  removed from output schema.
- Phase 6I-75 (merged): StackBuilder consumer-only signal-library
  loader; `--skip-durable-validation` opt-in CLI flag.
- Phase 6I-76 (merged): vectorized fast combine path in StackBuilder
  phase3 (`_combine_signals_fast`); synthetic K-search hot path
  measured at ~8.4 ms/combo, ≤ 18 ms/combo target.

## 3. Runner K-Patience Wiring Status

**Before this PR:** `stackbuilder_workbook_runner.py` did not expose
`--k-patience`; the engine namespace hardcoded `k_patience=1` inside
`build_stackbuilder_args_namespace`; dry-run `effective_config` did
not report `k_patience`. `--allow-decreasing` was already present
and threaded.

**Phase 6I-78 runner-only wiring added (Part 0):**

- `parse_args` now accepts `--k-patience <int>` (default `1`, help
  text explains the semantics).
- `_effective_config` reports `k_patience`.
- `build_stackbuilder_args_namespace` threads
  `int(getattr(args, "k_patience", 1))` into the engine namespace.
- Default value of `1` preserves the runner's prior hardcoded
  behavior when the flag is omitted.
- `stackbuilder.py` and all other engine files are **unchanged**.
- 9 new focused runner tests added in
  `test_scripts/test_stackbuilder_workbook_runner.py`:
  default-is-1, accept-explicit-1, accept-3, effective_config
  default + explicit, namespace pass-through default + explicit,
  dry-run plan effective_config exposes k_patience + allow_decreasing,
  allow_decreasing default unchanged. All 51 runner tests pass.

## 4. Operator Configuration Used (K=1..K=6 Exhaustive Stress Configuration)

> The configuration recorded below was launched under the
> operator's stated "LEGACY-matched" intent at the time of the
> smoke. Later source inspection clarified the LEGACY Dash default
> is `exhaustive_k=4`, so this configuration is actually
> **deeper-than-LEGACY**. See the framing correction at the top
> of this document and §22 for the corrected runtime
> interpretation.

| Flag | Value | Role |
|---|---|---|
| `--secondaries` | `SPY` | single-secondary smoke |
| `--primary-source` | `impact_xlsx` | primaries from ImpactSearch ranking |
| `--impact-xlsx-dir` | `output/impactsearch` | workbook directory |
| `--outdir` | `<SESSION_DIR>/output/stackbuilder` | **isolated** output root |
| `--jobs` | `1` | sequential per-secondary |
| `--exhaustive-k` | `6` | exhaustive K=1..K=6 over 40-row cohort |
| `--k-max` | `12` | beam K=7..K=12 |
| `--search` | `beam` | beam mode for K > exhaustive_k |
| `--beam-width` | `12` | beam width |
| `--top-n` | `20`, `--bottom-n` | `20` | 40-row cohort |
| `--allow-decreasing` | (set) | engine accepts non-improving K levels |
| `--k-patience` | `1` | one non-improving level tolerated |
| `--write` | (set, smoke only) | authorized write |
| `--allow-network-fetch` | (set, smoke only) | authorized network |
| `--duration-budget-minutes` | `1440` | overnight evidence budget (NOT a pass condition) |
| `--operator-budget-label` | `phase-6i-78-smoke` | provenance label |
| `--skip-durable-validation` | (set) | Phase 5C fail-closed bypassed by operator opt-in |
| `--update-selected` | (set) | session-only `selected_build.json` |
| `--no-progress` | (set) | quieter logging |

## 5. Exhaustive and Beam Combination Count Reference (40-row cohort)

| K | Count | Mode |
|---:|---:|---|
| 1 | 40 | singleton signal score |
| 2 | 780 | exhaustive |
| 3 | 9,880 | exhaustive |
| 4 | 91,390 | exhaustive |
| 5 | 658,008 | exhaustive |
| 6 | 3,838,380 | exhaustive |
| **K=1..K=6 sum** | **4,598,478** | exhaustive |
| 7..12 | beam | bounded by `beam_width × remaining` per level |

## 6. Pre-Checks

- cwd: `<PROJECT_ROOT>`.
- Starting branch: `main`. Starting HEAD: `302db19` (Phase 6I-76
  squash).
- `main` contains `Phase 6I-76: optimize StackBuilder combine hot path`.
- PR #292 (Phase 6I-77 evidence): open, unmerged at the start of
  Phase 6I-78. Does not block this work.
- Working tree clean on `main` at smoke start.
- All required files / directories present.
- Runner CLI inspection: `--allow-decreasing` present and threaded
  on `main`. `--k-patience` missing → Part 0 wiring required (added
  on this branch).
- Process-conflict probe: no engine processes active before launch.

## 7. Session Evidence Path

`<SESSION_DIR> = logs/phase_6i78_stackbuilder_spy_smoke/<UTC_TIMESTAMP>/`

Subdirectories:

- `<SESSION_DIR>/dry_run/`
- `<SESSION_DIR>/smoke/`
- `<SESSION_DIR>/snapshots/`
- `<SESSION_DIR>/output/stackbuilder/`

Canonical `output/stackbuilder/` was NOT written.

## 8. SPY Workbook Provenance

| Path | Size | Age at smoke start | SHA-256 (recorded) |
|---|---:|---:|---|
| `output/impactsearch/SPY_analysis.xlsx` | 5,569,554 B | 98.5 h (~4.1 d) | captured in `<SESSION_DIR>/snapshots/pre_snapshot.json` |
| `output/impactsearch/SPY_analysis.xlsx.manifest.json` | 2,478 B | 98.5 h (~4.1 d) | captured in pre_snapshot.json |

Both within the 45-day runner default freshness window.

## 9. Commands Run

Dry-run (no `--write`, no `--allow-network-fetch`):

```
<PINNED_INTERPRETER> stackbuilder_workbook_runner.py \
    --secondaries SPY \
    --primary-source impact_xlsx \
    --impact-xlsx-dir output/impactsearch \
    --outdir <SESSION_DIR>/output/stackbuilder \
    --jobs 1 \
    --exhaustive-k 6 --k-max 12 --search beam --beam-width 12 \
    --top-n 20 --bottom-n 20 \
    --allow-decreasing --k-patience 1 \
    --duration-budget-minutes 1440 \
    --operator-budget-label phase-6i-78-dry-run \
    --skip-durable-validation --update-selected --no-progress \
    1> <SESSION_DIR>/dry_run/run.stdout.json \
    2> <SESSION_DIR>/dry_run/run.stderr.log
```

Smoke (authorized write):

```
<PINNED_INTERPRETER> stackbuilder_workbook_runner.py \
    --secondaries SPY \
    --primary-source impact_xlsx \
    --impact-xlsx-dir output/impactsearch \
    --outdir <SESSION_DIR>/output/stackbuilder \
    --jobs 1 \
    --exhaustive-k 6 --k-max 12 --search beam --beam-width 12 \
    --top-n 20 --bottom-n 20 \
    --allow-decreasing --k-patience 1 \
    --write --allow-network-fetch \
    --duration-budget-minutes 1440 \
    --operator-budget-label phase-6i-78-smoke \
    --skip-durable-validation --update-selected --no-progress \
    1> <SESSION_DIR>/smoke/run.stdout.json \
    2> <SESSION_DIR>/smoke/run.stderr.log
```

Both invocations went through a PowerShell wrapper `.ps1` under the
session dir so the parent shell's command line did not embed
`stackbuilder_workbook_runner.py` as a literal substring (the
runner's process-conflict probe matches on cmdline substrings).

## 10. Dry-Run Plan Result

`status=dry_run`, `would_call_engine=false`,
`write_requested=false`, `network_authorized=false`,
`duration_budget_minutes=1440`,
`operator_budget_label=phase-6i-78-dry-run`,
`preflight_issues=[]`, `process_conflict.status=ok`.

`per_secondary_plan` length = 1, with `secondary=SPY`. Planned
progress path under `<SESSION_DIR>/output/stackbuilder/_progress/`.

`effective_config` (every key required by spec):

| Key | Value |
|---|---|
| `primary_source` | `impact_xlsx` |
| `impact_xlsx_dir` | `output/impactsearch` |
| `outdir` | `<SESSION_DIR>/output/stackbuilder` |
| `jobs` | 1 |
| `exhaustive_k` | 6 |
| `k_max` | 12 |
| `search` | `beam` |
| `beam_width` | 12 |
| `top_n` | 20 |
| `bottom_n` | 20 |
| `allow_decreasing` | **true** |
| `k_patience` | **1** |
| `skip_durable_validation` | **true** |

Dry-run side-effect check: zero files under
`<SESSION_DIR>/output/stackbuilder/` after the dry-run. Canonical
`output/stackbuilder/` unchanged.

## 11. Smoke Run Result

| Field | Value |
|---|---|
| Runner `status` | `ok` |
| Runner `summary` | `{ok: 1, error: 0, total: 1}` |
| Runner `warnings` | `[]` |
| Runner `elapsed_seconds` | 52,450.25 |
| Per-secondary `status` | `ok` |
| Per-secondary `elapsed_seconds` | 52,450.232 |
| Final K (engine) | **K=12** |
| Final stack | `AWR[D], CLH[D], CP[I], EXPO[D], FCFS[D], LLY[I], GBCI[D], HCSG[D], JNJ[I], MO[I], AROW[D], CI[I]` |
| Final Total Capture (%) | 143.4907 |
| Final Sharpe (display) | 3.930 |
| Final Trigger Days | 268 |

`effective_config` in smoke stdout matches dry-run: `allow_decreasing
= true`, `k_patience = 1`, plus all other operator overrides.

Runner manifest fields:

- `status` = `complete`
- `validation_status` = `skipped`
- `validation_artifact_path` = `None`
- `validation_artifact_hash` = `None`
- `durable_validation_status` = `skipped`
- `durable_validation_skip_reason` = `operator_flag`
- `cli_args.skip_durable_validation` = `true`

## 12. Monitoring Summary

Launched runner PID 15100. The smoke ran ~14h 34m. The PowerShell
wrapper sampled every 5 minutes into `<SESSION_DIR>/monitor.log` —
177 SAMPLE records + LAUNCHED + FINAL + EXITED + WRAPPER_DONE.

Resource trace (selected highlights):

- RSS grew from ~455 MB at +5 min to ~990 MB+ peak during the K=6
  exhaustive scan; stabilized then declined as beam K=7..K=12 ran.
- CPU seconds tracked monotonically with wall-clock (single-job
  configuration).
- Thread count 1–3 throughout.
- No external time-based termination was issued (per spec, the
  smoke was allowed to run to natural completion).

## 13. Per-K Timing Decomposition Through K=12

Per-K artifact `combo_k=N.json` mtimes give a clean K-by-K split
because the engine writes each `combo_k=N.json` immediately on
accepting/publishing K=N's winner. The K=N artifact mtime therefore
marks the END of K=N work, not the finalize-phase write order. This
contrasts with Phase 6I-77 where all artifacts were written together
in finalize.

| Phase | Start (s from start) | Duration (s) | Evidence |
|---|---:|---:|---|
| Phase 1 preflight + secondary load | 0 | 9.5 | start → `rank_all.xlsx` mtime |
| Phase 2 ImpactSearch XLSX rank_all | 0 | 9.5 | (combined with Phase 1 in evidence) |
| Phase 2 cohort assembly | 9.5 | 2.4 | `cohort.xlsx` mtime |
| Phase 3 K=1 singleton scoring | 11.9 | 5.7 | `combo_k=1.json` mtime |
| Phase 3 K=2 exhaustive (780) | 17.6 | 5.3 | `combo_k=2.json` mtime |
| Phase 3 K=3 exhaustive (9,880) | 22.9 | 78.9 | `combo_k=3.json` mtime |
| Phase 3 K=4 exhaustive (91,390) | 101.8 | 806.5 | `combo_k=4.json` mtime |
| Phase 3 K=5 exhaustive (658,008) | 908.3 | 6,639.8 | `combo_k=5.json` mtime |
| Phase 3 K=6 exhaustive (3,838,380) | 7,548.1 | **44,849.9** | `combo_k=6.json` mtime |
| Phase 3 K=7 beam | 52,398.0 | 0.8 | `combo_k=7.json` mtime |
| Phase 3 K=8 beam | 52,398.8 | 9.3 | `combo_k=8.json` mtime |
| Phase 3 K=9 beam | 52,408.1 | 9.8 | `combo_k=9.json` mtime |
| Phase 3 K=10 beam | 52,417.9 | 10.4 | `combo_k=10.json` mtime |
| Phase 3 K=11 beam | 52,428.3 | 10.9 | `combo_k=11.json` mtime |
| Phase 3 K=12 beam | 52,439.2 | 11.3 | `combo_k=12.json` mtime |
| Finalize + manifest write | 52,450.5 | ~0.1 | `run_manifest.json` |
| **Total** | — | **52,450.5** | manifest `elapsed_seconds=52450.53` |

K levels definitely entered: K=1..K=12 (all 12 produced
`combo_k=N.json` artifacts). K=12 was reached and finalized cleanly.

The K=6 exhaustive scan over 3,838,380 combinations accounts for
**~85.5%** of the entire wall-clock. The Phase 6I-76 fast combine
path keeps the per-combo cost low (per-combo amortization at K=6
is ~11.7 ms/combo — see the per-combo reconciliation in §22), but
the **combinatorial workload itself** is the dominant cost driver
in this smoke. The K=6 workload is not part of the LEGACY Dash
default traversal (LEGACY default is `exhaustive_k=4`), so this
~12.5 h is workload-attributable to the Phase 6I-78 stress
configuration, not to a per-combo engine regression. See §22 for
the corrected runtime-vs-LEGACY interpretation.

## 14. K=1 Fast-Path Assertion Result

| Boundary | Value |
|---|---|
| `cohort.xlsx` mtime | 2026-05-22T23:15:29.870 |
| `combo_k=1.json` mtime | 2026-05-22T23:15:35.535 |
| K=1 duration after cohort | **5.7 s** |
| K=1 winner | `SBSI[D]` |
| K=1 Total Capture (%) | 428.5247 |
| K=1 Sharpe (display) | 0.57 |
| K=1 Trigger Days | 6,750 |

K=1 verdict: **PASS** (≤ 30 s).

## 15. K=12 Traversal Verification

Classification: **REACHED_K12 cleanly finalized.**

Evidence:

- `combo_k=12.json` exists with `K=12`, 12 members, and Total
  Capture present.
- Engine stderr `[PHASE3] K=12: Sharpe=3.930 (+0.1600) | TD=268 |
  Capture=143.49% | Members=[…12 members…]` (one line per K
  level from K=1 through K=12, all printed exactly once).
- Engine stderr `[RESULT] Best stack K=12: Sharpe=3.930,
  Capture=143.49%, TD=268`.
- `summary.json.final_stack_size = 12`.
- Run-directory name encodes all 12 members verbatim.
- No `Stopping at K` or `No candidate improves Total Capture` line
  in stderr.

`--allow-decreasing` was essential: Total Capture decreased from
K=1 (428.52%) to K=12 (143.49%) and Sharpe oscillated (e.g. K=8
delta −0.15, K=11 delta −0.46). With the prior runner default
(`allow_decreasing=False` + `k_patience=1`), the engine would have
stopped at K=2 or K=3, exactly as Phase 6I-77 observed.

## 16. selected_build.json Result

`<SESSION_DIR>/output/stackbuilder/SPY/selected_build.json`:

| Field | Value |
|---|---|
| `schema_version` | 1 |
| `secondary` | `SPY` |
| `selected_run_id` | `SPY-20260522_231517-15100` |
| `selected_run_dir` | under `<SESSION_DIR>/output/stackbuilder/SPY/…` |
| `selected_k` | 12 |
| `selected_metric` | `auto` |
| `total_capture` | 143.4907 |
| `sharpe_ratio` | 3.93 (display only) |
| `selection_policy` | `v2.total_capture_then_latest` |
| `operator_pinned` | false |
| `source_manifest_path` | under `<SESSION_DIR>/output/stackbuilder/SPY/…/run_manifest.json` |

Selected build path is entirely under the isolated `<SESSION_DIR>`;
nothing under canonical `output/stackbuilder/`.

## 17. Progress-Path Isolation Verification

`effective_config.effective_progress_dir` resolved to
`<SESSION_DIR>/output/stackbuilder/_progress`. Per-run progress JSON
written there (1 file). Canonical
`output/stackbuilder/_progress/` file count and latest mtime
unchanged between pre-snapshot and post-snapshot.

## 18. Bounded Cohort and rank_inverse Absence Verification

- `cohort.xlsx` row count: **40** (= `top_n 20 + bottom_n 20`).
- `combo_leaderboard.xlsx` row count: **12** (K=1 through K=12, all
  twelve published).
- `rank_inverse.*` artifacts in run directory: **0** (Phase 6I-73
  contract preserved).

## 19. Durable Validation Skip Verification

`run_manifest.json` confirms:

| Field | Value |
|---|---|
| `cli_args.skip_durable_validation` | `true` |
| `durable_validation_status` | `skipped` |
| `durable_validation_skip_reason` | `operator_flag` |
| `validation_status` | `skipped` |
| `validation_artifact_path` | `None` |
| `validation_artifact_hash` | `None` |

Engine emitted `[VALIDATION] Validation: SKIPPED (operator_flag).
No durable validation sidecar was written.` exactly once. No
durable validation sidecar produced.

## 20. Canonical Artifact Safety

Pre/post snapshot diff:

| Path | Pre | Post | Verdict |
|---|---|---|---|
| `output/stackbuilder/` | 5,228 files, latest 2026-05-15T01:12:12 | identical | unchanged |
| `output/stackbuilder/_progress/` | 648 files | identical | unchanged |
| `output/impactsearch/` | 16 files | identical | unchanged |
| `output/impactsearch/SPY_analysis.xlsx` SHA-256 | recorded | identical | unchanged |
| `output/impactsearch/SPY_analysis.xlsx.manifest.json` SHA-256 | recorded | identical | unchanged |
| `output/onepass/onepass.xlsx` SHA-256 | recorded | identical | unchanged |
| `output/validation/` | does not exist | does not exist | unchanged |
| `signal_library/data/stable/` | 71,980 files | identical | unchanged |
| `price_cache/daily/` | 6 files, latest 2026-05-15T03:11:07 | identical | unchanged |

Zero canonical diffs. `--allow-network-fetch` was authorized but the
engine did not need to refresh any cached prices for SPY's secondary
window — the existing six `price_cache/daily/` files served the run.

git tracked working tree at end of smoke: runner + focused-runner
test wiring changes from Part 0 and this evidence doc only.

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
| `Stopping at K` | **0** (engine did not stop early) |
| `No candidate improves Total Capture` | **0** |
| `Failed download` | 0 |
| `Exception` | 0 |
| `Error:` | 0 |
| `K=12` | 2 (engine `[PHASE3] K=12` + `[RESULT] Best stack K=12`) |
| `[PHASE3] K=N` lines | 12 (one per K from 1..12) |

## 22. Total Smoke Wall-Clock and Per-Combo Reconciliation

### 22.1 Measured wall-clock totals

- Total wall-clock: **52,450.5 s = 14 h 34 m 10 s ≈ 874 minutes**.
- Dominant cost: K=6 exhaustive over 3,838,380 combinations
  consumed ~44,850 s (~12 h 28 m, ~85.5% of wall).
- Phase 1 + Phase 2 (workbook load + cohort assembly): ~11.9 s
  (negligible).
- K=1..K=5 exhaustive total: ~7,536 s (~14.4% of wall).
- K=7..K=12 beam total: ~52 s (~0.1% of wall) — the Phase 6I-76 fast
  combine path keeps beam steps sub-second per K=accept.

### 22.2 Configuration mismatch versus LEGACY Dash defaults

The LEGACY Dash UI used `exhaustive_k=4` by default, so LEGACY's
actual exhaustive workload for SPY on the 40-row cohort is the
K=1..K=4 sum, with K=5..K=12 handled by beam:

- LEGACY Dash default exhaustive units (K=1..K=4):

  | K | Count |
  |---:|---:|
  | 1 | 40 |
  | 2 | 780 |
  | 3 | 9,880 |
  | 4 | 91,390 |
  | **Sum K=1..K=4** | **102,090** |

- Phase 6I-78 stress smoke exhaustive units (K=1..K=6):

  | K | Count |
  |---:|---:|
  | K=1..K=4 sum | 102,090 |
  | 5 | 658,008 |
  | 6 | 3,838,380 |
  | **Sum K=1..K=6** | **4,598,478** |

Phase 6I-78 therefore ran roughly **45× more exhaustive
candidate work** than the actual LEGACY Dash-default K=1..K=4
exhaustive configuration. The ~14h 34m wall-clock is explained by
that workload mismatch, not by a per-combo engine regression.

### 22.3 Per-combo reconciliation against the Phase 6I-76 fast-combine target

Per-K amortized timings, derived from the measured per-K durations
already recorded in §13:

| K | Combos | Duration (s) | Amortized ms/combo |
|---:|---:|---:|---:|
| 2 | 780 | 5.3 | ~6.8 |
| 3 | 9,880 | 78.9 | ~8.0 |
| 4 | 91,390 | 806.5 | ~8.8 |
| 5 | 658,008 | 6,639.8 | ~10.1 |
| 6 | 3,838,380 | 44,849.9 | ~11.7 |

These per-combo numbers are broadly consistent with the
Phase 6I-76 synthetic fast-combine target (≤ 18 ms/combo at K=4
synthetic), and they sit well under that target across every
measured K level. The mild monotone growth from ~6.8 ms/combo at
K=2 up to ~11.7 ms/combo at K=6 is expected overhead at larger K
(member-array sizes grow, cohort-cache pressure grows). Nothing
in this range indicates a per-combo engine regression versus the
Phase 6I-76 baseline.

### 22.4 Back-of-envelope LEGACY-default runtime estimate

Treating the per-combo amortizations above as a representative
estimate (not a guarantee), the LEGACY Dash-default exhaustive
workload of **102,090 units** at roughly **8-9 ms/combo** would
correspond to:

- 102,090 × 8 ms ≈ 817 s ≈ **13.6 minutes** of exhaustive scoring.
- 102,090 × 9 ms ≈ 919 s ≈ **15.3 minutes** of exhaustive scoring.

Adding the small Phase 1 + Phase 2 setup (~11.9 s here) and the
K=5..K=12 beam tail (per §13, K=7..K=12 beam totaled ~52 s; under
LEGACY-default `exhaustive_k=4`, K=5 and K=6 would also run as
beam steps, which on this engine should remain in the
beam_width × remaining-cohort × per-combo regime), the estimated
LEGACY-default total lands in the **~14-16 minute** range.

That estimate is consistent with the operator's stated ~20-minute
LEGACY SPY baseline. The correct test of this estimate is a
follow-up smoke using `--exhaustive-k 4`, not reinterpretation of
this Phase 6I-78 K=1..K=6 stress run. See §24 for the recommended
follow-up command shape.

### 22.5 Corrected runtime verdict

Per the spec runtime classification (> 60 min): **MATERIAL-SLOWDOWN
under the Phase 6I-78 stress configuration**. This is **not** an
apples-to-apples LEGACY runtime comparison: this smoke ran ~45×
more exhaustive candidate work than the LEGACY Dash default, the
per-combo amortizations are consistent with the Phase 6I-76 fast
combine target across every K level, and a back-of-envelope LEGACY-
default estimate lands in the same range as the operator's
~20-minute baseline. The 14h 34m wall-clock is workload-explained,
not per-combo-explained.

## 23. Verdicts

| Axis | Verdict | Reason |
|---|---|---|
| Runner / safety | **PASS** | `status=ok`, all required artifacts present, canonical safety zero-diff, forbidden expected-zero counts all zero, durable validation cleanly skipped, `rank_inverse` absent, cohort = 40 |
| K-depth traversal | **PASS** | K=12 reached and cleanly finalized; all 12 `combo_k=N.json` published; no early-stop diagnostic in stderr |
| Runtime-vs-LEGACY | **MATERIAL-SLOWDOWN under the Phase 6I-78 stress configuration; NOT an apples-to-apples LEGACY runtime comparison** | 14 h 34 m for SPY at `--exhaustive-k 6` is workload-explained, not per-combo-explained: ~45× more exhaustive candidate work than the LEGACY Dash default (`exhaustive_k=4`), per-combo amortization ranges ~6.8-11.7 ms/combo and stays under the Phase 6I-76 ≤18 ms/combo target across all K. The correct LEGACY runtime comparison requires a follow-up smoke at `--exhaustive-k 4` (see §22.4 / §24). |
| **Aggregate** | **SUSPECT / EVIDENCE-NEEDS-FOLLOW-UP** | runner/safety PASS + K-depth PASS; runtime axis is classified MATERIAL-SLOWDOWN by the spec rule (> 60 min) but cannot be compared directly to LEGACY defaults from this stress configuration. Apples-to-apples LEGACY comparison is deferred to a `--exhaustive-k 4` follow-up smoke. |

The smoke validates the K-depth traversal end-to-end and confirms
that Phase 6I-78's runner-only `--k-patience` wiring + the
operator's `--allow-decreasing` flag together produce the intended
K=12 behavior. The 14h 34m wall-clock is dominated by the K=6
exhaustive scan over 3,838,380 combinations — work that is **not
part of the LEGACY Dash-default traversal** (LEGACY default is
`exhaustive_k=4`). Per-combo amortizations measured in this smoke
(~6.8-11.7 ms/combo across K=2..K=6) are consistent with the
Phase 6I-76 synthetic fast-combine target, so the runtime result is
workload-explained, not a per-combo engine regression. A back-of-
envelope LEGACY-default estimate (102,090 exhaustive units × ~8-9
ms/combo ≈ 14-16 minutes; see §22.4) is consistent with the
operator's ~20-minute LEGACY SPY baseline, and the correct
apples-to-apples test is a follow-up smoke at `--exhaustive-k 4`.

## 24. Follow-Ups

- **Phase D benchmark** remains a separate authorized task. Not
  started.
- **Phase E canonical write** (promoting smoke outputs to canonical
  `output/stackbuilder/`) remains a separate authorized task. Not
  started.
- **Phase 5C validation-scope** work remains separate. Durable
  validation default is unchanged on `main`; the skip used here is
  Phase 6I-75's operator-explicit opt-in.
- **Apples-to-apples LEGACY-default follow-up smoke.** The correct
  test of the runtime hypothesis (LEGACY ~20 min for SPY is broadly
  achievable on this engine under LEGACY-default traversal) is a
  follow-up SPY smoke that uses the actual LEGACY Dash default
  exhaustive depth. Suggested command shape:

  ```
  --exhaustive-k 4
  --k-max 12
  --search beam
  --beam-width 12
  --allow-decreasing
  --k-patience 1
  --top-n 20
  --bottom-n 20
  ```

  Combined with `--secondaries SPY`, the same isolated `--outdir`
  pattern, `--skip-durable-validation`, and the standard write
  + budget flags. That smoke is the right place to settle the
  apples-to-apples runtime question and validate the §22.4
  back-of-envelope estimate against measured engine wall-clock.

- **Deeper-than-LEGACY exhaustive stress validation (this PR).**
  Phase 6I-78 demonstrates that the current engine can produce
  correct artifacts and reach K=12 under the deeper K=1..K=6
  exhaustive configuration. That result stands independent of the
  LEGACY runtime question and is useful evidence for any future
  deeper-stress validation.
- StackBuilder-direct call paths (`stackbuilder.py` as an entry
  point) remain out of scope. This smoke used
  `stackbuilder_workbook_runner.py` exclusively.

---

### Constraints verified by this PR

- SPY-only controlled smoke against the post-Phase-6I-76 current
  engine.
- StackBuilder was run only through `stackbuilder_workbook_runner.py`.
- Part 0 added the runner-only `--k-patience` wiring (CLI flag +
  `_effective_config` entry + namespace pass-through + 9 focused
  tests). `stackbuilder.py` and all other engine files are
  unchanged.
- Smoke explicitly passed `--allow-decreasing`, `--k-patience 1`,
  `--exhaustive-k 6`, `--k-max 12`, `--search beam`,
  `--beam-width 12`, `--top-n 20`, `--bottom-n 20`.
- Durable validation was skipped with `--skip-durable-validation`.
- Output root was isolated under `logs/`.
- No canonical `output/stackbuilder/` write was authorized.
- Per-K timing was decomposed K=1..K=12 from `combo_k=N.json`
  artifact mtimes (an honest split because the engine writes those
  artifacts at K-accept time, not in finalize).
- K=1 fast-path assertion enforced: PASS at 5.7 s.
- K=12 traversal explicitly verified: REACHED_K12, cleanly
  finalized.
- `rank_inverse` artifacts absent.
- `selected_build.json` under isolated `<SESSION_DIR>` only.
- No OnePass / ImpactSearch / TrafficFlow / Spymaster / Confluence /
  MTF run launched.
- No StackBuilder engine code changed.
- Smoke evidence under `logs/` is not staged.
