# Phase 6I-79 StackBuilder Production Run Evidence

## 1. Scope and Non-Goals

Production StackBuilder run for all 8 ImpactSearch secondaries
under the actual LEGACY Dash-default traversal configuration
(`--exhaustive-k 4` plus K=5..K=12 beam). Outputs were written to
canonical `output/stackbuilder/` for downstream TrafficFlow / MTF /
Confluence pipeline use.

This is **not** an isolated smoke. It is **not** a benchmark. It
is **not** a code-change PR.

Non-goals: TrafficFlow / MTF / Confluence executions (separate
follow-ups), Phase E canonical write of any other engine,
Phase 5C validation-scope changes, Phase 7+ universe-wide beam
research. No engine code modified.

## 2. Engine State Reference

`main` HEAD at run start: `9481ff9` (Phase 6I-78 squash, with
Phase 6I-77 and Phase 7+ scoping already merged on `main`).

- Phase 6I-73 (merged): Sharpe removed as a StackBuilder
  selection criterion; inverse rescoring bounded; `rank_inverse`
  artifact removed from output schema.
- Phase 6I-75 (merged): StackBuilder consumer-only signal-library
  loader; `--skip-durable-validation` opt-in CLI flag.
- Phase 6I-76 (merged): vectorized fast combine path in
  `_combine_signals_fast`; synthetic K-search hot path measured
  ~8.4 ms/combo, ≤ 18 ms/combo target.
- Phase 6I-77 (merged): multi-secondary smoke evidence (corrected
  framing — K=2 accepted, K=4 attempted under the missing
  `--allow-decreasing` configuration).
- Phase 6I-78 (merged): runner-side `--k-patience` wiring + SPY
  K=12 stress-smoke evidence (corrected framing — `--exhaustive-k
  6` was deeper-than-LEGACY; LEGACY default is `--exhaustive-k 4`).

## 3. LEGACY-Default Configuration Used

Traversal controls (the actual LEGACY Dash defaults, as clarified
by Phase 6I-78):

| Flag | Value |
|---|---|
| `--exhaustive-k` | 4 |
| `--k-max` | 12 |
| `--search` | beam |
| `--beam-width` | 12 |
| `--top-n` | 20 |
| `--bottom-n` | 20 |
| `--allow-decreasing` | (set) |
| `--k-patience` | 1 |

Other controls:

| Flag | Value | Role |
|---|---|---|
| `--secondaries` | `AAPL,AMZN,GOOGL,META,MSFT,NVDA,SPY,TSLA` | all 8 |
| `--primary-source` | `impact_xlsx` | primaries from ImpactSearch ranking |
| `--impact-xlsx-dir` | `output/impactsearch` | workbook directory |
| `--outdir` | `output/stackbuilder` | **canonical** output root (intentional) |
| `--jobs` | 1 | sequential per-secondary |
| `--write` | (set) | authorized production write |
| `--allow-network-fetch` | (set) | authorized price-cache refresh path |
| `--duration-budget-minutes` | 1440 | overnight evidence budget (NOT a target) |
| `--operator-budget-label` | `phase-6i-79-production` | provenance label |
| `--skip-durable-validation` | (set) | Phase 5C fail-closed bypassed by operator opt-in |
| `--update-selected` | (set) | refresh canonical `selected_build.json` |
| `--no-progress` | (set) | quieter logging |

## 4. Pre-Checks

- cwd: `<PROJECT_ROOT>`.
- Starting branch: `main`. Starting HEAD: `9481ff9`.
- Phase 6I-77 (`#292`), Phase 6I-78 (`#293`), and Phase 7+
  scoping (`#294`) all present on `main`.
- Working tree clean.
- Runner CLI inspection confirmed: `--allow-decreasing` and
  `--k-patience` both wired through argparse, `_effective_config`,
  and `build_stackbuilder_args_namespace`.
- Process-conflict probe: no engine processes active before
  launch.
- 8 ImpactSearch workbooks discovered with matching sidecars; max
  age ~4.8 days; all within the 45-day runner-default freshness
  window.

## 5. Session Evidence Path

`<SESSION_DIR> = logs/phase_6i79_stackbuilder_production_run/<UTC_TIMESTAMP>/`

Subdirectories:

- `<SESSION_DIR>/dry_run/`
- `<SESSION_DIR>/run/`
- `<SESSION_DIR>/snapshots/`

Pre/post canonical snapshots persisted under
`<SESSION_DIR>/snapshots/`. The actual StackBuilder run outputs
were written to canonical `output/stackbuilder/<SECONDARY>/<run-dir>/`
per the spec. The session dir itself contains only evidence
(stdout, stderr, monitor.log, snapshot JSON, wrapper scripts).

## 6. Secondaries Inventoried

Sorted deterministically: `AAPL, AMZN, GOOGL, META, MSFT, NVDA,
SPY, TSLA`. Count: **8** (matches expected).

Each `<SECONDARY>_analysis.xlsx` (size ~5.5 MB, age 91–115 hours
at run start) had a matching
`<SECONDARY>_analysis.xlsx.manifest.json` sidecar. All XLSX +
sidecar SHA-256 values recorded in
`<SESSION_DIR>/snapshots/pre_snapshot.json`.

## 7. Commands Run

Dry-run (no `--write`, no `--allow-network-fetch`):

```
<PINNED_INTERPRETER> stackbuilder_workbook_runner.py \
    --secondaries <SECONDARY_LIST> \
    --primary-source impact_xlsx \
    --impact-xlsx-dir output/impactsearch \
    --outdir output/stackbuilder \
    --jobs 1 \
    --exhaustive-k 4 --k-max 12 --search beam --beam-width 12 \
    --top-n 20 --bottom-n 20 \
    --allow-decreasing --k-patience 1 \
    --duration-budget-minutes 1440 \
    --operator-budget-label phase-6i-79-dry-run \
    --skip-durable-validation --update-selected --no-progress \
    1> <SESSION_DIR>/dry_run/run.stdout.json \
    2> <SESSION_DIR>/dry_run/run.stderr.log
```

Production run (authorized write):

```
<PINNED_INTERPRETER> stackbuilder_workbook_runner.py \
    --secondaries <SECONDARY_LIST> \
    --primary-source impact_xlsx \
    --impact-xlsx-dir output/impactsearch \
    --outdir output/stackbuilder \
    --jobs 1 \
    --exhaustive-k 4 --k-max 12 --search beam --beam-width 12 \
    --top-n 20 --bottom-n 20 \
    --allow-decreasing --k-patience 1 \
    --write --allow-network-fetch \
    --duration-budget-minutes 1440 \
    --operator-budget-label phase-6i-79-production \
    --skip-durable-validation --update-selected --no-progress \
    1> <SESSION_DIR>/run/run.stdout.json \
    2> <SESSION_DIR>/run/run.stderr.log
```

Both invocations went through a PowerShell wrapper `.ps1` under
the session dir so the parent shell's command line did not embed
`stackbuilder_workbook_runner.py` as a literal substring (the
runner's process-conflict probe matches on cmdline substrings).

## 8. Dry-Run Result

`status=dry_run`, `would_call_engine=false`, `write_requested=false`,
`network_authorized=false`, `duration_budget_minutes=1440`,
`operator_budget_label=phase-6i-79-dry-run`, `preflight_issues=[]`,
`process_conflict.status=ok`.

`per_secondary_plan` length = 8; all 8 expected secondaries
present; every planned progress path under
`output/stackbuilder/_progress/`.

`effective_config` confirmed (matches LEGACY-default):

| Key | Value |
|---|---|
| `primary_source` | `impact_xlsx` |
| `impact_xlsx_dir` | `output/impactsearch` |
| `outdir` | `output/stackbuilder` |
| `jobs` | 1 |
| `exhaustive_k` | **4** |
| `k_max` | 12 |
| `search` | `beam` |
| `beam_width` | 12 |
| `top_n` | 20 |
| `bottom_n` | 20 |
| `allow_decreasing` | **true** |
| `k_patience` | **1** |
| `skip_durable_validation` | **true** |

Dry-run side-effect check: canonical
`output/stackbuilder/` file count + latest mtime unchanged
between pre-snapshot and a post-dry-run probe. Dry-run wrote zero
canonical artifacts.

## 9. Production Run Result

| Field | Value |
|---|---|
| Runner `status` | `ok` |
| Runner `summary` | `{ok: 8, error: 0, total: 8}` |
| Runner `warnings` | `[]` |
| Runner `elapsed_seconds` | 7,057.64 |
| Total wall-clock | **~117.6 min ≈ 1 h 58 m** |
| 6-hour ceiling | not hit |
| Runner exit code | 0 |

`effective_config` in run stdout matches dry-run: `exhaustive_k=4`,
`k_max=12`, `search=beam`, `beam_width=12`, `top_n=20`,
`bottom_n=20`, `allow_decreasing=true`, `k_patience=1`,
`skip_durable_validation=true`, `outdir=output/stackbuilder`.

Monitor wrapper recorded 25 SAMPLE samples + LAUNCHED + FINAL +
EXITED + WRAPPER_DONE. RSS peaked ~1.72 GB during the K=5 / K=6
beam stages; thread count 1–3 throughout. No external time-based
termination was issued.

## 10. Per-Secondary Validation Summary

For every secondary, the following invariants held:

- Fresh run directory under
  `output/stackbuilder/<SECONDARY>/<seedTC__…>/`, name encodes
  all 12 final stack members.
- 18 files in the run dir: `rank_all.xlsx`, `rank_direct.xlsx`,
  `cohort.xlsx`, `combo_k=1.json` .. `combo_k=12.json`,
  `combo_leaderboard.xlsx`, `run_manifest.json`, `summary.json`.
- All required artifacts present (`has_required = True`).
- `cohort.xlsx` row count: **40** (= `top_n 20 + bottom_n 20`).
- `combo_leaderboard.xlsx` row count: **12**.
- 0 `rank_inverse.*` artifacts (Phase 6I-73 contract preserved).

Manifest invariants for every secondary:

- `cli_args.skip_durable_validation = true`
- `validation_status = skipped`
- `durable_validation_status = skipped`
- `durable_validation_skip_reason = operator_flag`
- `validation_artifact_path = None`
- `validation_artifact_hash = None`

`selected_build.json` invariants for every secondary:

- Exists at
  `output/stackbuilder/<SECONDARY>/selected_build.json`.
- `selected_run_dir` points to the **fresh** run directory created
  in this Phase 6I-79 run (not a historical run).
- `selected_k = 12`.
- `selection_policy = v2.total_capture_then_latest`.
- `operator_pinned = false`.

## 11. Final K Reached Per Secondary

All 8 secondaries reached **K=12** cleanly.

| Secondary | Final K | combo_k levels published | combo_leaderboard rows | Final stack size |
|---|---:|---|---:|---:|
| AAPL | 12 | 1..12 | 12 | 12 |
| AMZN | 12 | 1..12 | 12 | 12 |
| GOOGL | 12 | 1..12 | 12 | 12 |
| META | 12 | 1..12 | 12 | 12 |
| MSFT | 12 | 1..12 | 12 | 12 |
| NVDA | 12 | 1..12 | 12 | 12 |
| SPY | 12 | 1..12 | 12 | 12 |
| TSLA | 12 | 1..12 | 12 | 12 |

Per-secondary summary headlines (from `summary.json`):

| Secondary | elapsed (s) | wall-clock | K=1 winner | Best Total Capture (%) | Best Sharpe (display) |
|---|---:|---:|---|---:|---:|
| AAPL | 1,195.7 | 19m56s | WEN[D] | 223.4606 | 4.60 |
| AMZN | 888.3 | 14m48s | AIRT[D] | 332.8131 | 7.33 |
| GOOGL | 741.3 | 12m21s | ACO-X.TO[D] | 111.7194 | 7.88 |
| META | 584.2 | 9m44s | SMMU[I] | 172.0211 | 10.67 |
| MSFT | 1,145.6 | 19m06s | IMO[I] | 295.7148 | 4.96 |
| NVDA | 866.9 | 14m27s | ALK-B.CO[D] | 194.6708 | 7.22 |
| SPY | 1,000.2 | 16m40s | SBSI[D] | 157.4113 | 4.15 |
| TSLA | 631.1 | 10m31s | EGY.AX[D] | 212.6952 | 8.08 |

Inverse K=1 winners (mode suffix `[I]`): META (SMMU[I]) and
MSFT (IMO[I]) — 2/8.

## 12. selected_build.json Verification

For every secondary, `selected_build.json` was written under
`output/stackbuilder/<SECONDARY>/selected_build.json` and:

- `selected_run_dir` resolves to the fresh
  `output/stackbuilder/<SECONDARY>/seedTC__…/` directory
  created by this Phase 6I-79 run.
- `selected_k = 12`.
- `selected_metric = auto`.
- `total_capture` and `sharpe_ratio` match the per-secondary best
  values in §11.
- `selection_policy = v2.total_capture_then_latest`.
- `operator_pinned = false`.
- `source_manifest_path` points at the same secondary's fresh
  `run_manifest.json` under the canonical run dir.

## 13. Canonical Artifact Safety

Pre/post snapshot diff (recorded at
`<SESSION_DIR>/snapshots/pre_snapshot.json` and
`<SESSION_DIR>/snapshots/post_snapshot.json`):

| Path | Pre | Post | Verdict |
|---|---|---|---|
| `output/stackbuilder/` | 5,228 files | 5,388 files | CHANGED (authorized, see §14) |
| `output/stackbuilder/_progress/` | 648 files | 656 files | CHANGED (authorized, see §14) |
| `output/impactsearch/` (count + dir mtime) | 16 files, mtime 2026-05-19T20:03 | identical | unchanged |
| `output/impactsearch/*.xlsx` SHA-256 (all 8) | recorded | identical | **all 8 unchanged** |
| `output/impactsearch/*.manifest.json` SHA-256 (all 8) | recorded | identical | **all 8 unchanged** |
| `output/onepass/onepass.xlsx` SHA-256 | recorded | identical | unchanged |
| `output/validation/` | does not exist | does not exist | unchanged |
| `signal_library/data/stable/` | 71,980 files | identical | unchanged |
| `price_cache/daily/` | 6 files, latest mtime 2026-05-15T03:11 | identical | unchanged |

Non-StackBuilder canonical artifact safety: **PASS** (all
expected-unchanged roots are byte-identical). The
`--allow-network-fetch` flag was authorized but the engine did not
need to refresh any cached secondary prices — the existing six
`price_cache/daily/` files served all 8 secondaries.

## 14. Authorized Write Summary: output/stackbuilder Changes

Authorized write delta:

- `output/stackbuilder/`: **+160 files** (5,228 → 5,388),
  latest mtime advanced to 2026-05-23T17:42:38.
- `output/stackbuilder/_progress/`: **+8 files** (648 → 656),
  one per secondary, latest mtime advanced to
  2026-05-23T17:42:38.

The +160 file delta is consistent with 8 secondaries × 18 files
per fresh run dir (rank_all + rank_direct + cohort +
combo_k=1..12 + combo_leaderboard + run_manifest + summary =
18 files) + 8 × `selected_build.json` updates at the
`<SECONDARY>/` parent root, plus minor cumulative overhead from
the runner partial writes (which the runner re-finalizes via
`os.replace` per the Phase 6I-70 contract). All writes are
authorized production behavior; no canonical-safety violation.

These outputs are intentionally **not staged** in git per the
spec — they are runtime artifacts for downstream pipeline use.

## 15. stderr / Warning Scan

Run stderr: 18,851 B, 144 lines.

| Pattern | Count |
|---|---:|
| `[ONEPASS:` | **0** |
| `Forcing rebuild` | **0** |
| `Traceback` | **0** |
| `[STACKBUILDER:library_missing]` | 0 |
| `[STACKBUILDER:library_invalid]` | 0 |
| `[STACKBUILDER:library_unreadable]` | 0 |
| `[STACKBUILDER:library_manifest_mismatch]` | 0 |
| `Stopping at K` | **0** (no early stops) |
| `No candidate improves Total Capture` | **0** |
| `Failed download` | 0 |
| `Exception` | 0 |
| `Error:` | 0 |
| `[VALIDATION] Validation: SKIPPED` | 8 (one per secondary) |
| `[COMPLETE]` | 8 |
| `[RESULT]` | 8 |
| `[PHASE3] K=N` lines | 96 (12 K-levels × 8 secondaries) |

Every forbidden expected-zero count is 0. The 96 `[PHASE3] K=N`
lines (12 per secondary × 8) are direct evidence that every K
level from K=1 through K=12 was published for every secondary
without invoking the patience stop logic.

## 16. Total Wall-Clock

Total: **7,057.64 s = 117.6 min ≈ 1 h 57 m 38 s** for 8
secondaries. Per-secondary range: 9m44s (META) to 19m56s (AAPL);
mean ~14.7 min. This is **consistent with the operator's
~20 min/secondary LEGACY baseline** and with Phase 6I-78's
back-of-envelope LEGACY-default runtime estimate of ~14-16 min
per secondary.

## 17. Verdicts Per Secondary

| Secondary | Verdict | Elapsed | Final K | K=1 Winner | Selected Build Updated | Notes |
|---|---|---:|---:|---|---|---|
| AAPL | **PASS** | 19m56s | 12 | WEN[D] | yes (fresh) | all 12 combo_k levels; cohort 40; rank_inverse 0; durable val skipped |
| AMZN | **PASS** | 14m48s | 12 | AIRT[D] | yes (fresh) | same |
| GOOGL | **PASS** | 12m21s | 12 | ACO-X.TO[D] | yes (fresh) | same |
| META | **PASS** | 9m44s | 12 | SMMU[I] | yes (fresh) | same; inverse K=1 winner |
| MSFT | **PASS** | 19m06s | 12 | IMO[I] | yes (fresh) | same; inverse K=1 winner |
| NVDA | **PASS** | 14m27s | 12 | ALK-B.CO[D] | yes (fresh) | same |
| SPY | **PASS** | 16m40s | 12 | SBSI[D] | yes (fresh) | same |
| TSLA | **PASS** | 10m31s | 12 | EGY.AX[D] | yes (fresh) | same |

## 18. Aggregate Verdict

**PASS.**

All eight per-secondary verdicts are PASS. Runner exited normally
with `status=ok`. Every required artifact is present in every
fresh run directory. `selected_build.json` updated for all 8 and
points to fresh Phase 6I-79 run directories. Every expected-zero
forbidden string is zero. Non-StackBuilder canonical artifacts
are byte-identical to pre-snapshot. The only canonical changes are
the authorized `output/stackbuilder/` writes documented in §14.

Wall-clock landed at ~117.6 min for 8 secondaries — consistent
with the operator's ~20 min/secondary LEGACY baseline and with
the Phase 6I-78 §22.4 back-of-envelope LEGACY-default estimate.
The Phase 6I-77 "engine produced bad builds" framing concern is
explicitly resolved: this run reaches K=12 cleanly under the
actual LEGACY-default traversal, with no early-stop diagnostic
fired anywhere in stderr.

## 19. Follow-Ups: TrafficFlow Headless Dev, MTF, Confluence

- **TrafficFlow headless development** can now consume the
  canonical `output/stackbuilder/<SECONDARY>/` artifacts produced
  by this run. Path remains a separate authorized task.
- **MTF (multi-timeframe builder)** consumption of these
  StackBuilder outputs remains a separate authorized task.
- **Confluence** consumption of MTF outputs downstream remains a
  separate authorized task.
- **Phase 7+ universe-wide beam research** remains parked in its
  scoping doc (`md_library/shared/2026-05-23_PHASE_7_PLUS_UNIVERSE_WIDE_BEAM_SCOPING.md`).
  Not started.
- No engine/runtime code changed in this PR; no OnePass /
  ImpactSearch / TrafficFlow / Spymaster / Confluence / MTF run
  was launched. StackBuilder was run only through
  `stackbuilder_workbook_runner.py`.
