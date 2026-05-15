# Phase 6I-54a: Local secondary-price cache rebuild planner for StackBuilder pilot universe

**Date:** 2026-05-15 (amendment-1 same day)
**Base commit (main):** `5106375` (Phase 6I-53 squash-merge)
**Branch:** `phase-6i-54a-stackbuilder-price-cache-rebuild-planner`
**Status:** Read-only planner. No production writes. **Do not merge** until operator approval.

`<PINNED_PYTHON> = C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`

---

## Amendment-1: portable tests + honest mixed-provenance reporting

Codex audit caught two blockers in the original Phase 6I-54a commit (`a55ef10`):

### Issue 1: production-state test was non-portable

`test_production_state_classification_matches_expected` hard-failed in a clean Codex worktree where `project/cache/results` is absent (1 failed / 16 passed). Amendment-1 fixes this in two ways:

1. The production-state test is renamed to `test_production_state_classification_skips_when_cache_absent` and **skips cleanly** via `pytest.skip(...)` when `cache/results` is missing OR when none of the 6 known-ready tickers have PKLs on disk. The smoke is now informational, never a hard failure.
2. A new **fixture-based test** `test_six_use_existing_and_nineteen_needs_source_refresh_against_fixture` pins the same 6/19 classification deterministically using only `tmp_path` fixtures. This test works identically in any worktree.

A separate test (`test_planner_works_when_cache_results_directory_missing`) explicitly pins that the **planner itself** produces a valid report when both cache dirs are missing — every default-universe ticker classifies as `needs_source_refresh` with an empty `provenance_summary`.

### Issue 2: evidence doc overclaimed uniform provenance

The original evidence doc said *all six* `use_existing_signal_cache` tickers were `producer_engine="signal_engine_cache_refresher"` / `engine_version="6E-5.0.0"`. The evidence JSON actually showed mixed provenance. The reality (verified by direct inspection of the per-row JSON):

| Producer engine | Engine version | Ticker count | Tickers |
|---|---|---|---|
| `signal_engine_cache_refresher` | `6E-5.0.0` | 2 | `SPY, JNJ` |
| `spymaster` | `1.0.0` | 4 | `AAPL, HD, MCD, WMT` |

Amendment-1 adds a `provenance_summary` block to the planner output that surfaces this honestly. The block carries `distinct_provenance_count` and a `groups[]` list with `(producer_engine, engine_version, ticker_count, tickers)` per group. The summary covers ONLY `use_existing_signal_cache` rows (verified by `test_provenance_summary_excludes_non_use_existing_rows`).

The 6 ready tickers remain `use_existing_signal_cache` candidates — Phase 6I-54a does NOT downgrade the four `spymaster/1.0.0` files to `manual_review` because both producer paths write Close prices into the same PKL shape (per the manifest's `params.price_source="Close"`). **However**, the planner now explicitly tells Phase 6I-54b:

> `Phase 6I-54b MUST load and verify each candidate file via the approved provenance/loader path (NOT raw pickle.load) and perform actual Close-series extraction per ticker. Files produced by different builders / engine versions are NOT silently treated as identical -- the writer should record per-ticker provenance in its own evidence.`

This is exposed both as a free-text field on `provenance_summary.phase_6i_54b_verification_requirement` and as a stable invariant in `future_write_contract` (already present pre-amendment-1).

### Files changed in amendment-1

- `project/stackbuilder_price_cache_rebuild_planner.py` — new `provenance_summary` block added to `build_price_cache_rebuild_plan` output. No other behaviour change.
- `project/test_scripts/test_stackbuilder_price_cache_rebuild_planner.py` — 6 new amendment-1 tests + the original production smoke renamed + reworked to skip cleanly when cache/results is absent.
- `project/md_library/shared/2026-05-15_PHASE_6I54A_STACKBUILDER_PRICE_CACHE_REBUILD_PLAN.md` (this doc) — amendment-1 section + Section 4 rewrite.
- `project/md_library/shared/2026-05-15_PHASE_6I54A_STACKBUILDER_PRICE_CACHE_REBUILD_PLAN_EVIDENCE.json` — regenerated.

**23 / 23 Phase 6I-54a tests pass** (17 original + 6 amendment-1). Combined Phase 6I-50/51/52/53/54a regression: 100 / 100. Production roots untouched (combined 83036, pre = post).

---

## 1. Purpose

Phase 6I-53 found the StackBuilder secondary price cache at `price_cache/daily/` missing entirely; all 25 Phase 6I-52 pilot tickers classified as `skip_missing_cache_would_fetch_yfinance`. Phase 6I-54a defines **exactly how to populate that cache without a network round-trip** by inspecting the existing local signal-engine cache at `cache/results/`, ticker by ticker. Phase 6I-54a does NOT write any file under `price_cache/daily/` (or any other production root); it produces the **plan** that Phase 6I-54b (a separately-authorized write phase) will consume.

## 2. Critical distinction — two caches, kept separate

| Cache | Path | What it is | Phase 6I-54a treatment |
|---|---|---|---|
| Signal-engine cache | `cache/results/<TICKER>_precomputed_results.pkl` + `<TICKER>_precomputed_results.pkl.manifest.json` | Produced by Phase 6E-5 `signal_engine_cache_refresher` ("optimizer_v1" scope, `params.price_source="Close"`). Manifest sidecar is plain JSON; PKL is a numpy/pandas binary. | **READ ONLY:** plain-JSON manifest sidecar is read (`Path.read_text` + `json.loads`). PKL body is NOT loaded — that's Phase 6I-54b's job. |
| StackBuilder secondary price cache | `price_cache/daily/<TICKER>.parquet` (and four sibling forms) | Checked by `stackbuilder.load_secondary_prices` (`stackbuilder.py:530-556`). If missing, StackBuilder falls through to `_fetch_secondary_from_yf` (live yfinance). | **DESTINATION:** Phase 6I-54a only enumerates expected paths + checks existence. Phase 6I-54b will write `Date + Close` rows here. |

The planner module is explicit about which cache it is touching at every step; the test suite pins the distinction (`test_cache_dirs_are_distinct`).

## 3. What was added

### Module

`project/stackbuilder_price_cache_rebuild_planner.py`

- Public entry: `build_price_cache_rebuild_plan(tickers=None, *, signal_cache_dir=None, stackbuilder_price_cache_dir=None) -> dict`.
- CLI: `--tickers`, `--signal-cache-dir`, `--stackbuilder-price-cache-dir`, `--output` (production-root path guard, including `price_cache/daily/`).
- Stable taxonomy: `ALL_RECOMMENDED_ACTIONS = (use_existing_signal_cache, needs_source_refresh, needs_network_fetch, manual_review)`.
- Per-row blocker codes: `no_signal_cache_pkl_found`, `signal_cache_manifest_sidecar_missing`, `signal_cache_manifest_unreadable`, `signal_cache_manifest_unexpected_shape`, `signal_cache_price_source_not_close`, `stackbuilder_price_cache_already_present`.
- Per-row fields include: `expected_stackbuilder_cache_paths` (5 paths mirroring `stackbuilder.load_secondary_prices`), `current_cache_status` (present/missing), `existing_stackbuilder_cache_path`, `signal_cache_pkl_present`, `signal_cache_manifest_present`, `signal_cache_price_source`, `signal_cache_producer_engine`, `signal_cache_engine_version`, `signal_cache_build_timestamp`, `transformation_possible_without_network`, `recommended_action`, `blocker_codes`.
- Aggregate report includes `counts_by_recommended_action`, `tickers_by_recommended_action`, and a `future_write_contract` block that documents what Phase 6I-54b must produce (destination root, parquet/csv formats, required columns `Date + Close`, one file per ticker, no network, no yfinance).
- Strict read-only contract: no `pickle` / `subprocess` / `yfinance` / `stackbuilder` / writer / engine top-level imports. Statically enforced by `test_no_forbidden_top_level_imports` + `test_module_source_has_no_pickle_load_call`.

### Tests

`project/test_scripts/test_stackbuilder_price_cache_rebuild_planner.py` — 23 focused tests (17 original + 6 amendment-1), all passing.

| # | Test | Pins |
|---|---|---|
| 1 | `test_schema_and_taxonomy_constants_are_stable` | Schema + 4-action taxonomy + cache-dir defaults. |
| 2 | `test_expected_cache_paths_match_stackbuilder_order` | Five candidate paths in correct order. |
| 3 | `test_caret_stripped_variant_for_index_ticker` | `^GSPC` → caret-stripped variants. |
| 4 | `test_cache_dirs_are_distinct` | `cache/results` vs `price_cache/daily` separation. |
| 5 | `test_missing_both_caches_needs_source_refresh` | No source PKL → `needs_source_refresh`. |
| 6 | `test_signal_cache_with_close_manifest_routes_to_use_existing` | Happy path → `use_existing_signal_cache`. |
| 7 | `test_existing_stackbuilder_cache_routes_to_manual_review` | `price_cache/daily/<T>` already present → `manual_review`. |
| 8 | `test_missing_manifest_routes_to_manual_review` | PKL without manifest → `manual_review`. |
| 9 | `test_non_close_price_source_routes_to_manual_review` | `price_source != "Close"` → `manual_review`. |
| 10 | `test_unreadable_manifest_routes_to_manual_review` | Bad JSON manifest → `manual_review`. |
| 11 | `test_aggregate_counts_consistent_with_rows` | Counts sum to ticker count; tickers-by-action lists match. |
| 12 | `test_default_universe_is_phase_6i_52_pilot` | Default universe = 25-ticker pilot, SPY first. |
| 13 | `test_no_forbidden_top_level_imports` | No `pickle` / `subprocess` / `yfinance` / `stackbuilder` / engine / writer top-level imports. |
| 14 | `test_module_source_has_no_pickle_load_call` | No `pickle.load(` or `pickle_load_compat(` call expression. |
| 15 | `test_output_path_guard_rejects_production_and_pcd` | `--output` rejects all 5 production roots AND `price_cache/daily/`. |
| 16 | `test_future_write_contract_is_well_formed` | Phase 6I-54b write contract carries destination, formats, required columns. |
| 17 | `test_production_state_classification_matches_expected` | 6/25 `use_existing_signal_cache` (SPY, AAPL, JNJ, WMT, HD, MCD); 19/25 `needs_source_refresh`; 0 `manual_review` / `needs_network_fetch`. |

Combined Phase 6I planner regression: **100 / 100 tests pass** (16 from 6I-50 + 23 from 6I-51 + 23 from 6I-52 + 15 from 6I-53 + 23 from 6I-54a).

## 4. Per-ticker planner summary (production state, 2026-05-15)

```
<PINNED_PYTHON> stackbuilder_price_cache_rebuild_planner.py \
    --output md_library/shared/2026-05-15_PHASE_6I54A_STACKBUILDER_PRICE_CACHE_REBUILD_PLAN_EVIDENCE.json
```

| Field | Value |
|---|---|
| `signal_cache_dir` | `cache/results` |
| `signal_cache_dir_exists` | `True` |
| `stackbuilder_price_cache_dir` | `price_cache/daily` |
| `stackbuilder_price_cache_dir_exists` | **`False`** |
| `ticker_count` | 25 |
| `use_existing_signal_cache` | **6** |
| `needs_source_refresh` | **19** |
| `needs_network_fetch` | 0 |
| `manual_review` | 0 |

### Tickers by recommended action

| Action | Tickers |
|---|---|
| `use_existing_signal_cache` | `SPY, AAPL, JNJ, WMT, HD, MCD` |
| `needs_source_refresh` | `MSFT, GOOGL, AMZN, NVDA, META, TSLA, AVGO, ORCL, ADBE, CRM, AMD, QCOM, CSCO, JPM, BRK-B, V, MA, PG, KO` |

For all six `use_existing_signal_cache` tickers, the planner confirmed:
- `cache/results/<TICKER>_precomputed_results.pkl` exists.
- `cache/results/<TICKER>_precomputed_results.pkl.manifest.json` exists and parses as JSON.
- `params.price_source = "Close"`.
- `transformation_possible_without_network = True`.

**Provenance is MIXED across the six tickers (post amendment-1 correction).** The original Phase 6I-54a evidence doc incorrectly claimed all six were `producer_engine="signal_engine_cache_refresher"` / `engine_version="6E-5.0.0"`. The actual on-disk distribution (verified by direct inspection of the per-row evidence JSON):

| Producer engine | Engine version | Ticker count | Tickers |
|---|---|---|---|
| `signal_engine_cache_refresher` | `6E-5.0.0` | **2** | `SPY, JNJ` |
| `spymaster` | `1.0.0` | **4** | `AAPL, HD, MCD, WMT` |

The four `spymaster/1.0.0` files are **legacy** outputs of the original Spymaster path; the two `6E-5.0.0` files are recent outputs of the Phase 6E-5 refresher. The planner does NOT downgrade the legacy files because both producers write Close-price PKLs (per the manifest contract), but **Phase 6I-54b must verify each candidate independently** via the approved provenance/loader path and per-ticker Close-series extraction. Mixed provenance is recorded in the `provenance_summary` block of the evidence JSON.

For all 19 `needs_source_refresh` tickers:
- `cache/results/<TICKER>_precomputed_results.pkl` is missing.
- `blocker_codes` carries `no_signal_cache_pkl_found`.
- `transformation_possible_without_network = False`.

## 5. Recommended next write path

### Phase 6I-54b — supervised price-cache write (for the 6 ready tickers)

For SPY, AAPL, JNJ, WMT, HD, MCD the path is clear and **does not require any network access**. The future-write contract baked into the Phase 6I-54a planner output:

```json
{
  "destination_root": "price_cache/daily",
  "output_format_primary": "parquet",
  "output_format_fallback": "csv",
  "required_columns": ["Date", "Close"],
  "files_per_ticker": 1,
  "uses_network": false,
  "uses_yfinance": false,
  "transformation_source": "cache/results/<TICKER>_precomputed_results.pkl (read via the repo's verified pickle/provenance loader; NOT raw pickle.load)"
}
```

Documented template for Phase 6I-54b's CLI:

```
<PINNED_PYTHON> stackbuilder_price_cache_writer.py \
    --tickers SPY,AAPL,JNJ,WMT,HD,MCD \
    --signal-cache-dir cache/results \
    --stackbuilder-price-cache-dir price_cache/daily \
    --format parquet \
    --write
```

Phase 6I-54b should:
1. Use the repo's verified pickle/provenance loader (NOT raw `pickle.load`) to read each cache PKL.
2. Extract the Date-indexed Close series.
3. Emit `price_cache/daily/<TICKER>.parquet` with `Date` and `Close` columns.
4. Re-run the Phase 6I-53 preflight after write to confirm `pass_count` rises from 0/25 to 6/25.

### Separate authorization required — Phase 6E-5 source refresh (for the 19 missing tickers)

The remaining 19 tickers (MSFT, GOOGL, AMZN, NVDA, META, TSLA, AVGO, ORCL, ADBE, CRM, AMD, QCOM, CSCO, JPM, BRK-B, V, MA, PG, KO) cannot be served from local data alone. The `signal_engine_cache_refresher` (Phase 6E-5) is the documented path to populate `cache/results/` for them; that refresher uses yfinance internally and is **its own explicitly-authorized phase**. Phase 6I-54a does NOT pre-authorize it; the operator must run the refresher for each missing ticker in a separate session before a future Phase 6I-54b extension could include them.

## 6. Production-roots evidence pass

```
<PINNED_PYTHON> stackbuilder_price_cache_rebuild_planner.py \
    --output md_library/shared/2026-05-15_PHASE_6I54A_STACKBUILDER_PRICE_CACHE_REBUILD_PLAN_EVIDENCE.json
```

**Pre/post production-root inventory:**

| Root | Pre-run | Post-run | Diff |
|---|---|---|---|
| `cache/results` | 3239 | 3239 | 0 |
| `cache/status` | 1634 | 1634 | 0 |
| `output/research_artifacts` | 35 | 35 | 0 |
| `output/stackbuilder` | 5229 | 5229 | 0 |
| `signal_library/data/stable` | 72899 | 72899 | 0 |
| `price_cache/daily` | missing | missing | 0 |
| **Combined (5 documented)** | **83036** | **83036** | **0** |

## 7. What this PR does NOT do

- Does NOT write to `price_cache/daily/` (the destination cache stays empty).
- Does NOT load any `cache/results/<TICKER>_precomputed_results.pkl` content. Manifest sidecars are read as plain JSON; PKL bodies are untouched.
- Does NOT invoke yfinance, the source-cache refresher, the stable-promotion writer, the Confluence patch writer, the pipeline runner, OnePass, ImpactSearch, TrafficFlow, Spymaster, or StackBuilder.
- Does NOT pre-authorize Phase 6I-54b. Phase 6I-54b will be a separate explicit prompt with its own evidence pass.
- Does NOT import `pickle`, `subprocess`, `yfinance`, `dash`, or any engine/writer module (statically enforced).
- Does NOT modify the Phase 6I-50 / 6I-51 / 6I-52 / 6I-53 modules.

## 8. Next step

**Phase 6I-54b — supervised price-cache write for the 6 ready tickers** (SPY, AAPL, JNJ, WMT, HD, MCD). The transformation requires no network round-trip. The future-write contract is locked in this PR's evidence JSON.

After Phase 6I-54b lands, re-running the Phase 6I-53 preflight should flip the verdict from `pass_count=0, skip_count=25` to `pass_count=6, skip_count=19`, unblocking the Phase 6I-55 supervised StackBuilder batch for the 6 ready tickers.

**Out of scope for Phase 6I-54a/b:** populating the remaining 19 tickers requires a separate Phase 6E-5 source-refresh authorization (network-using). The 25-ticker locked pilot universe is preserved; the rollout simply proceeds in two waves (6 ready now, 19 once the refresher has run for them).
