# Phase 6I-28: adapter-side close-source join + SPY patch-readiness dry-run

Sprint date: **2026-05-13** (evidence captured **2026-05-14** UTC).
Branch: `phase-6i-28-spy-close-join-patch-readiness`.
Tickets: adapter (`multiwindow_k_input_adapter.py`), diagnostic (`multiwindow_k_input_adapter_diagnostic.py`), payload builder (`multiwindow_k_engine_payload_builder.py`), planner (`multiwindow_k_confluence_patch_planner.py`), writer (`multiwindow_k_confluence_patch_writer.py`) and their tests.
Doc: this file.

Phase 6I-27 (PR #244) directly proved that all 60 canonical
SPY `(K, window)` adapter cells skipped with
`missing_target_close` because the loaded SPY interval signal
libraries lack a usable `close` / `target_close` / `Close`
series. Phase 6I-28 implements the **smallest production-safe
fix**: an opt-in read-only close-source join driven from the
existing Spymaster cache PKL
(`cache/results/<TICKER>_precomputed_results.pkl`) via the
central provenance loader, plumbs the new option end-to-end
through the diagnostic / builder / planner / writer chain,
and captures a real SPY read-only evidence run to observe
where the chain now stands.

**Reality check first:** the fix is *strict-exact-date* by
design. On real SPY production data the join works for the
**daily** window but the four non-daily windows
(`1wk` / `1mo` / `3mo` / `1y`) do NOT find a matching daily
trading day in the cache for their bar-start dates, and the
daily window itself exposes a **second** blocker — member
signal libraries have different bar counts than the target.
This is exactly the "if the fix does not fully work" branch
of the Phase 6I-28 spec: the close-source join unblocks one
layer of the cascade, surfaces directly-observed evidence of
the remaining blockers, and **does not** force the chain to
report ready.

---

## 0. TL;DR

| Check | Result |
|---|---|
| Production roots mutated | **No** — 0/0/0 added/removed/changed across all 5 roots (83,023 files) |
| Phase 6I-27 `missing_target_close` blocker | **Resolved by this fix for the daily window** (target close successfully joined from `cache/results/SPY_precomputed_results.pkl`) |
| SPY can prepare the full canonical 60-cell grid | **No** (`prepared_cell_count=0`; `can_evaluate_full_60_cell_grid=false`) |
| Dominant skipped reason (Phase 6I-28 SPY diagnostic) | `target_close_join_incomplete` (48 of 60 cells — the four non-daily windows × K=1..12) |
| Second-dominant skipped reason | `no_members_available` (12 of 60 cells — daily × K=1..12) |
| Adapter top-level issue codes | `["empty_library", "target_close_join_incomplete"]` |
| Phase 6I-28 surfaces **directly-observed** secondary blockers | **Yes** — see § 7 |
| Phase 6I-22 strict full-member-coverage contract | **Unchanged** (pinned by tests) |
| Future artifact-write command preparation | **Still BLOCKED** (planner `patch_ready=false`, writer `planner_patch_ready=false`) |
| Gap audit `has_true_multiwindow_k_engine_outputs` | **false** before AND after (no artifact written) |

---

## 1. What changed

### 1.1 Adapter (`multiwindow_k_input_adapter.py`)

The Phase 6I-22 adapter is extended with an **opt-in
read-only** close-source fallback. The legacy path is
preserved 1-for-1 when neither new parameter is supplied —
backwards compatibility is pinned by
`test_close_source_disabled_preserves_legacy_missing_close`.

**New public surface:**

```python
prepare_multiwindow_k_inputs(
    target_ticker, *,
    ...
    close_source_root=None,         # NEW (opt-in)
    close_loader=None,              # NEW (test seam)
)
```

**New stable reason / issue codes:**

| Code | When it fires |
|---|---|
| `target_close_source_missing` | Close-source file not found for any candidate ticker form |
| `target_close_source_unreadable` | Close-source file present but load / verification / shape extraction failed |
| `target_close_join_incomplete` | Close-source loaded but one or more library dates have no exact-date close value |

**New `CloseSourceResolution` dataclass + default loader:**

The default loader resolves
`<close_source_root>/<TICKER>_precomputed_results.pkl`
(falling back through the same `_ticker_form_candidates`
that the library loader already uses), reads via
`provenance_manifest.load_verified_pickle_artifact` (output-
kind artifact contract — the existing central loader used by
Spymaster's `load_precomputed_results_from_file`), extracts
the `preprocessed_data` DataFrame's `Close` column +
`DatetimeIndex`, and returns a date-keyed map. Keys are
normalized to ISO `YYYY-MM-DD` strings via
`_normalize_date_key` so per-window library dates can be
matched without adding a pandas dependency just for date
parsing.

**Exact-date join only.** The new helper
`_resolve_target_close_via_close_source(dates_seq, resolution)`
walks the per-window library's `dates_seq` and for each date
looks up the normalized key in the close-source map. **Any
miss → cell is skipped with `target_close_join_incomplete`.**
No `.resample()`, no `.ffill()`, no interpolation, no
fabrication. The Phase 6I-28 helper module is AST-scanned by
the existing no-projection test PLUS a new dedicated test
`test_close_source_helpers_make_no_projection_calls`.

**B12 raw-pickle ban preserved.** The default loader routes
through `provenance_manifest.load_verified_pickle_artifact`;
no new `pickle.load` is added by Phase 6I-28, and the
repo-wide static regression
`test_b12_no_raw_pickle_load_outside_central_loader` continues
to pass.

**Per-call cache.** The close-source resolution is cached
once per `prepare_multiwindow_k_inputs` invocation, not per
cell. SPY's 60 canonical cells therefore trigger one cache
PKL load, not 60.

**Strict full-member coverage is unchanged.** The fallback
only supplies the *target close column*. Member coverage is
still strictly enforced: if any member of a K row has a
missing / empty / length-mismatched signal library, the cell
is skipped with `incomplete_member_coverage` (or
`no_members_available` when every member fails). The
Phase 6I-22 Codex amendment ("a K=6 build with one missing
member must NOT silently become a K=5 evaluation") is
preserved AND pinned by
`test_close_source_does_not_relax_strict_member_coverage`.

### 1.2 Diagnostic, builder, planner, writer threading

The optional `close_source_root` is forwarded end-to-end:

| Layer | New parameter | CLI flag(s) |
|---|---|---|
| `multiwindow_k_input_adapter_diagnostic.run_adapter_diagnostic` | `cache_dir` + `close_source_root` | `--cache-dir` / `--close-source-root` |
| `multiwindow_k_engine_payload_builder.build_multiwindow_k_engine_payload` | `close_source_root` | `--cache-dir` / `--close-source-root` |
| `multiwindow_k_confluence_patch_planner.plan_multiwindow_k_confluence_patch` | `close_source_root` | `--cache-dir` / `--close-source-root` |
| `multiwindow_k_confluence_patch_writer.apply_multiwindow_k_confluence_patch` | `close_source_root` | `--cache-dir` / `--close-source-root` |

When both `--cache-dir` and `--close-source-root` are supplied, the explicit `--close-source-root` wins. Otherwise `--cache-dir` is used as the close-source root — which matches the multi-window K module-family convention (`multiwindow_k_engine_gap_audit._default_cache_dir` already resolves to `cache/results`).

The writer's **three-key gate is untouched**:

1. `--write` CLI flag, AND
2. `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` env var, AND
3. `planner_patch_ready=true` (which still requires `payload_ready=true` which still requires `adapter.can_evaluate_full_60_cell_grid=true`).

Phase 6I-28 only changes WHEN the third gate can flip; the first two are unchanged.

### 1.3 Tests added (21 new)

| File | New tests | Total |
|---|---|---|
| `test_scripts/test_multiwindow_k_input_adapter.py` | +11 | 34 |
| `test_scripts/test_multiwindow_k_input_adapter_diagnostic.py` | +4 | 20 |
| `test_scripts/test_multiwindow_k_engine_payload_builder.py` | +2 | 31 |
| `test_scripts/test_multiwindow_k_confluence_patch_planner.py` | +2 | 32 |
| `test_scripts/test_multiwindow_k_confluence_patch_writer.py` | +2 | 39 |

**Adapter tests pin:**

1. Target lib with native close still works (preferred over fallback).
2. Target lib without close joins exact-date close values from fallback → cell prepares.
3. Exact-date join across all canonical windows can prepare 60 cells in the canonical fixture (`test_close_source_full_canonical_fixture_prepares_60_cells`).
4. Missing close source surfaces `target_close_source_missing`.
5. Unreadable close source surfaces `target_close_source_unreadable`.
6. Partial close coverage surfaces `target_close_join_incomplete` and **does not** prepare/fabricate the cell.
7. Strict full-member coverage is unchanged (`test_close_source_does_not_relax_strict_member_coverage`).
8. Legacy disabled-fallback behaviour preserved (`test_close_source_disabled_preserves_legacy_missing_close`).
9. `close_source_root` is threaded to the loader (`test_close_source_root_path_threaded_to_loader`).
10. Date-key normalization handles `datetime` / `date` / `Timestamp` / ISO-string shapes (`test_normalize_date_key_handles_common_shapes`).
11. Adapter has no projection calls — Phase 6I-28 helpers AST-scanned (`test_close_source_helpers_make_no_projection_calls`).

**Diagnostic tests pin:**

12-14. CLI threading: `close_source_root` / `cache_dir` are forwarded to the adapter, with the documented precedence.
15. `test_close_source_join_makes_diagnostic_report_60_prepared`: an end-to-end happy path where the diagnostic now reports `prepared=60` / `skipped=0` / `can_evaluate_full_60_cell_grid=true` and **no `missing_target_close` in `adapter_issue_codes`**.

**Builder / planner / writer tests pin:** the new kwarg is threaded through the seam at each layer (six tests, two per file: explicit value + None-default).

The repo-wide B12 static regression guard
(`test_b12_no_raw_pickle_load_outside_central_loader`) continues
to pass — no allowlist entry was added.

---

## 2. Repo state

```
Branch: phase-6i-28-spy-close-join-patch-readiness
Main HEAD (at branch creation): 1cee319 (Phase 6I-27, PR #244)
```

---

## 3. Test results

```
Adapter             : 34 passed
Diagnostic          : 20 passed
Core                : 38 passed
Builder             : 31 passed
Planner             : 32 passed
Writer              : 39 passed
Gap audit           : 23 passed
Static regression   : 9 passed (incl. B12 raw-pickle guard)
                    -----
Focused 8-way       : 226 passed in 4.05 s

Full repo regression: 1,813 passed in 5:52 (0 failures; 60 pre-existing
                      pandas fragmentation warnings unchanged)

py_compile           : clean across all 5 changed modules
git diff --check     : clean
```

---

## 4. SPY diagnostic evidence run

### 4.1 Temp evidence directory

All outputs are written **outside** every production root AND
**outside** the repo:

```
C:\Users\sport\AppData\Local\Temp\phase_6i28_spy_close_join_patch_readiness\
├── 00_snapshot_before.json
├── 01_diagnostic_spy_with_close_source.json
├── 02_gap_audit_before.json
├── 03_planner_spy.json
├── 04_writer_dry_run_spy.json
├── 04b_writer_execution_log.jsonl
├── 05_gap_audit_after.json
├── 99_snapshot_after.json
├── 99b_snapshot_diff.json
├── diff_helper.py             (copied from Phase 6I-26)
└── snapshot_helper.py         (copied from Phase 6I-26)
```

Pinned interpreter on every Python invocation:
`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

### 4.2 Diagnostic with close-source enabled

Command:

```
"<pinned-interp>" multiwindow_k_input_adapter_diagnostic.py \
  --ticker SPY \
  --stackbuilder-root output/stackbuilder \
  --signal-library-dir signal_library/data/stable \
  --cache-dir cache/results \
  > "<TEMP>/01_diagnostic_spy_with_close_source.json"
rc=0
```

Top-level result:

```json
{
  "ticker": "SPY",
  "prepared_cell_count": 0,
  "skipped_cell_count": 60,
  "can_evaluate_full_60_cell_grid": false,
  "adapter_issue_codes": [
    "empty_library",
    "target_close_join_incomplete"
  ],
  "counts_by_skipped_reason": {
    "no_members_available": 12,
    "target_close_join_incomplete": 48
  },
  "dominant_skipped_reason": "target_close_join_incomplete",
  "recommended_next_action": "resolve_target_close_join_incomplete",
  "selected_run_id": "seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D"
}
```

Direct observations vs Phase 6I-27 (PR #244):

| Field | Phase 6I-27 | Phase 6I-28 |
|---|---|---|
| `prepared_cell_count` | 0 | 0 |
| `skipped_cell_count` | 60 | 60 |
| `can_evaluate_full_60_cell_grid` | false | false |
| `adapter_issue_codes` | `["missing_target_close"]` | `["empty_library", "target_close_join_incomplete"]` |
| `counts_by_skipped_reason` | `{missing_target_close: 60}` | `{no_members_available: 12, target_close_join_incomplete: 48}` |
| `dominant_skipped_reason` | `missing_target_close` | `target_close_join_incomplete` |
| `recommended_next_action` | `resolve_missing_target_close` | `resolve_target_close_join_incomplete` |

**The Phase 6I-27 dominant blocker `missing_target_close` is gone.** It has been split into two directly-observed secondary blockers (see § 7) by the Phase 6I-28 fix.

### 4.3 Per-cell sample

**K=1, 1d** — daily close source resolved the target close,
but the daily member library failed:

```json
{
  "K": 1, "window": "1d", "prepared": false,
  "target_library_present": true,
  "members_attempted": [["PRGO", "D"]],
  "members_prepared": [],
  "members_missing": ["PRGO"],
  "skipped_reason": "no_members_available"
}
```

**K=1, 1wk** — weekly target dates did not exact-date-match
the daily close source:

```json
{
  "K": 1, "window": "1wk", "prepared": false,
  "target_library_present": true,
  "members_attempted": [["PRGO", "D"]],
  "members_prepared": [],
  "members_missing": [],
  "skipped_reason": "target_close_join_incomplete"
}
```

### 4.4 Gap audit before / after

`multiwindow_k_engine_gap_audit.py --ticker SPY` ran once
before and once after the full read-only chain:

| Field | Before | After |
|---|---|---|
| `states[0].has_true_multiwindow_k_engine_outputs` | `false` | `false` |
| `states[0].missing_capabilities` | `["missing_per_window_k_metrics", "missing_build_wide_window_alignment_fields", "missing_true_multiwindow_k_engine"]` | identical |

No artifact was written by Phase 6I-28; the audit verdict is
unchanged on both probes.

### 4.5 Planner dry-run

`multiwindow_k_confluence_patch_planner.py --ticker SPY ... --cache-dir cache/results`:

| Field | Value |
|---|---|
| `patch_ready` | **false** |
| `payload_summary.payload_ready` | false |
| `issue_codes` | `["payload_not_ready"]` |
| `recommended_next_action` | `build_payload_first` |

Planner remains blocked because the builder still reports
`payload_ready=false` because the adapter still reports
`can_evaluate_full_60_cell_grid=false`. The Phase 6I-22 →
6I-23 → 6I-24 mirror chain is intact.

### 4.6 Writer dry-run (NO `--write`)

`multiwindow_k_confluence_patch_writer.py --ticker SPY ... --cache-dir cache/results --execution-log <TEMP>/04b_writer_execution_log.jsonl`:

| Field | Value |
|---|---|
| `write_requested` | **false** |
| `write_authorized` | **false** |
| `planner_patch_ready` | **false** |
| `wrote_artifact` | **false** |
| `issue_codes` | `["write_not_requested"]` |
| `recommended_next_action` | `dry_run_review_patch_plan` |
| `pre_write_sha256` | `db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f` |
| `post_write_sha256` | `db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f` |
| Pre/post SHA equal? | **Yes** (same hash as Phase 6I-26) |

The on-disk Confluence artifact at
`output/research_artifacts/confluence/SPY/SPY__MTF_CONSENSUS.research_day.json`
is byte-for-byte unchanged. The writer correctly refuses to
mutate because (a) `--write` is absent, (b)
`PRJCT9_AUTOMATION_WRITE_AUTH` is unset, and (c)
`planner_patch_ready=false`. Any one of the three would block
on its own; all three were observed to block in this run.

---

## 5. Production-root diff (0/0/0)

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
| `output/stackbuilder` | 5,216 | 0 | 0 | 0 |
| `signal_library/data/stable` | 72,899 | 0 | 0 | 0 |
| **TOTAL** | **83,023** | **0** | **0** | **0** |

Zero added / zero removed / zero changed across all 83,023
files in all five production roots. **No production root was
touched by this evidence pass.**

---

## 6. Why this does NOT weaken strict multi-window K coverage

The product invariant is unchanged: "every canonical
`(K, window)` cell must evaluate every member of that K build
across every canonical window."

The Phase 6I-28 fix only changes the source of the **target's
close-price column**, not the member-coverage gate:

1. The adapter prefers a native `close` series inside the
   per-window signal library when one is present (pinned by
   `test_native_close_in_library_still_preferred_over_close_source`).
2. When the per-window library lacks `close`, the adapter
   attempts an **exact-date** join against the read-only
   cache PKL. Any library date that does not appear in the
   cache's date-keyed close map causes the cell to skip with
   `target_close_join_incomplete`. There is no resample, no
   ffill, no projection, no fabrication.
3. The strict full-member coverage gate is unchanged. If any
   member of a K row is missing / empty / length-mismatched
   the cell still skips with `incomplete_member_coverage` (or
   `no_members_available` when every member fails). Pinned
   by `test_close_source_does_not_relax_strict_member_coverage`.
4. `can_evaluate_full_60_cell_grid=True` still requires every
   canonical `(K, window)` cell to prepare with the FULL
   K-row member set. Partial-member cells continue to be
   counted out of that verdict (the Phase 6I-22 Codex
   amendment is preserved verbatim).
5. The writer's three-key gate is unchanged. `--write` +
   `PRJCT9_AUTOMATION_WRITE_AUTH` are still required, and
   `planner_patch_ready=true` is still required, which still
   requires `payload_ready=true` which still requires
   `can_evaluate_full_60_cell_grid=true`.

---

## 7. Directly-observed remaining blockers (Phase 6I-28)

The Phase 6I-28 fix unblocks one layer of the cascade and
exposes two distinct directly-observed blockers below it.

### 7.1 Non-daily windows: `target_close_join_incomplete` (48 of 60 cells)

For SPY's `1wk` / `1mo` / `3mo` / `1y` signal libraries, the
`dates` array carries **bar-start dates** (Mondays for
`1wk`, first-of-month for `1mo`, first-of-quarter for
`3mo`, first-of-year for `1y`). Those dates typically are
NOT trading days in the daily Spymaster cache, so the
strict-exact-date join finds no matching daily close for
the library bar.

Example (directly observed):

| Window | First library date | Falls on |
|---|---|---|
| `1wk` | `1993-01-25` | Monday (not a trading day in the daily index whose first observation is Friday `1993-01-29`) |
| `1mo` | `1993-01-01` | New Year's Day (market holiday) |

The strict-exact-date contract is correct — it refuses to
fabricate a weekly close by ffilling Friday's daily close.
Closing this blocker requires either an interval-aware close
source (e.g. extending the signal-library builder to persist
a `close` per interval) or a structurally sound bar-end
→ daily-close map (which is **not** a resample — it is a
contract about which calendar date the bar's close
*officially* lives on; that decision belongs to the signal
engine that produced the library, not the multi-window K
adapter).

### 7.2 Daily window: `no_members_available` (12 of 60 cells)

For SPY's `1d` library the close-source join SUCCEEDS — all
8,302 SPY daily library dates are present in the daily cache
PKL with a matching `Close`. The 12 daily cells get past the
target-close gate and into the member-loading loop, where a
**different** strict-alignment gate fires:

The Phase 6I-22 adapter enforces
`len(member_signals) == len(target_dates_seq)` for every
member library. Daily libraries for SPY's K-row members
(e.g. `PRGO`, `AWR`) have different bar counts than SPY's
daily library because they start at different first
trading days:

| Ticker | Daily bar count | First date | Last date |
|---|---|---|---|
| SPY | 8,302 | `1993-01-29` | `2026-01-22` |
| PRGO | 8,585 | `1991-12-17` | `2026-01-22` |
| AWR | 13,343 | (older) | `2026-01-22` |

Member signal sequences are therefore length-mismatched
against SPY's date sequence, the adapter treats them as
missing, and the cell skips with `no_members_available`.

This is the **second** real production blocker — it was
hidden behind `missing_target_close` until Phase 6I-28
unblocked the target-close gate.

Closing this blocker requires either (a) a
date-aligned-slicing layer in the adapter (slice each
member's daily signal sequence to the target's date range
before length-checking) or (b) a normalization pass in the
signal-library builder so every per-interval library carries
the same date axis.

### 7.3 Phase 6I-27 inferred blocker → directly observed cascade

Phase 6I-27 (the predecessor evidence pass) directly proved
that `missing_target_close` was the dominant skip reason for
all 60 SPY cells and inferred (from Phase 6I-22 docs) that
this was the root cause of the entire upstream
`adapter_not_ready` chain. Phase 6I-28 has now **directly
observed** that the cascade is:

```
target_close_join_incomplete   (48 cells, non-daily windows: bar-start dates do not exact-date-match the daily cache)
no_members_available           (12 cells, daily window: member signal libraries have different bar counts than the target)
```

The Phase 6I-27 fix at the close-source layer was real and
worked; the cascade simply has more layers than the prior
phase could see.

---

## 8. Future artifact-write command preparation

**Still BLOCKED.** Phase 6I-28 does not prepare any future
write command. `recommended_next_action` along the chain:

- Diagnostic: `resolve_target_close_join_incomplete`
- Planner: `build_payload_first`
- Writer: `dry_run_review_patch_plan`

The next implementation phase (Phase 6I-29 or later) must
close at least the daily-window blocker (`no_members_available`
from § 7.2) before any artifact-write command preparation
can begin. Closing the non-daily-window blocker (§ 7.1) is
deeper-scope work and may belong to the signal-library
builder rather than this adapter.

Concrete next-phase options (NOT executed by Phase 6I-28):

| Option | Scope | Trade-off |
|---|---|---|
| (a) Adapter-side date-aligned member slicing | Slice each member's daily signal sequence to the intersection of the target's date range BEFORE the length-check | Localized; preserves the adapter's "no projection" rule because slicing to an exact-date intersection is not a projection. Closes daily blocker. Does not help non-daily windows. |
| (b) Adapter-side close-source for non-daily windows | Resolve the cache PKL once per ticker and intersect with each non-daily window's bar-end calendar | Needs an explicit "bar-end date for window W" contract — that contract is structural, not a projection, but it belongs in a signal-library-aware module, not the adapter. |
| (c) Signal-library builder extension | Persist a `close` series alongside `dates` + `signals` for every interval | Forces a signal-library rebuild for affected tickers. Closes both blockers cleanly but is the largest-scope option. |

Each option still needs tests proving the adapter prepares
the full canonical 60-cell input set for SPY end-to-end
before any artifact-write phase proceeds.

---

## 9. No-production-activity confirmation

| Activity | Performed? |
|---|---|
| Writer `--write` invocation | **No** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` set | **No** (not even via PowerShell-scoped `$env:`) |
| Authorized launcher script created | **No** |
| Source refresh (`signal_engine_cache_refresher`) | **No** |
| `yfinance` fetch | **No** |
| `confluence_pipeline_runner` invocation | **No** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster / Confluence batch execution | **No** |
| Production data write | **No** (0/0/0 across 83,023 files) |
| Subprocess invocations from production modules | **No** |
| Execution-log writes to `output/automation_logs/` | **No** (writer's `--execution-log` argument pointed at the temp evidence dir; `04b_writer_execution_log.jsonl` lives outside the repo) |

The Phase 6H-5 two-key writer gate, Phase 6I-9 supervised
gate, Phase 6I-10 production-root snapshot strategy,
Phase 6I-12 ProviderFetchTelemetry four-surface contract,
Phase 6I-15 source-availability advisory contract,
Phase 6I-20 gap audit, Phase 6I-21 engine core,
Phase 6I-22 input adapter, Phase 6I-23 payload builder,
Phase 6I-24 patch planner, Phase 6I-25 patch writer, and
Phase 6I-27 adapter diagnostic are all unchanged in their
runtime contracts.

---

## 10. Operational state carried forward

- Cache state: `cache_date_range_end=2026-05-12`;
  `current_as_of_date=2026-05-13` (rolled at Phase 6I-26
  evidence pass).
- STATE 4 / cache-behind-cutoff (per Phase 6I-17 4-state
  list).
- Production `has_true_multiwindow_k_engine_outputs` — still
  `false` for SPY (gap audit, before AND after).
- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still open.
- Writer-surface provider telemetry — still pending.

---

## 11. Validation

- `git diff --check`: clean.
- `git diff --stat`: 10 files modified (5 production + 5
  test).
- Pinned interpreter on every Python invocation:
  `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.
- Focused 8-way: **226 passed**.
- Full repo regression: **1,813 passed in 5:52**; 0
  failures; 60 pre-existing pandas fragmentation warnings
  unchanged.

---

## 12. Reference paths

- Adapter: `project/multiwindow_k_input_adapter.py`.
- Adapter tests:
  `project/test_scripts/test_multiwindow_k_input_adapter.py`.
- Diagnostic: `project/multiwindow_k_input_adapter_diagnostic.py`.
- Diagnostic tests:
  `project/test_scripts/test_multiwindow_k_input_adapter_diagnostic.py`.
- Payload builder: `project/multiwindow_k_engine_payload_builder.py`.
- Patch planner: `project/multiwindow_k_confluence_patch_planner.py`.
- Patch writer: `project/multiwindow_k_confluence_patch_writer.py`.
- Phase 6I-27 evidence (predecessor):
  `project/md_library/shared/2026-05-13_PHASE_6I27_SPY_MULTIWINDOW_ADAPTER_DIAGNOSTIC_EVIDENCE.md`.
- Phase 6I-26 evidence (predecessor): `project/md_library/shared/2026-05-13_PHASE_6I26_SPY_CONFLUENCE_PATCH_WRITER_DRY_RUN_EVIDENCE.md`.
- Phase 6I-22 adapter spec: `project/md_library/shared/2026-05-13_PHASE_6I22_MULTIWINDOW_K_INPUT_ADAPTER.md` (§ 6 documents the `missing_target_close` limitation that Phase 6I-28 partially resolves).
- Temp evidence directory:
  `C:\Users\sport\AppData\Local\Temp\phase_6i28_spy_close_join_patch_readiness\`
  (OUTSIDE production roots, OUTSIDE the repo; nothing in it
  is committed).
- CLAUDE.md § 6 — current sprint state.
