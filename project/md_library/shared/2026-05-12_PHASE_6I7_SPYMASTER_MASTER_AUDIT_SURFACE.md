# Phase 6I-7 — Spymaster master-audit read-only surface

**Status:** read-only audit panel embedded in Spymaster
+ tests + this doc. No engine execution. No writer
invocation.

**Last updated:** 2026-05-12.

## 0. Scope statement

  - **Read-only only.** No source refresh. No pipeline
    write. No writer invocation.
  - **No yfinance call** from the audit code path.
    Spymaster as a whole already imports yfinance for
    its regression-baseline single-ticker flow; the
    audit surface introduces no new yfinance coupling.
  - **No OnePass / ImpactSearch / StackBuilder /
    TrafficFlow engine execution** from the audit code
    path.
  - **No destructive file operations.**
  - **No buttons that perform writes.** The single
    button on the surface (`master-audit-load-button`)
    runs the Phase 6I-6 execution-queue planner
    read-only and renders the resulting JSON. It does
    not invoke the writer.
  - **`daily_board_automation_writer` is NOT
    imported** by Spymaster (existing static state)
    or by the new helper module. Confirmed by two
    independent guards (a `spymaster_master_audit`
    AST scan and a `spymaster.py` text scan).
  - The Phase 6H-5 writer remains the only path that
    performs a refresh / pipeline write, and it
    still gates on its two-key authorization
    (`--write` CLI flag + the
    `PRJCT9_AUTOMATION_WRITE_AUTH` env var).

## 1. Why this exists

The Phase 6I-2 → 6I-6 chain shipped a complete
read-only planning / audit backend. Spymaster has been
the operator's primary research surface for years.
Wiring Spymaster as the **master audit surface** for
the new automation stack — without giving Spymaster
any execution power — replaces the old manual
"delete PKLs / paste batch / inspect K table" control
surface with an audit-first view that reads from the
existing read-only stack.

Spymaster gains visibility; the automation stack keeps
its two-key authorization moat.

## 2. What landed

### 2.1 New helper module: `project/spymaster_master_audit.py`

  - Stable layout-section helper:
    `build_audit_layout_section() -> html.Div`. Returns
    a collapsed-by-default `html.Details` carrying the
    surface.
  - Defensive planner invocation:
    `load_audit_report(*, tickers=None,
    from_stackbuilder_universe=True, max_refresh=10,
    max_pipeline=10, include_blocked=True, top_n=5,
    ...) -> tuple[report, error]`. Lazily imports the
    Phase 6I-6 `daily_board_execution_queue_planner`
    inside a `try/except` so a planner-import failure
    becomes a structured error rather than a Spymaster
    crash.
  - Panel renderer:
    `render_audit_panel(report, error) -> html.Div`.
    Builds three subpanels — counts, ranking tails,
    advisory commands — from a successful report, or a
    degraded "unavailable" panel from the error.
  - Stable ID constants:
    - `MASTER_AUDIT_SECTION_ID = "section-master-audit"`
    - `MASTER_AUDIT_DETAILS_ID = "master-audit-details"`
    - `MASTER_AUDIT_SUMMARY_ID = "master-audit-summary"`
    - `MASTER_AUDIT_LOAD_BUTTON_ID = "master-audit-load-button"`
    - `MASTER_AUDIT_STATUS_ID = "master-audit-status"`
    - `MASTER_AUDIT_PANEL_ID = "master-audit-panel"`
    - `MASTER_AUDIT_COUNTS_ID = "master-audit-counts"`
    - `MASTER_AUDIT_TAILS_ID = "master-audit-tails"`
    - `MASTER_AUDIT_ADVISORY_ID = "master-audit-advisory-commands"`
  - Surface copy constants:
    `READ_ONLY_NOTICE_TEXT`, `AUDIT_UNAVAILABLE_TEXT`.

The helper module imports **only** `dash.html` and
`typing`. Forbidden-imports static guard
(`test_helper_has_no_forbidden_imports`) blocks any
future import whose top-level package matches:
`daily_board_automation_writer`,
`signal_engine_cache_refresher`,
`confluence_pipeline_runner`,
`daily_board_automation_executor`, `yfinance`,
`spymaster` (circular), `trafficflow`,
`stackbuilder`, `onepass`, `impactsearch`,
`confluence`, `cross_ticker_confluence`,
`subprocess`. The Phase 6I-6 planner is imported
**lazily inside the `load_audit_report` function
body** so the helper's module-load cost stays zero.

### 2.2 Spymaster wiring: `project/spymaster.py`

Three small additions (the surrounding 14k lines are
unchanged):

  - **Defensive import** of the helper module near the
    other top-level imports. Wrapped in
    `try / except`; on failure, sets
    `_spymaster_master_audit = None` and records the
    error in `_MASTER_AUDIT_IMPORT_ERROR`. Spymaster
    boots in either case.
  - **Layout insertion** between the notification
    container and the footer Hr. The helper's
    `build_audit_layout_section()` is invoked when the
    helper is available; otherwise a static
    "unavailable" notice is rendered.
  - **Load callback** registered only when the helper
    is available. The callback wires
    `master-audit-load-button` → `load_audit_report`
    + `render_audit_panel` → `master-audit-panel` /
    `master-audit-status`. The callback's full body is
    wrapped in `try / except` so any planner failure
    surfaces as a visible unavailable state rather
    than a Dash callback crash.

The static text guard
(`test_spymaster_audit_path_does_not_import_writer`)
scans `spymaster.py` for `daily_board_automation_writer`,
`signal_engine_cache_refresher`,
`confluence_pipeline_runner`, and
`daily_board_automation_executor`; none of them
appear. The static text guard
(`test_spymaster_audit_path_introduces_no_write_button`)
blocks any future audit-path button id matching
`master-audit-write-button`, `master-audit-refresh-button`,
`master-audit-pipeline-button`, or `audit-write-button`.

### 2.3 Tests: `project/test_scripts/test_spymaster_master_audit_surface.py`

13 tests covering:

  1. Forbidden-imports static guard on the helper
     module.
  2. Spymaster.py text guard: no writer / refresher /
     pipeline-runner reference in the audit code path.
  3. Spymaster.py text guard: helper layout wiring +
     button ID reference present.
  4. Spymaster.py text guard: no write-button ID
     introduced in the audit path.
  5. Layout section has every required stable ID.
  6. `html.Details` defaults to collapsed (`open=False`)
     so the audit doesn't auto-run on Spymaster boot.
  7. `render_audit_panel` consumes a fake
     `ExecutionQueueReport` shape and emits the three
     subpanels (counts / tails / advisory) with the
     expected IDs.
  8. Count values from the fake report appear in the
     rendered text.
  9. Advisory commands render as `html.Pre` (display
     only). No `html.Button` exists inside the
     advisory subpanel. The exact writer command
     string is present in the rendered text.
  10. Graceful failure: `render_audit_panel(None,
      "planner_import_failed: ...")` renders the
      unavailable state; the three subpanels are NOT
      present.
  11. Defensive edge: `render_audit_panel(None, None)`
      still emits a visible unavailable state.
  12. The read-only notice copy mentions the four
      required contract points (read-only / advisory
      commands not executed; writer two-key auth;
      StackBuilder no age expiration; both top and
      bottom tails matter).
  13. `load_audit_report` exposes a callable that
      returns a 2-tuple `(report, error)`.

The fake `_FakeReport` dataclass in the test file
avoids requiring a Spymaster boot or a full Phase
6I-6 fixture stack for unit tests of the render
helper.

### 2.4 Doc: this file.

## 3. Surface behavior

### 3.1 Layout

The surface is wrapped in `html.Details(open=False)`.
The summary line reads "Daily Board Automation Audit
(read-only)". On expand, the operator sees:

  - The read-only notice copy (`READ_ONLY_NOTICE_TEXT`).
  - A "Load audit" button (`master-audit-load-button`).
  - A status span (`master-audit-status`) starting at
    `"Idle."`.
  - An empty panel container (`master-audit-panel`).

### 3.2 Click → planner

Clicking the load button fires the Phase 6I-7
callback, which:

  1. Calls `load_audit_report(...)` with the canned
     production defaults (`from_stackbuilder_universe=True,
     max_refresh=10, max_pipeline=10,
     include_blocked=True, top_n=5`).
  2. Calls `render_audit_panel(report, error)` to
     produce the panel body.
  3. Updates the panel container + the status span.

Any exception in this path is caught and surfaced as
an unavailable-state panel. The Dash callback never
crashes.

### 3.3 Rendered subpanels

**Counts** (`master-audit-counts`): per-queue sizes +
the operator-relevant aggregates
(`discovered_stackbuilder_ticker_count`,
`inspected_count`, `selected_refresh_count`,
`selected_pipeline_count`, and the seven queue
counts).

**Ranking tails** (`master-audit-tails`): one-line
ticker lists per tail (`positive_tail`,
`negative_tail`, `low_buy_tail`). Inline copy
reminds the operator that **both top and bottom
tails are meaningful** (the QQQ-vs-SQQQ inverse-
confirmation pattern).

**Advisory commands** (`master-audit-advisory-commands`):
the writer commands the Phase 6I-6 planner attached
to the two write-ready queues, rendered as
`html.Pre` text only. Inline copy reminds the
operator that these strings are **display-only**;
the Phase 6H-5 writer still requires the two-key
auth gate.

### 3.4 Graceful degradation

If the helper module fails to import, Spymaster
boots anyway. The surface renders a static
"Master audit unavailable: helper module import
failed. Spymaster continues to function." notice
in place of the live audit panel.

If the helper imports but the planner stack fails
(planner module import error, raised exception
inside the planner, etc.), the callback returns
the `render_audit_panel(None, error)` unavailable
state. The status span reports "Audit unavailable
(see panel)." The Dash callback graph remains
healthy.

## 4. Read-only contract (carried forward)

  - **No writer invocation.** The Phase 6H-5 writer is
    never imported by Spymaster, by the helper, or
    along the audit code path.
  - **No refresh / pipeline runner.** Phase 6E-5
    refresher and Phase 6D-4 pipeline runner are not
    imported.
  - **No subprocess** in the audit code path.
  - **No file mutation.** The helper renders Dash
    components from in-memory data. The planner it
    consumes is itself read-only (Phase 6I-6 contract
    carried forward).
  - **No new yfinance / dash live-engine import** in
    the audit code path.
  - **No write buttons / write callbacks.** The single
    button (`master-audit-load-button`) triggers the
    read-only load callback only.
  - **StackBuilder durability carried forward
    verbatim.** The notice copy says "Saved
    StackBuilder variants are durable inputs and do
    NOT expire by age." The planner stack's no-age-
    window contract continues to enforce this at the
    audit layer.
  - **Both ranking tails matter.** The notice copy
    explicitly says "Both the positive (Buy-leaning)
    and the bottom (Short / low-buy / inverse
    confirmation) ranking tails are meaningful; this
    surface exposes both."

## 5. Validation captured at module land

  - `py_compile` clean on both new / modified files
    (`spymaster.py`, `spymaster_master_audit.py`).
  - `test_spymaster_master_audit_surface.py`:
    13 passed in 0.09 s.
  - Focused 4-way (audit surface + execution queue +
    universe + upstream audit): 80 passed in 8.01 s.
  - Existing Spymaster tests still pass: 43 passed in
    3.90 s across
    `test_spymaster_help_matrix_ref_removed.py`,
    `test_spymaster_multi_primary_contract.py`,
    `test_spymaster_optimization_extraction.py`,
    `test_spymaster_validation_integration.py`.
  - `git diff --check` clean (LF→CRLF normalization
    warnings only; identical to every other repo
    pattern).

Read-only Spymaster boot smoke (in-process import;
no `app.run_server`, no engine, no writer, no
refresh, no yfinance call):

```
$ python -c "import spymaster; ..."

  section-master-audit:        FOUND
  master-audit-details:        FOUND
  master-audit-load-button:    FOUND
  master-audit-panel:          FOUND
  master-audit-status:         FOUND
  master-audit-summary:        FOUND
Spymaster boot smoke: OK
(no engine/writer/refresh executed)
```

The six stable section IDs are present in the live
Spymaster layout container.

Real-cache helper smoke (planner invoked read-only on
production roots; JSON only; no writes):

```
$ python -c "import spymaster_master_audit as sma; \
  report, err = sma.load_audit_report(tickers=['SPY'], \
  from_stackbuilder_universe=False); ..."

  error:                  None
  inspected_count:        1
  discovered_count:       248
```

## 6. Confirmation no production writes were run

Four independent checks:

1. **Forbidden-imports static guard on the helper.**
   Helper's AST imports only `dash.html` + `typing`.
   The Phase 6I-6 planner is imported **lazily
   inside the function body**, not at module load,
   so the static AST scan can pin the helper as
   import-clean against everything except the dash
   layout dependency.
2. **Spymaster.py text scan.** No new writer /
   refresher / pipeline-runner references introduced
   along the audit code path.
3. **No write buttons.** Static text scan blocks any
   future audit-path button id matching the write
   patterns.
4. **Real-cache helper smoke produces JSON only.**
   No file write; no engine execution; no yfinance
   fetch (the planner stack itself was confirmed
   write-clean across Phase 6I-1 / 6I-3 / 6I-4 /
   6I-5 / 6I-6).

## 7. Future work (named, not implemented here)

  - **Phase 6I-8** — supervised first authorized
    production run. Operational: Phase 5G data-
    licensing pre-launch gate + Phase 5C validation
    integration + scheduler / alerting wired
    through the Phase 6H-7 runbook + the Phase 6H-5
    writer's two-key gate.
  - **Phase 6I-9 (or similar)** — Spymaster
    interactive features on top of the audit panel:
    ticker filtering, queue-specific download
    buttons (still no writer invocation), JSONL
    export of the rendered report.
  - **Aggregate Confluence p-value** — explicit
    future gap from Phase 6I-2 § 4.3 / § 6.2.

## 8. Reference paths

### New / modified files (this PR)

  - `project/spymaster_master_audit.py` (new helper).
  - `project/spymaster.py` (3 small additions:
    defensive import, layout insertion, load
    callback).
  - `project/test_scripts/test_spymaster_master_audit_surface.py`
    (13 tests).
  - `project/md_library/shared/2026-05-12_PHASE_6I7_SPYMASTER_MASTER_AUDIT_SURFACE.md`
    (this doc).

### Modules consumed (read-only)

  - `daily_board_execution_queue_planner` (Phase
    6I-6) — the planner the helper consumes; itself
    consumes Phase 6I-5 / 6I-4 / 6I-3 / 6I-1.
  - `dash` (`html` only).

### Cross-references

  - Phase 6I-6 execution queue planner:
    `project/md_library/shared/2026-05-12_PHASE_6I6_DAILY_BOARD_EXECUTION_QUEUE_PLANNER.md`.
  - Phase 6H-7 production runbook + Phase 6H-5
    writer:
    `project/md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`
    (the operator stack the audit surface is meant
    to **reflect**, not bypass).
  - Phase 6I-2 migration map (§ 5 → § 7):
    `project/md_library/shared/2026-05-12_PHASE_6I2_MANUAL_WORKFLOW_MIGRATION_MAP.md`
    (the "what is already automated today" inventory
    plus the named gaps Phase 6I-3 → 6I-7 close).
