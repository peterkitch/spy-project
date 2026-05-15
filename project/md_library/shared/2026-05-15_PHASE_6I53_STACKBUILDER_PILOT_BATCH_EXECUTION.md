# Phase 6I-53: First supervised StackBuilder pilot batch execution

**Date:** 2026-05-15
**Base commit (main):** `3bb222e` (Phase 6I-52 squash-merge)
**Branch:** `phase-6i-53-stackbuilder-pilot-batch-execution`
**Status:** **Evidence-only.** Preflight gate stopped all 25 candidate StackBuilder commands. Zero StackBuilder runs executed. Production roots unchanged. **Do not merge** until operator approval.

`<PINNED_PYTHON> = C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`

---

## 1. Purpose + outcome

Phase 6I-53 was authorized by the operator to run the FIRST supervised StackBuilder pilot batch against the Phase 6I-52 25-ticker pilot universe, **subject to a preflight gate** that confirms each ticker has a local secondary-price cache on disk (so `stackbuilder.py`'s `_fetch_secondary_from_yf` yfinance fallback does NOT silently trigger).

**Outcome:** Zero tickers passed preflight. No StackBuilder command was invoked. Production roots remain bit-for-bit identical (combined 83036, pre = post, 0 / 0 / 0 / 0 / 0 across all 5 roots). The Phase 6I-50 + Phase 6I-51 reclassification verdicts are unchanged from pre-Phase-6I-53 state.

The Phase 6I-52 amendment-1 yfinance-fallback warning ("Phase 6I-53 must preflight local secondary-price-cache availability before running each command, because stackbuilder.py falls back to a live yfinance fetch when the local price source is missing") was load-bearing: without the preflight gate, this phase would have silently fetched 25 tickers' worth of yfinance data.

## 2. Critical authorization framing (preserved)

`stackbuilder.py` has **NO `--write` flag** and does **NOT use `PRJCT9_AUTOMATION_WRITE_AUTH`**. The Phase 6H-5 / 6I-25 / 6I-31 two-key gate applies to the Confluence patch writer / signal-library promotion writer / daily-board automation writer — NOT to StackBuilder. The only authorization gate on a StackBuilder invocation is the **operator's separate decision to actually run the command**. This operator prompt issued that authorization conditionally on preflight pass. Preflight did not pass; no command ran.

## 3. What was added

### Module

`project/confluence_stackbuilder_pilot_preflight.py` — a new read-only module that mirrors `stackbuilder.load_secondary_prices()` (`stackbuilder.py:530-556`) and emits a per-ticker preflight table without loading any parquet/csv content or invoking yfinance.

- Public entry: `build_preflight_table(tickers=None, *, price_cache_dir=None, env_overrides=None) -> dict`.
- CLI: `--tickers` (default: Phase 6I-52 pilot universe), `--price-cache-dir`, `--output` (production-root path guard).
- Pinned `PREFLIGHT_STATUS_PASS` / `PREFLIGHT_STATUS_SKIP_MISSING_CACHE` taxonomy.
- Probes the five candidate cache paths in the same order as `stackbuilder.load_secondary_prices`:
  1. `<PCD>/<TICKER>.parquet`
  2. `<PCD>/<TICKER>.csv`
  3. `<PCD>/<TICKER_no_caret>.parquet`
  4. `<PCD>/<TICKER_no_caret>.csv`
  5. `<PCD>/<TICKER>/daily.parquet`
- `<PCD>` resolves to `$PRICE_CACHE_DIR` if set, else `price_cache/daily` (matches `stackbuilder.py:225`).

### Tests

`project/test_scripts/test_confluence_stackbuilder_pilot_preflight.py` — 15 focused tests, all passing.

| # | Test | Pins |
|---|---|---|
| 1 | `test_schema_and_status_constants_are_stable` | Schema + taxonomy constants. |
| 2 | `test_candidate_paths_match_stackbuilder_order` | Five candidate paths match `stackbuilder.load_secondary_prices` order. |
| 3 | `test_candidate_paths_caret_stripped_for_index_ticker` | `^GSPC` → caret-stripped variants resolve to `GSPC.parquet` / `GSPC.csv`. |
| 4 | `test_pass_when_any_candidate_exists` | A single `SPY.parquet` classifies SPY as pass. |
| 5 | `test_pass_via_subdirectory_form` | The fifth candidate `<T>/daily.parquet` form is detected. |
| 6 | `test_pass_via_caret_stripped_form` | Caret-stripped variant classifies pass. |
| 7 | `test_skip_when_no_candidate_exists` | Empty cache dir → skip-missing-cache + `would_fetch_yfinance=True`. |
| 8 | `test_skip_when_price_cache_dir_does_not_exist` | Missing cache dir entirely → every ticker skips. |
| 9 | `test_aggregate_counts_consistent_for_mixed_universe` | Per-ticker classification + aggregate counts + sorted pass/skip lists. |
| 10 | `test_env_override_honored` | Injectable `env_overrides` controls the `PRICE_CACHE_DIR` resolution. |
| 11 | `test_explicit_price_cache_dir_overrides_env` | Explicit kwarg beats env var. |
| 12 | `test_default_universe_is_phase_6i_52_pilot` | Default universe = Phase 6I-52 25-ticker pilot, SPY first. |
| 13 | `test_no_forbidden_top_level_imports` | No `subprocess` / `yfinance` / writer-module / engine-module / `stackbuilder` top-level imports. |
| 14 | `test_output_path_guard_rejects_production_root_paths` | `--output` rejects all 5 production-root paths. |
| 15 | `test_production_state_all_pilot_tickers_currently_skip` | Against the real on-disk state: 0/25 pass, 25/25 skip — the actual Phase 6I-53 authorization gate. |

Combined Phase 6I planner regression: **77 / 77 tests pass** (16 from 6I-50 + 23 from 6I-51 + 23 from 6I-52 + 15 from 6I-53).

## 4. Preflight verdict (production state, 2026-05-15)

```
<PINNED_PYTHON> confluence_stackbuilder_pilot_preflight.py \
    --output md_library/shared/2026-05-15_PHASE_6I53_PREFLIGHT.json
```

| Field | Value |
|---|---|
| `price_cache_dir_used` | `price_cache\daily` (Windows; via `PRICE_CACHE_DIR` env-var fallback to the default) |
| `price_cache_dir_exists` | **`False`** |
| `ticker_count` | 25 |
| `pass_count` | **0** |
| `skip_count` | **25** |
| `tickers_passing_preflight` | `[]` |
| `tickers_skipped_missing_cache` | All 25: `AAPL, ADBE, AMD, AMZN, AVGO, BRK-B, CRM, CSCO, GOOGL, HD, JNJ, JPM, KO, MA, MCD, META, MSFT, NVDA, ORCL, PG, QCOM, SPY, TSLA, V, WMT` |

Every row carries `local_price_cache_available=false`, `resolved_cache_path=null`, `would_fetch_yfinance=true`, `preflight_status="skip_missing_cache_would_fetch_yfinance"`.

The five candidate paths checked per ticker (none of which existed):

```
price_cache/daily/<TICKER>.parquet
price_cache/daily/<TICKER>.csv
price_cache/daily/<TICKER_no_caret>.parquet
price_cache/daily/<TICKER_no_caret>.csv
price_cache/daily/<TICKER>/daily.parquet
```

## 5. StackBuilder execution log

| Field | Value |
|---|---|
| Tickers attempted | 0 |
| Tickers succeeded | 0 |
| Tickers failed | 0 |
| Commands executed | 0 |
| Reason no commands executed | Zero tickers passed preflight. Per the Phase 6I-53 operator instructions ("If zero tickers pass preflight, stop with evidence-only PR. Do not run StackBuilder."), no StackBuilder command was invoked. |

## 6. Production-root accounting

| Root | Pre-run | Post-run | Diff |
|---|---|---|---|
| `cache/results` | 3239 | 3239 | 0 |
| `cache/status` | 1634 | 1634 | 0 |
| `output/research_artifacts` | 35 | 35 | 0 |
| `output/stackbuilder` | 5229 | 5229 | 0 |
| `signal_library/data/stable` | 72899 | 72899 | 0 |
| **Combined** | **83036** | **83036** | **0** |

`md_library/shared/` is the only directory that gained files (the preflight JSON + post-run planner JSONs + evidence doc + evidence JSON) — explicitly outside every production root.

## 7. Phase 6I-50 / 6I-51 reclassification (post Phase 6I-53)

Both planners re-ran read-only against the unchanged production state. Verdicts identical to pre-Phase-6I-53.

### Phase 6I-50 launch planner

```
<PINNED_PYTHON> confluence_large_universe_launch_planner.py \
    --all-artifacts \
    --artifact-root output/research_artifacts \
    --cache-dir cache/results \
    --signal-library-dir signal_library/data/stable \
    --stackbuilder-root output/stackbuilder \
    > md_library/shared/2026-05-15_PHASE_6I53_POST_RUN_6I50_LAUNCH_PLAN.json
```

| Ticker | `recommended_next_action` |
|---|---|
| `SPY` | `already_board_ranked` |
| `_GSPC` | `blocked_missing_inputs` |

### Phase 6I-51 rollout batch planner

```
<PINNED_PYTHON> confluence_large_universe_rollout_batch_planner.py \
    --planner-json md_library/shared/2026-05-15_PHASE_6I53_POST_RUN_6I50_LAUNCH_PLAN.json \
    --output md_library/shared/2026-05-15_PHASE_6I53_POST_RUN_6I51_ROLLOUT_PLAN.json
```

| Batch | Tickers |
|---|---|
| `board_render_now` | `['SPY']` |
| `partial_artifact_write_candidates` | `[]` |
| `strict_artifact_write_candidates` | `[]` |
| `source_refresh_candidates` | `[]` |
| `signal_library_rebuild_or_promotion_candidates` | `[]` |
| `stackbuilder_rerun_candidates` | `[]` |
| `blocked_or_manual_review` | `['_GSPC']` |

This matches the pre-Phase-6I-53 state exactly (recorded in `2026-05-15_PHASE_6I51_ROLLOUT_BATCH_PLAN_EVIDENCE.json`). The reclassification reaffirms that no production output changed during Phase 6I-53.

## 8. No-production-activity confirmation

- No `--write` flag. No `PRJCT9_AUTOMATION_WRITE_AUTH`. No `--allow-partial-payload-plan`.
- No yfinance fetch (the preflight gate explicitly stopped any code path that could have triggered one).
- No `signal_engine_cache_refresher` execution.
- No `signal_library_stable_promotion_writer` execution.
- No `multiwindow_k_confluence_patch_writer` execution.
- No `confluence_pipeline_runner` execution.
- No `daily_board_automation_writer` / `daily_board_automation_executor` execution.
- No StackBuilder execution (zero candidate commands invoked).
- No OnePass / ImpactSearch / TrafficFlow / Spymaster batch execution.
- No `subprocess` in the preflight module (statically enforced by `test_no_forbidden_top_level_imports`).

## 9. Files added (4)

- `project/confluence_stackbuilder_pilot_preflight.py` — new reusable preflight module.
- `project/test_scripts/test_confluence_stackbuilder_pilot_preflight.py` — 15 focused tests.
- `project/md_library/shared/2026-05-15_PHASE_6I53_STACKBUILDER_PILOT_BATCH_EXECUTION.md` (this doc).
- `project/md_library/shared/2026-05-15_PHASE_6I53_STACKBUILDER_PILOT_BATCH_EXECUTION_EVIDENCE.json` — consolidated evidence (preflight + execution log + production-root diff + 6I-50/51 reclassification + no-production-activity confirmation).

Three supporting evidence JSONs also land in `md_library/shared/`:

- `2026-05-15_PHASE_6I53_PREFLIGHT.json` — raw preflight table.
- `2026-05-15_PHASE_6I53_POST_RUN_6I50_LAUNCH_PLAN.json` — Phase 6I-50 verdict post-Phase-6I-53.
- `2026-05-15_PHASE_6I53_POST_RUN_6I51_ROLLOUT_PLAN.json` — Phase 6I-51 verdict post-Phase-6I-53.

## 10. Next step

**The cache-rebuild gate is the prerequisite for Phase 6I-54 supervised batch execution.** The Phase 6I-53 preflight stopped 25 silent yfinance fetches; the next operator phase must rebuild the local secondary-price cache so the preflight gate flips from `skip_count=25` to `pass_count=25` (or to whatever subset is operationally desirable).

The cache-rebuild path is **out of scope for Phase 6I-53**. It is its own explicitly-authorized phase, conceptually parallel to (or downstream of) the existing Phase 6E-5 `signal_engine_cache_refresher` writer. Candidate next phases:

1. **Phase 6I-54a — local secondary-price cache rebuild plan.** A read-only planner that identifies what data is needed at `price_cache/daily/`, where it comes from, and what the supervised write path looks like. The Phase 6I-43 source-refresh policy v2 work may apply directly. Network-touching paths (yfinance, alternative providers) need explicit operator authorization.
2. **Phase 6I-54b — supervised cache-rebuild write.** Once the rebuild plan is locked, an explicitly-authorized write phase populates `price_cache/daily/` (or wherever `PRICE_CACHE_DIR` points). After this, re-running Phase 6I-53's preflight should show `pass_count > 0`.
3. **Phase 6I-55 — supervised StackBuilder pilot batch (retry).** With cache populated, re-run Phase 6I-53 preflight + supervised batch. Each passing-preflight ticker runs the locked Phase 6I-52 StackBuilder command; per-ticker evidence is captured; Phase 6I-50 + 6I-51 are re-run to surface the post-rollout reclassification.

The Phase 6I-52 locked policy + 25-ticker pilot universe + Phase 6I-53 preflight module remain valid building blocks for whatever the next phase looks like. The pilot universe and locked command shape do NOT need to be revised — only the cache gap needs to close.
