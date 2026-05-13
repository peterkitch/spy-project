# 2026-05-13 — Phase 6I-12: provider-fetch telemetry + flow-audit wording fix

## 0. Scope

Two narrow, additive code changes that convert Phase 6I-11
lessons into code-backed evidence improvements:

  - **Scope A — flow-audit wording fix.** Refine the Phase 6I-10
    flow integrity audit's `recommended_next_evidence_step`
    selection so it distinguishes four disjoint cases instead
    of a single binary True/False fork. The previous wording
    said *"Resolve the failing read-only checks"* whenever the
    composite verdict was `false`, even when no stage actually
    failed (see Phase 6I-11 doc § 9 for the observed quirk).
  - **Scope B — provider-fetch telemetry.** Add a narrow
    `provider_fetch_telemetry` payload to the signal engine
    cache refresher's result surface, plumb it through the
    writer's `RefreshOutcome` JSON serializer, and verify it
    survives onto the writer's stdout payload and the JSONL
    execution-log row. The telemetry is **fetch-attempt/result
    telemetry**, NOT HTTP / wire-level telemetry; it does not
    claim to capture provider-side request/response identifiers.

**No production-write authorization.** No writer `--write`
invocation. No source refresh against production roots. No
production pipeline write. No StackBuilder / OnePass /
ImpactSearch / TrafficFlow / Spymaster batch execution. No
yfinance fetch during tests (all tests use fake fetchers or
monkeypatch `_default_yfinance_fetcher`). No subprocess. The
Phase 6H-5 two-key writer authorization gate is unchanged.

## 1. Why Phase 6I-11 left direct yfinance / provider telemetry open

The Phase 6I-11 evidence-run doc (§ 7 row 4) recorded the
`real_yfinance_fetch` item as **INDIRECTLY EVIDENCED / DIRECT
TELEMETRY STILL OPEN**. The reasoning was honest but limited:

  - The live writer-internal refresher callable
    `signal_engine_cache_refresher.refresh_signal_engine_cache`
    did run, recorded in the writer's `functions_executed`.
  - The SPY signal-engine cache `date_range_end` advanced from
    `2026-05-11` to `2026-05-12`, consistent with the
    refresher's documented yfinance-backed path.
  - The cache PKL `+1,028`-byte delta and manifest delta were
    consistent with a real fetch+recompute on that calendar
    position.
  - **However**, no direct telemetry from the fetcher call
    itself was surfaced in the writer's stdout, JSONL
    execution-log row, status JSON, or PKL manifest. The
    evidence was confined to "the cache moved one trading day,
    therefore the refresher must have fetched."

That inference-from-delta is structurally fragile: it cannot
distinguish a real fetch from a hypothetical refresher path
that fabricated synthetic data, and it cannot record the row
count, the date range that came back from the provider, or the
exception class when a fetch fails.

Phase 6I-12 closes that **structural** gap by stamping
fetch-call-level facts onto the refresher's result surface and
threading them through the writer to stdout + JSONL. The next
supervised authorized run will therefore carry telemetry that
**directly observes** the refresher's fetch call, even though
the telemetry remains above the HTTP boundary.

## 2. What telemetry is now captured

A new dataclass `ProviderFetchTelemetry` is defined in
`signal_engine_cache_refresher.py`:

```
@dataclass
class ProviderFetchTelemetry:
    provider_name: str
    fetch_attempted: bool
    fetch_succeeded: bool
    ticker: str
    rows: int
    date_range_start: Optional[str]   # ISO date or None
    date_range_end: Optional[str]     # ISO date or None
    elapsed_seconds: float            # inside fetcher call only
    error: Optional[str]              # short exception class + msg
```

The JSON shape (via `to_json_dict()`):

```json
{
  "provider_name": "yfinance",
  "fetch_attempted": true,
  "fetch_succeeded": true,
  "ticker": "SPY",
  "rows": 6432,
  "date_range_start": "2000-01-03",
  "date_range_end": "2026-05-12",
  "elapsed_seconds": 4.214,
  "error": null
}
```

### 2.1 When the telemetry is populated

| Refresh path | `provider_fetch_telemetry` |
|---|---|
| Invalid ticker (early exit before fetcher call) | `None` |
| Invalid `max_sma_day` (early exit before fetcher call) | `None` |
| Fetcher raised an exception | populated; `fetch_attempted=true`, `fetch_succeeded=false`, `error=<exception class + brief msg>`, `rows=0`, date ranges `None` |
| Fetcher returned empty / non-DataFrame | populated; `fetch_attempted=true`, `fetch_succeeded=false`, `rows=0`, date ranges `None`, `error=None` |
| Fetcher returned a usable DataFrame, optimizer failed downstream | populated; `fetch_attempted=true`, `fetch_succeeded=true`, `rows=<actual>`, date ranges set |
| Dry-run / write-true happy path | populated; `fetch_attempted=true`, `fetch_succeeded=true`, `rows=<actual>`, date ranges set |

### 2.2 How `provider_name` is resolved

  - Explicit `provider_name=` kwarg on
    `refresh_signal_engine_cache` wins.
  - Else, if `data_fetcher is None` (default fetcher in use):
    `provider_name = "yfinance"` (module constant
    `DEFAULT_PROVIDER_NAME`).
  - Else, if the supplied `data_fetcher` callable exposes a
    `PROVIDER_NAME` attribute: use it.
  - Else: `"custom_callable"`.

### 2.3 Writer pass-through

`daily_board_automation_writer.py` extends `RefreshOutcome`
with a new optional field:

```
@dataclass
class RefreshOutcome:
    # ... existing fields unchanged ...
    provider_fetch_telemetry: Optional[dict[str, Any]] = None
```

The writer extracts the telemetry from the refresher result
via a defensive `getattr` + `to_json_dict()` adapter so it can
forward either the real `ProviderFetchTelemetry` dataclass or a
plain dict (used by test fakes) without ever importing the
refresher's dataclass at the top level. The writer's
`_refresh_outcome_to_json` adds a `provider_fetch_telemetry`
key (value `None` when no fetch ran) to the serialized
`RefreshOutcome` block; this serialized form is what lands in
the stdout JSON payload **and** in each row of the JSONL
execution log.

Existing writer fields are unchanged; the new field is
strictly additive.

## 3. What this telemetry does NOT prove

  - **Not HTTP-level provider telemetry.** No request
    identifiers, response identifiers, HTTP status codes,
    response headers, TLS facts, or provider-side error
    bodies are captured. Anything that lives below the
    `data_fetcher` callable boundary remains opaque to this
    telemetry.
  - **Not anti-mock telemetry.** A test or operator can still
    inject a `data_fetcher` callable that fabricates a
    DataFrame; the telemetry will faithfully report
    `provider_name="custom_callable"` (or whatever the caller
    declared) and `rows=<fabricated row count>`. The telemetry
    is honest about what it observed; it is not a tamper-
    proof attestation that the data came from a real exchange.
  - **Not a yfinance-version pin.** Provider library version,
    cache layer version, and proxy state are not recorded.
  - **Does not change `commands_executed` / `functions_executed`
    semantics.** `commands_executed` remains the logical
    command label; `functions_executed` remains the runtime
    proof recording the in-process callable. The new
    telemetry sits **alongside** these surfaces, not in place
    of them.

## 4. How the next supervised writer run will use this

The next supervised authorized writer run for SPY (a future
Phase 6I-13 or successor) will land with this telemetry
already in place. On a calendar position where the post-
refresh cache `date_range_end > current_as_of_date` strictly
(so the pipeline actually executes), the writer's JSONL row
will carry:

```
refresh_result.provider_fetch_telemetry = {
  "provider_name": "yfinance",
  "fetch_attempted": true,
  "fetch_succeeded": true,
  "ticker": "SPY",
  "rows": <actual>,
  "date_range_start": "<actual ISO>",
  "date_range_end": "<actual ISO>",
  "elapsed_seconds": <actual>,
  "error": null
}
```

That closes the **structural** evidence gap from Phase 6I-11:
the next run will record that the live refresher actually
invoked the yfinance-backed fetcher, what shape the returned
DataFrame had, and how long the fetcher call took. It does not
close the HTTP-telemetry gap; that remains a separate, larger
piece of work (a future phase could add a thin yfinance
HTTP-adapter wrapper that captures HTTP status codes and
response identifiers).

For the Phase 6I-11 audit's evidence matrix, the
`real_yfinance_fetch` item updates from "INDIRECTLY EVIDENCED /
DIRECT TELEMETRY STILL OPEN" to a finer-grained shape after
the next supervised run:

  - **Fetch-call-level telemetry: CLOSED** (this telemetry).
  - **HTTP-level provider telemetry: STILL OPEN** (deferred to
    a future phase if the operator decides the additional
    instrumentation cost is justified).

## 5. Flow-audit recommendation wording fix (Scope A)

`daily_board_flow_integrity_audit.py` now selects
`recommended_next_evidence_step` from four disjoint cases,
evaluated in priority order:

| # | Trigger | Wording prefix |
|---|---|---|
| 1 | `all_passed=False` (any stage failed) | "Resolve the failing read-only checks BEFORE any authorized run." |
| 2 | `all_passed=True` AND `production_roots_untouched=False` | "Investigate production-root mutation BEFORE any authorized run." (also names the snapshot precision) |
| 3 | `all_passed=True` AND `production_roots_untouched=True` AND `gate_safe=False` | "Do NOT authorize the writer now. ... `recommended_operator_action=<gate action>`. No read-only stage failed — this is an operator-action signal, not a regression." |
| 4 | `all_passed=True` AND `production_roots_untouched=True` AND `gate_safe=True` | The existing supervised-run-ready text. |

Priority order matters: case 1 (stage failure) is the most
urgent signal and takes precedence over case 2 (roots touched)
and case 3 (gate not safe); case 2 (regression signal)
supersedes case 3 (operator-action signal); case 3 is now
distinguishable from case 1.

### 5.1 New tests for Scope A

Three new tests added to
`test_daily_board_flow_integrity_audit.py`:

  - `test_wording_gate_not_safe_with_all_stages_passing_does_not_blame_stages`
    — monkeypatches `_stage_queue_and_gate` to return a
    passing StageCheck plus a gate verdict with
    `safe_to_authorize_writer_now=False`. Asserts the text
    is the **case 3** wording (names the gate action;
    leads with "Do NOT authorize the writer now"; does NOT
    include "Resolve the failing read-only checks"; does
    NOT include the supervised-run-ready wording).
  - `test_wording_injected_stage_failure_uses_failure_text`
    — monkeypatches `_stage_writer_static` to return a
    failing StageCheck. Asserts **case 1** wording. Also
    asserts the case-2 and case-3 phrases are NOT in the
    text (priority).
  - `test_wording_production_root_mutation_uses_mutation_text`
    — monkeypatches `_snapshot_production_roots` to return
    different snapshots on call 1 vs call 2 (no actual
    production-root mutation occurs; the simulation lives
    in the monkeypatched return value). Asserts **case 2**
    wording, names the snapshot precision string, and
    asserts case-1/3/4 phrases are NOT in the text
    (priority).

## 6. Files changed

| File | Lines |
|---|---|
| `project/daily_board_flow_integrity_audit.py` | +63 / −18 (Scope A; reworked text selection) |
| `project/signal_engine_cache_refresher.py` | +130 / −7 (Scope B; new dataclass + telemetry capture + threading) |
| `project/daily_board_automation_writer.py` | +50 / −0 (Scope B; new optional `RefreshOutcome` field + pass-through extractor) |
| `project/test_scripts/test_daily_board_flow_integrity_audit.py` | +216 / −0 (3 new Scope A tests) |
| `project/test_scripts/test_signal_engine_cache_refresher.py` | +154 / −0 (4 new Scope B refresher tests) |
| `project/test_scripts/test_daily_board_automation_writer.py` | +194 / −0 (2 new Scope B writer tests) |
| `project/md_library/shared/2026-05-13_PHASE_6I12_PROVIDER_FETCH_TELEMETRY_AND_FLOW_AUDIT_WORDING.md` | this doc |
| **total (code+tests)** | **+807 / −25** |

## 7. Tests run

```
test_scripts/test_signal_engine_cache_refresher.py
test_scripts/test_daily_board_automation_writer.py
test_scripts/test_daily_board_flow_integrity_audit.py
test_scripts/test_daily_board_supervised_run_gate.py
test_scripts/test_confluence_ranking_contract_validator.py
                                                       174 passed in 157.17 s

Full regression (test_scripts):
                                                       1549 passed in 343.05 s
  (1540 baseline + 9 new = 1549; 60 pre-existing pandas
   fragmentation warnings unchanged; no new failures)
```

`py_compile` clean on all changed modules + tests.
`git diff --check` clean (LF→CRLF normalization warning only).

## 8. No-production-write confirmation

  - No writer `--write` invocation. No
    `PRJCT9_AUTOMATION_WRITE_AUTH` environment variable set.
  - No source refresh executed against production roots
    (`project/cache/results/`, `project/cache/status/`). All
    refresher tests run against `tmp_path` fixtures with fake
    fetchers; the test
    `test_provider_fetch_telemetry_provider_name_defaults_to_yfinance_when_default_fetcher`
    monkeypatches `_default_yfinance_fetcher` so the default
    code path **never reaches the network**.
  - No production pipeline write. No yfinance fetch. No
    subprocess.
  - No StackBuilder / OnePass / ImpactSearch / TrafficFlow /
    Spymaster batch execution.
  - The Phase 6H-5 two-key writer authorization gate is
    unchanged.
  - The Phase 6I-10 production-root snapshot strategy is
    unchanged (`relative_path_size_mtime`).
  - The Phase 6I-1 contract validator, Phase 6I-3 ranking
    emitter, Phase 6I-6 queue planner, Phase 6I-9 supervised
    gate, and Phase 6I-10 audit semantics are unchanged
    except for the text-selection refinement above.

## 9. Updated remaining evidence gaps

After Phase 6I-12, the three items left over from Phase 6I-11
update as follows:

| Item | After 6I-11 | After 6I-12 (this PR; instrumentation only) | What still needs to happen |
|---|---|---|---|
| `real_confluence_pipeline_runner_write` | STILL OPEN | STILL OPEN (unchanged) | Future supervised run on a calendar position where `cache_date_range_end > current_as_of_date` strictly. |
| `real_post_pipeline_validation_on_writer_path` | STILL OPEN | STILL OPEN (unchanged) | Same future condition; the Phase 6I-8 contract-validation callable fires only after the pipeline executes on the writer path. |
| `real_yfinance_fetch` direct telemetry | INDIRECTLY EVIDENCED / DIRECT TELEMETRY STILL OPEN | **Fetch-call telemetry instrumented; awaiting capture on a future supervised run.** | (a) Future supervised authorized run captures `refresh_result.provider_fetch_telemetry` in the JSONL row with `provider_name="yfinance"` and a non-zero `rows` count — that closes the fetch-call-level slice. (b) HTTP-level provider telemetry remains a deliberate non-goal of this phase. |

## 10. Reference paths

  - Phase 6I-10 baseline (audit module + evidence matrix):
    `project/md_library/shared/2026-05-12_PHASE_6I10_END_TO_END_FLOW_EVIDENCE_AUDIT.md`
  - Phase 6I-11 first supervised authorized run for SPY (with
    the wording-quirk § 9 that motivated Scope A):
    `project/md_library/shared/2026-05-12_PHASE_6I11_SUPERVISED_SPY_WRITER_EVIDENCE_RUN.md`
  - Refresher module: `project/signal_engine_cache_refresher.py`
  - Writer module: `project/daily_board_automation_writer.py`
  - Flow integrity audit module:
    `project/daily_board_flow_integrity_audit.py`
