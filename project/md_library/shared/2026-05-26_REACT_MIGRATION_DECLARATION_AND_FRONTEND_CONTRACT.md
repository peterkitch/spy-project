# PRJCT9 React Migration Declaration and Frontend Contract

**Date:** 2026-05-26

**Status:** Authoritative for the eventual React migration of the PRJCT9 launch surface. Defines the trigger condition for migration and the behavioral and data contract the React app must satisfy. Does NOT initiate the migration. The Dash app (`mvp_signal_board.py`) remains the active validation and launch surface until the trigger condition is met.

**Anchor documents:**

- 2026-05-25 MVP Ranking Contract (PR #325).
- 2026-05-26 MVP v0 ranking engine (PR #326).
- 2026-05-26 MVP v0 Dash front-end (PR #327).
- 2026-05-26 MVP v0 Dash front-end live operator fixes (PR #328).
- 2026-05-04 PRJCT9 North Star.
- 2026-05-25 Confluence Terminology Glossary (PR #323).

---

## Purpose

This document records the decision to rebuild the PRJCT9 launch surface on a React static-site platform after MVP v1 is fully working. It specifies the trigger condition that initiates migration, the data contract the React app will consume, and the behavioral contract the React app must satisfy.

It does NOT specify visual design, look and feel, color palette, typography, component library, framework, hosting platform, or publish pipeline. Those choices belong to the React build phase and are intentionally deferred.

Every future React implementation prompt, scoping doc, and audit prompt should cite this document.

---

## Decision

The PRJCT9 launch surface will be rebuilt as a React static site. The current Dash app (`mvp_signal_board.py`, port 8062) is an operator-grade validation surface. It exists to validate engine output, artifact shape, ranking behavior, and interaction requirements before the production front-end is rebuilt.

Rationale:

- A static React site hosted on a CDN platform is simpler to operate than a long-running Python Dash server.
- Static hosting has better scale and reliability characteristics for a public read-only board.
- React offers stronger options for polished interactivity, mobile responsiveness, charting, routing, and product-grade UI.
- Future user-write functionality, if pursued in Phase 7+, is easier to add around a React frontend than to retrofit into Dash.
- The Python ranking engine is framework-agnostic. It emits JSON artifacts that any frontend can consume.

This is not a tentative "maybe React someday" note. React is the planned launch platform once the trigger condition is met.

---

## Trigger Condition

The React migration begins only when ALL of the following are true:

1. The v1 history artifact exists and is emitted by Phase E, or a Phase E companion writer, under the canonical run root alongside the existing Phase E outputs. This is Phase 3a of the MVP Ranking Contract implementation plan.

2. The v1 ranking engine is implemented and validated. It computes the five-timeframe match rule, sign-applied per-bar captures, BUY/SHORT recommendation derived from K=6 total capture sign, v1 Sharpe over matching bars, and the CCC time series. This is Phase 3b.

3. The v1 Dash front-end renders the BUY/SHORT recommendation, current alignment state tuple, CCC chart, and v1 metrics. This is Phase 3c.

4. The operator has personally verified that the v1 metrics and CCC chart correctly reflect the MVP Ranking Contract Steps v1.1 through v1.9 against real Phase E data for the eight Phase 6I-79 secondaries.

Until all four are true, no React implementation begins. The Dash app remains the active validation surface.

When all four are true, the next implementation PR may begin the React rebuild. The Dash app does not retire immediately. Dash and React coexist during transition until the React app reaches behavioral parity and the operator declares cutover.

### Trigger Condition Amendment (post K=6 MTF launch path / post Identity Correction)

The original four-item trigger condition above predates the **Identity Correction** in PR #335 (recorded in `md_library/shared/2026-05-25_MVP_RANKING_CONTRACT.md` Identity Correction amendment), which reclassified the earlier v1 surface as **OnePass Multi-Timeframe** rather than the operator-intended stack-multi-timeframe launch path. The original trigger paragraph above is **superseded for the current website launch path** but is preserved as historical record of the pre-Identity-Correction v1 definition.

For the current website launch path, the React migration trigger is **reconciled to the K=6 MTF MVP launch path** as locked in `md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`. The reconciled trigger requires ALL of the following:

1. The K=6 MTF history producer is implemented and emits `k6_mtf_history_v1` artifacts per secondary under `output/k6_mtf/<RUN_TIMESTAMP>/<SEC>/k6_mtf_history.json` (PR #339 chain).

2. The K=6 MTF ranking engine is implemented and consumes `k6_mtf_history_v1` artifacts only, emitting `k6_mtf_ranking_v1` at `output/k6_mtf/<RUN_TIMESTAMP>/k6_mtf_ranking.json` per the launch-path contract (PR #340 chain).

3. `mvp_signal_board.py` renders the `k6_mtf_ranking_v1` schema (PR #341 / PR #364 chain) and is the live operator-facing surface.

4. The operator has personally verified that the K=6 MTF metrics, CCC chart, and ranking correctly reflect the K=6 MTF launch-path contract against the operator-authorized live artifact at `output/k6_mtf/20260528T083411Z_post_fix/k6_mtf_ranking.json` for the 8 Tier 1 secondaries (AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY, TSLA).

This amendment **does not authorize compute**, does not authorize the React rebuild itself, and **does not change React's artifact-boundary rules or any of the Forbidden Behaviors** below. Dash (`mvp_signal_board.py`) remains the operator cockpit / prototype until React. The artifact remains the stable boundary; the React app reads only the ranking artifact and never calls the Python ranking engine, reads raw signal libraries, or reads price caches at runtime.

This amendment also acknowledges that the Phase 5 honest validation report (per `md_library/shared/2026-05-04_PRJCT9_NORTH_STAR.md` Phase 5 honest-validation paragraph) remains a separate **public-credibility gate before public launch** that is independent of, and in addition to, the React migration trigger above.

---

## Scope Boundaries

This document defines the contract for the ranked-board launch surface only. It does NOT cover:

- Operator-facing engine Dash apps such as Spymaster, OnePass, ImpactSearch, StackBuilder, TrafficFlow, Confluence, cross-ticker dashboards, research previews, or legacy board tools.
- TrafficFlow Producer A / Producer B Path 3b work.
- The v1 history artifact schema, filename, or writer location.
- Visual design.
- Backend hosting choice for published JSON artifacts.
- Authentication, user accounts, crowdsourcing, or premium-tier product work.

---

## Architecture Target

The target architecture is:

1. Python engines run offline on a scheduled cadence.
2. Phase E canonical output feeds the MVP ranking engine.
3. The ranking engine writes a schema-stamped JSON ranking artifact.
4. A publish step copies the latest approved ranking artifact to a public-readable static asset location. The publish step is deferred and not specified here.
5. The React static site fetches the published JSON artifact at runtime and renders the board and detail surface client-side.
6. There is no Python server in the request path, no backend database for MVP, and no live recomputation.

The Python engine remains the source of truth for scoring. The frontend is a pure consumer of emitted artifacts.

---

## Data Contract

The React app reads exactly one input artifact for the board.

For v0, the input is `mvp_ranking_v0.json`, emitted by `mvp_ranking_v0.py`.

Top-level v0 fields:

- `schema_version` (string, `"mvp_ranking_v0"`)
- `generated_at_utc` (UTC timestamp)
- `ranking_status` (string)
- `trafficflow_run_root` (relative path)
- `trafficflow_run_id` (string)
- `trafficflow_orchestrator_invocation_id` (string or null)
- `trafficflow_run_status` (string or null)
- `secondaries_requested` (array of ticker strings)
- `secondaries_ranked` (array of ticker strings, in display order)
- `per_secondary` (array of records)
- `issues` (array, possibly empty)

Per-secondary v0 records include:

- `rank` (integer)
- `secondary` (string)
- `k` (integer, must equal 6)
- `members` (array of strings)
- `triggers` (integer)
- `wins` (integer or null)
- `losses` (integer or null)
- `win_pct` (numeric or null)
- `stddev_pct` (numeric or null)
- `sharpe` (numeric)
- `p_value` (numeric or null)
- `avg_capture_pct` (numeric or null)
- `total_capture_pct` (numeric)
- `phase_e_status` (object, possibly empty)
- `low_sample_warning` (boolean)

For v1, the React app consumes a v1 ranking artifact whose exact schema is deferred until Phase 3b lands. The v1 schema should include, at minimum, all v0 fields plus:

- `trade_direction` (string, `BUY` or `SHORT`)
- `current_alignment_state` (object with 1d, 1wk, 1mo, 3mo, 1y keys)
- v1 match-rule metrics
- v1 sample size
- v1 win / loss metrics
- CCC time series

The React app must not assume the v1 schema is finalized until the v1 scoping PR lands.

The React app reads ONLY the ranking artifact. It does NOT:

- Call the Python ranking engine at runtime.
- Read Phase E artifacts directly.
- Read raw signal libraries, price caches, or cache PKLs.
- Run any pipeline component.

The artifact is the stable boundary. If the React app needs a field not present in the artifact, the correct fix is to extend the engine and re-emit the artifact.

---

## Behavioral Contract

The React app must reproduce the behavior validated by the Dash app at the corresponding feature level. Visual treatment is deferred.

For v0:

1. Landing view displays a ranked table of secondaries.
2. Display order is the `per_secondary` order from the artifact. The React app must not re-sort, re-rank, or re-filter.
3. The visible board columns are exactly: Rank, Ticker, Sharpe Score.
4. Sharpe Score displays the engine-emitted `sharpe` value formatted to two decimals. No sign flipping, recomputation, or magnitude-only display.
5. The landing view does not display Phase E status, total capture, triggers, low-sample warning, provenance, generated timestamp, or disclaimer below the table.
6. Each row is clickable.
7. Clicking a row opens a detail view. The visual form may be modal, drawer, overlay, or route, but it must preserve the detail behavior.
8. The detail view displays:
   - Ticker / secondary
   - Member ticker list
   - K=6 metrics: Sharpe, total capture %, triggers, wins, losses, win %, avg capture %, stddev %, p-value
   - Low-sample warning value
   - Phase E status key/value pairs
   - Provenance: Source Phase E run, trafficflow run root, Ranking generated at
   - Disclaimer: Historical performance does not guarantee future returns.
9. Clicking the same row again closes the detail view if the chosen interaction model supports row toggling. If the React implementation uses a route-based detail page instead, it must provide an equally clear close / back behavior.
10. Clicking a different row switches the detail view to that row.
11. A close affordance closes the detail view.
12. Empty `per_secondary` renders a clear empty state.
13. Missing or unreadable artifact renders a clear error state. The app must not compute, fabricate, or partially synthesize data.
14. Missing optional fields render as `Unavailable` or an equivalent placeholder.

For v1:

15. Rows additionally display trade direction from the engine-emitted `trade_direction` field.
16. Detail view additionally displays current alignment state across 1d, 1wk, 1mo, 3mo, 1y.
17. Detail view additionally displays v1 metrics.
18. Detail view renders the engine-emitted CCC series as the hero chart.
19. v1 ranking uses the engine-emitted v1 ranking order. The React app still must not re-sort unless the artifact explicitly provides sorted order for the selected view.

---

## Forbidden Behaviors

The React app must NOT:

- Sign-flip Sharpe or capture values.
- Derive BUY/SHORT recommendations in the frontend.
- Recompute Sharpe, capture, win %, p-value, CCC, or any metric.
- Run match-rule scoring in the frontend.
- Call the Python ranking engine at runtime.
- Read Phase E artifacts directly.
- Read raw signal libraries, price caches, or cache PKLs.
- Display data not present in the consumed artifact.
- Make claims based on hypothetical direction changes not emitted by the engine.

If implementation pressure points toward any forbidden behavior, stop and extend the engine / artifact instead.

---

## What This Document Does NOT Decide

This document does not decide:

- React framework choice.
- Component library.
- Styling approach.
- State management.
- Hosting platform.
- Publish step design.
- CI/CD.
- Visual design.
- Mobile breakpoints.
- Accessibility implementation details.
- Internationalization.
- Authentication or user-write functionality.

Those belong to the React build phase after the trigger condition is met.

---

## Implications for Current Work

For v0 and v1 Dash work:

- Dash remains the active validation surface.
- Do not over-polish Dash beyond what helps operator validation.
- Keep the ranking engine and artifact schema clean, because React will consume the same boundary.
- UI changes that alter the display contract should be reflected in the MVP Ranking Contract by documentation-only PR.

For v1 Phase 3 work:

- Phase 3a history artifact work is unaffected.
- Phase 3b ranking engine work should emit the v1 artifact that React will later consume.
- Phase 3c Dash UI validates the v1 behavior before React starts.

For post-trigger React work:

- The first React scoping PR cites this document.
- Dash and React coexist during transition.
- Cutover requires operator-declared behavioral parity.

---

## References

- 2026-05-04 PRJCT9 North Star.
- 2026-04-30 PRJCT9 Sprint Plan.
- 2026-05-25 Confluence Terminology Glossary (PR #323).
- 2026-05-25 Known Bugs Log (PR #324).
- 2026-05-25 MVP Ranking Contract (PR #325).
- 2026-05-26 MVP v0 ranking engine (PR #326).
- 2026-05-26 MVP v0 Dash front-end (PR #327).
- 2026-05-26 MVP v0 Dash front-end live operator fixes (PR #328).
