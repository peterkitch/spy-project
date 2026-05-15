# Phase 6I-46: TrafficFlow-compatible invalid-member handling for the multi-window Confluence path

**Date:** 2026-05-15
**Scope:** new TrafficFlow-style invalid-member contract threaded
through the Phase 6I-22..6I-31 multi-window Confluence chain. Proven
end-to-end against the Phase 6I-45 SPY/TEF blocker.
**Authorization basis:** code implementation phase. Read-only against
production Confluence + signal-library + StackBuilder roots; staged
output only to `_phase_6i_46_staged_libraries/` (outside all
production roots). No `--write` on any guarded writer, no
`PRJCT9_AUTOMATION_WRITE_AUTH`, no source refresh, no yfinance, no
`confluence_pipeline_runner`, no StackBuilder / OnePass /
ImpactSearch / TrafficFlow / Spymaster batch execution.
**Verdict:** **PARTIAL_SURFACE_READY** — the multi-window chain now
emits an honest partial / blocked verdict when authored members are
invalid; the strict 60-cell complete contract is preserved verbatim;
the SPY/TEF case now produces a partial payload with TEF surfaced as
`excluded_members` rather than the legacy `incomplete_member_coverage`
silent skip. **STRICT_WRITE_STILL_BLOCKED** — partial payloads are
display-only; the patch writer and stable promotion writer correctly
refuse to mutate any production artifact under the partial contract.

---

## 0. Top-line summary

| Layer | Pre-Phase-6I-46 behaviour | Post-Phase-6I-46 behaviour |
|---|---|---|
| Adapter (`multiwindow_k_input_adapter`) | Cells with members the staged dir lacks skip with `incomplete_member_coverage`; no record of `original_members` vs `effective_members`. | When `invalid_members` is supplied, cells with authored members in that set skip with `unprepared_due_to_excluded_members`; original / effective / excluded member lists + structured exclusion records surfaced on the report. |
| Diagnostic | Emits the same fields as before. | Also emits `data_completeness_status`, `data_warning_symbol`, `original_members_by_K`, `effective_members_by_K`, `excluded_members_by_K`, `incomplete_member_detail`, per-cell `excluded_members`. |
| Payload builder | `payload_ready=True` only when `can_evaluate_full_60_cell_grid=True`. | Same gate; additionally surfaces `data_completeness_status` / `partial_payload_available` so a downstream consumer can render the partial state. Strict 60-cell gate **unchanged**. |
| Patch planner | `patch_ready=False` when payload not ready. | Adds `ISSUE_PARTIAL_PAYLOAD_NOT_PROMOTABLE` + `ACTION_PARTIAL_PAYLOAD_NOT_PROMOTABLE` when upstream is partial. Mirrors completeness fields onto the plan. |
| Patch writer | Refuses writes when planner says not ready. | Same behaviour. Partial plans cannot mutate the artifact. |
| Ranking export | Reads on-disk artifact; surfaces incomplete-build-members when an artifact provides member-level issue data. | Default member-completeness provider now reads `data_completeness_status` + `incomplete_member_detail` from the artifact (top-level or under `multiwindow_k_engine_payload_metadata`) and surfaces TEF-style exclusions as `has_incomplete_build_members=True` with structured `reason:telemetry_reason` strings. |
| Website export package / view / static board renderer / overlays | Existing `data_completeness` block (Phase 6I-40) passes through ranking-row fields. | Inherits the new partial state automatically — no schema change needed at those layers. |

### Numbers (SPY/TEF end-to-end)

| Stage | Value |
|---|---|
| Sandbox build | 70 PKLs written, 0 failed (14 non-TEF tickers × 5 intervals) |
| Adapter `prepared_cell_count` | 30 (K=1..6 × 5 windows) |
| Adapter `skipped_cell_count` | 30 (K=7..12 × 5 windows) |
| Adapter `dominant_skipped_reason` | **`unprepared_due_to_excluded_members`** (was `incomplete_member_coverage` in Phase 6I-45) |
| Adapter `data_completeness_status` | **`partial`** |
| Adapter `data_warning_symbol` | **`!`** |
| Adapter `incomplete_member_detail` records | 6 (one per K row that authored TEF as a member) |
| Adapter `can_evaluate_full_60_cell_grid` | False (strict gate preserved) |
| Payload `payload_ready` | False (strict gate preserved) |
| Payload `data_completeness_status` | `partial` |
| Payload `partial_payload_available` | **True** |
| Patch planner `patch_ready` | False |
| Patch planner `recommended_next_action` | **`partial_payload_not_promotable`** |
| Patch planner `issue_codes` | `['payload_not_ready', 'partial_payload_not_promotable']` |
| Patch writer dry-run `wrote_artifact` | False |
| Patch writer pre/post SHA | byte-identical |
| Promotion planner | `plan_ready=True` for the staged file inventory (70/70 found, 44 add + 26 replace + 0 unchanged) — **unchanged from Phase 6I-45** |
| Static board renderer (no overlays) | rc=0, 52,164-byte HTML, empty stderr |
| Static board renderer (with overlays) | rc=0, 52,164-byte HTML, empty stderr |
| Production-root diff (pre / post phase) | **0 / 0 / 0 across all 5 roots** |
| Phase 6I-46 focused tests | 24 / 24 passed |
| Full regression | 2,188 / 2,188 passed (+24 vs Phase 6I-45 baseline) |

---

## 1. Phase 6I-45 dependency summary

Phase 6I-45 (PR #262, merged 2026-05-15 at `095026b`) proved that the
Phase 6I-44-refreshed cache supports a clean staged signal-library
rebuild for the 14 non-TEF SPY K-universe tickers (70 PKLs, interval-
native close, TEF correctly excluded) but the downstream chain
BLOCKED at 30 / 60 cells. The structural blocker: the single SPY
StackBuilder seed run
`seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D`
permanently encodes TEF as a member; the K=7..12 cells require TEF;
TEF is `invalid_or_delisted` under Phase 6I-43 policy v2 and excluded
from the staged dir; the adapter therefore skipped 30 cells with
`incomplete_member_coverage`, `members_missing=['TEF']`.

The Phase 6I-45 amendment-1 recommended **Option A: TrafficFlow-
compatible invalid-member handling**. Phase 6I-46 implements that
recommendation and proves it against the same SPY/TEF blocker.

---

## 2. Chosen K-grid partial behaviour

**Option A** from the Phase 6I-45 doc, applied at the adapter cell
level rather than at the report level. The canonical 60-cell shape
is preserved verbatim. Cells are bucketed as follows:

| Cell condition | Phase 6I-45 outcome | Phase 6I-46 outcome |
|---|---|---|
| K row contains NO invalid member, all libraries load | prepared | prepared |
| K row contains NO invalid member, some member library missing | `incomplete_member_coverage` skip | `incomplete_member_coverage` skip (unchanged) |
| K row contains AN invalid member | `incomplete_member_coverage` skip | **`unprepared_due_to_excluded_members` skip** (new) |

Key invariants:

  * The canonical 60-cell shape is preserved. The adapter does NOT
    re-scale the K grid to the effective member count.
  * For each affected cell, the adapter preserves the K row's
    authored member list verbatim on the per-cell state, AND records
    structured exclusion entries (`ExclusionRecord`) carrying the
    invalid-member ticker plus the upstream reason / telemetry /
    classification.
  * The adapter does NOT compute on the effective subset for the
    affected cells. The Phase 6I-20 strict complete-payload contract
    requires the FULL authored member set; computing on a partial
    subset would change the K-of-N semantics. A future phase may
    introduce a separate effective-K artifact path; until then, the
    affected cells are explicitly "unprepared with reason."
  * Report-level `data_completeness_status` summarizes the overall
    state: `partial` when at least one cell prepared AND at least
    one cell was unprepared due to excluded members; `blocked` when
    exclusions are present AND zero cells prepared; `complete`
    otherwise (no exclusions present).
  * `payload_ready` and `can_evaluate_full_60_cell_grid` are
    untouched — a partial payload NEVER silently promotes to strict
    complete.

---

## 3. New schema fields (summary)

### 3.1 `multiwindow_k_input_adapter.py`

  * **New dataclass** `ExclusionRecord(ticker, reason, telemetry_reason, source_classification)`.
  * **New constants:** `REASON_UNPREPARED_DUE_TO_EXCLUDED_MEMBERS`, `ISSUE_EXCLUDED_INVALID_MEMBER`, `DATA_COMPLETENESS_STATUS_{COMPLETE,PARTIAL,BLOCKED}`, `DATA_WARNING_SYMBOL_{NONE,INCOMPLETE}`, `ALL_DATA_COMPLETENESS_STATUSES`.
  * **New parameter** on `prepare_multiwindow_k_inputs(..., invalid_members=None)`. Mapping of `ticker -> {reason, telemetry_reason, source_classification}`.
  * **New per-cell field** `PerCellAdapterState.excluded_members: tuple[ExclusionRecord, ...] = ()`.
  * **New report fields** on `MultiWindowKInputAdapterReport`:
    * `original_members_by_K: dict[int, tuple[(str, Optional[str]), ...]]`
    * `effective_members_by_K: dict[int, tuple[(str, Optional[str]), ...]]`
    * `excluded_members_by_K: dict[int, tuple[ExclusionRecord, ...]]`
    * `incomplete_member_detail: tuple[dict, ...]`
    * `data_completeness_status: str` (default `"complete"`)
    * `data_warning_symbol: str` (default `""`)

### 3.2 `multiwindow_k_input_adapter_diagnostic.py`

  * **New CLI flag** `--invalid-members-json` (inline JSON or `@PATH`).
  * **New parameter** `run_adapter_diagnostic(..., invalid_members=None)`.
  * Diagnostic JSON dict gains keys `original_members_by_K`, `effective_members_by_K`, `excluded_members_by_K`, `incomplete_member_detail`, `data_completeness_status`, `data_warning_symbol`. Per-cell records gain `excluded_members`.

### 3.3 `multiwindow_k_engine_payload_builder.py`

  * **New parameter** `build_multiwindow_k_engine_payload(..., invalid_members=None)`.
  * `AdapterSummary` and `MultiWindowKEnginePayloadReport` gain the four mirror fields (`data_completeness_status`, `data_warning_symbol`, `incomplete_member_detail`, `partial_payload_available`).
  * `partial_payload_available=True` iff `data_completeness_status=='partial'` AND adapter reported `prepared_cell_count > 0`. `payload_ready` semantics **unchanged**: still requires strict full 60-cell coverage.

### 3.4 `multiwindow_k_confluence_patch_planner.py`

  * **New parameter** `plan_multiwindow_k_confluence_patch(..., invalid_members=None)`.
  * **New constants** `ISSUE_PARTIAL_PAYLOAD_NOT_PROMOTABLE`, `ACTION_PARTIAL_PAYLOAD_NOT_PROMOTABLE`.
  * `MultiWindowKConfluencePatchPlan` gains the four mirror fields.
  * When upstream payload is `partial` or `blocked`: planner emits the new issue code AND sets `recommended_next_action="partial_payload_not_promotable"`. `patch_ready` stays False; `planned_payload` stays empty.

### 3.5 `multiwindow_k_confluence_patch_writer.py`

  * **No new code**. Existing `_writer_plan_payload_is_consistent` + `--write` two-key gate continue to refuse partial plans because the planner's `patch_ready=False` short-circuits before any mutation logic runs.

### 3.6 `confluence_multiwindow_ranking_export.py`

  * `_default_member_completeness_provider` now reads `data_completeness_status` + `incomplete_member_detail` from the on-disk Confluence artifact (top-level or under `multiwindow_k_engine_payload_metadata`) and translates to the existing `has_incomplete_build_members` / `incomplete_member_count` / `incomplete_members` / `incomplete_member_reasons` schema. The Phase 6I-40 `data_completeness` block + the website export package, reader/view, and renderer already surface the warning when the provider reports incomplete members — those layers required no schema changes.

### 3.7 `signal_library_fresh_staging_readiness.py`

  * **New CLI flag** `--invalid-members-json` (inline or `@PATH`).
  * **New parameter** `evaluate_fresh_staging_readiness(..., invalid_members=None)`.
  * All four downstream callables (adapter / payload / planner / writer) accept and forward the new parameter when non-empty.

---

## 4. SPY / TEF evidence

### 4.1 Inputs

| Parameter | Value |
|---|---|
| `--tickers` | `SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO` (14 non-TEF) |
| `--primary-ticker` | `SPY` |
| `--staged-dir` | `_phase_6i_46_staged_libraries` (outside all 5 production roots) |
| `--cache-dir` | `cache/results` |
| `--current-as-of-date` | `2026-05-14` |
| `--skip-source-availability` | set |
| `--invalid-members-json` | `{"TEF": {"reason": "invalid_or_delisted", "telemetry_reason": "provider_fetch_failed_zero_rows", "source_classification": "phase_6i_43_invalid_or_delisted"}}` |
| Python interpreter | pinned `spyproject2`: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe` |

### 4.2 Adapter

| Field | Value |
|---|---|
| `selected_run_dir` | `output\stackbuilder\SPY\seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D` |
| `prepared_cell_count` | **30** |
| `skipped_cell_count` | **30** |
| `can_evaluate_full_60_cell_grid` | False |
| `data_completeness_status` | **`partial`** |
| `data_warning_symbol` | **`!`** |
| `dominant_skipped_reason` | **`unprepared_due_to_excluded_members`** |
| `counts_by_skipped_reason` | `{'unprepared_due_to_excluded_members': 30}` |
| `incomplete_member_detail` length | **6** (one record per K row that authored TEF) |
| `adapter_issue_codes` | `['excluded_invalid_member']` (plus the existing per-cell codes) |

Each of the 30 skipped cells now carries:

  * `members_attempted` = the K row's authored members (preserved verbatim, INCLUDING TEF).
  * `excluded_members` = `(ExclusionRecord(ticker="TEF", reason="invalid_or_delisted", telemetry_reason="provider_fetch_failed_zero_rows", source_classification="phase_6i_43_invalid_or_delisted"),)`.
  * `members_missing` = `("TEF",)`.
  * `skipped_reason` = `"unprepared_due_to_excluded_members"`.

The K=1..6 cells (which authored only non-TEF members) prepare cleanly,
exactly as in Phase 6I-45.

### 4.3 Payload builder

| Field | Value |
|---|---|
| `payload_ready` | False |
| `data_completeness_status` | `partial` |
| `data_warning_symbol` | `!` |
| `partial_payload_available` | **True** |

### 4.4 Patch planner

| Field | Value |
|---|---|
| `patch_ready` | False |
| `recommended_next_action` | **`partial_payload_not_promotable`** |
| `issue_codes` | `['payload_not_ready', 'partial_payload_not_promotable']` |
| `data_completeness_status` | `partial` (mirrored from payload) |
| `planned_payload` | `{}` |

### 4.5 Patch writer dry-run

| Field | Value |
|---|---|
| `write_requested` | False |
| `wrote_artifact` | **False** |
| `pre_write_sha256` | `db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f` |
| `post_write_sha256` | same as pre |

The writer's existing two-key gate
(`--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`) was
never engaged. Even if it had been, the planner's `patch_ready=False`
+ `planner_patch_ready=False` cascade would have refused the mutation.

### 4.6 Promotion planner / writer (unchanged from Phase 6I-45)

The Phase 6I-31 promotion planner is unaffected by the
Phase 6I-46 changes (it operates on the staged file inventory, not
the multi-window K payload). The Phase 6I-45 numbers carry forward:

| Field | Value |
|---|---|
| `plan_ready` | True |
| `expected_file_count` / `staged_files_found` | 70 / 70 |
| `libraries_to_add` / `libraries_to_replace` / `libraries_unchanged` | 44 / 26 / 0 |
| Promotion writer dry-run `wrote_files` | False |

This is **file-inventory readiness only**, NOT downstream-chain
readiness. The structural blocker (TEF in the StackBuilder seed run)
remains; promotion still cannot make SPY rank-eligible without either
the Phase 6I-46 partial-write contract (currently not authorized) or
a StackBuilder rebuild.

---

## 5. Downstream readiness table — Phase 6I-45 vs Phase 6I-46

| Stage | Phase 6I-45 | Phase 6I-46 |
|---|---|---|
| Adapter `prepared` / 60 | 30 / 60 (skip reason: `incomplete_member_coverage`) | 30 / 60 (skip reason: **`unprepared_due_to_excluded_members`** — honest) |
| Adapter `members_missing` for skipped cells | `['TEF']` (silent skip; no structured reason) | `['TEF']` + `excluded_members=[{reason: invalid_or_delisted, telemetry_reason: provider_fetch_failed_zero_rows, source_classification: phase_6i_43_invalid_or_delisted}]` |
| Adapter `data_completeness_status` | (field did not exist) | `partial` |
| Adapter `data_warning_symbol` | (field did not exist) | `!` |
| Payload `payload_ready` | False | False (strict gate preserved) |
| Payload `partial_payload_available` | (field did not exist) | True |
| Patch planner `recommended_next_action` | `build_payload_first` | `partial_payload_not_promotable` |
| Patch planner `issue_codes` | `['payload_not_ready']` | `['payload_not_ready', 'partial_payload_not_promotable']` |
| Patch writer dry-run `wrote_artifact` | False | False |
| Patch writer pre/post SHA | byte-identical | byte-identical |
| Promotion planner `plan_ready` | True (file inventory only) | True (unchanged) |
| Production-root diff | 0/0/0 | 0/0/0 |

---

## 6. Website / rendering preview

| Surface | Behaviour |
|---|---|
| Phase 6I-34 ranking export | Default member-completeness provider auto-surfaces `data_completeness_status='partial'` from a future Confluence artifact that carries the new metadata. Test fixture verifies TEF surfaces with structured `reason:telemetry_reason` strings. |
| Phase 6I-35 website export package | Carries the row's `data_completeness` block through unchanged. |
| Phase 6I-36 website reader / view | Same. |
| Phase 6I-41 static board renderer | Existing rendering of `data_completeness.data_warning_symbol`, `data_completeness.incomplete_members`, and `data_completeness.data_completeness_status` is sufficient — no schema change needed. Smoke run rc=0, 52,164-byte HTML. |
| Phase 6I-42 local overlays | Same. Smoke run with `--with-local-overlays` rc=0, 52,164-byte HTML. |

The current on-disk Confluence artifacts predate the Phase 6I-46 patch
writer being authorized to land partial-payload metadata, so the
ranking export reports the same blocked-state today as in Phase 6I-45
(15 inspected / 0 eligible / 15 blocked). The new ranking export code
is **forward-compatible**: as soon as an artifact carries the new
fields, the partial state surfaces automatically.

---

## 7. Production-root diff (pre / post phase)

```
PRE  : 3239 / 1634 / 35 / 5224 / 72899
POST : 3239 / 1634 / 35 / 5224 / 72899

cache/results:               modified 0  added 0  removed 0
cache/status:                modified 0  added 0  removed 0
output/research_artifacts:   modified 0  added 0  removed 0
output/stackbuilder:         modified 0  added 0  removed 0
signal_library/data/stable:  modified 0  added 0  removed 0
```

Zero production-root activity. The 70 staged libraries live under the
working-tree `_phase_6i_46_staged_libraries/` directory (gitignored,
deleted at end of phase). The static board HTML files and all
intermediate JSON evidence files also live in the working tree.

---

## 8. Verdict

**Phase 6I-46 verdict:** **PARTIAL_SURFACE_READY + STRICT_WRITE_STILL_BLOCKED.**

  * **Partial surface ready.** The chain now emits an honest partial
    / blocked state when authored members are invalid. The SPY/TEF
    case produces:
    * adapter `data_completeness_status='partial'` + `data_warning_symbol='!'`;
    * payload `partial_payload_available=True` (with strict
      `payload_ready=False`);
    * planner `recommended_next_action='partial_payload_not_promotable'`;
    * writer dry-run `wrote_artifact=False` with pre/post SHA equal.
    The ranking export's member-completeness provider auto-surfaces
    the partial state from future artifacts. The website / view /
    renderer / overlays inherit the partial state through the
    Phase 6I-40 `data_completeness` block without schema changes.

  * **Strict write still blocked.** Partial payloads are display-only
    in Phase 6I-46. The Phase 6I-20 strict 60-cell complete-payload
    contract is preserved verbatim; `payload_ready`, `patch_ready`,
    and `can_evaluate_full_60_cell_grid` all stay False under partial.
    The patch writer continues to refuse mutations. The stable
    promotion writer continues to refuse mutations.

  * **Strict complete behaviour byte-identical** for any caller that
    does not pass `invalid_members`. The 24 new Phase 6I-46 tests +
    the 2,164 existing tests (2,188 total) all pass; no test was
    modified to accommodate the new fields.

  * **TEF is not silently dropped.** TEF surfaces in
    `original_members_by_K`, `excluded_members_by_K`,
    `incomplete_member_detail`, every affected per-cell
    `excluded_members`, and the planner's `issue_codes`. The
    ranking export inspects all 15 SPY-K-universe tickers and
    surfaces TEF honestly in `blocked_rows` with reason
    `artifact_missing` (its Phase 6I-44 cache state, untouched).

---

## 9. Next step

The Phase 6I-46 partial surface is **display-only**. To make the
partial state user-visible on the website board (the actual product
goal), one of the following separate Phase 6I-47 directions is
required:

1. **Define a partial-payload artifact contract.** Allow the
   Phase 6I-25 patch writer to merge the partial payload into the
   Confluence artifact under a clearly labeled
   `data_completeness_status='partial'` block (NOT under the strict
   `per_window_k_metrics` / `build_wide_window_alignment` / metadata
   keys — those remain reserved for strict complete payloads). The
   ranking export's existing provider already reads from
   `multiwindow_k_engine_payload_metadata.data_completeness_status`
   so a partial label there flows through to the website.
2. **Rebuild SPY StackBuilder without TEF.** Avoid the partial path
   entirely for SPY by re-running StackBuilder with the 11-member
   non-TEF set (or a 12-member set where TEF is replaced by a
   search-selected valid member). See Phase 6I-45 doc § 9 Option B
   for the K-grid contract caveats (55-cell vs 60-cell vs K-ceiling
   convention).
3. **Defer the SPY board surface** and iterate the multi-ticker
   board against other tickers whose StackBuilder runs are free of
   invalid members.

Each direction is its own supervised phase. None is authorized in
Phase 6I-46.

---

## 10. Tests run

  * **Full regression**: `pytest test_scripts -q` → **2,188 passed**
    (was 2,164 before; +24 new Phase 6I-46 tests). 165 pre-existing
    pandas-fragmentation warnings, unchanged from the sprint baseline.
    No new warnings.

  * **Phase 6I-46 focused suite**:
    `pytest test_scripts/test_phase_6i46_trafficflow_compatible_invalid_member_handling.py -q`
    → **24 / 24 passed**. Covers:
    * pin: complete-behaviour byte-identical when `invalid_members` is
      absent / empty (× 2);
    * SPY/TEF fixture produces per-cell `unprepared_due_to_excluded_members`,
      preserves `original_members`, derives `effective_members`,
      carries structured `excluded_members`, reports `partial`
      status with `!` warning, preserves strict
      `can_evaluate_full_60_cell_grid=False`, and emits the new
      issue code (× 7);
    * blocked status when zero cells prepare (× 1);
    * diagnostic surfaces the new fields + per-cell exclusion records
      (× 2);
    * payload builder threads completeness fields, preserves strict
      `payload_ready=False` under partial, sets
      `partial_payload_available=True` only when prepared > 0,
      complete-status path unchanged (× 3);
    * planner emits `partial_payload_not_promotable` issue code +
      action; complete-status path unchanged (× 2);
    * patch writer dry-run refuses writes for partial plans (× 1);
    * ranking export's member-completeness provider auto-surfaces
      partial from an artifact carrying the new fields; baseline
      shape preserved for legacy artifacts; top-level and metadata
      location both supported (× 3);
    * partial payload never mistaken for strict complete coverage
      (× 1);
    * static / forbidden-import guards still pass; new constants
      live on the public modules (× 2).

  * **Touched-module focused suite**: `pytest` on the 12 modules
    threaded by Phase 6I-46 → **381 / 381 passed** (no new failures,
    no warning regressions).

  * **Static guards** (`py_compile` on every changed module):
    * `multiwindow_k_input_adapter.py`
    * `multiwindow_k_input_adapter_diagnostic.py`
    * `multiwindow_k_engine_payload_builder.py`
    * `multiwindow_k_confluence_patch_planner.py`
    * `confluence_multiwindow_ranking_export.py`
    * `signal_library_fresh_staging_readiness.py`

  * **`git diff --check`** → clean.

---

## 11. No-production-activity confirmation

| Surface | Touched? |
|---|---|
| `cache/results` | **No** (0 / 0 / 0 diff vs pre-phase) |
| `cache/status` | **No** (0 / 0 / 0 diff) |
| `output/research_artifacts` | **No** (0 / 0 / 0 diff) |
| `output/stackbuilder` | **No** (0 / 0 / 0 diff) |
| `signal_library/data/stable` | **No** (0 / 0 / 0 diff) |
| `_phase_6i_46_staged_libraries/` (working tree, gitignored .pkl + .json) | Yes — sandbox built 70 PKLs + 70 manifests; not in any production root |
| Confluence patch writer (`multiwindow_k_confluence_patch_writer`) | dry-run only; `wrote_artifact=False`; pre SHA == post SHA |
| Signal-library promotion writer (`signal_library_stable_promotion_writer`) | NOT invoked in this phase (Phase 6I-45 evidence still authoritative) |
| `PRJCT9_AUTOMATION_WRITE_AUTH` env var | **Never set** |
| Source refresh (`signal_engine_cache_refresher --write`) | **Not invoked** |
| `yfinance` fetch | **None** (`--skip-source-availability`; cache-only reads) |
| `confluence_pipeline_runner` | **Not invoked** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster batch | **Not invoked** |

---

## 12. Evidence artifact index (working-tree, not committed)

| Artifact | Purpose |
|---|---|
| `_phase_6i_46_pre_snapshot.json` | Pre-phase production-root snapshot. |
| `_phase_6i_46_post_snapshot.json` | Post-phase production-root snapshot. |
| `_phase_6i_46_diff_report.txt` | Pre/post diff (0/0/0 across all 5 roots). |
| `_phase_6i_46_snapshot_tool.py` | Snapshot helper (read-only). |
| `_phase_6i_46_diff_tool.py` | Diff helper (read-only). |
| `_phase_6i_46_staging_readiness.json` | Full Phase 6I-32 harness output with `--invalid-members-json`. |
| `_phase_6i_46_staging_readiness.stderr` | Builder progress. |
| `_phase_6i_46_staged_libraries/` | 70 PKLs + 70 manifests for the 14 non-TEF tickers × 5 intervals. |
| `_phase_6i_46_ranking_export.json` | Phase 6I-34 multi-ticker ranking export (15 inspected / 0 eligible / 15 blocked; baseline preserved). |
| `_phase_6i_46_board.html` | Phase 6I-41 static board (no overlays), 52,164 bytes. |
| `_phase_6i_46_board_with_overlays.html` | Phase 6I-42 static board with local overlays, 52,164 bytes. |

These working-tree files are intentionally not committed; the
authoritative record of the phase is this markdown plus the code +
test changes.

---

## 13. Files changed

| File | Change |
|---|---|
| `project/multiwindow_k_input_adapter.py` | New `ExclusionRecord` dataclass; new `invalid_members` parameter; per-cell `excluded_members` field; report-level `original_members_by_K` / `effective_members_by_K` / `excluded_members_by_K` / `incomplete_member_detail` / `data_completeness_status` / `data_warning_symbol` fields; new `REASON_UNPREPARED_DUE_TO_EXCLUDED_MEMBERS` skipped reason; new `ISSUE_EXCLUDED_INVALID_MEMBER` issue code. |
| `project/multiwindow_k_input_adapter_diagnostic.py` | New `--invalid-members-json` CLI flag; `invalid_members` forwarded to adapter; diagnostic JSON gains the new fields. |
| `project/multiwindow_k_engine_payload_builder.py` | `invalid_members` parameter; `AdapterSummary` + `MultiWindowKEnginePayloadReport` mirror the new fields; new `partial_payload_available` flag. |
| `project/multiwindow_k_confluence_patch_planner.py` | `invalid_members` parameter; new `ISSUE_PARTIAL_PAYLOAD_NOT_PROMOTABLE` + `ACTION_PARTIAL_PAYLOAD_NOT_PROMOTABLE`; plan mirrors completeness fields; partial / blocked upstream → planner emits new issue code + action. |
| `project/confluence_multiwindow_ranking_export.py` | `_default_member_completeness_provider` reads `data_completeness_status` / `incomplete_member_detail` from artifact (top-level or under metadata) and translates to the existing schema. |
| `project/signal_library_fresh_staging_readiness.py` | `invalid_members` parameter + `--invalid-members-json` CLI flag; forwarded to all four downstream callables (adapter / payload / planner / writer). |
| `project/test_scripts/test_phase_6i46_trafficflow_compatible_invalid_member_handling.py` | **New** focused-test module with 24 tests. |
| `project/md_library/shared/2026-05-15_PHASE_6I46_TRAFFICFLOW_COMPATIBLE_INVALID_MEMBER_HANDLING.md` | **New** evidence doc (this file). |

The patch writer (`multiwindow_k_confluence_patch_writer.py`) and the
website export / view / static renderer / overlays modules required
no code changes — their existing schemas already accommodate the
new partial state via the Phase 6I-40 `data_completeness` block and
the planner's existing `patch_ready=False` cascade.
