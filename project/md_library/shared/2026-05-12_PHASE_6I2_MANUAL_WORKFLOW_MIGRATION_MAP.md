# Phase 6I-2 — Manual TrafficFlow / Spymaster → Confluence pipeline migration map

**Status:** read-only inventory + contract design. No code
moved, no production data refreshed. This doc maps the old
operator-driven daily workflow onto the new Phase 6H / 6I
automation stack and names the remaining gaps.

**Last updated:** 2026-05-12.

## 0. Scope statement

  - **No production writes.** No `cache/`, `output/`,
    `signal_library/`, or `stackbuilder/` byte changed.
  - **No source refresh.** No yfinance fetch.
  - **No Phase 6D pipeline write.**
  - **No StackBuilder run, no OnePass run, no Spymaster
    batch processing.**
  - This phase produces a doc; it does not modify any
    operator-facing code path.
  - **No 30-day StackBuilder stale window.** Saved stack
    variants are durable. Multiple stacks per ticker are
    first-class. Ambiguous selection remains a
    policy/config problem, not an age problem (Phase
    6H-3 contract carried forward).

## 1. The old manual workflow

The pre-Phase-6 workflow was six manual operator steps. It
predates the multi-timeframe / Confluence layer and used
the single-K TrafficFlow board as the "what's interesting
today?" surface.

  1. **Force freshness.** Delete every saved
     `<TICKER>_precomputed_results.pkl` under
     `project/cache/results/` so nothing carries over.
  2. **Auto-discover the work list.** Open `trafficflow.py`
     (port 8052 by default). On boot, TrafficFlow scans
     every StackBuilder combo leaderboard for every
     secondary ticker, walks each leaderboard's K rows,
     enumerates each row's `Members` field, and reports the
     union of member tickers that lack a fresh PKL. This
     produced ~1,000 ticker entries spanning ~200
     stackbuilt secondaries.
  3. **Copy to Spymaster.** Operator copied the
     comma-separated ticker list into Spymaster's
     `batch-ticker-input` textarea (port 8050) and clicked
     the `batch-process-button`.
  4. **Wait for cache regeneration.** Spymaster's
     `batch_process_tickers` callback queued the tickers
     into a background worker thread
     (`process_ticker_queue`) which fetched yfinance, ran
     the SMA-pair optimizer, and atomically wrote each
     `<TICKER>_precomputed_results.pkl` via
     `save_precomputed_results`. Hundreds of fetches; tens
     of minutes.
  5. **Single-K TrafficFlow ranking.** Return to
     TrafficFlow, pick one K (e.g. K=6),
     `build_board_rows(sec, k=6, ...)` for every secondary,
     wait for the per-K metrics to fill. Operator then read
     the resulting table.
  6. **AI summarization.** Paste the K=6 metrics table into
     an external AI assistant, ask it to highlight
     market-wide opportunities, weighting higher Sharpe,
     lower p-values, capture quality, trigger counts, etc.

This worked but had three load-bearing problems:

  - **No multi-timeframe / Confluence input.** The single-K
    table ignored the Phase 6D-3 multi-timeframe
    consensus layer entirely.
  - **No persistence guarantees.** Step 1's force-delete
    threw away every cache PKL, so a partial Spymaster
    failure left the operator with HALF a tree and no
    audit trail of what got refreshed when.
  - **No execution log.** Step 4's background worker had
    no JSONL audit trail; Step 6's AI summarization had no
    structured ranking input.

The Phase 6H / 6I stack addresses each of those gaps
explicitly. The map below shows the per-step replacement
or the planned gap if one is still open.

## 2. Exact old code paths (read-only audit)

### 2.1 TrafficFlow missing-PKL / ticker discovery

`project/trafficflow.py`:

  - `get_all_missing_pkls_all(secs: list[str]) -> list[str]`
    (line 2933) — scans every secondary's combo leaderboard
    (`_find_latest_combo_table`), walks every K row, parses
    `Members`, and calls `_classify_pkl_freshness(ticker)`
    per member. Returns the sorted union of tickers
    flagged not-fresh. This is the "auto-discover the work
    list" step.
  - `scan_missing_stale_pkls(secs, k_limit, include_stale,
    verbose)` (line 533) — internal helper that builds the
    same set as a `{ticker: reason}` map for UI display
    inside the TrafficFlow Dash app.
  - Dash surface: the `missing-pkls` Div
    (`spymaster.py:3097` analogue inside trafficflow.py
    around line 3097) renders the missing/stale summary
    string when the operator clicks Refresh.

### 2.2 TrafficFlow K-metric table

`project/trafficflow.py`:

  - `build_board_rows(sec: str, k: int, run_fence: dict,
    missing_map=None) -> list[dict]` (line 2956). Reads the
    latest combo leaderboard for `sec`, filters by `K == k`,
    drops `_progress` sentinels, and for each row:
      * sanitizes Members,
      * skips rows where no member has a PKL,
      * calls
        `compute_build_metrics_spymaster_parity(sec,
        members, eval_to_date=cap_dt)` to get
        Triggers / Wins / Losses / Win% / StdDev% / Sharpe
        / p / Avg% / Total% via
        `canonical_scoring.score_captures`,
      * computes `_calculate_signal_mix` for the protocol
        agreement ratio,
      * builds one `dict` per row with the columns:
        ``Ticker, K, Members, Trigs, Wins, Losses, Win %,
        StdDev %, Sharpe, p, Avg %, Total %, Today, Now,
        NEXT, TMRW, MIX``.
  - The board ranks rows by Sharpe descending (per the
    module's own docstring: "Ranks rows by Sharpe desc.
    Whole-row color = green (>=2), yellow (-2..2 or no
    triggers), red (<=-2).").
  - **Single K only.** The operator had to pick one K and
    read the table for that K. There is no K-aggregate or
    cross-timeframe ranking in this layer.

### 2.3 Spymaster batch processing

`project/spymaster.py`:

  - Dash UI: `batch-ticker-input` textarea +
    `batch-process-button` (~line 6403). Operator
    comma-pasted the discovery list.
  - `batch_process_tickers` callback (line 11823) —
    parses the input, queues into `ticker_queue`, primes
    the result table with "Queued" status, and starts a
    daemon `process_ticker_queue` thread.
  - `process_ticker_queue` — the worker that calls
    `yf.download(...)` per ticker, runs the Spymaster SMA
    optimizer, and invokes
    `save_precomputed_results(ticker, results)`
    (line 4607) to write the cache PKL + provenance
    manifest atomically.
  - Status JSON: `<TICKER>_status.json` under
    `project/cache/status/`.

### 2.4 StackBuilder run discovery (already part of the
Phase 6H stack)

`project/trafficflow_k_artifact_builder.py`:

  - `discover_latest_stackbuilder_run(target_ticker, *,
    stackbuilder_root=None) -> Path | None` — newest
    `mtime` seed-run directory under
    `stackbuilder_root/<TARGET>/`. Real-form first,
    safe-form fallback.

`project/daily_board_automation_preflight.py`:

  - `_discover_stackbuilder_runs(ticker,
    stackbuilder_root)` — every leaderboard-bearing
    seed-run directory.
  - `_resolve_stackbuilder_selection(runs)` — applies the
    Phase 6H-3 policy:
      * `no_stack_available` (zero runs),
      * `single_available_stack`,
      * `latest_mtime_existing_pipeline_default` (multi
        with clear newest),
      * `ambiguous_tied_mtime` (multi with tied newest —
        blocks automation).

## 3. New replacement modules (per old step)

| Old step | Old code | New replacement | Status |
|---|---|---|---|
| 1. Force freshness via delete | manual `rm cache/results/*.pkl` | `cache_cutoff_watcher.evaluate_cache_cutoff_state` — read-only inequality probe; never deletes. Per-ticker recommendation tells the operator whether a refresh is even useful. | **Replaced.** Phase 6H-2. |
| 2. Auto-discover work list | `trafficflow.get_all_missing_pkls_all(secs)` | `daily_board_automation_preflight.build_daily_board_automation_plan(tickers)` — explicit operator-supplied ticker list; no silent universe sweep. The planner returns per-ticker `recommended_automation_action`. | **Replaced (deliberately scoped narrower).** The new layer requires an explicit ticker list; see § 5 for the missing universe-sweep replacement. |
| 3. Paste into Spymaster batch | `spymaster.batch-ticker-input` textarea | `daily_board_automation_writer.py --tickers ...` CLI with the two-key auth gate. | **Replaced.** Phase 6H-5 / 6H-6. |
| 4. Wait for cache regeneration | `spymaster.batch_process_tickers` → `process_ticker_queue` → `save_precomputed_results` | `signal_engine_cache_refresher.refresh_signal_engine_cache(ticker, *, cache_dir, status_dir, write, current_as_of_date)` invoked under the writer's authorized live path. Append-only JSONL execution log replaces the silent worker thread. | **Replaced.** Phase 6E-5 + 6H-5 + 6H-6. |
| 5. Single-K TrafficFlow ranking | `trafficflow.build_board_rows(sec, k, ...)` | The Phase 6D pipeline (`confluence_pipeline_runner.run_confluence_pipeline_for_ticker`) writes daily K (K=1..12) → MTF K → Confluence MTF artifacts. The Daily Signal Board consumes the Confluence layer. | **Replaced — and upgraded** from single-K to multi-K / multi-timeframe / Confluence. See § 4 for the ranking evolution. |
| 6. AI summarization of K=6 table | operator copy-paste into external AI | The Phase 6I-1 `confluence_ranking_contract_validator.validate_confluence_ranking_contracts(tickers)` returns a per-ticker `board_row_preview` (ticker, consensus_signal, signal_value, agreement_active, agreement_total, agreement_ratio, coverage, as_of_date, rank_eligible). This is a STRUCTURED ranking input. | **Partially replaced.** The structured row exists; the multi-ticker ranking sort + AI-summarization handoff is the open Phase 6I-3 gap; see § 5. |

## 4. Single-K → multi-K / multi-timeframe / Confluence ranking evolution

The old TrafficFlow K-metric table ranked combo rows
inside a single K. The new ranking aggregates across
K=1..12 and across 5 timeframes (1d, 1wk, 1mo, 3mo, 1y),
then collapses the resulting 60-cell grid into a single
per-ticker Confluence verdict.

### 4.1 Old: single-K, per-row Sharpe-rank table

```
(secondary "SPY", K=6)
+-----------+-----+-------------+-------+--------+-----+
| Members   | K   | Sharpe      | p     | Trigs  | ... |
+-----------+-----+-------------+-------+--------+-----+
| AAA, BBB  | 6   | 1.42        | 0.03  | 88     | ... |
| CCC, DDD  | 6   | 0.91        | 0.08  | 71     | ... |
| ...                                                  |
+------------------------------------------------------+
sort by Sharpe desc
```

The operator picked one K and treated each row (one
member-tuple inside the leaderboard) as one candidate.

### 4.2 New: aggregate across K × timeframe → one row per ticker

```
ticker SPY, current_as_of_date 2026-05-08
  → 12 K values × 5 timeframes = 60 cells
  → Phase 6D-2 MTF artifact per K
  → Phase 6D-3 Confluence MTF artifact (one)
       buy_votes / short_votes / none_votes / missing_votes
       active_count = buy + short
       available_count = active_count + none
       agreement_active = strict-unanimity (Phase 6I-1 § 4.5)
       agreement_total = available_count
       confluence_signal ∈ {Buy, Short, None}
  → Daily Signal Board row (Phase 6I-1 § 4.7)
       ticker / consensus_signal / agreement_active /
       agreement_total / agreement_ratio / coverage /
       as_of_date / rank_eligible / ranking_blocked_reason
```

The validator's `board_row_preview` collapses the 60-cell
grid into the structured ranking row the public board
consumes. Each ticker's `agreement_ratio = active_count /
available_count` is the natural successor to the old
`Sharpe` column — a normalized, per-ticker "how strong is
the saved-research evidence?" score that already
incorporates K-aggregate and multi-timeframe input.

### 4.3 Ranking inputs the new contract exposes per ticker

  - `consensus_signal` ∈ `{"Buy", "Short", "None"}` and
    `consensus_signal_value` ∈ `{1, -1, 0}` (Phase 6I-1
    enforces the alias coherence).
  - `agreement_active` (strict-unanimity count) and
    `agreement_total` (available_count).
  - `agreement_ratio` (= `active_count / available_count`,
    matches `daily_signal_board._confluence_active_total`).
  - `coverage` = `"Full"` only when every contract from
    cache → Confluence passes.
  - `rank_eligible` mirrors
    `confluence_pipeline_readiness.leader_eligible`.
  - `ranking_blocked_reason` ∈
    {`""`, `stale_confluence_day_artifact`,
    `missing_confluence_day_artifact`,
    `missing_multitimeframe_trafficflow_bridge`,
    `insufficient_trafficflow_k_coverage`,
    `health_report_blocked`,
    `confluence_agreement_unavailable`}.

These are the explicit fields a future cross-ticker
ranking layer (Phase 6I-3, see § 6) can sort on, replacing
the manual "paste into AI and ask it to weight Sharpe and
p-value" step.

## 5. What is already automated today (post Phase 6H / 6I-1)

| Capability | Module | Status |
|---|---|---|
| Cache-vs-cutoff inequality probe | `cache_cutoff_watcher.py` | ✓ Phase 6H-2 |
| Per-ticker automation plan (cache + StackBuilder inventory + MTF libs + Confluence presence + recommended action) | `daily_board_automation_preflight.py` | ✓ Phase 6H-3 |
| Dry-run executor with refresh → recheck → pipeline sequencing | `daily_board_automation_executor.py` | ✓ Phase 6H-4 |
| Guarded live writer (two-key auth: `--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`) | `daily_board_automation_writer.py` | ✓ Phase 6H-5 |
| `status_dir` plumbing so authorized rehearsals stay temp-isolated | `daily_board_automation_writer.py --status-dir` | ✓ Phase 6H-6 |
| Operator runbook + machine-readable command manifest | `2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md` + `..._OPERATOR_COMMAND_MANIFEST.json` | ✓ Phase 6H-7 |
| Per-ticker source-cache refresh (non-interactive replacement for Spymaster batch processing) | `signal_engine_cache_refresher.refresh_signal_engine_cache` | ✓ Phase 6E-5 |
| Per-ticker Phase 6D pipeline (daily K → MTF K → Confluence) | `confluence_pipeline_runner.run_confluence_pipeline_for_ticker` | ✓ Phase 6D-4 |
| Per-ticker readiness verdict | `confluence_pipeline_readiness.inspect_ticker_pipeline` | ✓ Phase 6C-8 |
| Per-ticker Confluence ranking data contract (7 checks) | `confluence_ranking_contract_validator.py` | ✓ Phase 6I-1 |
| Per-ticker `board_row_preview` ready to feed a cross-ticker ranker | `confluence_ranking_contract_validator.TickerRankingContractValidation.board_row_preview` | ✓ Phase 6I-1 |

The operator can replace steps 1–4 of the old workflow
**today** via the Phase 6H-7 runbook recipe (one ticker at
a time, two-key auth, all roots redirected to operator
paths). Step 5 produces a structurally-correct ranking row
per ticker via the validator.

## 6. What is still missing

The replacement chain is complete per-ticker; the gaps are
the parts of the old workflow that were inherently
multi-ticker / batch-scale and have not been re-built yet.

### 6.1 Universe-scale discovery (replacement for old step 2)

The old TrafficFlow auto-discovery enumerated every
member ticker across every saved StackBuilder leaderboard
and reported the union (~1,000 entries). The new stack
intentionally rejects that pattern at the operator level
("never invoke any automation command with --all-tickers,
--universe, or any wildcard pattern" per Phase 6H-7
runbook § 9). A future phase should add a **read-only
universe-coverage report** that:

  - Walks every leaderboard-bearing StackBuilder seed-run
    directory under `output/stackbuilder/`.
  - Enumerates every member ticker that appears in any
    combo row.
  - Reports per-ticker readiness (already covered by the
    Phase 6H-3 preflight) for the union set.
  - Emits a structured **pilot ticker list** the operator
    can hand to the writer one ticker at a time, OR a
    deliberate sub-list for a supervised batch.
  - Never invokes a writer itself.

This is a READ-ONLY operator tool; it does not introduce a
universe-sweep writer. The two-key gate stays.

### 6.2 Cross-ticker ranking + sort (replacement for old step 6)

The Phase 6I-1 validator produces a `board_row_preview`
per ticker. There is no module that:

  - Loads the per-ticker board_row_preview set,
  - Sorts by a composite score (e.g. `(rank_eligible,
    agreement_ratio, abs(consensus_signal_value),
    coverage == "Full")` with deterministic tie-breaks),
  - Emits the top-N (or full) ranking as a structured
    JSONL / Markdown table the operator (or an AI
    assistant) can read directly.

This is the natural Phase 6I-3 module: **cross-ticker
ranking emitter**. It would be read-only (no writes),
take an explicit ticker list, call the validator per
ticker, and emit the sorted output.

### 6.3 Persistent execution log audit dashboard

The writer's `--execution-log <path>` JSONL is the new
audit trail (Phase 6H-5). There is no module that:

  - Reads the JSONL over a window of dates,
  - Identifies repeat `refresh_executed_pipeline_withheld`
    or `watcher_exception` patterns,
  - Surfaces the recurring-issue ticker list to the
    operator.

This is the audit-side companion to the writer; could
ship as Phase 6I-4 or roll into a later validation-ledger
extension.

### 6.4 Scheduler + supervised first production run

Phase 6H-7 § 11 named this as the load-bearing
operational gap before automation can ride a scheduler:

  - UTC-aware scheduler with idempotent retries against
    the cache-vs-cutoff gate.
  - Alerting on recurring `refresh_executed_pipeline_withheld`
    across more than one trading day for the same ticker.
  - Phase 5G data-licensing pre-launch gate.
  - Phase 5C `validation_contract_v1` sidecar integration
    so every authorized write feeds the
    `honest_validation_ledger.py` aggregator.
  - First authorized supervised production run (one
    ticker, operator at the keyboard).

These are operational decisions, not engineering work the
migration map can prescribe.

## 7. Multi-stack-per-ticker representation (no age window)

Phase 6H-3 ratified that saved StackBuilder variants are
**durable** inputs to the daily automation. The same
contract carries forward verbatim in this migration map.
Specifically:

  - Saved variants under
    `output/stackbuilder/<TICKER>/<seed_run_id>/` are
    research artifacts produced by the operator-driven
    StackBuilder workflow. They do NOT expire by age.
  - A ticker may have **multiple** saved variants from
    different OnePass seeds / parameter sweeps. All are
    valid.
  - The Phase 6D pipeline default
    (`trafficflow_k_artifact_builder.discover_latest_stackbuilder_run`)
    picks newest-mtime. That default is **preserved** by
    the Phase 6H-3 planner and the Phase 6I-1 validator
    via `stackbuilder_selection_policy =
    latest_mtime_existing_pipeline_default`.
  - A single saved variant is **OK**.
  - Tied newest-mtime is **`ambiguous_tied_mtime`** and
    blocks automation. The operator must pick a variant
    out of band. This is the only place stack selection
    blocks the chain.
  - **There is no 30-day stale window. There is no
    age-based stale rule. Stack age alone is never a
    block.** The Phase 6I-1 validator includes a static
    source-code guard (`STACKBUILDER_AGE_DAYS` /
    `STACKBUILDER_STALE_DAYS` / "30 days" / "thirty days"
    are all forbidden substrings) so a future PR cannot
    silently introduce one.

### 7.1 Future explicit-stack-selection contract

When automation needs to pin a specific saved variant
across runs (rather than relying on newest-mtime), a
later phase should ship:

  - A `stackbuilder_run_selection_policy` config (per
    ticker or per universe) with values like:
      * `"latest_mtime_existing_pipeline_default"` (today)
      * `"pinned_run_id"` + `pinned_run_id: str`
      * `"highest_combined_capture"` + tie-break rule
      * `"operator_choice"` (manual)
  - The pipeline runner reads the config, not the
    filesystem mtime.
  - The Phase 6H-3 planner + Phase 6I-1 validator surface
    the active policy in
    `stackbuilder_selection_policy` so the operator can
    audit what was used.

This work is **NOT in scope for Phase 6I-2**. The
migration map only documents the future contract shape.

## 8. Proposed next implementation phases (after Phase 6I-2)

In rough operator-priority order. **None of these are
blocked by 6I-2 itself; they are the next functional
deltas the map exposes.**

### Phase 6I-3 — Cross-ticker Confluence ranking emitter

  - Module: `confluence_ranking_emitter.py` (read-only).
  - API: `emit_confluence_ranking(tickers, *, ...) ->
    RankingTable`.
  - Calls the Phase 6I-1 validator per ticker, collects
    `board_row_preview` set, sorts deterministically by
    a composite score, emits JSONL / Markdown.
  - CLI: `python confluence_ranking_emitter.py --tickers
    SPY,AAPL,...`.
  - No writes. No subprocess. No engine import.

### Phase 6I-4 — Read-only universe-coverage report

  - Module: `stackbuilder_universe_coverage.py` (read-only).
  - Walks every leaderboard-bearing StackBuilder seed-run
    dir; enumerates the member-ticker union.
  - Cross-references with the Phase 6H-3 preflight for
    per-ticker readiness.
  - Emits a structured pilot-ticker list the operator can
    hand-feed (one ticker at a time) to the writer.
  - Replaces the old `trafficflow.get_all_missing_pkls_all`
    discovery surface with the new contract.
  - **Does NOT invoke any writer.** Two-key gate stays.

### Phase 6I-5 — Execution-log audit dashboard

  - Module: `automation_execution_log_audit.py` (read-only).
  - Loads the writer's `--execution-log` JSONL stream.
  - Identifies recurring `refresh_executed_pipeline_withheld`
    or `watcher_exception` patterns across days/tickers.
  - Emits operator-facing summary + Phase 5C
    `validation_contract_v1` sidecar hand-off.

### Phase 6I-6 — Explicit StackBuilder selection contract

  - Adds `stackbuilder_run_selection_policy` config
    surface per § 7.1.
  - Pipeline runner reads the config; planner + validator
    report the resolved policy.
  - No age window introduced.

### Phase 6I-7 (operational, not engineering) — First supervised authorized production run

  - Phase 5G data-licensing decision.
  - Phase 5C validation integration.
  - Scheduler + retry + alerting wiring.
  - First single-ticker authorized run with operator
    supervision.

## 9. Confirmation no production writes were run in Phase 6I-2

  - **No code change.** Phase 6I-2 ships one new doc; no
    `.py`, no test, no `.json` artifact, no `.gitignore`
    rule. The audit trail across the four open
    workstreams (TrafficFlow, Spymaster batch,
    StackBuilder discovery, new automation modules) was
    performed by reading source files and the existing
    Phase 6H / 6I-1 docs only.
  - **No production refresh.** No yfinance fetch occurred
    during the audit.
  - **No pipeline write.** No StackBuilder run, no
    OnePass run, no Spymaster batch processing.
  - **No `git checkout`, `git restore`, or any destructive
    git operation** that could have wiped operator state.
  - **No deletion of any production cache / output /
    signal_library file.** Even the audit grep commands
    were read-only.

## 10. Reference paths

### Old workflow surfaces (audited; unchanged)

  - `project/trafficflow.py`:
      - `get_all_missing_pkls_all` (line 2933) — missing-PKL discovery.
      - `scan_missing_stale_pkls` (line 533) — UI summary helper.
      - `build_board_rows` (line 2956) — single-K metric table.
      - `_classify_pkl_freshness` (around line 836) — per-ticker freshness rule.
  - `project/spymaster.py`:
      - `batch-ticker-input` textarea + `batch-process-button` (lines 6429-6442).
      - `batch_process_tickers` callback (line 11823).
      - `process_ticker_queue` background worker.
      - `save_precomputed_results` (line 4607) — atomic cache writer.

### New replacement modules (all read-only by default; writes only via Phase 6H-5 two-key gate)

  - `project/cache_cutoff_watcher.py` (Phase 6H-2).
  - `project/daily_board_automation_preflight.py` (Phase 6H-3).
  - `project/daily_board_automation_executor.py` (Phase 6H-4).
  - `project/daily_board_automation_writer.py` (Phase 6H-5 + 6H-6).
  - `project/signal_engine_cache_refresher.py` (Phase 6E-5).
  - `project/confluence_pipeline_runner.py` (Phase 6D-4).
  - `project/confluence_pipeline_readiness.py` (Phase 6C-8).
  - `project/confluence_ranking_contract_validator.py` (Phase 6I-1).

### Cross-references

  - Phase 6H-7 runbook + manifest:
    `project/md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`
    + `project/md_library/shared/2026-05-12_PHASE_6H7_OPERATOR_COMMAND_MANIFEST.json`
  - Phase 6H-6 root-plumbing doc:
    `project/md_library/shared/2026-05-12_PHASE_6H6_LIVE_WRITER_ROOT_PLUMBING.md`
  - Phase 6H-5 guarded-write executor doc:
    `project/md_library/shared/2026-05-12_PHASE_6H5_GUARDED_WRITE_EXECUTOR_FOUNDATION.md`
  - Phase 6I-1 ranking-contract-validator doc:
    `project/md_library/shared/2026-05-12_PHASE_6I1_CONFLUENCE_RANKING_CONTRACT_VALIDATOR.md`
  - Phase 6G-5 persist-skip-lag contract:
    `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md`
    § 6.8 and
    `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md`
    § 7.
