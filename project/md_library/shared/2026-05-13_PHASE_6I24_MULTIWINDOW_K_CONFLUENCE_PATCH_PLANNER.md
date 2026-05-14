# Phase 6I-24: Read-only Confluence artifact patch planner for the multi-window K engine payload

Sprint date: **2026-05-13**.
Branch: `phase-6i-24-multiwindow-k-confluence-patch-planner`.
Module: `project/multiwindow_k_confluence_patch_planner.py`.
Tests: `project/test_scripts/test_multiwindow_k_confluence_patch_planner.py`.
Doc: this file.

This phase ships the **final read-only step in the multi-
window K engine track** before an explicit artifact-write
phase. The planner wires the Phase 6I-23 in-memory payload
builder to a read-only inspection of the existing on-disk
Confluence artifact and produces a **reviewable patch
plan** describing exactly which top-level JSON keys a
future writer phase would attach or replace — without
writing anything.

It still does **NOT** write the Confluence artifact, and
production `has_true_multiwindow_k_engine_outputs` remains
False until a later phase actually persists the planned
fields.

---

## 0. Scope

A read-only Python module that, for one target ticker:

1. Calls Phase 6I-23
   `build_multiwindow_k_engine_payload(...)` (or an
   injected stand-in via the `payload_builder_callable`
   seam). The builder itself gates on the Phase 6I-22
   adapter's strict full-member coverage AND validates
   the core-grid result covers the full canonical 60-cell
   grid before reporting `payload_ready=True`.
2. Locates the target ticker's Confluence artifact path
   under `<artifact_root>/confluence/<TICKER>/`
   (default resolves `output/research_artifacts/`
   relative to the project dir; injectable via the
   `artifact_locator_callable` seam). Returns the newest
   `*.research_day.json` file by mtime.
3. Loads the existing artifact read-only via `json.load`
   on the located file (injectable via the
   `artifact_loader_callable` seam). The artifact bytes
   are NOT modified by this call.
4. Classifies which top-level JSON keys a future writer
   phase would attach (`fields_to_add`) or replace
   (`fields_to_replace`) on the existing artifact:

   - `per_window_k_metrics` (Phase 6I-20-shaped);
   - `build_wide_window_alignment` (Phase 6I-20-shaped);
   - `multiwindow_k_engine_payload_metadata` (a small
     attribution block carrying `generated_at` /
     `cell_count` / `K_values` / `windows` /
     `current_as_of_date` / `phase` / `planner_phase`
     so a future writer + audit can trace which builder
     run produced the payload).
5. Returns a structured
   `MultiWindowKConfluencePatchPlan` carrying the
   **planned payload body** (the exact JSON object that
   would be merged) plus a compact existing-field summary
   and a stable `recommended_next_action` code.

---

## 1. Decision semantics

The planner's verdict cascade resolves to exactly one of
four recommended-next-action codes:

| Condition | `patch_ready` | `recommended_next_action` |
|---|---|---|
| Payload builder reports `payload_ready=False` | `False` | `build_payload_first` |
| Payload ready, artifact missing | `False` | `create_confluence_artifact_first` |
| Payload ready, artifact present but unreadable | `False` | `manual_review_required` |
| Payload ready, artifact readable | `True` | `ready_for_reviewed_artifact_write` |

`patch_ready=False` always means `fields_to_add=()`,
`fields_to_replace=()`, and `planned_payload={}`. No
near-miss schema; no fabrication.

`patch_ready=True` does **NOT** authorize an artifact
write. It only means a reviewable patch plan is available
for a future writer phase to consume.

### 1.1 Field add/replace classification

For each of the three planned top-level keys:

- If the existing artifact already carries the key →
  appended to `fields_to_replace`.
- Otherwise → appended to `fields_to_add`.

The two lists partition `PLANNED_PAYLOAD_KEYS` exactly.
The planner never mutates the existing artifact; the
classification is informational — a future writer phase
decides how to merge.

---

## 2. What this module IS NOT

- **NOT an artifact writer.** No path through this module
  writes to the Confluence artifact (or any on-disk file).
  Pinned by `test_planner_module_has_no_artifact_writes`
  via AST scan over `ast.Call` nodes rejecting
  `Path.write_text` / `Path.write_bytes` / `json.dump`
  calls. The default artifact loader uses
  `open(path, "r", encoding="utf-8")` plus `json.load`;
  the artifact file is opened read-only.
- **NOT a writer / refresher / pipeline runner.** No
  `--write` invocation, no source refresh, no `yfinance`
  fetch, no `PRJCT9_AUTOMATION_WRITE_AUTH`, no
  subprocess, no StackBuilder / OnePass / ImpactSearch /
  TrafficFlow / Spymaster batch execution.
- **NOT a fabricator.** When the upstream payload
  builder reports `payload_ready=False` (any of its
  four issue-code paths) the planner refuses to
  fabricate a patch body and returns `patch_ready=False`
  + empty `planned_payload`.
- **NOT a flip of production
  `has_true_multiwindow_k_engine_outputs`.** That
  boolean closes only on a future supervised write
  phase that actually writes the planned fields to the
  on-disk Confluence artifact.

---

## 3. Operational-state caveats carried forward

- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still
  open.
- Writer-surface provider telemetry — still pending.
- Production `has_true_multiwindow_k_engine_outputs` —
  still False.
- Operational state remains **STATE C / WAIT** (cache
  `2026-05-12` == cutoff `2026-05-12`). The Phase 6H-5
  two-key writer gate, the Phase 6I-9 supervised gate,
  the Phase 6I-10 production-root snapshot strategy,
  the Phase 6I-12 ProviderFetchTelemetry four-surface
  contract, the Phase 6I-15 source-availability
  advisory contract, the Phase 6I-20 gap audit, the
  Phase 6I-21 engine core, the Phase 6I-22 input
  adapter, and the Phase 6I-23 payload builder are all
  unchanged.

---

## 4. Public API

```python
from multiwindow_k_confluence_patch_planner import (
    plan_multiwindow_k_confluence_patch,
    MultiWindowKConfluencePatchPlan,
    CANONICAL_WINDOWS,
    CANONICAL_K_VALUES,
    PLANNED_PAYLOAD_KEYS,
    # Stable issue codes:
    ISSUE_PAYLOAD_NOT_READY,
    ISSUE_CONFLUENCE_ARTIFACT_MISSING,
    ISSUE_CONFLUENCE_ARTIFACT_UNREADABLE,
    # Stable recommended-action codes:
    ACTION_BUILD_PAYLOAD_FIRST,
    ACTION_CREATE_CONFLUENCE_ARTIFACT_FIRST,
    ACTION_READY_FOR_REVIEWED_ARTIFACT_WRITE,
    ACTION_MANUAL_REVIEW_REQUIRED,
)

plan = plan_multiwindow_k_confluence_patch(
    "SPY",
    artifact_root=None,         # default: output/research_artifacts
    stackbuilder_root=None,
    signal_library_dir=None,
    K_values=CANONICAL_K_VALUES,
    windows=CANONICAL_WINDOWS,
    run_dir=None,
    current_as_of_date=None,
    payload_builder_callable=None,   # injection seam
    artifact_loader_callable=None,   # injection seam
    artifact_locator_callable=None,  # injection seam
)

if plan.patch_ready:
    # plan.planned_payload is the exact JSON object a
    # future writer phase would merge onto the Confluence
    # artifact at plan.artifact_path. The two Phase 6I-20-
    # shaped fields are accepted by gap_audit's
    # _per_window_k_metrics_are_valid and
    # _build_wide_alignment_is_valid validators.
    ...
else:
    # plan.recommended_next_action names the next step.
    ...
```

`MultiWindowKConfluencePatchPlan` fields:

| Field | Meaning |
|---|---|
| `generated_at` | ISO timestamp. |
| `target_ticker` | Upper-cased. |
| `current_as_of_date` | Passed-through cutoff date. |
| `artifact_path` | Resolved Confluence artifact path (or `None`). |
| `artifact_exists` | True iff a `.research_day.json` was found. |
| `payload_ready` | Mirror of upstream Phase 6I-23 verdict. |
| `patch_ready` | True iff payload AND artifact are both ready. |
| `fields_to_add` | Keys in `PLANNED_PAYLOAD_KEYS` absent from the existing artifact. |
| `fields_to_replace` | Keys in `PLANNED_PAYLOAD_KEYS` present in the existing artifact. |
| `existing_field_summary` | Compact top-level shape of the existing artifact. |
| `payload_summary` | Compact summary of the Phase 6I-23 builder's report. |
| `planned_payload_keys` | Iteration order of `planned_payload`. |
| `planned_payload` | Exact JSON object a future writer would merge. |
| `issue_codes` | Stable tuple of `ISSUE_*` strings raised by the planner. |
| `recommended_next_action` | One of the four `ACTION_*` codes. |
| `remaining_limitations` | Operational-state caveats (Phase 6I-24 doc shape). |

### 4.1 Stable issue codes

`ALL_ISSUE_CODES`:

- `payload_not_ready` — upstream Phase 6I-23 builder
  returned `payload_ready=False`.
- `confluence_artifact_missing` — no
  `*.research_day.json` found under
  `<artifact_root>/confluence/<TICKER>/`.
- `confluence_artifact_unreadable` — file found but JSON
  parse failed or top-level was not a dict.

### 4.2 Stable recommended-action codes

`ALL_ACTIONS`:

- `build_payload_first` — payload builder not ready.
- `create_confluence_artifact_first` — payload ready,
  artifact absent.
- `manual_review_required` — payload ready, artifact
  present but unreadable.
- `ready_for_reviewed_artifact_write` — payload ready,
  artifact present + readable; a future explicit writer
  phase may consume `planned_payload`.

### 4.3 Planned payload top-level keys

`PLANNED_PAYLOAD_KEYS`:

- `per_window_k_metrics` (Phase 6I-20-shaped)
- `build_wide_window_alignment` (Phase 6I-20-shaped)
- `multiwindow_k_engine_payload_metadata`

---

## 5. CLI

```
python multiwindow_k_confluence_patch_planner.py --ticker SPY
```

JSON to stdout. `rc=0` / `rc=2` (invalid args / missing
`--ticker`) / `rc=3` (unexpected). No `SystemExit` leak.

---

## 6. Strictly read-only

The load-bearing claims of this module:

- No `daily_board_automation_writer` import.
- No `signal_engine_cache_refresher` import.
- No `confluence_pipeline_runner` import.
- No `yfinance` / `dash` import.
- No `trafficflow` / `spymaster` / `impactsearch` /
  `onepass` / `confluence` / `cross_ticker_confluence` /
  `daily_signal_board` import.
- No `subprocess`.
- **No `trafficflow_multitimeframe_bridge` import**.
- **No `trafficflow_k_artifact_builder` import**.
- **No `multiwindow_k_engine_gap_audit` import** (tests
  exercise the audit's validators to prove integration
  but the production code path does not import the
  audit).
- **No `multiwindow_k_input_adapter` import** (the
  planner consumes the Phase 6I-23 builder, which in
  turn consumes the adapter; this layer does not import
  the adapter directly).
- **No projection logic**: no call to `.resample()` or
  `.ffill()` anywhere in code (AST-verified).
- **No raw `pickle.load`** (Confluence artifacts are
  JSON; AST-verified).
- **No on-disk artifact write.** Pinned by
  `test_planner_module_has_no_artifact_writes` (AST
  scan over `ast.Call` nodes rejecting `write_text` /
  `write_bytes` / `json.dump` calls).

Allowed imports: the Phase 6I-21 core (read-only by
contract; for canonical-constants only) and the
Phase 6I-23 payload builder (read-only by contract).

---

## 7. Tests (23 pinned contracts)

`project/test_scripts/test_multiwindow_k_confluence_patch_planner.py`:

1. Forbidden-imports static guard.
2. Not a projection: no `.resample()` / `.ffill()` call.
3. No raw `pickle.load`.
4. **No artifact writes anywhere in code**: AST scan
   rejects `Path.write_text` / `Path.write_bytes` /
   `json.dump` call sites in the planner module.
5. **Payload-not-ready** → `patch_ready=False`,
   `fields_to_add=()`, `fields_to_replace=()`,
   `planned_payload={}`, `ISSUE_PAYLOAD_NOT_READY`,
   `ACTION_BUILD_PAYLOAD_FIRST`.
6. **Missing artifact** → `patch_ready=False`,
   `ISSUE_CONFLUENCE_ARTIFACT_MISSING`,
   `ACTION_CREATE_CONFLUENCE_ARTIFACT_FIRST`.
7. **Unreadable artifact** (fake loader returns `None`)
   → `patch_ready=False`,
   `ISSUE_CONFLUENCE_ARTIFACT_UNREADABLE`,
   `ACTION_MANUAL_REVIEW_REQUIRED`.
8. **Add path**: artifact exists without the planner's
   three keys → `patch_ready=True`, all three in
   `fields_to_add`, `fields_to_replace=()`,
   `planned_payload` populated (`per_window_k_metrics`
   has 60 entries; `build_wide_window_alignment` has 5
   entries).
9. **Replace path**: artifact already has all three
   keys → all three in `fields_to_replace`,
   `fields_to_add=()`, `planned_payload` still
   populated.
10. **Mixed add + replace**: artifact has 1 of the 3
    keys → that key in `fields_to_replace`, the other
    two in `fields_to_add`. Lists partition
    `PLANNED_PAYLOAD_KEYS`.
11. **Existing artifact bytes UNCHANGED**: capture raw
    bytes + mtime before plan; assert identical after.
12. **Phase 6I-20 validator integration**: the planned
    payload's `per_window_k_metrics` and
    `build_wide_window_alignment` both pass
    `gap_audit._per_window_k_metrics_are_valid` and
    `gap_audit._build_wide_alignment_is_valid`
    respectively.
13-14. JSON round-trip on ready + not-ready paths.
15-19. CLI: `rc=0` (happy); `rc=2` (missing ticker);
    `rc=2` (unknown flag); `rc=2` (no `SystemExit`
    leak); `rc=3` (unhandled exception).
20-23. Constants: re-exported from the core; every
    `ALL_ISSUE_CODES` entry exposed as an `ISSUE_*`
    module attribute; every `ALL_ACTIONS` entry exposed
    as an `ACTION_*` module attribute;
    `PLANNED_PAYLOAD_KEYS` pinned to the three target
    keys.

All tests use `tmp_path` fixtures + injected fake
loaders + monkeypatch only. No real adapter, no real
core, no real Confluence artifact, no `yfinance` /
live engine / production-write touch.

---

## 8. Validation

```
py_compile: clean on multiwindow_k_confluence_patch_planner.py +
            test_multiwindow_k_confluence_patch_planner.py.

pytest test_scripts/test_multiwindow_k_confluence_patch_planner.py -q:
  23 passed in 0.99 s

Focused 5-way (planner + Phase 6I-23 builder + Phase 6I-22
              adapter + Phase 6I-21 core + Phase 6I-20 gap audit):
  136 passed in 1.32 s
  ├── multiwindow_k_confluence_patch_planner   23 passed
  ├── multiwindow_k_engine_payload_builder     29 passed
  ├── multiwindow_k_input_adapter              23 passed
  ├── multiwindow_k_engine_core                38 passed
  └── multiwindow_k_engine_gap_audit           23 passed

git diff --check: clean.
```

---

## 9. What this phase does NOT do

- Does **NOT** write any on-disk artifact.
- Does **NOT** add `per_window_k_metrics` or
  `build_wide_window_alignment` to the on-disk Confluence
  artifact.
- Does **NOT** flip the Phase 6I-20 gap audit to True
  against production tickers.
- Does **NOT** close `real_confluence_pipeline_runner_write`
  / `real_post_pipeline_validation_on_writer_path` /
  writer-surface provider telemetry.
- Does **NOT** authorize a writer run.
- Does **NOT** mutate any existing Confluence artifact
  byte or mtime.

---

## 10. Reference paths

- Module: `project/multiwindow_k_confluence_patch_planner.py`.
- Tests: `project/test_scripts/test_multiwindow_k_confluence_patch_planner.py`.
- Phase 6I-23 payload builder (this planner's upstream):
  `project/multiwindow_k_engine_payload_builder.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I23_MULTIWINDOW_K_ENGINE_PAYLOAD_BUILDER.md`.
- Phase 6I-22 input adapter:
  `project/multiwindow_k_input_adapter.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I22_MULTIWINDOW_K_INPUT_ADAPTER.md`.
- Phase 6I-21 engine core:
  `project/multiwindow_k_engine_core.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I21_MULTIWINDOW_K_ENGINE_CORE_EVALUATOR.md`.
- Phase 6I-20 gap audit (the contract this planner's
  output satisfies):
  `project/multiwindow_k_engine_gap_audit.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I20_MULTIWINDOW_K_ENGINE_GAP_CONTRACT.md`.
- Phase 6I-18 next-probe handoff (operational state):
  `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md`.
- CLAUDE.md sec 6 — current sprint state.
