# ImpactSearch benchmark toolkit

Three measurement-only diagnostics retained from the Phase 6I-57 SPY
ImpactSearch baseline work. **None of these scripts modifies any
engine/runtime file, exports a workbook, writes a validation
sidecar, or invokes `impactsearch_workbook_runner.py`.** They run
direct engine functions (or, for the scaling ladder, spawn isolated
subprocesses) and write only under `logs/` / their own session-root
directories. The canonical production output at
`output/impactsearch/SPY_analysis.xlsx` is never touched.

The pinned interpreter for every script is:

```
C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe
```

All commands assume the working directory is `project/`.

---

## 1. `benchmark_impactsearch_fastpath.py`

**Question it answers.** Does cold-vs-warm filesystem access to the
signal-library `.pkl` files explain a large part of the
Dash-fast / terminal-slow ImpactSearch runtime gap?

**What it measures.** Per-primary wall time for
`impactsearch.process_single_ticker(...)` over a deterministic
top-N slice of `signal_library/data/stable/*_stable_v*.pkl`, split
into `pkl_load_verify_seconds` (the wrapped
`_load_signal_library_quick` wall) and `non_load_compute_seconds`
(everything else), in three modes: COLD/cold-not-guaranteed, WARM,
PREWARM. Verifies `primary_yfinance_fetch_count == 0` for every
mode via the `IMPACT_REQUIRE_ZERO_PRIMARY_YF=1` gate.

**Exact run command.**

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" ^
  test_scripts/benchmarks/benchmark_impactsearch_fastpath.py ^
  --executor thread --limit 300
```

`--workers N` overrides the default worker resolution.
`--ignore-conflicts` proceeds despite a positive conflict check.

**Expected output shape.** A single stdout report: header (env
snapshot, primary hash, secondary prep, conflict check), one
`MODE: <label>` block per mode with per-mode timing stats and
yfinance role attribution, followed by a VERDICT block with four
speedup ratios and one of: cold filesystem I/O is not the main
cause / compute bottleneck / OS filesystem cache likely dominates /
benchmark contaminated / benchmark deferred.

**Calls production runner?** No. Calls the engine helper
`impactsearch.process_single_ticker` directly via an in-process
`ThreadPoolExecutor`.

**Writes to `output/`?** No. Reports are stdout only; the script
writes no files.

**Concurrent with another ImpactSearch run?** No. The script does
a non-invasive WMIC/`ps` conflict check before timing anything
and defers if a competing `impactsearch_workbook_runner` /
`impactsearch.py` / `onepass.py` process is detected; it also
defers on query errors, timeouts, or ambiguous output. The
`--ignore-conflicts` flag overrides this with a conspicuous
warning; timings may be contaminated.

---

## 2. `benchmark_impactsearch_stage_isolation.py`

**Question it answers.** Which of the four candidate layers
explains the 20-hour pathology seen in the prior terminal SPY
checkpoint?
- A. `process_primary_tickers` orchestration
- B. durable validation fold/candidate evaluation
- C. empirical validation permutations / bootstrap
- D. still unknown

**What it measures.** Wall time and instrumentation snapshots for
four stages across up to four deterministic 300-ticker slices
(`first300`, `evenly_spaced300`, `uvw300`, `last300`):

1. `direct_loop` — per-primary `process_single_ticker` in the
   parent (mirrors the fastpath benchmark, but only as a sanity
   anchor).
2. `process_primary_tickers_only` — `impactsearch.process_primary_tickers(SPY, slice, use_multiprocessing=...)`
   in a timeout-protected child, once per slice and once per
   snapshot-env profile (`snapshots_disabled` and
   `snapshots_production_default`).
3. `validation_core_no_empirical` — durable validation fold
   construction without empirical permutations, on a
   `--validation-slice-limit` sub-slice (default 25), in a
   timeout-protected child.
4. `validation_core_default_tiny` — durable validation with the
   default empirical layer on a `--tiny-validation-limit` sub-slice
   (default 10), in a timeout-protected child.

Stages 2–4 use `multiprocessing` child processes (Windows-safe
spawn) so progress-tracker globals do not bleed and so long
stages can be terminated without stranding the parent.

**Exact run command.**

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" ^
  test_scripts/benchmarks/benchmark_impactsearch_stage_isolation.py ^
  --slices first300 evenly_spaced300 ^
  --validation-slice-limit 25 ^
  --tiny-validation-limit 10 ^
  --validation-timeout-sec 600 ^
  --process-primary-timeout-sec 600
```

Useful flags: `--skip-direct-loop`, `--skip-process-primary`,
`--skip-validation-no-empirical`, `--skip-validation-default-tiny`
to omit specific stages; `--executor {serial,thread}` and
`--workers N` to control Stage 1; `--ignore-conflicts` to override
the conflict-check defer.

**Expected output shape.** Stdout report: header (env, conflict
check, slices), then one block per stage × slice × snapshot-env
profile combination, then summary tables that compute
`stage2_minus_stage1` and `stage3_minus_stage2` deltas and
classify the bottleneck into category A / B / C / D.

**Calls production runner?** No. Calls
`impactsearch.process_single_ticker`,
`impactsearch.process_primary_tickers`, and
`validation_engine` callable surfaces directly.

**Writes to `output/`?** No. Stdout-only report.

**Concurrent with another ImpactSearch run?** No. Same WMIC/`ps`
conflict-check defer behavior as the fastpath benchmark.

---

## 3. `benchmark_impactsearch_scaling_ladder.py`

**Question it answers.** Does the `impactsearch_workbook_runner.py`
threaded path actually deliver multi-core CPU speedup? Does the
runner add meaningful overhead vs the engine alone? Does worker
count, snapshot env vars, or slice composition (first-N vs
evenly-spaced-N) move the needle?

**What it measures.** Spawns 8 isolated runner subprocesses + 1
direct-engine subprocess against a fresh timestamped session root
under `logs/phase_6i57_baseline/legacy_fast_scaling_<ts>/`. Each
runner subprocess invokes
`impactsearch_workbook_runner.py --validation-mode legacy_fast
--secondaries SPY --primary-source explicit_csv --primaries <csv>
--use-multiprocessing --write --allow-network-fetch
--output-dir <run_dir>`, never the canonical
`output/impactsearch/` path. The direct-engine subprocess calls
`impactsearch.process_primary_tickers("SPY", slice,
use_multiprocessing=True, mark_complete=False)` with no workbook
export.

Run matrix:
- `first50_threaded_w8`, `first50_serial`,
- `first150_threaded_w4`, `first150_threaded_w8`,
  `first150_threaded_w16`,
  `first150_threaded_w8_snapshots_disabled`,
- `first300_threaded_w8`, `evenly_spaced300_threaded_w8`,
- `direct_engine_first300_w8`.

Each subprocess is sampled with `psutil` every ~500 ms to capture
RSS and user-CPU; final effective-cores = cpu_delta / wall.

**Exact run command.**

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" ^
  test_scripts/benchmarks/benchmark_impactsearch_scaling_ladder.py
```

No CLI flags. The script unconditionally runs all 9 subprocesses
sequentially. Total wall ≈ 4–6 minutes on the reference hardware.

**Expected output shape.** stdout shows one `[label] launching: ...`
line per subprocess and one `[label] rc=... wall=... cpu_delta=...
eff_cores=...` line per completion, ending with
`WROTE: <session_root>/results.json`. The session-root directory
contains: `results.json` (aggregated run table), per-run
subdirectories with `run.stdout.json` / `run.stderr.log` /
isolated `--output-dir` xlsx artifacts, and an auto-generated
`direct_engine_helper.py` (the script writes it at runtime and
spawns it as the direct-engine subprocess).

**Calls production runner?** Yes — the runner subprocesses launch
`impactsearch_workbook_runner.py`. However, each invocation uses an
isolated `--output-dir` under the session root; it does **not**
write to `output/impactsearch/`. The direct-engine subprocess calls
the engine helper directly with no export.

**Writes to `output/`?** No. All workbook output from this script
lives under
`logs/phase_6i57_baseline/legacy_fast_scaling_<ts>/<run_label>/`.

**Concurrent with another ImpactSearch run?** No. The script has no
built-in conflict check; it launches 9 subprocesses sequentially
that import `impactsearch` and consume the same signal-library
directory. If a production ImpactSearch run is in flight, defer
until that run completes — concurrent timing measurements would be
contaminated and you risk filesystem contention on the stable
library.

---

## Safety summary

| script | runs ImpactSearch runner | writes `output/impactsearch/` | has conflict-check defer | safe alongside production |
|---|:---:|:---:|:---:|:---:|
| `benchmark_impactsearch_fastpath.py` | no | no | yes | no |
| `benchmark_impactsearch_stage_isolation.py` | no | no | yes | no |
| `benchmark_impactsearch_scaling_ladder.py` | yes (isolated `--output-dir`) | no | no — defer manually | no |

The `legacy_fast` validation mode is exercised only by the scaling-
ladder script's runner subprocesses, and only against their isolated
`--output-dir` paths. The fastpath and stage-isolation scripts never
go through `impactsearch_workbook_runner.py` and therefore never
emit a workbook or a manifest.
