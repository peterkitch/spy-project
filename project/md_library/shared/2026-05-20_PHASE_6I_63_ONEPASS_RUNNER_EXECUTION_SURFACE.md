# Phase 6I-63 - OnePass workbook execution surface audit + runner scoping

**Date:** 2026-05-20
**Scope:** read-only Phase A scoping for future `onepass_workbook_runner.py`. No code modified, no engines run, no commit made.
**Candidate path:** `md_library/shared/2026-05-20_PHASE_6I_63_ONEPASS_RUNNER_EXECUTION_SURFACE.md`

## Pre-check

> Actual local paths live in project/CLAUDE.md and are not committed to tracked docs.

- cwd: `<PROJECT_ROOT>`
- repo root: `<REPO_ROOT>`
- branch: `main`
- HEAD: `6719fad245dcf37f6a1b60fb8ce60a81ec5c4606`
- git status: `## main...origin/main` clean
- pinned interpreter from `CLAUDE.md`: `<PINNED_INTERPRETER>`

---

## 1. Focused audit of recent `onepass.py` changes

Audit window: commits touching `onepass.py` since 2026-05-01.

Current `onepass.py` line count is 3,686. The pre-window parent (`7406886^`) had 3,049 lines. Net delta across the six audited commits is **+637 lines / +20.9%**. This is material, but smaller than the ImpactSearch growth that exposed the manifest-verify bottleneck.

| Commit | Date | onepass.py delta | Functional change | Touched sensitive areas | Perf risk | Correctness risk | Verdict |
|---|---:|---:|---|---|---|---|---|
| `7406886` | 2026-05-01 | +62 / -151 | Removed Adj Close basis path, forced raw `Close`, imported `canonical_scoring.score_captures`, delegated `_metrics_from_ccc` and `calculate_metrics_from_signals` scoring to canonical helper. | import-time setup, `save_signal_library`, `_coerce_to_close_frame`, `_metrics_from_ccc`, `calculate_metrics_from_signals`, per-ticker loop price-basis enforcement. Current evidence: `onepass.py:11`, `1944`, `1964`, `1995`, `2072`, `2237-2910`. | maybe | medium | Verify during pilot |
| `0768355` | 2026-05-01 | +24 / -13 | Anchored `logs/onepass.log` to `Path(__file__).parent / "logs"` and standardized buy/short sentinel pairs. | import-time logging setup, `perform_incremental_update`, full rebuild loop. Current evidence: `onepass.py:84-97`, `915-940`, `2741-2794`. | no | low/medium | Trust, verify row parity |
| `667ce6d` | 2026-05-03 | +103 / -3 | Added provenance manifest attach/refresh/verify on signal-library writes, metadata repair writes, and library loads. | import-time provenance imports, `_ensure_signal_alignment_and_persist`, `_persist_library_metadata`, `save_signal_library`, `load_signal_library`. Current evidence: `onepass.py:22-29`, `1154-1310`, `1348-1534`. | yes | medium | Performance risk; verify during pilot |
| `838009f` | 2026-05-03 | +49 / -32 | Replaced direct pickle load + `verify_manifest` with `provenance_manifest.load_verified_signal_library`; loader has content-hash cache keyed by path/mtime/size. | `load_signal_library`. Current evidence: `onepass.py:1348-1534`; cache evidence: `provenance_manifest.py:99-122`, `183-211`, `1036`. | yes | medium | Performance risk; verify during pilot |
| `8081f73` | 2026-05-03 | +40 / -0 | Added XLSX output manifest sidecar in `export_results_to_excel`; inspects preexisting workbook/sidecar, writes workbook, rereads workbook, writes manifest JSON. | `export_results_to_excel`. Current evidence: `onepass.py:2100-2229`. | no per-ticker; yes export-only | low/medium | Trust, verify export |
| `9715de3` | 2026-05-05 | +591 / -33 | Added structured rejection diagnostics, `rejection_out` plumbing across load/fetch/coerce/save, bounded recent-error UI state, and per-ticker error surfacing. | `save_signal_library`, `load_signal_library`, `fetch_data_raw`, `_coerce_to_close_frame`, `process_onepass_tickers`, Dash callback. Current evidence: `onepass.py:1154`, `1348`, `1594`, `2237-2910`, `3325-3454`. | maybe | low/medium | Verify during pilot |

### Specific risk scrutiny

**Manifest verification on library loads:** same class as the ImpactSearch slowdown. `onepass.py:1348` routes load through `load_verified_signal_library`; `provenance_manifest.py:183-211` computes/uses content hash cache keyed by `(resolved_path, mtime_ns, size)`. PR #278 auto-sized this cache globally (`provenance_manifest.py:102-122`), which reduces eviction risk. OnePass still loads each library once in a full pass, so first-load hashing remains real cost; unlike ImpactSearch multi-secondary warm-cache runs, OnePass does not obviously benefit from deserialized-library LRU unless it reloads the same ticker repeatedly.

**Canonical scoring delegation:** same family as ImpactSearch scoring centralization, but likely lower impact. It is called once per ticker metrics payload (`onepass.py:1964`, `2072`), not inside a nested primary-secondary matrix. Expected overhead is small relative to yfinance fetch, SMA generation, or manifest hash, but pilot timing should measure it.

**Structured `rejection_out` plumbing:** not the same class as the ImpactSearch slowdown. Mostly dict population and logging on failure/branch paths. Expected steady-state cost is small, but it added a large amount of code in the per-ticker path, so pilot should confirm no excessive logging or error-list growth.

**XLSX manifest sidecar:** not per-ticker. It adds post-run export/readback/manifest cost only. This should not dominate a full 35,990-ticker run.

**Wrapper/refactor indirection:** no obvious new wrapper layer equivalent to the ImpactSearch headless-vs-Dash divergence. The higher-risk change is verified loader manifest hashing, not call indirection.

---

## 2. Current OnePass surface

| Layer | File:line | Symbol / behavior |
|---|---|---|
| Import-time setup | `onepass.py:11`, `20`, `22-29` | Imports canonical scoring, `tqdm`, and provenance helpers. |
| Import-time stdout hazard | `onepass.py:65` | Prints parity-config load status to stdout. A runner that reserves stdout for JSON must redirect/capture import-time stdout. |
| Logging setup | `onepass.py:84-97` | Configures console handler and `logs/onepass.log` file handler at import time. |
| Constants | `onepass.py:522-528` | `MAX_SMA_DAY=114`, `ENGINE_VERSION="1.0.0"`, `SIGNAL_LIBRARY_DIR="signal_library/data"`, `PERSIST_SKIP_BARS=1`. |
| Rewarm append | `onepass.py:543` | `perform_rewarm_append(...)` exists but is a placeholder returning `None`; no call sites found. |
| Incremental update | `onepass.py:797`, call at `2489` | `perform_incremental_update(ticker, signal_data, new_df)` handles NEW_DATA append using accumulator state, T-1 persistence, and fingerprint updates. |
| Save library | `onepass.py:1154` | `save_signal_library(..., *, rejection_out=None)` writes `signal_library/data/stable/<ticker>_stable_v1_0_0.pkl` with manifest. |
| Load library | `onepass.py:1348` | `load_signal_library(ticker, *, rejection_out=None)` uses verified provenance loader. |
| Fetch current data | `onepass.py:1594` | `fetch_data_raw(...)` uses yfinance path; current OnePass is not zero-network. |
| Metrics | `onepass.py:1944`, `1995` | `_metrics_from_ccc(...)` and `calculate_metrics_from_signals(...)` delegate scoring to canonical helper. |
| Workbook export | `onepass.py:2100` | `export_results_to_excel(output_filename, metrics_list)` writes 15-column workbook and sidecar manifest. |
| Core engine entry | `onepass.py:2237` | `process_onepass_tickers(tickers_list, use_existing_signals=False, *, emit_summary=True, write_report_json=True)`. |
| Full ticker loop | `onepass.py:2269` | Existing `tqdm(tickers_list, desc="Processing One-Pass Tickers", unit="ticker")`. |
| Dash app object | `onepass.py:2968` | Dash app is built at module import. Importing does not start server, but pulls Dash/yfinance/logging into process. |
| Dash callback | `onepass.py:3325` | `start_processing(...)` parses UI ticker input/options and starts background worker. |
| Dash worker pattern | `onepass.py:3362-3440` | Loops one ticker at a time and calls `process_onepass_tickers([ticker], use_existing_signals=reuse_existing, emit_summary=False, write_report_json=False)`. |
| `__main__` | `onepass.py:3664-3686` | Creates dirs and starts Dash on port `8052`; no argparse / CLI workbook mode. |

### Runner path to mirror

The runner should mirror the Dash worker shape, not invent a new engine path:

```python
result = process_onepass_tickers(
    [ticker],
    use_existing_signals=True,
    emit_summary=False,
    write_report_json=False,
)
```

One ticker at a time preserves the current Dash semantics, including per-call analysis clock, per-ticker continuation behavior, and error tracking. The runner can wrap the outer ticker iterable in its own tqdm for full-run ETA.

### `use_existing_signals=True` behavior

When a library exists:

- `load_signal_library(...)` verifies and loads it.
- `fetch_data_raw(...)` still fetches current yfinance data.
- `evaluate_library_acceptance(...)` decides reuse / incremental update / rebuild.
- If no new data and acceptance is good, existing stored pairs/signals are used for metrics.
- If new data is detected, `perform_incremental_update(...)` appends only new rows using accumulator state, then `save_signal_library(...)`.
- If acceptance says rebuild or update fails, OnePass falls through to the full rebuild path.

When `use_existing_signals=False`, OnePass still detects/loads some library state early, but skips the reuse branch and follows the full rebuild path. A future runner `--force-rebuild` flag should mirror this exactly by passing `use_existing_signals=False`.

---

## 3. Canonical baseline

Canonical artifact:

- `output/onepass/onepass.xlsx`
- SHA-256: `7bf83e85fb119e95ef0f4aa8a669268f32679dea4abb6c3a88f5bbf3d1a6f067`
- size: 3,103,667 bytes
- rows: 35,990
- columns: 15
- manifest: `output/onepass/onepass.xlsx.manifest.json`
- manifest commit: `887d88250a3d953b92c926bee428104d214d88bb`
- manifest producer_engine: `onepass`
- manifest engine_version: `1.0.0`

Column order:

```
Primary Ticker
Trigger Days
Wins
Losses
Win Ratio (%)
Std Dev (%)
Sharpe Ratio
t-Statistic
p-Value
Significant 90%
Significant 95%
Significant 99%
Avg Daily Capture (%)
Total Capture (%)
Last Updated
```

Acceptance target for the runner should be row/schema/content parity, not byte identity. Byte identity is not realistic because workbook metadata, `Last Updated`, manifest `build_timestamp`, `git_commit`, and preexisting-manifest state can legitimately differ.

Workbook readback must use strict NA handling (`na_filter=False` or equivalent). Literal tickers `NA` and `NAN` are valid and must not be coerced to missing values.

---

## 4. Runner design - locked decisions

Operator decisions for v1:

- Universe source: `global_ticker_library/data/master_tickers.txt`, not `V8_Ticker.txt`.
- Current `master_tickers.txt` count: 37,270 unique tickers.
- Default engine mode: `use_existing_signals=True`.
- No automatic backup of `signal_library/data/stable/` before runs.
- No `--max-library-age-hours`, trust threshold, grace threshold, or freshness threshold in v1.
- Force rebuild, if exposed, mirrors current `onepass.py` behavior by passing `use_existing_signals=False`.
- No quarantine of canonical `output/onepass/onepass.xlsx` before write.
- Use atomic `.runner_partial.xlsx` + `os.replace` only.
- `onepass.py` remains source of truth for behavior, except the May 1-6 changes above must be watched in pilot timing.
- Append-mode architecture for downstream engines is future work and out of scope for v1.

Atomic export clarification: v1 should produce a full-universe workbook into `onepass.runner_partial.xlsx` and atomically replace `onepass.xlsx` after successful export. It should not rely on appending to the existing canonical workbook for partial updates. Partial/append architecture is explicitly deferred.

---

## 5. Runner CLI contract

Proposed v1 CLI:

```
onepass_workbook_runner.py
  [--tickers-file global_ticker_library/data/master_tickers.txt]
  [--tickers "AAPL,MSFT,..."]
  [--output-dir output/onepass]
  [--output-file onepass.xlsx]
  [--force-rebuild]
  --write
  --allow-network-fetch
```

Rules:

- Default is dry-run; no workbook write unless `--write`.
- Actual processing requires `--allow-network-fetch` because current OnePass always calls yfinance in `fetch_data_raw`.
- Default ticker source is `global_ticker_library/data/master_tickers.txt`.
- `--tickers` overrides file input for small pilots.
- Default `use_existing_signals=True`.
- `--force-rebuild` means `use_existing_signals=False`.
- Process-conflict check is mandatory at startup.
- Per-ticker errors continue; batch-level setup/export errors abort.
- `tqdm` goes to stderr for full-run ETA.
- Final structured JSON goes to stdout.
- Import-time stdout from `onepass.py` must be captured/redirected so stdout remains parseable JSON.

Mandatory process-conflict patterns should include at least:

```
onepass.py
onepass_workbook_runner.py
impactsearch.py
impactsearch_workbook_runner.py
stackbuilder.py
trafficflow.py
spymaster.py
confluence.py
multi_timeframe_builder.py
signal_library_stable_promotion_writer.py
```

The Phase 6I-59 LRU lesson applies operationally: do not mutate `signal_library/data/stable/` while any long-lived consumer process is active. OnePass itself can mutate stable libraries, so it must not run concurrently with ImpactSearch, stable promotion, or another OnePass runner.

---

## 6. ImpactSearch lessons applied

- Lazy import: runner module should not import `onepass` at top level. `onepass.py` creates Dash/logging/yfinance state at import and prints to stdout.
- Double gate: require `--write` and `--allow-network-fetch`.
- Atomic export: write `.runner_partial.xlsx` and sidecar, then `os.replace` into canonical paths only after success.
- Manifest sidecar: preserve `export_results_to_excel` sidecar behavior.
- Process conflict: mandatory because OnePass writes stable libraries and ImpactSearch may cache them.
- No vague labels: do not name any mode "fast" or "optimized" without same-report benchmark evidence.
- Parity before scale: first runner implementation must prove it matches Dash semantics before full-universe overnight run.

---

## 7. Phased implementation plan

### Phase A - this scoping doc

Read-only audit and execution-surface lock. No code changes.

### Phase B - runner scaffold + tests

Add `onepass_workbook_runner.py` dry-run-first. Tests should cover:

- no top-level `onepass`, `dash`, `yfinance`, or engine import;
- CLI parsing;
- master ticker parsing;
- explicit `--tickers`;
- `NA` / `NAN` ticker preservation;
- dry-run does not write;
- `--write` requires `--allow-network-fetch`;
- process-conflict guard;
- stdout remains structured JSON despite `onepass.py` import-time print;
- fake engine callable per-ticker continuation;
- atomic partial replacement;
- no quarantine behavior;
- no production output writes in tests.

### Phase C - supervised smoke

Small-N run with explicit ticker list and isolated output dir. Confirm:

- exact command;
- per-ticker continuation;
- yfinance gate behavior;
- workbook schema;
- manifest sidecar;
- JSON stdout parseability;
- progress stderr behavior;
- no stable-library surprise outside expected OnePass behavior.

### Phase D - full-universe authorized run

Run against `master_tickers.txt`, canonical `output/onepass/onepass.xlsx`, hard wall-clock ceiling based on manual V8 run evidence. Manual V8 Dash run was about 15 hours for 37,270 input tickers, producing 35,990 rows. First headless full-universe run should have a 20-24 hour ceiling and periodic monitoring.

### Phase E - operator launcher integration

Add gitignored launcher only after runner behavior is proven. Do not bundle launcher work into Phase B.

---

## 8. Open items deferred

- Append-mode downstream architecture for ImpactSearch / StackBuilder / TrafficFlow / MTF / Confluence.
- Multiprocessing or parallel OnePass engine work.
- Stable-promotion integration.
- Stable-library backup strategy.
- A true no-network/library-only OnePass mode.
- Replacing import-time stdout prints inside `onepass.py`.
- Merge/close status for PR #277 cleanup: current main still tracks stale `signal_library/batch_updater.py`; that file is broken on import due to missing `signal_library_utils` and should not be revived for this runner.

---

## 9. Final recommendation

Build `onepass_workbook_runner.py` fresh. Do not revive `signal_library/batch_updater.py`.

The runner should mirror the Dash worker one ticker at a time, default to `use_existing_signals=True`, read `master_tickers.txt`, require explicit network authorization, write atomically through a partial workbook, and produce JSON-only stdout. Before any full-universe overnight run, Phase B/C must measure the May 1-6 changes, especially verified-loader manifest hashing, against a small pilot.

---

## 10. Phase 6I-66 - Phase C supervised smoke (closeout)

Supervised small-N integration verification for the merged Phase B runner. No code or test changes. Recorded here so the Phase A scoping doc carries the Phase C outcome without needing to consult untracked session logs.

**Session evidence path (untracked, gitignored):**
`logs/phase_6i66_smoke_run/20260520T101353Z/`

**Runner commit:** `6c29c54a62438d921149057ce3199d1860ced687` (Phase 6I-64 squash, PR #282).

**Tickers (6):** `AAPL, MSFT, SPY, NVDA, GOOGL, AMZN`.

**Command behavior:**

- `onepass_workbook_runner.py` invoked with `--write` and `--allow-network-fetch`.
- `--force-rebuild` not passed; `use_existing_signals=True` (default).
- Output isolation: workbook written to the session `output_dir/`. Canonical `output/onepass/onepass.xlsx` not written to.

**Runtime:**

- Exit code: `0`.
- Elapsed: `23.993` s.
- 6/6 per-ticker results `status="ok"`.
- `metrics_count = 6`.

**Stdout / stderr discipline:**

- Stdout was exactly one parseable JSON object.
- `onepass.py` import-time and per-ticker prints did not contaminate stdout (captured by the runner's `contextlib.redirect_stdout` around the lazy import and the engine call).
- `tqdm` progress appeared on stderr (outer runner bar + inner OnePass-engine per-ticker bar).
- No unhandled traceback.

**Workbook (`onepass_smoke.xlsx`):**

- Sidecar `onepass_smoke.xlsx.manifest.json` written alongside.
- 6 rows, 15 canonical OnePass columns (`Primary Ticker`, `Trigger Days`, `Wins`, `Losses`, `Win Ratio (%)`, `Std Dev (%)`, `Sharpe Ratio`, `t-Statistic`, `p-Value`, `Significant 90%`, `Significant 95%`, `Significant 99%`, `Avg Daily Capture (%)`, `Total Capture (%)`, `Last Updated`).
- `Primary Ticker` set exactly `{AAPL, MSFT, SPY, NVDA, GOOGL, AMZN}`; no duplicate rows.

**Manifest:**

- `producer_engine = "onepass"`.
- `engine_version = "1.0.0"`.
- `current_run_row_count = 6`.
- `git_commit` matched the runner commit (`6c29c54a62438d921149057ce3199d1860ced687`).
- `git_dirty = false`.

**Canonical artifacts unchanged:**

- `output/onepass/onepass.xlsx` SHA-256 unchanged: `7bf83e85fb119e95ef0f4aa8a669268f32679dea4abb6c3a88f5bbf3d1a6f067`.
- `output/impactsearch/SPY_analysis.xlsx` SHA-256 unchanged: `d3c538452f9345902ba546e5f370e3857a5d155a8e14d3e80af353567c450b56`.

**Stable library mutation profile:**

- `signal_library/data/stable/` file count unchanged.
- 12 changed files total: the six smoke ticker `_stable_v1_0_0.pkl` files plus six matching `.manifest.json` sidecars.
- Changed tickers were only `AAPL, MSFT, SPY, NVDA, GOOGL, AMZN`.
- Zero non-smoke stable PKLs changed.
- **Rebuild cause:** the pre-existing smoke PKLs lacked the newer `params.engine_version` provenance field, so OnePass performed a one-time per-ticker rebuild despite `use_existing_signals=True`. The rebuild populated the field; T-1 persistence dropped the last bar before save, matching documented engine behavior.

**Verdict:** PASS.

### Phase D note

- The Phase D full-universe run should expect some one-time stable-library provenance rebuilds for tickers whose existing PKLs lack `params.engine_version`. This is normal under `use_existing_signals=True` and does not indicate a runner defect.
- Phase D must snapshot `signal_library/data/stable/` before and after the run and verify that the mutation scope is bounded to the universe actually processed.
- Phase D remains a separate authorized task. This closeout does not authorize a full-universe run.
