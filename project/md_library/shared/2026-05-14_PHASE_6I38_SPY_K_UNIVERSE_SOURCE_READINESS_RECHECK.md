# Phase 6I-38: SPY K-universe source-readiness re-run

**Branch:** `phase-6i-38-spy-k-universe-source-readiness-recheck`
**Date:** 2026-05-14
**Mode:** Read-only re-run of the Phase 6I-33 source-cache refresh
readiness coordinator. No code changes.

## 1. Verdict

**`refresh_candidate_ready = false` — refresh remains blocked.**

Recommended next action from the readiness coordinator:
`wait_or_resolve_blockers`.

### 1.1 Per-classification counts

| Classification | Count |
|---|---|
| `source_equal_cutoff_wait` | 14 |
| `source_behind_or_error` | 1 |
| `already_cache_ready` | 0 |
| `source_ready_for_refresh` | 0 |
| `manual_blocker` | 0 |

### 1.2 What changed since Phase 6I-33

- **14 equities advanced.** In Phase 6I-33, every one of
  the 14 non-TEF equities reported
  `new_cache_date_range_end=2026-05-13 < current_as_of_date=2026-05-14`
  → `source_behind_or_error`. They are now reporting
  `new_cache_date_range_end=2026-05-14 ==
  current_as_of_date=2026-05-14` → `source_equal_cutoff_wait`.
  yfinance has caught up to the cutoff, BUT the predicate
  requires STRICT `new_cache_date_range_end > cutoff`. The
  Phase 6I-15 / 6I-17 operator discipline says **WAIT** —
  a refresh would not advance the predicate.
- **TEF unchanged.** TEF still classifies as
  `source_behind_or_error` with the same yfinance
  "delisted" telemetry as Phase 6I-33 (cache stuck at
  `2026-01-28`; `new_cache_date_range_end=null`;
  `fetch_succeeded=false`; `rows=0`; `error=null`). This is
  a persistent vendor-side blocker, not a transient one.

## 2. Per-ticker readiness

| Ticker | Classification | `cache_date_range_end` | `new_cache_date_range_end` | Notes |
|---|---|---|---|---|
| SPY  | `source_equal_cutoff_wait` | 2026-05-12 | 2026-05-14 | |
| AROW | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| AWR  | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| CLH  | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| CP   | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| EXPO | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| FCFS | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| GBCI | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| HCSG | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| JNJ  | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| LLY  | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| MO   | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| PRA  | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| PRGO | `source_equal_cutoff_wait` | 2026-05-04 | 2026-05-14 | |
| TEF  | `source_behind_or_error` | 2026-01-28 | `null` | `source_issue:source_missing_new_cache_date`, `provider_fetch_failed` |

### 2.1 Aggregate blocker reasons (full list)

```
SPY:source_equal_cutoff_wait
AROW:source_equal_cutoff_wait
AWR:source_equal_cutoff_wait
CLH:source_equal_cutoff_wait
CP:source_equal_cutoff_wait
EXPO:source_equal_cutoff_wait
FCFS:source_equal_cutoff_wait
GBCI:source_equal_cutoff_wait
HCSG:source_equal_cutoff_wait
JNJ:source_equal_cutoff_wait
LLY:source_equal_cutoff_wait
MO:source_equal_cutoff_wait
PRA:source_equal_cutoff_wait
PRGO:source_equal_cutoff_wait
TEF:source_behind_or_error
```

### 2.2 TEF telemetry (persistent blocker)

```json
{
  "ticker": "TEF",
  "cache_exists": true,
  "cache_date_range_end": "2026-01-28",
  "current_as_of_date": "2026-05-14",
  "cache_ahead_of_cutoff": false,
  "cache_equal_to_cutoff": false,
  "cache_behind_cutoff": true,
  "source_ahead_of_cutoff": false,
  "source_equal_to_cutoff": false,
  "source_behind_cutoff": false,
  "new_cache_date_range_end": null,
  "provider_fetch_telemetry": {
    "provider_name": "yfinance",
    "fetch_attempted": true,
    "fetch_succeeded": false,
    "ticker": "TEF",
    "rows": 0,
    "date_range_start": null,
    "date_range_end": null,
    "elapsed_seconds": 0.25,
    "error": null
  },
  "classification": "source_behind_or_error",
  "notes": [
    "source_issue:source_missing_new_cache_date",
    "provider_fetch_failed"
  ]
}
```

The yfinance probe surface also surfaces the upstream
warning:

```
1 Failed download:
['TEF']: YFPricesMissingError('possibly delisted; no price
data found  (1d 1927-06-08 -> 2026-05-14) (Yahoo error =
"No data found, symbol may be delisted")')
```

TEF is consistent with the Phase 6I-33 verdict: vendor-side
classification of "possibly delisted." This is a persistent
blocker, not a transient hiccup.

### 2.3 SPY telemetry (representative `source_equal_cutoff_wait`)

```json
{
  "ticker": "SPY",
  "cache_date_range_end": "2026-05-12",
  "current_as_of_date": "2026-05-14",
  "cache_behind_cutoff": true,
  "source_equal_to_cutoff": true,
  "new_cache_date_range_end": "2026-05-14",
  "provider_fetch_telemetry": {
    "provider_name": "yfinance",
    "fetch_attempted": true,
    "fetch_succeeded": true,
    "ticker": "SPY",
    "rows": 8380,
    "date_range_start": "1993-01-29",
    "date_range_end": "2026-05-14",
    "elapsed_seconds": 0.875,
    "error": null
  },
  "classification": "source_equal_cutoff_wait",
  "notes": []
}
```

yfinance fetch succeeded for SPY and reports the latest
bar at `2026-05-14`. The cache is at `2026-05-12` (one
trading day behind a prior `current_as_of_date` resolution).
The predicate `new_cache_date_range_end > current_as_of_date`
is NOT satisfied because `2026-05-14 == 2026-05-14`. Per
the Phase 6I-15 / 6I-17 operator discipline the correct
action is WAIT.

## 3. Refresh remains blocked

The aggregate verdict is **`refresh_candidate_ready=false`**.
Blockers come in two distinct shapes:

1. **14 equities — `source_equal_cutoff_wait`.** Source has
   matched the cutoff but has not advanced past it. A
   supervised refresh now would not change the cache state
   in a way that would advance the strict-greater predicate.
   The Phase 6E-5 refresher's published / unpublished
   classification under the Phase 6I-15 discipline therefore
   says WAIT.

2. **TEF — `source_behind_or_error`.** Vendor-side
   "possibly delisted" classification. Refreshing TEF
   currently yields zero rows from yfinance and the
   readiness predicate cannot fire while TEF is in the
   universe.

## 4. Recommended next concrete actions

**No supervised refresh command is prepared by this PR**
per the operator discipline: when
`refresh_candidate_ready=false`, this evidence pass does
NOT emit an executable future refresh command block.

Concrete next actions the operator may pick from:

1. **Wait for the next trading day.** When the cutoff
   resolves to 2026-05-15 (or later) AND yfinance has
   published that trading day's bar for the 14 equities,
   re-run this readiness module. If the verdict flips to
   `refresh_candidate_ready=true` (or every equity flips
   to `source_ready_for_refresh` and TEF stays as the only
   blocker), request a separate operator-authorized
   supervised source-cache refresh prompt.

2. **Triage TEF independently.** TEF has now persisted
   across Phase 6I-33 and Phase 6I-38 with the same
   "possibly delisted" yfinance telemetry (cache stuck at
   `2026-01-28`; `new_cache_date_range_end=null` in both
   runs). Options:
   - Confirm whether TEF is genuinely delisted on Yahoo
     (manually, via the operator's separate channels).
   - If delisted, decide whether to drop TEF from the SPY
     K-universe (membership change) or pin its evaluation
     cutoff to its last available trading date
     (2026-01-28).
   - Until TEF is resolved, the aggregate predicate cannot
     reach `refresh_candidate_ready=true` while TEF is in
     the universe. Note that the downstream Phase 6I-22
     adapter / Phase 6I-32 sandbox builder ALREADY
     accommodates TEF's earlier cutoff for sandbox proofs
     (sandbox binding cutoff 2026-01-28 for TEF was used
     during Phase 6I-30), so a SPY-pilot refresh that
     excludes TEF from the K-universe is technically
     feasible if the operator decides to scope it that
     way.

3. **Continue parallel website-renderer / scoring work.**
   The SPY pilot remains parked. Other work (Phase 6I-37
   already-merged current-build signal surface → website
   renderer / Dash UI shell; researched scoring contract
   to replace the first-pass ranking rule) can proceed
   independently while waiting on source readiness.

## 5. Exact command executed

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
  signal_library_source_refresh_readiness.py \
  --tickers "SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO,TEF" \
  --cache-dir cache/results \
  --current-as-of-date 2026-05-14
```

- rc = 0
- stdout JSON saved as
  `project/md_library/shared/2026-05-14_PHASE_6I38_SPY_K_UNIVERSE_SOURCE_READINESS_RECHECK.json`
  (989 lines including pretty-printed
  `cache_cutoff_raw_summary` + `source_availability_raw_summary`
  blocks).
- stderr was the yfinance warning about TEF
  ("possibly delisted; no price data found"). No
  unhandled traceback.

The Phase 6I-33 / 6I-15 read-only contract was followed:
no `--write` flag; no `PRJCT9_AUTOMATION_WRITE_AUTH`; no
production-writer / pipeline-runner / batch-engine
invocation. The source-availability probe internally
dry-runs the Phase 6E-5 refresher with `write=False`
(observable yfinance read; no cache mutation).

## 6. Production-root snapshot — 0/0/0/0/0 diff

Pre-run vs post-run file counts under all five canonical
production roots:

| Root | Pre | Post | Diff |
|---|---|---|---|
| `cache/results` | 3239 | 3239 | 0 |
| `cache/status` | 1634 | 1634 | 0 |
| `output/research_artifacts` | 35 | 35 | 0 |
| `output/stackbuilder` | 5221 | 5221 | 0 |
| `signal_library/data/stable` | 72899 | 72899 | 0 |
| **Total** | **83028** | **83028** | **0** |

The yfinance dry-run probe read only; no cache PKL was
written; no status JSON was touched; no Confluence
artifact, StackBuilder leaderboard, or stable signal
library was modified.

## 7. No-production-activity confirmation

- No writer `--write` invocation (any writer).
- `PRJCT9_AUTOMATION_WRITE_AUTH` never read or set.
- No production source-cache refresh in write mode
  (`signal_engine_cache_refresher.py --write` NOT run).
- No production promotion
  (`signal_library_stable_promotion_writer.py`).
- No Confluence patch writer
  (`multiwindow_k_confluence_patch_writer.py`).
- No `confluence_pipeline_runner` invocation.
- No StackBuilder / OnePass / ImpactSearch / TrafficFlow /
  Spymaster batch execution.
- No production data write of any kind.
- Production roots unchanged (`0/0/0/0/0` diff above).

A read-only source-availability probe was performed via
the established Phase 6I-33 coordinator, which internally
dry-runs the Phase 6E-5 refresher with `write=False`. This
follows the Phase 6I-15 / 6I-33 read-only pattern and
writes nothing.

## 8. SPY remains parked

The SPY pilot stays **PARKED** at the Phase 6I-33 / 6I-34
cursor: production
`has_true_multiwindow_k_engine_outputs=false` for SPY;
production Confluence artifacts (SPY + `_GSPC`) still
classify `daily_only` under the Phase 6I-34 ranking export
contract and surface empty `current_build_signals=[]` /
`current_build_signal_summary=null` on the Phase 6I-37
website surface (no fabrication).

The SPY pilot will resume when (and only when) a future
re-run of this readiness module reports
`refresh_candidate_ready=true`. At that point a separate
operator-authorized supervised source-cache refresh prompt
will be required before any write step.
