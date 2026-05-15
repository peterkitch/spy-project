# Phase 6I-55: Supervised StackBuilder pilot batch retry — stopped on locked-policy gap

**Date:** 2026-05-15
**Base commit (main):** `63b06c9` (Phase 6I-54b squash-merge)
**Branch:** `phase-6i-55-stackbuilder-pilot-batch-retry`
**Status:** **STOPPED on first ticker** per the prompt's `If any of the 6 no longer pass, STOP and report.` discipline. **Zero StackBuilder writes.** Production roots unchanged. Evidence-only PR. **Do not merge** until operator resolves the Phase 6I-52 locked-policy gap surfaced here.

`<PINNED_PYTHON> = C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`

---

## 1. Summary

Phase 6I-55 was authorized to run the Phase 6I-52 locked StackBuilder command shape against the 6 ready tickers (SPY, AAPL, JNJ, WMT, HD, MCD) after the Phase 6I-53 preflight confirmed local price-cache availability. The preflight passed cleanly (6/25 — exactly the expected set). The first attempted run (SPY) exited with **rc=1 FATAL** before reaching `load_secondary_prices`:

```
[FATAL] Primary tickers field is empty. Please supply one or more primaries.
```

`stackbuilder.py:869` carries the comment `CRITICAL: Strictly require user primaries; no 72k fallback`, so the auto-discovery path the argparse help text mentions is **explicitly disabled** at runtime for safety. The Phase 6I-52 locked command shape **does not include `--primaries`** — this is a real gap in the policy lock that surfaces only at execution time. The unresolved-policy block in Phase 6I-52 had `per_ticker_member_universe_sizing` listed as deferred, but the absence of any `--primaries` decision in the locked command was not flagged.

Per the prompt's stop-and-report discipline, the remaining 5 tickers were **not** attempted — they would have produced identical FATAL exits with the same gap. Production roots are bit-for-bit unchanged; the `output/stackbuilder/SPY/` directory still has its 19 legacy files and no new seed-run dir was created.

## 2. Preflight verdict (still 6/25)

```
<PINNED_PYTHON> confluence_stackbuilder_pilot_preflight.py \
    --output md_library/shared/2026-05-15_PHASE_6I55_PRE_RUN_PREFLIGHT.json
```

| Field | Value |
|---|---|
| `price_cache_dir_exists` | `True` |
| `pass_count` | **6** |
| `skip_count` | **19** |
| Passing | `[AAPL, HD, JNJ, MCD, SPY, WMT]` |
| Skipping (first 5 of 19) | `[ADBE, AMD, AMZN, AVGO, BRK-B]` |

The Phase 6I-54b authorized write held: the 6 CSV files are still present, the preflight still classifies SPY/AAPL/JNJ/WMT/HD/MCD as `pass`, and the other 19 still classify as `skip_missing_cache_would_fetch_yfinance`. **The preflight gate was the right gate**; it just wasn't the last gate.

## 3. The Phase 6I-52 locked-policy gap

### The locked command (Phase 6I-52)

```
<PINNED_PYTHON> stackbuilder.py \
    --secondary <TICKER> \
    --top-n 20 --bottom-n 20 --max-k 6 \
    --search beam --beam-width 12 \
    --seed-by total_capture --optimize-by total_capture \
    --min-trigger-days 30 \
    --combine-mode intersection \
    --signal-lib-dir signal_library/data/stable
```

No `--primaries`. No `--prefer-impact-xlsx`. The implicit assumption was that `stackbuilder.py` would auto-discover primaries from the master list or signal library, per the `--primaries` argparse help: *"Comma-separated list of primary tickers to analyze (if not set, uses master list or discovers from signal library)"*.

### What the runtime actually does

`stackbuilder.py:862-889` (`phase1_preflight`):

```python
# CRITICAL: Strictly require user primaries; no 72k fallback
if specified_primaries is not None:
    primaries = primary_universe(specified_primaries)
    if not primaries:
        if getattr(args, "prefer_impact_xlsx", False):
            print("[INFO] Will attempt to use ImpactSearch Excel for primaries.")
            primaries = []
        else:
            raise SystemExit("[FATAL] No primary tickers provided. ...")
else:
    # No primaries specified at all - allow if using ImpactSearch xlsx
    if getattr(args, "prefer_impact_xlsx", False):
        print("[INFO] No primaries specified, will use all from ImpactSearch Excel.")
        primaries = []
    else:
        raise SystemExit("[FATAL] Primary tickers field is empty. ...")
```

The runtime path **disables** the auto-discovery the help text advertises — by design, to prevent accidental 72k-ticker runs. Either `--primaries <CSV>` or `--prefer-impact-xlsx` is required.

### Evidence from legacy on-disk seed-runs

Both pre-existing seed-run directory names embed an explicit 12-ticker primaries set:

| Legacy seed-run dir | Primaries embedded in dir name |
|---|---|
| `output/stackbuilder/SPY/seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D` | AWR, CP, EXPO, LLY, CLH, GBCI, HCSG, **TEF**, JNJ, MO, AROW, PRA (matches the Phase 6I-43/44/49 SPY K-universe) |
| `output/stackbuilder/AAPL/seedTC__EXC-D_HD-D_JFJ.L-D_MWY.L-D_JCH.L-D_ATR.L-D_ETE.AT-I_HGT.L-D_CSL.AX-D_MGR.AX-I_TTE-I_AAPL-D` | EXC, HD, JFJ.L, MWY.L, JCH.L, ATR.L, ETE.AT, HGT.L, CSL.AX, MGR.AX, TTE, AAPL (a different international set chosen by an earlier ImpactSearch / curated workflow) |

So the two legacy runs used **completely different primaries sets**. There is no single canonical "default primaries" that the Phase 6I-52 lock could silently inherit — the choice is a real operator policy decision.

## 4. Execution log

| # | Ticker | rc | Files added under `output/stackbuilder/<T>/` | yfinance fetched | Notes |
|---|---|---|---|---|---|
| 1 | **SPY** | **1** | 0 | No | FATAL: `Primary tickers field is empty.` Exit before `load_secondary_prices`. |
| 2 | AAPL | — | — | — | NOT ATTEMPTED (stop-and-report). |
| 3 | JNJ | — | — | — | NOT ATTEMPTED. |
| 4 | WMT | — | — | — | NOT ATTEMPTED. |
| 5 | HD | — | — | — | NOT ATTEMPTED. |
| 6 | MCD | — | — | — | NOT ATTEMPTED. |

**Raw run output** for SPY:
- stdout: `md_library/shared/2026-05-15_PHASE_6I55_RUN_SPY_STDOUT.txt` (1 line: `[SUCCESS] parity_config loaded successfully (STRICT_PARITY_MODE=False)`)
- stderr: `md_library/shared/2026-05-15_PHASE_6I55_RUN_SPY_STDERR.txt` (1 line: `[FATAL] Primary tickers field is empty. Please supply one or more primaries.`)

## 5. No yfinance fetched

The SPY FATAL exit happened **inside `phase1_preflight` BEFORE `load_secondary_prices` is called** (`stackbuilder.py:889` raises before `:864`). Even if the FATAL had not fired, the Phase 6I-53 preflight had already confirmed all 6 ready tickers have local CSVs in `price_cache/daily/`, so `_fetch_secondary_from_yf` would not have been reached. Both layers of the defense in depth held.

## 6. Production-root accounting

| Root | Pre-run | Post-run | Diff |
|---|---|---|---|
| `cache/results` | 3239 | 3239 | 0 |
| `cache/status` | 1634 | 1634 | 0 |
| `output/research_artifacts` | 35 | 35 | 0 |
| `output/stackbuilder` | 5229 | 5229 | 0 |
| `signal_library/data/stable` | 72899 | 72899 | 0 |
| `price_cache/daily` | 6 | 6 | 0 |
| **Combined (5 documented)** | **83036** | **83036** | **0** |

Per-ticker `output/stackbuilder/<TICKER>/` state:

| Ticker | Pre | Post | Diff |
|---|---|---|---|
| SPY | 19 files | 19 files | 0 |
| AAPL | 19 files | 19 files | 0 |
| JNJ | dir missing | dir missing | 0 |
| WMT | dir missing | dir missing | 0 |
| HD | dir missing | dir missing | 0 |
| MCD | dir missing | dir missing | 0 |

## 7. Phase 6I-50 / 6I-51 reclassification (unchanged)

Read-only re-runs against the unchanged production state. Verdicts identical to pre-Phase-6I-55:

- **Phase 6I-50:** `SPY → already_board_ranked`, `_GSPC → blocked_missing_inputs`.
- **Phase 6I-51:** `board_render_now=['SPY']`, `blocked_or_manual_review=['_GSPC']`, other batches empty.

## 8. Options for resolution

The `--primaries` axis is a real operator policy decision; this phase deliberately does NOT pick one unilaterally.

| Option | Description | Tradeoff |
|---|---|---|
| **A — explicit `--primaries` per ticker** | Operator supplies a curated `--primaries` list per ticker (e.g. from ImpactSearch output). | Most auditable; requires operator decision per ticker. |
| **B — add `--prefer-impact-xlsx`** | If each pilot ticker has an ImpactSearch XLSX on disk, this flag auto-loads its primaries. | Requires per-ticker XLSX existence (not verified by Phase 6I-52). |
| **C — run ImpactSearch first** | Run ImpactSearch (its own authorized phase) to produce per-ticker XLSXs, then retry Phase 6I-55 with option B. | Most complete but adds a phase. |
| **D — uniform fallback primaries** | Use the SPY K-universe (`AWR, CP, EXPO, LLY, CLH, GBCI, HCSG, TEF, JNJ, MO, AROW, PRA`) uniformly for all 6 tickers. | Cheapest. Ties every secondary to the SPY-shaped K-universe, which may not be representative for AAPL/JNJ/WMT/HD/MCD. Also includes TEF (the known-invalid ticker), which would propagate through the partial-payload path. |

**Recommended next step:** the operator picks an option (A/B/C/D) and either:
1. **Amend Phase 6I-52** with a third amendment that adds the `--primaries` decision to the locked command shape; OR
2. **Run a separate Phase 6I-55a planner phase** that resolves the `--primaries` axis for each of the 6 ready tickers, then a Phase 6I-55b retry uses the locked-plus-amended command shape.

Either way, no Phase 6I-55 batch retry can responsibly proceed until the gap is closed.

## 9. What this PR does NOT do

- Does NOT pick a `--primaries` value unilaterally. The Phase 6I-55 implementation deliberately stops at the first FATAL instead of inventing a policy decision the Phase 6I-52 lock did not authorize.
- Does NOT touch any of the 5 documented production roots. `output/stackbuilder/` is bit-for-bit identical.
- Does NOT modify `price_cache/daily/`. The 6 CSV files from Phase 6I-54b are preserved.
- Does NOT invoke yfinance, the source-cache refresher, the signal-library promotion writer, the Confluence patch writer, the pipeline runner, OnePass, ImpactSearch, TrafficFlow, or Spymaster batch.
- Does NOT modify the Phase 6I-50 / 6I-51 / 6I-52 / 6I-53 / 6I-54a / 6I-54b modules.

## 10. Files added (5)

- `project/md_library/shared/2026-05-15_PHASE_6I55_STACKBUILDER_PILOT_BATCH_RETRY.md` (this doc).
- `project/md_library/shared/2026-05-15_PHASE_6I55_STACKBUILDER_PILOT_BATCH_RETRY_EVIDENCE.json` — consolidated evidence (preflight + execution log + policy-gap analysis + production-root diff + reclassification).
- `project/md_library/shared/2026-05-15_PHASE_6I55_PRE_RUN_PREFLIGHT.json` — raw preflight table.
- `project/md_library/shared/2026-05-15_PHASE_6I55_RUN_SPY_STDOUT.txt` — SPY run stdout (one `[SUCCESS]` line).
- `project/md_library/shared/2026-05-15_PHASE_6I55_RUN_SPY_STDERR.txt` — SPY run stderr (the FATAL).
- `project/md_library/shared/2026-05-15_PHASE_6I55_POST_RUN_6I50_LAUNCH_PLAN.json` — Phase 6I-50 verdict post-attempt.
- `project/md_library/shared/2026-05-15_PHASE_6I55_POST_RUN_6I51_ROLLOUT_PLAN.json` — Phase 6I-51 verdict post-attempt.

## 11. Tests

Per the prompt: *"Add or update tests only if needed for reusable execution evidence/parsing helpers."* No reusable helpers were added (the runs were direct one-shot Bash invocations with stdout/stderr capture). No new tests therefore. Focused regression confirmed unchanged:

```
"<PINNED_PYTHON>" -m pytest \
    test_scripts/test_confluence_stackbuilder_pilot_preflight.py \
    test_scripts/test_confluence_stackbuilder_rollout_policy.py \
    test_scripts/test_confluence_large_universe_rollout_batch_planner.py -q
61 passed in 1.43s
```
