# TrafficFlow Runner Headless Speed-Parity Audit

Session date (UTC): 2026-05-24
Session directory (gitignored): `<SESSION_DIR>` =
`logs/trafficflow_headless_speed_parity_audit/20260524T225513Z/`
Branch: `trafficflow-runner-headless-speed-parity-audit`

This audit answers whether the headless `trafficflow_runner.py`
path preserves the practical performance profile of the current
`trafficflow.py` compute path for the daily-cadence K surface
(K=6 alone and K=1..6) across all 8 Phase 6I-79 secondaries, and
whether Phase E canonical-write design can proceed.

**Headline result.** Static review confirms the headless runner
uses the same `trafficflow.build_board_rows` compute path with
`TF_BITMASK_FASTPATH=1` default-enabled and `PARALLEL_SUBSETS=0`
default-disabled. Three measurement campaigns show that the K=6
and K=1..6 surfaces are operationally fast (65 s and 81-98 s
respectively for all 8 secondaries combined), and that a
multi-secondary single invocation is supported, collision-safe, and
recovers about 17.7 percent wall-clock vs eight separate
subprocesses by saving interpreter / import overhead. Canonical
safety holds (no canonical artifacts modified). **Recommendation:
PASS WITH NOTES.** Phase E canonical-write design can responsibly
proceed for the daily K=1..6 surface; K=10..12 should be treated as
a separable heavy / opt-in stage (PR #313 measured it at about 89
percent of full-K wall-clock).

---

## 1. Scope and Non-Goals

In scope:

- Static review of `trafficflow.py` (compute path, fastpath
  flags) and `trafficflow_runner.py` (lazy compute wrapper,
  `--secondaries` CLI, output layout).
- Review of three legacy TrafficFlow optimization docs under
  `md_library/trafficflow/`.
- Three measurement campaigns:
  - **4a** K=6 only, one subprocess per secondary x 8 secondaries.
  - **4b** K=1..6 sequential, one subprocess per secondary x 8.
  - **4c** K=1..6 multi-secondary single invocation (one process,
    all 8 secondaries) - gated by static safety inspection.
- Per-invocation correctness verification.
- Pre/post canonical safety snapshots across 9 roots, 8
  `selected_build.json`, 8 `combo_leaderboard.xlsx`, 48
  `combo_k=1..6.json`, `onepass.xlsx`, 64 member PKLs, and all 8
  `price_cache/daily/<SEC>.csv` files.

Out of scope (NOT performed):

- Modifying `trafficflow.py` or `trafficflow_runner.py`.
- Phase E canonical writes.
- Full K=1..12 re-measurement.
- K=10..12 spot check.
- `PARALLEL_SUBSETS=1` comparison.
- PKL or price-cache refresh.
- Network fetch.

---

## 2. References

- PR #313 - Phase D full-K re-measurement evidence (1 h 46.7 min
  for 96/96 cells; K=10..12 ~89 percent of total).
- PR #312 - K=1..12 PKL readiness repair (56 PKLs).
- PR #310 - broader Phase C smoke (K=1,2,3,4,6 across the 6
  non-SPY/AAPL secondaries).
- PR #309 - SPY/AAPL Phase C network-block re-validation
  (K=1,2,3,4,6).
- PR #301 - bare-compute K benchmark.
- `md_library/trafficflow/2025-10-08_BITMASK_ENABLED_AS_PRODUCTION_DEFAULT.md`
- `md_library/trafficflow/2025-10-14_TRAFFICFLOW_SUBSET_PARALLELIZATION_TEST_RESULTS_AND_ANALYSIS.md`
- `md_library/trafficflow/2025-10-14_TRAFFICFLOW_PERFORMANCE_BOTTLENECK_ANALYSIS_AND_OPTIMIZATION_PROPOSALS.md`

---

## 3. Static Findings: Current Headless vs TrafficFlow

### 3.1 `trafficflow.py`

- **`TF_BITMASK_FASTPATH`** (line 258):
  `os.environ.get("TF_BITMASK_FASTPATH", "1").lower() in {"1","true","on","yes"}`.
  Default `"1"` -> **ENABLED**. Controlled by env var.
- **`TF_POST_INTERSECT_FASTPATH`** (line 252): default `"0"` ->
  disabled.
- **`PARALLEL_SUBSETS`** (line 241):
  `os.environ.get("PARALLEL_SUBSETS", "0") not in {"0","false","False"}`.
  Default `"0"` -> **DISABLED**. Controlled by env var.
- **`PARALLEL_SUBSETS_MIN_K`** (line 243): default `4`.
- **`_subset_metrics_spymaster_bitmask`** (line 2557): bitmask
  fast-path metric function. Selected as `_subset_fn` at line
  2790 when `TF_BITMASK_FASTPATH` is on (line 2789 guard).
- **`compute_build_metrics_spymaster_parity`** (line 2690):
  averaging entry point. Dispatches to the chosen `_subset_fn`.
- **`build_board_rows`** (line 2956): the function the runner
  invokes. Calls `compute_build_metrics_spymaster_parity` at
  line 3001.

### 3.2 `trafficflow_runner.py`

- **Lazy compute wrapper** (`_default_compute_loader`, line 1670+):
  imports `trafficflow as tf` locally, pins
  `tf._find_latest_combo_table` to the resolved
  `combo_leaderboard.xlsx` path, patches the PR #308 network /
  cache-write surface (`_patch_engine_network_surface`), then
  calls `tf.build_board_rows(...)` at line 1740. Restores both
  pins in a `finally` block.
- **No `TF_BITMASK_FASTPATH` override** anywhere in the runner.
- **No secondary-level parallelism** in the runner. Sequential
  `for sec in secondaries: preflight_secondary(sec, ...)` at
  line 2234.
- **`--secondaries`** (line 161-162):
  `p.add_argument("--secondaries", default=None, help="Comma/whitespace-separated secondary tickers.")`.
  Comma-separated multi-secondary IS supported. `resolve_secondaries`
  (line 319) joins the args, calls `_parse_ticker_blob`, and
  returns a list.
- **Output layout** (line 1905-1908):
  `sec_dir = output_dir / str(secondary); board_rows_k=<K>.{json,csv}`
  written under each `sec_dir`. Run-level files
  (`run_manifest.json`, `run.stdout.json`) at the top of
  `output_dir` (line 2331-2352, lines also visible at
  1958/1991). Multi-secondary single invocation is therefore
  **collision-safe by design**: per-secondary board-row files
  live under per-secondary subdirectories, with one shared
  manifest+stdout-sidecar at the invocation level.

### 3.3 Multi-secondary safety verdict

Both safety gates pass:

- multi-secondary input is supported by the runner CLI (Section
  3.2);
- artifact paths are collision-safe under a single `--output-dir`
  because the runner uses per-secondary subdirectories for
  board-row files and a single shared pair of run-level files.

Part 4c proceeded.

---

## 4. Legacy Optimization Wins/Losses Reviewed

| Optimization | Status today |
|---|---|
| Bitmask / vectorized fast path (`_subset_metrics_spymaster_bitmask`) | **Adopted, default-on** in `trafficflow.py` (line 258). Documented ~3x speedup in `2025-10-08_BITMASK_ENABLED_AS_PRODUCTION_DEFAULT.md`. |
| Post-intersect fast path (`TF_POST_INTERSECT_FASTPATH`) | Implemented but **disabled by default** (line 252). |
| Subset-level parallelism (`PARALLEL_SUBSETS`) | Implemented but **disabled by default** (line 241). Per `2025-10-14_TRAFFICFLOW_SUBSET_PARALLELIZATION_TEST_RESULTS_AND_ANALYSIS.md`: K=4 102 s -> 105 s (-3 percent, within noise); K=5 223 s -> 209 s (+6 percent, marginal); K=1..3 unchanged. Original (pre-bitmask) PARALLEL_SUBSETS rejection: "Without parallelization 18.32 s, With parallelization 19.35 s (SLOWER)". Reason for not adopting: not robustly faster on the K values that mattered; bitmask vectorization already captured most of the win. |
| Secondary-level parallelism | **Legacy / Dash only.** Documented in `2025-10-14_TRAFFICFLOW_PERFORMANCE_BOTTLENECK_ANALYSIS_AND_OPTIMIZATION_PROPOSALS.md` as "Outer Parallelization (Secondary-Level): 16 workers processing 100 secondaries in parallel". Not present in `trafficflow_runner.py`. Open question for headless. |
| PKL preloading | Investigated in `2025-10-14_TRAFFICFLOW_SUBSET_PARALLELIZATION_TEST_RESULTS_AND_ANALYSIS.md`. The PKL cache was cleared on every K change, "negating the benefit of preloading". Not adopted. |
| MKL / thread environment | Discussed in the bottleneck-analysis doc. The pinned audit interpreter uses an MKL-backed NumPy 1.26.4 stack (see project CLAUDE.md "Pinned Python interpreter" section). Threading is BLAS-default; no `MKL_NUM_THREADS` override applied in any of the campaigns below. |

Categorization summary for this audit:

- Already present in current `trafficflow.py`: bitmask fast path,
  subset-parallel scaffolding (off by default), post-intersect
  scaffolding (off by default).
- Investigated and rejected as default: subset-level
  parallelism, PKL preloading.
- Legacy / Dash orchestration only: secondary-level parallelism.
- Open question for headless: whether to revive secondary-level
  parallelism in the runner.
- Not applicable to this runner: anything Dash-specific.

### 4.1 Likely speed-gap candidates

Ranked by likely contribution to PR #313's 1 h 46.7 min full-K
wall-clock:

1. **K=10..12 combinatorics** (PR #313 measured this as ~89
   percent of full-K wall-clock).
2. **Sequential per-secondary subprocess orchestration** vs the
   legacy Dash secondary-parallel ThreadPoolExecutor.
3. **Interpreter / import overhead per subprocess** (each subprocess
   pays the cost of importing `trafficflow`, pandas, numpy, etc.
   This audit's Section 8 quantifies it at about 2 s per
   invocation for the K=1..6 surface).
4. Single-core per-secondary compute (CPU/wall ratio 0.85 - 0.96
   across all measured invocations confirms this).
5. Lack of secondary-level runner parallelism for the K-tail
   surface (would only matter if K=10..12 is on the daily path).

---

## 5. Measurement Methodology

Tool: session-local Python wrapper using `psutil 6.0.0`.

- Each runner invocation launched via `subprocess.Popen` with
  stdout/stderr redirected to `<SESSION_DIR>/runs/`.
- Polls the runner process and all descendants every 0.5 seconds.
- Tracks max `sum(rss)` and max `sum(vms)` across the process tree.
- Sums last-seen `cpu_times().user` and `.system` across PIDs at
  exit.
- Per-invocation measurement JSON saved under
  `<SESSION_DIR>/measurements/`.

USS / PSS not captured on Windows by psutil 6.0.0 by default.

Inter-invocation pause: 5 s. No `PARALLEL_SUBSETS` or
`TRAFFICFLOW_PARALLEL_SUBSETS` set in subprocess environments.

Measurement noise sources observed: none beyond standard desktop
process mix.

---

## 6. K=6 Speed-Parity Results

Campaign 4a, K=6 only, one subprocess per secondary, sequential
across all 8.

| Secondary | Wall (s) | Peak RSS (MiB) | CPU total (s) | CPU/wall | K=6 cell elapsed (s) |
|-----------|----------|----------------|---------------|----------|----------------------|
| SPY       | 11.14    | 265            |  9.98         | 0.90     |  7.04 (from 4b)      |
| AAPL      | 13.16    | 291            | 12.09         | 0.92     |  8.78 (from 4b)      |
| AMZN      |  5.57    | 251            |  4.80         | 0.86     |  1.63 (from 4b)      |
| GOOGL     |  5.57    | 216            |  4.89         | 0.88     |  2.30 (from 4b)      |
| META      |  7.10    | 210            |  6.27         | 0.88     |  3.70 (from 4b)      |
| MSFT      | 12.15    | 274            | 11.19         | 0.92     |  7.90 (from 4b)      |
| NVDA      |  6.08    | 224            |  5.14         | 0.85     |  2.56 (from 4b)      |
| TSLA      |  4.56    | 208            |  3.86         | 0.85     |  1.07 (from 4b)      |
| **Total** | **65.32**| -              | 58.24         | -        | -                    |

(K=6 cell elapsed values are taken from 4b's `per_cell_summary`
because 4a's manifest only records the single-cell elapsed; for
4b the per-cell extraction is unambiguous.)

Comparison to prior measurements (K=6 cell only):

| Secondary | PR #309 K=6 (s) | PR #310 K=6 (s) | PR #313 K=6 (s) | This audit 4b K=6 (s) |
|-----------|------------------|------------------|------------------|------------------------|
| SPY       | 7.29             | -                | 6.97             | 7.04                  |
| AAPL      | 8.62             | -                | 8.59             | 8.78                  |
| AMZN      | -                | 1.88             | 1.65             | 1.63                  |
| GOOGL     | -                | 2.34             | 2.27             | 2.30                  |
| META      | -                | 3.85             | 3.64             | 3.70                  |
| MSFT      | -                | 8.30             | 7.81             | 7.90                  |
| NVDA      | -                | 2.64             | 2.52             | 2.56                  |
| TSLA      | -                | 1.09             | 1.05             | 1.07                  |

K=6 per-cell timings are within roughly +/- 3 percent of the PR
#310 and PR #313 baselines for every secondary. **K=6 speed
parity vs prior runner measurements: confirmed.**

Total K=6 surface across 8 secondaries: 65.32 s sequential
subprocess (avg 8.16 s including ~2 s Python startup + import
per subprocess, ~6 s compute). For practical daily cadence, this
is comfortably under 2 minutes.

---

## 7. K=1..6 Practical Surface Results

Campaign 4b, K=1..6 sequential, one subprocess per secondary.

| Secondary | Wall (s) | Peak RSS (MiB) | CPU total (s) | CPU/wall | K1   | K2   | K3   | K4   | K5   | K6   |
|-----------|----------|----------------|---------------|----------|------|------|------|------|------|------|
| SPY       | 17.21    | 290            | 16.39         | 0.95     | 0.30 | 0.74 | 1.00 | 1.99 | 3.19 | 7.04 |
| AAPL      | 20.25    | 309            | 18.63         | 0.92     | 0.45 | 0.58 | 1.07 | 2.47 | 3.83 | 8.78 |
| AMZN      |  8.61    | 265            |  7.38         | 0.86     | 0.39 | 0.78 | 0.60 | 0.82 | 1.60 | 1.63 |
| GOOGL     |  8.11    | 249            |  7.00         | 0.86     | 0.30 | 0.46 | 0.57 | 0.69 | 1.03 | 2.30 |
| META      | 10.19    | 246            |  8.94         | 0.88     | 0.17 | 0.31 | 0.52 | 1.16 | 1.58 | 3.70 |
| MSFT      | 18.76    | 269            | 16.91         | 0.90     | 0.44 | 0.59 | 1.23 | 1.64 | 3.85 | 7.90 |
| NVDA      |  8.61    | 269            |  7.77         | 0.90     | 0.28 | 0.39 | 0.41 | 0.98 | 1.22 | 2.56 |
| TSLA      |  6.58    | 235            |  5.88         | 0.89     | 0.34 | 0.38 | 0.43 | 0.54 | 0.98 | 1.07 |
| **Total** | **98.32**| -              | 88.90         | -        | -    | -    | -    | -    | -    | -    |

Per-cell K=1..6 timings are within noise of PR #313 baselines
(no cell deviated more than ~5 percent for SPY/AAPL/MSFT/META; the
smaller secondaries' K=5 cells, which PR #310 did not measure,
are new but consistent with PR #313's first K=5 measurement).

K=1..6 surface total across 8 secondaries: **98.32 s
sequential** (avg 12.3 s per secondary). Operationally
acceptable as a daily-cadence default surface.

K=5 adds about 30-50 percent to the K=1..6 wall-clock for the
larger secondaries (SPY/AAPL/MSFT), but the absolute cost
(1.0 - 3.9 s per cell) is small and does not change the K=1..6
profile materially.

---

## 8. Orchestration Comparison

Campaign 4c, K=1..6, multi-secondary single invocation (one
runner process, comma-separated `--secondaries`).

| Metric | 4b sum across 8 (sequential) | 4c single invocation | Delta |
|---|---|---|---|
| Wall-clock        | 98.32 s          | **80.95 s**       | -17.37 s (-17.7 percent) |
| Peak RSS          | 309 MiB (per-sec max) | **980 MiB** (whole tree) | one shared working set |
| CPU total         | 88.90 s          | 77.50 s          | -11.4 s |
| CPU/wall ratio    | avg 0.90         | **0.96**         | higher utilization |
| Per-cell elapsed  | (matches 4b within noise)             | (matches 4b within noise)            | no per-cell regression |

Per-cell elapsed in 4c vs 4b is within roughly +/- 5 percent
for every (secondary, K) pair. The wall-clock savings come
entirely from eliminating seven Python interpreter startups and
seven `import trafficflow` cycles (~2.2 s each).

Peak RSS in 4c (980 MiB) is about 3.2x the largest per-secondary
subprocess (309 MiB AAPL) but still under 1 GiB. This is the
combined working set of all 8 secondaries' member PKLs and
intermediate compute state coexisting in one process. As a
fraction of the ~200 GiB operator-described context, 980 MiB is
about 0.48 percent.

Interpretation: multi-secondary single invocation is a free
17.7 percent wall-clock improvement for the daily K=1..6 surface
relative to the per-secondary-subprocess shape that PR #313
measured. It is not a substitute for legacy secondary-level
ThreadPoolExecutor concurrency (which would, in principle, deliver
roughly Nx speedup up to core count - this audit did not test
that and explicitly does not recommend it as default), but it is
a meaningful runner-as-is improvement with zero code change.

---

## 9. K=10..12 Context from PR #313

K=10..12 was NOT re-run in this audit. PR #313 is the source for
K-tail cost characterization:

- K=10..12 across the 8 secondaries totaled **5,720.79 s** out of
  PR #313's 6,400.48 s aggregate (about **89 percent** of full-K
  wall-clock).
- K=12 alone (mean 418 s, max 863 s for MSFT) is roughly 6-12x
  the cost of K=6.

For Phase E design, this audit recommends treating K=10..12 as a
**separable, heavy / opt-in stage** distinct from the
daily-cadence K=1..6 surface measured here. The K-tail cost is
not a runner orchestration defect; it is a combinatorial fact
about the engine that legacy benchmarks did not exercise at the
same scale.

---

## 10. Canonical Safety Check

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
- All 48 `combo_k=1..6.json` files: unchanged.
- `output/onepass/onepass.xlsx`: unchanged.
- All 64 member PKLs (K=1..6 union across 8 secondaries): unchanged.
- All 8 `price_cache/daily/<SEC>.csv`: SHA-256, size, and mtime
  byte-identical.

`cache/results/` and `cache/status/` are byte-identical pre/post.
This audit performed no refresh, and PR #308's network /
cache-write surface block held throughout.

---

## 11. Findings

11.1 **Correctness**: every invocation across campaigns 4a, 4b,
4c exited 0 with `status=ok`. Board files present per
expectation (1/1 for 4a, 6/6 per secondary for 4b, 48/48 for 4c).
Artifact lists complete in every manifest+sidecar pair.
Selected-build provenance matches the pre-snapshot SHA for every
secondary in every campaign. Zero `.tmp` residue.

11.2 **Privacy**: zero leak-token hits and zero drive-letter
pattern matches across all 51 scanned runner JSON artifacts (17
captured stdout + 17 on-disk manifests + 17 on-disk stdout
sidecars).

11.3 **Performance**:
- K=6 surface (4a): 65.32 s total / 8 secondaries; per-cell K=6
  timings within +/- 3 percent of PR #309/#310/#313 baselines.
- K=1..6 surface sequential (4b): 98.32 s total / 8 secondaries;
  per-cell timings within +/- 5 percent of PR #313 baselines.
- K=1..6 multi-secondary single invocation (4c): 80.95 s, a
  17.7 percent wall-clock improvement vs 4b. Per-cell timings
  preserved within +/- 5 percent.
- Peak RSS: 980 MiB max in 4c (all 8 secondaries co-resident);
  ~309 MiB max in the per-secondary subprocesses.
- CPU/wall ratio: 0.85 - 0.96 across all campaigns, consistent
  with `PARALLEL_SUBSETS=0` default.

11.4 **Orchestration**: legacy / Dash TrafficFlow secondary-level
parallelism is absent from `trafficflow_runner.py`. The runner
does support comma-separated multi-secondary input, and the
output layout is collision-safe under a single `--output-dir`
because per-secondary board-row files live in per-secondary
subdirectories. The 17.7 percent multi-secondary speedup
captured in 4c is the upper bound achievable from
process-reuse alone; further wall-clock reduction would
require either secondary-level concurrency (the legacy
ThreadPoolExecutor approach) or an engine-internal change. This
audit does not recommend adopting either as default for the
daily K=1..6 surface.

11.5 **Canonical safety**: zero modifications. All 9 roots
file-count and latest-mtime unchanged; all sampled SHAs
unchanged; cache/results and cache/status byte-identical;
all 8 price-cache CSVs byte-identical.

11.6 **No measurement noise** beyond standard desktop process
mix.

---

## 12. Recommendation

**PASS WITH NOTES.**

Direct answers to the six audit questions:

a. **Is headless using the same optimized compute path?** YES.
   `trafficflow_runner.py` line 1740 invokes
   `tf.build_board_rows(...)` through the lazy compute wrapper
   that pins `_find_latest_combo_table` and the PR #308 network
   / cache-write surface. `trafficflow.py` line 258 enables
   `TF_BITMASK_FASTPATH` by default. The wrapper does not
   override that flag. The runner exercises the bitmask
   `_subset_metrics_spymaster_bitmask` path documented in
   `2025-10-08_BITMASK_ENABLED_AS_PRODUCTION_DEFAULT.md`.

b. **Is K=6 headless speed-parity vs practical legacy
   expectations?** YES. K=6 per-cell timings match PR #309 /
   #310 / #313 within +/- 3 percent. The full 8-secondary K=6
   surface costs 65.32 s sequentially - well under 2 minutes
   for daily-cadence use.

c. **Is K=1..6 operationally acceptable?** YES. 98.32 s
   sequential or 80.95 s multi-secondary single invocation, for
   the full 8-secondary K=1..6 surface. Per-cell timings within
   noise of PR #313 baseline. Both invocation shapes are well
   under 2 minutes.

d. **Should K=10..12 be separated into a heavy / optional
   stage?** YES, recommended. PR #313 measured K=10..12 as
   about 89 percent of full-K wall-clock. The daily-cadence
   path should be K=1..6 (this audit measured 81-98 s); K=10..12
   is a separable heavy stage whose policy is for Phase E to
   decide explicitly (run on a slower cadence, run on opt-in,
   parallelize at the secondary level, or skip outside specific
   research runs).

e. **Is a runner amendment needed for secondary-level parallel
   orchestration before Phase E?** NO, not for the daily
   K=1..6 surface. The runner as-shipped supports
   comma-separated multi-secondary input with collision-safe
   isolated output. 4c showed a 17.7 percent speedup vs eight
   separate subprocesses, sufficient for the daily K=1..6
   surface. A secondary-level ThreadPoolExecutor amendment
   would be appropriate to revisit when (and if) K=10..12 is
   put on a daily-cadence schedule, but it is not a Phase E
   blocker for K=1..6.

f. **Can Phase E proceed, or is an optimization amendment
   required first?** **Phase E can proceed for the K=1..6
   canonical-write design.** No optimization amendment is
   required first. The audit identifies three concrete Phase E
   design inputs:
   - daily-cadence target surface: K=1..6, expected wall-clock
     ~80-100 s for all 8 secondaries combined;
   - high-K policy: K=10..12 is a separable / opt-in heavy
     stage, not a daily-cadence default;
   - orchestration shape: the multi-secondary single
     invocation (one process, all 8 secondaries) is collision-
     safe and saves about 17.7 percent vs per-secondary
     subprocesses, but the per-secondary subprocess shape
     remains a valid fallback.

---

This was a static review plus isolated-output runtime
measurement audit. No code was modified. No canonical artifacts
were modified. `TF_BITMASK_FASTPATH` state was inspected at
`trafficflow.py` line 258 and confirmed default-enabled; the
runner does not override it. K=10..12 was not rerun; PR #313 is
the source for K-tail cost. All session evidence under
`<SESSION_DIR>` is gitignored. Bottom-line: **Phase E
canonical-write design can responsibly begin for the K=1..6
daily-cadence surface**; K=10..12 should be designed as a
separable opt-in heavy stage.
