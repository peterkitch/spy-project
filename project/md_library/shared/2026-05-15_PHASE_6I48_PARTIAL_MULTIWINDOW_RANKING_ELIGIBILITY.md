# Phase 6I-48: partial multi-window ranking eligibility

**Date:** 2026-05-15
**Scope:** elevate partial-only multi-window Confluence artifacts (the
Phase 6I-47 namespaced block `multiwindow_k_partial_payload_metadata`)
from blocked-only display rows to **rank-eligible rows with an
explicit `partial_effective_members` basis + a visible `!` warning**,
TrafficFlow-style. The strict Phase 6I-20 complete-payload contract
is preserved verbatim — partial rows NEVER claim strict 60/60
completeness, never flip strict gates, and never write to the strict
Phase 6I-20 keys.
**Authorization basis:** code implementation phase. Read-only against
production. No production Confluence artifact write. No
`PRJCT9_AUTOMATION_WRITE_AUTH`. No source refresh. No yfinance. No
`confluence_pipeline_runner`. No batch engines.
**Verdict:** **READY.** Partial-rankable contract implemented and
proven against a tmp fixture; strict complete + Phase 6I-47
blocked-partial behaviour both preserved.
**Amendment-1 status:** added below in § 11. Closes two Codex audit
gaps: (1) `ranking_eligibility_basis` now threads end-to-end
through the export package, reader/view, and renderer (badge + data
attribute + detail panel); (2) partial-ranked rows now populate
`current_build_signals` / `current_build_signal_summary` /
`primary_build_summary` from the effective metrics so the website
detail card / table cells are at parity with strict-complete rows.

---

## 0. Top-line summary

| Stage | Result |
|---|---|
| Payload builder new effective branch | Runs Phase 6I-21 core grid on adapter's prepared-cell subset when `partial_payload_available=True`. Emits `effective_per_window_k_metrics` + `effective_build_wide_window_alignment` + `effective_cell_count`. **Strict `payload_ready` / strict `per_window_k_metrics` / strict `build_wide_window_alignment` unchanged on the partial path.** |
| Planner `_build_partial_payload_block` | Copies effective metrics into the partial namespaced block (under distinct field names; strict-key forbidden-list guard still active). |
| Writer-side `_writer_partial_payload_is_consistent` | Still rejects strict keys inside the partial block; ACCEPTS the new `effective_*` fields (they have different names from the strict keys). |
| Ranking export new partial-rankable branch | `data_status='partial_multiwindow'` artifact with `effective_per_window_k_metrics` + `prepared_cell_count > 0` → `rank_eligible=True`, `ranking_blocked_reason=None`, `ranking_eligibility_basis='partial_effective_members'`. Without effective metrics → blocked (Phase 6I-47 preserved). |
| Ranking export taxonomy unchanged | 10 blocked reasons, 5 data statuses. New constants on a separate `RANKING_ELIGIBILITY_BASIS_*` axis (2 entries: strict + partial). |
| Row schema | New `ranking_eligibility_basis: Optional[str] = None`. Strict-complete rows now also carry the explicit `strict_full_60_cell` value (no behaviour change). |
| Phase 6I-48 focused suite | **16 / 16 passed**. |
| Full regression | **2,235 / 2,235 passed** (+16 vs Phase 6I-47 baseline 2,219). 165 pre-existing pandas-fragmentation warnings, unchanged. |
| Production-root diff | **0 / 0 / 0** across all 5 roots. |

---

## 1. Phase 6I-47 dependency summary

Phase 6I-47 (PR #264, merged 2026-05-15 at `0a53cc5`) created the
partial multi-window artifact contract: namespaced
`multiwindow_k_partial_payload_metadata` block (schema_version
`phase_6i_47_partial_multiwindow_v1`), `disjoint` from the strict
Phase 6I-20 keys, with planner + writer + ranking-export read paths.
Partial-only artifacts at Phase 6I-47 close classified as
`data_status='partial_multiwindow'` + `ranking_blocked_reason='partial_multiwindow_only'`,
`rank_eligible=False` — **display-only**.

Operator/Codex correction: that's too conservative for the
TrafficFlow-style product surface. Partial-but-usable tickers should
still be rankable, but must carry a visible `!` warning and must
never claim strict/full 60-cell completeness. Phase 6I-48 implements
that correction.

---

## 2. Exact schema additions

### 2.1 Payload report (`MultiWindowKEnginePayloadReport`)

New fields, all empty by default for back-compat:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `effective_per_window_k_metrics` | `list[dict]` | `[]` | Per-cell metrics produced by running the Phase 6I-21 core grid on the adapter's prepared-cell subset. Same per-cell shape as strict `per_window_k_metrics` BUT a distinct field name. |
| `effective_build_wide_window_alignment` | `dict[str, dict]` | `{}` | Parallel per-window alignment surface for the effective subset. |
| `effective_cell_count` | `int` | `0` | Number of cells in `effective_per_window_k_metrics`. |

Populated **only when** `partial_payload_available=True` AND the core
grid runs cleanly on the adapter's prepared cells. Strict
`per_window_k_metrics` / `build_wide_window_alignment` /
`payload_ready` STAY at their pre-Phase-6I-48 defaults on the partial
path.

### 2.2 Partial namespaced block (`multiwindow_k_partial_payload_metadata`)

Added to the namespaced block (planner `_build_partial_payload_block`):

  * `effective_per_window_k_metrics: list[dict]`
  * `effective_build_wide_window_alignment: dict`
  * `effective_cell_count: int`

These fields have field names DIFFERENT from the strict Phase 6I-20
keys, so the existing planner-side
(`_planner_partial_payload_is_valid`) and writer-side
(`_writer_partial_payload_is_consistent`) forbidden-list guards
(which check for the literal strings `per_window_k_metrics` /
`build_wide_window_alignment` / `multiwindow_k_engine_payload_metadata`)
continue to refuse a partial block that smuggles strict keys.

### 2.3 Ranking export (`PerTickerRankingRow`)

New row field:

| Field | Type | Default | Values |
|---|---|---|---|
| `ranking_eligibility_basis` | `Optional[str]` | `None` | `"strict_full_60_cell"` (strict-complete rows), `"partial_effective_members"` (partial-rankable rows), `None` (blocked rows) |

New constants:

  * `RANKING_ELIGIBILITY_BASIS_STRICT_FULL_60_CELL = "strict_full_60_cell"`
  * `RANKING_ELIGIBILITY_BASIS_PARTIAL_EFFECTIVE_MEMBERS = "partial_effective_members"`
  * `ALL_RANKING_ELIGIBILITY_BASES`

Existing taxonomies unchanged: `ALL_RANKING_BLOCKED_REASONS` stays at
10 entries (the `partial_multiwindow_only` blocked reason still fires
when a partial block lacks effective metrics); `ALL_DATA_STATUSES`
unchanged.

---

## 3. How partial ranking metrics are derived

1. **Adapter (Phase 6I-46):** processes the K-row member set; when
   `invalid_members` is supplied, cells whose authored members
   intersect that set are marked `unprepared_due_to_excluded_members`.
   The remaining cells (e.g. K∈{1..6} × 5 windows for the SPY/TEF
   case) are prepared with their authored member sets.
2. **Payload builder (Phase 6I-48, NEW):** in the `not
   can_evaluate_full_60_cell_grid` branch, when
   `partial_payload_available=True` (i.e. ≥1 cell prepared), the
   builder runs the Phase 6I-21 core grid against the adapter's
   `per_cell_inputs` map. Output cells become
   `effective_per_window_k_metrics`; alignment is computed via the
   existing `_build_window_alignment` helper and stored in
   `effective_build_wide_window_alignment`.
3. **Planner (Phase 6I-48, NEW):** `_build_partial_payload_block`
   copies these three fields verbatim into the partial namespaced
   block. The strict Phase 6I-20 keys are NEVER touched.
4. **Ranking export (Phase 6I-48, NEW):** in the
   `data_status='partial_multiwindow'` branch, before falling back
   to blocked-row, the export tries
   `_try_build_partial_rankable_row`. The helper:
     * refuses if `partial_block` is missing or
       `effective_per_window_k_metrics` is missing / empty;
     * refuses if `prepared_cell_count <= 0`;
     * refuses if the partial block smuggles any of the strict
       Phase 6I-20 key names;
     * otherwise calls `_aggregate_per_window_k_metrics` (the same
       aggregator strict-complete rows use) on the effective metrics
       to produce `total_capture_pct_sum`, `avg_sharpe_ratio`,
       `trigger_days_sum`, `windows_firing`, `k_cells_firing`,
       `strongest_*`, `latest_*` counts;
     * builds the row's `data_completeness` block via
       `_build_data_completeness(rank_eligible=True, member_block,
       blocked_reason=None)` — which produces
       `data_completeness_status='partial'` +
       `data_warning_symbol='!'` because the member_block reports
       `has_incomplete_build_members=True` (TEF is in
       `incomplete_member_detail`);
     * returns a `PerTickerRankingRow` with `rank_eligible=True`,
       `data_status='partial_multiwindow'`,
       `ranking_blocked_reason=None`,
       `ranking_eligibility_basis='partial_effective_members'`,
       `k_cells_available=prepared_cell_count` (NOT canonical 60),
       and the aggregated sort values populated for the website
       leaderboard.

---

## 4. Strict-vs-partial separation pins

Every Phase 6I-48 path is gated so the strict Phase 6I-20 contract
remains untouched:

  * **Strict payload report:** `payload_ready=True` still requires
    `can_evaluate_full_60_cell_grid=True`. The effective fields are
    purely additive; the strict gate function is unchanged.
  * **Strict planned payload:** the planner's strict `patch_ready` /
    `planned_payload` / `fields_to_add` / `fields_to_replace` are
    unaffected by the partial branch. `_writer_plan_payload_is_consistent`
    (strict-only validator) continues to require exactly the three
    strict `PLANNED_PAYLOAD_KEYS` and refuses anything else.
  * **Partial namespaced block:** still namespaced under
    `multiwindow_k_partial_payload_metadata`. The `effective_*`
    fields live INSIDE that block — never as top-level artifact keys
    — so a strict-only reader continues to see no strict-shape data.
  * **Writer-side validators:** unchanged. The forbidden-key list
    (literal strings `per_window_k_metrics` /
    `build_wide_window_alignment` /
    `multiwindow_k_engine_payload_metadata`) still rejects any block
    that smuggles them. The new `effective_*` fields have different
    names, so they pass the existing validator without weakening
    it.
  * **Ranking-export classifier:** unchanged. A strict-complete
    artifact still classifies as `data_status='full_60_cell'`. A
    `partial_multiwindow` artifact only reaches the new
    `_try_build_partial_rankable_row` path; that helper itself
    defensively refuses any partial block that smuggles the strict
    keys.
  * **Sort values for partial rows:** carry real numeric values for
    Total Capture / Sharpe / Trigger Days, but `k_cells_available`
    reflects the prepared subset (not canonical 60), and the
    `data_status` field continues to distinguish strict vs partial
    rows.

---

## 5. Evidence

### 5.1 Synthetic partial-rankable fixture (tmp artifact)

A tmp Confluence artifact carrying ONLY the partial namespaced block
with effective metrics (30 cells, K∈{1..6} × all 5 canonical
windows, each cell `total_capture_pct=1.5`, `sharpe_ratio=0.4`,
`trigger_days=5`) produces the following ranking row:

| Field | Value |
|---|---|
| `inspected_count` | 1 |
| `eligible_count` | **1** |
| `blocked_count` | 0 |
| `ranking_rows[0].ticker` | `SPY` |
| `ranking_rows[0].rank_eligible` | **True** |
| `ranking_rows[0].data_status` | `partial_multiwindow` |
| `ranking_rows[0].ranking_blocked_reason` | `None` |
| `ranking_rows[0].ranking_eligibility_basis` | **`partial_effective_members`** |
| `data_completeness.data_completeness_status` | `partial` |
| `data_completeness.data_warning_symbol` | **`!`** |
| `data_completeness.has_incomplete_build_members` | True |
| `data_completeness.incomplete_members` | `["TEF"]` |
| `row_sort_values.total_capture_pct_sort` | **45.0** (= 30 cells × 1.5) |
| `row_sort_values.sharpe_ratio_sort` | **0.4** (= avg across 30 cells of 0.4) |
| `row_sort_values.trigger_days_sort` | **150** (= 30 × 5) |
| `k_cells_available` | **30** (NOT canonical 60) |
| `k_cells_total` | 60 (canonical universe size — unchanged) |
| `windows_firing` count | 5 |

The row is in the `ranking_rows` list (not `blocked_rows`); one
ticker → exactly one row.

### 5.2 Partial-without-effective-metrics fixture (blocked path preserved)

A partial-only artifact whose block has
`prepared_cell_count=0` and no `effective_per_window_k_metrics` (the
Phase 6I-47 default fixture):

| Field | Value |
|---|---|
| `inspected_count` | 1 |
| `eligible_count` | 0 |
| `blocked_count` | **1** |
| `blocked_rows[0].rank_eligible` | **False** |
| `blocked_rows[0].data_status` | `partial_multiwindow` |
| `blocked_rows[0].ranking_blocked_reason` | `partial_multiwindow_only` |
| `blocked_rows[0].ranking_eligibility_basis` | `None` |

Phase 6I-47 behaviour preserved verbatim.

### 5.3 Strict complete fixture (unchanged)

A strict complete artifact (full 60-cell `per_window_k_metrics` +
`build_wide_window_alignment` + `multiwindow_k_engine_payload_metadata`):

| Field | Value |
|---|---|
| `rank_eligible` | True |
| `data_status` | `full_60_cell` |
| `ranking_blocked_reason` | `None` |
| `ranking_eligibility_basis` | **`strict_full_60_cell`** |
| `data_completeness.data_completeness_status` | `complete` |
| `data_completeness.data_warning_symbol` | `None` / `""` |
| `k_cells_available` | 60 |

Phase 6I-25/6I-34 strict behaviour preserved.

### 5.4 Defensive: strict-key smuggle inside partial block

A malformed artifact whose partial block carries an extra
`per_window_k_metrics: []` field is classified as
`incomplete_multiwindow` (because the strict-keys-present branch
runs first) and the row is blocked. The Phase 6I-48 partial-rankable
helper also refuses to promote it independently, so even a
hypothetical bypass of the classifier still wouldn't let a strict-key
smuggle row claim rank-eligible.

### 5.5 Renderer surface

The Phase 6I-41 static board renderer + Phase 6I-42 overlays + the
website export package + reader/view already render the
`data_completeness.data_warning_symbol` and
`data_completeness.incomplete_members` fields via the existing
Phase 6I-40 plumbing. Phase 6I-48 needed no code change at those
layers — the partial-rankable row's `data_completeness` block carries
the same fields a strict-blocked-with-warning row would, so the
renderer auto-shows the warning column with `!` and a detail panel
listing TEF as the excluded member. Verified by running the
renderer against the production artifact root (rc=0, 52,164-byte
HTML, empty stderr).

### 5.6 Sorting

The Phase 6I-48 row carries the same `row_sort_values` schema as
strict rows. The website's existing sort key set (Total Capture %,
Sharpe Ratio, Trigger Days, Rank, Ticker) operates on the partial
row's real numeric metrics derived from the effective cells, so the
website can rank partial tickers honestly alongside strict tickers.

---

## 6. Tests

### 6.1 Phase 6I-48 focused suite

`pytest test_scripts/test_phase_6i48_partial_multiwindow_ranking_eligibility.py -q` → **16 / 16 passed**.

Covers:

  * New schema fields on payload report (× 2).
  * Constants exposed (× 1).
  * Strict complete row → `basis='strict_full_60_cell'` (× 1).
  * Partial with effective metrics → rank-eligible + correct
    completeness / warning / member detail (× 1).
  * Partial-rankable row does NOT claim strict completeness (× 1).
  * Sort values are numeric for partial rows (× 1).
  * One ticker → one row (× 1).
  * `ranking_eligibility_basis` present in JSON serialization (× 1).
  * Partial without effective metrics → blocked (Phase 6I-47
    preserved) (× 1).
  * Zero prepared cells → blocked (× 1).
  * Strict-key smuggle inside partial block → blocked (× 1).
  * Writer-side `_writer_partial_payload_is_consistent` still rejects
    strict keys (× 1); ACCEPTS the new `effective_*` fields (× 1).
  * Static guards (× 2).

### 6.2 Touched-module focused suite

`pytest` across the planner + writer + payload + adapter + ranking
export + website export / view / static renderer / overlays +
Phase 6I-46 + Phase 6I-47 + Phase 6I-48 tests → **403 / 403 passed**.

### 6.3 Full regression

`pytest test_scripts -q` → **2,235 / 2,235 passed** (was 2,219 in
Phase 6I-47 baseline; +16 new Phase 6I-48 tests). 165 pre-existing
pandas-fragmentation warnings (unchanged from sprint baseline). No
new warnings.

### 6.4 Other checks

  * `py_compile` clean on the 3 changed modules:
    `multiwindow_k_engine_payload_builder.py`,
    `multiwindow_k_confluence_patch_planner.py`,
    `confluence_multiwindow_ranking_export.py`.
  * `git diff --check` clean.

---

## 7. Production-root diff

```
PRE  : 3239 / 1634 / 35 / 5227 / 72899
POST : 3239 / 1634 / 35 / 5227 / 72899

cache/results:               modified 0  added 0  removed 0
cache/status:                modified 0  added 0  removed 0
output/research_artifacts:   modified 0  added 0  removed 0
output/stackbuilder:         modified 0  added 0  removed 0
signal_library/data/stable:  modified 0  added 0  removed 0
```

Zero production-root activity. All evidence-runner artifacts lived
under a `tempfile.mkdtemp` directory and were deleted at end of
phase.

---

## 8. No-production-activity confirmation

| Surface | Touched? |
|---|---|
| `cache/results` / `cache/status` / `output/research_artifacts` / `output/stackbuilder` / `signal_library/data/stable` | **No** (0 / 0 / 0 diff each) |
| `multiwindow_k_confluence_patch_writer --write` against production | **Not invoked** |
| `signal_library_stable_promotion_writer --write` | **Not invoked** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` env var | **Never set** |
| Source refresh (`signal_engine_cache_refresher`) | **Not invoked** |
| yfinance | **No fetch** |
| `confluence_pipeline_runner` | **Not invoked** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster batch | **Not invoked** |

---

## 9. Files changed

| File | Change |
|---|---|
| `project/multiwindow_k_engine_payload_builder.py` | New report fields `effective_per_window_k_metrics` / `effective_build_wide_window_alignment` / `effective_cell_count`; new effective-branch in `build_multiwindow_k_engine_payload` (runs the core grid on the adapter's prepared-cell subset when `partial_payload_available=True`); JSON serialization carries the new fields. |
| `project/multiwindow_k_confluence_patch_planner.py` | `_build_partial_payload_block` now copies `effective_per_window_k_metrics` / `effective_build_wide_window_alignment` / `effective_cell_count` into the partial namespaced block. |
| `project/confluence_multiwindow_ranking_export.py` | New `RANKING_ELIGIBILITY_BASIS_STRICT_FULL_60_CELL` / `RANKING_ELIGIBILITY_BASIS_PARTIAL_EFFECTIVE_MEMBERS` constants + `ALL_RANKING_ELIGIBILITY_BASES`; new `ranking_eligibility_basis` field on `PerTickerRankingRow`; new `_try_build_partial_rankable_row` helper invoked before the blocked-row fallback when `data_status='partial_multiwindow'`; strict-complete row construction now passes `ranking_eligibility_basis='strict_full_60_cell'`; JSON serialization carries the new field. |
| `project/test_scripts/test_phase_6i48_partial_multiwindow_ranking_eligibility.py` | **New** focused-test module with 16 tests. |
| `project/md_library/shared/2026-05-15_PHASE_6I48_PARTIAL_MULTIWINDOW_RANKING_ELIGIBILITY.md` | **New** evidence doc (this file). |

Patch writer, website export package, reader/view, static board
renderer, overlays required NO code change — partial-rankable rows
reuse the existing Phase 6I-40 `data_completeness` plumbing (which
already surfaces `data_warning_symbol` + `incomplete_members`) and
the existing row-level `data_status` / `rank_eligible` /
`ranking_blocked_reason` fields the renderer already consumes.

---

## 10. Verdict & next step

**Verdict:** **READY.** Phase 6I-48 makes the partial-payload
artifact contract user-visible on the leaderboard:

  * Partial tickers may rank, with `data_status='partial_multiwindow'`,
    `data_warning_symbol='!'`, and an explicit
    `ranking_eligibility_basis='partial_effective_members'` so a
    consumer (audit, website, future scoring contract) can tell
    apart strict from partial rows at a glance.
  * Strict complete rows continue to rank under
    `ranking_eligibility_basis='strict_full_60_cell'` with no
    behaviour change.
  * Partial rows lacking effective metrics or with zero prepared
    cells remain blocked under
    `ranking_blocked_reason='partial_multiwindow_only'`, exactly as
    in Phase 6I-47.
  * Strict Phase 6I-20 contract preserved end-to-end: partial rows
    NEVER flip strict `payload_ready` / `patch_ready` /
    `can_evaluate_full_60_cell_grid`, NEVER write to strict keys,
    NEVER pass writer-side strict-payload validators.

**Next step (Phase 6I-49):** after Codex audit + merge of Phase
6I-48, the operator may authorize a separate supervised
single-ticker partial-artifact write for SPY only:

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
  multiwindow_k_confluence_patch_writer.py \
  --ticker SPY \
  --artifact-root output/research_artifacts \
  --stackbuilder-root output/stackbuilder \
  --signal-library-dir <staged dir> \
  --cache-dir cache/results \
  --current-as-of-date <YYYY-MM-DD> \
  --invalid-members-json '{"TEF": {...}}' \
  --allow-partial-payload-plan \
  --write
```

with `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` set in the
environment of that supervised session ONLY. The partial namespaced
block (now carrying `effective_per_window_k_metrics`) would land on
SPY's production Confluence artifact; the ranking export would then
auto-flip SPY's row from `daily_only` (or `partial_multiwindow_only`)
to a **rank-eligible** `partial_effective_members` row with the `!`
warning surfaced on the live website.

---

## 11. Amendment-1 (Codex audit response): basis threaded through the website chain + current/primary surfaces populated for partial rows

Codex audit of the Phase 6I-48 base implementation found two
website-contract gaps:

  1. `ranking_eligibility_basis` was emitted on `PerTickerRankingRow`
     but **lost** as soon as the row passed through
     `confluence_website_export_package` / reader-view / renderer —
     a consumer reading the website JSON or HTML could not see
     whether a row was strict-complete or partial.
  2. Partial-ranked rows had **empty** `current_build_signals`,
     `current_build_signal_summary`, and `primary_build_summary`
     even though `effective_per_window_k_metrics` carried enough
     cell data to populate them.

Amendment-1 closes both gaps. Strict-complete behaviour remains
byte-identical; the only behaviour change is on the partial-ranked
row + on the website-surface pass-through of the basis tag.

### 11.1 Changed files (amendment-1)

| File | Change |
|---|---|
| `project/confluence_multiwindow_ranking_export.py` | `_try_build_partial_rankable_row` now calls the existing Phase 6I-37 `_build_current_signal_matrix` + `_build_current_signal_summary` + Phase 6I-39 `_build_primary_build_summary` helpers against `effective_per_window_k_metrics` + `effective_build_wide_window_alignment` and sets the corresponding fields on the constructed row. No new cells are fabricated — if the effective metrics contain 30 cells, the matrix has 30 entries and `k_cells_available` still tracks the prepared count, not the canonical 60. |
| `project/confluence_website_export_package.py` | `_normalize_ranking_row` + `_normalize_blocked_row` + the `ticker_details` normalizer now pass `ranking_eligibility_basis` through verbatim. Blocked rows pass `None`; partial-rankable rows pass `partial_effective_members`; strict-complete rows pass `strict_full_60_cell`. |
| `project/confluence_website_reader_view.py` | The ranking-table-row normalizer and the ticker-card normalizer now both carry `ranking_eligibility_basis` through to `ranking_table[*]` and `ticker_cards[*]`. |
| `project/confluence_static_board_renderer.py` | `_ranking_row_html` renders a visible badge in the ticker cell — `Partial (effective members)` (with a tooltip explaining the basis) or `Strict 60-cell` (also tool-tipped) — and adds a `data-ranking-eligibility-basis="..."` attribute to the `<tr>` so a future CSS / JS filter can target partial rows. The inline JS detail-panel renderer (`_INLINE_JS`) shows a new **Ranking eligibility basis** section with both a human label and the internal code, immediately above the existing **Data completeness** section. Strict-only rows render the `Strict 60-cell` badge; partial rows render the `Partial (effective members)` badge and continue to render the `!` warning column from the existing Phase 6I-40 plumbing. |
| `project/test_scripts/test_phase_6i48_partial_multiwindow_ranking_eligibility.py` | Added 11 new tests covering: partial row's `current_build_signals` length matches the effective-cell count; partial row's `current_build_signal_summary` is non-null and carries Phase 6I-37 keys; partial row's `primary_build_summary` is non-null; partial matrix does NOT fabricate cells to canonical 60; blocked-partial rows still carry empty current/primary surfaces (regression); strict-complete rows still carry `strict_full_60_cell` basis + populated current/primary (regression); export-package `ranking_rows[*]` + `ticker_details[*]` carry the basis (partial + strict); reader-view `ranking_table[*]` + `ticker_cards[*]` carry the basis; rendered HTML contains the `Partial (effective members)` badge + the `!` warning + `data-ranking-eligibility-basis="partial_effective_members"` attribute; rendered HTML contains the `Strict 60-cell` badge + `data-ranking-eligibility-basis="strict_full_60_cell"` attribute. |

### 11.2 Amendment-1 evidence

| Surface | Strict-complete row | Partial-rankable row | Blocked row |
|---|---|---|---|
| `PerTickerRankingRow.ranking_eligibility_basis` | `strict_full_60_cell` | `partial_effective_members` | `None` |
| `PerTickerRankingRow.current_build_signals` length | 60 | **30** (effective cells; no fabrication) | `()` |
| `PerTickerRankingRow.current_build_signal_summary` | populated | **populated** (Phase 6I-37 schema) | `None` |
| `PerTickerRankingRow.primary_build_summary` | populated | **populated** (Phase 6I-39 schema) | `None` |
| Export package `ranking_rows[*].ranking_eligibility_basis` | `strict_full_60_cell` | `partial_effective_members` | `None` |
| Export package `ticker_details[*].ranking_eligibility_basis` | `strict_full_60_cell` | `partial_effective_members` | `None` |
| View model `ranking_table[*].ranking_eligibility_basis` | `strict_full_60_cell` | `partial_effective_members` | n/a (blocked uses `blocked_table`) |
| View model `ticker_cards[*].ranking_eligibility_basis` | `strict_full_60_cell` | `partial_effective_members` | `None` |
| Renderer HTML `<tr>` data attribute | `data-ranking-eligibility-basis="strict_full_60_cell"` | `data-ranking-eligibility-basis="partial_effective_members"` | (no row in the ranking table) |
| Renderer HTML ticker-cell badge | `Strict 60-cell` | **`Partial (effective members)`** | — |
| Renderer HTML warning column | (none) | **`!`** | (n/a) |
| Renderer detail-panel basis section | "Strict (full 60-cell)" + code | **"Partial (effective members)" + code** | (hidden when basis is `None`) |

### 11.3 Amendment-1 tests

- **Phase 6I-48 focused suite (including amendment-1):** `pytest test_scripts/test_phase_6i48_partial_multiwindow_ranking_eligibility.py -q` → **27 / 27 passed** (was 16 in the base; +11 amendment-1).
- **Touched-module focused suite:** the ranking export + export package + reader/view + static renderer + overlays suites all pass alongside the new amendment-1 tests; **209 / 209**.
- **Full regression:** `pytest test_scripts -q` → **2,246 / 2,246 passed** (was 2,235 in the Phase 6I-48 base; +11 amendment-1 tests). 165 pre-existing pandas-fragmentation warnings unchanged. No new warnings.
- `py_compile` clean on all 4 changed modules (`confluence_multiwindow_ranking_export.py`, `confluence_website_export_package.py`, `confluence_website_reader_view.py`, `confluence_static_board_renderer.py`).
- `git diff --check` clean.

### 11.4 Amendment-1 no-production-activity confirmation

  * Production roots: **0 / 0 / 0** diff across `cache/results`, `cache/status`, `output/research_artifacts`, `output/stackbuilder`, `signal_library/data/stable`.
  * No `--write` on any guarded writer against production. No `PRJCT9_AUTOMATION_WRITE_AUTH`.
  * No source refresh, no yfinance, no `confluence_pipeline_runner`, no batch engines.
  * No production promotion. No Confluence patch writer.
  * Tests + evidence runs operated against tmp fixtures only.
