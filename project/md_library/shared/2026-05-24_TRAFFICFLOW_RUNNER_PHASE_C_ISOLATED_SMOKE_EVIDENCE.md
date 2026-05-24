# TrafficFlow Runner Phase C Isolated-Output Smoke Evidence (SPY, AAPL)

## 1. Scope and Non-Goals

This is the first supervised real-compute exercise of
`trafficflow_runner.py --write` against canonical inputs. Writes are
directed at isolated per-secondary directories under
`<SESSION_DIR>/isolated_output/`; the canonical `output/trafficflow/`
root remains structurally forbidden.

Scope:

- Secondaries: SPY and AAPL.
- K levels: 1, 2, 3, 4, 6 (10 cells total).
- Mode: `--write` to an isolated output directory.
- Real `trafficflow.build_board_rows` is invoked via the
  lazy-import + pinned `_find_latest_combo_table` wrapper added in
  PR #306 (no mocked compute in this PR).

Non-goals:

- No other 6 secondaries.
- No canonical `output/trafficflow/` writes.
- No `selected_output.json` or downstream MTF / Confluence handoff.
- No runner / code / test changes.
- No PKL refresh.
- No price-cache refresh (no `--refresh-stale-prices` / no
  `--allow-network-fetch` was passed to the runner).

## 2. References

- Phase A scoping doc:
  `md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_EXECUTION_SURFACE.md`
- PR #303 Phase B runner: merged at `f392cd2`.
- PR #304 dry-run evidence:
  `md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_PHASE_B_REAL_DATA_DRY_RUN_EVIDENCE.md`
- PR #305 stale repair + revalidation:
  `md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_STALE_REPAIR_AND_REVALIDATION_EVIDENCE.md`
- PR #306 Phase C isolated-write implementation: merged at
  `14022be` (squashed scaffold + selected-build pinning + provenance
  fix).

## 3. Test Suite Re-Run Confirmation

Before any runner invocation:

```
<PINNED_INTERPRETER> -m pytest test_scripts/test_trafficflow_runner.py -q
-> 63 passed in 2.22s
```

## 4. Pre-Run Canonical Safety Snapshot

Captured at session start. File counts:

| Root | Pre-run file count |
|---|---:|
| `output/stackbuilder/` | 5,388 |
| `output/impactsearch/` | 16 |
| `output/onepass/` | 2 |
| `output/trafficflow/` | absent (does not exist) |
| `output/validation/` | 0 |
| `signal_library/data/stable/` | 71,980 |
| `cache/results/` | 3,267 |
| `cache/status/` | 1,648 |
| `price_cache/daily/` | 12 |

SHA-256 captured for `output/stackbuilder/SPY/selected_build.json`,
`output/stackbuilder/AAPL/selected_build.json`,
`output/onepass/onepass.xlsx`, both per-secondary
`combo_leaderboard.xlsx` files, and the 15-ticker union of required
member PKLs across SPY+AAPL K=1..6 leaderboards. Full snapshot
stored at `<SESSION_DIR>/preflight/pre_run_snapshot.json`.

## 5. Invocation Methodology

Per-secondary command shape:

```
<PINNED_INTERPRETER> trafficflow_runner.py \
  --secondaries <SECONDARY> \
  --k-range 1,2,3,4,6 \
  --stackbuilder-root output/stackbuilder \
  --output-dir <SESSION_DIR>/isolated_output/<SECONDARY> \
  --write
```

Flags deliberately NOT passed:

- `--refresh-missing-pkls`
- `--refresh-stale-prices`
- `--allow-network-fetch`
- `--explicit-build`

Output directories were isolated per secondary so the second
invocation could not overwrite the first secondary's run-level files.
Each invocation captured its stdout to
`<SESSION_DIR>/runs/<SECONDARY>_stdout.json` and stderr to
`<SESSION_DIR>/runs/<SECONDARY>_stderr.log`.

## 6. Per-Secondary Results: SPY

| Field | Value |
|---|---|
| Exit code | 0 |
| Elapsed wall-clock | 14.36 s |
| Status (envelope) | `ok` |
| Write mode (envelope) | `isolated` |
| `write_authorized` | `true` |
| `output_dir_isolated` | `true` |
| `canonical_write_blocked` | `false` |
| Board-row files present (per K) | 5/5 JSON, 5/5 CSV |
| Run-level files present | `run_manifest.json`, `run.stdout.json` both present |

Per-cell timing (from `run_manifest.json` `per_cell_summary`):

| K | Elapsed (s) | PR #301 baseline (s) | Ratio |
|---|---:|---:|---:|
| K1 | 0.83 | 0.11 | 7.5x |
| K2 | 0.75 | 0.32 | 2.3x |
| K3 | 1.00 | 0.66 | 1.5x |
| K4 | 2.01 | 0.83 | 2.4x |
| K6 | 7.10 | 3.39 | 2.1x |

Per-cell row count: 1 row per K (5/5), matching the Phase 6I-79
1-row-per-K leaderboard density.

Artifact-list completeness verified: both on-disk
`run_manifest.json` and `run.stdout.json` list all 5 K JSON, all 5
K CSV, and both run files. `write_summary.artifacts_written_count`
matches each list length.

Selected-build provenance:

- `selected_build_path = output/stackbuilder/SPY/selected_build.json`
  (sanitized repo-relative POSIX).
- `selected_build_sha256` first-16 hex `4e276dac950af65b` matches
  the pre-run snapshot exactly.
- `explicit_build_override = false`.

Privacy sanitization: zero token hits and zero drive-letter pattern
hits across the captured stdout, the on-disk `run_manifest.json`,
and the on-disk `run.stdout.json`.

Atomic-write pattern: zero `.tmp` files remained under
`<SESSION_DIR>/isolated_output/SPY/` after the run.

## 7. Per-Secondary Results: AAPL

| Field | Value |
|---|---|
| Exit code | 0 |
| Elapsed wall-clock | 16.52 s |
| Status (envelope) | `ok` |
| Write mode (envelope) | `isolated` |
| `write_authorized` | `true` |
| `output_dir_isolated` | `true` |
| `canonical_write_blocked` | `false` |
| Board-row files present (per K) | 5/5 JSON, 5/5 CSV |
| Run-level files present | `run_manifest.json`, `run.stdout.json` both present |

Per-cell timing:

| K | Elapsed (s) | PR #301 baseline (s) | Ratio |
|---|---:|---:|---:|
| K1 | 1.05 | 0.17 | 6.2x |
| K2 | 0.57 | 0.37 | 1.5x |
| K3 | 1.05 | 0.78 | 1.3x |
| K4 | 2.46 | 0.87 | 2.8x |
| K6 | 8.68 | 1.94 | 4.5x |

Per-cell row count: 1 row per K (5/5).

Artifact-list completeness verified: both on-disk
`run_manifest.json` and `run.stdout.json` list all 5 K JSON, all 5
K CSV, and both run files. `write_summary.artifacts_written_count`
matches each list length.

Selected-build provenance:

- `selected_build_path = output/stackbuilder/AAPL/selected_build.json`
  (sanitized repo-relative POSIX).
- `selected_build_sha256` first-16 hex `39a970ce74be4331` matches
  the pre-run snapshot exactly.
- `explicit_build_override = false`.

Privacy sanitization: zero token hits and zero drive-letter pattern
hits across all three relevant files.

Atomic-write pattern: zero `.tmp` files remained under
`<SESSION_DIR>/isolated_output/AAPL/` after the run.

## 8. Aggregate Analysis

| Metric | Result |
|---|---:|
| Total wall-clock across both invocations | 30.88 s |
| Cells produced | **10 / 10** (5 per secondary x 2) |
| Board-row artifacts written | **20 / 20** (10 JSON + 10 CSV) |
| Run-level artifacts written | **4 / 4** (2 manifest + 2 stdout) |
| Aggregate row count across cells | 10 (1 row per cell) |
| Selected-build provenance matches | **2 / 2** |
| Privacy leaks across captured + on-disk JSON | **0** |
| `.tmp` files remaining anywhere under `<SESSION_DIR>/isolated_output/` | **0** |
| Cells classifying ELIGIBLE pre-compute | 10 / 10 (matches PR #305 baseline) |
| Cells with non-`ok` status | 0 |

Per-cell elapsed is generally 1.3x - 7.5x of the PR #301 single-cell
benchmark baseline, driven by (a) Phase C isolated artifact writes
(JSON + CSV per cell, manifest + stdout per invocation), (b) per-cell
startup cost being amortized over a smaller number of cells than the
PR #301 sweep, and (c) the runner's overhead around the lazy import,
pinned `_find_latest_combo_table`, and atomic write pattern. The
ratios are within the "WATCH" envelope from the Phase A scoping doc
and do not block forward progress, but they are higher than the bare
PR #301 numbers and worth noting before broader-universe runs.

## 9. Privacy Sanitization Verification

Tokens scanned: the standard six-item denylist defined by the
operator's privacy rule (covering usernames, conda installation
brand, env name, OS user-data directory, OS user-home root, and the
project env name) plus a regex matching a single ASCII letter
followed by a colon and a path separator (the drive-letter prefix
shape).

| File | Token hits | Drive-letter hits |
|---|---:|---:|
| `<SESSION_DIR>/runs/SPY_stdout.json` | 0 | 0 |
| `<SESSION_DIR>/runs/AAPL_stdout.json` | 0 | 0 |
| `<SESSION_DIR>/isolated_output/SPY/run_manifest.json` | 0 | 0 |
| `<SESSION_DIR>/isolated_output/SPY/run.stdout.json` | 0 | 0 |
| `<SESSION_DIR>/isolated_output/AAPL/run_manifest.json` | 0 | 0 |
| `<SESSION_DIR>/isolated_output/AAPL/run.stdout.json` | 0 | 0 |

Sampled SPY and AAPL envelope fields:

- `cwd == "<PROJECT_ROOT>"` (literal placeholder).
- Path fields render as repo-relative POSIX strings such as
  `output/stackbuilder/SPY/selected_build.json`,
  `cache/results/<TICKER>_precomputed_results.pkl`,
  `output/stackbuilder/SPY/<run_dir>/combo_leaderboard.xlsx`.
- `artifacts_written` entries are repo-relative POSIX strings
  rooted at the isolated `<SESSION_DIR>/isolated_output/<SEC>/`
  branch.
- `process_conflict_result.conflicts == []` in both runs; the
  raw-cmdline redaction path was not exercised.

## 10. Post-Run Canonical Safety Check

Compared to the Part 4 pre-run snapshot:

| Root | Pre count | Post count | Unchanged (count + latest mtime) |
|---|---:|---:|---:|
| `output/stackbuilder/` | 5,388 | 5,388 | yes |
| `output/impactsearch/` | 16 | 16 | yes |
| `output/onepass/` | 2 | 2 | yes |
| `output/trafficflow/` | 0 (absent) | 0 (absent) | yes |
| `output/validation/` | 0 | 0 | yes |
| `signal_library/data/stable/` | 71,980 | 71,980 | yes |
| `cache/results/` | 3,267 | 3,267 | yes |
| `cache/status/` | 1,648 | 1,648 | yes |
| `price_cache/daily/` | 12 | 12 | **NO** (file count unchanged but latest mtime moved into the session window) |

SHA-256 checks:

- All 8 per-secondary `selected_build.json` SHA-256s byte-identical
  pre/post (`selected_build_sha_unchanged == True`).
- Both per-secondary `combo_leaderboard.xlsx` SHA-256s
  byte-identical (`combo_leaderboard_sha_unchanged == True`).
- `output/onepass/onepass.xlsx` SHA-256 byte-identical
  (`onepass_xlsx_sha_unchanged == True`).
- All 15 required member PKL SHA-256s byte-identical
  (`pkl_sha_unchanged == True`).

Per-file SHA-256 diff for `price_cache/daily/`:

| File | Pre SHA (first 16) | Post SHA (first 16) | Touched during session |
|---|---|---|---|
| AAPL.csv | (pre, captured) | `29490141806b715c` | **YES** (size 344,941 -> 348,338, +3,397 B) |
| SPY.csv | (pre, captured) | `bbd8f28f3e3c9c83` | **YES** (size 231,432 -> 232,006, +574 B) |
| AMZN/GOOGL/META/MSFT/NVDA/TSLA.csv | (pre) | (post; mtime 2026-05-24T04:19, predates session start 2026-05-24T09:15) | no |
| HD/JNJ/MCD/WMT.csv | (pre) | (post; mtime 2026-05-15, predates session start) | no |

## 11. Findings

### 11.1 CRITICAL: TrafficFlow engine wrote canonical price caches during compute despite `--allow-network-fetch` not being passed

The runner refused to add `would_refresh_prices` entries because
`--refresh-stale-prices` was not passed, and no `--allow-network-fetch`
was supplied. The runner's own refresh helper was therefore never
invoked. However, **`trafficflow.build_board_rows` -> `_load_secondary_prices` -> `_needs_refresh` -> `_fetch_secondary_from_yf` -> `_write_cache_file`** fired anyway during compute because the price-cache TTL freshness check inside the engine is independent of the runner's `--allow-network-fetch` flag.

Affected files:

- `price_cache/daily/SPY.csv`: pre-session SHA differs from post-session
  SHA; size 231,432 -> 232,006 bytes (~+574 B, consistent with appending
  ~1 trading day).
- `price_cache/daily/AAPL.csv`: pre-session SHA differs from
  post-session SHA; size 344,941 -> 348,338 bytes (~+3,397 B,
  consistent with appending several trading days; pre-PR-301 baseline
  was 2026-05-04, then refreshed by PR #301 amendment, but the engine
  re-extended it again here).

This is a **canonical-safety violation of the Phase C contract**.
The contract says "Do not write to ... `price_cache/daily/` ..." and
"No network fetch", and the runner-level surface honored both. The
violation came from the engine internals invoked by `build_board_rows`.

Root cause: TrafficFlow's compute-time price-cache freshness gate
does not consult the runner. The PR #306 wrapper pins
`_find_latest_combo_table` to the selected `combo_leaderboard` path,
but it does NOT block the engine's `_needs_refresh` /
`_fetch_secondary_from_yf` / `_write_cache_file` path.

The compute results themselves remain correct (status `ok` for both
secondaries with 1 row per cell as expected), and only the
`price_cache/daily/{SPY,AAPL}.csv` files were touched (no other
canonical roots changed and no PKLs were rewritten), so the violation
is bounded. The other six PR #305 secondaries' caches were untouched
because they were not invoked in this smoke. But the surface needs
to be closed before broader smoke or any operator-authorized
canonical run.

### 11.2 No privacy leaks

Zero token hits and zero drive-letter pattern hits across all 6
relevant on-session JSON files (captured stdout per secondary + on-disk
manifest + on-disk `run.stdout.json` per secondary). `cwd` is the
literal `<PROJECT_ROOT>` placeholder; path fields are repo-relative
POSIX or sanitized.

### 11.3 No directory-listing fallback during compute

Selected-build provenance is exactly the consumed
`selected_build.json` for both secondaries (verified by SHA-256
match against the pre-run snapshot). The PR #306 wrapper-pinned
`_find_latest_combo_table` path is operating as designed.

### 11.4 No `.tmp` remnants

Zero `.tmp` files remain anywhere under
`<SESSION_DIR>/isolated_output/`. The atomic-write pattern functioned
correctly across all 4 run-level files and all 20 board-row files.

### 11.5 Per-cell timing higher than PR #301 bare benchmark

Per-cell elapsed in the Phase C write path is roughly 1.3x - 7.5x the
PR #301 bare-compute baseline, dominated at lower K (K=1, K=2) by the
constant-cost write overhead and at higher K by the
`_subset_metrics_spymaster_bitmask` pattern from prior evidence. K=6
for AAPL is 8.68 s vs the PR #301 1.94 s baseline (4.5x); SPY K=6 is
7.10 s vs 3.39 s (2.1x). Not a blocker for SPY+AAPL smoke; worth
revisiting at broader-universe scale or with `PARALLEL_SUBSETS=1`
under Phase D measurement.

## 12. Recommendation

**AMENDMENT NEEDED before broader smoke or Phase D.**

The Phase C runner contract is structurally complete EXCEPT for the
price-cache write surface that fires through the engine's compute-time
freshness gate. The fix belongs in the runner (no engine changes
allowed by this PR's contract anyway). Suggested fix shape, for a
future PR:

- During the isolated-write execution path, the runner's wrapper
  should additionally pin `trafficflow._needs_refresh -> False` and /
  or `trafficflow._fetch_secondary_from_yf -> empty-DataFrame`
  whenever `--allow-network-fetch` is NOT set, restoring them in a
  `finally` block alongside the existing
  `_find_latest_combo_table` pin.
- Tests must verify: (i) without `--allow-network-fetch`, the runner
  monkey-patches the engine's price-cache-refresh path so it cannot
  write to `price_cache/daily/`; (ii) with `--allow-network-fetch`,
  the runner restores the engine defaults so network fetch + cache
  write CAN occur.
- The runner's existing `--refresh-stale-prices` reporting path is
  untouched by this gap; it remains correct.

Until that amendment lands:

- **Do not** broaden the smoke to additional secondaries via this
  runner without first refreshing those secondaries' price caches via
  the dedicated `trafficflow.refresh_secondary_caches` helper (or
  the runner's `--refresh-stale-prices` flag) so the engine's
  freshness gate never fires during compute.
- Phase D measurement work should proceed only on a copy of the
  price-cache directory, or with the engine's network path explicitly
  short-circuited.
- The structural canonical-output guardrail
  (`output/trafficflow/` refusal) and the selected-build pinning are
  both functioning correctly and need no change.

Verdict on what THIS smoke proves:

- 10 / 10 cells computed and serialized cleanly.
- 24 / 24 expected artifacts written to the isolated output dir.
- Selected-build provenance is correct (2 / 2).
- Privacy sanitization is correct (0 leaks across 6 files).
- Atomic-write pattern is correct (0 `.tmp` remnants).
- All non-price-cache canonical roots remain untouched
  (`output/stackbuilder/`, `output/impactsearch/`, `output/onepass/`,
  `output/trafficflow/`, `output/validation/`,
  `signal_library/data/stable/`, `cache/results/`, `cache/status/`
  all byte-identical pre/post).
- The price-cache canonical-safety violation is documented above as
  a runner-amendment item for a separate PR.

## Notes on this evidence task

- This was a supervised real-compute smoke against canonical inputs,
  writing to isolated output only.
- `--refresh-missing-pkls`, `--refresh-stale-prices`,
  `--allow-network-fetch`, and `--explicit-build` were NOT passed.
- `output/trafficflow/` remained structurally forbidden (no canonical
  TrafficFlow writes).
- No PKL was rewritten; no PKL refresh helper was invoked.
- No Dash server launched.
- Real `trafficflow.build_board_rows` was invoked through the
  lazy-import + pinned `_find_latest_combo_table` wrapper added in
  PR #306.
- Two canonical price-cache files (`SPY.csv`, `AAPL.csv`) were
  modified by the engine's compute-time freshness gate; this is a
  runner-contract gap documented in section 11.1 and section 12.
- All session evidence
  (`<SESSION_DIR>/preflight/`, `<SESSION_DIR>/runs/`,
  `<SESSION_DIR>/isolated_output/`, `<SESSION_DIR>/analysis/`,
  orchestrator script) lives under `logs/` and is gitignored.
- Broader smoke and Phase D measurement should wait until the
  price-cache write gap is closed at the runner layer.
