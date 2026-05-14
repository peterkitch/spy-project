# Phase 6I-27: SPY multi-window K adapter diagnostic evidence

Sprint date: **2026-05-13** (evidence captured **2026-05-14** UTC).
Branch: `phase-6i-27-spy-multiwindow-adapter-diagnostic`.
Module: `project/multiwindow_k_input_adapter_diagnostic.py` (new).
Tests: `project/test_scripts/test_multiwindow_k_input_adapter_diagnostic.py` (new).
Doc: this file.

Phase 6I-26 (PR #243) directly observed `adapter_not_ready`
and `payload_not_ready` for SPY, but **inferred** the
likely root cause (`missing_target_close`) from Phase 6I-22
documentation rather than capturing it as direct evidence.
This phase implements a read-only Phase 6I-22 adapter
diagnostic and runs it against SPY to convert that
inference into a directly-observed fact.

**TL;DR: the Phase 6I-22 inference is now DIRECTLY PROVEN.** All 60 canonical `(K, window)` SPY cells skipped with `missing_target_close` (the adapter's own per-cell reason code). No production root was mutated by this evidence pass (0/0/0 diff across 83,021 files).

---

## 0. Verdict (TL;DR)

| Check | Result |
|---|---|
| Production roots mutated | **No** — 0/0/0 added/removed/changed across all 5 roots (83,021 files) |
| SPY can prepare full canonical 60-cell grid | **No** (`prepared_cell_count=0`; `can_evaluate_full_60_cell_grid=false`) |
| Dominant skipped reason | **`missing_target_close`** (60 of 60 canonical cells) |
| Adapter top-level issue codes | `["missing_target_close"]` |
| Phase 6I-22 inference status (vs Phase 6I-26) | **DIRECTLY PROVEN** by Phase 6I-27 — was inferred only at Phase 6I-26 |
| Future artifact-write command preparation | **Still BLOCKED** until the upstream `missing_target_close` gap is fixed |
| StackBuilder run + signal libraries findable | **Yes** (per-cell `target_library_present=true`; `selected_run_id=seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D`) |
| Operational state | STATE 4 / cache-behind-cutoff (carried forward from Phase 6I-26) |

---

## 1. Module implementation

### 1.1 `project/multiwindow_k_input_adapter_diagnostic.py` (new; +400 lines)

Read-only CLI that wraps the existing Phase 6I-22 adapter:

```python
def run_adapter_diagnostic(
    target_ticker, *,
    stackbuilder_root=None,
    signal_library_dir=None,
    K_values=CANONICAL_K_VALUES,
    windows=CANONICAL_WINDOWS,
    run_dir=None,
    current_as_of_date=None,         # accepted for CLI parity;
                                     #   NOT forwarded to the
                                     #   Phase 6I-22 adapter
                                     #   (which does not accept
                                     #   that argument)
    adapter_callable=None,           # injection seam (tests)
) -> dict[str, Any]
```

The diagnostic:

1. Calls Phase 6I-22 `prepare_multiwindow_k_inputs(...)` in its default strict full-member coverage mode (`allow_partial_members` is **never** forwarded; pinned by a dedicated test).
2. Walks the canonical 60 `(K, window)` pairs and looks up each cell's `PerCellAdapterState` from the adapter's report.
3. Serializes per-cell fields: `K` / `window` / `prepared` / `target_library_present` / `members_attempted` (as `[ticker, protocol]` pairs) / `members_prepared` / `members_missing` / `skipped_reason`.
4. Aggregates `counts_by_skipped_reason` + `dominant_skipped_reason` (most frequent non-prepared reason).
5. Derives `recommended_next_action` (`adapter_ready_for_writer_evidence_run` when full grid is preparable; `resolve_<dominant_reason>` otherwise; `manual_review_required` defensive fallback).

CLI: `--ticker SPY` (required) + `--stackbuilder-root` / `--signal-library-dir` / `--run-dir` / `--current-as-of-date`. JSON to stdout. `rc=0/2/3`. No `SystemExit` leak.

**Strictly read-only.** Forbidden-imports static guard blocks writer / refresher / pipeline / live engines / yfinance / dash / subprocess + explicit no-`trafficflow_multitimeframe_bridge` / no-`multiwindow_k_engine_gap_audit` / no-`multiwindow_k_engine_payload_builder` / no-`multiwindow_k_confluence_patch_planner` / no-`multiwindow_k_confluence_patch_writer`. AST-level no-projection (`.resample()` / `.ffill()`) + no raw `pickle.load` + no `Path.write_text` / `write_bytes` / `json.dump` call sites.

### 1.2 `project/test_scripts/test_multiwindow_k_input_adapter_diagnostic.py` (new; +560 lines, 16 tests)

Pinned contracts:

1. Forbidden-imports static guard.
2. No `.resample()` / `.ffill()` call.
3. No raw `pickle.load`.
4. No artifact writes anywhere (AST guard).
5. **Full canonical fixture → 60 prepared / 0 skipped**, `can_evaluate_full_60_cell_grid=true`, `recommended_next_action=adapter_ready_for_writer_evidence_run`, every per-cell row has `K` members in `members_prepared`.
6. **Missing target close → 60 of 60 cells skipped with `missing_target_close`** (strict every-cell coverage).
7. **Missing member library → strict per-cell skip** with `incomplete_member_coverage` (one missing window for one member skips only the affected cells; other windows still prepare with the FULL member set).
8. **Length mismatch → strict per-cell skip** with `incomplete_member_coverage`.
9. **`allow_partial_members` never forwarded** to the adapter (assertion-pinned inside spy adapter callable).
10. **Diagnostic JSON includes all 60 canonical cells even on failure paths** (`no_stackbuilder_run` short-circuit yields 60 per-cell entries).
11. **`counts_by_skipped_reason` matches** the per-cell diagnostics counts byte-for-byte.
12-16. CLI: `rc=0` (happy) / `rc=2` (missing ticker / unknown flag / no SystemExit leak) / `rc=3` (unhandled exception).

All tests use `tmp_path` fixtures + injected fakes only. No production roots touched.

---

## 2. Repo state

```
Branch: phase-6i-27-spy-multiwindow-adapter-diagnostic
Main HEAD (at branch creation): e0c42e9
$ git log --oneline -5
e0c42e9 Phase 6I-26: supervised SPY Confluence patch writer DRY-RUN evidence (#243)
10b535b Phase 6I-25: guarded Confluence artifact patch writer for the multi-window K engine payload (#242)
e62cb5a Phase 6I-24: read-only Confluence artifact patch planner for the multi-window K engine payload (#241)
948c961 Phase 6I-23: in-memory multi-window K engine payload builder (#240)
66599c7 Phase 6I-22: read-only adapter from StackBuilder rows + OnePass interval libraries into multi-window K engine core inputs (#239)
```

---

## 3. Test results

```
$ pytest test_scripts/test_multiwindow_k_input_adapter_diagnostic.py -q
  16 passed in 0.54 s

Focused 7-way (diagnostic + adapter + core + builder + planner + writer + gap audit):
  196 passed in 1.71 s
  ├── multiwindow_k_input_adapter_diagnostic    16 passed
  ├── multiwindow_k_input_adapter               23 passed
  ├── multiwindow_k_engine_core                 38 passed
  ├── multiwindow_k_engine_payload_builder      29 passed
  ├── multiwindow_k_confluence_patch_planner    30 passed
  ├── multiwindow_k_confluence_patch_writer     37 passed
  └── multiwindow_k_engine_gap_audit            23 passed

py_compile: clean.
git diff --check: clean (LF→CRLF normalization warning only on the markdown doc).
```

---

## 4. SPY diagnostic evidence run

### 4.1 Temp evidence directory

All evidence outputs were written to a temp directory **outside** every production root AND **outside** the repo:

```
C:\Users\sport\AppData\Local\Temp\phase_6i27_spy_adapter_diagnostic\
├── 00_snapshot_before.json     (production-root snapshot before; 83,021 files)
├── 01_diagnostic_spy.json      (full diagnostic JSON output)
├── 99_snapshot_after.json      (production-root snapshot after)
```

The snapshot helper + diff helper from Phase 6I-26 (`C:\Users\sport\AppData\Local\Temp\phase_6i26_spy_confluence_patch_writer_dry_run\snapshot_helper.py` and `diff_helper.py`) were reused as-is.

Pinned interpreter on every Python invocation: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

### 4.2 Exact command run

```
$ "<pinned-interp>" multiwindow_k_input_adapter_diagnostic.py \
    --ticker SPY \
    --stackbuilder-root output/stackbuilder \
    --signal-library-dir signal_library/data/stable \
    > "<TEMP>/01_diagnostic_spy.json" 2>&1
$ echo "rc=$?"
rc=0
```

### 4.3 Top-level diagnostic summary

```json
{
  "ticker": "SPY",
  "expected_canonical_cell_count": 60,
  "prepared_cell_count": 0,
  "skipped_cell_count": 60,
  "can_evaluate_full_60_cell_grid": false,
  "adapter_issue_codes": ["missing_target_close"],
  "selected_run_id": "seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D",
  "counts_by_skipped_reason": {
    "missing_target_close": 60
  },
  "dominant_skipped_reason": "missing_target_close",
  "recommended_next_action": "resolve_missing_target_close",
  "per_cell_diagnostics": "<60 entries, see § 4.5>"
}
```

### 4.4 Adapter aggregate state

| Field | Value |
|---|---|
| `prepared_cell_count` | **0** |
| `skipped_cell_count` | **60** |
| `can_evaluate_full_60_cell_grid` | **false** |
| `adapter_issue_codes` | `["missing_target_close"]` |
| `selected_run_id` | `seedTC__AWR-D_CP-I_EXPO-D_LLY-I_CLH-D_GBCI-D_HCSG-D_TEF-I_JNJ-I_MO-I_AROW-D_PRA-D` |
| `counts_by_skipped_reason` | `{"missing_target_close": 60}` |
| `dominant_skipped_reason` | `missing_target_close` |
| `recommended_next_action` | `resolve_missing_target_close` |

The fact that `selected_run_id` is non-empty proves the StackBuilder run discovery succeeded; the fact that `target_library_present=true` on every per-cell row (see § 4.5) proves the SPY signal libraries are present and loadable for every canonical window. The single dominant reason `missing_target_close` is **directly the adapter's own per-cell skip code** for "library loaded successfully but does not carry a `close` / `target_close` / `Close` series".

### 4.5 Per-cell sample (representative cells)

**K=1, window=1d:**
```json
{
  "K": 1,
  "window": "1d",
  "prepared": false,
  "target_library_present": true,
  "members_attempted": [["PRGO", "D"]],
  "members_prepared": [],
  "members_missing": [],
  "skipped_reason": "missing_target_close"
}
```

**K=12, window=1y:**
```json
{
  "K": 12,
  "window": "1y",
  "prepared": false,
  "target_library_present": true,
  "members_attempted": [
    ["AWR", "D"], ["CP", "I"], ["EXPO", "D"], ["LLY", "I"],
    ["CLH", "D"], ["GBCI", "D"], ["HCSG", "D"], ["TEF", "I"],
    ["JNJ", "I"], ["MO", "I"], ["AROW", "D"], ["PRA", "D"]
  ],
  "members_prepared": [],
  "members_missing": [],
  "skipped_reason": "missing_target_close"
}
```

Key observations from the per-cell rows:

- **`target_library_present=true` on every cell.** The SPY signal libraries (`SPY_stable_v1_0_0.pkl` + `SPY_stable_v1_0_0_1wk.pkl` + `..._1mo.pkl` + `..._3mo.pkl` + `..._1y.pkl`) all load successfully via the central provenance loader.
- **`members_prepared` / `members_missing` are both empty on every cell.** This is the adapter's intentional behaviour: when the target's `close` / `target_close` / `Close` field is absent, the adapter short-circuits before iterating member libraries (since there's no point preparing members for a target with no price series). The empty `members_*` tuples here are a property of the skip, NOT a separate adapter failure.
- **Member rosters per K row are correctly parsed.** K=1 has PRGO[D] (one Direct member); K=12 has the full 12-member StackBuilder seed run with correctly-parsed Direct/Inverse protocols matching the `selected_run_id` string. This proves the Phase 6I-22 `parse_stack_members_with_protocol` chain is healthy for SPY.

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

| Production root | Files | Added | Removed | Changed |
|---|---|---|---|---|
| `cache/results` | 3,239 | 0 | 0 | 0 |
| `cache/status` | 1,634 | 0 | 0 | 0 |
| `output/research_artifacts` | 35 | 0 | 0 | 0 |
| `output/stackbuilder` | 5,214 | 0 | 0 | 0 |
| `signal_library/data/stable` | 72,899 | 0 | 0 | 0 |
| **TOTAL** | **83,021** | **0** | **0** | **0** |

Zero added / zero removed / zero changed across all 83,021 files in all five production roots. **No production root was touched by this evidence pass.**

---

## 6. Inference vs direct observation

### 6.1 Directly observed in this Phase 6I-27 pass

- Adapter `issue_codes` includes **`missing_target_close`** (top-level aggregate).
- All 60 canonical `(K, window)` cells skipped with `skipped_reason="missing_target_close"` (per-cell direct evidence).
- `target_library_present=true` on every cell — the SPY signal-library `.pkl` files load successfully via the central provenance loader; the gap is specifically the absent `close` series inside the loaded payload, NOT a missing or unreadable file.
- StackBuilder run discovered successfully (`selected_run_id` is a real seed-run); leaderboard read successfully; all 12 K rows parsed; member rosters correctly parsed for every K row with Direct/Inverse protocol annotations intact.
- `can_evaluate_full_60_cell_grid=false` because the adapter cannot prepare any cell.

### 6.2 Prior inferred likely cause (Phase 6I-22 / Phase 6I-26)

- Phase 6I-22 doc § 6 documented a `missing_target_close` limitation as a known risk: production signal-library `.pkl` files carry `dates` + `signals` reliably but do not always carry a `close` series.
- Phase 6I-26 directly observed `adapter_not_ready` / `payload_not_ready` for SPY but did **not** run the per-cell adapter diagnostic; the `missing_target_close` explanation was **inferred** from prior Phase 6I-22 documentation, not directly captured.

### 6.3 Convergence

Phase 6I-27 has **converted the Phase 6I-26 inference into a directly-observed fact**. The dominant skipped reason for all 60 SPY canonical cells is exactly the `missing_target_close` code that Phase 6I-22 documented as a known limitation. The two observations agree byte-for-byte.

---

## 7. Future artifact-write command preparation

**Still BLOCKED.** The Phase 6I-26 conclusion stands: a future supervised authorized-write phase requires the upstream `missing_target_close` adapter gap to be resolved before a future write command can be prepared. Phase 6I-27 does NOT prepare such a command (the spec explicitly requires this, and the diagnostic verdict is `recommended_next_action=resolve_missing_target_close`).

### 7.1 Concrete next-phase options

| Option | Description |
|---|---|
| (a) **Signal-library builder extension** | Extend `signal_library` builders to persist a `close` series alongside `dates` and `signals` for every interval (`1d` / `1wk` / `1mo` / `3mo` / `1y`). Forces a full signal-library rebuild for affected tickers. |
| (b) **Adapter-side close-source join** | Modify `multiwindow_k_input_adapter._default_library_loader` (or its callers) to fall back to a separate cache source (e.g. `cache/results/<TICKER>_precomputed_results.pkl`) for the target's `close` series when the signal library does not carry one. No signal-library rebuild required. |
| (c) **Adapter relaxation** | NOT a recommended option — relaxing the adapter's strict-coverage requirement would weaken the Phase 6I-22 contract that Phase 6I-24 / 6I-25 depend on. |

Either (a) or (b) is acceptable per the Phase 6I-26 doc § 12.2. Each needs new tests proving the adapter prepares the full canonical 60-cell input set for SPY end-to-end before any artifact-write phase proceeds.

### 7.2 Source-refresh gap (separate)

Independently, Phase 6I-26 documented that `cache_date_range_end=2026-05-12` is now **behind** `current_as_of_date=2026-05-13`. This is a **separate** gap from `missing_target_close` — even after the close-source join is in place, a supervised refresh would still be required before any production-write run, per the Phase 6I-15 / 6I-17 / 6I-18 source-availability discipline.

---

## 8. No-production-activity confirmation

| Activity | Performed? |
|---|---|
| Writer `--write` invocation against any production ticker | **No** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` env var set | **No** (not even via PowerShell-scoped `$env:`) |
| Authorized launcher script created | **No** |
| Source refresh (`signal_engine_cache_refresher`) | **No** |
| `yfinance` fetch | **No** |
| `confluence_pipeline_runner` invocation | **No** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster / Confluence batch execution | **No** |
| Production data write | **No** (0/0/0 across 83,021 files) |
| Subprocess invocations from production modules | **No** — direct operator commands were pinned-interpreter Python invocations of `multiwindow_k_input_adapter_diagnostic.py` and standard `git` / `gh` housekeeping for branch + PR |
| Execution-log written to `output/automation_logs/` | **No** — the diagnostic does not emit an execution log; the JSON stdout was captured to the temp dir only |

The Phase 6H-5 two-key writer gate, Phase 6I-9 supervised gate, Phase 6I-10 production-root snapshot strategy, Phase 6I-12 ProviderFetchTelemetry four-surface contract, Phase 6I-15 source-availability advisory contract, Phase 6I-20 gap audit, Phase 6I-21 engine core, Phase 6I-22 input adapter, Phase 6I-23 payload builder, Phase 6I-24 patch planner, and Phase 6I-25 patch writer are all unchanged.

---

## 9. Operational state carried forward

- Cache state: `cache_date_range_end=2026-05-12`; `current_as_of_date=2026-05-13` (rolled at Phase 6I-26 evidence pass).
- STATE 4 / cache-behind-cutoff (per Phase 6I-17 4-state list).
- Production `has_true_multiwindow_k_engine_outputs` — still `false` for SPY.
- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still open.
- Writer-surface provider telemetry — still pending.

---

## 10. Validation

- `git diff --check`: clean (only the new module + test + this Markdown doc are tracked; no whitespace errors).
- `git diff --stat`: 3 files added (module + test + doc); zero pre-existing files modified.
- Pinned interpreter used on every Python invocation: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.
- Focused 7-way: 196 passed.

---

## 11. Reference paths

- Module: `project/multiwindow_k_input_adapter_diagnostic.py`.
- Tests: `project/test_scripts/test_multiwindow_k_input_adapter_diagnostic.py`.
- Phase 6I-22 adapter (subject of this diagnostic): `project/multiwindow_k_input_adapter.py` + `project/md_library/shared/2026-05-13_PHASE_6I22_MULTIWINDOW_K_INPUT_ADAPTER.md` (§ 6 documents the `missing_target_close` limitation that Phase 6I-27 now directly proves for SPY).
- Phase 6I-26 dry-run evidence (the predecessor evidence pass that left `missing_target_close` as inference): `project/md_library/shared/2026-05-13_PHASE_6I26_SPY_CONFLUENCE_PATCH_WRITER_DRY_RUN_EVIDENCE.md` § 12.3.
- Phase 6I-25 writer (downstream of this gap): `project/multiwindow_k_confluence_patch_writer.py` + `project/md_library/shared/2026-05-13_PHASE_6I25_MULTIWINDOW_K_CONFLUENCE_PATCH_WRITER.md`.
- Phase 6I-24 planner: `project/multiwindow_k_confluence_patch_planner.py` + `project/md_library/shared/2026-05-13_PHASE_6I24_MULTIWINDOW_K_CONFLUENCE_PATCH_PLANNER.md`.
- Phase 6I-23 payload builder: `project/multiwindow_k_engine_payload_builder.py` + `project/md_library/shared/2026-05-13_PHASE_6I23_MULTIWINDOW_K_ENGINE_PAYLOAD_BUILDER.md`.
- Phase 6I-21 engine core: `project/multiwindow_k_engine_core.py`.
- Phase 6I-20 gap audit: `project/multiwindow_k_engine_gap_audit.py`.
- Temp evidence directory: `C:\Users\sport\AppData\Local\Temp\phase_6i27_spy_adapter_diagnostic\` (OUTSIDE production roots, OUTSIDE the repo; nothing in it is committed).
- Phase 6I-26 temp evidence directory (whose snapshot/diff helpers were reused): `C:\Users\sport\AppData\Local\Temp\phase_6i26_spy_confluence_patch_writer_dry_run\`.
- CLAUDE.md § 6 — current sprint state.
