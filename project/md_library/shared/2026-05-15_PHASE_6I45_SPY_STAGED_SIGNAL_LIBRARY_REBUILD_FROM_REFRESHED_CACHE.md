# Phase 6I-45: SPY K-universe staged signal-library rebuild from the refreshed cache

**Date:** 2026-05-15
**Scope:** the 15-ticker SPY K-universe — SPY, AROW, AWR, CLH, CP, EXPO,
FCFS, GBCI, HCSG, JNJ, LLY, MO, PRA, PRGO, TEF.
**Authorization basis:** read-only against production stable + Confluence
roots. Staged signal-library output written only to
`_phase_6i_45_staged_libraries/` (outside all five production roots).
No source refresh, no production write, no `--write` on any guarded
writer, no `PRJCT9_AUTOMATION_WRITE_AUTH`, no yfinance, no
`confluence_pipeline_runner`, no StackBuilder / OnePass / ImpactSearch /
TrafficFlow / Spymaster batch execution.
**Verdict:** **BLOCKED**. The 14 non-TEF staged libraries build cleanly
and the downstream chain runs without error, but only 30 of the 60
multi-window K cells prepare. The structural blocker is below.

---

## 0. Top-line summary

| Stage | Result | Numbers |
|---|---|---|
| Pre-phase production-root snapshot | clean | 3239 / 1634 / 35 / 5223 / 72899 = 83,030 files |
| Staged sandbox build (14 non-TEF tickers × 5 intervals) | OK | 70 PKLs + 70 manifests written; 0 failed |
| TEF exclusion | OK | TEF absent from staged dir, classified `invalid_or_delisted` upstream by Phase 6I-43 |
| Interval-native close | OK | Every staged library carries `interval` + `close` matching manifest |
| Adapter diagnostic | **BLOCKED** | `prepared_cell_count = 30`, `skipped_cell_count = 30`, `can_evaluate_full_60_cell_grid = False` |
| Payload builder | **BLOCKED** | `payload_ready = False`, `issue_codes = ['adapter_not_ready']` |
| Patch planner | **BLOCKED** | `patch_ready = False`, `issue_codes = ['payload_not_ready']` |
| Patch writer dry-run | clean dry-run | `planner_patch_ready = False`, `wrote_artifact = False`, `pre_sha == post_sha` |
| Promotion planner | OK | `plan_ready = True`, 70/70 files found, 44 add / 26 replace / 0 unchanged |
| Promotion writer dry-run | clean dry-run | `wrote_files = False`, `files_added = files_replaced = files_unchanged = []`, `write_requested = False`, `write_authorized = False` |
| Ranking export | honest blocked surface | inspected 15 / eligible 0 / blocked 15 (`daily_only` 1 + `artifact_missing` 14) |
| Static board renderer (no overlays) | OK | 52,164-byte HTML emitted; rc=0; empty stderr |
| Static board renderer (with local overlays) | OK | 52,164-byte HTML emitted; rc=0; empty stderr |
| Post-phase production-root diff | clean | 0 / 0 / 0 / 0 / 0 across all 5 roots |
| Focused tests (391 tests across 13 modules) | OK | 391 passed, 105 pre-existing pandas fragmentation warnings (no new) |

---

## 1. Phase 6I-44 dependency summary

Phase 6I-44 (PR #261, merged 2026-05-15 at `431ae5c`) supervised the
refresh of 14 non-TEF SPY K-universe ticker caches to
`cache_date_range_end == 2026-05-14`. TEF remained `invalid_or_delisted`
under Phase 6I-43 policy v2 (reason: `provider_fetch_failed_zero_rows`)
and its cache + status were untouched at the prior `2026-01-28` end.
Production roots after Phase 6I-44 closed at 3239 / 1634 / 35 / 5223 /
72899 files.

Phase 6I-45 consumes that refreshed cache to build staged signal
libraries for the 14 non-TEF tickers, runs the full Phase 6I-22..6I-31
downstream chain against the staged dir, and prepares evidence for a
potential separate supervised stable promotion phase. **No write to
production is authorized in Phase 6I-45.**

---

## 2. Exact staged build inputs + TEF exclusion rationale

### 2.1 Inputs

| Parameter | Value |
|---|---|
| `--tickers` | `SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO` (14 non-TEF) |
| `--primary-ticker` | `SPY` |
| `--staged-dir` | `_phase_6i_45_staged_libraries` (relative to `project/`, outside all 5 production roots) |
| `--cache-dir` | `cache/results` |
| `--current-as-of-date` | `2026-05-14` |
| `--skip-source-availability` | set (the cache is already at the cutoff date; source-availability would re-confirm equality) |
| Intervals | `1d,1wk,1mo,3mo,1y` (Phase 6I-32 default) |
| Python interpreter | pinned `spyproject2`: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe` |

### 2.2 TEF exclusion rationale

TEF is `invalid_or_delisted` under Phase 6I-43 policy v2 with reason
`provider_fetch_failed_zero_rows` (yfinance fetch_attempted=True,
fetch_succeeded=False, rows=0, warning "possibly delisted"). The
Phase 6I-44 supervised refresh round explicitly EXCLUDED TEF from the
14 authorized refresh commands; TEF's cache is untouched at
`2026-01-28`. Phase 6I-45 likewise excludes TEF from the staged build
inputs, in accordance with the user's instruction:

> *"TEF must remain excluded from staged rebuild commands / staged
> libraries. Evidence must explicitly show TEF as
> invalid/delisted / excluded / warning-member, not silently omitted."*

TEF is **not silently omitted**: it is surfaced in the Phase 6I-43
verdict, surfaced in the ranking export's `blocked_rows` with
`ranking_blocked_reason='artifact_missing'`, and is the central
participant in the Phase 6I-45 BLOCKED verdict (see § 4.1). The
warning that the original SPY K-universe build had one
invalid member is preserved in this evidence doc, in the Phase 6I-44
evidence doc, and in the Phase 6I-43 policy v2 verdict.

### 2.3 Staging directory is outside production roots

`_phase_6i_45_staged_libraries/` is:

  * under `project/` (the working tree),
  * NOT under `signal_library/data/stable` (the production stable
    library root),
  * NOT under `cache/results`, `cache/status`,
    `output/research_artifacts`, or `output/stackbuilder`.

The Phase 6I-32 staging harness's `_path_is_under_production_stable()`
guard runs before any disk-touching stage and would have refused the
build with `staged_dir_under_production_stable` if the path resolved
under signal_library/data/stable. The guard did not trip. The robust
staged-dir production-root guard is preserved.

---

## 3. Staged library counts by ticker / window + native close

### 3.1 Per-ticker × per-interval file count

Each of the 14 non-TEF tickers has 5 staged library PKLs and 5 matching
`.manifest.json` sidecars:

```
_phase_6i_45_staged_libraries/
  <TICKER>_stable_v1_0_0.pkl              + manifest   (interval=1d)
  <TICKER>_stable_v1_0_0_1wk.pkl          + manifest   (interval=1wk)
  <TICKER>_stable_v1_0_0_1mo.pkl          + manifest   (interval=1mo)
  <TICKER>_stable_v1_0_0_3mo.pkl          + manifest   (interval=3mo)
  <TICKER>_stable_v1_0_0_1y.pkl           + manifest   (interval=1y)
```

Total = 14 × (5 PKLs + 5 manifests) = **70 PKLs + 70 manifests = 140
files in the staged dir.**

Harness reported: `sandbox_build_attempted = True`,
`sandbox_build_written = 70`, `sandbox_build_failed = 0`.

`grep -i tef _phase_6i_45_staged_libraries/`: **no match** — TEF
correctly absent.

### 3.2 Interval-native close verification

Inspection of two representative staged libraries (SPY base + SPY
weekly):

| Field | `SPY_stable_v1_0_0.pkl` | `SPY_stable_v1_0_0_1wk.pkl` |
|---|---|---|
| `interval` (in-pickle) | `"1d"` (implied via builder identity) | `"1wk"` |
| `close` array | present | present |
| `manifest.interval` | `1d` | `1wk` |

The Phase 6I-30 interval-native close contract holds: each per-interval
staged library carries its own `interval` field + its own `close`
array, the manifest's `interval` matches the in-pickle interval, and
SPY weekly close is NOT the same array as SPY daily close (the
Phase 6I-30 sandbox proof + the focused
`test_multi_timeframe_builder_interval_close.py` suite both held in
this phase — 391 / 391 focused tests passed including all 11 interval
close cases).

---

## 4. Downstream readiness chain — full table

### 4.1 Adapter diagnostic — BLOCKED at 30 / 60 cells

| Field | Value |
|---|---|
| `selected_run_dir` | `output\stackbuilder\SPY\seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D` |
| `selected_run_id` | `seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D` |
| `canonical_k_values_inspected` | `[1,2,3,4,5,6,7,8,9,10,11,12]` |
| `canonical_windows_inspected` | `['1d','1wk','1mo','3mo','1y']` |
| `expected_canonical_cell_count` | 60 |
| `prepared_cell_count` | **30** |
| `skipped_cell_count` | **30** |
| `can_evaluate_full_60_cell_grid` | **False** |
| `dominant_skipped_reason` | `incomplete_member_coverage` |
| `adapter_issue_codes` | `['missing_member_library', 'incomplete_member_coverage']` |
| `counts_by_skipped_reason` | `{'incomplete_member_coverage': 30}` |
| `missing_libraries_by_ticker_window` | **`TEF: ['1d','1wk','1mo','3mo','1y']`** |

Per-cell distribution of the 30 skipped cells: 6 cells per window × 5
windows. The 6 skipped K-values per window are **K ∈ {7,8,9,10,11,12}**;
the 6 prepared K-values per window are **K ∈ {1,2,3,4,5,6}**. Every
skipped cell has `members_missing=['TEF']`. This is the structural
blocker — see § 5.

### 4.2 Payload builder — blocked downstream of adapter

| Field | Value |
|---|---|
| `payload_ready` | False |
| `cell_count` | 0 |
| `issue_codes` | `['adapter_not_ready']` |

### 4.3 Patch planner — blocked downstream of payload

| Field | Value |
|---|---|
| `patch_ready` | False |
| `issue_codes` | `['payload_not_ready']` |

### 4.4 Patch writer dry-run — clean dry-run, no write

| Field | Value |
|---|---|
| `write_requested` | False |
| `write_authorized` | False |
| `planner_patch_ready` | False |
| `wrote_artifact` | **False** |
| `pre_write_sha256`  | `db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f` |
| `post_write_sha256` | `db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f` |
| Pre vs post SHA | **EQUAL** — Confluence artifact byte-identical pre and post |

The patch writer correctly refused to write because the upstream chain
is blocked. `PRJCT9_AUTOMATION_WRITE_AUTH` was never set during this
phase. No `--write` flag was passed.

### 4.5 Ranking export — honest blocked surface

| Field | Value |
|---|---|
| `inspected_count` | 15 |
| `eligible_count` | 0 |
| `blocked_count` | 15 |
| `blocked_reason_counts` | `{'daily_only': 1, 'artifact_missing': 14}` |
| `data_status_counts` | `{'daily_only': 1, 'missing': 14}` |
| `freshness_status_counts` | `{'unknown': 15}` |

The 1 `daily_only` blocked row is SPY — its on-disk Confluence
artifact predates the Phase 6I-25 multi-window patch (no `--write` has
ever been authorized for that surface), so it carries daily-only
fields, not the Phase 6I-20 multi-window fields. The 14
`artifact_missing` rows are the other 14 tickers (no Confluence
artifact exists for them yet). TEF is included in the inspected set
and surfaces as `artifact_missing`, NOT silently dropped.

### 4.6 Static board renderer

Both invocations succeeded read-only with empty stderr:

| Mode | Output | Size |
|---|---|---|
| `--from-tickers SPY,...,TEF --artifact-root output/research_artifacts` | `_phase_6i_45_board.html` | 52,164 bytes |
| Same + `--with-local-overlays` against the 4 production roots | `_phase_6i_45_board_with_overlays.html` | 52,164 bytes |

Renderer output went to working-tree HTML files; the renderer's
`_refuse_production_root()` guard ensured no production root was used
as an `--output` target.

---

## 5. Structural blocker — exact root cause

The single SPY StackBuilder seed run on disk is

```
output/stackbuilder/SPY/seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D
```

This seed run encodes a **12-member set including TEF** (see `TEF-I` in
the run_id). The Phase 6I-22 input adapter consumes this run as the
authoritative member list for the SPY multi-window K grid. The
12-member set drives a 12-K-value × 5-window canonical 60-cell grid,
where K ∈ {1..12}.

For K ∈ {7..12} the adapter's combine-threshold enumeration cannot
satisfy member coverage without TEF: every K-cell in that range
requires at least one alignment-check subset that includes TEF.
Because TEF is correctly absent from the Phase 6I-45 staged library
dir (per Phase 6I-43 invalid_or_delisted + Phase 6I-44 untouched
contract), the adapter skips those cells with
`skipped_reason='incomplete_member_coverage'` and
`members_missing=['TEF']`.

For K ∈ {1..6} the adapter's enumeration can satisfy member coverage
using subsets of the 11 non-TEF members, so those cells prepare
cleanly.

**The blocker is structural: the on-disk StackBuilder seed run was
generated before TEF was flagged invalid_or_delisted, and permanently
encodes TEF as a member.** Re-running the staging harness or pointing
the chain at a different staged dir would not change this verdict —
the adapter would still read the same seed run and still require TEF
for K ∈ {7..12}.

---

## 6. Promotion preparation — read-only / dry-run

The Phase 6I-31 promotion planner against the staged dir and
production stable dir:

| Field | Value |
|---|---|
| `plan_ready` | **True** |
| `expected_file_count` | 70 |
| `staged_files_found` | 70 |
| `staged_files_missing` | 0 |
| `libraries_to_add` | 44 |
| `libraries_to_replace` | 26 |
| `libraries_unchanged` | 0 |
| `issue_codes` | `[]` |

The 26 to-be-replaced libraries are the existing
`SPY_stable_v1_0_0.pkl` (× 5 intervals) plus the 13 other tickers'
base `_stable_v1_0_0.pkl` artifacts already in production (one per
ticker — production never had the four per-interval variants for
those tickers). The 44 to-be-added libraries are the four
per-interval variants for the 11 other tickers (11 × 4 = 44).

The Phase 6I-31 promotion writer in dry-run mode (no `--write`, no
`PRJCT9_AUTOMATION_WRITE_AUTH` env var set):

| Field | Value |
|---|---|
| `write_requested` | False |
| `write_authorized` | False |
| `plan_ready` | True |
| `wrote_files` | **False** |
| `files_added` | `[]` |
| `files_replaced` | `[]` |
| `files_unchanged` | `[]` |
| `sidecars_copied` | `[]` |
| `issue_codes` | `['write_not_requested']` |
| `recommended_next_action` | `dry_run_review_promotion_plan` |

**Important caveat for Phase 6I-46.** The promotion planner reports
`plan_ready=True` for the staged 14-ticker set BECAUSE the staged
files are byte-coherent against expected file naming, NOT because the
downstream multi-window chain is ready. The chain is BLOCKED at 30 /
60 (see § 4.1). Even if the operator authorized the promotion writer
to actually copy the 70 staged libraries into `signal_library/data/
stable`, the SPY downstream chain would STILL fail at 30 / 60 because
the StackBuilder seed run requires TEF. **Promotion is not the
unblocker; the StackBuilder member set is.**

---

## 7. Production-root diff (pre / post phase)

```
PRE  counts: 3239 / 1634 / 35 / 5223 / 72899
POST counts: 3239 / 1634 / 35 / 5223 / 72899

cache/results:               modified 0  added 0  removed 0
cache/status:                modified 0  added 0  removed 0
output/research_artifacts:   modified 0  added 0  removed 0
output/stackbuilder:         modified 0  added 0  removed 0
signal_library/data/stable:  modified 0  added 0  removed 0
```

**Zero production-root activity in Phase 6I-45.** The phase is
strictly read-only against the five production roots; all staged
output lives under the working-tree `_phase_6i_45_staged_libraries/`
directory, and all evidence artifacts live under working-tree
`_phase_6i_45_*` files. None of these are committed.

---

## 8. Verdict — BLOCKED

**Phase 6I-45 verdict:** **BLOCKED — NOT READY for guarded stable
promotion.**

**Blocker:** the SPY StackBuilder seed run
`seedTC__...TEF-I...` encodes TEF as a member; Phase 6I-43 has
flagged TEF as `invalid_or_delisted`; Phase 6I-44 left TEF's cache
untouched; the Phase 6I-45 staged build correctly excludes TEF; the
Phase 6I-22 adapter cannot prepare 30 / 60 multi-window K cells
(K ∈ {7..12} × all 5 windows) because they require TEF as a member.
Payload / patch planner / patch writer dry-run all correctly cascade
the BLOCKED state. Production-stable promotion would land 70 clean
libraries but would NOT close the structural blocker, because the
blocker is in `output/stackbuilder/SPY/`, not in
`signal_library/data/stable/`.

### Why this is the honest verdict, not a regression

- Phase 6I-44 deliberately chose to flag TEF and exclude it from
  refresh / write surfaces. That choice was correct on the merits
  (TEF is genuinely delisted) and is the same choice surfaced here.
- The Phase 6I-30 sandbox 60-cell SPY proof preceded the Phase 6I-43
  invalid-member-handling design; at that time the staged build
  INCLUDED TEF's 1-day library and the adapter saw 12 members. Phase
  6I-45 is the first time the chain is run end-to-end with TEF
  formally excluded — the BLOCKED verdict is the predictable
  consequence of that design choice, surfaced honestly.

---

## 9. Smallest fix → Phase 6I-46 candidate scopes

Three candidate paths can unblock the 60-cell grid. None is authorized
in Phase 6I-45. The Phase 6I-46 prompt should pick one (or
deliberately defer):

### Option A (recommended) — rebuild SPY StackBuilder with the 11-member set

Re-run StackBuilder for SPY with member universe **AROW, AWR, CLH,
CP, EXPO, FCFS, GBCI, HCSG, JNJ, LLY, MO, PRA, PRGO** (TEF
dropped). The resulting seed run drives an 11-K-value × 5-window
canonical 55-cell grid (or the existing 60-cell convention with K
ceiling at 11; verify Phase 6I-21 engine treats the universe size as
a parameter). The adapter then has no missing-member dependency
because the seed run's member list matches the staged library set.

  * Pros: cleanest fix; the universe is coherent; all downstream cells
    can prepare.
  * Cons: requires authorizing StackBuilder batch execution for SPY
    in a separate supervised phase; explicit operator authorization
    for `output/stackbuilder/SPY/` writes (the StackBuilder writer
    surface is currently NOT a guarded `--write` API — needs
    confirmation). May also change the 60-cell convention to a
    55-cell convention if K ceiling tracks universe size.
  * Net effect: SPY's per-window K cells become evaluable end-to-end
    against the refreshed cache.

### Option B — backfill TEF's 4 missing per-interval libraries from the frozen 2026-01-28 cache

Production stable currently has only `TEF_stable_v1_0_0.pkl` (the
1-day base). Build the 4 per-interval variants
(`TEF_stable_v1_0_0_1wk.pkl`, `_1mo`, `_3mo`, `_1y`) from TEF's
frozen 2026-01-28 cache, with explicit `pinned_at=2026-01-28` and
`provider_status=invalid_or_delisted` annotations on each manifest.
Include those 4 + the base TEF library in the staged dir under a
distinct prefix or naming convention (e.g.
`TEF_pinned_v1_0_0_*.pkl`) so the adapter recognizes them as
pinned-invalid members.

  * Pros: keeps the existing 12-member StackBuilder run usable;
    smaller blast radius than Option A.
  * Cons: TEF's pinned 2026-01-28 close values are stale by ~3.5
    months relative to the 14 other tickers' 2026-05-14 close
    values, so K-cells containing TEF carry a stale-member warning
    and may be unsuitable for live ranking even after coverage is
    "complete." The adapter may also need a small amendment to
    recognize the pinned-invalid naming convention and pass a
    `pinned_invalid_member` flag through to the payload builder.
  * Net effect: 60 / 60 coverage is technically achievable but
    members for K ∈ {7..12} carry a stale-member warning.

### Option C — defer the SPY board surface; pivot to a multi-ticker minus-SPY board

Skip SPY for now. Build the Phase 6I-34..6I-42 multi-ticker board
against tickers that ALREADY have refresh-and-rebuild paths free of
invalid members. Wait until either Option A or Option B lands before
restoring SPY to the leader-eligible set.

  * Pros: zero StackBuilder work; immediate board iteration on the
    13 other tickers.
  * Cons: SPY remains parked indefinitely; the SPY pilot path stops
    being the proof path; the leader-row hero card on the Daily Signal
    Board may need a temporary alternate primary ticker.

### Recommendation

**Option A is the cleanest fix** if StackBuilder batch execution for
SPY is acceptable in Phase 6I-46. Option B is the smallest
incremental fix if it isn't. Option C is the deferral path.

---

## 10. Tests run (read-only, focused)

`pytest test_scripts/{test_signal_library_fresh_staging_readiness,
test_signal_library_stable_promotion,
test_multi_timeframe_builder_interval_close,
test_multiwindow_k_input_adapter,
test_multiwindow_k_input_adapter_diagnostic,
test_multiwindow_k_engine_payload_builder,
test_multiwindow_k_confluence_patch_planner,
test_multiwindow_k_confluence_patch_writer,
test_confluence_multiwindow_ranking_export,
test_confluence_static_board_renderer,
test_confluence_board_runtime_overlays,
test_confluence_website_export_package,
test_confluence_website_reader_view}.py -q`

Result: **391 passed, 105 pre-existing pandas-fragmentation warnings
(unchanged from sprint baseline)**. No new warnings. No focused-suite
failures.

No Python code or test was modified in Phase 6I-45 — this is an
evidence-only phase. No `py_compile` errors. `git diff --check` is
clean (no whitespace / conflict markers).

---

## 11. No-production-activity confirmation

| Surface | Touched? |
|---|---|
| `cache/results` | **No** (0 / 0 / 0 diff vs pre-phase) |
| `cache/status` | **No** (0 / 0 / 0 diff vs pre-phase) |
| `output/research_artifacts` | **No** (0 / 0 / 0 diff vs pre-phase) |
| `output/stackbuilder` | **No** (0 / 0 / 0 diff vs pre-phase) |
| `signal_library/data/stable` | **No** (0 / 0 / 0 diff vs pre-phase) |
| `_phase_6i_45_staged_libraries/` (working tree, gitignored .pkl + .json) | Yes — sandbox build wrote 140 files; not in any production root |
| Confluence patch writer (`multiwindow_k_confluence_patch_writer`) | dry-run only; `wrote_artifact=False`; pre SHA == post SHA |
| Signal-library promotion writer (`signal_library_stable_promotion_writer`) | dry-run only; `wrote_files=False`; `files_added=files_replaced=files_unchanged=[]` |
| `PRJCT9_AUTOMATION_WRITE_AUTH` env var | **Never set** |
| Source refresh (`signal_engine_cache_refresher --write`) | **Not invoked** |
| `yfinance` fetch | **None** (all probes used `--skip-source-availability` or read cache only) |
| `confluence_pipeline_runner` | **Not invoked** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster batch | **Not invoked** |

The two dry-run guarded writers (patch writer + promotion writer) both
correctly refused to mutate any production artifact: the
multiwindow_k_confluence_patch_writer reports `wrote_artifact=False`
with `pre_write_sha256 == post_write_sha256`; the
signal_library_stable_promotion_writer reports `wrote_files=False`
with all add/replace/unchanged lists empty.

---

## 12. Evidence artifact index (working-tree, not committed)

| Artifact | Purpose |
|---|---|
| `_phase_6i_45_pre_snapshot.json` | Pre-phase production-root snapshot. |
| `_phase_6i_45_post_snapshot.json` | Post-phase production-root snapshot. |
| `_phase_6i_45_diff_report.txt` | Pre/post diff (0/0/0 across all 5 roots). |
| `_phase_6i_45_snapshot_tool.py` | Snapshot helper (read-only). |
| `_phase_6i_45_diff_tool.py` | Diff helper (read-only). |
| `_phase_6i_45_staging_readiness.json` | Full Phase 6I-32 harness output. |
| `_phase_6i_45_staging_readiness.stderr` | Builder progress (no errors). |
| `_phase_6i_45_staged_libraries/` | 70 PKLs + 70 manifests for the 14 non-TEF tickers × 5 intervals. |
| `_phase_6i_45_promotion_plan.json` | Phase 6I-31 promotion planner output. |
| `_phase_6i_45_promotion_writer_dryrun.json` | Phase 6I-31 promotion writer dry-run output. |
| `_phase_6i_45_ranking_export.json` | Phase 6I-34 multi-ticker ranking export. |
| `_phase_6i_45_board.html` | Phase 6I-41 static board (no overlays), 52,164 bytes. |
| `_phase_6i_45_board_with_overlays.html` | Phase 6I-42 static board with local overlays, 52,164 bytes. |

These working-tree files are intentionally not committed. They are
intermediate evidence files that this document summarizes; the
authoritative record of the phase is this markdown.

---

## 13. Next step

If the operator picks **Option A** (recommended): open a separate
supervised Phase 6I-46 prompt to re-run StackBuilder for SPY with the
11-member non-TEF universe, then re-run the Phase 6I-45 harness
against the new seed run. Expected verdict on success:
`prepared_cell_count == 55` (or 60 if the engine treats the K ceiling
as a fixed parameter), `can_evaluate_full_grid == True`,
`payload_ready == True`, `patch_ready == True`,
`planner_patch_ready == True`. Only then is a separate Phase 6I-47
supervised Confluence patch writer + stable promotion authorization
appropriate.

If the operator picks **Option B**: open a separate supervised
Phase 6I-46 prompt to backfill TEF's 4 missing per-interval libraries
from the frozen 2026-01-28 cache, with explicit `pinned_invalid`
annotation, and amend the adapter (Phase 6I-22) to recognize pinned-
invalid members. Re-run the Phase 6I-45 harness with TEF included as a
pinned member.

If the operator picks **Option C**: continue the multi-ticker board
work without SPY and document the SPY-paused state on the front of
the Daily Signal Board.

**No write is authorized in this phase. The verdict is BLOCKED with
exact, structural blocker reasons. PR #261 closed Phase 6I-44; the
SPY pilot remains parked.**
