# Phase 6I-34: multi-ticker TrafficFlow-style Confluence ranking/export contract

Sprint date: **2026-05-14**.
Branch: `phase-6i-34-multi-ticker-confluence-ranking-export`.
Doc: this file.

Phase 6I-33 closed with the SPY K-universe source-refresh predicate `refresh_candidate_ready=false` (yfinance one trading day behind cutoff). Rather than wait idly on yfinance, this phase continues product development by building the **multi-ticker, TrafficFlow-style, multi-window Confluence ranking/export layer** that the website ultimately needs. The single-ticker SPY chain is the building block; the multi-ticker layer is the destination.

**SPY note:** the single-ticker SPY source-refresh / promote / write path is **parked, not abandoned**. It will resume when source-readiness flips. Nothing in this phase touches the SPY refresh / promote / Confluence-patch-write surfaces.

---

## 0. TL;DR

| Check | Result |
|---|---|
| Production roots mutated | **No** |
| New module | `project/confluence_multiwindow_ranking_export.py` — read-only multi-ticker ranking/export |
| Tests added | **19 new**, focused tmp_path fixtures + fakes; all 19 passed |
| Focused 13-way sweep | **331 passed in 11.95 s** |
| Production-data verdict | 2 inspected (SPY + _GSPC); **0 eligible / 2 blocked** with `ranking_blocked_reason=daily_only` (the existing Confluence artifacts predate Phase 6I-20 multi-window fields) |
| SPY source-refresh path | **PARKED** (Phase 6I-33 verdict `refresh_candidate_ready=false` unchanged) |
| Phase 6I-25 / 6I-28 / 6I-29 / 6I-30 / 6I-31 / 6I-32 / 6I-33 contracts | All untouched |

---

## 1. Why we are continuing development while SPY source-readiness waits

Phase 6I-33 evidence (PR #250, merged at `fcf277a`) recorded `refresh_candidate_ready=false` because yfinance has data only through 2026-05-13 while the resolved cutoff is 2026-05-14 (one trading day behind). The next operational event in the single-ticker SPY path is a **clock-time wait** until yfinance publishes the 2026-05-14 trading day; that wait is not an engineering action.

Meanwhile, the **multi-ticker ranking board is the final product**, not a downstream of the SPY pilot. The export contract (this PR) and the website reader/view layer (a future phase) can be built in parallel with the SPY pilot's wait state. When the SPY pilot eventually flips through refresh → promote → Confluence-patch-write, the multi-ticker export will already know how to consume the resulting artifacts. When the broader universe's artifacts later acquire the Phase 6I-20 fields, the export will rank them automatically without further code work.

**The Phase 6I-34 spec is explicit: SPY remains the pilot/proof path. This PR adds the multi-ticker layer; it does not abandon SPY.**

---

## 2. How this maps to the old TrafficFlow workflow

The legacy `trafficflow.py` produced a single ranking/export table over a configured universe, scoring each ticker on Group-A signal-breadth + Group-B performance-quality metrics for the daily window only.

Phase 6I-34's `confluence_multiwindow_ranking_export.py` is the **multi-window rebuild** of that ranking layer:

| Old TrafficFlow | Phase 6I-34 Confluence multi-window export |
|---|---|
| Daily window only | All 5 canonical windows (1d / 1wk / 1mo / 3mo / 1y) per ticker |
| Group-A signal-breadth (agreement_ratio, etc) | `windows_firing` / `windows_firing_count` / `all_windows_firing` / `all_members_firing_windows` |
| Group-B performance-quality (Sharpe, capture, trigger days) | `strongest_*` + `total_capture_pct_sum` + `avg_sharpe_ratio` + `trigger_days_sum` |
| Per-row latest signal | `latest_overall_direction` + `buy_signal_count` / `short_signal_count` / `none_signal_count` / `missing_signal_count` |
| Per-row blocker (eligibility) | `rank_eligible` + `ranking_blocked_reason` (5+ stable reason codes) + `data_status` |
| (none) | `chart_ready_available` + `chart_ready_source` + `chart_row_count` + `chart_blocker` |
| (none) | `freshness_status` (`fresh` / `stale` / `unknown`) |

Stay aligned with the existing **OnePass / ImpactSearch / StackBuilder / TrafficFlow / MultiTimeframe / Confluence** script family. Future module names should make data provenance obvious. The Phase 6I-34 module name reflects exactly its lineage: a Confluence (final ranking) layer that consumes multi-window K (Phase 6I-22..30) data.

---

## 3. Export schema

Top-level `MultiTickerRankingExportReport` JSON:

| Field | Type | Notes |
|---|---|---|
| `generated_at` | ISO timestamp | UTC |
| `artifact_root` | str | the supplied artifact root path |
| `inspected_count` | int | total tickers asked about |
| `eligible_count` | int | rank-eligible rows |
| `blocked_count` | int | blocked rows |
| `ranking_rows` | list | RANK-SORTED eligible rows |
| `blocked_rows` | list | blocked rows (input order) |
| `summary` | dict | aggregate counts + `ranking_rule` text + `k_cells_total_per_ticker=60` |
| `remaining_limitations` | list of strings | 6 stable string entries naming open gaps |

Each `PerTickerRankingRow`:

| Field | Type | Notes |
|---|---|---|
| `ticker` | str | uppercased |
| `artifact_path` | str | resolved `<TICKER>__MTF_CONSENSUS.research_day.json` if present, else daily-baseline path, else `null` |
| `artifact_last_date` | str | from artifact `generated_at` |
| `confluence_last_date` | str | from artifact `daily.last_date` |
| `data_status` | str | one of `full_60_cell` / `incomplete_multiwindow` / `daily_only` / `missing` / `unreadable` |
| `freshness_status` | str | one of `fresh` / `stale` / `unknown` |
| `rank_eligible` | bool | True ONLY when `data_status=full_60_cell` AND all 5 canonical windows present in `build_wide_window_alignment` |
| `ranking_blocked_reason` | str | one of 8 stable reason codes; `null` when eligible |
| `windows_available` / `windows_firing` / `all_windows_firing` | list / list / bool | window-level firing summary |
| `k_cells_available` / `k_cells_firing` / `k_cells_total` | int / int / int (=60) | cell-level firing summary |
| `all_members_firing_windows` | list of windows | from `build_wide_window_alignment` |
| `strongest_window` / `strongest_K` / `strongest_total_capture_pct` / `strongest_sharpe_ratio` | various | the highest-capture cell |
| `total_capture_pct_sum` / `avg_sharpe_ratio` / `trigger_days_sum` | float / float / int | cell-level aggregates |
| `latest_overall_direction` | str | "Buy" / "Short" / "None" / null |
| `buy_signal_count` / `short_signal_count` / `none_signal_count` / `missing_signal_count` | int × 4 | cell-level direction counts |
| `chart_ready_available` / `chart_ready_source` / `chart_row_count` / `chart_blocker` | bool / str / int|null / str|null | chart-readiness verdict |
| `issue_codes` | list of strings | per-row diagnostic codes |

---

## 4. First-pass ranking rule

```
Sorted DESCENDING by:
  1. all_windows_firing (True > False)
  2. windows_firing_count
  3. k_cells_firing
  4. total_capture_pct_sum
  5. avg_sharpe_ratio
  6. trigger_days_sum
  7. -len(issue_codes)         (fewer issues > more)
  8. ticker                     ASC (stable tiebreak)
```

**This is a first-pass transparent contract, NOT the final investment model.** A future phase replaces it with a researched scoring contract. The current rule prefers:

- Rows where ALL 5 canonical windows have at least one firing K cell (multi-window confluence is the headline filter);
- then more total firing K cells;
- then larger aggregate capture;
- then stronger average Sharpe;
- then more trigger days;
- then cleaner data (fewer issue codes);
- then alphabetical ticker (stable tiebreak).

---

## 5. Blocked-row semantics

A row is **blocked** (`rank_eligible=False`) when ANY of the following are true. Each maps to a stable `ranking_blocked_reason` code; the runtime taxonomy `ALL_RANKING_BLOCKED_REASONS` has exactly **9** stable codes (pinned by `test_all_ranking_blocked_reasons_taxonomy_size`):

| Reason code | Cause |
|---|---|
| `artifact_missing` | No Confluence artifact JSON exists at the expected per-ticker path |
| `artifact_unreadable` | File exists but cannot be parsed as JSON / is not a dict |
| `invalid_payload_shape` | Phase 6I-20 fields present but contents fail the strict per-cell or alignment-entry validators (see § 5a below) |
| `missing_per_window_k_metrics` | Field absent |
| `missing_build_wide_window_alignment` | Field absent OR one of the 5 canonical windows missing |
| `missing_multiwindow_payload_metadata` | Field absent |
| `incomplete_60_cell_grid` | `per_window_k_metrics` is missing one or more canonical (K, window) cells, OR contains a duplicate canonical (K, window) cell |
| `daily_only` | Artifact has the Phase 6C baseline shape (`timeframes` list + `summary`) but no Phase 6I-20 multi-window fields — legitimate but predates the multi-window contract |
| `projected_or_bridge_only` | Reserved for a future projection-source field; not currently emitted by the classifier. Present in the stable taxonomy so a future phase can populate it without breaking downstream consumers. |

**No fabrication.** Missing data surfaces as a blocked row with an honest reason. The module NEVER invents 60-cell data, NEVER treats daily-only as multi-window, NEVER treats projected / bridge-only as true multi-window.

### 5a. Strict Phase 6I-20 validation (Phase 6I-34 amendment-1)

The Phase 6I-34 amendment-1 tightened the per-cell and per-alignment-entry validators so a ticker reaches `rank_eligible=True` only when its artifact truly satisfies the Phase 6I-20 future-artifact shape.

**`per_window_k_metrics` strict rules:**

- Must be a `list` / `tuple` of mappings.
- Every canonical `(K, window)` pair for K=1..12 × window in (1d/1wk/1mo/3mo/1y) must exist **exactly once**.
- **Duplicate canonical (K, window) cells are rejected** — the artifact cannot carry two different evaluations for the same cell. Surfaces as `incomplete_60_cell_grid`.
- Non-canonical extras (e.g. a K=13 cell or a `6mo` window) are **tolerated silently** as diagnostic extras — they do NOT substitute for missing canonical cells.
- Each canonical cell must include all five Phase 6I-20 required fields with the correct type:
  - `K` — int (already canonical-checked); bool rejected.
  - `window` — str (already canonical-checked).
  - `total_capture_pct` — int|float, NOT bool, NOT None, key MUST be present.
  - `sharpe_ratio` — key MUST be present; value MAY be `None` (engine documents this for undefined Sharpe) OR int|float (NOT bool).
  - `trigger_days` — int, NOT bool, NOT None, key MUST be present.
- Any per-cell type/key violation surfaces as `invalid_payload_shape`.

**`build_wide_window_alignment` strict rules:**

- Must be a `Mapping`.
- All 5 canonical windows must be present as keys.
- Each canonical window entry must be a `Mapping` carrying:
  - `all_members_firing` — strict `bool` (int rejected, even truthy/falsy int).
  - `firing_member_count` — strict `int` (NOT bool).
  - `total_member_count` — strict `int` (NOT bool).
- Any per-entry type/key violation surfaces as `invalid_payload_shape`.

---

## 6. Chart-readiness semantics

For each ticker the module emits a conservative chart-readiness verdict. **Phase 6I-34 amendment-1: `daily.dates` / `daily.date_index` alone is NOT chart-ready.** The website needs at least one chartable value per date.

| `chart_ready_source` | When it fires |
|---|---|
| `confluence_artifact` | Artifact carries a non-empty `chart_rows` list-of-mappings each with `date` + at least one chartable value field; OR a non-empty `daily.dates`/`daily.date_index` axis WITH at least one chartable value field (`close` / `target_close` / `Close` / `cumulative_capture_pct` / `signals` / `primary_signals`) |
| `signal_engine_cache` | A `<TICKER>_precomputed_results.pkl` exists in the supplied `cache_dir` (file existence only — the module does NOT read the PKL) |
| `unavailable` | Neither of the above; `chart_blocker` is either `"insufficient_chart_fields"` (artifact has a date axis but no value column) or `"no_chart_data_source"` (no usable artifact or cache source) |

When `chart_ready_source=signal_engine_cache`, `chart_row_count=null` because the module does not crack the cache PKL (that needs the central provenance loader, which is the future website reader's job, not this export).

---

## 7. Broad-universe modes

Three CLI universe-selection modes (mutually exclusive):

```
--tickers SPY,QQQ,AAPL                       # explicit list
--all-artifacts                               # discover via <artifact_root>/confluence/* dirs
--from-stackbuilder-universe                  # discover via <artifact_root>/../stackbuilder/* dirs
```

`--top-n N` caps the discovered count for the two discovery modes.

The StackBuilder discovery is intentionally **LAZY**: it lists directory names under `<artifact_root>/../stackbuilder/` and does NOT run StackBuilder, does NOT read leaderboard XLSX, does NOT enumerate K rows. This is the Phase 6I-34 spec's explicit "implement via a lazy helper / injection seam and document the limitation" choice.

---

## 8. Tests added (36 new — 19 original + 17 amendment-1)

`project/test_scripts/test_confluence_multiwindow_ranking_export.py` pins:

| # | Test | Pins |
|---|---|---|
| 1 | Full 60-cell payload → `rank_eligible=True` | happy path |
| 2 | Missing `per_window_k_metrics` → blocked | required Phase 6I-20 field |
| 3 | Daily-only artifact → `data_status=daily_only`; `ranking_blocked_reason=daily_only` | preserved Phase 6C semantics |
| 4 | 59-of-60 cells → blocked with `incomplete_60_cell_grid` | strict 60-cell contract |
| 5 | `build_wide_window_alignment` missing one canonical window → blocked | strict alignment contract |
| 6 | Ranking sort prefers `all_windows_firing=True` even over stronger numbers from a 4-window candidate | first-pass key correctness |
| 7 | Buy / Short / None directions surfaced honestly via `latest_overall_direction` | per-row direction contract |
| 8 | Chart-ready TRUE when artifact has `chart_rows` | `chart_ready_source=confluence_artifact` |
| 9 | Chart-ready FALSE with explicit `chart_blocker` when artifact lacks chart data AND `cache_dir` is None | conservative fallback |
| 10 | `--all-artifacts` discovery handles unreadable / no-artifact dirs safely | resilience |
| 11-14 | CLI rc=0 / rc=2 (missing universe / unknown flag / empty universe) | operator-surface sanity |
| 15-19 | Static guards: no raw `pickle.load`; no forbidden top-level imports (yfinance / dash / subprocess / writers / refreshers / pipeline runners / live engines); no `.resample()` / `.ffill()`; AST has no `write=True` kwarg; no on-disk `write_text` / `write_bytes` / `json.dump` call sites | bounded read-only contract |
| 20 | Cell missing `total_capture_pct` → blocked with `invalid_payload_shape` | amendment-1 strict cell validation |
| 21 | Cell missing `sharpe_ratio` KEY → blocked (None VALUE accepted; missing KEY rejected) | amendment-1 key-vs-value distinction |
| 22 | Cell missing `trigger_days` → blocked with `invalid_payload_shape` | amendment-1 strict cell validation |
| 23 | `total_capture_pct=True` (bool) → blocked | amendment-1 bool rejection |
| 24 | `sharpe_ratio=False` (bool) → blocked | amendment-1 bool rejection |
| 25 | `trigger_days=True` (bool) → blocked | amendment-1 bool rejection |
| 26 | `sharpe_ratio=None` is accepted (engine documents this) | amendment-1 engine-contract preservation |
| 27 | Alignment entry missing `firing_member_count` → blocked | amendment-1 strict alignment validation |
| 28 | Alignment `firing_member_count=True` (bool) → blocked | amendment-1 bool rejection |
| 29 | Alignment `all_members_firing=1` (int, not bool) → blocked | amendment-1 strict bool check |
| 30 | Duplicate canonical (K, window) cell → blocked with `incomplete_60_cell_grid` | amendment-1 duplicate rejection |
| 31 | 60 canonical + non-canonical extra → ELIGIBLE | amendment-1 extras tolerated |
| 32 | 59 canonical + non-canonical extra → BLOCKED (extras don't substitute) | amendment-1 strict substitution rule |
| 33 | `daily.dates` alone without value field → chart-unavailable with `insufficient_chart_fields` | amendment-1 chart-readiness honesty |
| 34 | `daily.dates` + `close` → chart-ready | amendment-1 chart-readiness happy path |
| 35 | `chart_rows` missing value field → chart-unavailable | amendment-1 chart-rows strict shape |
| 36 | `ALL_RANKING_BLOCKED_REASONS` has exactly **9** entries (incl. `projected_or_bridge_only`) | amendment-1 doc / runtime consistency pin |

The repo-wide B12 raw-pickle static regression guard continues to pass without an allowlist entry.

---

## 9. Test results

```
Phase 6I-34 ranking-export tests   :  36 passed (19 original + 17 amendment-1)
                                       (original 19 ran at PR open;
                                        focused-suite re-run after
                                        amendment-1 below)
```

The amendment-1 commit re-runs only the
`test_confluence_multiwindow_ranking_export.py` suite — the
public surface (input fixture builders, blocked-reason
taxonomy, sort key, CLI shape) is unchanged from the
original 19 tests, so a full focused 13-way sweep is not
required again. Original PR-open focused 13-way: 331 passed.

py_compile: clean (module + tests).
git diff --check: clean.

---

## 10. Real production-data evidence

Ran `confluence_multiwindow_ranking_export.py --all-artifacts --artifact-root output/research_artifacts --cache-dir cache/results` against current production state. Discovered 2 ticker directories (SPY + _GSPC). Both Confluence artifacts predate the Phase 6I-20 multi-window contract (they carry only the Phase 6C baseline shape: `timeframes` list + `summary` dict).

| Field | Value |
|---|---|
| `inspected_count` | 2 |
| `eligible_count` | **0** |
| `blocked_count` | **2** |
| `summary.blocked_reason_counts` | `{daily_only: 2}` |
| `summary.data_status_counts` | `{daily_only: 2}` |

Per-ticker:

| Ticker | `data_status` | `ranking_blocked_reason` |
|---|---|---|
| SPY | `daily_only` | `daily_only` |
| _GSPC | `daily_only` | `daily_only` |

This is the expected outcome given the sprint state: production `has_true_multiwindow_k_engine_outputs=false` for SPY, and no Phase 6I-25 patch writer has been authorized. **When the SPY pilot eventually flips through refresh → promote → Confluence-patch-write, this same export run will produce one rank-eligible row for SPY without further code work.** The broader universe will follow once their artifacts acquire the Phase 6I-20 fields.

The evidence run produced **zero production-root mutations**.

---

## 11. No-production-activity confirmation

| Activity | Performed? |
|---|---|
| Writer `--write` invocation (any writer) | **No** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` set | **No** |
| Source refresh (`signal_engine_cache_refresher`) | **No** |
| `yfinance` fetch | **No** |
| Production promotion (`signal_library_stable_promotion_writer`) | **No** |
| Confluence patch writer (`multiwindow_k_confluence_patch_writer`) | **No** |
| `confluence_pipeline_runner` invocation | **No** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster / Confluence batch execution | **No** |
| Production data write | **No** |
| Production signal-library write to `signal_library/data/stable/` | **No** |

The Phase 6H-5 two-key writer gate, Phase 6I-9 supervised gate, Phase 6I-22 strict full-member-coverage gate, Phase 6I-25 patch-writer 5-gate cascade (including `_writer_plan_payload_is_consistent`), Phase 6I-28 close-source fallback contract, Phase 6I-29 exact-date member alignment, Phase 6I-30 interval-native close builder, Phase 6I-31 promotion writer 5-gate cascade + transactional rollback, Phase 6I-32 fresh-staging readiness harness, and Phase 6I-33 source-refresh readiness module are all unchanged in runtime contract.

---

## 12. Remaining gaps

- **Production SPY supervised source refresh still pending.** Phase 6I-33 verdict `refresh_candidate_ready=false`; yfinance one trading day behind cutoff. The SPY pilot resumes once readiness flips; this PR does not advance it.
- **Production signal-library promotion (Phase 6I-31 writer) still pending.** Requires the SPY refresh first.
- **Production Confluence patch write (Phase 6I-25 writer) still pending.** Requires the promotion first.
- **Broad-universe production Confluence artifacts do NOT yet carry the Phase 6I-20 fields.** Until they do, this export's `eligible_count` will be zero against production roots and only the `blocked_rows` surface will be populated. The current 2-ticker production evidence above demonstrates this state honestly.
- **Website UI reader / view layer** is the next phase after this export contract exists. The export's JSON shape is the contract the UI will consume.
- **First-pass ranking rule** is intentionally transparent (`all_windows_firing` → `windows_firing_count` → `k_cells_firing` → `total_capture_pct_sum` → `avg_sharpe_ratio` → `trigger_days_sum` → fewer issue codes → ticker). A future phase replaces it with a researched scoring contract.

---

## 13. Validation

- `py_compile`: clean.
- `git diff --check`: clean.
- `git diff --stat`: 3 files added — 1 new production module (`confluence_multiwindow_ranking_export.py`, ~860 lines), 1 new test file (`test_scripts/test_confluence_multiwindow_ranking_export.py`, ~700 lines), 1 new Markdown evidence doc (this file).
- Pinned interpreter: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.
- Focused 13-way: **331 passed in 11.95s**.
- Real production-roots run via `--all-artifacts`: rc=0; output captured to a temp evidence dir outside production roots and outside the repo.

---

## 14. Reference paths

- Export module: `project/confluence_multiwindow_ranking_export.py` (new).
- Tests: `project/test_scripts/test_confluence_multiwindow_ranking_export.py` (new; 19 tests).
- Phase 6I-33 evidence (predecessor — SPY source-refresh readiness): `project/md_library/shared/2026-05-14_PHASE_6I33_SPY_K_UNIVERSE_SOURCE_REFRESH_READINESS.md`.
- Phase 6I-32 evidence (fresh-staging harness): `project/md_library/shared/2026-05-14_PHASE_6I32_FRESH_STAGED_SIGNAL_LIBRARY_REBUILD_EVIDENCE.md`.
- Phase 6I-23 payload builder (`per_window_k_metrics` + `build_wide_window_alignment` + `multiwindow_k_engine_payload_metadata` schema): `project/multiwindow_k_engine_payload_builder.py`.
- Phase 6I-20 gap audit (canonical shape definitions): `project/multiwindow_k_engine_gap_audit.py`.
- Temp evidence directory: `C:\Users\sport\AppData\Local\Temp\phase_6i34_multi_ticker_ranking_export\` (OUTSIDE production roots, OUTSIDE the repo; nothing in it is committed).
- CLAUDE.md § 6 — current sprint state.
