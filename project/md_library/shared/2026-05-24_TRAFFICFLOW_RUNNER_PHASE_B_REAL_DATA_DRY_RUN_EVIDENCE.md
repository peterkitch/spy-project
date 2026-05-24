# TrafficFlow Runner Phase B Real-Data Dry-Run Evidence

## 1. Scope and Non-Goals

This document records the first-time invocation of the merged
`trafficflow_runner.py` from PR #303 against the real canonical
inputs on disk (Phase 6I-79 StackBuilder outputs, `cache/results`
PKLs, `price_cache/daily` secondaries). It is an evidence-only PR.

Scope:

- Invoke `trafficflow_runner.py` once per Phase 6I-79 secondary
  (AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY, TSLA) in strict
  dry-run mode with K levels 1, 2, 3, 4, 6.
- Capture and analyze the structured JSON stdout per invocation.
- Verify privacy sanitization, JSON shape, `selected_build.json`
  consumption, input readiness classification, max-SMA and freshness
  checks, and per-cell eligibility.
- Capture pre/post canonical-safety snapshots to prove no canonical
  artifacts were modified.
- Compare results against PR #301 readiness expectations and produce
  a Phase C recommendation.

Non-goals:

- This is NOT Phase C. Per the Phase A scoping doc, Phase C is the
  supervised smoke that writes to an isolated noncanonical
  `--output-dir`; operator-authorized canonical writes remain a
  later phase, not Phase C.
- This is NOT runner implementation.
- This is NOT TrafficFlow compute execution.
- No `--write`, no `--refresh-missing-pkls`, no `--refresh-stale-prices`,
  no `--allow-network-fetch` was passed.
- No TrafficFlow compute function was invoked.
- No `signal_engine_cache_refresher.py` invocation.
- No `trafficflow.refresh_secondary_caches` invocation.
- No Dash launch.

## 2. References

- Phase A scoping doc:
  `md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_EXECUTION_SURFACE.md`
- PR #301 readiness + K1/K2/K3/K4/K6 benchmark evidence:
  `md_library/shared/2026-05-23_TRAFFICFLOW_READINESS_AND_K_BENCHMARK_EVIDENCE.md`
- Phase 6I-79 production StackBuilder run evidence:
  `md_library/shared/2026-05-23_PHASE_6I_79_STACKBUILDER_PRODUCTION_RUN_EVIDENCE.md`
- PR #303 Phase B runner implementation: merged into `main` as squash
  commit `f392cd2 TrafficFlow runner Phase B: dry-run scaffold and tests`.
  Phase B amendment chain (`46d79c3` -> `8ccb835` -> `a37ee6e` ->
  squash `f392cd2`) is preserved on origin.

## 3. Pre-Run Canonical Safety Snapshot

Pre-run snapshot captured before any invocation. File counts:

| Root | Pre-run file count |
|---|---:|
| `output/stackbuilder/` | 5,388 |
| `output/impactsearch/` | 16 |
| `output/onepass/` | 2 |
| `signal_library/data/stable/` | 71,980 |
| `cache/results/` | 3,267 |
| `cache/status/` | 1,648 |
| `price_cache/daily/` | 12 |

Per-secondary `selected_build.json` SHA-256 captured for all 8
secondaries. `output/onepass/onepass.xlsx` SHA-256 captured. Full
snapshot stored at `<SESSION_DIR>/preflight/pre_run_snapshot.json`
(gitignored).

## 4. Test Suite Re-Run Confirmation

Before invoking the runner against real data:

```
<PINNED_INTERPRETER> -m pytest test_scripts/test_trafficflow_runner.py -q
-> 46 passed in 1.32s
```

The merged runner is in a known-good state.

## 5. Invocation Methodology

Exact command shape per secondary:

```
<PINNED_INTERPRETER> trafficflow_runner.py \
  --secondaries <SECONDARY> \
  --k-range 1,2,3,4,6 \
  --stackbuilder-root output/stackbuilder \
  --output-dir output/trafficflow
```

Flags deliberately NOT passed (per Phase B + this task's contract):

- `--write`
- `--refresh-missing-pkls`
- `--refresh-stale-prices`
- `--allow-network-fetch`
- `--explicit-build`

Each invocation's stdout went to
`<SESSION_DIR>/runs/<SECONDARY>_stdout.json` and stderr to
`<SESSION_DIR>/runs/<SECONDARY>_stderr.log`. The orchestrator
captured start/end UTC timestamps and exit codes.

## 6. Per-Secondary Results

All 8 secondaries: exit code 0, valid JSON stdout, all required
Phase B JSON envelope fields present
(`schema_version`, `stage`, `run_id`, `status`, `started_at`,
`ended_at`, `elapsed_seconds`, `cwd`, `git_head`, `inputs`,
`effective_config`, `process_conflict_result`,
`input_readiness_summary`, `per_secondary_results`,
`selected_build_consumed`, `benchmark_eligibility`,
`would_refresh_pkls`, `would_refresh_prices`, `artifacts_written`,
`warnings`, `errors`, `next_stage_ready`, `verdict`).

Per-secondary summary:

| Secondary | Exit | Elapsed (s) | JSON parsed | sel_build consumed | Price cache | Per-K eligibility | Aggregate verdict |
|---|---:|---:|---|---|---|---|---|
| AAPL  | 0 | 1.51 | yes | yes (secondary=AAPL, selected_k=12, policy=v2.total_capture_then_latest) | OK tail=2026-05-22 (11,439 rows) | K1/K2/K3/K4/K6 = STALE-GATED | STALE-GATED |
| AMZN  | 0 | 1.42 | yes | yes (secondary=AMZN, selected_k=12, policy=v2.total_capture_then_latest) | OK tail=2026-05-22 (7,301 rows) | K1/K2/K3/K4/K6 = STALE-GATED | STALE-GATED |
| GOOGL | 0 | 1.46 | yes | yes (secondary=GOOGL, selected_k=12, policy=v2.total_capture_then_latest) | OK tail=2026-05-22 (5,475 rows) | K1 = ELIGIBLE; K2/K3/K4/K6 = STALE-GATED | ELIGIBLE_WITH_NOTES |
| META  | 0 | 1.39 | yes | yes (secondary=META, selected_k=12, policy=v2.total_capture_then_latest) | OK tail=2026-05-22 (3,523 rows) | K1 = ELIGIBLE; K2/K3/K4/K6 = STALE-GATED | ELIGIBLE_WITH_NOTES |
| MSFT  | 0 | 1.42 | yes | yes (secondary=MSFT, selected_k=12, policy=v2.total_capture_then_latest) | OK tail=2026-05-22 (10,127 rows) | K1/K2/K3/K4/K6 = STALE-GATED | STALE-GATED |
| NVDA  | 0 | 1.42 | yes | yes (secondary=NVDA, selected_k=12, policy=v2.total_capture_then_latest) | OK tail=2026-05-22 (6,876 rows) | K1/K2/K3/K4/K6 = STALE-GATED | STALE-GATED |
| SPY   | 0 | 1.44 | yes | yes (secondary=SPY, selected_k=12, policy=v2.total_capture_then_latest) | OK tail=2026-05-22 (8,386 rows) | K1/K2/K3/K4/K6 = STALE-GATED | STALE-GATED |
| TSLA  | 0 | 1.34 | yes | yes (secondary=TSLA, selected_k=12, policy=v2.total_capture_then_latest) | OK tail=2026-05-22 (4,000 rows) | K1/K2/K3/K4/K6 = STALE-GATED | STALE-GATED |

All 8 `selected_build.json` files were consumed explicitly. No
secondary was refused. No `explicit_build_override` was triggered.
The runner did NOT fall back to a latest-by-ctime directory scan.

Repair flag behavior verified per spec:

- `--refresh-missing-pkls` was NOT passed: every payload reports
  `would_refresh_pkls = []`.
- `--refresh-stale-prices` was NOT passed: every payload reports
  `would_refresh_prices = []`.

`artifacts_written = []` and `next_stage_ready = false` for every
invocation, matching the Phase B dry-run contract.

## 7. Aggregate Analysis

### 7.1 Cell eligibility distribution (across all 8 secondaries x 5 K levels)

| Class | Count | Expected per PR #301 |
|---|---:|---:|
| ELIGIBLE | **2** | 40 |
| ELIGIBLE_WITH_NOTES | 0 | 0 |
| DATA-GATED | 0 | 0 |
| PKL-GATED | 0 | 0 |
| MAX-SMA-GATED | 0 | 0 |
| STALE-GATED | **38** | 0 |
| REFUSED | 0 | 0 |
| ERROR | 0 | 0 |

Two ELIGIBLE cells: GOOGL K=1 and META K=1 (the only K=1 builds
whose lone member has a PKL data tail >= 2026-05-22).

### 7.2 PKL classification distribution (per-(sec, member) entries)

| Class | Count |
|---|---:|
| OK | 14 |
| STALE | 48 |
| MISSING / INVALID / UNREADABLE / SCHEMA_MISMATCH | 0 |
| MISMATCH_MAX_SMA | 0 |
| CONFLICTING_MAX_SMA | 0 |
| UNDETERMINABLE_MAX_SMA | 0 |
| UNKNOWN_USABLE | 0 |

`OK` + `STALE` = 62 instances across the 8 secondaries. Member
overlap across secondaries produces 62 instances of 61 unique base
tickers (some members appear in multiple secondaries; the runner
classifies them per-secondary). The `max_sma_class` is `MATCH` for
every PKL - no max-SMA-day regression.

### 7.3 Price cache classification distribution

| Class | Count |
|---|---:|
| OK | 8 |
| MISSING / STALE / UNREADABLE | 0 |

All 8 secondary price caches were classified `OK` with `tail_date =
2026-05-22`, matching PR #301's post-amendment uniform tail.

### 7.4 Timing

Total wall-clock across 8 invocations: **11.40 s** (1.34 - 1.51 s
per secondary; median ~1.42 s). The runner's dry-run readiness
classification path is well under the 10-minute long-running
threshold for every secondary.

### 7.5 Unique required base ticker count

**61** unique base member tickers required across the 8 secondaries
x K=1/2/3/4/6 - exact match to PR #301 readiness inventory.

## 8. Privacy Sanitization Verification

Per-stdout-JSON token scan across all 8 files:

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

Tokens scanned: the standard six private-token denylist defined by
the operator's privacy rule (covering usernames, conda installation
brand, env name, OS user-data directory, OS user-home root, and the
project env name), plus a regex that matches a single ASCII letter
followed by a colon and a path separator (the typical Windows
drive-letter prefix shape). Zero hits across all 8 files.

Sampling SPY's stdout:

- `cwd` field is the literal placeholder string `<PROJECT_ROOT>`.
- All path-like fields are repo-relative POSIX strings (e.g.
  `output/stackbuilder/SPY/<run_dir>/combo_leaderboard.xlsx`,
  `price_cache/daily/SPY.csv`,
  `cache/results/<TICKER>_precomputed_results.pkl`).
- `process_conflict_result.conflicts == []` (no conflicts in this
  session); the raw-cmdline sanitization path was not exercised in
  this dry-run, but it remains under unit-test coverage in PR #303.

## 9. Post-Run Canonical Safety Check

Post-run snapshot compared to the Part 3 pre-run snapshot:

| Root | Pre count | Post count | Unchanged |
|---|---:|---:|---:|
| `output/stackbuilder/` | 5,388 | 5,388 | yes (latest mtime unchanged) |
| `output/impactsearch/` | 16 | 16 | yes |
| `output/onepass/` | 2 | 2 | yes |
| `signal_library/data/stable/` | 71,980 | 71,980 | yes |
| `cache/results/` | 3,267 | 3,267 | yes |
| `cache/status/` | 1,648 | 1,648 | yes |
| `price_cache/daily/` | 12 | 12 | yes |

All 8 per-secondary `selected_build.json` SHA-256s unchanged
(verified by `selected_build_sha_unchanged = True`).
`output/onepass/onepass.xlsx` SHA-256 unchanged
(`output_onepass_xlsx_unchanged = True`).

**No canonical artifact was modified by this task.**

## 10. Deviations from PR #301 Expectations

Two material deviations vs PR #301:

1. **Cell eligibility**: PR #301 reported 40/40 cells ELIGIBLE.
   The Phase B runner reports 2/40 ELIGIBLE and 38/40 STALE-GATED.
2. **PKL classification**: PR #301 reported 61/61 required PKLs
   OK. The Phase B runner reports 14 OK + 48 STALE per-(sec, member)
   entries.

Cause: the **Phase B runner enforces a stricter PKL freshness gate
that PR #301's readiness check did not apply**. The amendment chain
for PR #303 added a STALE classification in `classify_pkl` that
compares each PKL's `preprocessed_data.index.max()` against the
secondary's price-cache `tail_date` (here `2026-05-22`). When a
PKL's data tail is strictly older than the benchmark date it is
classified `STALE`, even if its `max_sma_day == 114` and schema
fields are complete.

PR #301's readiness rule classified PKLs as OK on a max-SMA-day +
schema basis only and produced an aggregate "all OK" verdict without
gating on per-PKL tail date against the secondary cache tail.
PR #301's readiness section 6.1 and 7 documented the freshness rule
verbally but the per-PKL inspection at that time did not compute
the cross-check against each secondary's `tail_date`. The Phase B
amendment closed exactly that gap.

Sampled evidence (from `<SESSION_DIR>/runs/SPY_stdout.json`):

- SBSI: `data_tail_date = 2026-05-04`, `benchmark_as_of_date =
  2026-05-22`, `freshness_class = STALE`, `max_sma_class = MATCH`,
  `has_SMA_114 = true`, `manifest_max_sma_day = null` (legacy
  manifest without explicit max-SMA-day; inferred MATCH from
  schema).
- CP: `data_tail_date = 2026-05-14`, `benchmark_as_of_date =
  2026-05-22`, `freshness_class = STALE`, `max_sma_class = MATCH`,
  `has_SMA_114 = true`, `manifest_max_sma_day = 114`.

In both cases the runner is reporting a true positive: the PKL's
data series ends before the secondary's benchmark date. Phase C
must either refresh the affected PKLs via the documented
`signal_engine_cache_refresher.py --max-sma-day 114` path, or
operator-authorize a freshness-policy revision.

No deviation in:

- price-cache classification (all 8 OK at `2026-05-22` matches
  PR #301);
- unique required base ticker count (61, matches);
- max-SMA-day classification (every PKL still `MATCH`);
- selected_build.json consumption shape (all 8 consumed exactly);
- canonical safety (no roots modified).

## 11. Findings

### 11.1 Privacy

PASS. Zero token leaks and zero drive-letter pattern hits across all
8 stdout JSON files. The runner's `sanitize_for_json` layer (added in
the PR #303 amendment chain) correctly redacted any absolute path
that would have appeared from real on-disk locations and replaced
`cwd` with the `<PROJECT_ROOT>` placeholder.

### 11.2 selected_build.json consumption

PASS. All 8 `selected_build.json` files were consumed explicitly. No
secondary was refused. No `explicit_build_override`. The runner did
NOT fall back to a latest-by-ctime directory scan.

### 11.3 Unexpected classifications

The only "unexpected vs PR #301" classification is the 38 STALE-GATED
cells / 48 STALE PKLs. Per section 10 this is a true positive caused
by the Phase B amendment's stricter freshness gate, NOT a runner bug
and NOT data drift on disk (the underlying PKLs and price caches are
exactly the post-PR-301 state per the canonical safety check).

### 11.4 Canonical safety

PASS. Pre/post snapshots prove every canonical root unchanged and
the per-secondary `selected_build.json` SHA-256 set is byte-identical.

### 11.5 Runner bugs surfaced by real-data invocation

None. The runner produced valid JSON for all 8 secondaries, applied
sanitization correctly, populated every expected field, and emitted
exit code 0 in dry-run mode.

## 12. Recommendation

**PASS WITH NOTES.** Phase C can proceed with notes.

What Phase C should focus on first:

1. **Decide how the operator wants Phase C to handle the
   STALE-GATED PKL surface.** Two operationally sensible options:
   (a) Authorize a bounded
   `signal_engine_cache_refresher.py --max-sma-day 114` pass over
   the 48 STALE members BEFORE the supervised smoke. This restores
   the PR #301-style "all ELIGIBLE" surface and gives Phase C real
   multi-K compute work across multiple secondaries. (b) Run the
   supervised smoke on the 2 currently-ELIGIBLE cells (GOOGL K=1
   and META K=1) under an isolated `--output-dir`. This validates
   the runner end-to-end without paying the refresh cost yet, but
   at the cost of very thin coverage. **Option (a) is the stronger
   path before a meaningful isolated-output smoke** because it
   matches the Phase A plan's "supervised smoke on 1-2 secondaries
   using isolated output dir" with non-trivial K coverage.

2. **Treat the freshness-gate behavior as a feature, not a bug.**
   Update the Phase A operator-locked v1 defaults note to make
   explicit that Phase C/D will operate against the strict
   freshness gate; this avoids surprise when the supervised smoke
   surfaces STALE PKLs not flagged by PR #301's earlier rule.

3. **Stay strictly within the Phase C contract.** Per the Phase A
   plan, Phase C is the supervised isolated-output smoke. It may
   pass `--write` ONLY after the Phase C runner implementation
   explicitly supports isolated-output writes, and ONLY when
   `--output-dir` points to an isolated noncanonical directory.
   Phase C must NOT write canonical `output/trafficflow/`
   artifacts (that is reserved for a later operator-authorized
   phase). Phase C must NOT issue network fetches unless
   `--allow-network-fetch` is explicitly authorized. Phase C
   compute work must be limited to the supervised smoke target
   cells/secondaries.

## Notes on this evidence task

- This was a dry-run evidence task.
- No `--write` flag was passed.
- No `--refresh-missing-pkls` was passed.
- No `--refresh-stale-prices` was passed.
- No `--allow-network-fetch` was passed.
- No canonical writes were authorized.
- No engines or TrafficFlow compute functions were invoked.
- The runner test suite (46 tests) was re-run before real-data
  invocation and all tests passed.
- Session evidence (per-secondary stdout JSON, stderr logs, pre/post
  snapshots, aggregate analysis) lives under
  `<SESSION_DIR>/runs/`, `<SESSION_DIR>/preflight/`, and
  `<SESSION_DIR>/analysis/` - all gitignored.
- Phase C can responsibly proceed with the notes in section 12.
