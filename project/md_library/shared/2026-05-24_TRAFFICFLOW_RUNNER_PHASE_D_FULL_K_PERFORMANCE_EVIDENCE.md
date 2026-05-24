# TrafficFlow Runner Phase D - Full-K Performance Evidence

Session date (UTC): 2026-05-24
Session directory (gitignored): `<SESSION_DIR>` =
`logs/trafficflow_phase_d_full_k_performance/20260524T103201Z/`
Branch: `trafficflow-runner-phase-d-full-k-performance`

This document captures runtime, peak memory (RSS / VMS), CPU usage,
artifact counts, and canonical-safety evidence for
`trafficflow_runner.py --write` across all 8 Phase 6I-79 secondaries
with `K=1..12` under isolated output mode. It is the first task to
exercise the K=7..12 surface; PR #309 and PR #310 deliberately covered
only the K=1,2,3,4,6 safety subset.

**Headline result.** Canonical safety holds: no canonical artifact
changed and all 8 `price_cache/daily/<SEC>.csv` files are SHA-256,
size, and mtime byte-identical pre/post. **Phase D performance is
characterized for the 46 ELIGIBLE cells the runner executed; the
remaining 50 cells (K=7..12 universally, plus a small set of K=5 / K=8
PKL-gated cells) were STALE-GATED or PKL-GATED and could not be
measured under this task's no-refresh rule.** All eight invocations
therefore exited with `exit_code=1, status=partial` (the runner's
documented signal when any requested cell was not eligible). This is
runner-as-designed behaviour, not a regression; see Section 11
(Critical Finding) for the operator-level read.

---

## 1. Scope and Non-Goals

In scope:

- Sequential `trafficflow_runner.py --write` invocations against all 8
  Phase 6I-79 secondaries (SPY, AAPL, AMZN, GOOGL, META, MSFT, NVDA,
  TSLA) at `K=1,2,3,4,5,6,7,8,9,10,11,12`.
- Per-secondary wall-clock, per-cell elapsed (from
  `run_manifest.json`), peak RSS / VMS, accumulated CPU user / system
  time captured via a session-local `psutil`-based polling wrapper.
- Per-cell artifact counts, selected-build provenance verification,
  privacy sanitization.
- Pre/post canonical safety snapshots across 9 roots plus per-secondary
  `selected_build.json`, `combo_leaderboard.xlsx`, `combo_k=1..12.json`,
  `onepass.xlsx`, and 117 member PKLs.

Out of scope:

- Phase E canonical writes.
- `PARALLEL_SUBSETS=1` comparison.
- Parallel secondary execution.
- Runner instrumentation changes.
- Engine / profile-guided optimization.
- Code or test modifications.
- PKL refresh.

---

## 2. References

- PR #301 - bare-compute K-benchmark for `K=1,2,3,4,6`; established
  the `PARALLEL_SUBSETS` default of 0 (no parallel speedup observed).
- PR #308 - runner amendment pinning the engine
  network/price-cache surface when `--allow-network-fetch` is not
  passed.
- PR #309 - SPY/AAPL Phase C re-validation under PR #308 for
  `K=1,2,3,4,6`. Price caches byte-identical pre/post.
- PR #310 - broader Phase C smoke under PR #308 for the remaining 6
  secondaries with the same K subset. Combined with PR #309, the full
  8-secondary K=1,2,3,4,6 safety surface is verified.
- PR #305 - stale-PKL repair sweep (47 PKLs). Established that the
  K=1,2,3,4,6 union of members for the 8 secondaries is fresh against
  the current benchmark as-of-date.
- PR #302 - Phase A scoping doc.

---

## 3. Why This Expands to K=1..12

Phase 6I-79 StackBuilder writes `combo_k=1.json` through
`combo_k=12.json` per secondary. PR #309 / PR #310 intentionally
restricted the smoke to `K=1,2,3,4,6` while the network/price-cache
safety surface was being validated. With safety verified, Phase D's
mandate is to characterize runtime and memory for the entire
operational K surface (K=1..12), so that an informed decision can be
made about Phase E (canonical writes).

The result of this task is that **the runner refuses to run K cells
whose member PKLs are not fresh against the benchmark as-of-date**
(STALE-GATED) or whose member PKLs are missing (PKL-GATED). Today,
every secondary's K=7..12 surface (and a small handful of K=5 / K=8
cells) is in this state. Per-cell measurement for those K levels is
therefore not possible under the no-refresh rule of this task.

---

## 4. Test Suite Re-Run Confirmation

Command shape:

    <PINNED_INTERPRETER> -m pytest test_scripts/test_trafficflow_runner.py -q

Result: `68 passed in 2.26s` (post-PR-#308 expected suite size).

---

## 5. Pre-Run Canonical Safety Snapshot

Captured to `<SESSION_DIR>/preflight/pre_run_snapshot.json`.

| Root                              | File count |
|-----------------------------------|------------|
| `output/stackbuilder/`            | 5388       |
| `output/impactsearch/`            | 16         |
| `output/onepass/`                 | 2          |
| `output/trafficflow/`             | absent     |
| `output/validation/`              | 0          |
| `signal_library/data/stable/`     | 71980      |
| `cache/results/`                  | 3267       |
| `cache/status/`                   | 1648       |
| `price_cache/daily/`              | 12         |

SHAs captured: 8 `selected_build.json`, 8 `combo_leaderboard.xlsx`,
8 x 12 = 96 `combo_k=N.json`, `onepass.xlsx`, and 117 member PKLs
(union of K=1..12 members across the 8 secondaries).

Central verification targets (size and first-16 SHA-256):

| File                              | Size (B) | SHA-256 first 16 |
|-----------------------------------|----------|------------------|
| `price_cache/daily/SPY.csv`       | 232006   | `bbd8f28f3e3c9c83` |
| `price_cache/daily/AAPL.csv`      | 348338   | `29490141806b715c` |
| `price_cache/daily/AMZN.csv`      | 220020   | `d531dc0c20012b1c` |
| `price_cache/daily/GOOGL.csv`     | 165856   | `6a4d020dd803fc81` |
| `price_cache/daily/META.csv`      | 104771   | `7e7756f2f883fca3` |
| `price_cache/daily/MSFT.csv`      | 287879   | `522086fcceb36df8` |
| `price_cache/daily/NVDA.csv`      | 212621   | `16daaa88f3768187` |
| `price_cache/daily/TSLA.csv`      | 121445   | `4778b43bc7f76035` |

---

## 6. Measurement Methodology

Tool: a session-local Python wrapper using `psutil 6.0.0`. The
wrapper:

1. Launches `trafficflow_runner.py` via `subprocess.Popen` with
   `stdout` / `stderr` redirected to files under `<SESSION_DIR>/runs/`.
2. Polls the runner process and all descendants every 0.5 seconds.
3. At each sample, sums `memory_info().rss` and
   `memory_info().vms` across the process tree; tracks the maximum
   observed sum for each.
4. At each sample, refreshes the per-PID `cpu_times().user` and
   `cpu_times().system` last-seen values; on exit, sums these across
   PIDs to get total CPU user / system seconds.
5. Records elapsed wall-clock, exit code, sample count, and writes
   one measurement JSON per secondary to
   `<SESSION_DIR>/measurements/<SECONDARY>_measurement.json`.

Memory fields captured: `peak_rss_bytes`, `peak_vms_bytes`. USS / PSS
are not captured (psutil does not expose them on Windows by default).

CPU fields captured: `cpu_user_seconds`, `cpu_system_seconds`,
`cpu_total_seconds`, `cpu_wall_ratio`.

Polling interval: 0.5 s. Inter-invocation pause: 5 s (so subprocess
teardown from one run does not bleed into the next measurement).

Measurement noise sources observed: none beyond the standard Windows
desktop process mix. No other engine / runner / Dash / refresher
processes were active at the start of the run.

Wrapper script (gitignored): `<SESSION_DIR>/orchestrator.py`. The
script is not committed.

---

## 7. Invocation Methodology

Exact command shape (placeholders) per secondary:

    <PINNED_INTERPRETER> trafficflow_runner.py \
        --secondaries <SECONDARY> \
        --k-range 1,2,3,4,5,6,7,8,9,10,11,12 \
        --stackbuilder-root output/stackbuilder \
        --output-dir <SESSION_DIR>/isolated_output/<SECONDARY> \
        --write

Flags explicitly NOT passed:

- `--refresh-missing-pkls`
- `--refresh-stale-prices`
- `--allow-network-fetch`
- `--explicit-build`

Environment variables explicitly NOT set:

- `PARALLEL_SUBSETS` (no parallelism override)
- `TRAFFICFLOW_PARALLEL_SUBSETS`

Invocation order (sequential): SPY, AAPL, AMZN, GOOGL, META, MSFT,
NVDA, TSLA.

Effective config per invocation (from runner stdout JSON, all 8
secondaries identical): `write_mode=isolated`, `write_authorized=true`,
`output_dir_isolated=true`, `canonical_write_blocked=false`,
`allow_network_fetch=false`, `parallel_subsets` not surfaced in
effective_config (default 0 honored).

---

## 8. Per-Secondary Correctness Verification

Every invocation produced JSON-parseable stdout, a populated
`run_manifest.json`, and a populated `run.stdout.json`. Selected-build
provenance matched the pre-snapshot SHA byte-for-byte for all 8
secondaries (`explicit_build_override=false`). Privacy sanitization
across all 24 runner JSON artifacts (8 captured stdout, 8 manifests,
8 stdout sidecars) showed zero denylist-token hits and zero
drive-letter pattern matches. Zero `.tmp` residue under any isolated
output directory.

Per-cell eligibility classification (from
`benchmark_eligibility.<SEC>` in run.stdout.json):

| Secondary | ELIGIBLE Ks                              | STALE-GATED Ks                | PKL-GATED Ks |
|-----------|-------------------------------------------|--------------------------------|---------------|
| SPY       | K1, K2, K3, K4, K5, K6                    | K7, K8, K9, K10, K11, K12      | -             |
| AAPL      | K1, K2, K3, K4, K5, K6                    | K7, K8, K9, K10, K11, K12      | -             |
| AMZN      | K1, K2, K3, K4, K5, K6                    | K7, K8, K9, K10, K11, K12      | -             |
| GOOGL     | K1, K2, K3, K4, K6                        | K5, K7, K8, K9, K10, K11, K12  | -             |
| META      | K1, K2, K3, K4, K5, K6, K7                | K9, K10, K11, K12              | K8            |
| MSFT      | K1, K2, K3, K4, K5, K6                    | K7, K8, K9, K10, K11, K12      | -             |
| NVDA      | K1, K2, K3, K4, K6                        | K7, K8, K9, K10, K11, K12      | K5            |
| TSLA      | K1, K2, K3, K4, K6                        | K7, K9, K10, K11, K12          | K5, K8        |

Per-secondary `write_summary.cells_*` counts (all internally
consistent; `eligible == written` in every case; no errors):

| Secondary | requested | eligible | written | skipped | errored |
|-----------|-----------|----------|---------|---------|---------|
| SPY       | 12        | 6        | 6       | 6       | 0       |
| AAPL      | 12        | 6        | 6       | 6       | 0       |
| AMZN      | 12        | 6        | 6       | 6       | 0       |
| GOOGL     | 12        | 5        | 5       | 7       | 0       |
| META      | 12        | 7        | 7       | 5       | 0       |
| MSFT      | 12        | 6        | 6       | 6       | 0       |
| NVDA      | 12        | 5        | 5       | 7       | 0       |
| TSLA      | 12        | 5        | 5       | 7       | 0       |
| Total     | 96        | 46       | 46      | 50      | 0       |

Artifact list completeness: the `artifacts_written` arrays in
`run_manifest.json` and `run.stdout.json` list every actually-written
board-row file plus the run-level files; the lists are internally
self-consistent (manifest and stdout-sidecar counts match each other
and match `write_summary.artifacts_written_count`). They do not list
files for STALE-/PKL-GATED cells because no such files were written.

Every secondary's invocation finished with `exit_code=1` because
`status=partial` (cells were skipped); this is the runner's documented
signal when not every requested cell was eligible. It is not a fault
of the runner under this measurement.

---

## 9. Performance Summary

### 9.1 Per-secondary wall-clock and totals

| Secondary | Wall (s) | Exit | Cells written | Status   |
|-----------|----------|------|---------------|----------|
| SPY       | 17.27    | 1    | 6/12          | partial  |
| AAPL      | 19.74    | 1    | 6/12          | partial  |
| AMZN      |  8.61    | 1    | 6/12          | partial  |
| GOOGL     |  7.60    | 1    | 5/12          | partial  |
| META      | 18.22    | 1    | 7/12          | partial  |
| MSFT      | 18.73    | 1    | 6/12          | partial  |
| NVDA      |  7.59    | 1    | 5/12          | partial  |
| TSLA      |  5.57    | 1    | 5/12          | partial  |
| Total     | 103.32   |  -   | 46/96         |  -       |

### 9.2 Per-K elapsed distribution (ELIGIBLE cells only)

| K  | n | min (s) | median (s) | max (s) | mean (s) |
|----|---|---------|------------|---------|----------|
| 1  | 8 | 0.17    | 0.33       | 0.44    | 0.34     |
| 2  | 8 | 0.31    | 0.51       | 0.78    | 0.52     |
| 3  | 8 | 0.41    | 0.59       | 1.20    | 0.72     |
| 4  | 8 | 0.54    | 1.06       | 2.41    | 1.27     |
| 5  | 5 | 1.59    | 3.15       | 3.78    | 2.83     |
| 6  | 8 | 1.05    | 3.08       | 8.62    | 4.32     |
| 7  | 1 | 7.85    | 7.85       | 7.85    | 7.85     |
| 8  | 0 | n/a     | n/a        | n/a     | n/a      |
| 9  | 0 | n/a     | n/a        | n/a     | n/a      |
| 10 | 0 | n/a     | n/a        | n/a     | n/a      |
| 11 | 0 | n/a     | n/a        | n/a     | n/a      |
| 12 | 0 | n/a     | n/a        | n/a     | n/a      |

`n` is the count of ELIGIBLE cells across the 8 secondaries at that K.

### 9.3 Per-secondary peak memory

Peak RSS / VMS observed by the wrapper across the runner process tree
(0.5 s polling). USS / PSS not captured on Windows by psutil 6.0.0.

| Secondary | Peak RSS (MiB) | Peak VMS (MiB) | Poll samples |
|-----------|----------------|----------------|---------------|
| SPY       | 285.8          | 264.6          | 34            |
| AAPL      | 311.2          | 287.2          | 39            |
| AMZN      | 268.8          | 244.6          | 17            |
| GOOGL     | 240.1          | 216.4          | 15            |
| META      | 249.5          | 224.4          | 36            |
| MSFT      | 271.7          | 247.6          | 37            |
| NVDA      | 256.2          | 231.4          | 15            |
| TSLA      | 224.4          | 199.9          | 11            |

Aggregate: max peak RSS 311.2 MiB (AAPL); median peak RSS 262.5 MiB;
min peak RSS 224.4 MiB (TSLA).

### 9.4 Per-secondary CPU

| Secondary | CPU user (s) | CPU sys (s) | CPU total (s) | CPU/wall ratio |
|-----------|--------------|-------------|---------------|----------------|
| SPY       | 14.69        | 1.66        | 16.34         | 0.95           |
| AAPL      | 16.97        | 1.66        | 18.63         | 0.94           |
| AMZN      |  6.44        | 1.38        |  7.81         | 0.91           |
| GOOGL     |  5.27        | 1.50        |  6.77         | 0.89           |
| META      | 15.53        | 1.73        | 17.27         | 0.95           |
| MSFT      | 16.19        | 1.55        | 17.74         | 0.95           |
| NVDA      |  5.36        | 1.44        |  6.80         | 0.90           |
| TSLA      |  3.39        | 1.39        |  4.78         | 0.86           |

CPU/wall ratio across the eight invocations is 0.86 - 0.95, clustered
near 1.0. This is consistent with `PARALLEL_SUBSETS=0` default
(effectively single-threaded compute with minor library-level
parallelism in BLAS / pandas).

---

## 10. Comparison to Prior Measurements

K=1,2,3,4,6 subset (the K levels PR #309 / PR #310 measured):

| Secondary | PR #309/310 wall (s) | This task K=1..6 sum (s) | Full K=1..12 wall (s) |
|-----------|----------------------|---------------------------|------------------------|
| SPY       | 14.15 (PR #309)      | 14.13                     | 17.27                  |
| AAPL      | 16.15 (PR #309)      | 16.84                     | 19.74                  |
| AMZN      |  7.39 (PR #310)      |  5.85                     |  8.61                  |
| GOOGL     |  6.98 (PR #310)      |  4.31 (K=5 STALE-GATED)   |  7.60                  |
| META      |  8.97 (PR #310)      | 10.78 (incl K=7 7.85 s)   | 18.22                  |
| MSFT      | 14.99 (PR #310)      | 15.42                     | 18.73                  |
| NVDA      |  7.39 (PR #310)      |  4.57 (K=5 PKL-GATED)     |  7.59                  |
| TSLA      |  5.46 (PR #310)      |  2.71 (K=5 PKL-GATED)     |  5.57                  |

This-task K=1..6 sums are the sum of per-cell elapsed for K=1..6 from
this run's `run_manifest.json`. The "full K=1..12 wall" column is the
wrapper's wall-clock for the whole invocation (which includes Python /
runner startup, lazy import of `trafficflow`, eligibility analysis,
isolated output write, and the 6 STALE-/PKL-GATED no-op slots).

K=1..6 per-cell timings are within noise of the PR #309 / PR #310
baselines (no deviation greater than 30 percent for any cell that was
ELIGIBLE in both runs). The K=1 acceleration relative to PR #307
(before PR #308's surface block) that PR #309 documented is preserved
here. No performance regression observed for the previously-validated
K subset.

K=5 is new measurement data (PR #309 / PR #310 did not exercise K=5).
Observed range across the 5 ELIGIBLE K=5 cells: 1.59 s (META) to
3.78 s (MSFT), median 3.15 s. The 3 K=5 cells that were STALE-/PKL-
GATED (GOOGL, NVDA, TSLA) could not be measured here.

---

## 11. Critical Finding - K=7..12 Surface is STALE-GATED

### 11.1 What happened

Across all 8 secondaries, **K=7..12 cells were classified by the
runner's benchmark eligibility check as STALE-GATED (or PKL-GATED in a
few cases) and were therefore not executed**. Additionally a small
set of K=5 / K=8 cells on GOOGL / META / NVDA / TSLA were
STALE-/PKL-GATED. In total, 50 of 96 requested cells were skipped.
The remaining 46 cells executed cleanly, produced 1 row each, and were
written to isolated output without error.

### 11.2 Why this is runner-as-designed

PR #305 established the freshness gate that the runner now relies on:
each candidate K cell's member-PKL set is checked for `data_tail_date
>= benchmark_as_of_date`. A PKL whose tail-date is older than the
benchmark as-of-date classifies the cell as STALE-GATED. A missing
PKL classifies the cell as PKL-GATED. The runner refuses to run gated
cells until they are refreshed (which is precisely the
`signal_engine_cache_refresher.py` workflow PR #305 documented).

PR #305 explicitly refreshed the **K=1..6 member union** (47 PKLs).
That work covered exactly the K subset PR #309 / PR #310 then
validated. The K=7..12 member union was not refreshed because the
operational K surface at the time was K=1,2,3,4,6 only.

### 11.3 Why this surfaces here

This Phase D task is the first task to extend `--k-range` past K=6.
The runner's gate fires correctly. Phase D therefore characterizes
the runtime / memory / CPU surface for the 46 cells the runner could
run; the remaining 50 cells require a prerequisite PKL-refresh
step that is **out of scope** for this task (the task spec
explicitly forbids `--refresh-missing-pkls`, `--refresh-stale-prices`,
and invoking `signal_engine_cache_refresher.py`).

### 11.4 What this implies for Phase E

Phase E (canonical writes to `output/trafficflow/`) cannot
responsibly proceed against the full K=1..12 operational surface
until a dedicated PKL-refresh pre-task is completed and re-validated
against the current benchmark as-of-date. The K=1..6 surface is
already operationally ready (PR #305 fresh, PR #309 / PR #310
canonical-safety verified, this task performance-characterized).

---

## 12. High-K Deep Dive (informational, limited data)

Only one K>=7 cell ran in this measurement: META K=7 at 7.85 s. This
single point is consistent with the K=6 hot path documented in PR
#301 (`_subset_metrics_spymaster_bitmask`) at slightly elevated cost:
META K=6 measured here at 3.64 s, K=7 at 7.85 s (roughly 2x).

Top 10 slowest cells overall (across all ELIGIBLE 46 cells):

| Rank | elapsed (s) | Secondary | K  |
|------|-------------|-----------|----|
| 1    | 8.62        | AAPL      | 6  |
| 2    | 7.85        | META      | 7  |
| 3    | 7.81        | MSFT      | 6  |
| 4    | 7.02        | SPY       | 6  |
| 5    | 3.78        | MSFT      | 5  |
| 6    | 3.75        | AAPL      | 5  |
| 7    | 3.64        | META      | 6  |
| 8    | 3.15        | SPY       | 5  |
| 9    | 2.52        | NVDA      | 6  |
| 10   | 2.41        | AAPL      | 4  |

---

## 13. Memory Ceiling Observations

- Max peak RSS observed: 311.2 MiB (AAPL).
- Median peak RSS observed: 262.5 MiB.
- Approximate operator-described memory context: 200 GiB.
- Max peak RSS as a percentage of the 200 GiB context: **0.152%**.

Descriptive estimate (informational, not a recommendation): peak RSS
across all 8 sequential invocations is below 0.16% of the 200 GiB
context, with the largest invocation comfortably under 1 GiB. A
naive linear estimate for 2 concurrent invocations would be at most
about 0.31% of the 200 GiB context; for 4 concurrent invocations,
about 0.61%. This is well within physical headroom; however, this
task does NOT recommend enabling parallel execution. Phase E and any
parallelism decision should be made on the basis of correctness,
operational complexity, and engine-internal serialization behaviour,
not on RAM availability alone.

---

## 14. Post-Run Canonical Safety Check

Captured to `<SESSION_DIR>/preflight/post_run_snapshot.json`.

File-count comparison pre vs post (file_count and latest_mtime both
unchanged for every root):

| Root                              | Pre count | Post count | Unchanged |
|-----------------------------------|-----------|------------|-----------|
| `output/stackbuilder/`            | 5388      | 5388       | yes       |
| `output/impactsearch/`            | 16        | 16         | yes       |
| `output/onepass/`                 | 2         | 2          | yes       |
| `output/trafficflow/`             | absent    | absent     | yes       |
| `output/validation/`              | 0         | 0          | yes       |
| `signal_library/data/stable/`     | 71980     | 71980      | yes       |
| `cache/results/`                  | 3267      | 3267       | yes       |
| `cache/status/`                   | 1648      | 1648       | yes       |
| `price_cache/daily/`              | 12        | 12         | yes       |

Per-file SHA-256 comparison (all unchanged):

- 8 `selected_build.json` files: unchanged.
- 8 `combo_leaderboard.xlsx` files: unchanged.
- 96 `combo_k=N.json` files (8 secondaries x K=1..12): unchanged.
- `output/onepass/onepass.xlsx`: unchanged.
- 117 member PKLs (union of K=1..12 members across all 8 secondaries):
  unchanged.

Central price-cache verification targets:

| File                           | SHA-256 unchanged | size unchanged | mtime unchanged |
|--------------------------------|--------------------|------------------|------------------|
| `price_cache/daily/SPY.csv`    | yes                | yes (232006)     | yes              |
| `price_cache/daily/AAPL.csv`   | yes                | yes (348338)     | yes              |
| `price_cache/daily/AMZN.csv`   | yes                | yes (220020)     | yes              |
| `price_cache/daily/GOOGL.csv`  | yes                | yes (165856)     | yes              |
| `price_cache/daily/META.csv`   | yes                | yes (104771)     | yes              |
| `price_cache/daily/MSFT.csv`   | yes                | yes (287879)     | yes              |
| `price_cache/daily/NVDA.csv`   | yes                | yes (212621)     | yes              |
| `price_cache/daily/TSLA.csv`   | yes                | yes (121445)     | yes              |

PR #308's surface block holds for the K=1..12 surface (no
price-cache modification regardless of whether each cell was ELIGIBLE
or GATED).

---

## 15. Privacy Sanitization Verification

Scope of scan: per-secondary captured stdout, per-secondary on-disk
`run_manifest.json`, per-secondary on-disk `run.stdout.json` (24
files total), this evidence doc, the intended commit message, the
intended PR body, and the final report.

Categories scanned: username / conda-path / drive-path denylist (per
CLAUDE.md privacy rule) and a case-sensitive drive-letter regular
expression.

Per-file results: zero token hits and zero drive-letter pattern
matches across every scanned artifact.

The ticker symbol NVDA appears in this doc and in the runner output;
per the task instructions, NVDA is the permitted ticker symbol and is
not the denylist token.

---

## 16. Findings

16.1 No cells errored. 46 / 46 ELIGIBLE cells completed with
`status=ok` at the cell level and 1 row each. 50 / 96 requested
cells were STALE-GATED or PKL-GATED and did not execute (see Section
11). The runner's exit code is 1 and stdout `status=partial` for
every invocation, which is the documented signal when at least one
requested cell was not eligible. This is not a defect.

16.2 No privacy leaks across the 24 runner JSON artifacts or the text
artifacts (evidence doc, commit message, PR body, final report).

16.3 No canonical safety violations. All 8 `price_cache/daily/<SEC>.csv`
files are byte-identical pre/post (SHA-256, size, and mtime
unchanged); all `selected_build.json`, `combo_leaderboard.xlsx`, and
`combo_k=1..12.json` artifacts unchanged; `onepass.xlsx` unchanged;
all 117 member PKLs unchanged.

16.4 No provenance mismatches. All 8 manifest entries report
`selected_build_sha256` byte-for-byte matching the pre-snapshot, with
`explicit_build_override=false`.

16.5 No performance regression versus PR #309 / PR #310 for the
K=1,2,3,4,6 subset where comparable cells exist. K=5 is new
measurement data and characterizes that K level for the first time.
K=7..12 is uncharacterized (see Section 11).

16.6 Memory and CPU footprint of the runner is small: max peak RSS
311.2 MiB, CPU/wall ratio 0.86 - 0.95, single-threaded default
honored.

16.7 No measurement noise observed beyond standard desktop process
mix.

---

## 17. Recommendation

**PASS WITH NOTES.**

The Phase D task's stated mandate (capture runtime, memory, CPU,
artifact counts, and canonical-safety evidence for `--write` across
all 8 Phase 6I-79 secondaries at K=1..12) is partially satisfied:

- All canonical-safety expectations are met. Phase D introduced zero
  canonical modifications, including for the K=7..12 surface that
  was newly exercised.
- Performance, memory, and CPU are fully characterized for the 46
  ELIGIBLE cells the runner executed.
- The remaining 50 cells (K=7..12 universally; a small set of
  K=5 / K=8 PKL-gated cells) cannot be measured under this task's
  no-refresh rule. The runner's STALE-/PKL-GATED behavior is correct,
  not a defect.

Proposed next step (NOT taken in this task):

- A separate, operator-supervised PKL-refresh pre-task that extends
  PR #305's refresh sweep to the K=7..12 member union for all 8
  secondaries, using `signal_engine_cache_refresher.py --max-sma-day
  114 ...` one ticker at a time, against the current benchmark
  as-of-date. After that refresh completes and re-validates, a
  follow-up Phase D measurement task can re-run the same K=1..12
  invocation shape and characterize the full operational K surface.

Phase E (canonical writes to `output/trafficflow/`) should remain
deferred until the K=7..12 surface is measured.

---

This was performance and memory measurement under isolated output
mode. It targeted K=1..12 (not just the prior K=1,2,3,4,6 validation
subset). No canonical artifacts were modified. No PARALLEL_SUBSETS
override was tested. Measurement methodology and tooling are
documented in Section 6. All session evidence under `<SESSION_DIR>`
is gitignored. Phase E should NOT begin until the K=7..12 member-PKL
refresh prerequisite task lands and a follow-up Phase D measurement
re-runs cleanly against it.
