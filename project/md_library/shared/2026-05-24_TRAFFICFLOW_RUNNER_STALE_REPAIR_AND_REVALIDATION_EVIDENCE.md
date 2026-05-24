# TrafficFlow Runner Stale-PKL Repair and Phase B Revalidation Evidence

## 1. Scope and Non-Goals

This document records the bounded refresh of TrafficFlow member PKLs
flagged STALE by the Phase B runner, followed by a complete Phase B
dry-run revalidation across all 8 Phase 6I-79 secondaries.

Scope:

- Discover the current STALE member PKL set via `trafficflow_runner.py`
  dry-run across all 8 ImpactSearch secondaries (AAPL, AMZN, GOOGL,
  META, MSFT, NVDA, SPY, TSLA) at K=1, 2, 3, 4, 6.
- Refresh each STALE ticker one at a time via
  `signal_engine_cache_refresher.py` with `--max-sma-day 114`,
  `--cache-dir cache/results`, `--status-dir cache/status`; dry-run
  before write per ticker.
- Capture pre/post canonical-safety snapshots to prove only the
  authorized cache surfaces changed.
- Re-run `trafficflow_runner.py` dry-run across all 8 secondaries and
  verify every cell is now ELIGIBLE and every required PKL is OK.
- Produce a Phase C recommendation.

Non-goals:

- This is NOT Phase C; no canonical TrafficFlow output writes.
- This is NOT TrafficFlow compute execution; the compute path is
  never invoked.
- This is NOT broad cache regeneration; refresh is bounded to the
  discovered stale ticker set.
- No `--write`, `--refresh-missing-pkls`, `--refresh-stale-prices`,
  or `--allow-network-fetch` was passed to `trafficflow_runner.py`.
- No `trafficflow.refresh_secondary_caches` invocation.
- No Dash launch.
- No price-cache writes.

## 2. References

- PR #304 dry-run evidence:
  `md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_PHASE_B_REAL_DATA_DRY_RUN_EVIDENCE.md`
- PR #303 Phase B runner implementation:
  merged as `f392cd2`; trafficflow_runner.py with the strict
  freshness gate that flagged the STALE set.
- PR #301 readiness + K1/K2/K3/K4/K6 benchmark evidence:
  `md_library/shared/2026-05-23_TRAFFICFLOW_READINESS_AND_K_BENCHMARK_EVIDENCE.md`
- PR #302 Phase A scoping:
  `md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_EXECUTION_SURFACE.md`

## 3. Current Stale Ticker Inventory

Pre-task `trafficflow_runner.py` dry-run across all 8 secondaries
emitted the canonical Phase B JSON envelope per secondary. Parsing
the per-(sec, member) `pkl_readiness` list and deduplicating by base
ticker produced the inventory below.

| Field | Value |
|---|---|
| Unique STALE base tickers | **47** |
| PR #304 expected count | 48 |
| Matches PR #304 exactly | No (47 vs 48) |

Why the count differs: PR #304 reported 48 *per-(secondary, member)
PKL entries* with classification STALE. Some stale members appear
under more than one secondary, so deduplicating to unique base
tickers yields 47, not 48. This is consistent with PR #304's
analysis: 14 OK + 48 STALE = 62 per-entry observations across 8
secondaries / 61 unique base tickers; the 1-entry gap reflects a
single member that appears in two secondaries' required-PKL sets and
was counted twice in PR #304's per-entry tally.

Stale tickers (sorted, 47 total):

```
1058.HK, 4243.KL, 5095.KL, 5657.KL, AIRT, ALK-B.CO, APR.F,
ARB.AX, ARLP, AWR, CBT.F, CGLO, CLDN.L, CLH, CML.L, CP, EGY.AX,
EVZ.AX, EXC, EXPO, FCFS, GEOO34.SA, GIB-A.TO, HD, IMO, JCH.L,
JFJ.L, KA8.DE, KRDMB.IS, KU1.F, LLY, MALJF, MCOA, MDD.F, MWY.L,
OLN, PGH.L, PRGO, PRS.OL, SBS.DE, SBSI, SKYW, TFF.PA, UDR, VHI,
WEN, XAR.L
```

Per-ticker pre-refresh observations (from
`<SESSION_DIR>/stale_inventory/stale_tickers.json`):

- `max_sma_class`: MATCH for every stale ticker (declared 114 or
  schema-inferred). The STALE classification is purely a freshness
  finding, not a max-SMA-day finding.
- `benchmark_as_of_date`: `2026-05-22` for every (sec, member) pair
  (uniform secondary price-cache tail).
- `data_tail_date`: per-ticker varied between 2026-05-04 and
  2026-05-21 (all strictly older than `2026-05-22`).

The discovered count is within the gate (`<= 50`); the task proceeds.

## 4. Refresh Gate Verification

All eight refresh gates pass:

| Gate | Result |
|---|---|
| A. `signal_engine_cache_refresher.py` exists on `main` | PASS |
| B. `signal_engine_cache_refresher.py` unmodified in working tree (`git diff --quiet`) | PASS |
| C. Stale ticker list is exact and bounded | PASS (47 tickers) |
| D. Count <= 50 | PASS |
| E. Every refresh call will pass `--max-sma-day 114`, `--cache-dir cache/results`, `--status-dir cache/status` | PASS (orchestrator hard-coded) |
| F. Each refresh call processes exactly one ticker (`--ticker <T>`) | PASS |
| G. Pre-refresh SHA-256/size/mtime snapshots captured for every target | PASS |
| H. No target ticker outside the discovered stale list | PASS |

## 5. Pre-Refresh Canonical Safety Snapshot

Captured to `<SESSION_DIR>/preflight/pre_refresh_snapshot.json`.
File counts at task start:

| Root | Pre-run file count |
|---|---:|
| `output/stackbuilder/` | 5,388 |
| `output/impactsearch/` | 16 |
| `output/onepass/` | 2 |
| `output/trafficflow/` | 0 |
| `output/validation/` | 0 |
| `signal_library/data/stable/` | 71,980 |
| `cache/results/` | 3,267 |
| `cache/status/` | 1,648 |
| `price_cache/daily/` | 12 |

Per-secondary `selected_build.json` SHA-256 captured for all 8.
`output/onepass/onepass.xlsx` SHA-256 captured. Per-ticker
PKL/manifest/status pre-state captured for every member of the
47-ticker stale list.

## 6. Bounded Refresh Actions

Per-ticker dry-run + write sequence under
`signal_engine_cache_refresher.py`. Command shape:

Dry-run:

```
<PINNED_INTERPRETER> signal_engine_cache_refresher.py \
  --ticker <TICKER> --dry-run \
  --cache-dir cache/results \
  --status-dir cache/status \
  --max-sma-day 114
```

Write:

```
<PINNED_INTERPRETER> signal_engine_cache_refresher.py \
  --ticker <TICKER> --write \
  --cache-dir cache/results \
  --status-dir cache/status \
  --max-sma-day 114
```

All 47 dry-runs returned exit 0. All 47 writes returned exit 0.
Zero consecutive failures; the 3-strike rule never triggered.

Per-ticker timing summary:

- Dry-run elapsed (47 tickers): min 3.41 s (`GEOO34.SA`), max
  6.88 s (`JCH.L`), total 236.39 s.
- Write elapsed (47 tickers): min 3.86 s (`GEOO34.SA`), max
  7.64 s (`JCH.L`), total 261.67 s.
- Cumulative refresh wall (dry-run sum + write sum) ~498 s
  (about 8.3 minutes for the entire bounded refresh).

Post-write verification per ticker (full detail in
`<SESSION_DIR>/refresh/refresh_summary.json`):

- `cache/results/<TICKER>_precomputed_results.pkl` exists.
- PKL pickle-loads as a dict; all four required TrafficFlow
  fields present (`preprocessed_data`, `active_pairs`,
  `daily_top_buy_pairs`, `daily_top_short_pairs`).
- `preprocessed_data` columns include `SMA_114` for every ticker
  (47 / 47).
- Manifest sidecar exists; `params.max_sma_day == 114` for every
  ticker.
- Post-write `data_tail_date == 2026-05-22` for every ticker
  (47 / 47).
- Status sidecar exists at `cache/status/<TICKER>_status.json`.

No failures. No tickers outside the discovered stale list were
touched.

## 7. Post-Refresh Canonical Safety Check

Compared to the Part 5 pre-refresh snapshot:

| Root | Pre count | Post count | Unchanged |
|---|---:|---:|---:|
| `output/stackbuilder/` | 5,388 | 5,388 | yes (file count + latest mtime) |
| `output/impactsearch/` | 16 | 16 | yes |
| `output/onepass/` | 2 | 2 | yes |
| `output/trafficflow/` | 0 | 0 | yes (still empty) |
| `output/validation/` | 0 | 0 | yes (still empty) |
| `signal_library/data/stable/` | 71,980 | 71,980 | yes |
| `cache/results/` | 3,267 | 3,267 | **content changed (47 PKL + 47 manifest overwrites)** |
| `cache/status/` | 1,648 | 1,648 | **content changed (47 status overwrites)** |
| `price_cache/daily/` | 12 | 12 | yes |

All 8 per-secondary `selected_build.json` SHA-256s unchanged
(`selected_build_sha_unchanged = True`).
`output/onepass/onepass.xlsx` SHA-256 unchanged
(`output_onepass_xlsx_unchanged = True`).

`cache/results/` and `cache/status/` content changes are exactly the
authorized writes from the bounded refresh: file counts unchanged
(no new files created; every refreshed ticker had a pre-existing PKL
that was overwritten) and the latest mtime advanced into the
refresh window. The change scope is exactly the 47-ticker stale list
plus their manifest and status sidecars.

**No canonical artifact outside `cache/results/` and `cache/status/`
was modified by this task.**

## 8. Re-Run Dry-Run Methodology

Same command shape as Part 1 and PR #304, one secondary at a time:

```
<PINNED_INTERPRETER> trafficflow_runner.py \
  --secondaries <SECONDARY> \
  --k-range 1,2,3,4,6 \
  --stackbuilder-root output/stackbuilder \
  --output-dir output/trafficflow
```

Flags deliberately NOT passed: `--write`, `--refresh-missing-pkls`,
`--refresh-stale-prices`, `--allow-network-fetch`, `--explicit-build`.

Stdout / stderr captured to
`<SESSION_DIR>/rerun_dry_run/<SECONDARY>_stdout.json` and
`<SESSION_DIR>/rerun_dry_run/<SECONDARY>_stderr.log`.

## 9. Re-Run Per-Secondary Results

All 8 secondaries: exit code 0, valid JSON stdout, elapsed
1.32 - 1.51 s.

| Secondary | Exit | Elapsed (s) | Verdict | K1 / K2 / K3 / K4 / K6 |
|---|---:|---:|---|---|
| AAPL  | 0 | 1.38 | ELIGIBLE | E / E / E / E / E |
| AMZN  | 0 | 1.51 | ELIGIBLE | E / E / E / E / E |
| GOOGL | 0 | 1.33 | ELIGIBLE | E / E / E / E / E |
| META  | 0 | 1.32 | ELIGIBLE | E / E / E / E / E |
| MSFT  | 0 | 1.34 | ELIGIBLE | E / E / E / E / E |
| NVDA  | 0 | 1.33 | ELIGIBLE | E / E / E / E / E |
| SPY   | 0 | 1.42 | ELIGIBLE | E / E / E / E / E |
| TSLA  | 0 | 1.41 | ELIGIBLE | E / E / E / E / E |

(E = ELIGIBLE). Every secondary's `selected_build.json` was consumed
explicitly. No refusals. No `explicit_build_override`. No
directory-listing fallback.

Repair flag behavior verified:

- `--refresh-missing-pkls` was NOT passed -> every payload reports
  `would_refresh_pkls = []`.
- `--refresh-stale-prices` was NOT passed -> every payload reports
  `would_refresh_prices = []`.

`artifacts_written = []` and `next_stage_ready = false` for every
payload, matching the Phase B dry-run contract.

## 10. Re-Run Aggregate Analysis

### 10.1 Cell eligibility distribution

| Class | Count |
|---|---:|
| ELIGIBLE | **40** |
| ELIGIBLE_WITH_NOTES | 0 |
| DATA-GATED | 0 |
| PKL-GATED | 0 |
| MAX-SMA-GATED | 0 |
| STALE-GATED | **0** |
| REFUSED | 0 |
| ERROR | 0 |

### 10.2 PKL classification distribution

| Class | Count |
|---|---:|
| OK | **62** |
| MISSING / STALE / INVALID / UNREADABLE / SCHEMA_MISMATCH | 0 |
| MISMATCH_MAX_SMA / CONFLICTING_MAX_SMA / UNDETERMINABLE_MAX_SMA | 0 |
| UNKNOWN_USABLE | 0 |

### 10.3 Price cache classification distribution

| Class | Count |
|---|---:|
| OK | 8 (`tail_date = 2026-05-22` for every secondary) |

## 11. Comparison to PR #304 Baseline

| Metric | PR #304 | This task (post-refresh) |
|---|---:|---:|
| Cells ELIGIBLE | 2 / 40 | **40 / 40** |
| Cells STALE-GATED | 38 / 40 | **0 / 40** |
| PKL OK (per-entry) | 14 / 62 | **62 / 62** |
| PKL STALE (per-entry) | 48 / 62 | **0 / 62** |
| Secondaries verdict ELIGIBLE | 0 / 8 | **8 / 8** |
| Secondaries verdict ELIGIBLE_WITH_NOTES | 2 / 8 (GOOGL, META) | 0 / 8 (now full ELIGIBLE) |
| Secondaries verdict STALE-GATED | 6 / 8 | 0 / 8 |
| Price cache OK | 8 / 8 | 8 / 8 (unchanged) |
| Unique required base tickers | 61 | 61 (unchanged) |

PR #304's STALE-GATED finding is now closed. The runner's strict
freshness gate is satisfied for every required (sec, member) entry.

## 12. Privacy Sanitization Verification

Per-stdout token + drive-letter scan across the 8 re-run JSON files
under `<SESSION_DIR>/rerun_dry_run/`:

| Secondary | Private token hits | Drive-letter pattern hits |
|---|---:|---:|
| AAPL  | 0 | 0 |
| AMZN  | 0 | 0 |
| GOOGL | 0 | 0 |
| META  | 0 | 0 |
| MSFT  | 0 | 0 |
| NVDA  | 0 | 0 |
| SPY   | 0 | 0 |
| TSLA  | 0 | 0 |

Tokens scanned: the standard six-item denylist defined by the
operator's privacy rule (covering usernames, conda installation
brand, env name, OS user-data directory, OS user-home root, and the
project env name) plus a regex matching a single ASCII letter
followed by a colon and a path separator. Zero hits across all 8
files. `cwd` field in every payload is the literal placeholder
`<PROJECT_ROOT>`; all path fields are repo-relative POSIX.

## 13. Findings

- **No refresh failures.** 47 / 47 tickers refreshed cleanly via
  `signal_engine_cache_refresher.py` with `--max-sma-day 114`. Every
  PKL ends at `data_tail_date = 2026-05-22` with `SMA_114` present
  and the four required TrafficFlow schema fields intact.
- **No cells remain non-ELIGIBLE.** 40 / 40 cells classify
  ELIGIBLE; 0 STALE-GATED, 0 PKL-GATED, 0 MAX-SMA-GATED,
  0 DATA-GATED, 0 REFUSED, 0 ERROR.
- **No unexpected canonical writes.** Pre/post snapshots confirm
  only `cache/results/` and `cache/status/` content changed; all
  other roots untouched; all 8 `selected_build.json` SHA-256s and
  `output/onepass/onepass.xlsx` SHA-256 unchanged.
- **Stale count differs from PR #304 by 1.** PR #304 reported 48
  per-(sec, member) STALE entries; this task reports 47 unique base
  tickers (one ticker appeared in two secondaries' required-PKL
  sets and was double-counted in PR #304's per-entry tally). The
  delta is documented and benign.
- **No privacy leaks.** Zero token hits and zero drive-letter
  pattern hits across all 8 re-run stdout files.

## 14. Recommendation for Phase C

**PASS.** Phase C can proceed.

The input surface is now fully ELIGIBLE across all 8 Phase 6I-79
secondaries under the Phase B runner's strict freshness gate. Phase C
can therefore choose meaningful multi-K supervised-smoke targets from
the now-eligible 8-secondary surface; the actual Phase C target count
remains a deliberate operator/scope decision rather than a mandate to
run all 8.

Phase C scope reminders (carried forward from the PR #304 amendment):

- Phase C remains the supervised isolated-output smoke; no canonical
  `output/trafficflow/` writes (canonical writes are reserved for a
  later operator-authorized phase).
- Phase C may pass `--write` ONLY after the Phase C runner
  implementation explicitly supports isolated-output writes AND
  `--output-dir` points to an isolated noncanonical directory.
- Phase C must NOT issue network fetches unless
  `--allow-network-fetch` is explicitly authorized.
- Phase C compute work must be limited to the operator-selected
  supervised smoke target cells/secondaries.

## Notes on this evidence task

- This was a bounded repair plus revalidation task.
- The stale ticker list was discovered by `trafficflow_runner.py`
  dry-run classification at task start; expected stale count was 48
  from PR #304; actual count was 47 (documented above).
- Every refresh call passed `--max-sma-day 114` explicitly.
- Every refresh call used `--cache-dir cache/results` and
  `--status-dir cache/status`.
- Every refresh call processed exactly one ticker at a time.
- No canonical artifacts outside `cache/results/` and
  `cache/status/` were modified.
- `trafficflow_runner.py` was invoked in dry-run mode only for both
  Part 1 (discovery) and Part 6 (revalidation).
- No `--write` was passed to `trafficflow_runner.py`.
- No `--refresh-*` flags were passed to `trafficflow_runner.py`.
- No TrafficFlow compute function was invoked.
- No Dash server launched.
- No price-cache refresh was performed.
- All session evidence
  (`<SESSION_DIR>/preflight/`, `<SESSION_DIR>/stale_inventory/`,
  `<SESSION_DIR>/refresh/`,
  `<SESSION_DIR>/post_refresh_verification/`,
  `<SESSION_DIR>/rerun_dry_run/`, `<SESSION_DIR>/analysis/`) lives
  under `logs/` and is gitignored.
- Phase C can responsibly proceed.
