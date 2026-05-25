# TrafficFlow Runner ThreadPool Feasibility Benchmark

Session date (UTC): 2026-05-25
Session directory (gitignored): `<SESSION_DIR>` =
`logs/trafficflow_runner_threadpool_feasibility/20260525T000923Z/`
Branch: `trafficflow-runner-threadpool-feasibility-benchmark`

This benchmark asks whether the headless `trafficflow_runner.py`
would materially benefit from **across-secondary parallelism** (the
pattern legacy / Dash TrafficFlow used) for the K=1..6
daily-cadence surface, before Phase E canonical-write design
decides whether to include a threading / process-fan-out amendment.

**Headline result.** Yes, secondary-level parallelism materially
improves wall-clock: external process fan-out delivers **3.54x at
4 workers** and **1.93x at 2 workers** vs per-secondary sequential
baseline for the K=1..6 surface across all 8 Phase 6I-79
secondaries. The benefit is achievable today via an **operator-
side wrapper that spawns concurrent runner subprocesses**, with
zero runner code change and zero new thread-safety risk. **An
in-process ThreadPool inside the runner is NOT safe** under the
current monkey-patch guardrail design and should not be pursued
without a separate redesign of the selected-build pin / PR #308
network-cache surface block to be thread-local. Canonical safety
holds across all four executed campaigns. **Recommendation: PASS
WITH NOTES.** Phase E canonical-write design can proceed without a
runner threading amendment; operator-side process fan-out is the
recommended path if the 28-second-class wall-clock is operationally
desired.

---

## 1. Scope and Non-Goals

In scope:

- Static review of legacy `ThreadPoolExecutor` pattern in
  `trafficflow.py` vs the runner's multi-secondary loop.
- Thread-safety review of the runner's selected-build pin and
  PR #308 engine network/cache surface block.
- Four benchmark campaigns under isolated-output `--write` mode
  for the K=1..6 daily-cadence surface across all 8 Phase 6I-79
  secondaries:
  - **4a** Baseline A: per-secondary sequential subprocesses (8
    runs).
  - **4b** Baseline B: multi-secondary single subprocess (1 run,
    all 8 secondaries).
  - **4c** Experiment C: external process fan-out, 2 concurrent
    workers.
  - **4d** Experiment D: external process fan-out, 4 concurrent
    workers.
  - **4e** Experiment E: in-process ThreadPool harness - SKIPPED
    on safety grounds (Section 7).
- Pre/post canonical safety snapshots across 9 roots, 8
  `selected_build.json`, 8 `combo_leaderboard.xlsx`, 48
  `combo_k=1..6.json`, `onepass.xlsx`, 64 member PKLs, 8
  `price_cache/daily/<SEC>.csv`.

Out of scope (NOT performed):

- Modifying `trafficflow.py`, `trafficflow_runner.py`, or any
  engine / runner / test file.
- `PARALLEL_SUBSETS` / intra-secondary subset parallelism (a
  separate axis already investigated and rejected per PR #301
  and `2025-10-14_TRAFFICFLOW_SUBSET_PARALLELIZATION_TEST_RESULTS_AND_ANALYSIS.md`).
- K=10..12 measurement.
- Phase E canonical-write design.
- Any runner amendment implementation.
- Direct `trafficflow.build_board_rows` ThreadPool harness that
  bypasses runner guardrails.

---

## 2. References

- PR #314 - TrafficFlow runner headless speed-parity audit
  (PASS WITH NOTES, Phase E can proceed for K=1..6).
- PR #313 - Phase D full-K re-measurement (K=10..12 ~89 percent
  of full-K wall-clock).
- PR #310 - broader Phase C smoke (K=1,2,3,4,6).
- PR #301 - intra-secondary `PARALLEL_SUBSETS` finding (rejected
  as default).
- `md_library/trafficflow/2025-10-08_BITMASK_ENABLED_AS_PRODUCTION_DEFAULT.md`
- `md_library/trafficflow/2025-10-14_TRAFFICFLOW_SUBSET_PARALLELIZATION_TEST_RESULTS_AND_ANALYSIS.md`
- `md_library/trafficflow/2025-10-14_TRAFFICFLOW_PERFORMANCE_BOTTLENECK_ANALYSIS_AND_OPTIMIZATION_PROPOSALS.md`

---

## 3. Static Review: Legacy Dash ThreadPool vs Runner Loop

### 3.1 ThreadPoolExecutor in `trafficflow.py`

Three distinct ThreadPool sites in `trafficflow.py`:

| Site | Line | Context | Unit of work | Default workers | Env override |
|---|---|---|---|---|---|
| Cross-secondary cell fan-out | 3249-3262 | Inside the Dash `@app.callback` for the Refresh button (anchor `@app.callback` at line 3155) | `build_board_rows(sec, k, run_fence, missing_map)` per `(sec, k)` pair | `min(16, os.cpu_count() or 8)` | `TRAFFICFLOW_MAX_WORKERS` |
| Intra-secondary subset fan-out | 2798-2807 | Inside `compute_build_metrics_spymaster_parity` when `PARALLEL_SUBSETS=1` | One subset metric | `PARALLEL_SUBSETS_MIN_K` gate | `PARALLEL_SUBSETS` |
| Price-refresh fan-out | 1271-1273 | `_refresh_secondary_caches` (network) | One yfinance fetch | `PRICE_REFRESH_THREADS` | env-controlled |

The cross-secondary pattern at 3249-3262 is the **legacy Dash**
pattern this audit is asking about. It lives inside a Dash callback
and is gated by the Dash UI; it is NOT used by the headless
runner.

### 3.2 Runner multi-secondary execution loop

`trafficflow_runner.py` contains **zero** references to
`ThreadPoolExecutor`, `as_completed`, `max_workers`, `threading`,
`Lock`, or `concurrent.futures`. The runner is structurally
serial:

- `--secondaries` parse (line 161-162, "Comma/whitespace-separated").
- `resolve_secondaries` (line 319) returns a list.
- Per-secondary preflight: `for sec in secondaries: ...` at line
  2234. Sequential.
- Per-secondary write execution: also sequential in the same
  envelope.
- `build_board_rows` invoked one cell at a time inside
  `_default_compute_loader` (line 1740).

### 3.3 Runner safety invariants under threads

The runner's `_default_compute_loader` (line 1670+) protects
correctness with two module-level monkey-patches that **rebind
attributes on the imported `trafficflow` module**:

- `tf._find_latest_combo_table = _pinned_finder` (line 1737):
  pins the per-secondary leaderboard path. Raises
  `RuntimeError("trafficflow_runner Phase C: unexpected
  _find_latest_combo_table call for {sec} (expected
  {other_sec})")` if a different secondary's compute somehow
  hits the same pin.
- `engine_surface_saved = _patch_engine_network_surface(tf)`
  (line 1739): rebinds `tf._needs_refresh`,
  `tf._fetch_secondary_from_yf`, `tf._write_cache_file`, and
  `tf._persist_cache` to safe no-ops (PR #308 fix).

Both patches are **process-global module attribute rebinds**.
They restore in a `finally` block (line 1747-1750).

Thread-safety classification:

| Invariant | Thread safety under in-process ThreadPool |
|---|---|
| Selected-build pinning (`_find_latest_combo_table` monkey-patch) | NOT thread-safe. Two concurrent secondaries would clobber each other's pin; the active pin at any instant is whichever thread last rebound the attribute. The `RuntimeError` defensive raise would fire spuriously, or worse the engine could use the wrong leaderboard if the clobber happened between the pin and the lookup. |
| PR #308 network/cache-write surface block (`_needs_refresh`, `_fetch_secondary_from_yf`, `_write_cache_file`, `_persist_cache` monkey-patches) | NOT thread-safe. Same reason. A thread that has just `_restore_engine_surface`d during its `finally` would un-pin the surface for any concurrent thread that still needs it. |
| Privacy sanitization on JSON | Thread-safe per-invocation (output paths are per-secondary). |
| Atomic write pattern (`<file>.tmp` + `os.replace`) | Thread-safe per-file (different secondaries write to different paths in different subdirs). |
| Isolated output path enforcement (`is_isolated_output_dir`) | Stateless function; thread-safe. |
| Process-conflict check | Designed for cross-process collision detection, not in-process threading. Re-entrant in the same process. |
| Artifact list completeness | Per-invocation envelope; thread-safe if invocations do not share envelope state. |

### 3.4 In-process ThreadPool safety verdict

**NOT SAFE under the current runner design.** The selected-build
pin and the PR #308 network/cache-write surface block both rely on
module-attribute rebinding, which is process-global. Two
concurrent threads in the same Python process running per-secondary
compute would corrupt these guarantees.

This audit therefore SKIPS Experiment E (in-process ThreadPool
harness) and does not attempt a workaround that bypasses runner
guardrails.

### 3.5 Smallest plausible runner amendment shape

If a future task wants headless secondary-level parallelism inside
the runner process, the safest amendment shape is:

- **Replace module-attribute rebinding with thread-local
  dependency injection.** Pass `_find_latest_combo_table` and the
  PR #308 engine network/cache surface override as explicit
  parameters into `build_board_rows` (or wrap them in a
  `contextvars.ContextVar` so each thread sees its own pin).
- That redesign should land as its own PR with its own tests
  before any in-process ThreadPool is introduced.

This audit does **not** recommend that amendment as a Phase E
prerequisite. External process fan-out (Section 6) already
delivers the practical speedup with zero code change and zero new
risk.

---

## 4. Benchmark Methodology

- `psutil 6.0.0` process-tree polling at 0.5 s interval.
- For concurrent campaigns: poll the union of all live spawned
  PIDs and their descendants; aggregate peak `sum(rss)` and
  `sum(vms)` across the union; sum last-seen
  `cpu_times().user`+`.system` across PIDs at exit.
- Subprocess scheduling for 4c / 4d via
  `concurrent.futures.ThreadPoolExecutor(max_workers=N)` in the
  harness orchestrator process (the harness threads only call
  `Popen`+`wait`; they do not call into `trafficflow`).
- Scheduling order for 4c / 4d: `SPY, MSFT, AAPL, META, AMZN,
  GOOGL, NVDA, TSLA` (puts the four largest workloads first so
  they overlap rather than tail-stall).
- Inter-campaign pause: 10 s. Inter-subprocess pause (4a only):
  5 s.
- No `PARALLEL_SUBSETS` / `TRAFFICFLOW_PARALLEL_SUBSETS` set.
- Measurement noise sources observed: none beyond standard
  desktop process mix.
- Detected hardware (per `psutil.cpu_count`): **24 logical / 16
  physical cores** (Intel i7-13700F per CLAUDE.md).

Harness script (gitignored, not committed):
`<SESSION_DIR>/harness/orchestrator.py`.

---

## 5. Baselines

### 5.1 4a Baseline A - per-secondary sequential subprocesses (8 runs)

| Secondary | Wall (s) | Peak RSS (MiB) | CPU total (s) | CPU/wall |
|-----------|----------|----------------|---------------|----------|
| SPY       | 17.72    | 288            | 16.48         | 0.93     |
| AAPL      | 20.25    | 311            | 19.17         | 0.95     |
| AMZN      |  9.11    | 267            |  8.34         | 0.92     |
| GOOGL     |  8.61    | 249            |  7.89         | 0.92     |
| META      | 10.63    | 244            |  9.91         | 0.93     |
| MSFT      | 18.73    | 273            | 17.72         | 0.95     |
| NVDA      |  9.12    | 266            |  8.19         | 0.90     |
| TSLA      |  7.10    | 234            |  5.94         | 0.84     |
| **Total** | **101.28** | max 311      | 93.64         | 0.93     |

### 5.2 4b Baseline B - multi-secondary single invocation

One subprocess processes all 8 secondaries sequentially in
process:

- Wall: **83.02 s**
- Peak RSS: **980 MiB** (all 8 secondaries' working sets
  co-resident)
- CPU total: 79.75 s
- CPU/wall: **0.96** (single-threaded as expected)
- Speedup vs 4a: **1.22x** (recovers Python startup + import
  overhead for the 7 secondaries beyond the first)

(This matches the PR #314 4c result of 80.95 s within 2.5
percent.)

---

## 6. Process-Level Concurrency Results

### 6.1 4c Experiment C - 2-worker external process fan-out

| Metric | Value |
|---|---|
| Wall-clock (campaign) | **52.61 s** |
| Aggregated peak RSS | **575 MiB** |
| Aggregated CPU total | 93.49 s |
| CPU/wall ratio | **1.78** (close to ideal 2.0 for 2 workers) |
| Poll samples | 103 |
| Per-secondary exit codes | all 0 |

Speedup:

- vs 4a (per-secondary sequential): **1.93x**
- vs 4b (multi-single-invocation): 1.58x

### 6.2 4d Experiment D - 4-worker external process fan-out

| Metric | Value |
|---|---|
| Wall-clock (campaign) | **28.62 s** |
| Aggregated peak RSS | **1114 MiB** |
| Aggregated CPU total | 96.55 s |
| CPU/wall ratio | **3.37** (about 84 percent of ideal 4.0 for 4 workers) |
| Poll samples | 55 |
| Per-secondary exit codes | all 0 |

Speedup:

- vs 4a (per-secondary sequential): **3.54x**
- vs 4b (multi-single-invocation): 2.90x

### 6.3 CPU/wall scaling summary

CPU total stayed nearly constant across all four campaigns (4a
93.64 s, 4c 93.49 s, 4d 96.55 s) - the **same work** was done in
each campaign. The wall-clock improvement is entirely from
parallel execution. CPU/wall ratio scaling (0.93 -> 1.78 -> 3.37)
tracks worker count up to ~84 percent of ideal at 4 workers.

### 6.4 Memory aggregation observations

| Campaign | Aggregated peak RSS | Percent of 200 GiB |
|---|---|---|
| 4a per-secondary sequential | 311 MiB (single worker max) | ~0.15 % |
| 4b multi-single invocation  | 980 MiB                       | ~0.48 % |
| 4c 2-worker process fan-out | 575 MiB                       | ~0.28 % |
| 4d 4-worker process fan-out | 1114 MiB                      | ~0.54 % |

RAM is not a binding constraint at this scale. Even an 8-worker
extrapolation (not measured) would sit roughly at the 4b + 4d
working-set ceilings combined, well under 3 GiB.

---

## 7. In-Process ThreadPool Harness (Experiment E) - SKIPPED

Per Part 1d static-review verdict: SKIPPED.

Reason: the runner's `_default_compute_loader` relies on two
process-global module-attribute rebinds:

- `trafficflow._find_latest_combo_table = _pinned_finder`
- `_patch_engine_network_surface(trafficflow)` rebinding
  `_needs_refresh`, `_fetch_secondary_from_yf`,
  `_write_cache_file`, `_persist_cache`.

Both are restored in a `finally` block. Under concurrent threads,
each thread's `finally` would un-pin or rebind these attributes
while another thread's compute is still in flight, corrupting the
selected-build pin and the PR #308 network/cache-write surface
block.

An in-process ThreadPool inside the runner therefore requires a
**redesign of the guardrail pinning** to be thread-local (via
explicit parameter passing or `contextvars.ContextVar`) **before**
it can be tested safely. That redesign is out of scope for this
audit. This audit explicitly does not run a workaround harness
that bypasses the runner guardrails, because such a harness would
not answer the **runner** feasibility question safely.

---

## 8. Canonical Safety

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

- All 8 `selected_build.json`: unchanged.
- All 8 `combo_leaderboard.xlsx`: unchanged.
- All 48 `combo_k=1..6.json`: unchanged.
- `output/onepass/onepass.xlsx`: unchanged.
- All 64 member PKLs (K=1..6 union across the 8 secondaries):
  unchanged.
- All 8 `price_cache/daily/<SEC>.csv`: SHA-256, size, and mtime
  byte-identical.

`cache/results/` and `cache/status/` byte-identical pre/post. No
refresh occurred and PR #308's surface block held across all 24
runner subprocess invocations.

Privacy sanitization: zero leak-token hits and zero drive-letter
pattern matches across the runner JSON artifacts in
`<SESSION_DIR>/runs/`.

Atomic write pattern: zero `.tmp` residue under any campaign's
`isolated_output/` tree.

---

## 9. Findings

9.1 **Secondary-level parallelism materially improves wall-clock.**
4-worker external process fan-out is 3.54x faster than
per-secondary sequential and 2.90x faster than multi-single-
invocation, for the K=1..6 daily-cadence surface. CPU/wall
ratio reaches 3.37 at 4 workers (~84 percent of ideal). The same
work is being done; the improvement is genuine parallelism, not
artifact reduction.

9.2 **Benefit justifies an operator-side wrapper, not a runner
in-process amendment.** The 3.54x speedup is captured by an
external process fan-out (each subprocess is independent, no
shared module state). A runner-level in-process ThreadPool would
require redesigning the selected-build pin and PR #308 network
surface block to be thread-local before it could safely deliver
the same speedup. That redesign is a follow-up; it is not a
Phase E prerequisite.

9.3 **Safest implementation shape: external process-level fan-out.**
Either an operator wrapper script or a thin orchestrator that
calls `trafficflow_runner.py` once per secondary with bounded
concurrency (4 workers measured; could go higher on this 16-
physical-core host). Each subprocess remains a complete
isolated `--write` runner invocation honoring every existing
guardrail.

9.4 **Safe worker count: 4 is comfortable; 8 is plausible but
not measured.** At 4 workers, peak RSS is 1114 MiB (~0.54 percent
of 200 GiB) and CPU/wall ratio is 3.37 (~84 percent of ideal).
Going to 8 workers on the 16-physical-core host would likely
extract more wall-clock, but with diminishing returns past the
secondary count (only 8 secondaries are in the daily set, so 8
workers would still serialize the K-tail cells of the slowest
secondary). Not measured in this audit.

9.5 **Phase E can proceed without a runner threading amendment.**
The audit's preferred shape is to let Phase E ship with the
runner as-is (the operator can choose multi-single-invocation
~83 s or operator-side 4-worker fan-out ~29 s). A
runner-level in-process threading amendment can land as
deferrable optimization work, gated by the guardrail redesign
described in 3.5.

9.6 **K=10..12 should remain excluded from the daily threaded path.**
PR #313 measured K=10..12 as ~89 percent of full-K wall-clock
(individual K=12 cells up to 863 s). A daily 4-worker fan-out
on K=1..12 would still spend most of its time on the K-tail,
defeating the purpose of the daily cadence. K=10..12 should
remain a separable opt-in heavy stage, as PR #314 recommended.

9.7 **No canonical safety violations.** All 24 subprocess
invocations across the 4 campaigns wrote into isolated
per-secondary subdirectories. All 9 roots byte-identical
pre/post (`cache/results/`, `cache/status/`, `price_cache/daily/`,
`output/*/`, `signal_library/data/stable/`). All 192 board-row
files (48 cells x 4 campaigns) written successfully. Zero `.tmp`
residue. Privacy scan clean.

---

## 10. Recommendation

**PASS WITH NOTES.**

Direct answers to the six feasibility questions:

a. **Does secondary-level parallelism materially improve K=1..6
   runner wall-clock?** **YES.** 4-worker external process fan-out
   is **3.54x faster** than per-secondary sequential (101.28 s ->
   28.62 s) and **2.90x faster** than multi-single-invocation
   (83.02 s -> 28.62 s). CPU/wall ratio 3.37 at 4 workers
   confirms genuine parallelism.

b. **Is the benefit large enough to justify a runner amendment?**
   **NOT FOR THE DAILY K=1..6 SURFACE, NO.** The benefit is fully
   captured by an operator-side external process fan-out (zero
   runner code change, zero new risk). A runner-level in-process
   ThreadPool would require a thread-safety redesign of the
   selected-build pin and PR #308 surface block; that is a
   deferrable optimization, not a Phase E gate.

c. **Which implementation shape is safest?** **External
   operator/process-level fan-out**, in descending order of
   safety: (1) external operator wrapper that spawns N concurrent
   `trafficflow_runner.py` subprocesses; (2) process-level runner
   fan-out helper inside the codebase (still subprocess-based);
   (3) runner-level in-process `ThreadPoolExecutor` only after a
   guardrail redesign (3.5 above); (4) status-quo (no change) -
   acceptable if the 83-second multi-single-invocation path meets
   operational requirements.

d. **What worker count looks safe for about 200 GiB RAM and the
   observed CPU behavior?** **4 workers comfortable, 8 plausible.**
   4-worker aggregated peak RSS is ~1114 MiB (~0.54 percent of
   200 GiB). CPU/wall ratio of 3.37 at 4 workers suggests room to
   scale further on the 16-physical-core host. 8 workers was not
   measured in this audit. Anything beyond 8 workers exceeds the
   daily-secondary count and would not add benefit for K=1..6.

e. **Should Phase E proceed before or after a threading /
   process-fan-out amendment?** **Phase E can proceed BEFORE any
   threading amendment.** The recommended sequencing is: ship
   Phase E using the current runner shape (single invocation or
   external operator fan-out wrapper), then evaluate a
   runner-level orchestration amendment as a separate follow-up.

f. **Should K=10..12 remain excluded from the daily threaded
   path?** **YES.** K=10..12 dominates wall-clock (PR #313: ~89
   percent of full-K cost) regardless of parallelism. Daily
   cadence should remain K=1..6; K=10..12 is a separable opt-in
   heavy stage per PR #314.

---

This was a feasibility benchmark with no production code
modifications. All session evidence under `<SESSION_DIR>` is
gitignored. No canonical artifacts were modified. Process-level
concurrency was tested with real `trafficflow_runner.py --write`
subprocess invocations at 2 and 4 workers. In-process ThreadPool
(Experiment E) was SKIPPED on the static thread-safety review
verdict that the runner's module-level monkey-patch guardrails are
not thread-safe. Bottom line: **Phase E canonical-write design
can proceed without a runner threading amendment**; if operational
wall-clock matters, the recommended path is an **operator-side
external process fan-out at 4 workers** (28.62 s for the full
8-secondary K=1..6 surface, 3.54x the sequential baseline).
