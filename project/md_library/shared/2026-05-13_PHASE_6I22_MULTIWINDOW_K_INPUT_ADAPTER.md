# Phase 6I-22: Read-only adapter from StackBuilder rows + OnePass interval libraries into multi-window K engine core inputs

Sprint date: **2026-05-13**.
Branch: `phase-6i-22-multiwindow-k-input-adapter`.
Module: `project/multiwindow_k_input_adapter.py`.
Tests: `project/test_scripts/test_multiwindow_k_input_adapter.py`.
Doc: this file.

This phase ships a **read-only adapter** that prepares the
per-`(K, window)` input map the Phase 6I-21
`multiwindow_k_engine_core.evaluate_k_window_grid(...)` function
expects. The previous phase showed the core math works on
in-memory fixtures; this phase shows real StackBuilder K rows
and real saved interval libraries can feed the core.

It does **not** write any artifact. It does **not** flip the
Phase 6I-20 gap audit's
`has_true_multiwindow_k_engine_outputs` to True against
production. It does **not** close the carry-forward evidence
gaps. It is one more layer of read-only plumbing toward the
future Confluence-artifact write phase.

---

## 0. Scope

A read-only Python module that, for one target ticker:

1. Discovers the latest StackBuilder seed-run directory under
   `output/stackbuilder/<TARGET>/` (or honours an explicit
   `run_dir` override).
2. Loads the seed-run's `combo_leaderboard.xlsx` via the
   existing `trafficflow_k_artifact_builder.load_stackbuilder_leaderboard`
   helper.
3. Iterates K rows via the existing
   `trafficflow_k_artifact_builder.iter_k_build_rows` helper so
   **each K row's own `members_str` is carried through**; the
   adapter never collapses K=1..12 into one shared member
   bundle.
4. Parses each K row's members via the existing public
   `research_artifacts.parse_stack_members_with_protocol`
   helper (Direct / Inverse protocols preserved per member).
5. For every `(K, window)` cell where `K` is one of the
   leaderboard's K rows AND `window` is one of the canonical
   windows, attempts to load the per-window signal library
   for the target and for every member. The default loader
   reads `signal_library/data/stable/<TICKER>_stable_v1_0_0[_<interval>].pkl`
   read-only via `pickle.load`; tests inject fakes via the
   `library_loader` seam.
6. When the target's per-window library is present AND carries
   `dates`, a target-`close` series, AND at least one member
   library is present with matching length, the cell is
   prepared and added to the aggregate report's
   `per_cell_inputs` map.
7. Returns a `MultiWindowKInputAdapterReport` carrying the
   per-cell input map AND per-cell diagnostics for every cell
   the adapter could not prepare.

The aggregate report flags `can_evaluate_full_60_cell_grid =
True` only when every canonical `(K, window)` pair (12 × 5 =
60 cells) is in the prepared map.

---

## 1. What this module IS

A thin read-only adapter that turns saved StackBuilder /
signal-library artifacts into the per-cell input map for the
Phase 6I-21 core. Inputs come from disk; outputs are
in-memory dataclasses + dicts. No fabrication: every
unrecoverable input shape is surfaced as a structured
`skipped_cells` entry with a stable reason code.

---

## 2. What this module IS NOT

- **NOT a projection / bridge.** No `pandas.resample()`,
  no `.ffill()`, no `trafficflow_multitimeframe_bridge`
  import. Each window's data is read FROM that window's
  own library; if a window's library is absent, the cell
  is skipped — the adapter never resamples daily signals
  to fake a weekly / monthly / quarterly / yearly cell.
- **NOT a persistence layer.** The adapter does NOT write
  `per_window_k_metrics` to the on-disk Confluence
  artifact. After this phase the Phase 6I-20 gap audit's
  `has_true_multiwindow_k_engine_outputs` will still
  return False against every production ticker.
- **NOT a writer / refresher / pipeline runner.** No
  `--write` invocation. No source refresh. No `yfinance`
  fetch. No `PRJCT9_AUTOMATION_WRITE_AUTH`. No subprocess.
  No StackBuilder / OnePass / ImpactSearch / TrafficFlow /
  Spymaster batch execution.
- **NOT a fabricator.** Missing libraries / missing
  target `close` / unparseable member strings produce
  structured `skipped_cells` entries with stable reason
  codes; they never produce fabricated rows.

---

## 3. Operational-state caveats carried forward

- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still
  open.
- Writer-surface provider telemetry — still pending.
- Production `has_true_multiwindow_k_engine_outputs` —
  still False. Closes only when a later phase wires this
  adapter's output through the core AND writes
  `per_window_k_metrics` + `build_wide_window_alignment`
  to the on-disk Confluence artifact.
- Operational state remains **STATE C / WAIT** (cache
  `2026-05-12` == cutoff `2026-05-12`).

---

## 4. Per-cell diagnostics

`PerCellAdapterState`:

| Field | Meaning |
|---|---|
| `K`, `window` | Cell key. |
| `prepared` | True iff the cell's input row was added to `per_cell_inputs`. |
| `target_library_present` | True iff the target's per-window library was loadable. |
| `members_attempted` | `(ticker, protocol)` pairs parsed from the K row's `members_str`. |
| `members_prepared` | Member tickers whose per-window library produced a usable signal column. |
| `members_missing` | Member tickers whose per-window library was absent / empty / length-mismatched. |
| `skipped_reason` | Stable reason code from `ALL_SKIPPED_REASON_CODES`, or `None` if prepared. |

`MultiWindowKInputAdapterReport`:

| Field | Meaning |
|---|---|
| `target_ticker` | Upper-cased target. |
| `selected_run_dir` / `selected_run_id` | The seed-run directory used. |
| `K_values`, `windows` | The supplied iteration tuples. |
| `attempted_cell_count` | `len(K_values) * len(windows)`. |
| `prepared_cell_count` | Cells in `per_cell_inputs`. |
| `missing_cell_count` | `attempted - prepared`. |
| `can_evaluate_full_60_cell_grid` | True iff every canonical `(K, window)` pair is prepared. |
| `per_cell_inputs` | The load-bearing output: feed directly into `evaluate_k_window_grid`. |
| `per_cell_states` | Per-cell diagnostic tuples. |
| `missing_libraries_by_ticker_window` | `{TICKER: [missing windows]}` for every absent library encountered. |
| `unparseable_member_strings` | `(K, raw_members_str)` pairs that failed to parse. |
| `skipped_cells` | `(K, window, reason)` triples. |
| `issue_codes` | Aggregate stable issue codes raised across all cells. |

### 4.1 Stable skipped-cell reason codes

`ALL_SKIPPED_REASON_CODES`:

- `no_stackbuilder_run`
- `leaderboard_load_failed`
- `no_k_row_in_leaderboard`
- `unparseable_members`
- `missing_target_library`
- `target_library_load_failed`
- `missing_target_close`
- `empty_library`
- `no_members_available`

### 4.2 Stable aggregate issue codes

`ALL_ISSUE_CODES`:

- `no_stackbuilder_run_for_target`
- `leaderboard_load_failed`
- `no_k_rows_in_leaderboard`
- `unparseable_members`
- `missing_target_library`
- `missing_target_close`
- `missing_member_library`
- `empty_library`

---

## 5. Public API

```python
from multiwindow_k_input_adapter import (
    prepare_multiwindow_k_inputs,
    PerCellAdapterState,
    MultiWindowKInputAdapterReport,
    CANONICAL_WINDOWS,
    CANONICAL_K_VALUES,
    ALL_SKIPPED_REASON_CODES,
    ALL_ISSUE_CODES,
)
import multiwindow_k_engine_core as core

report = prepare_multiwindow_k_inputs(
    "SPY",
    stackbuilder_root=None,         # default: output/stackbuilder
    signal_library_dir=None,        # default: signal_library/data/stable
    K_values=CANONICAL_K_VALUES,    # default
    windows=CANONICAL_WINDOWS,      # default
    run_dir=None,                   # optional override
    library_loader=None,            # injection seam (tests)
    stackbuilder_run_discovery_callable=None,  # injection seam
    leaderboard_loader_callable=None,          # injection seam
    k_rows_iter_callable=None,                 # injection seam
)

if report.can_evaluate_full_60_cell_grid:
    cells = core.evaluate_k_window_grid(
        target_ticker=report.target_ticker,
        per_cell_inputs=report.per_cell_inputs,
    )
    # cells: tuple of 60 PerWindowKCell.
else:
    # Inspect report.skipped_cells /
    # report.missing_libraries_by_ticker_window /
    # report.unparseable_member_strings to see what is
    # missing.
    ...
```

The injection seams exist so tests run entirely off
`tmp_path` fixtures + fake loaders; production callers just
pass the defaults.

---

## 6. The target-`close` limitation

The current production signal-library shape (e.g.
`SPY_stable_v1_0_0_1wk.pkl`) carries `dates` and `signals`
reliably but **does not always carry a `close` series**.
When the adapter cannot find a `close` / `target_close` /
`Close` field on the target's per-window library, it
records the cell as skipped with reason
`missing_target_close` and adds `missing_target_close` to
`issue_codes`.

**The adapter never fabricates close prices.** Surfacing
this gap is the load-bearing purpose of this Phase 6I-22
module — a later phase either (a) extends the signal-library
builder to carry per-window close, or (b) joins per-window
close from a separate cache source. Phase 6I-22 stops at
the gap.

---

## 7. Strictly read-only

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
- **No projection logic**: no call to `.resample()` or
  `.ffill()` anywhere in code. Pinned by the test suite
  via AST walk over `ast.Call` nodes.
- No production write at any layer.

Allowed imports: the Phase 6I-21 core (read-only by
contract), the `research_artifacts` module (public
`parse_stack_members_with_protocol` only), and the Phase 6F
StackBuilder K artifact builder (`discover_latest_stackbuilder_run`
/ `load_stackbuilder_leaderboard` / `iter_k_build_rows`).

`trafficflow_k_artifact_builder` is allowed because its module
name starts with `trafficflow_` (not `trafficflow`); the
first-segment forbidden-imports check correctly distinguishes
them. The builder helpers used by this adapter are read-only:
`discover_latest_stackbuilder_run` walks directory mtimes,
`load_stackbuilder_leaderboard` reads an Excel file via
`pandas.read_excel`, and `iter_k_build_rows` iterates the
resulting DataFrame.

### 7.1 Note on the `pandas` / `pickle` dependency

The adapter itself imports only `pickle`, `dataclasses`,
`datetime`, `pathlib`, `typing`, and the three project
modules listed above. It does NOT import `pandas` at top
level. However, the upstream
`trafficflow_k_artifact_builder.load_stackbuilder_leaderboard`
loads `combo_leaderboard.xlsx` via `pandas.read_excel`, and
real signal-library `.pkl` files often contain numpy / pandas
objects. The transitive dependency graph is therefore not
pandas-free.

The load-bearing claim is **no-projection / no-resample /
no-ffill / no-live-engine / no-production-write**; the lack
of a direct `pandas` import is an honest restatement that
this module's own code path uses plain Python loops + lists,
not pandas vectorization.

---

## 8. Tests (19 pinned contracts)

`project/test_scripts/test_multiwindow_k_input_adapter.py`:

1. Forbidden-imports static guard.
2. **Not a projection**: no `.resample()` / `.ffill()` call
   anywhere (AST walk).
3-5. Run / leaderboard short-circuits: missing seed run;
   leaderboard load failure; empty K-row iteration — every
   attempted cell skipped with the appropriate reason code.
6. **K rows with different member sets produce different
   per-cell columns** (K=1 has one member, K=2 has two; the
   adapter does NOT collapse them).
7. Direct / Inverse / None protocols flow through to the
   per-cell `member_protocols`.
8. Missing target library for one window skips every K cell
   in that window with reason `missing_target_library`.
9. Missing member library skips only the affected member;
   the cell still prepares if at least one member survives.
   `members_missing` reflects the dropped member; the
   missing-libraries map records the missing
   `(member, window)` pair.
10. When every member of a K row is missing for one window,
    the cell is skipped with reason `no_members_available`.
11. **Missing target `close` does NOT fabricate prices.**
    Cell skipped with reason `missing_target_close`.
12. Unparseable members short-circuit the K row across all
    windows with reason `unparseable_members`.
13. **Full canonical fixture (12 K rows × 5 canonical
    windows × full target + member libraries with close)
    prepares 60 cells; the resulting `per_cell_inputs` feeds
    `evaluate_k_window_grid` directly and produces 60
    `PerWindowKCell` cells with `member_count == K`.**
14. The same payload satisfies the Phase 6I-20 required
    five fields when run through
    `cells_to_per_window_k_metrics_payload`.
15. Empty `dates` skips with reason `empty_library`.
16. Member signal length mismatch drops only the
    misaligned member (no resample / no ffill).
17. Canonical constants re-exported from the core.
18. Every entry in `ALL_SKIPPED_REASON_CODES` is exposed as
    a `REASON_*` module attribute.
19. Every entry in `ALL_ISSUE_CODES` is exposed as an
    `ISSUE_*` module attribute.

All tests use `tmp_path` fixtures + in-memory fake loaders —
no production roots, no yfinance, no writer / pipeline / live
engine.

---

## 9. What this phase does NOT do

- Does **not** build the future TrafficFlow-style multi-window
  K engine end-to-end. This is one read-only plumbing layer
  on the path to it.
- Does **not** write any on-disk artifact. The adapter only
  produces in-memory dataclasses + dicts.
- Does **not** add `per_window_k_metrics` to the Confluence
  artifact.
- Does **not** add `build_wide_window_alignment` to the
  Confluence artifact (that's a separate per-build aggregate
  the future engine must also write).
- Does **not** flip the Phase 6I-20 gap audit to True against
  production tickers.
- Does **not** close `real_confluence_pipeline_runner_write` /
  `real_post_pipeline_validation_on_writer_path` / writer-
  surface provider telemetry.
- Does **not** authorize a writer run.
- Does **not** change the Phase 6H-5 two-key writer gate, the
  Phase 6I-9 supervised gate, the Phase 6I-10 production-root
  snapshot strategy, the Phase 6I-12 ProviderFetchTelemetry
  four-surface contract, or the Phase 6I-15 source-availability
  advisory contract.

---

## 10. Reference paths

- Module: `project/multiwindow_k_input_adapter.py`.
- Tests: `project/test_scripts/test_multiwindow_k_input_adapter.py`.
- Phase 6I-21 core (the consumer):
  `project/multiwindow_k_engine_core.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I21_MULTIWINDOW_K_ENGINE_CORE_EVALUATOR.md`.
- Phase 6I-20 gap audit (the contract this is heading toward
  satisfying for production):
  `project/multiwindow_k_engine_gap_audit.py` +
  `project/md_library/shared/2026-05-13_PHASE_6I20_MULTIWINDOW_K_ENGINE_GAP_CONTRACT.md`.
- StackBuilder K-row helpers (read-only upstream):
  `project/trafficflow_k_artifact_builder.py`
  (`discover_latest_stackbuilder_run`,
  `load_stackbuilder_leaderboard`, `iter_k_build_rows`,
  `KBuildRow`).
- Member-string parser (read-only upstream):
  `project/research_artifacts.py`
  (`parse_stack_members_with_protocol`).
- Signal-library shape reference:
  `project/signal_library/confluence_analyzer.load_signal_library_interval`
  (the production loader; this adapter's default loader is a
  thinner pickle-based read that tests can replace via
  injection).
- Phase 6I-18 next-probe handoff (operational state):
  `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md`.
- CLAUDE.md sec 6 — current sprint state.
