# PRJCT9 Confluence Terminology Glossary

**Date:** 2026-05-25

**Status:** Reference document. Authoritative for terminology; not authoritative for architectural decisions.

**Anchor audits:**

- 2026-05-25 TrafficFlow K-Artifact Producer Reconciliation.
- 2026-05-25 "Confluence" Terminology and Feature-Set Audit.

---

## Why this exists

The word "confluence" is used in this repo for at least four materially different operator-facing concepts. The shared name is historical: the original 2025-10 Multi-Timeframe Signal Confluence Analyzer planted the term, and every later sprint that touched signal alignment, cross-ticker ranking, or stack-aware multi-window evaluation reused it. The result is that "confluence" alone, without disambiguation, no longer carries a single referent.

The 2026-05-25 terminology audit found that the overloaded term has become a recurring source of mis-scoping during architectural decisions, audit prompts, and operator runbook work. It is not a launch blocker; it is a launch-degrader. The smallest action that converts the cost from "every audit pays it" to "one audit pays it, every future audit cites it" is this glossary.

This document is forward-looking. It introduces a numbered concept tagging convention. It does not rename, retire, or otherwise mutate any existing script. It does not resolve any open architectural question. Its only job is to make subsequent written material easier to read.

---

## The four concepts

### Concept 1: Multi-primary confluence

**Operator question:** "If I combine these N primary tickers as signal generators, what happens for target X across timeframes?"

**Aggregates:** operator-picked primary tickers treated as combined signal generators against one secondary target ticker.

**Defined by:** the operator's manual choice of primaries. The script does not infer primaries from StackBuilder output and is not stack-aware.

### Concept 2: Single-ticker multi-timeframe self-alignment

**Operator question:** "Does ticker X agree with itself across its own 1d, 1wk, 1mo, 3mo, and 1y signals?"

**Aggregates:** one ticker's own per-interval signal libraries across the five canonical windows.

**Defined by:** that single ticker's signal-library data. No StackBuilder. No member set. No cross-ticker comparison.

### Concept 3: Cross-ticker multi-timeframe signal screen

**Operator question:** "Across this universe of tickers, which ones currently show the strongest multi-timeframe signal alignment in their own signal libraries?"

**Aggregates:** each ticker's per-interval signal libraries, then ranks across the universe by self-alignment strength.

**Defined by:** per-ticker self-alignment (the same per-ticker reading as Concept 2), then sorted cross-ticker. **Not StackBuilder-aware.** `cross_ticker_confluence.py` explicitly comments that the StackBuilder run does not gate ranking eligibility; StackBuilder data is recorded for provenance only and does not influence the score.

### Concept 4: Stack-aware multi-window K-build confluence

**Operator question:** "For ticker X's StackBuilder-selected K-build, does the stack fire across canonical windows today, and how does it rank against other tickers' stacks?"

**Aggregates:** the StackBuilder-selected K-build for a target ticker (member set + per-member protocol assignments at a specific K value), evaluated across 1d, 1wk, 1mo, 3mo, and 1y windows.

**Defined by:** StackBuilder `selected_build.json` plus the corresponding seed-run leaderboard, per-interval signal libraries, and local cache PKLs for each member. This is the launch-path concept.

### Summary table

| Concept | Plain-English name | Key input | StackBuilder-aware? | On the launch path? |
| --- | --- | --- | --- | --- |
| 1 | Multi-primary confluence | Operator-picked primaries | No | No |
| 2 | Single-ticker self-alignment | One ticker's own libraries | No | No |
| 3 | Cross-ticker signal screen | Per-ticker libraries, ranked cross-ticker | No (provenance only) | No |
| 4 | Stack-aware multi-window K-build | StackBuilder selected build + libraries + cache | Yes | Yes |

---

## Script-to-concept mapping

### Concept 1: multi-primary

- `confluence.py` in multi-primary mode: Dash operator app on port 8056 (fallback if occupied); combines manually selected primaries against one target.
- `signal_library/confluence_analyzer.py`: shared alignment math used by the Dash app.

### Concept 2: single-ticker self-alignment

- `confluence.py` in single-ticker mode: Dash operator app on port 8056 (fallback if occupied); displays one ticker's own multi-timeframe alignment.
- `signal_library/confluence_analyzer.py`: shared alignment math.
- `signal_library/spymaster_confluence_bridge.py`: legacy / pre-PRJCT9-sprint Spymaster display bridge for confluence-card data. Not part of the launch path. This glossary does not retire it.

### Concept 3: cross-ticker screen, not StackBuilder-aware

- `cross_ticker_confluence.py`: Phase 4A CLI / read engine. Emits manifest-stamped run directories with `coverage.json`, `rankings.json`, `overlay.json`, `universe_snapshot.json`, and `run_manifest.json`.
- `cross_ticker_confluence_dash.py`: Phase 4B dashboard on port 8057. Reads Phase 4A run directories and does not recompute.

### Concept 4: stack-aware multi-window launch path

Generation, validation, and legacy decision surfaces:

- `trafficflow_k_artifact_builder.py`: Phase 6D-1 daily K research-day artifact builder.
- `trafficflow_multitimeframe_bridge.py`: Phase 6D-2 projection bridge from daily K artifacts to MTF K artifacts.
- `confluence_mtf_artifact_builder.py`: Phase 6D-3 builder that aggregates MTF K artifacts into one Confluence research-day artifact per target.
- `confluence_pipeline_readiness.py`: Phase 6C-8 readiness inspector.
- `confluence_pipeline_runner.py`: Phase 6D-4 offline pipeline runner; chains 6D-1 / 6D-2 / 6D-3 per ticker.
- `confluence_ranking_contract_validator.py`: Phase 6I-1 saved-artifact contract validator.
- `confluence_ranking_emitter.py`: Phase 6I-3 saved-artifact ranking emitter used by decision briefs and automation surfaces. Not the website ranking path.
- `confluence_decision_brief.py`: Phase 6I-19 presentation adapter over the Phase 6I-3 emitter.

True multi-window K track and website-facing launch chain:

- `multiwindow_k_engine_core.py`: Phase 6I-21 core evaluator.
- `multiwindow_k_input_adapter.py`: Phase 6I-22 input adapter, amended later for invalid-member handling.
- `multiwindow_k_input_adapter_diagnostic.py`: Phase 6I-27 adapter diagnostic.
- `multiwindow_k_engine_payload_builder.py`: Phase 6I-23 payload builder.
- `multiwindow_k_confluence_patch_planner.py`: Phase 6I-24 patch planner.
- `multiwindow_k_confluence_patch_writer.py`: Phase 6I-25 guarded patch writer.
- `confluence_multiwindow_ranking_export.py`: Phase 6I-34 website-oriented multi-window ranking / export.
- `confluence_website_export_package.py`: Phase 6I-35 website JSON envelope.
- `confluence_website_reader_view.py`: Phase 6I-36 renderer-ready view model.
- `confluence_static_board_renderer.py`: Phase 6I-41 static HTML ranking board renderer.
- `confluence_board_runtime_overlays.py`: Phase 6I-42 read-only local runtime overlays for the board.

### Rollout planners: operational tooling, not a fifth concept

These scripts orchestrate the launch-path rollout. They do not implement a separate confluence concept; their "confluence" in the filename reflects the Phase 6I rollout sprint naming convention.

- `confluence_large_universe_launch_planner.py`: Phase 6I-50 launch planner.
- `confluence_large_universe_rollout_batch_planner.py`: Phase 6I-51 rollout batch planner.
- `confluence_stackbuilder_rollout_policy.py`: Phase 6I-52 policy lock and seed universe.
- `confluence_stackbuilder_pilot_preflight.py`: Phase 6I-53 pilot-batch preflight.
- `confluence_impactsearch_primary_universe_readiness_planner.py`: Phase 6I-55a ImpactSearch / primary-universe readiness planner.

---

## Launch path

**The launch path is Concept 4.**

The website-facing ranking / export chain is:

```
confluence_multiwindow_ranking_export.py
    -> confluence_website_export_package.py
        -> confluence_website_reader_view.py
            -> confluence_static_board_renderer.py
```

Concepts 1, 2, and 3 are research, diagnostics, or operator exploration surfaces. They may remain useful, but they do not currently feed the launch ranking.

---

## Ranking emitters

Three distinct ranking emitters exist in this repo. They do not converge on a single ranking.

- `confluence_multiwindow_ranking_export.py`: Concept 4 multi-window ranking / export. Feeds the website export chain. **This is the launch ranking.**
- `confluence_ranking_emitter.py`: Concept 4 saved-artifact decision ranking. Used by decision briefs and automation surfaces. **Not** the website export chain.
- `cross_ticker_confluence.py` `rankings.json`: Concept 3 cross-ticker signal-library screen. **Not on the launch path.**

Only `confluence_multiwindow_ranking_export.py` flows into the website export chain.

---

## What this glossary does NOT decide

This document is terminology-only. It does not resolve:

- **TrafficFlow Producer A vs Producer B reconciliation.** A separate audit recommended reconciliation: Phase E becomes the operational foundation and emits or reconciles a `research_day_v1`-compatible payload before the older producer is retired.
- **MTF projection vs true multi-window K engine.** The Phase 6I-20 gap audit documents the projection limitation; this glossary only names the concepts.
- **The two `confluence.py` runtime bugs surfaced in operator testing on 2026-05-25**: the multi-primary callback collision and the 1mo data integrity issue. Those are tracked separately and matter only if `confluence.py` remains an operator tool.
- **Any script renames, retirements, deletions, or unification refactors.** This glossary does not authorize or scope any such work.

---

## Naming convention going forward

- In prompts, scoping docs, audit prompts, evidence docs, and operator runbooks, do not use bare "confluence" when a numbered concept is meant.
- Prefer `Concept 1`, `Concept 2`, `Concept 3`, or `Concept 4`, or name the script directly.
- Do not introduce a new `confluence_<something>.py` script unless it is part of Concept 4, the launch path.
- For Concept 1, 2, or 3 work, prefer names that state the actual job, such as `multi_primary_signal_aggregator`, `single_ticker_timeframe_alignment`, or `cross_ticker_signal_screener`.
- This convention is forward-looking only. Existing scripts are not renamed by this document.

---

## References

- Concept 1 / Concept 2: `md_library/confluence/2025-10-19_MULTI_TIMEFRAME_CONFLUENCE_IMPLEMENTATION_PLAN.md` (the original Multi-Timeframe Signal Confluence Analyzer plan; predates the PRJCT9 sprint).
- Concept 3: `md_library/shared/2026-05-04_PHASE_4_SCOPING.md` (Phase 4 cross-ticker multi-timeframe confluence engine scoping).
- Concept 4 baseline pipeline: `md_library/shared/2026-05-11_PHASE_6C8_CONFLUENCE_PIPELINE_CONTRACT.md`.
- Concept 4 gap and true multi-window track: `md_library/shared/2026-05-13_PHASE_6I20_MULTIWINDOW_K_ENGINE_GAP_CONTRACT.md`, plus `md_library/shared/2026-05-13_PHASE_6I21_MULTIWINDOW_K_ENGINE_CORE_EVALUATOR.md`, `md_library/shared/2026-05-13_PHASE_6I22_MULTIWINDOW_K_INPUT_ADAPTER.md`, `md_library/shared/2026-05-13_PHASE_6I23_MULTIWINDOW_K_ENGINE_PAYLOAD_BUILDER.md`, `md_library/shared/2026-05-13_PHASE_6I24_MULTIWINDOW_K_CONFLUENCE_PATCH_PLANNER.md`, and `md_library/shared/2026-05-13_PHASE_6I25_MULTIWINDOW_K_CONFLUENCE_PATCH_WRITER.md`.
- Concept 4 website ranking / export path: `md_library/shared/2026-05-14_PHASE_6I34_MULTI_TICKER_CONFLUENCE_RANKING_EXPORT.md`, `md_library/shared/2026-05-14_PHASE_6I35_WEBSITE_CONFLUENCE_EXPORT_PACKAGE.md`, `md_library/shared/2026-05-14_PHASE_6I36_CONFLUENCE_WEBSITE_READER_VIEW.md`, and `md_library/shared/2026-05-14_PHASE_6I41_STATIC_CONFLUENCE_BOARD_RENDERER.md`.
