# PRJCT9 MVP Ranking Contract

**Date:** 2026-05-25

**Status:** Authoritative for the current MVP sprint. Defines what the MVP displays, what it computes, and what it explicitly does not compute. Supersedes earlier ambiguity about MVP scope or scoring math. Does not supersede the broader North Star vision.

**Anchor documents:**

- 2026-05-25 Confluence Terminology Glossary (PR #323).
- 2026-05-25 Known Bugs Log (PR #324).
- 2026-05-04 PRJCT9 North Star.
- 2026-04-30 PRJCT9 Sprint Plan.
- 2026-05-25 TrafficFlow K-Artifact Producer Reconciliation audit.
- 2026-05-25 TrafficFlow Runner Phase E Canonical Write Contract evidence.

---

## Purpose

This document specifies the MVP Ranking Contract: the math the MVP computes, the data flow that feeds it, and the display surface that shows it.

It exists because earlier sprint work produced multiple partial implementations of overlapping concepts without a single authoritative specification of what the MVP actually computes or displays. Future MVP implementation prompts, scoping documents, audit prompts, and operator runbooks should reference this document as the canonical source of truth for what the MVP includes, what it excludes, and what it computes.

---

## Naming Convention Note

This document is named "MVP Ranking Contract" rather than "Canonical Scoring Specification" to avoid collision with the existing `canonical_scoring.py` module. Use "ranking contract" or "MVP ranking" when referring to this MVP-specific display and ranking layer.

---

## Relationship To Existing Confluence Concepts

The 2026-05-25 Confluence Terminology Glossary defines four operator-facing concepts that share the name "confluence." The MVP Ranking Contract is not one of those four concepts. It is a new ranking layer that sits alongside them and consumes TrafficFlow Phase E canonical output.

Future prompts and docs should refer to it as the "MVP ranking" or "MVP Ranking Contract." Do not call it "Concept 5" and do not fold it into the existing glossary numbering.

---

## What The MVP Is

A single Dash-rendered ranked board displaying eight secondaries, sorted by Sharpe ratio descending, with click-to-modal per-ticker detail views.

The eight secondaries are the Phase 6I-79 tickers that currently have completed K=6 stack builds:

    SPY, AAPL, AMZN, GOOGL, META, MSFT, NVDA, TSLA

The MVP ships in two versions:

- **MVP v0** is the minimum shippable version. It uses only fields that Phase E currently emits in `board_rows_k=6.json`. The board ranks by K=6 Sharpe descending as emitted. No sign-flipping, no BUY/SHORT recommendation, no CCC chart, no match-rule scoring.
- **MVP v1** is the full vision. It adds five-timeframe match-rule scoring, sign-applied capture computation, BUY/SHORT recommendation logic, and a Cumulative Combined Capture chart. It requires new work beyond what Phase E currently emits.

Both versions read from Phase E canonical output. v0 ships first; v1 follows. This document specifies both so the sequencing is clear from the start.

---

## The MVP v0 Ranking Math

For each of the eight secondaries, MVP v0 computes only from fields present in Phase E's existing `board_rows_k=6.json` output.

### v0 Honesty Principle

v0 displays Phase E values as emitted. v0 does not:

- sign-flip Sharpe or capture values;
- derive BUY or SHORT trade recommendations from total capture sign;
- recompute any metric that would require per-bar daily returns;
- make any claim that depends on knowing what the strategy would have done under a hypothetical direction other than the one Phase E already computed.

If v0 needs any of those capabilities, the correct response is to wait for v1 with the new history artifact. v0 must not synthesize them.

### Step v0.1: Read Summary Metrics

From the K=6 board row for each secondary in Phase E's `board_rows_k=6.json`, read fields that Phase E actually emits, including:

- Sharpe ratio, as emitted;
- total capture %, as emitted;
- triggers / sample size, as emitted;
- wins;
- losses;
- win %;
- member ticker list;
- any current signal / status field Phase E emits.

The v0 implementation must inspect the actual Phase E schema during its own scoping PR. If a current signal / status field is absent, v0 displays that value as unavailable and does not synthesize one.

### Step v0.2: Low-Sample Warning

If triggers < 30, set `low_sample_warning` to true.

The warning surfaces as a `!` marker on the displayed row, consistent with the TrafficFlow convention for missing or degraded data. The threshold of 30 is an MVP placeholder documented here so it can be tuned later when real data informs the right value.

### Step v0.3: Rank

Sort the eight tickers by Sharpe descending, as emitted. Negative Sharpes appear at the bottom of the table. Positive Sharpes appear at the top.

This ranking does not promote strongly negative Sharpes under a "short candidate" framing. That framing requires sign-flipping math that v0 cannot honestly perform. v1 introduces that capability.

### Step v0.4: Display The Board

The board shows one row per secondary, sorted per v0.3. Each row contains the fields from v0.1 plus the v0.2 warning marker.

### v0 Detail Modal

Clicking any row opens a modal that displays:

- ticker symbol;
- member ticker list;
- current Phase E signal / status field if emitted, otherwise unavailable;
- K=6 metrics from v0.1;
- Phase E run provenance;
- a close button.

The v0 modal does not include a CCC chart.

---

## The MVP v1 Ranking Math

MVP v1 adds the five-timeframe match-rule scoring, sign-applied capture computation, BUY/SHORT recommendation logic, and CCC chart from the original user vision.

It requires data that Phase E does not currently emit: a per-secondary daily history artifact with five-window signal state and per-bar close prices. Adding that artifact is real implementation work. It is explicitly scoped here as future work.

### Step v1.1: Determine Trade Direction

Read K=6 total capture % from Phase E's `board_rows_k=6.json` for the secondary:

- positive total capture: trade direction is BUY;
- negative total capture: trade direction is SHORT;
- zero total capture: default to BUY and flag the row with a `!` warning marker.

This BUY/SHORT determination is only honest in v1 because v1 has the per-bar history needed to recompute sign-applied metrics. In v0, the sign of K=6 total capture is displayed as emitted without a BUY/SHORT recommendation overlay.

### Step v1.2: Establish Current Alignment State

Read the latest signal direction at each of the five timeframes from the v1 history artifact:

    1d, 1wk, 1mo, 3mo, 1y

Each timeframe yields one of:

    BUY, SHORT, NONE

The result is a five-tuple describing the current state.

### Step v1.3: Walk Historical Bars

For each historical bar in the secondary's daily price series, as provided by the v1 history artifact, look up the five-timeframe signal state on that bar. This produces a historical five-tuple per bar.

### Step v1.4: Apply The Match Rule

A historical bar matches the current state if and only if:

- for every timeframe where the current signal is BUY, the historical signal is also BUY;
- for every timeframe where the current signal is SHORT, the historical signal is also SHORT;
- for every timeframe where the current signal is NONE, the historical signal is unconstrained;
- for every timeframe where the historical signal is NONE, that timeframe is unconstrained regardless of the current signal.

NONE on either side at any timeframe is a wildcard pass. Non-NONE values on both sides must match exactly.

### Step v1.5: Compute Per-Bar Capture On Matching Bars

For each matching historical bar, apply the trade direction from v1.1.

If direction is BUY:

    capture = (next close / current close - 1) * 100

If direction is SHORT:

    capture = -1 * (next close / current close - 1) * 100

If the next close is missing or invalid, the capture on that bar is zero and that bar does not count as a trigger.

The SHORT case is the sign-flip operation that v0 cannot perform. It requires per-bar close prices, which the v1 history artifact supplies and which `board_rows` alone does not.

### Step v1.6: Compute v1 Metrics

Over only the matching bars where capture was successfully computed:

- sample size N;
- total capture %;
- average capture %;
- Sharpe ratio;
- win count;
- loss count;
- win %.

Sharpe ratio uses:

    average per-bar capture / standard deviation of per-bar captures * sqrt(252)

The annualization factor follows the daily-bar convention.

### Step v1.7: Low-Sample Warning

If N < 30, set `low_sample_warning` to true. This uses the same threshold and warning marker as v0.2.

### Step v1.8: CCC Time Series

For each secondary, compute the cumulative sum of per-bar captures from v1.5, indexed by calendar date. This is the Cumulative Combined Capture time series. The v1 modal renders the CCC chart as the hero element.

### Step v1.9: Rank

Sort the eight tickers by v1.6 Sharpe ratio descending. The v1 ranking uses match-rule Sharpe with sign-applied captures, not the K=6 Phase E Sharpe. v0 and v1 may produce different rankings.

### v1 Detail Modal

In addition to all v0 modal fields, the v1 modal shows:

- trade direction, BUY or SHORT;
- current alignment state tuple;
- CCC time series chart;
- v1 metrics from v1.6;
- sample size N and `low_sample_warning` if applicable.

---

## Data Inputs

This section is load-bearing.

The MVP reads only from the sources specified here. The MVP does not read raw signal libraries directly. It does not read price cache CSVs directly. It does not read Spymaster cache PKLs directly.

### v0 Inputs

v0 inputs are:

- Phase E canonical run discovered via `output/trafficflow/selected_output.json`;
- per-secondary `board_rows_k=6.json` from the discovered run;
- per-secondary `secondary_manifest.json` for provenance;
- run-level `run_manifest.json` for run provenance.

That is the full v0 input list. v0 ships using only what Phase E already produces today.

### v1 Inputs

v1 requires the v0 inputs plus a new per-secondary artifact that does not exist today. The new artifact must contain, per secondary:

- per-bar daily history covering the secondary's available date range;
- five-timeframe signal state on each historical bar;
- secondary close price on each historical bar;
- sufficient data to compute v1.3 through v1.8, including SHORT sign-flipped capture.

The new artifact must be emitted by Phase E or a Phase E companion writer under the canonical run root, alongside the existing `board_rows_k=6.json`. It must be schema-stamped and discoverable from the selected Phase E run.

Adding this artifact is the v1 prerequisite. The work to add it is scoped as a separate PR, not part of this documentation effort and not part of v0 shipping.

The MVP ranking engine is forbidden from reading raw signal libraries, price cache CSVs, or Spymaster cache PKLs directly to substitute for the missing v1 artifact. If those inputs are needed, the correct response is to emit them as a Phase E-derived artifact, not to bypass Phase E.

---

## Data Flow: The MVP Spine

This section follows the math sections deliberately. The math defines what the MVP computes; the spine describes how the data reaches the computation.

The MVP launch spine is:

1. StackBuilder `selected_build.json`.
2. Phase E TrafficFlow runner / orchestrator.
3. `output/trafficflow/runs/<UTC_TS>/<SEC>/board_rows_k=6.json`.
4. `output/trafficflow/runs/<UTC_TS>/<SEC>/secondary_manifest.json`.
5. `output/trafficflow/runs/<UTC_TS>/run_manifest.json`.
6. `output/trafficflow/selected_output.json`.
7. MVP ranking engine, new and scoped separately.
8. Per-secondary MVP ranking output, schema TBD in scoping PR.
9. MVP Dash front-end, new or retrofit and scoped separately.
10. Operator browser at the MVP port.

For v1, the spine includes an additional Phase E-derived artifact between steps 6 and 7:

    output/trafficflow/runs/<UTC_TS>/<SEC>/<v1_history_artifact_name>.json

The v1 history artifact's exact name, schema, and writer location are TBD and will be specified in the v1 scoping PR. This document does not commit to those details.

### What The Spine Does Not Include

The MVP spine does not include:

- the Phase 4 cross-ticker confluence engine;
- the `confluence.py` multi-primary Dash app;
- the Phase 6D research-artifacts chain;
- the Phase 6I multiwindow K engine chain;
- Producer A's `research_day_v1` artifact tree;
- TrafficFlow Producer A / Producer B Path 3b migration work.

These are explicitly not on the MVP spine. Path 3b remains the long-term architectural recommendation, but it is non-blocking for the MVP because the MVP uses Phase E canonical output directly.

---

## Display Contract

The MVP Dash front-end displays the following. The same behavioral contract applies to the future React rebuild per the 2026-05-26 React Migration Declaration and Frontend Contract (PR #329).

### Landing View

- Header: "PRJCT9 Daily Signal Board" (text only, no images).
- Subheader: "MVP v0" or a similar version indicator.
- Ranking DataTable with exactly three visible columns:
  - **Rank**: 1 through N where N is the count of successfully ranked secondaries.
  - **Ticker**: the secondary ticker symbol.
  - **Sharpe Score**: the engine-emitted Sharpe value formatted to two decimals. In v0 this is the Phase E K=6 Sharpe as emitted. In v1 this is the match-rule Sharpe with sign-applied captures from Step v1.6.
- No visible content below the DataTable on the landing view.

The landing view does NOT show:

- Phase E Status field.
- Total capture %.
- Triggers / sample size.
- Low-sample warning indicator.
- Source Phase E run id.
- Ranking generated_at timestamp.
- Historical-performance disclaimer.

This simplification reflects operator feedback that the landing surface is the ranking surface. Supporting detail and provenance belong in the detail modal so the at-a-glance ranking is not crowded.

### Detail Modal

- Opens on row click.
- Renders as a true overlay: fixed-position container, semi-transparent backdrop, centered panel above the page content. The backdrop covers the viewport; the panel sits above it.
- Opening the modal must not push the landing view downward in normal document flow.

In the Dash implementation, the modal container, the inner panel, the modal content container, and the close button are all present in the initial layout from page load. The container is hidden when closed and visible when open; only the inner content container is rebuilt on each callback fire. This is a Dash-specific callback-graph requirement (callback Inputs must exist at page load), not a binding React implementation detail. A React rebuild may use a route-based detail page or a different overlay primitive as long as the behavioral contract below is preserved.

v0 modal content, in order:

1. **Ticker** as the modal title.
2. **Member ticker list**, comma-separated.
3. **K=6 metrics**:
   - Sharpe, engine-emitted, two decimals.
   - Total %, engine-emitted, two decimals.
   - Triggers, engine-emitted, integer.
   - Wins, engine-emitted, integer.
   - Losses, engine-emitted, integer.
   - Win %, engine-emitted, two decimals.
   - Avg %, engine-emitted, two decimals.
   - StdDev %, engine-emitted, two decimals.
   - p-value, engine-emitted, four decimals.
   - `low_sample_warning`, boolean displayed verbatim.
4. **Phase E Status** section:
   - Render each key/value pair from `phase_e_status`.
   - If `phase_e_status` is empty, display "No Phase E status fields emitted for this secondary."
5. **Provenance** section:
   - "Source Phase E run: <trafficflow_run_id>".
   - `trafficflow_run_root` (repo-relative path).
   - "Ranking generated at: <generated_at_utc>".
6. **Disclaimer** as the final modal body element:
   - "Historical performance does not guarantee future returns."
7. **Close button** in the modal panel.

v1 modal content extends v0 with the following additions:

- **Trade direction**, BUY or SHORT, derived per Step v1.1.
- **Current alignment state tuple**, rendered from the engine-emitted state values for 1d, 1wk, 1mo, 3mo, 1y.
- **CCC time series chart** as the hero element of the v1 modal, positioned prominently after the alignment tuple.
- **v1 metrics** from Step v1.6: v1 Sharpe, v1 total capture %, v1 N, v1 win count, v1 loss count, v1 win %.

The v1 landing-board DataTable has exactly four visible columns, in this order:

- **Rank**: 1 through N where N is the count of successfully ranked secondaries.
- **Ticker**: the secondary ticker symbol.
- **Sharpe Score**: the engine-emitted `v1_sharpe` value formatted to two decimals.
- **Trade Direction**: the engine-emitted `trade_direction` value (BUY or SHORT) from Step v1.1.

The Sharpe Score column on the v1 landing view reflects the v1 match-rule Sharpe, not the v0 Phase E K=6 Sharpe. v0 and v1 may therefore produce different rankings; that is expected and not an error.

### Modal Toggle Behavior

- First click on a row opens the modal for that row.
- Click on the same row closes the modal. In the Dash implementation, clicking a different cell in the same row is the reliable same-row close gesture because clicking the exact same cell may not re-fire the underlying DataTable signal.
- Click on a different row while the modal is open switches the modal content to the new row.
- The close button closes the modal regardless of click history.

### Error and Edge States

- If the ranking artifact path does not exist, render a clear error message ("Ranking artifact not found.") in place of the table. Do not crash. Do not attempt computation.
- If the artifact JSON is unreadable or malformed, render an error message ("Ranking artifact unreadable. See console output.").
- If the artifact `schema_version` is not the expected value, render a message naming both expected and actual schema strings.
- If `per_secondary` is empty, render the header and subheader with an empty-state message ("No ranked secondaries available in this run.") in place of the table.
- If a `per_secondary` record is missing an optional field, render "Unavailable" in that field's display position in the modal rather than crashing.

### Amendment History

This Display Contract section was amended on 2026-05-26 to reflect operator-accepted refinements from live testing of PR #327 and PR #328: true modal overlay behavior, landing-board column simplification to Rank / Ticker / Sharpe Score, removal of the landing-view footer, and relocation of provenance plus the historical-performance disclaimer into the modal. The ranking math sections and data input sections are unchanged from the original 2026-05-25 publication.

Amendment 2026-05-27: column order adjusted to place Sharpe Score before Trade Direction based on operator live verification preference.

---

## What The MVP Is Not

This section is load-bearing.

The MVP does not include:

- universe expansion beyond the eight Phase 6I-79 secondaries;
- 250-500 ticker scale;
- crowdsourcing;
- user accounts;
- login;
- user-write functionality;
- premium subscription tier;
- backend database;
- real-time signal recomputation;
- research or exploration surfaces;
- Concept 1, Concept 2, or Concept 3 launch surfaces;
- direct reads from raw signal libraries;
- direct reads from price cache CSVs;
- direct reads from Spymaster PKLs;
- BUY/SHORT recommendation logic in v0;
- CCC chart in v0;
- sign-flipped or recomputed SHORT metrics in v0;
- fixes to BUG-001 or BUG-002 from the Known Bugs Log.

The MVP reads from on-disk Phase E artifacts. Signals are updated by scheduled Phase E pipeline runs, not by user requests.

---

## Implementation Status

### What Exists Today

- Pipeline through TrafficFlow Phase E is operational.
- Phase E canonical output exists for the eight Phase 6I-79 secondaries.
- Phase E emits `board_rows_k=6.json`, per-secondary manifests, run manifests, and the discovery pointer.
- Candidate front-end surfaces exist, including `daily_signal_board.py` and the static HTML chain, but neither currently implements this MVP Ranking Contract.

### What Does Not Exist Today

- MVP v0 ranking engine.
- MVP v1 ranking engine.
- v1 history artifact.
- MVP Dash front-end implementing this display contract.

### What Needs To Happen To Ship The MVP

Three implementation phases follow this documentation PR. Each gets its own scoping prompt.

**Phase 1: MVP v0 ranking engine.** Implement v0.1 through v0.4 in a new headless script. Read Phase E canonical output only. Emit a per-secondary ranking output. Honor the v0 honesty principle: no sign-flipping, no recommendation, no per-bar recomputation.

**Phase 2: MVP v0 Dash front-end.** Either retrofit `daily_signal_board.py` or build a new minimal Dash app that consumes the v0 ranking output and renders the board plus v0 modal per this display contract.

**Phase 3: MVP v1 expansion.** Sub-phases:

- add Phase E history artifact emission;
- extend the ranking engine to implement v1.1 through v1.9;
- extend the Dash front-end to add v1 modal fields and the CCC chart.

This document does not scope phases 1, 2, or 3. Each becomes a separate scoping prompt after this document lands.

---

## Open Architectural Questions Deferred Until After MVP

The following are deferred until after MVP:

- TrafficFlow Producer A / Producer B Path 3b implementation. The reconciliation audit's recommendation stands as the long-term architectural fix, but Path 3b is non-blocking for MVP because MVP reads Phase E board rows directly.
- True multi-window K engine vs projected MTF. The distinction matters for long-term research honesty but is not blocking MVP v0.
- BUG-001 and BUG-002 from the Known Bugs Log. Both are in Concept 1 code paths and not on the MVP launch path.
- Open source license choice.
- Premium tier monetization.
- Choice between retrofitting `daily_signal_board.py` and building a new MVP Dash app.

---

## References

- 2026-05-04 PRJCT9 North Star.
- 2026-04-30 PRJCT9 Sprint Plan.
- 2026-05-25 Confluence Terminology Glossary (PR #323).
- 2026-05-25 Known Bugs Log (PR #324).
- 2026-05-25 TrafficFlow K-Artifact Producer Reconciliation audit.
- 2026-05-25 TrafficFlow Runner Phase E Canonical Write Contract.
- 2026-05-25 TrafficFlow Runner Phase E Epsilon All-8 Canonical Smoke Evidence.
