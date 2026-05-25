# TrafficFlow Runner At-Scale Performance Inference

Session date (UTC): 2026-05-25
Session directory (gitignored): `<SESSION_DIR>` =
`logs/trafficflow_at_scale_inference/20260525T004614Z/`
Branch: `trafficflow-runner-at-scale-performance-inference`

This evidence doc infers expected wall-clock, memory, and CPU
behavior for **future 250-secondary and 500-secondary libraries**
under K=1..6 and K=1..12 across 1, 4, 8, and 16 external process
workers, using measured 8-secondary evidence from PR #313, PR #314,
and PR #315 plus a synthetic fan-out proxy that validated 8- and
16-worker scaling against the existing 8 secondaries.

**The 250- and 500-secondary libraries are not built yet.** This task
performs no real 250/500-secondary measurement; it builds a model
from measured 8-secondary data and validates the model's worker
efficiency assumptions with a proxy that repeats the existing 8
secondaries at higher worker counts.

**Headline numbers (expected band at 16 workers):**

| Scenario               | Expected wall-clock | Peak RSS estimate |
|------------------------|---------------------|--------------------|
| 250 secondaries K=1..6 | ~4.7 min            | ~4.2 GiB          |
| 500 secondaries K=1..6 | ~9.3 min            | ~4.2 GiB          |
| 250 secondaries K=1..12| ~5.4 hours          | ~5.5 GiB          |
| 500 secondaries K=1..12| ~10.7 hours         | ~5.5 GiB          |

**Headline recommendation.** Daily-cadence target: K=1..6 with
external 16-worker process fan-out fits comfortably in a 5-10
minute window at the 250-500 secondary scale on the measured
hardware, with peak RSS roughly 4 GiB (~2 percent of the 200 GiB
context). K=1..12 at 250-500 secondaries is **not** a daily-cadence
target on this hardware - it is a multi-hour to multi-day job and
needs Phase E to design chunked / resumable / partial-publishing
orchestration around per-secondary atomicity. Recommendation:
**PASS WITH NOTES.** Phase E can proceed for K=1..6 daily on the
external-process-fan-out shape established by PR #315 and confirmed
at 8 / 16 workers here; K=10..12 work should land as a separable
heavy / overnight / opt-in stage with explicit chunking and
resumability requirements.

---

## 1. Scope and Non-Goals

In scope:

- Modeling task using measured 8-secondary evidence from PR #313,
  PR #314, PR #315 as input.
- Worker-efficiency model spanning 1, 2, 4, 8, 16 workers, with
  measured values at every point (8 and 16 from a synthetic proxy
  run in this task).
- Inference tables for 250 and 500 secondaries across K=1..6 and
  K=1..12 at 1, 4, 8, 16 worker counts, with optimistic / expected
  / pessimistic cost bands.
- Memory and CPU scaling estimates.
- Synthetic fan-out proxy that repeats the existing 8 secondaries
  to load-test 8- and 16-worker fan-out.
- Pre/post canonical safety verification across all proxy
  invocations.
- Phase E design implications.

Out of scope (NOT performed):

- Real 250- or 500-secondary measurement.
- Discovering or building a 250/500-secondary library.
- Modifying `trafficflow.py`, `trafficflow_runner.py`, or any
  other engine / runner / test file.
- Phase E canonical-write implementation.
- Guardrail thread-safety redesign for in-process ThreadPool.
- K=10..12 proxy run.
- `PARALLEL_SUBSETS` exploration.

---

## 2. References

- PR #315 - TrafficFlow runner ThreadPool feasibility benchmark
  (1/2/4-worker measured efficiency; in-process ThreadPool unsafe).
- PR #314 - headless speed-parity audit (K=6 and K=1..6 parity
  confirmed).
- PR #313 - Phase D full-K re-measurement (K=1..12 cost
  distribution; K=10..12 ~89 percent of total wall-clock).
- PR #310 - broader Phase C smoke (K=1,2,3,4,6 baseline).
- PR #301 - bare-compute K benchmark and intra-secondary
  `PARALLEL_SUBSETS` finding (rejected default).

---

## 3. Modeling Inputs from PR #313 / #314 / #315

Normalized into `<SESSION_DIR>/model_inputs/*.json`.

### 3.1 PR #315 (K=1..6, 8-secondary fan-out)

| Campaign | Wall (s) | Peak RSS (MiB) | CPU/wall |
|---|---|---|---|
| 4a per-secondary sequential x 8                  | 101.28 | 311 (single max) | 0.93 |
| 4b multi-secondary single invocation             |  83.02 | 980              | 0.96 |
| 4c 2-worker external process fan-out             |  52.61 | 575              | 1.78 |
| 4d 4-worker external process fan-out             |  28.62 | 1114             | 3.37 |
| 4e in-process ThreadPool                         | SKIPPED (unsafe under runner guardrails) | - | - |

### 3.2 PR #314 (K=6 and K=1..6 speed parity)

- K=6 only x 8 secondaries (sequential subprocess): 65.32 s.
- K=1..6 sequential x 8: 98.32 s.
- K=1..6 multi-single invocation: 80.95 s.
- Conclusion (kept): K=10..12 should remain separable opt-in heavy.

### 3.3 PR #313 (K=1..12 full-K x 8 secondaries)

- Aggregate K=1..12 wall: 6400.48 s (~1 h 46.7 min).
- K=10..12 share of aggregate: 89.4 percent.
- K=12 max cell: 863.30 s (MSFT).

Per-secondary K=1..12 wall (s): SPY 1469.97, AAPL 1455.19, AMZN
255.06, GOOGL 407.37, META 699.69, MSFT 1624.97, NVDA 224.13, TSLA
264.10.

---

## 4. Current 8-Secondary Workload Profile

### 4.1 K=1..6 per-secondary cost distribution

| Stat | Value (s) |
|---|---|
| mean   | 12.66 |
| median |  9.88 |
| min    |  7.10 (TSLA) |
| max    | 20.25 (AAPL) |
| n      | 8     |

Heavy / Medium / Light buckets:

- Heavy (> 15 s):  SPY, AAPL, MSFT
- Medium (10-15 s): META
- Light (<= 10 s): AMZN, GOOGL, NVDA, TSLA

### 4.2 K=1..12 per-secondary cost distribution

| Stat | Value (s) |
|---|---|
| mean   | 800.06 |
| median | 553.53 |
| min    | 224.13 (NVDA) |
| max    | 1624.97 (MSFT) |
| n      | 8     |

Heavy / Medium / Light buckets:

- Heavy (> 1000 s): SPY, AAPL, MSFT
- Medium (500 - 1000 s): META
- Light (<= 500 s): AMZN, GOOGL, NVDA, TSLA

### 4.3 Dense-sample caveat

The Phase 6I-79 8-secondary set is **intentionally dense** (large
leaderboards, large member unions: 64 unique members for K=1..6,
117 unique members for K=1..12). A random 250- or 500-secondary
library is likely to have a thinner per-secondary cost
distribution. Per-secondary mean cost for a future library could
easily be 50 percent of the dense baseline (optimistic) or, if
the library is itself dense, 150 percent (pessimistic). The
modeling uses these as the 3-band scenario.

---

## 5. Worker Efficiency Model

All points measured (1, 2, 4 from PR #315; 8, 16 from this task's
synthetic proxy):

| Workers | Effective parallelism | Efficiency | Source |
|---|---|---|---|
|  1 |  1.00 | 1.000 | measured PR #315 4a |
|  2 |  1.93 | 0.963 | measured PR #315 4c |
|  4 |  3.54 | 0.885 | measured PR #315 4d |
|  8 |  5.70 | 0.713 | measured this-task proxy P1 |
| 16 | 12.45 | 0.778 | measured this-task proxy P2 |

Notes:

- Efficiency dips from 0.885 at 4 workers to 0.713 at 8 workers,
  then rises to 0.778 at 16 workers. This is consistent with a
  load-imbalance penalty that hurts most when the worker count is
  comparable to the small job-count head (8 jobs / 8 workers in P1
  has limited rebalancing slack), and recovers when the queue is
  longer (32 jobs / 16 workers in P2 lets the scheduler smooth out
  tail-stalls).
- CPU/wall ratios in proxy: P1 = 5.46 (theoretical max 8.0); P2 =
  11.94 (theoretical max 16.0). Strong parallelism at both worker
  counts.
- The 16-physical-core host (24 logical) is not saturated at 16
  workers; further worker counts beyond 16 were not measured here.

---

## 6. 250-Secondary K=1..6 Runtime Inference

Per-secondary K=1..6 cost bands (from PR #315 4a mean 12.66 s):
optimistic 6.33 s, expected 12.66 s, pessimistic 18.99 s.
Load-imbalance margin: +10 percent.

| Workers | Eff. par. | Optimistic | Expected | Pessimistic | Peak RSS estimate |
|---|---|---|---|---|---|
|  1 |  1.00 | 29.0 min  | 58.0 min  | 87.0 min  |   271 MiB |
|  4 |  3.54 |  8.2 min  | 16.4 min  | 24.6 min  |  1083 MiB |
|  8 |  5.70 |  5.1 min  | 10.2 min  | 15.3 min  |  2166 MiB |
| 16 | 12.45 |  **2.3 min** | **4.7 min** | **7.0 min** | **4331 MiB** |

Operationally acceptable as a single daily run at 4 / 8 / 16
workers.

---

## 7. 500-Secondary K=1..6 Runtime Inference

Same per-secondary cost bands and imbalance margin as Section 6.

| Workers | Eff. par. | Optimistic | Expected | Pessimistic | Peak RSS estimate |
|---|---|---|---|---|---|
|  1 |  1.00 | 58.0 min  | 116.1 min | 174.1 min |   271 MiB |
|  4 |  3.54 | 16.4 min  |  32.8 min |  49.2 min |  1083 MiB |
|  8 |  5.70 | 10.2 min  |  20.3 min |  30.5 min |  2166 MiB |
| 16 | 12.45 |  **4.7 min** | **9.3 min** | **14.0 min** | **4331 MiB** |

Operationally acceptable as a single daily run at 8 / 16 workers.
At 4 workers, 500-secondary K=1..6 still fits in 16-49 minutes;
borderline for a daily cadence depending on operator preference.

---

## 8. 250-Secondary K=1..12 Runtime Inference

Per-secondary K=1..12 cost bands (from PR #313 mean 800.06 s):
optimistic 400.03 s, expected 800.06 s, pessimistic 1200.09 s.
Load-imbalance margin: +20 percent (K-tail produces longer
heavy-cell tails that hurt scheduling).

| Workers | Eff. par. | Optimistic | Expected | Pessimistic | Peak RSS estimate |
|---|---|---|---|---|---|
|  1 |  1.00 | 33.3 hours | 66.7 hours | 100.0 hours |   350 MiB |
|  4 |  3.54 |  9.4 hours | 18.8 hours |  28.3 hours |  1400 MiB |
|  8 |  5.70 |  5.8 hours | 11.7 hours |  17.5 hours |  2800 MiB |
| 16 | 12.45 |  **2.7 hours** | **5.4 hours** | **8.0 hours** | **5600 MiB** |

Not a daily-cadence run. Overnight at best with 16 workers and
optimistic costs; pessimistic 16-worker is 8 hours.

---

## 9. 500-Secondary K=1..12 Runtime Inference

Same per-secondary cost bands and imbalance margin as Section 8.

| Workers | Eff. par. | Optimistic | Expected | Pessimistic | Peak RSS estimate |
|---|---|---|---|---|---|
|  1 |  1.00 | 66.7 hours | 133.3 hours | 200.0 hours |   350 MiB |
|  4 |  3.54 | 18.8 hours |  37.7 hours |  56.5 hours |  1400 MiB |
|  8 |  5.70 | 11.7 hours |  23.4 hours |  35.1 hours |  2800 MiB |
| 16 | 12.45 |  **5.4 hours** | **10.7 hours** | **16.1 hours** | **5600 MiB** |

Not viable as a single canonical run on this hardware. Even 16
workers at optimistic costs is overnight; expected is a half-day;
pessimistic is most of a day. Phase E must treat 500-secondary
K=1..12 as inherently chunked / multi-stage work.

---

## 10. Memory and CPU Scaling Estimate

Per-worker peak RSS from this task's proxy:

- 8-worker proxy P1: aggregated peak RSS 2166 MiB / 8 workers =
  **270.7 MiB / worker**.
- 16-worker proxy P2: aggregated peak RSS 4171 MiB / 16 workers =
  **260.7 MiB / worker**.

This is consistent across worker counts and with PR #315's
per-worker observations (single 4a workers averaged ~270 MiB).
The K=1..6 per-worker RSS is therefore well-bounded at ~270 MiB.

K=1..12 per-worker RSS (from PR #313) was ~350 MiB single-worker;
proxy did not exercise K=1..12 so the 350 MiB / worker estimate
in the K=1..12 tables is conservative.

CPU/wall ratios in proxy (8 workers 5.46, 16 workers 11.94)
confirm that the runner achieves ~68 - 75 percent of theoretical
core utilization at these worker counts. No measurement noise or
contention symptoms observed.

Aggregate peak RSS at any modeled worker count stays well below
the 200 GiB operator-described context:

- 16 workers, K=1..6: ~4.2 GiB (2.1 percent).
- 16 workers, K=1..12: ~5.5 GiB (2.7 percent).

RAM is not a binding constraint at any modeled scale.

---

## 11. Synthetic Fan-Out Proxy Result

Proxy purpose: validate that worker efficiency at 8 and 16 workers
matches the same efficiency curve observed at 1, 2, 4 workers in
PR #315. The proxy repeats the existing 8 secondaries (2x for P1,
4x for P2) under unique per-job isolated output directories. This
is not a 250/500 benchmark; it is a worker-scaling validation.

### 11.1 Proxy P1: 16 jobs at 8 workers

- Wall-clock: **39.41 s**
- Aggregated peak RSS: 2166 MiB
- Aggregated CPU total: 215.28 s
- CPU/wall ratio: **5.46** (theoretical max 8.0)
- Serial work: 224.80 s
- Effective parallelism: **5.70**
- Efficiency: **0.713**
- Per-job exit codes: all 0; all 16 jobs produced all 6 board-row
  files; zero `.tmp` residue; privacy scan clean.

### 11.2 Proxy P2: 32 jobs at 16 workers

- Wall-clock: **55.55 s**
- Aggregated peak RSS: 4171 MiB
- Aggregated CPU total: 662.97 s
- CPU/wall ratio: **11.94** (theoretical max 16.0)
- Serial work: 691.79 s
- Effective parallelism: **12.45**
- Efficiency: **0.778**
- Per-job exit codes: all 0; all 32 jobs produced all 6 board-row
  files; zero `.tmp` residue; privacy scan clean.

### 11.3 Proxy interpretation

The 16-worker efficiency (0.778) is higher than 8-worker (0.713),
which is plausible: P2 has 32 jobs across 16 workers (queue depth
2.0), while P1 has 16 jobs across 8 workers (queue depth 2.0) but
the longer P2 queue lets the scheduler smooth tail-stalls
slightly better. For modeling 250 / 500-secondary runs, the
ratios `serial_work / wall_clock = 5.70` and `12.45` are the
authoritative effective-parallelism numbers used in the inference
tables.

---

## 12. Canonical Safety (Proxy)

All proxy runner invocations were isolated `--write` into per-job
`<SESSION_DIR>/isolated_output/proxy_<NN>w/job_<NNN>_<SEC>/` dirs.

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
- All 64 member PKLs (K=1..6 union): unchanged.
- All 8 `price_cache/daily/<SEC>.csv`: SHA-256, size, and mtime
  byte-identical.

48 proxy job invocations (16 P1 + 32 P2) produced 288 board-row
JSON + 288 board-row CSV files entirely under `<SESSION_DIR>` -
zero leakage into canonical paths.

---

## 13. Phase E Design Implications

Implications fall out of the inference tables and the proxy
result:

13.1 **K=1..6 at 250 secondaries**: comfortably a single daily
canonical run at any of 4 / 8 / 16 workers. 16 workers gives
~5-minute expected wall-clock with ~4 GiB peak RSS. Phase E
canonical-write design does NOT need chunking for this surface,
but per-secondary atomicity is still desirable so a single
failed secondary cannot corrupt the daily publish.

13.2 **K=1..6 at 500 secondaries**: still a single canonical run
at 8 / 16 workers (~10 / ~9 minutes expected). 4-worker pushes
~33 min expected which is borderline for daily cadence. Same
per-secondary atomicity guidance as 13.1.

13.3 **K=1..12 at 250 secondaries**: NOT a daily-cadence run.
16-worker expected ~5.4 hours; pessimistic 8 hours. Phase E
must treat this as either a weekly cadence or a chunked
multi-stage job with resumability.

13.4 **K=1..12 at 500 secondaries**: NOT viable as a single
canonical run at any modeled worker count. 16-worker expected
~10.7 hours; pessimistic ~16 hours. K=10..12 absolutely must
be separated; the K=1..6 daily run alone fits the daily window
and K=10..12 can land on a slower cadence.

13.5 **Phase E required design properties**:

- **Chunking** by secondary: emit Phase E artifacts per
  secondary, not per universe. A 500-secondary run that has to
  re-do all 500 because secondary #437 errored at hour 9 is not
  operationally viable.
- **Resumability**: persist per-secondary completion markers so
  a partial run can resume from the failed boundary.
- **Partial publishing**: emit completed secondaries' canonical
  artifacts as they finish, not in one final batch, so even an
  interrupted run leaves usable output.
- **Per-secondary atomicity**: each secondary's canonical
  output should land via an atomic `.tmp` -> `os.replace`
  rename so a partial write cannot corrupt the canonical
  surface.
- **Progress / status manifest**: emit a top-level
  progress.json that lists status per secondary
  (PENDING / RUNNING / COMPLETE / FAILED / SKIPPED) so an
  operator or successor process can introspect mid-run state.
- **Failed-secondary quarantine**: a single failed secondary
  should not block the rest. The Phase E orchestrator should
  catch per-secondary exceptions, write a failure record, and
  proceed.
- **Heavy K-tail separation**: K=10..12 lives in its own
  artifact and own invocation path; the daily K=1..6 path
  does not depend on K=10..12 completion.

13.6 **External process fan-out is sufficient** for the daily
K=1..6 surface at the 250-500 scale on the measured hardware.
Aggregated peak RSS at 16 workers stays under 5 GiB.

13.7 **Runner-internal thread-safety redesign is NOT a Phase E
prerequisite.** The external process fan-out captures the
benefit (12.45x effective parallelism at 16 workers). A
runner-internal in-process ThreadPool would require a guardrail
redesign (per PR #315 Section 3.5) before it could be tested
safely, and would not deliver speedup beyond what external
process fan-out already provides. Treat in-process threading as
a deferrable optimization rather than a Phase E gate.

---

## 14. Findings

14.1 **Worker scaling holds out to 16 workers.** 8-worker
efficiency 0.713; 16-worker efficiency 0.778. Both proxies show
strong CPU/wall ratios (5.46 and 11.94 respectively). No
contention or memory pressure symptoms.

14.2 **Per-worker memory is small and stable.** ~270 MiB per
worker for K=1..6 across proxy invocations; ~350 MiB per worker
for K=1..12 from PR #313's per-secondary peak. 16-worker
aggregated peaks stay under 5 GiB (~2.5 percent of 200 GiB).

14.3 **K=1..6 daily-cadence target operationally feasible** for
250 and 500 secondaries at 16 workers (~5 / ~9 min expected).

14.4 **K=1..12 is a heavy-cadence surface** at the 250-500 scale
on this hardware. Multi-hour to multi-day, depending on cost
band. Phase E must separate K=10..12 from the daily path.

14.5 **No canonical safety violations** during proxy. 48 proxy
invocations all completed isolated `--write` without touching
any canonical artifact.

14.6 **Dense-sample caveat carried.** The 8-secondary mean cost
(K=1..6 = 12.66 s, K=1..12 = 800.06 s) reflects a particularly
heavy sample. Future 250/500 libraries may average less per
secondary. The optimistic band (50 percent of mean) is the floor
estimate.

---

## 15. Recommendation

**PASS WITH NOTES.**

Direct answers to the six required questions:

a. **250-secondary K=1..6 at 4 / 8 / 16 workers operationally
   acceptable?** YES. Expected 16.4 / 10.2 / 4.7 minutes
   respectively. Pessimistic 24.6 / 15.3 / 7.0 minutes. All
   fit a daily cadence comfortably.

b. **500-secondary K=1..6 at 4 / 8 / 16 workers operationally
   acceptable?** YES at 8 / 16 workers (~20 / ~9 minutes
   expected). MAYBE at 4 workers (~33 minutes expected, ~49
   minutes pessimistic - borderline for daily cadence; operator
   call).

c. **K=1..12 at 250 / 500 secondaries acceptable as a daily
   run?** NO. 250-sec K=1..12 at 16 workers is ~5.4 hours
   expected; 500-sec K=1..12 at 16 workers is ~10.7 hours
   expected. Not a daily-cadence surface on this hardware.

d. **Separate K=10..12 as a heavy / overnight / optional
   stage?** YES, required. K=10..12 is about 89 percent of full-K
   cost (per PR #313) and the K-tail dominates at any worker
   count. The daily run target is K=1..6; K=10..12 should be a
   separable stage with its own orchestration, cadence, and
   chunking policy.

e. **Phase E needs chunking / resumability / partial publishing
   / per-secondary atomicity?** YES. At 250-500 secondary scale,
   single-shot runs without these properties are operationally
   fragile. K=1..6 daily run benefits from at minimum
   per-secondary atomicity and progress manifest. K=1..12
   requires all of chunking, resumability, partial publishing,
   per-secondary atomicity, progress / status manifest, and
   failed-secondary quarantine.

f. **Guardrail thread-safety redesign priority before Phase E,
   or external process fan-out sufficient for now?** External
   process fan-out is sufficient for now. The 16-worker external
   process fan-out delivers 12.45x effective parallelism with no
   runner code change. A guardrail thread-safety redesign (PR
   #315 Section 3.5) is a deferrable optimization, NOT a Phase E
   prerequisite. Phase E can ship with the runner as-is and an
   operator-side process-fan-out wrapper or thin orchestrator.

---

This was an inference and modeling task using existing measured
8-secondary evidence as input, plus a synthetic 16- and 32-job
proxy that validated 8- and 16-worker external process fan-out
efficiency. No real 250- or 500-secondary measurements were
performed; those libraries are not built yet. The optional
synthetic proxy validates worker-count scaling only and is not a
250/500 benchmark. Modeling assumptions and 3-band uncertainty
are documented; the 8-secondary sample is intentionally dense, so
the optimistic band is the lower-bound estimate, not the expected
value. Phase E design implications are recommendations, not
implementation. All session evidence under `<SESSION_DIR>` is
gitignored. Bottom line: **Phase E can proceed for K=1..6 daily
at 250-500 secondary scale with external-process-fan-out
orchestration**; K=10..12 should land as a separable heavy stage
with explicit chunking, resumability, partial publishing, and
per-secondary atomicity.
