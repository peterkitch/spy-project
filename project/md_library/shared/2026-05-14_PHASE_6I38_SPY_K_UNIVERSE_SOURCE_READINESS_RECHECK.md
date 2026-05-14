# Phase 6I-38: SPY K-universe source-readiness re-run

**Branch:** `phase-6i-38-spy-k-universe-source-readiness-recheck`
**Date:** 2026-05-14
**Mode:** Read-only re-run of the Phase 6I-33 source-cache refresh
readiness coordinator. No code changes.

## 1. Verdict

**`refresh_candidate_ready = false` under the current
Phase 6I-33 strict-greater readiness predicate — refresh
remains blocked.**

Recommended next action from the readiness coordinator:
`wait_or_resolve_blockers`.

The blocker for the 14 non-TEF tickers is **not** "fresh
provider data unavailable." yfinance returned data
through `2026-05-14` for all 14. The blocker is that the
current harness rule requires
`new_cache_date_range_end > current_as_of_date` (strict),
while the observed state is
`new_cache_date_range_end == current_as_of_date`. See
§ 3 for the policy ambiguity this exposes — it is the
next concrete decision required of the operator. TEF is
a separate persistent vendor-side blocker; see § 4 (2).

### 1.1 Per-classification counts

| Classification | Count |
|---|---|
| `source_equal_cutoff_wait` | 14 |
| `source_behind_or_error` | 1 |
| `already_cache_ready` | 0 |
| `source_ready_for_refresh` | 0 |
| `manual_blocker` | 0 |

### 1.2 What changed since Phase 6I-33 (and what did NOT)

**Important precision** (Codex audit, amendment-1): the
14 non-TEF equities now have yfinance data through
`2026-05-14`. The blocker for those 14 tickers is NOT
"fresh data unavailable." It is the existing Phase 6I-33
**strict-greater** predicate requiring
`new_cache_date_range_end > current_as_of_date` while the
observed source data is `==` `current_as_of_date`. The
production cache for these tickers is stale (SPY at
`2026-05-12`; other 13 at `2026-05-04`) and the provider
has the target bar — the harness simply does not allow a
refresh up to the cutoff under its current rule.

- **14 equities advanced.** In Phase 6I-33, every one of
  the 14 non-TEF equities reported
  `new_cache_date_range_end=2026-05-13 < current_as_of_date=2026-05-14`
  → `source_behind_or_error`. They are now reporting
  `new_cache_date_range_end=2026-05-14 ==
  current_as_of_date=2026-05-14` → `source_equal_cutoff_wait`.
  The current Phase 6I-33 readiness harness says **WAIT**
  under its existing strict-greater predicate. *This does
  NOT mean yfinance lacks the 2026-05-14 bar for those
  tickers.* For 14 tickers, source data equals the target
  cutoff while cache is stale; the next phase should
  explicitly decide whether equal-cutoff-after-close is
  sufficient for supervised refresh.
- **TEF unchanged.** TEF still classifies as
  `source_behind_or_error` with the same yfinance
  "delisted" telemetry as Phase 6I-33 (cache stuck at
  `2026-01-28`; `new_cache_date_range_end=null`;
  `fetch_succeeded=false`; `rows=0`; `error=null`). This is
  a persistent vendor-side blocker, not a transient one,
  and is **a separate question from the
  source-equal-cutoff-after-close policy decision above**.
  Even if the equal-cutoff policy is later relaxed, TEF
  still needs triage: replacement ticker, exclusion
  policy, alternate provider, or K-universe rebuild
  without TEF.

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
bar at `2026-05-14`. **The cache is at `2026-05-12`** (two
trading days stale relative to the resolved
`current_as_of_date=2026-05-14`). The current harness
predicate `new_cache_date_range_end > current_as_of_date`
is NOT satisfied because `2026-05-14 == 2026-05-14`. The
Phase 6I-33 readiness harness says **WAIT** under the
existing strict-greater predicate. **This does NOT mean
yfinance lacks the 2026-05-14 bar for SPY** — it returned
8,380 rows including the `2026-05-14` bar with
`fetch_succeeded=true`. The harness rule simply forbids a
refresh under the strict-greater predicate at the moment
the provider's latest bar equals the cutoff.

## 3. Policy ambiguity exposed by Phase 6I-38

Phase 6I-38 is the first re-run where the harness probed
a state in which the provider has the **target cutoff
bar** while the production cache is **strictly behind the
cutoff** for 14 tickers. That exposes a policy question
that the Phase 6I-33 readiness rule does not currently
answer:

- **Current harness readiness rule:**
  `source_ready_for_refresh` fires ONLY when
  `new_cache_date_range_end > current_as_of_date` (strict).
  Anything else, including the equal-cutoff case, classifies
  as `source_equal_cutoff_wait` and demotes the aggregate
  verdict to `refresh_candidate_ready=false`.

- **Observed state for 14 tickers in this run:**
  `cache_date_range_end < current_as_of_date` AND
  `new_cache_date_range_end == current_as_of_date`. The
  provider has the target bar; the production cache does
  not. A refresh now would advance the cache from a
  stale state to the target cutoff. The strict-greater
  predicate forbids it.

- **Operational question (NOT decided in this PR):**
  Should stale cache be allowed to refresh **to the target
  cutoff after market close** when the provider has that
  target bar — i.e., should the readiness rule accept
  `new_cache_date_range_end >= current_as_of_date` when
  `cache_date_range_end < current_as_of_date`, or should
  it preserve the strict-greater rule because of an
  intraday / partial-bar concern that may still apply
  even after the official close?

- **No rule change in this PR.** Phase 6I-38 is an
  evidence-only re-run; the readiness predicate is
  unchanged from Phase 6I-33. The policy decision above
  is recorded here as **the next concrete decision** the
  operator should make before another readiness re-run,
  separate from the trading-day-rollover wait. The
  decision and any rule change would be a future phase
  (Phase 6I-N) with its own preflight, audit, and tests
  — including any new classification (for example
  `source_equal_cutoff_publishable_for_refresh`) and any
  new aggregate-verdict semantics.

## 4. Refresh remains blocked

The aggregate verdict is **`refresh_candidate_ready=false`
under the current strict-greater harness rule**. Blockers
come in two distinct shapes, and they are independent:

1. **14 equities — `source_equal_cutoff_wait`.** Source
   has reached the cutoff but has not advanced past it.
   The current harness rule (strict-greater) forbids the
   refresh. *The blocker is the readiness rule, not the
   provider's data availability.* See § 3 above for the
   policy ambiguity this exposes.

2. **TEF — `source_behind_or_error`.** Vendor-side
   "possibly delisted" classification. Refreshing TEF
   currently yields zero rows from yfinance and the
   readiness predicate cannot fire while TEF is in the
   universe with this telemetry. **TEF is a separate
   issue from the equal-cutoff policy decision above.**
   Even if the equal-cutoff policy is later relaxed, TEF
   still needs triage independently: replacement ticker,
   exclusion policy, alternate provider, or K-universe
   rebuild without TEF.

## 5. Recommended next concrete actions

**No supervised refresh command is prepared by this PR**
per the operator discipline: when
`refresh_candidate_ready=false`, this evidence pass does
NOT emit an executable future refresh command block.

Concrete next actions the operator may pick from
(independent paths):

1. **Decide the equal-cutoff policy (the question
   surfaced in § 3).** Should the readiness rule treat
   `new_cache_date_range_end == current_as_of_date` (with
   `cache_date_range_end < current_as_of_date`) as
   sufficient for supervised refresh? A future phase
   (Phase 6I-N) with its own preflight, audit, and tests
   would amend the readiness coordinator + supervised
   refresh gate accordingly. **This PR does not change
   the rule.**

2. **Wait for the next trading day.** Without changing
   the rule, when the cutoff resolves to 2026-05-15 (or
   later) AND yfinance has published that trading day's
   bar for the 14 equities, re-run this readiness module.
   The 14 non-TEF equities would flip to
   `source_ready_for_refresh` (strict-greater satisfied)
   and the aggregate could then approach
   `refresh_candidate_ready=true` (modulo TEF).

3. **Triage TEF independently.** TEF has now persisted
   across Phase 6I-33 and Phase 6I-38 with the same
   "possibly delisted" yfinance telemetry (cache stuck at
   `2026-01-28`; `new_cache_date_range_end=null` in both
   runs). Options:
   - Confirm whether TEF is genuinely delisted on Yahoo
     (manually, via the operator's separate channels).
   - If delisted, decide on replacement ticker, exclusion
     policy, alternate provider, or K-universe rebuild
     without TEF.
   - Pin TEF's evaluation cutoff to its last available
     trading date (`2026-01-28`).
   - Until TEF is resolved, the aggregate predicate
     cannot reach `refresh_candidate_ready=true` while
     TEF is in the universe — and this is **separate**
     from the equal-cutoff policy in § 3. Even if the
     equal-cutoff policy is later relaxed, TEF still
     needs its own decision. Note that the downstream
     Phase 6I-22 adapter / Phase 6I-32 sandbox builder
     ALREADY accommodates TEF's earlier cutoff for
     sandbox proofs (sandbox binding cutoff 2026-01-28
     for TEF was used during Phase 6I-30), so a SPY-pilot
     refresh that excludes TEF from the K-universe is
     technically feasible if the operator decides to
     scope it that way.

4. **Continue parallel website-renderer / scoring work.**
   The SPY pilot remains parked. Other work (Phase 6I-37
   already-merged current-build signal surface → website
   renderer / Dash UI shell; researched scoring contract
   to replace the first-pass ranking rule) can proceed
   independently while waiting on source readiness.

## 6. Exact command executed

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

## 7. Production-root snapshot — 0/0/0/0/0 diff

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

## 8. No-production-activity confirmation

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

## 9. SPY remains parked

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
