# TrafficFlow Runner Phase D - Full-K Re-Measurement Evidence

Session date (UTC): 2026-05-24
Session directory (gitignored): `<SESSION_DIR>` =
`logs/trafficflow_phase_d_full_k_remeasurement/20260524T204014Z/`
Branch: `trafficflow-runner-phase-d-full-k-remeasurement`

This document re-runs the Phase D full-K performance measurement that
PR #311 attempted but could only partially execute (46/96 cells) due
to STALE-GATED / PKL-GATED member PKLs in the K=7..12 surface. PR
#312 repaired that surface (56 unique PKLs refreshed, post-refresh
dry-run reported 96/96 ELIGIBLE). This task re-runs the exact PR #311
invocation shape against the now-ready surface and characterizes
end-to-end performance for the full K=1..12 surface across all 8
Phase 6I-79 secondaries.

**Headline result.**

- **Correctness and canonical safety: PASS.** All 96 cells executed,
  all canonical artifacts byte-identical pre/post, zero
  modifications outside the gitignored session evidence directory.
- **Performance characterization: PASS WITH NOTES.** Total
  wall-clock 6,400.48 s (about 1 h 46.7 min). Max peak RSS 478.5 MiB
  (MSFT) at about 0.234 percent of the 200 GiB operator-described
  context. Per-cell elapsed scales sharply with K: K=1..6 medians
  sit in the 0.3 - 3.0 s band (consistent with PR #311 baseline);
  K=7..12 medians escalate from 6.87 s (K=7) to 297.55 s (K=12);
  slowest single cell is MSFT K=12 at 863.30 s (about 14.4 min).
- **Phase E canonical-write design should remain deferred** until a
  dedicated headless TrafficFlow speed-parity / optimization audit
  resolves how the sequential, secondary-by-secondary runner
  invocation shape this PR measured compares to the legacy
  TrafficFlow secondary-parallel orchestration the engine was
  optimized against. This PR characterizes the runner's full-K
  sequential cost; it does not yet prove speed parity with legacy
  TrafficFlow. See Section 9.5 below and Section 16 for the
  proposed next-step audit.

---

## 1. Scope and Non-Goals

In scope:

- Re-run `trafficflow_runner.py --write` sequentially against the 8
  Phase 6I-79 secondaries (SPY, AAPL, AMZN, GOOGL, META, MSFT, NVDA,
  TSLA) with `--k-range 1,2,3,4,5,6,7,8,9,10,11,12`.
- Isolated per-secondary output under
  `<SESSION_DIR>/isolated_output/<SECONDARY>/`. No canonical
  `output/trafficflow/` writes.
- Per-secondary wall-clock, per-cell elapsed (from
  `run_manifest.json`), peak RSS / VMS, accumulated CPU user / system
  time via a session-local `psutil` process-tree polling wrapper at
  0.5 s interval.
- Pre/post canonical safety snapshots covering 9 roots,
  per-secondary `selected_build.json`, `combo_leaderboard.xlsx`, all
  96 `combo_k=N.json` files, `onepass.xlsx`, all 8
  `price_cache/daily/<SEC>.csv` files, and all 117 member PKLs.

Out of scope (NOT performed):

- Phase E canonical writes.
- `PARALLEL_SUBSETS=1` comparison or any parallelism-override test.
- Parallel secondary execution.
- Runner instrumentation changes.
- Engine / profile-guided optimization recommendations.
- Code or test modification.
- PKL refresh.
- Price-cache refresh.

---

## 2. References

- PR #311 - initial Phase D full-K partial measurement. 46/96 cells
  executed, 50/96 cells STALE-/PKL-GATED, canonical safety held.
- PR #312 - bounded PKL readiness repair for the K=1..12 member
  union (56 unique tickers refreshed; post-repair dry-run reported
  96/96 ELIGIBLE).
- PR #310 - broader Phase C smoke under PR #308 (K=1..6).
- PR #309 - SPY/AAPL Phase C re-validation under PR #308.
- PR #308 - engine network/price-cache surface block.
- PR #301 - bare-compute K-benchmark (K=1,2,3,4,6) that established
  the `PARALLEL_SUBSETS=0` default.

---

## 3. Test Suite Re-Run Confirmation

Command shape:

    <PINNED_INTERPRETER> -m pytest test_scripts/test_trafficflow_runner.py -q

Result: `68 passed in 2.58s` (matches expected post-PR-#308 suite
size).

---

## 4. Pre-Run Readiness Sanity Check

Optional pre-run dry-run of `trafficflow_runner.py` (no `--write`,
no `--refresh-*`, no `--allow-network-fetch`) for all 8 secondaries
with `K=1..12`:

| Secondary | exit | verdict   | ELIGIBLE Ks |
|-----------|------|-----------|-------------|
| SPY       | 0    | ELIGIBLE  | 12 / 12     |
| AAPL      | 0    | ELIGIBLE  | 12 / 12     |
| AMZN      | 0    | ELIGIBLE  | 12 / 12     |
| GOOGL     | 0    | ELIGIBLE  | 12 / 12     |
| META      | 0    | ELIGIBLE  | 12 / 12     |
| MSFT      | 0    | ELIGIBLE  | 12 / 12     |
| NVDA      | 0    | ELIGIBLE  | 12 / 12     |
| TSLA      | 0    | ELIGIBLE  | 12 / 12     |
| Total     | -    | -         | **96 / 96** |

PR #312's post-repair readiness held end-to-end. Measurement
proceeded.

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
| `cache/results/`                  | 3305       |
| `cache/status/`                   | 1667       |
| `price_cache/daily/`              | 12         |

SHAs captured: 8 `selected_build.json`, 8 `combo_leaderboard.xlsx`,
all 96 `combo_k=1..12.json`, `onepass.xlsx`, 117 member PKLs (union
of K=1..12 members across all 8 secondaries), and all 8 price-cache
CSVs (full size + mtime + SHA-256).

All 117 required member PKLs present pre-run (PR #312 brought the
19 previously-MISSING PKLs into existence and refreshed the 37
previously-STALE ones).

---

## 6. Measurement Methodology

Same shape as PR #311.

Tool: session-local Python wrapper using `psutil 6.0.0`.

- Launches `trafficflow_runner.py` via `subprocess.Popen` with
  stdout/stderr redirected to files under `<SESSION_DIR>/runs/`.
- Polls the runner process and all descendants every 0.5 seconds.
- At each sample, sums `memory_info().rss` and `memory_info().vms`
  across the process tree; tracks the maximum sum observed.
- At each sample, refreshes per-PID `cpu_times().user` and
  `.system`; on exit sums these across PIDs.
- Writes one measurement JSON per secondary to
  `<SESSION_DIR>/measurements/<SECONDARY>_measurement.json`.

Memory fields captured: `peak_rss_bytes`, `peak_vms_bytes`. USS / PSS
not captured on Windows by psutil 6.0.0 by default.

CPU fields captured: `cpu_user_seconds`, `cpu_system_seconds`,
`cpu_total_seconds`, `cpu_wall_ratio`.

Polling interval: 0.5 s. Inter-invocation pause: 5 s.

Measurement noise sources observed: none beyond the standard desktop
process mix. No other engine / runner / Dash / refresher processes
were active at the start of the run.

Wrapper script (gitignored): `<SESSION_DIR>/orchestrator.py`. Not
committed.

---

## 7. Invocation Methodology

Exact command shape (placeholders) per secondary:

    <PINNED_INTERPRETER> trafficflow_runner.py \
        --secondaries <SECONDARY> \
        --k-range 1,2,3,4,5,6,7,8,9,10,11,12 \
        --stackbuilder-root output/stackbuilder \
        --output-dir <SESSION_DIR>/isolated_output/<SECONDARY> \
        --write

Flags explicitly NOT passed: `--refresh-missing-pkls`,
`--refresh-stale-prices`, `--allow-network-fetch`,
`--explicit-build`.

Environment variables explicitly NOT set: `PARALLEL_SUBSETS`,
`TRAFFICFLOW_PARALLEL_SUBSETS`.

Order (sequential): SPY, AAPL, AMZN, GOOGL, META, MSFT, NVDA, TSLA.

Effective config per invocation (all 8 identical):
`write_mode=isolated`, `write_authorized=true`,
`output_dir_isolated=true`, `canonical_write_blocked=false`,
`allow_network_fetch=false`, no `parallel_subsets` surfaced (runner
default 0 honored).

---

## 8. Per-Secondary Correctness Verification

For every secondary: exit=0, status=ok, `write_mode=isolated`,
`write_summary.artifacts_written_count=26` (12 JSON + 12 CSV + 2
run-level), 12/12 board-row JSON + 12/12 board-row CSV present,
`run_manifest.json` + `run.stdout.json` present, artifact list
complete (both files list themselves and all 24 board files),
selected-build provenance matches the pre-snapshot SHA byte-for-byte,
`explicit_build_override=false`, zero privacy hits across all 24
runner JSON artifacts (8 captured stdout, 8 on-disk manifests, 8
on-disk stdout sidecars), zero `.tmp` residue, per-cell JSON row
count = 1 and CSV row count = 1 for every cell.

---

## 9. Performance Summary

### 9.1 Per-secondary wall-clock and totals

| Secondary | Wall (s)  | Wall (min) | Exit | Cells written | Status |
|-----------|-----------|------------|------|---------------|--------|
| SPY       |  1469.97  | 24.50      | 0    | 12 / 12       | ok     |
| AAPL      |  1455.19  | 24.25      | 0    | 12 / 12       | ok     |
| AMZN      |   255.06  |  4.25      | 0    | 12 / 12       | ok     |
| GOOGL     |   407.37  |  6.79      | 0    | 12 / 12       | ok     |
| META      |   699.69  | 11.66      | 0    | 12 / 12       | ok     |
| MSFT      |  1624.97  | 27.08      | 0    | 12 / 12       | ok     |
| NVDA      |   224.13  |  3.74      | 0    | 12 / 12       | ok     |
| TSLA      |   264.10  |  4.40      | 0    | 12 / 12       | ok     |
| Total     |  6400.48  | 106.67     | -    | **96 / 96**   | -      |

### 9.2 Per-K elapsed distribution

n = 8 ELIGIBLE cells per K (one per secondary).

|  K | min (s) | median (s) |   max (s) | mean (s) |
|----|---------|------------|-----------|----------|
|  1 |  0.19   |   0.32     |   0.48    |   0.34   |
|  2 |  0.29   |   0.51     |   0.77    |   0.52   |
|  3 |  0.41   |   0.58     |   1.21    |   0.72   |
|  4 |  0.55   |   1.05     |   2.44    |   1.27   |
|  5 |  1.00   |   1.57     |   3.75    |   2.13   |
|  6 |  1.08   |   3.02     |   8.59    |   4.28   |
|  7 |  1.91   |   6.87     |  18.69    |   9.36   |
|  8 |  5.10   |  12.82     |  37.99    |  19.43   |
|  9 | 10.91   |  28.33     |  86.25    |  42.69   |
| 10 | 24.04   |  62.44     | 188.69    |  93.61   |
| 11 | 52.68   | 137.19     | 409.13    | 203.17   |
| 12 | 93.54   | 297.55     | 863.30    | 418.31   |

Median elapsed roughly doubles per K from K=7 to K=12.

### 9.3 Per-secondary peak memory

| Secondary | Peak RSS (MiB) | Peak VMS (MiB) | Poll samples |
|-----------|----------------|----------------|---------------|
| SPY       | 435.7          | 413.1          | 2905          |
| AAPL      | 453.6          | 429.1          | 2875          |
| AMZN      | 365.4          | 342.5          |  504          |
| GOOGL     | 320.5          | 296.3          |  805          |
| META      | 306.3          | 282.1          | 1383          |
| MSFT      | 478.5          | 453.9          | 3212          |
| NVDA      | 364.2          | 341.0          |  443          |
| TSLA      | 310.2          | 286.0          |  522          |

Aggregate: max peak RSS 478.5 MiB (MSFT); median peak RSS 364.8 MiB;
min peak RSS 306.3 MiB (META).

### 9.4 Per-secondary CPU

| Secondary | CPU user (s) | CPU sys (s) | CPU total (s) | CPU/wall ratio |
|-----------|--------------|-------------|---------------|----------------|
| SPY       | 1395.86      |  35.88      | 1431.73       | 0.974          |
| AAPL      | 1375.95      |  36.03      | 1411.98       | 0.970          |
| AMZN      |  237.17      |   9.48      |  246.66       | 0.967          |
| GOOGL     |  386.56      |  11.39      |  397.95       | 0.977          |
| META      |  659.94      |  22.89      |  682.83       | 0.976          |
| MSFT      | 1535.41      |  43.91      | 1579.31       | 0.972          |
| NVDA      |  208.81      |   6.58      |  215.39       | 0.961          |
| TSLA      |  245.91      |   9.98      |  255.89       | 0.969          |

CPU/wall ratio 0.961 - 0.977 across all 8 invocations. Consistent
with `PARALLEL_SUBSETS=0` default - effectively single-threaded with
minor BLAS / pandas library parallelism.

### 9.5 Legacy TrafficFlow Speed-Parity Caveat

The 1 h 46.7 min full-K wall-clock measured above is the cost of the
headless runner's specific orchestration shape, not a like-for-like
benchmark against the legacy Dash TrafficFlow surface. Four points
are load-bearing for any subsequent Phase E design decision:

- **The headless runner uses the real TrafficFlow compute path.**
  `trafficflow_runner.py` invokes
  `trafficflow.build_board_rows(...)` (see line 1740 in
  `trafficflow_runner.py` and the
  `_default_compute_loader` wrapper that pins the engine's
  cache-write / network-fetch surface). There is no compute-shim
  layer; the same engine function legacy callers used is the
  function this measurement exercised.
- **The bitmask fast path is active by default.** `trafficflow.py`
  sets `TF_BITMASK_FASTPATH = os.environ.get("TF_BITMASK_FASTPATH",
  "1").lower() in {"1","true","on","yes"}` at module load (see
  `trafficflow.py` line 258). The default-enabled bitmask path was
  documented as a 3x speedup (30 s -> 10 s) in
  `md_library/trafficflow/2025-10-08_BITMASK_ENABLED_AS_PRODUCTION_DEFAULT.md`,
  and `build_board_rows` selects
  `_subset_metrics_spymaster_bitmask` when the flag is on. The
  measurement above ran with that flag on by default; the observed
  K-tail cost is therefore not explained by missing vectorization.
- **K=10..12 consumed about 89 percent of total wall-clock.** That
  share is a combinatorial fact about K-tail subset enumeration,
  not a runner overhead. Legacy K=6-only measurements (the regime
  the bitmask fast path was originally tuned against) are not
  comparable to a K=1..12 sum without explicitly separating the
  K=1..6 head from the K=7..12 tail.
- **The runner invoked secondaries one at a time; legacy
  TrafficFlow ran them in parallel.** Legacy / Dash TrafficFlow
  used a secondary-level ThreadPoolExecutor (16 workers processing
  100 secondaries in parallel; see
  `md_library/trafficflow/2025-10-14_TRAFFICFLOW_PERFORMANCE_BOTTLENECK_ANALYSIS_AND_OPTIMIZATION_PROPOSALS.md`
  for the outer-parallelization architecture, and
  `md_library/trafficflow/2025-10-14_TRAFFICFLOW_SUBSET_PARALLELIZATION_TEST_RESULTS_AND_ANALYSIS.md`
  for the subsequent finding that subset-level parallelism was
  marginal or slightly slower and not adopted as default). PR #311
  and this re-measurement intentionally measured sequential
  per-secondary runner invocations; the K-tail cost in any
  legacy-equivalent secondary-parallel orchestration will look
  meaningfully different on wall-clock even if per-cell engine
  cost is identical.

Therefore: PR #313 characterizes full-K sequential runner cost, but
does NOT yet prove speed parity with legacy TrafficFlow. A
dedicated headless TrafficFlow speed-parity / optimization audit
should answer that question before any Phase E canonical-write
design proceeds. See Section 16 for the proposed audit scope.

---

## 10. Comparison to PR #311

| Secondary | PR #311 wall (s) | This task wall (s) | PR #311 cells | This cells | Delta cells |
|-----------|-------------------|---------------------|----------------|------------|-------------|
| SPY       |  17.27            |  1469.97            | 6              | 12         | +6          |
| AAPL      |  19.74            |  1455.19            | 6              | 12         | +6          |
| AMZN      |   8.61            |   255.06            | 6              | 12         | +6          |
| GOOGL     |   7.60            |   407.37            | 5              | 12         | +7          |
| META      |  18.22            |   699.69            | 7              | 12         | +5          |
| MSFT      |  18.73            |  1624.97            | 6              | 12         | +6          |
| NVDA      |   7.59            |   224.13            | 5              | 12         | +7          |
| TSLA      |   5.57            |   264.10            | 5              | 12         | +7          |

PR #311 did not execute K=7..12 (all 50 those cells were
STALE-/PKL-GATED), so the wall-clock deltas overwhelmingly reflect
the cost of the newly-measured high-K cells. For the K=1..6 cells
that ran in both tasks, per-cell timings sit within noise of the PR
#311 baseline (e.g. SPY K=1 0.31 vs PR #311 0.29; SPY K=6 6.97 vs PR
#311 7.02; AAPL K=6 8.59 vs PR #311 8.62). No K=1..6 regression
observed.

K=7..12 is characterized end-to-end for the first time.

---

## 11. High-K Deep Dive

K=10, K=11, K=12 per-secondary elapsed (seconds):

| Secondary | K=10   | K=11   | K=12   |
|-----------|--------|--------|--------|
| SPY       | 174.83 | 383.50 | 768.79 |
| AAPL      | 167.85 | 352.86 | 773.67 |
| AMZN      |  28.01 |  60.52 | 132.45 |
| GOOGL     |  45.43 | 100.12 | 218.84 |
| META      |  79.44 | 174.25 | 376.25 |
| MSFT      | 188.69 | 409.13 | 863.30 |
| NVDA      |  24.04 |  52.68 | 119.65 |
| TSLA      |  40.60 |  92.29 |  93.54 |

Aggregate per K: K=10 sum 748.93 s, K=11 sum 1625.36 s,
K=12 sum 3346.50 s. K=10..12 together account for 5,720.79 s out
of the 6,400.48 s aggregate wall-clock (about 89 percent of total).

Top 10 slowest cells across the full 96-cell surface:

| Rank | elapsed (s) | Secondary | K  |
|------|-------------|-----------|----|
| 1    |  863.30     | MSFT      | 12 |
| 2    |  773.67     | AAPL      | 12 |
| 3    |  768.79     | SPY       | 12 |
| 4    |  409.13     | MSFT      | 11 |
| 5    |  383.50     | SPY       | 11 |
| 6    |  376.25     | META      | 12 |
| 7    |  352.86     | AAPL      | 11 |
| 8    |  218.84     | GOOGL     | 12 |
| 9    |  188.69     | MSFT      | 10 |
| 10   |  174.83     | SPY       | 10 |

The hot path documented in PR #300 / PR #301 evidence
(`_subset_metrics_spymaster_bitmask` and the bitmask-enumeration
inner loop) dominates the K-tail. No optimization recommendation is
made in this task.

---

## 12. Memory Ceiling Observations

- Max peak RSS observed: 478.5 MiB (MSFT, slowest secondary).
- Median peak RSS observed: 364.8 MiB.
- Min peak RSS observed: 306.3 MiB (META).
- Approximate operator-described memory context: 200 GiB.
- Max peak RSS as a percentage of the 200 GiB context: **0.234 percent**.

Descriptive estimate (informational, not a recommendation): peak RSS
across all 8 sequential invocations is below 0.24 percent of the 200
GiB context, with the largest invocation comfortably under 0.5 GiB.
A naive linear estimate for 2 concurrent invocations is roughly 0.47
percent of context; for 4 concurrent, roughly 0.94 percent; for 8
concurrent, roughly 1.87 percent. RAM is clearly not a binding
constraint at this scale; however, parallelism behavior depends on
engine-internal serialization (BLAS thread pools, Python GIL, file
system contention on the price_cache and member-PKL I/O paths) and
not just on RAM headroom. This task makes no parallelism
recommendation.

---

## 13. Post-Run Canonical Safety Check

Captured to `<SESSION_DIR>/preflight/post_run_snapshot.json`.

| Root                              | Pre count | Post count | Unchanged |
|-----------------------------------|-----------|------------|-----------|
| `output/stackbuilder/`            | 5388      | 5388       | yes       |
| `output/impactsearch/`            | 16        | 16         | yes       |
| `output/onepass/`                 | 2         | 2          | yes       |
| `output/trafficflow/`             | absent    | absent     | yes       |
| `output/validation/`              | 0         | 0          | yes       |
| `signal_library/data/stable/`     | 71980     | 71980      | yes       |
| `cache/results/`                  | **3305**  | **3305**   | **yes**   |
| `cache/status/`                   | **1667**  | **1667**   | **yes**   |
| `price_cache/daily/`              | 12        | 12         | yes       |

Per-file SHA-256 comparison:

- All 8 `selected_build.json` files: unchanged.
- All 8 `combo_leaderboard.xlsx` files: unchanged.
- All 96 `combo_k=N.json` files: unchanged.
- `output/onepass/onepass.xlsx`: unchanged.
- All 117 member PKLs (the K=1..12 union across all 8 secondaries):
  unchanged.

Central price-cache verification targets:

| File                           | SHA unchanged | size unchanged | mtime unchanged |
|--------------------------------|---------------|------------------|------------------|
| `price_cache/daily/SPY.csv`    | yes           | yes              | yes              |
| `price_cache/daily/AAPL.csv`   | yes           | yes              | yes              |
| `price_cache/daily/AMZN.csv`   | yes           | yes              | yes              |
| `price_cache/daily/GOOGL.csv`  | yes           | yes              | yes              |
| `price_cache/daily/META.csv`   | yes           | yes              | yes              |
| `price_cache/daily/MSFT.csv`   | yes           | yes              | yes              |
| `price_cache/daily/NVDA.csv`   | yes           | yes              | yes              |
| `price_cache/daily/TSLA.csv`   | yes           | yes              | yes              |

`cache/results/` (file count 3305 - latest mtime preserved) and
`cache/status/` (1667 - latest mtime preserved) are byte-identical
pre/post. This task performed no refresh, and the runner's
network/cache-write surface block (PR #308) held across the full
K=1..12 surface.

---

## 14. Privacy Sanitization Verification

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
per the task instructions, NVDA is the permitted ticker symbol and
is not the denylist token.

---

## 15. Findings

15.1 No cells errored. 96 / 96 ELIGIBLE cells completed with
`status=ok` at the cell level and 1 row each. All 8 invocations
returned `exit_code=0` and `status=ok`. The 50 cells that were
STALE-/PKL-GATED in PR #311 are now fully cleared.

15.2 No privacy leaks across the 24 runner JSON artifacts or the text
artifacts (evidence doc, commit message, PR body, final report).

15.3 No canonical safety violations. All 9 tracked roots are
file-count- and latest-mtime-unchanged pre/post. Every SHA-256
sampled (8 selected_build.json, 8 combo_leaderboard.xlsx, 96
combo_k=N.json, onepass.xlsx, 117 member PKLs, 8 price-cache CSVs)
is unchanged. `cache/results/` and `cache/status/` are byte-identical
pre/post.

15.4 No provenance mismatches. All 8 manifest entries report
`selected_build_sha256` matching the pre-snapshot byte-for-byte,
with `explicit_build_override=false`.

15.5 K=1..6 timing consistency vs PR #311: within noise (no cell
deviated more than approximately 5 percent for SPY, AAPL, MSFT, META
where both runs had data; small-secondary K=5 cells that were
GATED in PR #311 are new data and not a regression).

15.6 K=7..12 measurement: characterized end-to-end for the first
time. K-tail cost is the dominant Phase D finding (about 89 percent
of total wall-clock from K=10..12 alone).

15.7 Memory footprint of the runner is small: max peak RSS 478.5 MiB
(0.234 percent of the 200 GiB context). CPU/wall ratio 0.96 - 0.98,
single-threaded default honored.

15.8 No measurement noise observed beyond standard desktop process
mix.

15.9 Operator-process note: SPY, AAPL, MSFT, and META exceeded the
PR #311 expected wall-clock band (5 - 60 s) substantially. SPY at
24.5 min, AAPL at 24.3 min, MSFT at 27.1 min, and META at 11.7 min
are explained entirely by K=10/11/12 cell cost (see Section 11). All
four invocations exceeded the task spec's 15-minute LONG-RUNNING
threshold but completed successfully without intervention - the
LONG-RUNNING annotation is informational, not a failure mode.

15.10 Headless speed parity with legacy TrafficFlow remains
unproven (informational finding, not a correctness or canonical-
safety issue). The 1 h 46.7 min full-K sequential wall-clock
measured here is not directly comparable to legacy TrafficFlow
performance because (a) legacy TrafficFlow parallelized across
secondaries and PR #313 did not, and (b) K=10..12 dominates the
total and was not historically a legacy benchmark target (legacy
benchmarks were typically K=6-only). The bitmask fast path is
active by default in `trafficflow.py` and the runner exercises the
real `trafficflow.build_board_rows` path (see Section 9.5), so the
observed cost is orchestration and K-tail combinatorics, not
absence of vectorization. A required follow-up should compare
K=6-only and K=1..6 runner performance against legacy-equivalent
orchestration before any Phase E canonical-write design proceeds.

---

## 16. Recommendation

**PASS WITH NOTES.**

Correctness and canonical safety: PASS. The runner's runtime, peak
memory, and CPU profile are characterized across the 8-secondary
K=1..12 operational surface under isolated-output `--write` mode.
PR #312's PKL readiness repair fully cleared the K=7..12 gate, and
this task measured the full surface without any cell skipping.

Performance characterization: PASS WITH NOTES. The 1 h 46.7 min
full-K sequential wall-clock measured here is not directly
comparable to legacy TrafficFlow performance, because the headless
runner invocations were sequential per secondary while legacy
TrafficFlow used a secondary-level ThreadPoolExecutor, and because
K=10..12 (about 89 percent of total wall-clock) is not a legacy
benchmark regime. See Section 9.5 for the speed-parity caveat
detail.

Required next step before Phase E canonical-write design:

  **TrafficFlow runner headless speed-parity / optimization
  audit.** Suggested scope:

  - Review the legacy TrafficFlow optimization history
    (`md_library/trafficflow/2025-10-08_BITMASK_ENABLED_AS_PRODUCTION_DEFAULT.md`,
    `md_library/trafficflow/2025-10-14_TRAFFICFLOW_SUBSET_PARALLELIZATION_TEST_RESULTS_AND_ANALYSIS.md`,
    `md_library/trafficflow/2025-10-14_TRAFFICFLOW_PERFORMANCE_BOTTLENECK_ANALYSIS_AND_OPTIMIZATION_PROPOSALS.md`).
  - Compare the sequential per-secondary runner orchestration this
    PR exercised against a legacy-style secondary-parallel
    orchestration on equivalent hardware.
  - Benchmark K=6-only and K=1..6 separately from K=7..12 so the
    legacy-comparable head can be isolated from the new K-tail.
  - Confirm `TF_BITMASK_FASTPATH` is active during every measured
    run and document any environment override.
  - Keep canonical writes forbidden throughout the audit; reuse the
    isolated-output `<SESSION_DIR>` pattern.
  - Do NOT adopt `PARALLEL_SUBSETS=1`, secondary-level concurrency,
    or any other optimization as a default without separate
    evidence and operator authorization.

Phase E canonical-write design remains deferred until that audit
resolves the operational policy for K=10..12 and the question of
secondary-level parallelism.

---

This was the Phase D full-K re-measurement after PR #312's PKL
readiness repair. No canonical artifacts were modified. No PKL or
price-cache refresh occurred. No `PARALLEL_SUBSETS` override was
tested. Measurement methodology matches PR #311 (`psutil`
process-tree polling at 0.5 s interval). The runner exercised the
real `trafficflow.build_board_rows` compute path with the default
`TF_BITMASK_FASTPATH=1` setting; the observed K-tail cost reflects
orchestration and combinatorics, not absence of vectorization. All
session evidence under `<SESSION_DIR>` is gitignored. Phase E
canonical-write design remains deferred pending a dedicated
headless TrafficFlow speed-parity / optimization audit.
