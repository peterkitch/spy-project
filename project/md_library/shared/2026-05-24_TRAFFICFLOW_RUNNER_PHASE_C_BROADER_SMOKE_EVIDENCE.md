# TrafficFlow Runner Phase C - Broader Smoke Evidence (AMZN, GOOGL, META, MSFT, NVDA, TSLA)

Session date (UTC): 2026-05-24
Session directory (gitignored): `<SESSION_DIR>` =
`logs/trafficflow_phase_c_broader_smoke/20260524T101004Z/`
Branch: `trafficflow-runner-phase-c-broader-smoke`

This document extends the PR #309 verification pattern to the
remaining six Phase 6I-79 secondaries. The central verification
target is that each secondary's `price_cache/daily/<SEC>.csv` remains
byte-identical (SHA-256, size, mtime) pre/post the `--write` smoke,
under PR #308's engine network/price-cache surface block. SPY.csv and
AAPL.csv are also re-sampled as controls (they should not be touched
at all by these six invocations).

---

## 1. Scope and Non-Goals

In scope:

- Six target secondaries: AMZN, GOOGL, META, MSFT, NVDA, TSLA at
  `K=1,2,3,4,6` each.
- Real `trafficflow.build_board_rows` executed through the PR #306 +
  PR #308 lazy compute loader (selected-build pin +
  network/cache-write surface block).
- Per-secondary isolated output dir under
  `<SESSION_DIR>/isolated_output/<SECONDARY>/`.
- Pre/post canonical safety snapshots covering nine roots plus
  selected-build, combo-leaderboard, onepass, and 47 member PKLs.
- SPY.csv / AAPL.csv re-sampled as canonical safety controls.

Out of scope:

- SPY and AAPL real-compute smoke (already covered by PR #307 / PR
  #309).
- Canonical `output/trafficflow/` writes.
- `selected_output.json` and downstream handoff.
- Phase D RAM / performance instrumentation.
- Any code, test, or runner change.

---

## 2. References

- PR #307 - initial Phase C smoke that surfaced the canonical-safety
  violation in `price_cache/daily/SPY.csv` and
  `price_cache/daily/AAPL.csv`.
- PR #308 - runner amendment that pins the engine
  network/price-cache surface (`_needs_refresh`,
  `_fetch_secondary_from_yf`, `_write_cache_file`, `_persist_cache`)
  when `--allow-network-fetch` is not passed.
- PR #309 - SPY/AAPL re-validation under PR #308's block. PR #309
  established the verification template this doc reuses for the
  remaining six secondaries.
- PR #306 - Phase C isolated-write implementation (lazy compute
  loader, `_find_latest_combo_table` pin, isolated output structure,
  manifest / stdout sidecars).
- PR #302 - Phase A scoping doc.

---

## 3. Test Suite Re-Run Confirmation

Command shape:

    <PINNED_INTERPRETER> -m pytest test_scripts/test_trafficflow_runner.py -q

Result: `68 passed in 2.46s` (post-PR-#308 expected suite size).

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

Selected-build SHA-256 captured for all six target secondaries.
Combo-leaderboard SHA-256 captured for each target. Member PKL SHAs
captured across the union of K=1,2,3,4,6 members (47 unique members
across the six target secondaries).

Central verification targets (full meta captured):

| File                               | Size (B) | SHA-256 first 16   |
|------------------------------------|----------|--------------------|
| `price_cache/daily/AMZN.csv`       | 220020   | `d531dc0c20012b1c`  |
| `price_cache/daily/GOOGL.csv`      | 165856   | `6a4d020dd803fc81`  |
| `price_cache/daily/META.csv`       | 104771   | `7e7756f2f883fca3`  |
| `price_cache/daily/MSFT.csv`       | 287879   | `522086fcceb36df8`  |
| `price_cache/daily/NVDA.csv`       | 212621   | `16daaa88f3768187`  |
| `price_cache/daily/TSLA.csv`       | 121445   | `4778b43bc7f76035`  |

Control samples (carried over from PR #309 post-state):

| File                               | Size (B) | SHA-256 first 16   |
|------------------------------------|----------|--------------------|
| `price_cache/daily/SPY.csv`        | 232006   | `bbd8f28f3e3c9c83`  |
| `price_cache/daily/AAPL.csv`       | 348338   | `29490141806b715c`  |

---

## 5. Invocation Methodology

Exact command shape (placeholders) per secondary:

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

Invocation order: AMZN, GOOGL, META, MSFT, NVDA, TSLA (sequential).
stdout per secondary captured to
`<SESSION_DIR>/runs/<SECONDARY>_stdout.json`; stderr to
`<SESSION_DIR>/runs/<SECONDARY>_stderr.log`.

---

## 6. Per-Secondary Results

For every secondary listed: exit=0, status=ok, `write_mode=isolated`,
`effective_config.write_authorized=true`,
`effective_config.output_dir_isolated=true`,
`effective_config.canonical_write_blocked=false`,
`effective_config.allow_network_fetch=false`,
`write_summary.artifacts_written_count=12`, board-row files 5/5 JSON +
5/5 CSV, `run_manifest.json` + `run.stdout.json` present, artifact
list complete (both files list themselves and all 10 board files),
manifest count matches array length, selected-build provenance matches
the pre-snapshot SHA, `explicit_build_override=false`, zero privacy
hits across captured stdout / on-disk manifest / on-disk stdout, zero
`.tmp` residue, per-cell JSON row count = 1 and CSV row count = 1 per
cell.

### 6.1 AMZN

| Field          | Value |
|----------------|-------|
| exit code      | 0 |
| elapsed        | 7.39 s |
| `selected_build_sha256` | `ef97daeefebd7eabf55d6f11eb6f64fd92f206493303b862b150164d81e9b462` |
| `selected_build_path` (sanitized) | `output/stackbuilder/AMZN/selected_build.json` |
| per-cell elapsed | K1=0.39, K2=0.78, K3=0.61, K4=0.83, K6=1.88 |

### 6.2 GOOGL

| Field          | Value |
|----------------|-------|
| exit code      | 0 |
| elapsed        | 6.98 s |
| `selected_build_sha256` | `b179c0b674918707c76ea37c3262ddd4e965cbd632c66f62df4d2efd96f6d404` |
| `selected_build_path` (sanitized) | `output/stackbuilder/GOOGL/selected_build.json` |
| per-cell elapsed | K1=0.31, K2=0.46, K3=0.57, K4=0.71, K6=2.34 |

### 6.3 META

| Field          | Value |
|----------------|-------|
| exit code      | 0 |
| elapsed        | 8.97 s |
| `selected_build_sha256` | `146fa13e46dffc1fd850870a762c9121bea99f3c55287e0474075ee362485417` |
| `selected_build_path` (sanitized) | `output/stackbuilder/META/selected_build.json` |
| per-cell elapsed | K1=0.18, K2=0.33, K3=0.52, K4=1.18, K6=3.85 |

### 6.4 MSFT

| Field          | Value |
|----------------|-------|
| exit code      | 0 |
| elapsed        | 14.99 s |
| `selected_build_sha256` | `60124b653afbfb6b0d34b627e2f372e08497714f474f69ac0ed5d54620ddf5b0` |
| `selected_build_path` (sanitized) | `output/stackbuilder/MSFT/selected_build.json` |
| per-cell elapsed | K1=0.46, K2=0.59, K3=1.23, K4=1.66, K6=8.30 |

### 6.5 NVDA

| Field          | Value |
|----------------|-------|
| exit code      | 0 |
| elapsed        | 7.39 s |
| `selected_build_sha256` | `bea79cac6e8c12fa83a5e90b74e7594579c89e6ca4194364af1221854b00323b` |
| `selected_build_path` (sanitized) | `output/stackbuilder/NVDA/selected_build.json` |
| per-cell elapsed | K1=0.28, K2=0.40, K3=0.42, K4=1.02, K6=2.64 |

(The ticker symbol NVDA is allowed under the privacy rule; it is not
the denylist token.)

### 6.6 TSLA

| Field          | Value |
|----------------|-------|
| exit code      | 0 |
| elapsed        | 5.46 s |
| `selected_build_sha256` | `c365fa2d32ee6a44f7c0788c349e9752e83ead707950b01519c1f0d90e7f18f8` |
| `selected_build_path` (sanitized) | `output/stackbuilder/TSLA/selected_build.json` |
| per-cell elapsed | K1=0.36, K2=0.38, K3=0.45, K4=0.55, K6=1.09 |

Privacy sanitization: zero denylist-token hits and zero drive-letter
pattern matches across all 18 scanned JSON files (6 captured stdout,
6 on-disk `run_manifest.json`, 6 on-disk `run.stdout.json`). Categories
checked: username / conda / drive-path denylist plus a case-sensitive
drive-letter regular expression.

Atomic write pattern: zero `.tmp` residue under any of the six
isolated output directories.

Per-cell content sanity: every `board_rows_k=<K>.json` is valid JSON
with 1 row; every `board_rows_k=<K>.csv` parses as CSV with 1 data row.
JSON and CSV row counts match cell-for-cell across all 30 cells.

Per-cell classification: every cell completed successfully; the
manifest does not emit STALE-GATED, PKL-GATED, MAX-SMA-GATED,
DATA-GATED, REFUSED, or ERROR classifications for any of the 30 cells.

---

## 7. Aggregate Analysis

| Metric                                            | Value |
|---------------------------------------------------|-------|
| Total wall-clock across the 6 invocations         | 51.18 s |
| Per-secondary elapsed                             | AMZN 7.39, GOOGL 6.98, META 8.97, MSFT 14.99, NVDA 7.39, TSLA 5.46 |
| Cells produced                                    | 30 / 30 |
| Board-row files written (JSON+CSV)                | 60 / 60 |
| Run-level files written                           | 12 / 12 |
| Aggregate rows across 30 cells                    | 30 |
| Selected-build provenance matches                 | 6 / 6 |
| Privacy leaks                                     | 0 |
| `.tmp` residue                                    | 0 |
| Canonical artifact modifications                  | 0 |

Per-cell elapsed (overall, n=30): min=0.18 s, median=0.58 s,
max=8.30 s.

Per-cell elapsed by K (n=6 per K):

| K | min (s) | median (s) | max (s) |
|---|---------|------------|---------|
| 1 | 0.18    | 0.33       | 0.46    |
| 2 | 0.33    | 0.43       | 0.78    |
| 3 | 0.42    | 0.55       | 1.23    |
| 4 | 0.55    | 0.92       | 1.66    |
| 6 | 1.09    | 2.49       | 8.30    |

---

## 8. Critical Verification: `price_cache/daily/` Modification Check

Central PR #308 verification targets:

| File                              | Pre SHA (16 hex) | Post SHA (16 hex) | Size pre -> post | mtime unchanged |
|-----------------------------------|--------------------|---------------------|--------------------|------------------|
| `price_cache/daily/AMZN.csv`      | `d531dc0c20012b1c`  | `d531dc0c20012b1c`   | 220020 -> 220020   | yes              |
| `price_cache/daily/GOOGL.csv`     | `6a4d020dd803fc81`  | `6a4d020dd803fc81`   | 165856 -> 165856   | yes              |
| `price_cache/daily/META.csv`      | `7e7756f2f883fca3`  | `7e7756f2f883fca3`   | 104771 -> 104771   | yes              |
| `price_cache/daily/MSFT.csv`      | `522086fcceb36df8`  | `522086fcceb36df8`   | 287879 -> 287879   | yes              |
| `price_cache/daily/NVDA.csv`      | `16daaa88f3768187`  | `16daaa88f3768187`   | 212621 -> 212621   | yes              |
| `price_cache/daily/TSLA.csv`      | `4778b43bc7f76035`  | `4778b43bc7f76035`   | 121445 -> 121445   | yes              |

Control samples (re-checked):

| File                              | Pre SHA (16 hex) | Post SHA (16 hex) | Size pre -> post | mtime unchanged |
|-----------------------------------|--------------------|---------------------|--------------------|------------------|
| `price_cache/daily/SPY.csv`       | `bbd8f28f3e3c9c83`  | `bbd8f28f3e3c9c83`   | 232006 -> 232006   | yes              |
| `price_cache/daily/AAPL.csv`      | `29490141806b715c`  | `29490141806b715c`   | 348338 -> 348338   | yes              |

SHA-256, size, AND mtime are unchanged for all eight files. PR #308's
engine network/price-cache block HOLDS across the broader Phase 6I-79
secondary surface.

Verdict: PR #308 fix HOLDS.

---

## 9. Full Canonical Safety Check

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

- All 6 target `selected_build.json` files: unchanged.
- All 6 target `combo_leaderboard.xlsx` files: unchanged.
- `output/onepass/onepass.xlsx`: unchanged.
- All 47 member PKLs (union across the six target secondaries):
  unchanged.

Latest-mtime also unchanged for every root listed above.

---

## 10. Comparison to PR #309

PR #309 verified SPY and AAPL under PR #308's block. This task
verifies the remaining six. Combined, all 8 Phase 6I-79 secondaries
now have clean Phase C isolated-output evidence under PR #308.

| Metric                          | PR #309 (SPY+AAPL)         | This run (6 secondaries)             |
|---------------------------------|----------------------------|--------------------------------------|
| Secondaries covered             | 2                          | 6                                    |
| Cells produced                  | 10 / 10                    | 30 / 30                              |
| Board-row files written         | 20                         | 60                                   |
| Run-level files                 | 4                          | 12                                   |
| Total elapsed                   | 30.30 s                    | 51.18 s                              |
| Per-secondary elapsed (range)   | 14.15 - 16.15 s            | 5.46 - 14.99 s                       |
| Target price-cache CSV deltas   | 0 / 2 modified             | 0 / 6 modified                       |
| Control price-cache CSV deltas  | n/a (targets were SPY/AAPL) | 0 / 2 modified (SPY/AAPL controls)   |
| Provenance matches              | 2 / 2                      | 6 / 6                                |
| Privacy leaks                   | 0                          | 0                                    |
| `.tmp` residue                  | 0                          | 0                                    |
| Verdict                         | PASS                       | PASS                                 |

Runner output behavior is consistent: same exit codes, statuses,
write_mode shape, manifest schema, and provenance fields. Canonical
safety behavior is consistent: zero `price_cache/daily/<SEC>.csv`
modifications across all eight Phase 6I-79 secondaries when PR #308's
surface block is in effect.

Per-secondary timing variance is expected and reflects member density
under each secondary's combo leaderboard (e.g. MSFT's K=6 cell at
8.30 s vs TSLA's K=6 cell at 1.09 s).

---

## 11. Privacy Sanitization Verification

Scope of scan: per-secondary captured stdout, per-secondary on-disk
`run_manifest.json`, per-secondary on-disk `run.stdout.json` (18 files
total), this evidence doc, the intended commit message, the intended
PR body, and the final report.

Categories scanned: username / conda-path / drive-path denylist (per
CLAUDE.md privacy rule) and a case-sensitive drive-letter regular
expression.

Per-file results: zero token hits and zero drive-letter pattern
matches across every scanned artifact.

The ticker symbol NVDA appears in this doc and in the on-disk runner
artifacts; per the task instructions, NVDA is a permitted ticker
symbol and is not the denylist token.

---

## 12. Findings

12.1 No cells errored. 30 / 30 succeeded with `status=ok`.

12.2 No privacy leaks across the 18 scanned runner artifacts or the
text artifacts (evidence doc, commit message, PR body, final report).

12.3 No canonical safety violations. All six target
`price_cache/daily/<SEC>.csv` files are byte-identical pre/post; both
control CSVs (SPY/AAPL) are byte-identical pre/post; all other tracked
canonical roots are file-count- and latest-mtime-unchanged; every
SHA-256 sampled (6 `selected_build.json`, 6 `combo_leaderboard.xlsx`,
`onepass.xlsx`, 47 member PKLs) is unchanged.

12.4 No provenance mismatches. All six manifest entries report
`selected_build_sha256` byte-for-byte matching the pre-snapshot, with
`explicit_build_override=false`.

12.5 Behavioral note (informational, not a finding): several per-cell
elapsed values fall below the lower end of the broad expected ranges
given in the task spec (e.g. META K=1 at 0.18 s vs the 0.30 s lower
bound, several K=2 cells just below 0.50 s, several K=3 cells below
0.70 s). This is consistent with PR #308's design: with the surface
block active, `_needs_refresh` short-circuits to `False` and
`_fetch_secondary_from_yf` returns an empty `DataFrame` during compute,
removing the per-secondary refresh-check overhead. The same effect was
observed in PR #309 (SPY K=1 0.83 -> 0.30 s, AAPL K=1 1.05 -> 0.45 s).
Upper-bound timings (MSFT K=6 at 8.30 s) sit comfortably within the
expected 5.0 - 15.0 s K=6 range. No cell exceeded any range.

12.6 No `.tmp` residue under any isolated output directory.

---

## 13. Recommendation

PASS.

PR #308's engine network/price-cache surface block is verified
end-to-end against real canonical inputs for the remaining six
Phase 6I-79 secondaries (AMZN, GOOGL, META, MSFT, NVDA, TSLA).
Combined with PR #309's SPY/AAPL verification, the full 8-secondary
Phase 6I-79 surface now has clean Phase C isolated-output evidence
under PR #308's protection. All eight `price_cache/daily/<SEC>.csv`
files are byte-identical pre/post their respective smokes, and no
canonical artifact (`selected_build.json`, `combo_leaderboard.xlsx`,
`onepass.xlsx`, member PKLs) has been modified at any point.

Proposed next step: Phase D - RAM and per-cell performance
measurement under the same isolated-output write mode. Phase D should
remain non-canonical (still writing only to a `<SESSION_DIR>` isolated
output dir, no canonical `output/trafficflow/` writes) and should
reuse the per-secondary invocation pattern established in PR #306 /
PR #309 / this PR. Only after Phase D measurement is reviewed should
Phase E (canonical write to `output/trafficflow/`) be designed.

---

This broader Phase C smoke covers the remaining six Phase 6I-79
secondaries (AMZN, GOOGL, META, MSFT, NVDA, TSLA) after PR #309
verified SPY and AAPL. The central verification target was each
target secondary's `price_cache/daily/<SEC>.csv` remaining byte-
identical pre/post. `--refresh-*` and `--allow-network-fetch` flags
were NOT passed. Real `trafficflow.build_board_rows` was invoked
through the lazy-import + pinned wrapper from PR #306 + PR #308. All
session evidence under `<SESSION_DIR>` is gitignored. The full
8-secondary Phase C surface is now verified, and Phase D can
responsibly begin.
