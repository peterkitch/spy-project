# Phase 6I-55: Supervised StackBuilder pilot batch retry — stopped on locked-policy gap

**Date:** 2026-05-15 (amendment-1 same day, docs-only precision pass)
**Base commit (main):** `63b06c9` (Phase 6I-54b squash-merge)
**Branch:** `phase-6i-55-stackbuilder-pilot-batch-retry`
**Status:** **STOPPED on first ticker** per the prompt's `If any of the 6 no longer pass, STOP and report.` discipline. **Zero StackBuilder writes.** Production roots unchanged. Evidence-only PR. **Do not merge** until operator resolves the upstream ImpactSearch / primary-universe policy gap surfaced here (see Section 8 below; reframed in amendment-1).

`<PINNED_PYTHON> = C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`

## Amendment-1: verified upstream chain + reframed policy gap (docs-only)

Codex re-audit verified the actual upstream dependency chain in code and asked Phase 6I-55 to reframe its failure narrative before merge. The original framing called the gap "missing `--primaries`" — accurate at the runtime layer, but **too narrow**. The real gap is one layer upstream: there is no documented upstream **ImpactSearch / primary-universe policy** that would produce a `--primaries` value (or an ImpactSearch workbook) for each of the 6 ready tickers.

Amendment-1 is a **docs-only** precision pass:

- New **Section 3a "Verified upstream chain"** below cites the exact code paths (OnePass / signal-library → ImpactSearch → StackBuilder → Confluence).
- **Section 8 options** revised. The preferred next path is now a read-only **Phase 6I-55a ImpactSearch / primary-universe readiness planner**. Explicit arbitrary `--primaries` is demoted to a **manual-override** path. The uniform SPY K-universe fallback is marked **NOT recommended** (it ties every secondary to the SPY-shaped K-universe and includes TEF, the known-invalid ticker).
- New **Section 3b "Concrete upstream state today"** records what's actually on disk: SPY + AAPL have ImpactSearch workbooks dated 2026-01-09 (likely stale); JNJ, WMT, HD, MCD are **missing the workbook entirely**.
- Production-roots evidence + execution-log + per-ticker results are unchanged — Phase 6I-55 still wrote nothing.

No code changes. One small doc-guard test added to pin that future doc edits don't lose the upstream-chain citations. No production activity (no StackBuilder, no OnePass, no ImpactSearch, no yfinance, no source refresh, no promotion, no Confluence patch writer, no pipeline runner).

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

## 3a. Verified upstream chain (amendment-1)

The Phase 6I-52 lock specified the StackBuilder *invocation* but treated the upstream dependency chain as implicit. Codex's amendment-1 audit verified the actual chain in code:

```
OnePass / signal_library  ->  ImpactSearch  ->  StackBuilder  ->  Confluence
       (primaries)              (rankings)        (stacks)        (board)
```

Each link is a real code path:

| # | Stage | Module / function (line) | What it produces |
|---|---|---|---|
| 1 | **OnePass** writes per-primary signal libraries | `onepass.py:1154 save_signal_library(...)` | `signal_library/data/stable/<TICKER>_stable_v1_0_0.pkl` + interval variants (currently 72,899 files in the repo). |
| 2 | **ImpactSearch** consumes signal libraries | `impactsearch.py:1525 load_signal_library(ticker, ...)` (per-primary read) | In-memory ranking model. |
| 3 | **ImpactSearch** writes per-secondary ranking workbooks | `impactsearch.py:2491 export_results_to_excel(...)` writing under `output/impactsearch/` (e.g. `impactsearch.py:1355 output_dir = 'output/impactsearch'`) | `output/impactsearch/<TICKER>_analysis.xlsx` — the per-secondary ranking workbook. |
| 4 | **StackBuilder** consumes the ImpactSearch workbook | `stackbuilder.py:583 try_load_rank_from_impact_xlsx(...)` (used at `:1105` when `--prefer-impact-xlsx`) | The `rank_direct` / `rank_inverse` DataFrames StackBuilder needs for K1 seed selection. |
| 5 | **StackBuilder** authorization gate at runtime | `stackbuilder.py:889 phase1_preflight` raises FATAL when neither `--primaries` nor `--prefer-impact-xlsx` is supplied — the explicit "no 72k fallback" guard documented inline (`stackbuilder.py:869`). | (Block point — exits before any write.) |
| 6 | **StackBuilder** iterates K levels | `stackbuilder.py:1487 phase3_build_stacks(...)` (called from the entry routine at `:2431` and `:2866`) — uses `--seed-by`, `--max-k`, `--search`, `--beam-width`, `--combine-mode`, etc. | `output/stackbuilder/<TICKER>/seedTC__...` directories. |
| 7 | **Confluence** consumes the StackBuilder outputs | (Phase 6I-22 → 6I-37 chain; out of scope for Phase 6I-55) | `output/research_artifacts/confluence/<TICKER>/...` MTF Confluence artifact. |

The CLI flag `--prefer-impact-xlsx` is declared at `stackbuilder.py:3361`: `Prefer ImpactSearch .xlsx ranking if available`. It is the **intended bridge** from ImpactSearch into StackBuilder. Explicit `--primaries <CSV>` is the *manual override* — it works but skips the curated upstream ranking ImpactSearch produces.

**Implication for the Phase 6I-55 framing.** The gap is not narrowly "the Phase 6I-52 locked command shape forgot `--primaries`". The gap is broader: there is no documented Phase 6I policy that names whether the StackBuilder rollout should consume ImpactSearch output (`--prefer-impact-xlsx`) — and if it should, no Phase 6I phase has guaranteed those workbooks exist and are fresh for each pilot ticker. Phase 6I-55 inherited an underspecified upstream chain, not just a missing CLI flag.

## 3b. Concrete upstream state today

The repo has an `output/impactsearch/` directory with `<TICKER>_analysis.xlsx` files for many tickers. **For the 6 Phase 6I-54a/6I-54b ready tickers, the state is mixed**:

| Ticker | `output/impactsearch/<T>_analysis.xlsx` present? | Mtime | Freshness assessment |
|---|---|---|---|
| `SPY` | YES (~5.4 MB) | **2026-01-09** | Likely stale — ~4 months old as of 2026-05-15. Operator decision on whether `--prefer-impact-xlsx` over a 4-month-old ranking is acceptable for the first rollout. |
| `AAPL` | YES (~5.4 MB) | **2026-01-09** | Likely stale (same vintage as SPY). |
| `JNJ` | **NO (missing)** | — | Cannot use `--prefer-impact-xlsx`; either run ImpactSearch first OR supply explicit `--primaries`. |
| `WMT` | **NO (missing)** | — | Same as JNJ. |
| `HD` | **NO (missing)** | — | Same as JNJ. |
| `MCD` | **NO (missing)** | — | Same as JNJ. |

So **even option B (`--prefer-impact-xlsx`) cannot serve 4 of the 6 ready tickers today** — the workbook simply doesn't exist for JNJ/WMT/HD/MCD. The upstream chain is broken further upstream than the Phase 6I-55 narrative originally framed. For SPY + AAPL, the workbooks exist but are operator-decision-stale.

This is the concrete on-disk evidence behind the amendment-1 revised Section 8 below.

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

## 8. Options for resolution (amendment-1 revised)

The underlying decision is upstream of `--primaries`: it is *which curated primaries source should the StackBuilder rollout consume, and is that source ready for the 6 ready tickers?*. The intended app chain (Section 3a) is OnePass → ImpactSearch → StackBuilder. Phase 6I-55 deliberately does NOT pick a path unilaterally; the options below are framed against that chain.

### Preferred path — Phase 6I-55a ImpactSearch / primary-universe readiness planner

Before any further StackBuilder authorization, a **read-only Phase 6I-55a planner** should formally inspect, for each of the 6 ready secondary tickers, whether the upstream ImpactSearch chain is ready. The planner should:

- Probe `output/impactsearch/<TICKER>_analysis.xlsx` (the canonical workbook produced by `impactsearch.py:2491 export_results_to_excel`).
- Verify the workbook's `provenance_manifest` sidecar (Phase 3B-2A produced output-manifest helpers for these workbooks; the workbook should have a paired `.manifest.json` recording `build_timestamp`, `params`, and a content hash).
- Extract the `rank_direct` / `rank_inverse` sheets via `stackbuilder.py:583 try_load_rank_from_impact_xlsx`-style inspection and report the **primary universe StackBuilder would actually use** for that ticker.
- Apply a freshness rule (operator decides — e.g. workbook `build_timestamp` within N days of the StackBuilder pilot's data-as-of date).
- Classify each ticker into a stable taxonomy:

  | Status code | Meaning |
  |---|---|
  | `ready_for_stackbuilder_with_impact_xlsx` | Workbook present, manifest verified, fresh enough, primaries extractable. The Phase 6I-52 amendment can wire `--prefer-impact-xlsx` for this ticker. |
  | `needs_impactsearch_run` | Workbook missing OR stale OR manifest unverifiable. ImpactSearch must run (its own authorized phase) before StackBuilder. |
  | `manual_review` | Workbook present but ambiguous (e.g. zero rank rows, structural issue, conflicting provenance). |

- Emit per-ticker evidence + an aggregate report. **Read-only**: no ImpactSearch invocation, no StackBuilder invocation, no yfinance, no `subprocess`, no raw `pickle.load` (use the existing verified-loader pattern from Phase 6I-54a/b).

The 6I-55a planner is the natural counterpart to Phase 6I-53 (StackBuilder local-cache preflight) and Phase 6I-54a (price-cache rebuild planner). Same stop-and-resolve pattern.

### Other paths (kept for completeness)

| Option | Description | Recommendation |
|---|---|---|
| **A — explicit `--primaries <CSV>` per ticker** | Operator supplies a curated primaries list per ticker out-of-band (paste into a future `--accept-explicit-primaries` flag on the Phase 6I-52 amendment). | **Manual override only.** Works in a pinch; skips the curated ImpactSearch ranking and the upstream-chain audit trail. Should not be presented as equivalent to the app chain. |
| **B — add `--prefer-impact-xlsx` to the locked command** | Direct addition to the Phase 6I-52 locked shape. | Cannot serve JNJ/WMT/HD/MCD today (workbooks missing — see Section 3b). Serves SPY/AAPL today but only against ~4-month-old workbooks. **Operator-decision blocker until Phase 6I-55a confirms workbook freshness.** |
| **C — run ImpactSearch first** | Run ImpactSearch (its own authorized phase) to produce per-ticker workbooks, then retry Phase 6I-55 with option B. | This is the path Phase 6I-55a's `needs_impactsearch_run` classification directs the operator to. ImpactSearch authorization is its own phase. |
| **D — uniform SPY K-universe fallback** | Use `AWR, CP, EXPO, LLY, CLH, GBCI, HCSG, TEF, JNJ, MO, AROW, PRA` as `--primaries` for all 6 tickers. | **NOT recommended.** Ties every secondary to the SPY-shaped K-universe (no economic basis for AAPL/JNJ/WMT/HD/MCD using SPY's K-universe). Also includes TEF (the Phase 6I-43-flagged known-invalid ticker), which would propagate through the partial-payload path. Emergency-manual only. |

### Concrete next-step recommendation

1. **Write Phase 6I-55a** as a read-only planner mirroring the Phase 6I-53 / 6I-54a pattern. Its output classifies each of the 6 ready secondary tickers as `ready_for_stackbuilder_with_impact_xlsx`, `needs_impactsearch_run`, or `manual_review`.
2. **Operator reviews 6I-55a output.** For `needs_impactsearch_run` tickers, the operator either authorizes a Phase 6I-55b ImpactSearch batch (its own authorized phase) or accepts deferring those tickers until a separate ImpactSearch cycle runs.
3. **Phase 6I-52 amendment-3** locks the StackBuilder command shape with `--prefer-impact-xlsx` (and any freshness criteria the operator selects). Manual `--primaries` override stays available as a documented emergency path.
4. **Phase 6I-55c** retries the supervised batch using the amended locked command, against only the `ready_for_stackbuilder_with_impact_xlsx` tickers.

No Phase 6I-55 batch retry should proceed under the current Phase 6I-52 lock — the upstream chain Phase 6I-55a will surface is the real prerequisite.

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
