# Phase 6I-25: Guarded Confluence artifact patch writer for the multi-window K engine payload

Sprint date: **2026-05-13**.
Branch: `phase-6i-25-multiwindow-k-confluence-patch-writer`.
Module: `project/multiwindow_k_confluence_patch_writer.py`.
Tests: `project/test_scripts/test_multiwindow_k_confluence_patch_writer.py`.
Doc: this file.

This phase ships the **first guarded artifact-write
implementation layer** in the multi-window K engine track.
The writer consumes a Phase 6I-24
`MultiWindowKConfluencePatchPlan` with `patch_ready=True`
and persists the planned payload fields onto the existing
on-disk Confluence research-day artifact — but **only**
after the two-key writer authorization gate passes.

This PR ships the writer + tests + doc only. **No
production artifact was written in this phase.** Every
test operates on `tmp_path` fixtures with monkey-patched
env state. Production
`has_true_multiwindow_k_engine_outputs` remains False
until a future supervised authorized run invokes this
writer against the real Confluence artifact AND the
Phase 6I-20 audit verifies the persisted shape.

---

## 0. Scope

A guarded artifact-write layer. For one target ticker the
writer:

1. Calls the Phase 6I-24 planner
   (`multiwindow_k_confluence_patch_planner.plan_multiwindow_k_confluence_patch`,
   or an injected stand-in via the
   `patch_planner_callable` seam). The planner itself
   enforces the two-gate `patch_ready=True` contract
   (upstream Phase 6I-23 builder readiness AND
   planner-side local Phase 6I-20-shaped validator).
2. Verifies the **two-key writer authorization** (same
   pattern as Phase 6H-5):
   - CLI flag / function arg `write=True`;
   - env var `PRJCT9_AUTOMATION_WRITE_AUTH ==
     "phase_6h5_explicit"`.
3. Reads the existing artifact JSON, merges the planned
   payload onto a **copy**, writes the merged JSON to a
   same-directory temporary file, then atomically
   replaces the original via `Path.replace`.
4. Records SHA-256 of the artifact before and after the
   write attempt so an audit can verify the identity of
   bytes that were replaced.
5. Optionally appends one JSONL row to the execution log
   (`--execution-log`) per invocation, covering both
   dry-run and write attempts.

---

## 1. `wrote_artifact=True` requires THREE gates (Phase 6I-25 Codex amendment)

The writer is the final mutation boundary in the
multi-window K engine track. It does NOT blindly trust
an injected or buggy planner object that claims
`patch_ready=True`. `wrote_artifact=True` requires ALL
of:

1. **Two-key writer authorization** (same pattern as
   Phase 6H-5 `daily_board_automation_writer`):

   | Gate | Required |
   |---|---|
   | `write=True` (CLI `--write` flag or function arg) | Yes |
   | `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` | Yes |

   Both keys are required. **`write=False` is the
   default.**

2. **Upstream planner readiness**: the Phase 6I-24
   planner reported `patch_ready=True`.

3. **Writer-side plan/payload consistency** (Phase 6I-25
   Codex amendment): the writer's own
   `_writer_plan_payload_is_consistent(plan)` accepts the
   plan. This validator requires ALL of:

   - `plan.planned_payload` is a Mapping;
   - `set(planned_payload.keys()) ==
     set(PLANNED_PAYLOAD_KEYS)` — exactly the three
     planned top-level keys;
   - `plan.planned_payload_keys` mirrors those three
     keys (same set, length 3);
   - `plan.fields_to_add` and `plan.fields_to_replace`
     partition `PLANNED_PAYLOAD_KEYS` exactly;
   - `plan.fields_to_add` and `plan.fields_to_replace`
     are disjoint;
   - no unknown keys appear in either
     `plan.fields_to_add` or `plan.fields_to_replace`;
   - `per_window_k_metrics` passes
     `_writer_per_window_k_metrics_are_valid` (canonical
     60-cell grid, required-five fields, no
     `bool`-as-`int`, no duplicates);
   - `build_wide_window_alignment` passes
     `_writer_build_wide_alignment_is_valid` (one entry
     per canonical window with bool / int field types).

   The validators are re-derived LOCALLY in the writer
   module — they do NOT call the Phase 6I-24 planner's
   private validators. The contract is small enough to
   mirror, and self-sufficiency means a buggy / replaced
   planner cannot subvert the writer's own contract.

Either auth gate failed / planner not ready / writer-side
consistency rejected → `wrote_artifact=False` +
appropriate `ISSUE_*` + `recommended_next_action`; **no
file mutation**.

### 1.1 Decision cascade

| State | `wrote_artifact` | `issue_codes` | `recommended_next_action` |
|---|---|---|---|
| `write=False` | `False` | `write_not_requested` | `dry_run_review_patch_plan` |
| `write=True` but env wrong | `False` | `env_authorization_missing_or_invalid` | `set_write_authorization_and_rerun` |
| Both keys pass + `patch_ready=False` | `False` | `patch_plan_not_ready` | `resolve_patch_plan_first` |
| Both keys pass + `patch_ready=True` + no artifact path | `False` | `artifact_path_missing` | `manual_review_required` |
| **Both keys pass + `patch_ready=True` + plan/payload inconsistent** *(Codex amendment)* | `False` | `patch_plan_contract_invalid` | `manual_review_required` |
| Both keys pass + `patch_ready=True` + read failure | `False` | `artifact_read_failed` | `manual_review_required` |
| Both keys pass + `patch_ready=True` + write failure | `False` | `artifact_write_failed` | `manual_review_required` |
| Both keys pass + `patch_ready=True` + writer-side consistency OK + write succeeds | `True` | `()` | `artifact_write_complete` |

On any not-write path, the original artifact bytes are
**not touched**. The atomic-write helper's `except` path
unlinks the temp file before re-raising, so the target
artifact is byte-for-byte identical to its pre-call state
on write failure.

---

## 2. Atomic write semantics

Write order:

1. Read existing artifact via
   `open(path, "r", encoding="utf-8") + json.load`.
2. Verify top-level is a dict (else
   `ISSUE_ARTIFACT_READ_FAILED`).
3. Build `merged = dict(existing); merged[k] = planned[k]
   for k in PLANNED_PAYLOAD_KEYS if k in planned`.
   Unrelated existing fields preserved verbatim.
4. Create same-directory temp file via
   `tempfile.mkstemp(prefix=".tmp_",
   suffix=".research_day.json", dir=str(parent))`.
5. Write merged JSON to the temp file; flush + fsync.
6. `Path(tmp).replace(artifact_path)` — atomic rename
   within the same filesystem.
7. On any failure mid-way, unlink the temp file and
   re-raise. The original artifact bytes are unchanged.

**No `subprocess` is used.** All filesystem operations
use Python stdlib (`tempfile`, `os.fdopen`, `os.fsync`,
`Path.replace`, `Path.unlink`).

The SHA-256 hash of the artifact is captured before and
after the write attempt. On a successful write, the two
hashes differ; on any not-write path, the two are
identical. The hashes are over raw bytes so byte-for-byte
identity is auditable.

---

## 3. What this module IS NOT

- **NOT a writer for arbitrary fields.** Only the three
  Phase 6I-24-defined `PLANNED_PAYLOAD_KEYS`
  (`per_window_k_metrics` /
  `build_wide_window_alignment` /
  `multiwindow_k_engine_payload_metadata`) are merged.
  Existing artifact fields outside those three keys are
  preserved.
- **NOT a refresher / pipeline runner / live engine.**
  No source refresh. No `yfinance` fetch. No
  `confluence_pipeline_runner`. No StackBuilder /
  OnePass / ImpactSearch / TrafficFlow / Spymaster batch
  execution. No `subprocess`. The writer only consumes a
  precomputed patch plan.
- **NOT an unguarded write surface.** Both authorization
  keys are required; defaults are dry-run.
- **NOT a flipper of production
  `has_true_multiwindow_k_engine_outputs`.** That
  boolean closes only after a future supervised run
  invokes this writer against the real Confluence
  artifact AND the Phase 6I-20 audit verifies the
  persisted shape.

---

## 4. Operational-state caveats carried forward

- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still
  open.
- Writer-surface provider telemetry — still pending.
- Production `has_true_multiwindow_k_engine_outputs` —
  still False.
- Operational state remains **STATE C / WAIT** (cache
  `2026-05-12` == cutoff `2026-05-12`). The Phase 6H-5
  two-key writer gate (used by
  `daily_board_automation_writer.py`), the Phase 6I-9
  supervised gate, the Phase 6I-10 production-root
  snapshot strategy, the Phase 6I-12 ProviderFetchTelemetry
  four-surface contract, the Phase 6I-15 source-
  availability advisory contract, the Phase 6I-20 gap
  audit, the Phase 6I-21 engine core, the Phase 6I-22
  input adapter, the Phase 6I-23 payload builder, and
  the Phase 6I-24 patch planner are all unchanged.

---

## 5. Public API

```python
from multiwindow_k_confluence_patch_writer import (
    apply_multiwindow_k_confluence_patch,
    MultiWindowKConfluencePatchWriteResult,
    CANONICAL_WINDOWS, CANONICAL_K_VALUES,
    PLANNED_PAYLOAD_KEYS,
    ENV_VAR_NAME,            # "PRJCT9_AUTOMATION_WRITE_AUTH"
    ENV_VAR_REQUIRED_VALUE,  # "phase_6h5_explicit"
    # Stable issue codes:
    ISSUE_WRITE_NOT_REQUESTED,
    ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID,
    ISSUE_PATCH_PLAN_NOT_READY,
    ISSUE_ARTIFACT_PATH_MISSING,
    ISSUE_ARTIFACT_READ_FAILED,
    ISSUE_ARTIFACT_WRITE_FAILED,
    # Stable recommended-action codes:
    ACTION_DRY_RUN_REVIEW_PATCH_PLAN,
    ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN,
    ACTION_RESOLVE_PATCH_PLAN_FIRST,
    ACTION_ARTIFACT_WRITE_COMPLETE,
    ACTION_MANUAL_REVIEW_REQUIRED,
)

result = apply_multiwindow_k_confluence_patch(
    "SPY",
    artifact_root=None,             # default: output/research_artifacts
    stackbuilder_root=None,
    signal_library_dir=None,
    K_values=CANONICAL_K_VALUES,
    windows=CANONICAL_WINDOWS,
    run_dir=None,
    current_as_of_date=None,
    write=False,                    # default: dry-run
    execution_log=None,             # optional JSONL path
    patch_planner_callable=None,    # injection seam (tests)
)
```

### 5.1 Result fields

| Field | Meaning |
|---|---|
| `generated_at` | ISO timestamp. |
| `target_ticker` | Upper-cased. |
| `artifact_path` | Resolved Confluence artifact path (or `None`). |
| `write_requested` | Mirror of `write=` kwarg. |
| `write_authorized` | True iff BOTH `write=True` AND env var matches. |
| `planner_patch_ready` | Mirror of upstream planner verdict. |
| `wrote_artifact` | True iff every gate passed AND atomic write succeeded. |
| `fields_added` | Mirror of `plan.fields_to_add` on the write path; `()` otherwise. |
| `fields_replaced` | Mirror of `plan.fields_to_replace` on the write path; `()` otherwise. |
| `planned_payload_keys` | The three planned top-level keys. |
| `issue_codes` | Stable tuple of `ISSUE_*` strings. |
| `recommended_next_action` | One of five `ACTION_*` codes. |
| `pre_write_sha256` | SHA-256 of artifact before the call (or `None`). |
| `post_write_sha256` | SHA-256 of artifact after the call (equals `pre_write_sha256` on no-write paths). |
| `execution_log_path` | Mirror of `execution_log=` kwarg if supplied. |
| `planner_summary` | Compact summary of the planner's report. |
| `remaining_limitations` | Operational-state caveats (Phase 6I-25 doc shape). |

### 5.2 Stable issue codes

`ALL_ISSUE_CODES`:

- `write_not_requested` — `write=False` (dry-run).
- `env_authorization_missing_or_invalid` — env var
  absent or wrong value.
- `patch_plan_not_ready` — planner returned
  `patch_ready=False`.
- `artifact_path_missing` — planner returned
  `artifact_path=None`.
- `artifact_read_failed` — JSON parse failure on the
  existing artifact / non-dict top level.
- `artifact_write_failed` — atomic write helper raised;
  original bytes preserved.
- `patch_plan_contract_invalid` **(Phase 6I-25 Codex
  amendment)** — planner claimed `patch_ready=True` but
  the writer's own `_writer_plan_payload_is_consistent`
  validator rejected the plan. Causes: `planned_payload`
  not a Mapping; `set(planned_payload.keys())` doesn't
  match `set(PLANNED_PAYLOAD_KEYS)`;
  `planned_payload_keys` attr lies about the keys;
  `fields_to_add` / `fields_to_replace` don't partition
  `PLANNED_PAYLOAD_KEYS` exactly; the two lists overlap;
  unknown keys in either list; or
  `per_window_k_metrics` / `build_wide_window_alignment`
  payload contents fail the local Phase 6I-20-shape
  validators. The writer is the final mutation
  boundary; a malformed / injected / buggy planner
  object cannot drive a partial or malformed write
  through this layer.

### 5.3 Stable recommended-action codes

`ALL_ACTIONS`:

- `dry_run_review_patch_plan` — dry-run; review the
  planner output, then re-run with `--write` if
  appropriate.
- `set_write_authorization_and_rerun` — set the env var
  and re-run.
- `resolve_patch_plan_first` — fix the upstream payload
  / artifact gap surfaced by the planner.
- `artifact_write_complete` — write succeeded.
- `manual_review_required` — artifact path missing /
  read failure / write failure.

---

## 6. CLI

```
# Dry-run (default).
python multiwindow_k_confluence_patch_writer.py --ticker SPY

# Authorized write (also requires the env var).
PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit \
  python multiwindow_k_confluence_patch_writer.py \
    --ticker SPY --write
```

CLI flags: `--ticker SPY`, `--artifact-root`,
`--stackbuilder-root`, `--signal-library-dir`,
`--run-dir`, `--current-as-of-date`, `--write`,
`--execution-log`.

JSON to stdout. **`rc=0` for both dry-run and successful
write paths** (the JSON describes the outcome). `rc=2`
for missing `--ticker` / invalid args. `rc=3` for
unexpected exception. No `SystemExit` leak from `main()`.

---

## 7. Execution log

When `--execution-log <path>` is supplied, exactly one
JSON line is appended to the file per invocation
(dry-run AND write attempts). The line is the full
result dict, parseable directly with `json.loads`.

Tests use `tmp_path / "logs" / "writer.jsonl"` only;
production roots are never touched by the test suite.

---

## 8. Strictly read-only by default

The load-bearing claims of this module:

- No `daily_board_automation_writer` import.
- No `signal_engine_cache_refresher` import.
- No `confluence_pipeline_runner` import.
- No `yfinance` / `dash` import.
- No `trafficflow` / `spymaster` / `impactsearch` /
  `onepass` / `confluence` / `cross_ticker_confluence` /
  `daily_signal_board` import.
- No `subprocess` import or `subprocess.X(...)` call —
  AST-verified by tests.
- No `trafficflow_multitimeframe_bridge` import.
- No `trafficflow_k_artifact_builder` import.
- No `multiwindow_k_engine_gap_audit` import.
- No `multiwindow_k_input_adapter` import (the writer
  consumes Phase 6I-24, which transitively consumes
  6I-23 → 6I-22 — the writer never directly imports the
  adapter).
- No `multiwindow_k_engine_payload_builder` import (same
  reason).
- No `confluence_decision_brief` import.
- No projection logic (no `.resample()` / `.ffill()`
  call) — AST-verified.
- No raw `pickle.load` — AST-verified.
- The only on-disk writes happen inside
  `_atomic_write_artifact` (gated by the authorization
  cascade) and the execution-log appender (only when
  `--execution-log` is supplied). Both are protected by
  the cascade above.

Allowed imports: the Phase 6I-21 core (for canonical
constants only) and the Phase 6I-24 patch planner (the
upstream chain).

---

## 9. Tests (37 pinned contracts)

`project/test_scripts/test_multiwindow_k_confluence_patch_writer.py`:

1. Forbidden-imports static guard.
2. Not a projection: no `.resample()` / `.ffill()` call.
3. No raw `pickle.load`.
4. No `subprocess` import or call (AST-level; docstring
   text allowed to mention the word).
5. **Dry-run does not mutate artifact**: `write=False` →
   bytes / mtime / SHA unchanged; `ISSUE_WRITE_NOT_REQUESTED`
   + `ACTION_DRY_RUN_REVIEW_PATCH_PLAN`.
6. **Missing env var with `write=True`** → not mutated;
   `ISSUE_ENV_AUTHORIZATION_MISSING_OR_INVALID` +
   `ACTION_SET_WRITE_AUTHORIZATION_AND_RERUN`.
7. **Wrong env value with `write=True`** → same.
8. **Authorized but `patch_ready=False`** → not
   mutated; `ISSUE_PATCH_PLAN_NOT_READY` +
   `ACTION_RESOLVE_PATCH_PLAN_FIRST`.
9. **Authorized + ready happy path** writes exactly the
   three planned keys, preserves unrelated existing
   fields (engine / artifact_version / daily / summary
   carried through verbatim), `wrote_artifact=True`,
   SHA changes, `ACTION_ARTIFACT_WRITE_COMPLETE`.
10. **Existing planned keys are replaced, not
    duplicated**: pre-existing stale
    `per_window_k_metrics` / `build_wide_window_alignment`
    / `multiwindow_k_engine_payload_metadata` are
    fully overwritten with the fresh planned payload.
11. **Mixed add + replace mirrors the planner**: when
    the planner classifies 1 key as replace and 2 as
    add, the writer's `fields_added` / `fields_replaced`
    mirror exactly that classification.
12. **Atomic write failure preserves original bytes**:
    `monkeypatch` `_atomic_write_artifact` to raise; the
    original artifact bytes are unchanged;
    `ISSUE_ARTIFACT_WRITE_FAILED` +
    `ACTION_MANUAL_REVIEW_REQUIRED`.
13. **Artifact read failure**: artifact present but
    non-JSON → `ISSUE_ARTIFACT_READ_FAILED`.
14. **Missing artifact path**: planner returned
    `artifact_path=None` →
    `ISSUE_ARTIFACT_PATH_MISSING`.
15. **Execution log appends one JSONL row per
    invocation**: two invocations (dry-run + write
    attempt) produce two valid JSON lines.
16. **Authorized write logs row with
    `ACTION_ARTIFACT_WRITE_COMPLETE`**.
17. JSON round-trip on dry-run result.
18-21. Constants: `ALL_ISSUE_CODES` /
    `ALL_ACTIONS` exposed; env-var constants pinned;
    `PLANNED_PAYLOAD_KEYS` re-exported from planner.
22-26. CLI: `rc=0` (dry-run happy); `rc=2` (missing
    ticker); `rc=2` (unknown flag); `rc=2` (no
    `SystemExit` leak); `rc=3` (unhandled exception).
27. CLI write path works against `tmp_path` only with
    monkey-patched env (end-to-end CLI integration; the
    on-disk artifact mutated lives inside the
    `tmp_path` tree).
28. **No production roots touched**: defensive
    regression guard pins that the written artifact
    path is under `tmp_path`.
29. **(Phase 6I-25 Codex amendment)** Plan with
    `patch_ready=True` but `planned_payload` missing
    one of the three planned keys → no mutation +
    `ISSUE_PATCH_PLAN_CONTRACT_INVALID` +
    `ACTION_MANUAL_REVIEW_REQUIRED`.
30. **(Phase 6I-25 Codex amendment)**
    `planned_payload_keys` attr lies about the keys →
    no mutation.
31. **(Phase 6I-25 Codex amendment)** `fields_to_add` +
    `fields_to_replace` don't partition
    `PLANNED_PAYLOAD_KEYS` exactly → no mutation.
32. **(Phase 6I-25 Codex amendment)** `fields_to_add`
    and `fields_to_replace` overlap on a shared key →
    no mutation.
33. **(Phase 6I-25 Codex amendment)** `fields_to_add`
    contains an unknown key outside
    `PLANNED_PAYLOAD_KEYS` → no mutation.
34. **(Phase 6I-25 Codex amendment)**
    `per_window_k_metrics` has only 59 canonical cells
    → writer's local Phase 6I-20-shape validator
    rejects → no mutation.
35. **(Phase 6I-25 Codex amendment)**
    `build_wide_window_alignment` missing one canonical
    window → writer's local validator rejects → no
    mutation.
36. **(Phase 6I-25 Codex amendment)** Valid happy path
    still writes after the new validator is wired in
    (regression guard).
37. **(Phase 6I-25 Codex amendment)** Reflective:
    `ISSUE_PATCH_PLAN_CONTRACT_INVALID` is in
    `ALL_ISSUE_CODES`.

All tests use `tmp_path` fixtures + `monkeypatch` env
state + injected fake `_FakePlan` returns through the
`patch_planner_callable` seam. No real planner, no real
artifact, no production roots, no `yfinance` / live
engine / production-write touch.

---

## 10. Validation

```
py_compile: clean on multiwindow_k_confluence_patch_writer.py +
            test_multiwindow_k_confluence_patch_writer.py.

pytest test_scripts/test_multiwindow_k_confluence_patch_writer.py -q:
  37 passed in 0.77 s

Focused 6-way (writer + Phase 6I-24 planner + Phase 6I-23 builder +
              Phase 6I-22 adapter + Phase 6I-21 core + Phase 6I-20 gap audit):
  180 passed in 1.65 s
  ├── multiwindow_k_confluence_patch_writer    37 passed
  ├── multiwindow_k_confluence_patch_planner   30 passed
  ├── multiwindow_k_engine_payload_builder     29 passed
  ├── multiwindow_k_input_adapter              23 passed
  ├── multiwindow_k_engine_core                38 passed
  └── multiwindow_k_engine_gap_audit           23 passed

git diff --check: clean.
```

---

## 11. What this phase does NOT do

- Does **NOT** run this writer against production roots.
- Does **NOT** set `PRJCT9_AUTOMATION_WRITE_AUTH` outside
  test fixtures.
- Does **NOT** invoke source refresh, `yfinance`,
  pipeline runner, StackBuilder, OnePass, ImpactSearch,
  TrafficFlow, Spymaster, or Confluence batch execution.
- Does **NOT** perform any production data write.
- Does **NOT** flip the Phase 6I-20 gap audit to True
  against production tickers.
- Does **NOT** close `real_confluence_pipeline_runner_write`
  / `real_post_pipeline_validation_on_writer_path` /
  writer-surface provider telemetry.

---

## 12. Next phase

**The next phase should be a supervised evidence run
only if Codex approves the exact one-shot command.**

The one-shot would:

1. Pre-snapshot the target ticker's Confluence artifact
   (mtime + SHA-256).
2. Invoke this writer with `--write` AND the env var on
   a single production ticker (initial candidate: SPY),
   following the Phase 6I-11 supervised-run pattern
   (temp-launcher script, pinned interpreter, two-key
   auth, deleted before commit).
3. Re-run the Phase 6I-20 gap audit and confirm
   `has_true_multiwindow_k_engine_outputs=True` for that
   ticker.
4. Capture pre / post SHA-256, surgical diff of the
   artifact (top-level keys added / replaced; unrelated
   fields unchanged), and the execution log row.
5. Submit as a docs-only PR with the supervised-run
   evidence; do NOT extend to other tickers without
   Codex sign-off.

That phase is separate; this PR ships the writer code,
tests, and doc only.

---

## 13. Reference paths

- Module: `project/multiwindow_k_confluence_patch_writer.py`.
- Tests: `project/test_scripts/test_multiwindow_k_confluence_patch_writer.py`.
- Phase 6I-24 patch planner (this writer's upstream):
  `project/multiwindow_k_confluence_patch_planner.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I24_MULTIWINDOW_K_CONFLUENCE_PATCH_PLANNER.md`.
- Phase 6I-23 payload builder:
  `project/multiwindow_k_engine_payload_builder.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I23_MULTIWINDOW_K_ENGINE_PAYLOAD_BUILDER.md`.
- Phase 6I-22 input adapter:
  `project/multiwindow_k_input_adapter.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I22_MULTIWINDOW_K_INPUT_ADAPTER.md`.
- Phase 6I-21 engine core:
  `project/multiwindow_k_engine_core.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I21_MULTIWINDOW_K_ENGINE_CORE_EVALUATOR.md`.
- Phase 6I-20 gap audit (the contract this writer's
  output satisfies):
  `project/multiwindow_k_engine_gap_audit.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I20_MULTIWINDOW_K_ENGINE_GAP_CONTRACT.md`.
- Phase 6H-5 writer authorization pattern (reused):
  `project/daily_board_automation_writer.py` +
  `project/md_library/shared/2026-05-12_PHASE_6H5_GUARDED_WRITE_EXECUTOR_FOUNDATION.md`.
- Phase 6I-18 next-probe handoff (operational state):
  `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md`.
- CLAUDE.md sec 6 — current sprint state.
