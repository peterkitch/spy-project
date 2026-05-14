# Phase 6I-35: website-ready Confluence export package + reader contract

Sprint date: **2026-05-14**.
Branch: `phase-6i-35-website-confluence-export-package`.
Doc: this file.

Phase 6I-34 (PR #251) merged the **first read-only multi-ticker TrafficFlow-style Confluence ranking/export contract**. The current SPY pilot's source-refresh path is parked (Phase 6I-33 verdict `refresh_candidate_ready=false`) and production Confluence artifacts do not yet carry the Phase 6I-20 multi-window fields — so the 6I-34 export run reports 0 eligible / 2 blocked daily-only rows in production today.

Phase 6I-35 is the obvious next step: **build the data contract that a future website / public API will actually serve.** This phase wraps the Phase 6I-34 export and normalizes it into a stable, versioned JSON envelope the UI can consume. No styling, no UI, no chart rendering — only the data shape and the honest empty-state.

---

## 1. Why this is the next step after Phase 6I-34

The Product North Star (CLAUDE.md § 6, post-Phase-6I-33): the final Confluence product is a **multi-ticker, TrafficFlow-style ranking board** over a large ticker universe using all five canonical windows. Phase 6I-34 produced the per-ticker classification + first-pass ranking. Phase 6I-35 produces the **website-ready data contract** that a UI / API can render against — including:

- A stable schema version envelope so future website code can pin a version.
- Normalized ranking rows (with explicit `rank` field) and blocked rows.
- A per-ticker `ticker_details` map for detail-page rendering.
- Three rollup summaries (`chart_readiness_summary`, `freshness_summary`, `issue_summary`) so the UI can render dashboard chrome without re-scanning the rows.
- An **honest `empty_state` object** that surfaces today's reality (no eligible tickers because production artifacts don't carry the Phase 6I-20 fields) instead of hiding it.
- A `remaining_limitations` list naming every gap the operator still needs to close.

This unblocks website launch work without waiting on yfinance / the SPY pilot. A future website reader / view layer is the next phase after this one.

---

## 2. How this helps website launch without waiting on SPY / yfinance

The Phase 6I-33 source-refresh predicate `refresh_candidate_ready=false` is a clock-time wait (yfinance one day behind cutoff). The SPY pilot's refresh → promote → patch-write sequence cannot proceed until yfinance publishes the next trading day. **Meanwhile this phase's deliverable does not depend on any of that** — it consumes the same Phase 6I-34 output that already runs against production, and produces a website-ready package that:

- Honestly reports the empty state today (no eligible rankings) AND
- Will automatically produce one rank-eligible row for SPY once the SPY pilot lands (no further code work needed in this layer), AND
- Will automatically rank the broader universe once their artifacts acquire the Phase 6I-20 fields.

The UI / API layer that consumes this contract can be built in parallel.

---

## 3. Module

`project/confluence_website_export_package.py` (new, ~540 lines). One public function `build_website_export_package(...)` + CLI. Read-only — every external call is reached via the Phase 6I-34 export's existing read paths. The Phase 6I-34 `build_multiwindow_ranking_export` is the only consumed entry point and is reached through an injection seam so tests can drive package shape via fakes.

Hard contract pins (AST-scanned by tests):

- `write=True` never passed to any callable.
- `PRJCT9_AUTOMATION_WRITE_AUTH` never read or set.
- No top-level imports of yfinance / dash / subprocess / live engines / writers / refreshers / pipeline runners.
- No raw `pickle.load`. No `.resample()` / `.ffill()`.
- No on-disk writes (no `Path.write_text` / `Path.write_bytes` / `json.dump` call sites).

---

## 4. Schema

Top-level envelope (`schema_version="confluence_website_export_v1"`):

| Field | Type | Notes |
|---|---|---|
| `schema_version` | str | Stable `"confluence_website_export_v1"` |
| `generated_at` | ISO timestamp | UTC |
| `source` | str | `"confluence_multiwindow_ranking_export"` |
| `artifact_root` | str | from the underlying export |
| `cache_dir` | str | nullable; from CLI |
| `universe_mode` | str | one of `explicit_tickers` / `all_artifacts` / `from_stackbuilder_universe` |
| `inspected_count` / `eligible_count` / `blocked_count` | int × 3 | from the underlying export |
| `has_eligible_rankings` | bool | `eligible_count > 0` |
| `ranking_rows` | list | normalized eligible rows (see § 4.1) |
| `blocked_rows` | list | normalized blocked rows (see § 4.2) |
| `ticker_details` | dict | per-ticker detail map keyed by ticker (see § 4.3) |
| `chart_readiness_summary` | dict | counts (see § 4.4) |
| `freshness_summary` | dict | counts by freshness_status |
| `issue_summary` | dict | counts by issue code + ranking_blocked_reason |
| `empty_state` | dict | honest empty-state object when `eligible_count=0`; `null` otherwise |
| `remaining_limitations` | list | underlying + Phase 6I-35 gaps |

### 4.1 Normalized ranking row

`rank` / `ticker` / `latest_overall_direction` / `windows_firing_count` / `windows_total=5` / `k_cells_firing` / `k_cells_total=60` / `all_windows_firing` / `all_members_firing_windows` / `strongest_window` / `strongest_K` / `strongest_total_capture_pct` / `strongest_sharpe_ratio` / `total_capture_pct_sum` / `avg_sharpe_ratio` / `trigger_days_sum` / `chart_ready_available` / `freshness_status` / `issue_codes`.

### 4.2 Normalized blocked row

`ticker` / `ranking_blocked_reason` / `data_status` / `freshness_status` / `chart_ready_available` / `chart_blocker` / `issue_codes`.

### 4.3 Ticker details (per ticker)

`ticker` / `rank_eligible` / `artifact_path` / `data_status` / `ranking_blocked_reason` / `per_window_summary` / `build_wide_window_alignment` / `chart_ready_available` / `chart_ready_source` / `chart_row_count` / `chart_blocker` / `freshness_status` / `issue_codes` / `detail_available` / `detail_blocker`.

**No fabrication.** When a row is blocked (or otherwise missing per-window data), `per_window_summary` is `null` and `build_wide_window_alignment` is `null`. `detail_available=False` is paired with a stable `detail_blocker` string carrying the underlying `ranking_blocked_reason` (or `no_phase_6i20_payload` as a fallback).

### 4.4 Chart-readiness summary

```
{
  "ready_count": int,
  "unavailable_count": int,
  "by_source": {"confluence_artifact": N, "signal_engine_cache": N, "unavailable": N}
}
```

### 4.5 Empty state

```
{
  "headline": "No tickers are rank-eligible yet.",
  "reason": <stable string>,
  "next_action": <stable string>,
  "blocked_count": int,
  "sample_blockers": [
    {"ticker": str, "ranking_blocked_reason": str, "data_status": str},
    ... up to 5
  ]
}
```

The `reason` distinguishes two states:

- **inspected_count=0** → `"Universe discovery returned zero tickers. Supply --tickers, point --artifact-root at a populated Confluence directory, or use a different universe mode."`
- **inspected_count>0 AND eligible_count=0** → `"Production Confluence artifacts do not yet carry the Phase 6I-20 multi-window fields (per_window_k_metrics + build_wide_window_alignment + multiwindow_k_engine_payload_metadata). The single-ticker SPY pilot through refresh / promote / Confluence-patch-write is still pending."`

When `eligible_count > 0`, `empty_state` is `null`.

---

## 5. Universe modes

| Flag | universe_mode | Notes |
|---|---|---|
| `--tickers SPY,QQQ,AAPL` | `explicit_tickers` | Explicit list |
| `--all-artifacts` | `all_artifacts` | Lists `<artifact_root>/confluence/*` directory names |
| `--from-stackbuilder-universe` | `from_stackbuilder_universe` | Lists `<artifact_root>/../stackbuilder/*` directory names (lazy; does NOT run StackBuilder) |

Optional `--top-n N` caps discovered counts.

---

## 6. Current production result

Ran `--all-artifacts --artifact-root output/research_artifacts --cache-dir cache/results`:

| Field | Value |
|---|---|
| `schema_version` | `"confluence_website_export_v1"` |
| `inspected_count` | 2 |
| `eligible_count` | **0** |
| `blocked_count` | **2** |
| `has_eligible_rankings` | **false** |
| `universe_mode` | `"all_artifacts"` |
| `empty_state.headline` | `"No tickers are rank-eligible yet."` |
| `empty_state.reason` | the Phase-6I-20-not-yet-shaped explanation |
| `chart_readiness_summary` | `{ready_count: 1 (SPY via signal_engine_cache), unavailable_count: 1 (_GSPC)}` |
| `freshness_summary` | `{unknown: 2}` |
| `issue_summary.by_ranking_blocked_reason` | `{daily_only: 2}` |
| `ticker_details` keys | `SPY`, `_GSPC` |

This is the **expected honest output** for current sprint state: production Confluence artifacts still carry only the Phase 6C daily-baseline shape. SPY has a cache PKL so its `chart_ready_source=signal_engine_cache` fires; `_GSPC` lacks one (or its filename differs from the canonical pattern) so its chart-readiness is unavailable. **Zero production data writes** during the evidence run.

---

## 7. Tests added (20 new)

`project/test_scripts/test_confluence_website_export_package.py` (new). Each test uses fakes for the underlying Phase 6I-34 export:

| # | Test | Pins |
|---|---|---|
| 1 | Envelope carries stable `schema_version="confluence_website_export_v1"` | version contract |
| 2 | One eligible row → `rank=1` | rank assignment |
| 3 | Multiple eligible rows → sequential ranks `[1, 2, 3]` | rank ordering |
| 4 | Blocked rows preserve `ranking_blocked_reason` codes | blocked-row fidelity |
| 5 | `eligible_count=0` → honest `empty_state` with the Phase-6I-20-not-yet-shaped reason | empty-state honesty |
| 6 | `inspected_count=0` → distinct empty-state reason | universe-empty distinction |
| 7 | `chart_readiness_summary` counts ready vs unavailable | rollup correctness |
| 8 | `freshness_summary` counts statuses | rollup correctness |
| 9 | `issue_summary` counts issue codes AND blocked reasons | rollup correctness |
| 10 | `ticker_details.detail_available=False` for blocked rows | no-fabrication |
| 11 | No fabrication of `per_window_summary` for blocked rows (must be `null`) | no-fabrication |
| 12 | Eligible row's `ticker_details` carries `per_window_summary` | happy path |
| 13-15 | CLI rc=2 (missing universe / unknown flag / empty universe) | operator-surface sanity |
| 16 | CLI happy path against tmp_path → rc=0, JSON envelope correct | end-to-end CLI |
| 17 | No raw `pickle.load` | B12 scope |
| 18 | No forbidden top-level imports | strictly bounded |
| 19 | No on-disk write call sites (`write_text` / `write_bytes` / `json.dump`) | stdout-only contract |
| 20 | AST has no `write=True` keyword arg | belt-and-braces dry-run guard |

The repo-wide B12 raw-pickle static regression guard continues to pass without an allowlist entry.

---

## 8. Test results

```
Phase 6I-35 package tests           :  20 passed
Phase 6I-34 ranking-export tests    :  36 passed
Static regression guards            :   9 passed
                                    -----
Focused sweep                       :  65 passed in 4.18 s

py_compile                          : clean
git diff --check                    : clean
```

---

## 9. Remaining gaps

- **SPY source refresh still parked.** Phase 6I-33 verdict `refresh_candidate_ready=false`. Waits on a clock-time event (yfinance publishing 2026-05-14 data).
- **Production signal-library promotion still pending.** Requires the SPY refresh first; then Phase 6I-31 promotion writer authorization in a separate prompt.
- **Production Confluence patch write still pending.** Requires the promotion first; then Phase 6I-25 patch writer authorization in a separate prompt.
- **Website UI / reader / view layer is still pending.** This phase's JSON envelope is the contract the UI consumes; a future phase implements the UI / API.
- **Final researched scoring model still pending.** The underlying Phase 6I-34 first-pass ranking rule (`all_windows_firing` → ... → ticker) is intentionally transparent; a future phase replaces it.

---

## 10. Output behavior

Default: JSON to stdout. `--output <path>` is **NOT implemented in this phase** — writing the package to disk needs path-guard + atomic-write semantics that are out of scope here. Stdout-only keeps the contract strictly read-only. A future phase may add a guarded `--output` flag that writes only under `output/site_exports/` and rejects paths under production roots.

---

## 11. No-production-activity confirmation

| Activity | Performed? |
|---|---|
| Writer `--write` invocation (any writer) | **No** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` set | **No** |
| Source refresh | **No** |
| `yfinance` fetch | **No** |
| Production promotion (`signal_library_stable_promotion_writer`) | **No** |
| Confluence patch writer (`multiwindow_k_confluence_patch_writer`) | **No** |
| `confluence_pipeline_runner` invocation | **No** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster batch execution | **No** |
| Production data write | **No** |

The Phase 6H-5 two-key writer gate, Phase 6I-9 supervised gate, Phase 6I-22 strict full-member-coverage gate, Phase 6I-25 patch-writer 5-gate cascade (including `_writer_plan_payload_is_consistent`), Phase 6I-28 close-source fallback contract, Phase 6I-29 exact-date member alignment, Phase 6I-30 interval-native close builder, Phase 6I-31 promotion writer 5-gate cascade + transactional rollback, Phase 6I-32 fresh-staging readiness harness, Phase 6I-33 source-refresh readiness module, and Phase 6I-34 multi-ticker ranking export are all unchanged in runtime contract.

---

## 12. Reference paths

- Module: `project/confluence_website_export_package.py` (new).
- Tests: `project/test_scripts/test_confluence_website_export_package.py` (new; 20 tests).
- Phase 6I-34 evidence (predecessor — multi-ticker ranking export): `project/md_library/shared/2026-05-14_PHASE_6I34_MULTI_TICKER_CONFLUENCE_RANKING_EXPORT.md`.
- Phase 6I-33 evidence (SPY source-refresh readiness, parked): `project/md_library/shared/2026-05-14_PHASE_6I33_SPY_K_UNIVERSE_SOURCE_REFRESH_READINESS.md`.
- Temp evidence directory: `C:\Users\sport\AppData\Local\Temp\phase_6i35_website_confluence_export_package\` (OUTSIDE production roots, OUTSIDE the repo).
- CLAUDE.md § 6 — current sprint state.
