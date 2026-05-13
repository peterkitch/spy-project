# Phase 6I-23: In-memory multi-window K engine payload builder

Sprint date: **2026-05-13**.
Branch: `phase-6i-23-multiwindow-k-engine-payload-builder`.
Module: `project/multiwindow_k_engine_payload_builder.py`.
Tests: `project/test_scripts/test_multiwindow_k_engine_payload_builder.py`.
Doc: this file.

This phase ships the **first in-memory assembly of the
future Confluence payload shape**. It wires the Phase 6I-22
read-only adapter to the Phase 6I-21 read-only core
evaluator and produces the structured object a later phase
will write to the on-disk Confluence artifact under
`per_window_k_metrics` and `build_wide_window_alignment`.

It still does **NOT** write any artifact. After this phase
the Phase 6I-20 gap audit's
`has_true_multiwindow_k_engine_outputs` will still return
False against every production ticker.

---

## 0. Scope

A read-only Python module that, for one target ticker:

1. Calls the Phase 6I-22 adapter
   (`multiwindow_k_input_adapter.prepare_multiwindow_k_inputs`,
   or an injected stand-in via the `adapter_callable`
   seam) in its default strict-coverage mode. The builder
   **never** forwards `allow_partial_members`.
2. Inspects `adapter_report.can_evaluate_full_60_cell_grid`.
   When **False**, the builder returns
   `payload_ready=False` with `per_window_k_metrics=[]`,
   `build_wide_window_alignment={}`, and emits
   `ISSUE_ADAPTER_NOT_READY`. **The core grid is NOT called
   on this path.**
3. When **True**, calls Phase 6I-21
   `multiwindow_k_engine_core.evaluate_k_window_grid(target_ticker,
   per_cell_inputs=adapter_report.per_cell_inputs)` (or an
   injected stand-in via the `core_grid_callable` seam).
4. Converts the resulting cells to
   `per_window_k_metrics` via Phase 6I-21's
   `cells_to_per_window_k_metrics_payload` helper.
5. Builds `build_wide_window_alignment` by counting, for
   every canonical window, how many of the build's K rows
   have a firing combined signal (`Buy` or `Short`) at the
   latest bar of that window. Every canonical window gets
   an entry; the Phase 6I-20 audit's
   `_build_wide_alignment_is_valid` rejects mappings
   missing any canonical window.
6. Returns a structured `MultiWindowKEnginePayloadReport`
   with `payload_ready=True`,
   `per_window_k_metrics` (60 entries),
   `build_wide_window_alignment` (5 entries), and a compact
   `AdapterSummary` of the adapter's run state.

---

## 1. What this module IS

The first **in-memory** assembly of the Phase 6I-20-defined
future Confluence payload shape. Inputs come from the
adapter (which reads disk); outputs are in-memory
dataclasses + dicts ready for a future writer phase to
persist verbatim.

**Strict member coverage propagates.** The builder gates
on `adapter_report.can_evaluate_full_60_cell_grid`. That
boolean is True only when:

- every canonical `(K, window)` cell is prepared by the
  adapter, AND
- every prepared cell carries its FULL K-row member set
  (`len(members_prepared) == len(members_attempted)`).

`allow_partial_members=True` cells, even when they
structurally fill 60 slots, never qualify (the Phase 6I-22
amendment hardened the verdict; see
`2026-05-13_PHASE_6I22_MULTIWINDOW_K_INPUT_ADAPTER.md`
§1.1). The Phase 6I-23 builder does NOT forward
`allow_partial_members` at all — pinned by a test that
asserts the builder's adapter-callable kwargs never
contain that key.

---

## 2. What this module IS NOT

- **NOT a persistence layer.** The builder does NOT write
  its output to disk. The Phase 6I-20 gap audit's
  `has_true_multiwindow_k_engine_outputs` will still return
  False against every production ticker until a later
  phase wires this builder into a path that updates the
  on-disk Confluence artifact.
- **NOT a fabricator.** `payload_ready=False` always
  means `per_window_k_metrics=[]` AND
  `build_wide_window_alignment={}`. No near-miss schema.
- **NOT a writer / refresher / pipeline runner.** No
  `--write` invocation, no source refresh, no `yfinance`
  fetch, no `PRJCT9_AUTOMATION_WRITE_AUTH`, no subprocess,
  no StackBuilder / OnePass / ImpactSearch / TrafficFlow /
  Spymaster batch execution.
- **NOT a partial-mode path.** The builder never forwards
  `allow_partial_members`; the strict
  `can_evaluate_full_60_cell_grid` gate is the only path
  to `payload_ready=True`.

---

## 3. Operational-state caveats carried forward

This phase **closes** the integration gap between the
Phase 6I-22 adapter and the Phase 6I-21 core: a single
public function turns "I have a StackBuilder K build's
saved interval libraries" into "I have the
Phase 6I-20-shaped payload in memory."

This phase **does NOT close**:

- `real_confluence_pipeline_runner_write` — still open
  (closes on a future supervised run that writes the
  payload to the Confluence artifact);
- `real_post_pipeline_validation_on_writer_path` — still
  open (same future condition);
- writer-surface provider telemetry — still pending;
- the production true-engine artifact write — still
  pending.

Operational state remains **STATE C / WAIT** (cache
`2026-05-12` == cutoff `2026-05-12`). The Phase 6H-5
two-key writer gate, the Phase 6I-9 supervised gate, the
Phase 6I-10 production-root snapshot strategy, the
Phase 6I-12 ProviderFetchTelemetry four-surface contract,
the Phase 6I-15 source-availability advisory contract,
the Phase 6I-20 gap audit, the Phase 6I-21 engine core,
and the Phase 6I-22 input adapter are all unchanged.

---

## 4. Public API

```python
from multiwindow_k_engine_payload_builder import (
    build_multiwindow_k_engine_payload,
    MultiWindowKEnginePayloadReport,
    AdapterSummary,
    CANONICAL_WINDOWS,
    CANONICAL_K_VALUES,
    ISSUE_ADAPTER_NOT_READY,
    ISSUE_CORE_GRID_FAILED,
    ISSUE_NO_CELLS_EVALUATED,
    ISSUE_CORE_GRID_INCOMPLETE,         # Phase 6I-23 Codex amendment
)

report = build_multiwindow_k_engine_payload(
    "SPY",
    stackbuilder_root=None,        # default: output/stackbuilder
    signal_library_dir=None,       # default: signal_library/data/stable
    K_values=CANONICAL_K_VALUES,   # default
    windows=CANONICAL_WINDOWS,     # default
    run_dir=None,                  # optional StackBuilder seed-run override
    adapter_callable=None,         # injection seam (tests)
    core_grid_callable=None,       # injection seam (tests)
)

if report.payload_ready:
    # A later phase will persist these two fields onto the
    # on-disk Confluence artifact under their respective
    # top-level keys.
    per_window_k_metrics = report.per_window_k_metrics
    build_wide_window_alignment = (
        report.build_wide_window_alignment
    )
else:
    # Inspect report.adapter_summary +
    # report.issue_codes to see why.
    ...
```

`MultiWindowKEnginePayloadReport` fields:

| Field | Meaning |
|---|---|
| `generated_at` | ISO timestamp. |
| `target_ticker` | Upper-cased. |
| `payload_ready` | True iff every gate passed. |
| `K_values` / `windows` | Tuples passed to the adapter. |
| `cell_count` | Number of cells returned by the core (0 when not ready). |
| `per_window_k_metrics` | List of 60 dicts when ready; `[]` when not. |
| `build_wide_window_alignment` | Mapping of 5 canonical windows → `{all_members_firing, firing_member_count, total_member_count}` when ready; `{}` when not. |
| `adapter_summary` | `AdapterSummary` with run-dir / cell counts / skipped cells / adapter issue codes. |
| `issue_codes` | Stable tuple of `ISSUE_*` strings raised by this builder. |
| `remaining_limitations` | Operational-state caveats (Phase 6I-23 doc-shape). |

### 4.1 Build-wide window alignment semantics (Phase 6I-23 Codex amendment)

For each canonical window the alignment entry reports
**member-slot** counts aggregated across the build's
canonical K rows — the field names match what they count
(no K-row aliasing).

- `total_member_count` = `sum(cell.member_count for
  canonical K cells in this window)`. With
  `K_values=CANONICAL_K_VALUES` and each cell carrying
  its own K-sized member set, this sums to
  `1 + 2 + ... + 12 = 78` per window.
- `firing_member_count` = sum of members firing in the
  cell's aligned direction. For a cell with
  `latest_combined_signal == "Buy"`, the aligned
  contribution is `cell.latest_buy_count`; for `"Short"`
  it is `cell.latest_short_count`; `"None"` (or any
  other / empty signal) contributes `0`.
- `all_members_firing` = `True` only when every canonical
  K cell in this window exists AND has
  `aligned == cell.member_count` (i.e. every member of
  that K row is firing in the aligned direction at the
  latest bar) AND `total_member_count > 0`.

This matches the operator-facing question
"is every ticker in the build firing across every
available window?" — the Phase 6I-21 core's per-cell
K-thresholded combine rule allows a Buy combined signal
even when some active members are None (e.g.
`[Buy, Buy, None]` at K=1 fires Buy with `latest_buy_count=2`
and `member_count=3`). The Phase 6I-23 builder's
`all_members_firing` correctly flips to `False` for that
window because not every member of that K row is firing
in the aligned direction.

### 4.2 Stable issue codes

`ALL_ISSUE_CODES`:

- `adapter_not_ready` — adapter reported
  `can_evaluate_full_60_cell_grid=False`. Core not called.
- `core_grid_failed` — adapter said ready but the core
  raised. Caught defensively.
- `no_cells_evaluated` — adapter said ready and the core
  returned but produced zero cells (logically impossible
  on real data; defended against synthetic fakes).
- `core_grid_incomplete` **(Phase 6I-23 Codex amendment)**
  — adapter said ready and the core returned cells, but
  the cells do NOT cover the canonical 60-cell grid
  exactly (missing canonical pair, duplicate
  `(K, window)`, or noncanonical-only substitute for a
  missing canonical cell). `payload_ready=False`,
  `per_window_k_metrics=[]`, `build_wide_window_alignment={}`.
  The contract validator
  `_core_cells_cover_full_canonical_60` runs after the
  empty-result check.

### 4.3 Core-grid completeness validation (Phase 6I-23 Codex amendment)

Before `payload_ready=True`, the builder validates that
the core grid result:

- contains no duplicate `(K, window)` pair (a real engine
  emits one cell per pair; duplicates indicate a stub /
  bug);
- covers the full canonical 60-cell grid (every pair
  where `K ∈ {1..12}` AND `window ∈ {1d, 1wk, 1mo, 3mo, 1y}`);
- noncanonical extras (e.g. `(K=13, "2d")`) are tolerated
  but never substitute for any missing canonical cell.

If validation fails, `payload_ready=False` and the new
`core_grid_incomplete` issue code fires. This guarantees
that `payload_ready=True` always means the payload itself
satisfies the Phase 6I-20 future-artifact contract — a
contract bug Codex caught: previously the builder
rejected only the empty-result case, so a non-empty but
incomplete core result could pass through as `payload_ready=True`.

---

## 5. CLI

```
python multiwindow_k_engine_payload_builder.py --ticker SPY
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
- **No `trafficflow_multitimeframe_bridge` import** (the
  projection / bridge module).
- **No `trafficflow_k_artifact_builder` import** (the
  builder consumes the adapter, not the StackBuilder
  helpers directly — those live one layer down inside
  the adapter).
- **No `multiwindow_k_engine_gap_audit` import** (the gap
  audit is a downstream consumer of this builder's output
  shape — the test suite exercises the audit's validators
  to prove integration but the production code path does
  not).
- **No projection logic**: no call to `.resample()` or
  `.ffill()` anywhere in code (AST-verified).
- **No raw `pickle.load`** (AST-verified; the central
  provenance loader for signal-library files lives
  downstream inside the Phase 6I-22 adapter and is the
  only sanctioned path).

Allowed imports: the Phase 6I-21 core (read-only by
contract), the Phase 6I-22 adapter (read-only by
contract). Both are themselves gated by repo-wide static
guards (forbidden-imports + B12 raw-pickle ban).

---

## 7. Tests (29 pinned contracts)

`project/test_scripts/test_multiwindow_k_engine_payload_builder.py`:

1. Forbidden-imports static guard.
2. **No projection**: no `.resample()` / `.ffill()` call
   anywhere (AST `ast.Call` walk).
3. **No raw `pickle.load`** (AST `ast.Call` walk).
4. **Adapter-not-ready short-circuits**: fake adapter
   with `can_evaluate_full_60_cell_grid=False` →
   `payload_ready=False`; the spy core records ZERO
   invocations (the builder must not call the core when
   the adapter is not ready); `per_window_k_metrics=[]`;
   `build_wide_window_alignment={}`;
   `ISSUE_ADAPTER_NOT_READY` raised; adapter's own issue
   codes embedded in `adapter_summary`.
5. **Partial-member adapter report keeps `payload_ready=False`**:
   a fake adapter with 60 structural cells in
   `per_cell_inputs` but
   `can_evaluate_full_60_cell_grid=False` (simulating
   Phase 6I-22 partial mode) STILL yields
   `payload_ready=False`. The builder's gate is the
   boolean, not the structural count.
6. **Full canonical inputs emit 60 per_window_k_metrics**:
   fake adapter returning 60 real `per_cell_inputs` →
   builder calls Phase 6I-21 core → returns 60
   `PerWindowKCell`s → emits 60 `per_window_k_metrics`
   entries covering every canonical `(K, window)` pair.
7. **`build_wide_window_alignment` has 5 entries**
   matching `CANONICAL_WINDOWS` exactly.
8. **Phase 6I-20 `per_window_k_metrics` validator
   accepts** the builder output:
   `gap_audit._per_window_k_metrics_are_valid(report.per_window_k_metrics)` returns True.
9. **Phase 6I-20 `build_wide_window_alignment` validator
   accepts** the builder output:
   `gap_audit._build_wide_alignment_is_valid(report.build_wide_window_alignment)` returns True.
10. Per-entry field types: `all_members_firing` is `bool`;
    `firing_member_count` / `total_member_count` are `int`.
11. **(Phase 6I-23 Codex amendment, member-slot
    semantics)** All-firing fixture pins counts: in the
    canonical all-Buy fixture every window reports
    `total_member_count == firing_member_count == 78`
    (= `1 + 2 + ... + 12`, sum of K-sized member sets)
    and `all_members_firing=True`.
12. **(Phase 6I-23 Codex amendment, member-slot
    semantics)** None-signal cell pins counts: replacing
    the `(K=12, window="1d")` cell with a None cell
    (member_count=12) inside a full-canonical 60-cell
    spy-core result reports `total_member_count=78` /
    `firing_member_count=66` (= `78 - 12`) /
    `all_members_firing=False` for `1d`; every other
    window remains fully firing (78/78/True).
13. **(Phase 6I-23 Codex amendment, member-slot
    semantics)** Buy combined signal with partial member
    firing: a K=3 cell with `member_count=3` but
    `latest_buy_count=2` contributes `aligned=2` to
    `firing_member_count` and prevents
    `all_members_firing` for that window (the K-threshold
    combine rule allows Buy when `buy_n >= K` even with
    some None members; the builder correctly reports the
    cell as not fully aligned).
14. **(Phase 6I-23 Codex amendment, completeness
    validation)** Core returns 1 cell → `payload_ready=False`
    + `ISSUE_CORE_GRID_INCOMPLETE`.
15. **(Phase 6I-23 Codex amendment)** Core returns 59
    canonical cells → `payload_ready=False` +
    `ISSUE_CORE_GRID_INCOMPLETE`.
16. **(Phase 6I-23 Codex amendment)** Core returns
    duplicate `(K, window)` cell → `payload_ready=False`
    + `ISSUE_CORE_GRID_INCOMPLETE`.
17. **(Phase 6I-23 Codex amendment)** Core returns 59
    canonical + 1 noncanonical (e.g. `(K=13, "2d")`)
    → `payload_ready=False` + `ISSUE_CORE_GRID_INCOMPLETE`
    (noncanonical extras must not substitute for missing
    canonical cells).
18. **(Phase 6I-23 Codex amendment)** Core returns 60
    canonical + 1 noncanonical extra → `payload_ready=True`
    (extras tolerated on top of a complete canonical
    set).
19. **Core-grid exception** → `payload_ready=False` +
    `ISSUE_CORE_GRID_FAILED`.
20. **Empty core result** → `payload_ready=False` +
    `ISSUE_NO_CELLS_EVALUATED`.
21. **Builder NEVER forwards `allow_partial_members`** to
    the adapter. Pinned by an assertion inside the fake
    adapter wrapper that fires if the kwarg ever appears
    in the adapter call's kwargs.
22-23. JSON round-trip on both happy and not-ready paths.
24-28. CLI: `rc=0` (happy); `rc=2` (missing ticker);
    `rc=2` (unknown flag); `rc=2` (no `SystemExit` leak
    on argparse error); `rc=3` (unhandled exception).
29. Constants re-exported from the core; every
    `ALL_ISSUE_CODES` entry is exposed as a module
    attribute.

All tests use injected fakes / `monkeypatch` only — no
real adapter, no real core, no disk read or write, no
`yfinance` / live engine / production-write touch.

---

## 8. Validation

```
py_compile: clean on multiwindow_k_engine_payload_builder.py +
            test_multiwindow_k_engine_payload_builder.py.

pytest test_scripts/test_multiwindow_k_engine_payload_builder.py -q:
  29 passed in 0.93 s

Focused 4-way (builder + Phase 6I-22 adapter + Phase 6I-21 core +
              Phase 6I-20 gap audit):
  113 passed in 1.17 s
  ├── multiwindow_k_engine_payload_builder  29 passed
  ├── multiwindow_k_input_adapter           23 passed
  ├── multiwindow_k_engine_core             38 passed
  └── multiwindow_k_engine_gap_audit        23 passed

git diff --check: clean.
```

---

## 9. What this phase does NOT do

- Does **NOT** write any on-disk artifact.
- Does **NOT** add `per_window_k_metrics` or
  `build_wide_window_alignment` to the on-disk Confluence
  artifact.
- Does **NOT** flip Phase 6I-20's
  `has_true_multiwindow_k_engine_outputs` to True against
  production tickers.
- Does **NOT** close
  `real_confluence_pipeline_runner_write` /
  `real_post_pipeline_validation_on_writer_path` /
  writer-surface provider telemetry.
- Does **NOT** authorize a writer run.
- Does **NOT** change the Phase 6H-5 two-key writer gate,
  the Phase 6I-9 supervised gate, the Phase 6I-10
  production-root snapshot strategy, the Phase 6I-12
  ProviderFetchTelemetry four-surface contract, the
  Phase 6I-15 source-availability advisory contract, the
  Phase 6I-20 gap audit, the Phase 6I-21 engine core, or
  the Phase 6I-22 input adapter.

---

## 10. Reference paths

- Module: `project/multiwindow_k_engine_payload_builder.py`.
- Tests: `project/test_scripts/test_multiwindow_k_engine_payload_builder.py`.
- Phase 6I-22 adapter (this builder's upstream):
  `project/multiwindow_k_input_adapter.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I22_MULTIWINDOW_K_INPUT_ADAPTER.md`.
- Phase 6I-21 core (this builder's grid evaluator):
  `project/multiwindow_k_engine_core.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I21_MULTIWINDOW_K_ENGINE_CORE_EVALUATOR.md`.
- Phase 6I-20 gap audit (the contract this builder's
  output satisfies):
  `project/multiwindow_k_engine_gap_audit.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I20_MULTIWINDOW_K_ENGINE_GAP_CONTRACT.md`.
- Phase 6I-18 next-probe handoff (operational state):
  `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md`.
- CLAUDE.md sec 6 — current sprint state.
