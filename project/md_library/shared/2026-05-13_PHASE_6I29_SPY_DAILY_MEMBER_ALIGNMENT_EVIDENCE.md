# Phase 6I-29: exact-date member alignment for daily multi-window K cells + SPY evidence

Sprint date: **2026-05-13** (evidence captured **2026-05-14** UTC).
Branch: `phase-6i-29-spy-daily-member-alignment`.
Ticket: adapter (`multiwindow_k_input_adapter.py`) + tests.
Doc: this file.

Phase 6I-28 (PR #245) resolved the Phase 6I-27 inferred-then-proven `missing_target_close` blocker for the daily window via the opt-in close-source join — but the chain then exposed a deeper directly-observed blocker: SPY's daily member signal libraries (PRGO 8,585 bars; AWR 13,343 bars; etc) have different bar counts than SPY's daily library (8,302 bars), so the Phase 6I-22 strict length-check rejected every member as missing. The Phase 6I-28 evidence pass landed on the `no_members_available` reason for all 12 daily cells.

Phase 6I-29 implements the smallest production-safe widening of the adapter's member-loading contract that resolves the daily blocker: **exact-date alignment of member signals onto the target's date axis** when bar counts disagree. Strict semantics — no resample / no ffill / no interpolation / no "nearest date" / no fabrication. The Phase 6I-22 strict full-member-coverage gate is preserved verbatim.

---

## 0. TL;DR

| Check | Result |
|---|---|
| Production roots mutated | **No** — 0/0/0 added/removed/changed across all 5 roots (83,024 files) |
| Phase 6I-28 daily `no_members_available` blocker | **Resolved** for SPY (12 of 12 daily cells now prepare) |
| SPY can prepare the full canonical 60-cell grid | **No** — 12 of 60 prepared, 48 still skipped |
| Dominant remaining skipped reason | `target_close_join_incomplete` (48 non-daily cells, unchanged from Phase 6I-28 § 7.1) |
| Adapter top-level issue codes | `["target_close_join_incomplete"]` (the Phase 6I-28 secondary issue `empty_library` is GONE) |
| Phase 6I-22 strict full-member coverage | **Unchanged** (pinned by 12 new and 2 amended tests) |
| Future artifact-write command preparation | **Still BLOCKED** (planner `patch_ready=false`, writer `planner_patch_ready=false`) |
| Gap audit `has_true_multiwindow_k_engine_outputs` | **false** before AND after (no artifact written) |

---

## 1. What changed

### 1.1 Adapter (`multiwindow_k_input_adapter.py`)

**New public surface — `_align_member_signals_to_target_dates`:**

```python
def _align_member_signals_to_target_dates(
    target_dates_seq: list[Any],
    member_dates_seq: Optional[list[Any]],
    member_signals_seq: list[Any],
) -> tuple[Optional[list[str]], Optional[str]]
```

Builds a `date_key -> signal` map from `zip(member_dates_seq, member_signals_seq)` (first observation wins on duplicate keys), then walks `target_dates_seq` in order looking up each target's normalized ISO `YYYY-MM-DD` key. Returns `(aligned, None)` on a complete alignment or `(None, reason)` on failure. The two failure reasons are:

| Reason | Cause |
|---|---|
| `REASON_MEMBER_SIGNAL_DATE_AXIS_MISSING` | Member library has no usable `dates` / `date_index` series, OR the dates / signals lengths disagree, OR no member date normalizes |
| `REASON_MEMBER_DATE_ALIGNMENT_INCOMPLETE` | Member library is well-formed but one or more target dates have no exact-date match on the member axis |

**Member-loading loop change:**

The previous Phase 6I-22 contract enforced `len(member_signals) == len(dates_seq)` as a hard precondition. Phase 6I-29 splits this into:

1. **Fast path** (`len(member_signals) == len(dates_seq)`): preserve the Phase 6I-22 semantics exactly — accept the member's signals positionally without consulting `member_dates`. This keeps backwards-compatibility for callers and fixtures that already rely on positional matching when bar counts agree.
2. **Slow path** (`len(member_signals) != len(dates_seq)`): extract `member_dates`, call `_align_member_signals_to_target_dates`. On success use the aligned signals; on failure mark the member missing with the corresponding new issue code surfaced.

**New stable reason / issue codes:**

| Code | When it fires |
|---|---|
| `REASON_MEMBER_DATE_ALIGNMENT_INCOMPLETE` / `ISSUE_MEMBER_DATE_ALIGNMENT_INCOMPLETE` | Slow-path alignment found one or more target dates missing from member's date axis |
| `REASON_MEMBER_SIGNAL_DATE_AXIS_MISSING` / `ISSUE_MEMBER_SIGNAL_DATE_AXIS_MISSING` | Member library lacks a usable date axis or has mismatched dates/signals lengths |

Both codes are added to `ALL_SKIPPED_REASON_CODES` and `ALL_ISSUE_CODES` for completeness.

**Strict semantics preserved:**

- The alignment helper is **exact-date-only**. AST-scanned by the existing no-projection regression test AND a new focused per-helper AST scan (`test_member_alignment_no_projection_calls_in_helper`).
- No `pickle.load` added. The B12 raw-pickle static regression guard continues to pass without an allowlist entry.
- The Phase 6I-22 Codex strict-coverage gate is unchanged: if ANY member of a K row remains unusable (fast-path or slow-path), the cell still skips with `incomplete_member_coverage` (or `no_members_available` when every member fails). Pinned by amended `test_strict_member_signal_length_mismatch_skips_cell` and new `test_member_alignment_mixed_k_row_strict_coverage_holds`.
- Partial-member mode (`allow_partial_members=True`) **never** flips `can_evaluate_full_60_cell_grid` to True. Pinned by new `test_member_alignment_partial_mode_does_not_unlock_full_grid`.
- The Phase 6I-28 target-close join is unchanged. Phase 6I-29 deliberately does NOT address the non-daily `target_close_join_incomplete` blocker (it likely requires bar-end semantics that belong in the signal-library builder, not the adapter).

### 1.2 Downstream chain — no API changes

The new alignment behaviour lives entirely inside `multiwindow_k_input_adapter.py`. The Phase 6I-23 builder / Phase 6I-24 planner / Phase 6I-25 writer / Phase 6I-27 diagnostic all consume the adapter's existing output surface unchanged. The new issue codes flow through `adapter_summary.adapter_issue_codes` automatically because that field already passes the full tuple of issue codes from the adapter.

The Phase 6I-25 writer-mutation contract is **untouched**: two-key authorization gate (`--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`) plus the four downstream mutation gates (`planner_patch_ready`, `artifact_path` resolution, `_writer_plan_payload_is_consistent(plan)`) all remain in force exactly as before.

### 1.3 Tests added (14 new + 2 amended)

| File | New tests | Total |
|---|---|---|
| `test_scripts/test_multiwindow_k_input_adapter.py` | **+12** | 46 |
| `test_scripts/test_multiwindow_k_input_adapter_diagnostic.py` | **+2** | 22 |

**Amended:** `test_strict_member_signal_length_mismatch_skips_cell` and `test_member_signal_length_mismatch_strict_skip` (in the two test files respectively). Both previously asserted "length mismatch alone skips the cell" — which is now correct only when the dates also don't align. The amended fixtures use member dates that do NOT overlap the target date range, so the alignment helper is exercised AND fails, and the strict-coverage gate still fires. Each amended test now also asserts that the new `ISSUE_MEMBER_DATE_ALIGNMENT_INCOMPLETE` issue code is surfaced.

**Adapter tests pin** (12 new):

1. **Equal-length fast path** still works as before (`test_member_alignment_equal_length_uses_fast_path`).
2. **Extra older dates** (member starts before target) → alignment succeeds.
3. **Extra newer dates** (member extends past target) → alignment succeeds.
4. **Missing one target date** → cell skips, never fabricates.
5. **Member date axis missing/null** → surfaces `member_signal_date_axis_missing`.
6. **Mixed K=2 row** where AAA aligns and BBB fails → strict-coverage gate fires; cell skips with `incomplete_member_coverage`; K=2 does NOT silently become K=1.
7. **Partial-member mode** prepares the surviving member only AND keeps `can_evaluate_full_60_cell_grid=False`.
8. **Direct helper unit tests** (3 — successful alignment with reordered superset; incomplete-alignment failure; axis-missing failure).
9. **Full canonical fixture** with daily-window member superset dates prepares all 60 cells (members align on 1d, fast-path on non-1d).
10. **Helper AST scan** — `_align_member_signals_to_target_dates` contains no `.resample()` / `.ffill()` calls.

**Diagnostic tests pin** (2 new):

11. Diagnostic JSON exposes `ISSUE_MEMBER_DATE_ALIGNMENT_INCOMPLETE` in `adapter_issue_codes` when alignment fails.
12. Diagnostic end-to-end happy path: close-source supplies target close + daily members have superset date axes → diagnostic reports `prepared=60` / `skipped=0` / `can_evaluate_full_60_cell_grid=true` and `no_members_available` is gone from the counts map.

---

## 2. Why exact-date member alignment is NOT projection

The Phase 6I-22 / 6I-25 / 6I-28 spec is unambiguous: the adapter is a strictly read-only input preparation layer that NEVER resamples, ffills, projects, or fabricates. Phase 6I-29 preserves this contract.

| Operation | Resample / ffill / projection? | Used by Phase 6I-29? |
|---|---|---|
| Pick the signal value indexed by the member's bar position, when bar counts match positionally | No — pure positional lookup | Yes — Phase 6I-22 fast path, unchanged |
| Pick the signal value at the same calendar date in the member library, when dates exist on both sides | **No — pure exact-date lookup** | **Yes — Phase 6I-29 alignment helper** |
| Fill missing dates with the nearest prior value | Yes — ffill / projection | **No, forbidden** |
| Interpolate / smooth across missing dates | Yes — interpolation | **No, forbidden** |
| Snap a date to the nearest available bar | Yes — nearest-date projection | **No, forbidden** |
| Resample 1d signals to 1wk / 1mo etc | Yes — resample | **No, forbidden** |

The Phase 6I-29 alignment helper does only the second operation: for each target date, look it up in the member's exact-date map; if absent, the member is unusable. **If a target date does not appear in the member library, the helper returns `None` — it never falls back to a different date and never fabricates a value.** This is the same exact-date discipline the Phase 6I-28 close-source join already applies to the target's `close` column; Phase 6I-29 extends that same discipline to the member signals column.

---

## 3. Repo state

```
Branch: phase-6i-29-spy-daily-member-alignment
Main HEAD (at branch creation): 7407415 (Phase 6I-28, PR #245)
```

---

## 4. Test results

```
Adapter             : 46 passed (34 prior + 12 new)
Diagnostic          : 22 passed (20 prior + 2 new)
Core                : 38 passed
Builder             : 31 passed
Planner             : 32 passed
Writer              : 39 passed
Gap audit           : 23 passed
Static regression   : 9 passed (incl. B12 raw-pickle guard)
                    -----
Focused 8-way       : 240 passed in 4.25 s

Full repo regression: 1,827 passed in 5:44 (0 failures; 60 pre-existing
                      pandas fragmentation warnings unchanged)

py_compile          : clean across all changed Python files
git diff --check    : clean
```

---

## 5. SPY diagnostic evidence run

### 5.1 Temp evidence directory

```
C:\Users\sport\AppData\Local\Temp\phase_6i29_spy_daily_member_alignment\
├── 00_snapshot_before.json
├── 01_diagnostic_spy.json
├── 02_gap_audit_before.json
├── 03_planner_spy.json
├── 04_writer_dry_run.json
├── 04b_writer_execution_log.jsonl
├── 05_gap_audit_after.json
├── 99_snapshot_after.json
├── 99b_snapshot_diff.json
├── diff_helper.py            (copied from Phase 6I-26)
└── snapshot_helper.py        (copied from Phase 6I-26)
```

Pinned interpreter: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

### 5.2 Diagnostic

Command:

```
"<pinned-interp>" multiwindow_k_input_adapter_diagnostic.py \
  --ticker SPY \
  --stackbuilder-root output/stackbuilder \
  --signal-library-dir signal_library/data/stable \
  --cache-dir cache/results \
  > "<TEMP>/01_diagnostic_spy.json"
rc=0
```

Top-level result:

```json
{
  "ticker": "SPY",
  "prepared_cell_count": 12,
  "skipped_cell_count": 48,
  "can_evaluate_full_60_cell_grid": false,
  "adapter_issue_codes": ["target_close_join_incomplete"],
  "counts_by_skipped_reason": {
    "target_close_join_incomplete": 48
  },
  "dominant_skipped_reason": "target_close_join_incomplete",
  "recommended_next_action": "resolve_target_close_join_incomplete"
}
```

### 5.3 Phase 6I-28 → Phase 6I-29 delta

| Field | Phase 6I-28 | Phase 6I-29 |
|---|---|---|
| `prepared_cell_count` | 0 | **12** |
| `skipped_cell_count` | 60 | **48** |
| `can_evaluate_full_60_cell_grid` | false | false |
| `adapter_issue_codes` | `[empty_library, target_close_join_incomplete]` | `[target_close_join_incomplete]` |
| `counts_by_skipped_reason` | `{no_members_available: 12, target_close_join_incomplete: 48}` | `{target_close_join_incomplete: 48}` |
| `dominant_skipped_reason` | `target_close_join_incomplete` | `target_close_join_incomplete` |

**The 12 daily cells that skipped with `no_members_available` at Phase 6I-28 now all prepare.** The `empty_library` issue code (Phase 6I-22 fallout from length-mismatch when the helper rejected the member) is gone because the Phase 6I-29 alignment helper successfully aligns the previously-rejected members onto the target's date axis. The 48 non-daily cells continue to skip with `target_close_join_incomplete` — Phase 6I-29 deliberately does NOT widen the close-source semantics to non-daily windows.

### 5.4 Per-cell samples

**K=1, 1d** (single-member daily — the canonical "this works now" case):

```json
{
  "K": 1, "window": "1d",
  "prepared": true,
  "target_library_present": true,
  "members_attempted": [["PRGO", "D"]],
  "members_prepared": ["PRGO"],
  "members_missing": [],
  "skipped_reason": null
}
```

**K=12, 1d** (full 12-member daily — the StackBuilder seed run):

```
members_prepared = ["AWR", "CP", "EXPO", "LLY", "CLH", "GBCI",
                    "HCSG", "TEF", "JNJ", "MO", "AROW", "PRA"]
members_missing  = []
prepared = True
```

All 12 K-row members for SPY's daily window aligned successfully via the Phase 6I-29 exact-date helper. The strict full-member-coverage gate fired cleanly — every member is in `members_prepared` and `members_missing` is empty.

### 5.5 Per-window count breakdown (after Phase 6I-29)

| Window | Prepared | Skipped | Skipped reason |
|---|---|---|---|
| `1d` | **12** | 0 | — |
| `1wk` | 0 | 12 | `target_close_join_incomplete` |
| `1mo` | 0 | 12 | `target_close_join_incomplete` |
| `3mo` | 0 | 12 | `target_close_join_incomplete` |
| `1y` | 0 | 12 | `target_close_join_incomplete` |
| **TOTAL** | **12** | **48** | — |

### 5.6 Gap audit before / after

| Field | Before | After |
|---|---|---|
| `states[0].has_true_multiwindow_k_engine_outputs` | `false` | `false` |
| `states[0].missing_capabilities` | unchanged | unchanged |

No artifact was written by Phase 6I-29; the audit verdict is unchanged on both probes.

### 5.7 Planner dry-run

| Field | Value |
|---|---|
| `patch_ready` | **false** |
| `payload_summary.payload_ready` | false |
| `issue_codes` | `["payload_not_ready"]` |
| `recommended_next_action` | `build_payload_first` |

Planner remains blocked because `can_evaluate_full_60_cell_grid=false` (12 of 60 cells prepared, not 60). The Phase 6I-22 → 6I-23 → 6I-24 mirror chain is correctly enforced.

### 5.8 Writer dry-run (NO `--write`)

| Field | Value |
|---|---|
| `write_requested` | **false** |
| `write_authorized` | **false** |
| `planner_patch_ready` | **false** |
| `wrote_artifact` | **false** |
| `issue_codes` | `["write_not_requested"]` |
| `recommended_next_action` | `dry_run_review_patch_plan` |
| `pre_write_sha256 == post_write_sha256` | **Yes** (db10e089… unchanged) |

The Phase 6I-25 writer-mutation contract is intact. Mutation requires all of: (gate #1) `--write`, (gate #2) `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`, (gate #3) `planner_patch_ready=true`, (gate #4) artifact path resolves, (gate #5) `_writer_plan_payload_is_consistent(plan)` accepts. Gates #1 / #2 / #3 are all observed-blocked in this run; gates #4 / #5 were not reached because the authorization gate failed first.

---

## 6. Production-root diff (0/0/0)

```json
{
  "cache/results":              {"added": 0, "removed": 0, "changed": 0},
  "cache/status":               {"added": 0, "removed": 0, "changed": 0},
  "output/research_artifacts":  {"added": 0, "removed": 0, "changed": 0},
  "output/stackbuilder":        {"added": 0, "removed": 0, "changed": 0},
  "signal_library/data/stable": {"added": 0, "removed": 0, "changed": 0},
  "TOTAL":                      {"added": 0, "removed": 0, "changed": 0}
}
```

| Root | Files | Added | Removed | Changed |
|---|---|---|---|---|
| `cache/results` | 3,239 | 0 | 0 | 0 |
| `cache/status` | 1,634 | 0 | 0 | 0 |
| `output/research_artifacts` | 35 | 0 | 0 | 0 |
| `output/stackbuilder` | 5,217 | 0 | 0 | 0 |
| `signal_library/data/stable` | 72,899 | 0 | 0 | 0 |
| **TOTAL** | **83,024** | **0** | **0** | **0** |

Zero added / zero removed / zero changed across all 83,024 files in all five production roots.

---

## 7. Remaining blocker

**`target_close_join_incomplete` — 48 non-daily cells.** Unchanged from Phase 6I-28 § 7.1. The Phase 6I-28 close-source join is daily-only; non-daily window libraries carry bar-START dates (Mondays for 1wk, first-of-month for 1mo, first-of-quarter for 3mo, first-of-year for 1y) that fall on non-trading days in the daily Spymaster cache.

Phase 6I-29 intentionally does NOT address this. Two reasons:

1. **Scope discipline:** the spec calls out exact-date member alignment as the daily-window fix and explicitly notes that the non-daily `target_close_join_incomplete` blocker likely remains.
2. **Architectural concern:** "what calendar date does a 1wk bar's `close` officially live on?" is a contract about the signal engine that produced the library, not an inference the adapter should make. Bar-end semantics belong in the signal-library builder, not in the multi-window K adapter. Inventing a bar-end → daily-trading-day map at the adapter layer would be a projection by another name.

### 7.1 Next-phase options

| Option | Scope | Trade-off |
|---|---|---|
| (a) Signal-library builder extension | Persist a `close` series alongside `dates` + `signals` for every interval | Largest scope; forces a signal-library rebuild for affected tickers. Cleanest semantic ownership — the engine that knows the bar-end calendar writes the close column. |
| (b) Adapter-side bar-end close-source contract | Per non-daily window, define an explicit bar-end → daily-cache-date map that the close-source join consults | Smaller scope but introduces calendar logic that lives outside the signal engine. Risk of drift if the bar-end calendar in the library differs from the adapter's assumption. |
| (c) Wait and see | Re-evaluate after the daily multi-window K path is fully wired through to a writer-authorized run | Defers the question and might be the right call if the daily K=12 evaluation produces actionable evidence on its own. |

None of these are executed in Phase 6I-29.

---

## 8. No-production-activity confirmation

| Activity | Performed? |
|---|---|
| Writer `--write` invocation | **No** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` set | **No** (not even via PowerShell-scoped `$env:`) |
| Authorized launcher script created | **No** |
| Source refresh (`signal_engine_cache_refresher`) | **No** |
| `yfinance` fetch | **No** |
| `confluence_pipeline_runner` invocation | **No** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster / Confluence batch execution | **No** |
| Production data write | **No** (0/0/0 across 83,024 files) |
| Subprocess invocations from production modules | **No** |
| Execution-log writes to `output/automation_logs/` | **No** (writer's `--execution-log` argument pointed at the temp evidence dir; `04b_writer_execution_log.jsonl` lives outside the repo) |

The Phase 6H-5 two-key writer gate, Phase 6I-9 supervised gate, Phase 6I-10 production-root snapshot strategy, Phase 6I-12 ProviderFetchTelemetry four-surface contract, Phase 6I-15 source-availability advisory contract, Phase 6I-20 gap audit, Phase 6I-21 engine core, Phase 6I-22 input adapter strict contract, Phase 6I-23 payload builder, Phase 6I-24 patch planner, Phase 6I-25 patch writer (including `_writer_plan_payload_is_consistent`), Phase 6I-27 adapter diagnostic, and Phase 6I-28 close-source join are all unchanged in their runtime contracts.

---

## 9. Operational state carried forward

- Cache state: `cache_date_range_end=2026-05-12`; `current_as_of_date=2026-05-13` (rolled at Phase 6I-26 evidence pass).
- STATE 4 / cache-behind-cutoff (per Phase 6I-17 4-state list).
- Production `has_true_multiwindow_k_engine_outputs` — still `false` for SPY (gap audit, before AND after).
- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still open.
- Writer-surface provider telemetry — still pending.

---

## 10. Validation

- `git diff --check`: clean.
- `git diff --stat`: 4 files touched — 1 production module modified (`multiwindow_k_input_adapter.py`), 2 test files modified (adapter + diagnostic), 1 new Markdown evidence doc added (this file).
- Pinned interpreter on every Python invocation: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.
- Focused 8-way: **240 passed**.
- Full repo regression: **1,827 passed in 5:44**; 0 failures; 60 pre-existing pandas fragmentation warnings unchanged.

---

## 11. Reference paths

- Adapter: `project/multiwindow_k_input_adapter.py` (alignment helper at module level; member-loading loop updated inline).
- Adapter tests: `project/test_scripts/test_multiwindow_k_input_adapter.py` (12 new + 1 amended).
- Diagnostic tests: `project/test_scripts/test_multiwindow_k_input_adapter_diagnostic.py` (2 new + 1 amended).
- Phase 6I-28 evidence (predecessor): `project/md_library/shared/2026-05-13_PHASE_6I28_SPY_CLOSE_JOIN_PATCH_READINESS_DRY_RUN.md` § 7.2 documents the daily `no_members_available` blocker that Phase 6I-29 resolves.
- Phase 6I-22 adapter spec: `project/md_library/shared/2026-05-13_PHASE_6I22_MULTIWINDOW_K_INPUT_ADAPTER.md` (the strict full-member-coverage Codex amendment that Phase 6I-29 preserves).
- Phase 6I-25 writer Codex amendment: `_writer_plan_payload_is_consistent(plan)` in `project/multiwindow_k_confluence_patch_writer.py` (untouched by Phase 6I-29).
- Temp evidence directory: `C:\Users\sport\AppData\Local\Temp\phase_6i29_spy_daily_member_alignment\` (OUTSIDE production roots, OUTSIDE the repo; nothing in it is committed).
- CLAUDE.md § 6 — current sprint state.
