# TrafficFlow Runner Phase C - Network-Block Re-Validation Evidence

Session date (UTC): 2026-05-24
Session directory (gitignored): `<SESSION_DIR>` =
`logs/trafficflow_phase_c_network_block_revalidation/20260524T094935Z/`
Branch: `trafficflow-runner-phase-c-network-block-revalidation`

This document re-runs PR #307's SPY/AAPL Phase C isolated-output smoke
to verify, against real canonical inputs, that PR #308's engine
network/price-cache surface block prevents the compute-time
`price_cache/daily/` modification that PR #307 surfaced. The central
verification target is that `price_cache/daily/SPY.csv` and
`price_cache/daily/AAPL.csv` remain byte-identical pre/post.

---

## 1. Scope and Non-Goals

In scope:

- Re-execute the Phase C isolated-output smoke for SPY and AAPL with
  `K=1,2,3,4,6` using the same logical command shape PR #307 used.
- Invoke `trafficflow_runner.py` in `--write` mode with per-secondary
  isolated output directories under `<SESSION_DIR>/isolated_output/`.
- Real `trafficflow.build_board_rows` is exercised through the lazy
  compute loader. PR #306 pins `_find_latest_combo_table`; PR #308
  additionally pins `_needs_refresh`, `_fetch_secondary_from_yf`,
  `_write_cache_file`, and `_persist_cache` because
  `--allow-network-fetch` is not passed.
- Central verification: SHA-256 / size / mtime of
  `price_cache/daily/SPY.csv` and `price_cache/daily/AAPL.csv` pre vs
  post.
- Full canonical safety check across `output/stackbuilder/`,
  `output/impactsearch/`, `output/onepass/`, `output/trafficflow/`,
  `output/validation/`, `signal_library/data/stable/`,
  `cache/results/`, `cache/status/`, `price_cache/daily/`.

Out of scope:

- The other six secondaries.
- Canonical `output/trafficflow/` writes (Phase C is structurally
  forbidden from that path).
- `selected_output.json` and downstream handoff.
- Phase D RAM / performance instrumentation.
- Any code, test, or runner change.

---

## 2. References

- PR #307 - initial Phase C smoke evidence
  (`md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_PHASE_C_ISOLATED_SMOKE_EVIDENCE.md`).
- PR #308 - runner amendment that pins the engine
  network/price-cache surface when `--allow-network-fetch` is not
  passed (merge commit on `main`: TrafficFlow runner: block engine
  network/price-cache writes when network not authorized).
- PR #306 - Phase C isolated-write implementation (lazy compute
  loader, `_find_latest_combo_table` pin, isolated output structure,
  manifest / stdout sidecars).
- PR #302 - Phase A scoping doc
  (`md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_EXECUTION_SURFACE.md`).
- Phase B real-data dry-run evidence (PR #304) and stale-PKL repair
  evidence (PR #305).

---

## 3. Test Suite Re-Run Confirmation

Command shape:

    <PINNED_INTERPRETER> -m pytest test_scripts/test_trafficflow_runner.py -q

Result: `68 passed in 2.48s`. Matches the expected post-PR-#308 suite
size.

---

## 4. Pre-Run Canonical Safety Snapshot

Captured to `<SESSION_DIR>/preflight/pre_run_snapshot.json`.

Root file counts:

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

Selected-build SHA-256 (first 16 hex) and combo leaderboard SHA-256
(first 16 hex) captured per target secondary. Member PKL SHAs captured
across the union of K=1,2,3,4,6 members for SPY and AAPL (15 unique
members).

Central verification target (full meta captured):

| File                                   | Size (B) | SHA-256 first 16  |
|----------------------------------------|----------|-------------------|
| `price_cache/daily/SPY.csv`            | 232006   | `bbd8f28f3e3c9c83` |
| `price_cache/daily/AAPL.csv`           | 348338   | `29490141806b715c` |

Note: these are the post-PR-#307 sizes. PR #307 documented that its
smoke modified these files by +574 bytes (SPY) and +3,397 bytes (AAPL)
relative to the pre-PR-#307 state. The current task verifies whether
the engine's compute-time refresh path further modifies them now that
PR #308's surface block is in place.

---

## 5. Invocation Methodology

Exact command shape (with placeholders) per secondary:

    <PINNED_INTERPRETER> trafficflow_runner.py \
        --secondaries <SECONDARY> \
        --k-range 1,2,3,4,6 \
        --stackbuilder-root output/stackbuilder \
        --output-dir <SESSION_DIR>/isolated_output/<SECONDARY> \
        --write

Flags explicitly NOT passed:

- `--refresh-missing-pkls`
- `--refresh-stale-prices`
- `--allow-network-fetch`
- `--explicit-build`

Invocation order: SPY first, then AAPL.

stdout captured to `<SESSION_DIR>/runs/<SECONDARY>_stdout.json` and
stderr captured to `<SESSION_DIR>/runs/<SECONDARY>_stderr.log`.

---

## 6. Per-Secondary Results: SPY

| Field                                       | Value |
|---------------------------------------------|-------|
| exit code                                   | 0 |
| elapsed wall-clock                          | 14.15 s |
| `status`                                    | `ok` |
| `write_mode`                                | `isolated` |
| `effective_config.write_authorized`         | `true` |
| `effective_config.output_dir_isolated`      | `true` |
| `effective_config.canonical_write_blocked`  | `false` |
| `effective_config.allow_network_fetch`      | `false` |
| `write_summary.artifacts_written_count`     | 12 |

Artifact existence under
`<SESSION_DIR>/isolated_output/SPY/SPY/`:

- `board_rows_k=1.json`, `board_rows_k=2.json`, `board_rows_k=3.json`,
  `board_rows_k=4.json`, `board_rows_k=6.json` - all present (5/5).
- `board_rows_k=1.csv`, `board_rows_k=2.csv`, `board_rows_k=3.csv`,
  `board_rows_k=4.csv`, `board_rows_k=6.csv` - all present (5/5).

Artifact existence under `<SESSION_DIR>/isolated_output/SPY/`:

- `run_manifest.json` - present.
- `run.stdout.json` - present.

Artifact list completeness: both `run_manifest.json` and
`run.stdout.json` list themselves and all 10 board-row files (10 +
2 = 12). `write_summary.artifacts_written_count` (12) matches the
length of the `artifacts_written` array in both files.

Selected-build provenance:

- `canonical_artifacts_referenced[0].selected_build_path` =
  `output/stackbuilder/SPY/selected_build.json` (repo-relative POSIX,
  sanitized).
- `canonical_artifacts_referenced[0].selected_build_sha256` =
  `4e276dac950af65b29a293269f0030412693f5b59ef11bbcc6e5ce2f962a97ea`.
- Matches pre-snapshot `selected_build_sha256` byte-for-byte.
- `explicit_build_override` = `false`.

Privacy sanitization: zero hits on the username / conda / drive-path
denylist categories and zero drive-letter pattern matches across the
captured stdout, on-disk `run_manifest.json`, and on-disk
`run.stdout.json`.

Atomic write pattern: zero `.tmp` files remain under the SPY isolated
output directory.

Per-cell timing vs PR #307 baseline:

| K | This run (s) | PR #307 (s) | Delta |
|---|--------------|-------------|-------|
| 1 | 0.30         | 0.83        | -0.53 |
| 2 | 0.75         | 0.75        |  0.00 |
| 3 | 1.02         | 1.00        | +0.02 |
| 4 | 2.06         | 2.01        | +0.05 |
| 6 | 7.29         | 7.10        | +0.19 |

K=1 dropped substantially because PR #308's surface block short-
circuits `_needs_refresh` to `False`, eliminating the compute-time
refresh-check path that previously fired on K=1. K=2..6 are within
noise.

Per-cell JSON row count: 1 per cell (5 total). CSV row counts match
JSON row counts cell-for-cell.

---

## 7. Per-Secondary Results: AAPL

| Field                                       | Value |
|---------------------------------------------|-------|
| exit code                                   | 0 |
| elapsed wall-clock                          | 16.15 s |
| `status`                                    | `ok` |
| `write_mode`                                | `isolated` |
| `effective_config.write_authorized`         | `true` |
| `effective_config.output_dir_isolated`      | `true` |
| `effective_config.canonical_write_blocked`  | `false` |
| `effective_config.allow_network_fetch`      | `false` |
| `write_summary.artifacts_written_count`     | 12 |

Artifact existence under
`<SESSION_DIR>/isolated_output/AAPL/AAPL/`:

- 5 `board_rows_k=N.json` and 5 `board_rows_k=N.csv` (10/10).

Artifact existence under `<SESSION_DIR>/isolated_output/AAPL/`:

- `run_manifest.json` - present.
- `run.stdout.json` - present.

Artifact list completeness: both files list themselves and all 10
board-row files. `write_summary.artifacts_written_count` (12) matches
the length of the `artifacts_written` array in both files.

Selected-build provenance:

- `selected_build_path` =
  `output/stackbuilder/AAPL/selected_build.json` (repo-relative POSIX,
  sanitized).
- `selected_build_sha256` =
  `39a970ce74be4331ddf55df197c400a956cc1fd71ad4076d61a54803633599e3`.
- Matches pre-snapshot `selected_build_sha256` byte-for-byte.
- `explicit_build_override` = `false`.

Privacy sanitization: zero leak-token hits, zero drive-letter pattern
matches across captured stdout, on-disk manifest, and on-disk stdout
sidecar.

Atomic write pattern: zero `.tmp` files remain.

Per-cell timing vs PR #307 baseline:

| K | This run (s) | PR #307 (s) | Delta |
|---|--------------|-------------|-------|
| 1 | 0.45         | 1.05        | -0.60 |
| 2 | 0.58         | 0.57        | +0.01 |
| 3 | 1.06         | 1.05        | +0.01 |
| 4 | 2.47         | 2.46        | +0.01 |
| 6 | 8.81         | 8.68        | +0.13 |

Same K=1 acceleration pattern. K=2..6 within noise.

Per-cell JSON row count: 1 per cell. CSV row counts match.

---

## 8. Aggregate Analysis

| Metric                                            | Value |
|---------------------------------------------------|-------|
| Total wall-clock across both invocations          | 30.30 s |
| SPY elapsed                                       | 14.15 s |
| AAPL elapsed                                      | 16.15 s |
| Cells produced                                    | 10 / 10 |
| Board-row files written (JSON+CSV)                | 20 / 20 |
| Run-level files written                           | 4 / 4 |
| Aggregate rows across all 10 cells                | 10 |
| Selected-build provenance matches                 | 2 / 2 |
| Privacy leaks                                     | 0 |
| `.tmp` residue                                    | 0 |
| Canonical artifact modifications                  | 0 |

---

## 9. Critical Verification: `price_cache/daily/` Modification Check

Central PR #308 verification target.

| File                              | Pre SHA-256 (16 hex) | Post SHA-256 (16 hex) | Size (B) pre -> post | mtime unchanged |
|-----------------------------------|----------------------|-----------------------|----------------------|-----------------|
| `price_cache/daily/SPY.csv`       | `bbd8f28f3e3c9c83`    | `bbd8f28f3e3c9c83`     | 232006 -> 232006     | yes             |
| `price_cache/daily/AAPL.csv`      | `29490141806b715c`    | `29490141806b715c`     | 348338 -> 348338     | yes             |

SHA-256, size, AND mtime are unchanged for both files. PR #308's
engine network/price-cache block HOLDS against the same runner
invocation shape that PR #307 used.

Verdict: PR #308 fix HOLDS.

---

## 10. Full Canonical Safety Check

File-count and latest-mtime comparison pre vs post:

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

Per-file SHA-256 comparison:

- `output/stackbuilder/SPY/selected_build.json`: unchanged.
- `output/stackbuilder/AAPL/selected_build.json`: unchanged.
- `output/stackbuilder/SPY/.../combo_leaderboard.xlsx`: unchanged.
- `output/stackbuilder/AAPL/.../combo_leaderboard.xlsx`: unchanged.
- `output/onepass/onepass.xlsx`: unchanged.
- All 15 member PKLs (union of K=1..6 members across SPY and AAPL):
  unchanged.

Latest-mtime also unchanged for every root listed above.

---

## 11. Comparison to PR #307

| Aspect                                  | PR #307                                | This run                          |
|-----------------------------------------|----------------------------------------|-----------------------------------|
| SPY exit / status                       | 0 / ok                                 | 0 / ok                            |
| AAPL exit / status                      | 0 / ok                                 | 0 / ok                            |
| SPY elapsed                             | 14.36 s                                | 14.15 s                           |
| AAPL elapsed                            | 16.52 s                                | 16.15 s                           |
| Cells produced                          | 10 / 10                                | 10 / 10                           |
| Board-row files written                 | 20                                     | 20                                |
| Run-level files                         | 4                                      | 4                                 |
| `price_cache/daily/SPY.csv`             | modified by +574 bytes                 | byte-identical pre/post           |
| `price_cache/daily/AAPL.csv`            | modified by +3,397 bytes               | byte-identical pre/post           |
| Other canonical artifacts               | unchanged                              | unchanged                         |
| Privacy leaks                           | 0                                      | 0                                 |

Runner output behavior: still correct. Same exit codes, same status,
same artifact counts, comparable wall-clock (slightly faster in
aggregate because the engine's compute-time refresh-check is now
short-circuited).

Canonical safety behavior: now correct. The two files PR #307
modified are byte-identical pre/post in this re-run.

---

## 12. Privacy Sanitization Verification

Scope of scan: captured stdout per secondary, on-disk
`run_manifest.json` per secondary, on-disk `run.stdout.json` per
secondary, and this evidence doc.

Categories scanned: username / conda-path / drive-path denylist (per
CLAUDE.md privacy rule) and a case-sensitive drive-letter regular
expression.

Per-file results: zero token hits and zero drive-letter pattern
matches across every scanned artifact.

This evidence doc, the intended commit message, and the intended PR
body were each scanned with the same denylist and pattern. All passed.

---

## 13. Findings

13.1 No cells errored. SPY 5/5 and AAPL 5/5 succeeded.

13.2 No privacy leaks.

13.3 No canonical safety violations. The two files PR #307 modified
are byte-identical pre/post; all other tracked canonical roots are
file-count- and latest-mtime-unchanged; every SHA-256 sampled
(selected_build.json, combo_leaderboard.xlsx, onepass.xlsx, 15 member
PKLs) is unchanged.

13.4 No provenance mismatches. Both secondaries report
`selected_build_sha256` byte-for-byte matching the pre-snapshot, with
`explicit_build_override=false`.

13.5 Behavioral note (informational, not a finding): K=1 wall-clock
dropped from 0.83 s to 0.30 s for SPY and from 1.05 s to 0.45 s for
AAPL. This is consistent with PR #308's design: with the surface
block in place, `_needs_refresh` short-circuits to `False` and
`_fetch_secondary_from_yf` returns an empty `DataFrame` during compute,
removing the per-secondary refresh-check that PR #307 went through on
the first K cell. K=2..6 timings are within noise.

13.6 No `.tmp` residue under either isolated output directory.

---

## 14. Recommendation

PASS.

PR #308 fix is verified end-to-end against real canonical inputs for
SPY and AAPL. The runner's `--write` mode is now canonical-safe for
the documented invocation shape when `--allow-network-fetch` is not
passed.

Proposed next step: broaden the Phase C smoke to the remaining six
secondaries one at a time, using the same invocation shape and the
same pre/post canonical safety snapshot methodology. The six
remaining secondaries should be selected from the operator-curated
master ticker list and the cross-checked against current StackBuilder
selected_build availability. Each invocation should produce its own
pre/post snapshot for `price_cache/daily/<SEC>.csv` to confirm the
network block holds beyond the two files exercised here.

After broader smoke success, the next phase is Phase D measurement
(per-cell RAM/performance instrumentation) and only then Phase E
(canonical write), with no implementation work begun on either until
this re-validation is reviewed.

---

This re-verification of PR #307's Phase C smoke after PR #308's
network-block fix demonstrates that the central verification target -
`price_cache/daily/SPY.csv` and `price_cache/daily/AAPL.csv` remaining
byte-identical pre/post - is satisfied. `--refresh-*` and
`--allow-network-fetch` flags were NOT passed. Real
`trafficflow.build_board_rows` was invoked through the lazy-import +
pinned wrapper from PR #306 + PR #308. All session evidence under
`<SESSION_DIR>` is gitignored. Phase C can now responsibly broaden to
the remaining six secondaries.
