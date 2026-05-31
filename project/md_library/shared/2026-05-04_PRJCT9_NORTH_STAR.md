# PRJCT9 North Star

**Date:** 2026-05-04

**Status:** Living document - directional, not prescriptive

**Author:** PRJCT9 sprint

**Purpose:** Capture the destination vision so every phase
can be evaluated against direction, not just internal
consistency.

**Status note (added post K=6 MTF MVP launch):** This North
Star is a **directional destination document**, NOT the
current sprint cursor. For the current sprint cursor read,
in order: `<PROJECT_DIR>/CLAUDE.md` Section 6;
`<PROJECT_DIR>/md_library/shared/2026-05-23_POST_PHASE_6I_SPRINT_CARRYFORWARD.md`
(carryforward ledger);
`<PROJECT_DIR>/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`
(current launch path);
`<PROJECT_DIR>/md_library/shared/2026-05-26_REACT_MIGRATION_DECLARATION_AND_FRONTEND_CONTRACT.md`
(next major phase);
`<PROJECT_DIR>/md_library/shared/2026-05-25_KNOWN_BUGS_LOG.md`
(deferred bugs). The Phase 5 honest-validation paragraph
at L189-L193 below remains **standing as a public-credibility
gate before public launch** and is NOT weakened by this
note; the local 8-ticker K=6 MTF MVP board may serve as the
operator cockpit while Phase 5 validation work and React
migration work continue in parallel.

## The vision

PRJCT9 becomes an open-source, public-facing pattern-finding
research tool - a Wikipedia of pattern finding. Anyone visits,
immediately understands what they're looking at, and recognizes
how it applies to their reality.

A soccer mom should be able to land on the site and understand
it. A quantitative researcher should be able to arrive and
understand it. Both should see how it applies to them.

The site is not a leaderboard, not a stock-picking service,
not a trading signal feed. It is a tool for finding patterns
in time-series data - defaulting to financial tickers as the
first and most-developed domain, but extensible toward any
time-series the user brings.

## Open-source research commons

PRJCT9 is an open-source research engine and public research
site. The code, schematics, assumptions, manifests, and output
provenance are inspectable by anyone. The project earns trust
by showing its work, not by asking users to trust a black box.

The PRJCT9 GitHub repository is already public. Users curious
about how the displayed results were generated can inspect,
audit, fork, and run the code themselves.

A discovered pattern should be shareable, revisitable, and
challengeable: users should be able to point to the inputs,
parameters, run date, manifest, coverage state, and output
that produced it. Over time, the site should support a growing
body of documented patterns that can be explored for fun,
critiqued by experts, and referenced for professional research.

## Two user paths

**Path 1: Curated default view.**

The site lands users on the top current confluence matches -
tickers with full required timeframe coverage where every
available timeframe (1d, 1wk, 1mo, 3mo, 1y) currently agrees
on signal direction. Full unanimity buy or full unanimity
short. Partial-coverage tickers may be shown only when clearly
labeled as partial.

For each surviving ticker, the site shows full historical
performance (charts, useful data) so users can see how that
ticker has historically behaved under PRJCT9's signal model.

The default view is a filtered current-match view, not a
promise that the listed tickers are "best." It shows where
the model currently sees full timeframe agreement and lets
users inspect the historical evidence.

**Path 2: Manual ticker entry.**

Users type any yfinance ticker (e.g., INTC). The site returns
the best available readout for that ticker from cached and
manifest-stamped data:

- The full stackbuild for INTC, if cached
- Multi-timeframe results (which intervals show buy, which
  show short, which show none), where libraries exist
- Current status report
- Historical performance based on current signals

When full stackbuild or interval data is missing, the site
explains the gap and may queue compute. Manual entry does not
promise instant full computation for arbitrary tickers.

The full validated yfinance ticker universe is searchable.

## Crowdsourcing direction

Long term, PRJCT9 supports crowdsourced pattern finding -
combining user intuition, imagination, and computing power
to discover and verify patterns at scale beyond what one or
two people can achieve alone.

Crowdsourcing manifests in stages:

- Stage 1 (current): The repo is public; anyone can fork and
  contribute code, ideas, or run the engine on their own data
- Stage 2 (post-sprint): Users can contribute compute power
  to help maintain or expand the public dataset
- Stage 3 (research commons): Discovered patterns are
  submitted, verified, documented, and referenced as a
  growing public knowledge base

Volunteer-contributed compute is part of the destination but
not part of the current sprint. Canonical public outputs
remain controlled until volunteer-produced artifacts can be
authenticated, validated, and safely incorporated.

## Bring-your-own-data direction

The engine's eventual reach extends beyond financial time
series to any time-series data a user wants to analyze:
weather, disease outbreaks, power grid behavior, sports
statistics, social patterns. The financial-ticker default is
where PRJCT9 starts; the broader research engine is where it
goes.

Whether the same engine code can transfer cleanly from
financial data to arbitrary user-provided time series is a
genuine architectural question that the sprint does not
attempt to solve. The sprint focuses on yfinance financial
data. Bring-your-own-data is a future direction.

## Accessibility principle

The site must be approachable for non-experts and respected
by experts. This means:

- Plain language explanations alongside technical detail
- Visual displays that communicate without requiring
  statistical literacy
- Statistical detail available for those who want it
- No assumed prior context about PRJCT9's signal model

Both ends of the user spectrum should land on the site and
see immediate value. Not the same value - the soccer mom sees
"here are tickers worth watching, here's why" while the
quantitative researcher sees "here are confluence matches
with Sharpe ratios, p-values, and historical performance" -
but both should see something useful within seconds.

## Product feel

The site should be fun, fast, and easy to begin using, while
still being difficult to master. A new user should get value
within seconds. A serious researcher should be able to keep
drilling into assumptions, methods, provenance, statistics,
and historical behavior. The exploration should feel
exciting without overstating certainty.

## Research-honesty guardrail

PRJCT9 should make exploration exciting without overstating
certainty. The site should distinguish observed historical
behavior from prediction. Every displayed result includes
its run date, sources, coverage state, and caveats. Coverage
gaps, stale data, and failed inputs are exposed as data,
not hidden.

## Engine vs presentation

PRJCT9 has two layers: the engine (data, signals, manifests)
and the presentation (the public site, the operator UIs).
These layers must remain separated:

- The engine produces durable manifest-stamped data
- The presentation reads that data and renders it
- Recomputation, refetching, or producer rebuilding happens
  in the engine layer, never in the presentation layer
- The engine does not know about the website; the website
  does not recompute engine outputs

This separation enables the bring-your-own-data and
crowdsourcing directions: the same engine outputs can feed
the public site, the operator UIs, volunteer-contributed
research, and any future user-driven research workflow.

## Sprint scope vs ultimate destination

The current sprint (Phases 4 through 6) delivers:

- Phase 4: manifest-stamped data layer aggregating existing
  engine outputs
- Phase 5: honest validation report, cleanups, controlled
  compute infrastructure, curated universe maintenance, and
  pre-launch hardening
- Phase 6: public PRJCT9.com presenting the engine's results
  on yfinance financial data

The original PRJCT9 sprint plan named Phase 5 as the Honest
Validation Report. This North Star preserves that requirement
as part of Phase 5 / pre-launch hardening; it is not dropped.
For PRJCT9 to stand as a respected research tool, the
validation layer is not optional.

The sprint does NOT deliver:

- Volunteer-contributed compute infrastructure (Phase 7+)
- Bring-your-own-data ingestion (Phase 7+)
- Pattern submission, verification, or knowledge base layer
  (Phase 7+)
- Alternate data source integration (post-sprint decision)

The sprint's job is to ship a credible, transparent, public
demonstration of the PRJCT9 engine on financial data. The
ultimate destination - Wikipedia of pattern finding,
crowdsourced research commons, bring-your-own-data - is the
direction the sprint pushes toward, not the deliverable.

## Data source posture

The sprint uses yfinance (Yahoo Finance via the yfinance
Python library) as its data source. This matches the entire
existing PRJCT9 codebase and signal library.

yfinance has known constraints:

- Yahoo's API terms restrict the library to personal/
  research use; commercial or large-scale public
  redistribution carries legal exposure
- Rate limiting is real and aggressive
- yfinance is unaffiliated with Yahoo and disclaims
  production use

These constraints become meaningfully consequential when
PRJCT9.com goes public-facing at scale. The sprint
acknowledges this without solving it. Pre-launch, a separate
data licensing review is needed; post-sprint, alternate data
providers (Polygon, EODHD, Bloomberg, etc.) or a carefully
scoped derived-research posture should be evaluated.

The Wikipedia-of-pattern-finding ultimate vision will likely
require alternate or user-supplied data sources regardless of
the licensing review outcome. That's a destination concern,
not a sprint concern.

## How phases reference this document

Every future phase preflight prompt should:

- Reference this document as the destination
- Explicitly state how the phase pushes toward it
- Flag any decision that makes the destination harder to
  reach
- Defer or reject changes that pull away from the
  destination, even when otherwise reasonable

## What this North Star is not

- Not a feature list
- Not a UI specification
- Not a Phase 5 or Phase 6 implementation plan
- Not a marketing document
- Not a contract about what ships when

It is the destination. Each phase decides how to push toward
it. Each preflight checks alignment.
