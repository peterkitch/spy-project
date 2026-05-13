# 2026-05-13 — Phase 6I-15: read-only source-availability gate integration

## 0. Scope

Makes the **source-availability predicate** introduced in
Phase 6I-14's docs first-class in the supervised-run chain.
The Phase 6I-13 attempt established that the five existing
read-only probes cannot tell an operator whether a future
refresh would land a strictly-future trading day on the
cache; they only inspect existing on-disk cache / gate /
validator state. This phase closes that gap with a new
read-only probe module, a backward-compatible wiring into
the Phase 6I-9 supervised gate, and a new advisory action
on the gate that the Phase 6I-10 flow integrity audit
surfaces.

**Strict no-write contract.** All new code is read-only.
The probe calls the Phase 6E-5 refresher with
`write=False` (dry-run). The new gate action
(`source_ready_for_supervised_refresh`) is **advisory
only** — `safe_to_authorize_writer_now` NEVER flips to
`true` via the source-availability surface. The Phase
6H-5 two-key writer authorization gate (`--write` +
`PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`) and
the Phase 6I-9 supervised gate's existing decision
cascade are unchanged. No production writes. No yfinance
fetch during tests (fakes / monkeypatches only).

## 1. The three states this work distinguishes

After Phase 6I-15, the supervised-run chain can surface
**three** distinct calendar positions for a single ticker
(SPY today):

  1. **Existing cache already strictly ahead of cutoff.**
     `cache_ahead_of_cutoff = true` on
     `cache_cutoff_watcher`; the gate emits
     `authorize_guarded_writer_for_selected_tickers`
     (assuming the rest of the chain is healthy). This is
     the existing happy path; nothing about it changes.
  2. **Existing cache equals cutoff, but a no-write refresh
     dry-run shows a fetchable strictly-future trading day
     IS available.** `cache_equal_to_cutoff = true`; the
     existing gate verdict is
     `wait_for_cache_ahead_of_cutoff`; the **new**
     `source_availability_probe` verdict is
     `source_ready_for_refresh`; the gate **upgrades** its
     `recommended_operator_action` to the **new advisory**
     `source_ready_for_supervised_refresh`. `safe_to_authorize_writer_now`
     STAYS `false`. The operator's next move is the Phase
     6I-11 supervised-run pattern (authorize a fresh
     refresh, re-run the five standard probes against the
     post-refresh cache, proceed only if the gate now
     reports safe).
  3. **Existing cache equals cutoff AND no fetchable
     strictly-future trading day is available yet.** The
     gate continues to emit
     `wait_for_cache_ahead_of_cutoff`; the new probe
     reports `source_equal_cutoff_wait` /
     `source_behind_cutoff_wait` /
     `source_unavailable_manual_review`. The operator
     halts — no action will productively unstick the gate
     until the upstream provider has fresh data to fetch.

The post-Phase-6I-13 SPY state is **state 3** today
(cache `2026-05-12` = cutoff `2026-05-12`; no fetchable
strictly-future trading day until the next U.S. market
close).

## 2. New module: `source_availability_probe.py`

### 2.1 Public surface

```python
ACTION_SOURCE_READY_FOR_REFRESH       = "source_ready_for_refresh"
ACTION_SOURCE_EQUAL_CUTOFF_WAIT       = "source_equal_cutoff_wait"
ACTION_SOURCE_BEHIND_CUTOFF_WAIT      = "source_behind_cutoff_wait"
ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW = "source_unavailable_manual_review"

ISSUE_SOURCE_REFRESH_DRY_RUN_FAILED      = "source_refresh_dry_run_failed"
ISSUE_SOURCE_MISSING_NEW_CACHE_DATE      = "source_missing_new_cache_date"
ISSUE_SOURCE_UNPARSEABLE_NEW_CACHE_DATE  = "source_unparseable_new_cache_date"
ISSUE_SOURCE_UNPARSEABLE_CURRENT_AS_OF_DATE = "source_unparseable_current_as_of_date"

@dataclass
class SourceAvailabilityState:
    ticker: str
    current_as_of_date: str
    old_cache_date_range_end: Optional[str]
    new_cache_date_range_end: Optional[str]
    source_ahead_of_cutoff: bool
    source_equal_to_cutoff: bool
    source_behind_cutoff: bool
    dry_run_attempted: bool
    dry_run_succeeded: bool
    provider_fetch_telemetry: Optional[dict[str, Any]]
    recommended_source_action: str
    issue_codes: tuple[str, ...] = ()

@dataclass
class SourceAvailabilityReport:
    generated_at: str
    current_as_of_date: str
    inspected_count: int
    states: tuple[SourceAvailabilityState, ...]
    counts_by_recommended_source_action: dict[str, int]
    source_ready_tickers: tuple[str, ...] = ()

    def to_json_dict(self) -> dict[str, Any]: ...

def evaluate_source_availability(
    ticker: str,
    *,
    cache_dir: Optional[Any] = None,
    status_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    refresher_callable: Optional[Callable[..., Any]] = None,
) -> SourceAvailabilityState: ...

def evaluate_source_availability_many(
    tickers: Iterable[str],
    *,
    cache_dir: Optional[Any] = None,
    status_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    refresher_callable: Optional[Callable[..., Any]] = None,
) -> SourceAvailabilityReport: ...

def main(argv: Optional[Sequence[str]] = None) -> int: ...
```

### 2.2 Decision rules

  - `new_cache_date_range_end > current_as_of_date`
    → `ACTION_SOURCE_READY_FOR_REFRESH`;
    `source_ahead_of_cutoff = true`.
  - `new_cache_date_range_end == current_as_of_date`
    → `ACTION_SOURCE_EQUAL_CUTOFF_WAIT`;
    `source_equal_to_cutoff = true`.
  - `new_cache_date_range_end < current_as_of_date`
    → `ACTION_SOURCE_BEHIND_CUTOFF_WAIT`;
    `source_behind_cutoff = true`.
  - Missing / unparseable date OR refresher dry-run
    raised → `ACTION_SOURCE_UNAVAILABLE_MANUAL_REVIEW`
    with the appropriate `ISSUE_*` code; all three
    boolean predicates `false`.

`write=False` always. The probe **never** writes to
production roots. The default refresher callable
(`signal_engine_cache_refresher.refresh_signal_engine_cache`)
itself only lazily imports `yfinance` inside its
`_default_yfinance_fetcher`, so tests that inject a fake
refresher via `refresher_callable=...` never trigger the
network.

`provider_fetch_telemetry` is surfaced verbatim on the
state (same shape Phase 6I-12 ProviderFetchTelemetry
defines: provider_name / fetch_attempted /
fetch_succeeded / ticker / rows / date_range_start /
date_range_end / elapsed_seconds / error). The probe
accepts either a real `ProviderFetchTelemetry` dataclass
(via `.to_json_dict()`) or a plain dict (used by test
fakes).

### 2.3 CLI

```
python source_availability_probe.py --ticker SPY
python source_availability_probe.py --tickers SPY,AAPL
```

`--cache-dir`, `--status-dir`, and `--current-as-of-date`
are passed through to the refresher. JSON to stdout.
Exit codes: `0` success, `2` invalid args (no
`SystemExit` leak), `3` unexpected exception.

### 2.4 Forbidden-imports static guard

The probe module is statically tested against:

```
daily_board_automation_writer, confluence_pipeline_runner,
daily_board_automation_executor, yfinance, dash, spymaster,
trafficflow, stackbuilder, onepass, impactsearch,
confluence, cross_ticker_confluence, daily_signal_board,
subprocess
```

(`signal_engine_cache_refresher` is NOT forbidden — the
probe imports it at top level because the refresher
itself defers `yfinance` to a lazy import inside its
default fetcher, and the probe pins `write=False` at the
call site.)

## 3. Supervised gate wiring

`daily_board_supervised_run_gate.evaluate_supervised_run_gate`
gains two optional parameters:

```python
def evaluate_supervised_run_gate(
    ...,
    include_source_availability: bool = False,
    source_availability_callable: Optional[Any] = None,
) -> SupervisedRunGateReport: ...
```

Default: **off**. The flag is OFF by default to preserve
the existing gate contract (no per-ticker refresher
dry-run invoked along the gate's read-only path). When
ON **and** `wait_for_cache_ahead_tickers` is non-empty,
the gate calls the probe over those wait tickers (via
the injectable callable; defaults to a lazy
`source_availability_probe.evaluate_source_availability_many`).

### 3.1 New `SupervisedRunGateReport` fields

```
source_availability_checked: bool                 # always emitted
source_ready_tickers: tuple[str, ...]             # always emitted
source_wait_tickers: tuple[str, ...]              # always emitted
source_manual_review_tickers: tuple[str, ...]     # always emitted
source_availability_by_ticker: dict[str, str]     # always emitted
```

When `include_source_availability=False`,
`source_availability_checked` is `False` and all ticker
tuples / by_ticker entries are empty. The JSON shape is
backward-compatible — existing consumers see the new
keys but they read as empty/zero unless the flag is on.

### 3.2 Decision behavior

Three load-bearing rules:

  1. **Existing safety is never reduced.** If the gate
     was already safe (write-ready candidates exist), the
     source-availability probe is **not even invoked**
     (no `wait_for_cache_ahead_tickers` to probe).
     `safe_to_authorize_writer_now` stays `true`.
  2. **Source-readiness never flips safety.** When the
     probe says a wait ticker is source-ready,
     `recommended_operator_action` upgrades to the new
     advisory `source_ready_for_supervised_refresh`, but
     `safe_to_authorize_writer_now` STAYS `false`.
  3. **Source-not-ready preserves the existing wait
     action.** When no wait ticker is source-ready, the
     gate emits the unchanged
     `wait_for_cache_ahead_of_cutoff` action; only the
     new `source_*_tickers` fields differ from the
     pre-Phase-6I-15 output.

The new advisory action does NOT emit a writer command.
The Phase 6H-5 two-key gate is the only authorization
surface that emits writer commands.

### 3.3 New gate CLI flag

```
--include-source-availability
```

Opt-in. When supplied, the gate consults the probe and
emits the new fields in its JSON output. Without the
flag, the gate behaves exactly as in Phase 6I-14.

## 4. Flow integrity audit wiring

`daily_board_flow_integrity_audit.run_daily_board_flow_integrity_audit`
calls the supervised gate with
`include_source_availability=True` so the audit's
read-only end-to-end snapshot always inspects the
source-availability state. The audit's `gate_summary`
gains four passthrough keys:

```
source_availability_checked
source_ready_tickers
source_wait_tickers
source_manual_review_tickers
```

### 4.1 Five-case wording selector

The Phase 6I-12 four-case `recommended_next_evidence_step`
selector gains a **case 3b**:

  - **Case 1** — any stage failed →
    "Resolve the failing read-only checks ..."
  - **Case 2** — all pass + production roots touched →
    "Investigate production-root mutation ..."
  - **Case 3a** — all pass + roots untouched + gate not
    safe + source NOT ready (or probe not run) →
    "Do NOT authorize the writer now ... operator-action
    signal, not a regression ..." (unchanged from Phase
    6I-12)
  - **Case 3b (NEW)** — all pass + roots untouched + gate
    not safe + gate action ==
    `source_ready_for_supervised_refresh` AND
    `source_ready_tickers` non-empty →
    "A supervised refresh CAN BE PREPARED for
    [tickers] ... Running the refresh is NOT a writer
    authorization. Use the Phase 6I-11 supervised-run
    pattern ..."
  - **Case 4** — all pass + roots untouched + gate safe
    → the supervised-run-ready text (unchanged).

Priority order is preserved: case 1 wins over 2 over 3
over 4. Inside case 3, the new 3b takes precedence over
3a only when both the new gate action AND the non-empty
source-ready set are present.

The production-root snapshot behavior
(`relative_path_size_mtime` strategy) is unchanged.

## 5. Why source-ready is NOT the same as writer-authorized

This is the most important conceptual point of Phase
6I-15. The new `source_ready_for_supervised_refresh`
action is an **operator advisory** — it tells the
operator that running a refresh would be productive
(would land a strictly-future trading day on the
cache). It does **NOT**:

  - flip `safe_to_authorize_writer_now` to `true`;
  - emit a writer command;
  - bypass the Phase 6H-5 two-key writer gate;
  - bypass the Phase 6I-9 supervised gate's decision
    cascade.

The operator's response to the advisory action is the
existing **Phase 6I-11 supervised-run pattern**:

  1. Authorize a fresh refresh via the Phase 6I-11
     temp-launcher-script pattern (one-shot
     `daily_board_automation_writer.py --ticker SPY
     --write` with `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`
     in a `try/finally` block that removes the env var).
  2. Wait for the writer to land the new cache.
  3. Re-run the five standard read-only probes against
     the post-refresh cache.
  4. Proceed to a real authorized pipeline run **only**
     if `gate.safe_to_authorize_writer_now=true` and the
     full five-precondition checklist passes against the
     new probe outputs.

Source-availability is therefore **pre-decision evidence**,
not authorization. The probe answers "would a refresh be
productive?"; the gate answers "is it safe to write?".

## 6. How this prepares the next supervised run

Before Phase 6I-15:

  - The five standard probes report
    `wait_for_cache_ahead_of_cutoff`.
  - The operator has no read-only way to know whether
    today's calendar position permits a productive
    refresh; the only way to find out was to actually
    authorize the refresh and see what landed.

After Phase 6I-15:

  - The flow audit emits **case 3b** when (and only
    when) the calendar position permits a productive
    refresh. That is the operator's read-only signal
    that the supervised-run pattern is worth
    attempting.
  - The audit emits **case 3a** when the calendar
    position does not permit a productive refresh.
    That is the operator's read-only signal to halt
    and wait.

Neither case 3a nor case 3b authorizes anything. Both
preserve the existing two-key authorization surface
intact.

## 7. Test evidence

```
test_scripts/test_source_availability_probe.py
                                                15 passed in 0.95 s

test_scripts/test_daily_board_supervised_run_gate.py
                                                28 passed in 0.99 s
  (23 base + 5 Phase 6I-15)

test_scripts/test_daily_board_flow_integrity_audit.py
                                                23 passed in 103.19 s
  (21 base + 2 Phase 6I-15)

Focused 7-way (probe + gate + flow audit + cache
cutoff watcher + source freshness preflight + writer +
contract validator):
                                                204 passed in 158.90 s
```

`py_compile` clean on the new module + all changed
modules + all new and changed test files. `git diff
--check` clean.

### 7.1 Test surface highlights

**Probe (`test_source_availability_probe.py`, 15 new
tests):**

  - Forbidden-imports static guard.
  - Per-ticker verdicts for ahead / equal / behind /
    missing-date / unparseable-date / dry-run-exception.
  - `provider_fetch_telemetry` pass-through for both
    plain-dict and `to_json_dict()` shapes.
  - Multi-ticker `evaluate_source_availability_many`
    aggregates counts + names the `source_ready_tickers`
    set correctly.
  - `to_json_dict()` round-trips through `json.dumps`.
  - CLI rc=0 / rc=2 / no `SystemExit` leak.

**Supervised gate (`test_daily_board_supervised_run_gate.py`,
+5 Phase 6I-15 tests):**

  - Default-off leaves the new fields empty even in the
    wait state.
  - Equal-cache + source-ready produces the new
    advisory action AND
    `safe_to_authorize_writer_now=False`.
  - Equal-cache + source-not-ready keeps the existing
    wait action.
  - Gate-safe path is never reduced by the probe (and
    the probe is not invoked at all when there are no
    wait tickers).
  - The new fields serialize in `to_json_dict()` and
    `json.dumps` round-trips cleanly.

**Flow integrity audit
(`test_daily_board_flow_integrity_audit.py`, +2 Phase
6I-15 tests):**

  - Case 3b wording fires when the gate action is
    `source_ready_for_supervised_refresh` AND
    `source_ready_tickers` is non-empty.
  - Case 3a wording is preserved verbatim when the
    probe ran but found nobody source-ready.

## 8. No-production-write confirmation

  - No code path in this PR writes to a production root.
    The new probe calls the refresher with `write=False`
    at the function-arg level; the gate's wiring calls
    the probe over the wait-tickers list only.
  - No `--write` invocation of any writer module.
  - No `PRJCT9_AUTOMATION_WRITE_AUTH` env var set
    anywhere in the test suite or in this PR's commit
    history.
  - No live `yfinance` fetch during tests. Tests inject
    fakes via `refresher_callable=...` /
    `source_availability_callable=...` /
    `queue_planner_callable=...`. The probe module's
    default refresher (the real Phase 6E-5 callable)
    itself only lazily imports `yfinance` inside its
    `_default_yfinance_fetcher`, so even an accidental
    bare call would not pull the network in until the
    actual fetcher was invoked.
  - No StackBuilder / OnePass / ImpactSearch /
    TrafficFlow / Spymaster batch execution.
  - No subprocess.
  - No real read-only smoke against production roots
    was run during this PR. Every assertion lives
    inside `tmp_path` fixtures or against fakes.
  - The Phase 6H-5 two-key writer gate, the Phase 6I-9
    supervised gate's existing decision cascade for
    write-ready / manual / upstream-blocked /
    downstream-gap / leader-eligible / truncation
    cases, the Phase 6I-10 production-root snapshot
    strategy (`relative_path_size_mtime`), and the
    Phase 6I-12 ProviderFetchTelemetry four-surface
    contract are all unchanged.

## 9. Files changed (this PR)

| File | Lines |
|---|---|
| `project/source_availability_probe.py` | new, +~520 |
| `project/daily_board_supervised_run_gate.py` | +~95 / -2 |
| `project/daily_board_flow_integrity_audit.py` | +~70 / -10 |
| `project/test_scripts/test_source_availability_probe.py` | new, +~380 |
| `project/test_scripts/test_daily_board_supervised_run_gate.py` | +~230 / 0 |
| `project/test_scripts/test_daily_board_flow_integrity_audit.py` | +~170 / 0 |
| `project/md_library/shared/2026-05-13_PHASE_6I15_SOURCE_AVAILABILITY_GATE_INTEGRATION.md` | new (this doc) |
| `project/CLAUDE.md` | +~10 (brief Phase 6I-15 pending note in § 6) |

## 10. Reference paths

  - Phase 6I-14 sprint-state refresh (predicate-first
    operator discipline):
    `project/md_library/shared/2026-05-13_PHASE_6I14_SPRINT_STATE_AND_NEXT_RUN_HANDOFF.md`
  - Phase 6I-12 ProviderFetchTelemetry instrumentation:
    `project/md_library/shared/2026-05-13_PHASE_6I12_PROVIDER_FETCH_TELEMETRY_AND_FLOW_AUDIT_WORDING.md`
  - Phase 6I-11 first authorized SPY writer run:
    `project/md_library/shared/2026-05-12_PHASE_6I11_SUPERVISED_SPY_WRITER_EVIDENCE_RUN.md`
  - Phase 6I-10 flow integrity audit (evidence matrix):
    `project/md_library/shared/2026-05-12_PHASE_6I10_END_TO_END_FLOW_EVIDENCE_AUDIT.md`
  - Phase 6E-5 refresher (write=False dry-run path):
    `project/signal_engine_cache_refresher.py`
  - Phase 6H-5 guarded writer (two-key gate; unchanged):
    `project/md_library/shared/2026-05-12_PHASE_6H5_GUARDED_WRITE_EXECUTOR_FOUNDATION.md`
  - Phase 6I-9 supervised gate (extended in this PR):
    `project/daily_board_supervised_run_gate.py`
  - Phase 6I-10 flow integrity audit (extended in this
    PR): `project/daily_board_flow_integrity_audit.py`
