# Phase 5G-2 Operator Accepted-Risk Decision Record

**Date:** 2026-06-01

**Status:** OPERATOR-AUTHORIZED UNDER ACCEPTED RISK FOR MODE B DERIVED-ONLY NON-COMMERCIAL PUBLIC SURFACE

**Author:** PRJCT9 sprint (Phase 5G-2 decision record, narrow Mode B posture)

**Phase:** 5G-2 (data provider / licensing decision record for the narrow Mode B launch posture)

> **Disclaimer.** This document is not legal advice and does not claim legal clearance. Qualified counsel has NOT reviewed or cleared the posture described here. This record captures the operator's decision to proceed under a narrow accepted-risk posture for a Mode B derived-only, non-commercial public surface while yfinance remains in the data pipeline. The risks identified in `project/md_library/shared/2026-05-08_PHASE_5G_DATA_LICENSING_PRELAUNCH_GATE.md` Sections 4, 5, 6, 7, 11, and 13 remain unresolved and are not erased by this record. This record does not authorize raw OHLCV public display, public monetization while yfinance remains in use, or any expansion of the public surface beyond the controls enumerated in Section 5 below.

---

## 1. Status and Scope

- **Date:** 2026-06-01.
- **Status:** OPERATOR-AUTHORIZED UNDER ACCEPTED RISK FOR MODE B DERIVED-ONLY NON-COMMERCIAL PUBLIC SURFACE.
- **Decision authority:** the operator alone. Qualified counsel has not reviewed or cleared this posture.

This document is a Phase 5G-2 decision record derived from the Phase 5G-2 template at `md_library/shared/2026-05-08_PHASE_5G_2_DATA_PROVIDER_DECISION_RECORD_TEMPLATE.md` and informed by the Phase 5G-1 research artifact at `md_library/shared/2026-05-08_PHASE_5G_DATA_LICENSING_PRELAUNCH_GATE.md`. It supersedes the unsigned template for the current public-promotion decision but does not erase the Phase 5G-1 risks.

This record is the smallest decision artifact that removes the procedural self-block on the K=6 MTF Phase 5 honest-validation public-promotion path under a narrow accepted-risk posture. It does not:

- Make legal claims or assert legal clearance.
- Substitute for qualified counsel review.
- Authorize raw OHLCV public display.
- Authorize public monetization while yfinance remains in the data pipeline.
- Run compute, change code, switch providers, promote, or deploy.
- Mutate `output/`, `cache/results`, `price_cache`, `signal_library/data/stable`, or `output/stackbuilder`.
- Resolve the persistent-cache or 24-hour-storage questions raised in Phase 5G-1 Sections 4.3.2 and 11.3. Those remain named accepted-risk items per Section 6 below.

The risks scoped in Phase 5G-1 remain real. This record is an operator decision to proceed under those risks for the narrow surface described; it is not a denial of those risks.

---

## 2. Source Documents

This decision record references but does not rewrite the following source documents. The decisions captured below remain bounded to the conditions documented in those sources.

| Source | Path | Used for |
|---|---|---|
| Phase 5G-1 research / scoping artifact | `md_library/shared/2026-05-08_PHASE_5G_DATA_LICENSING_PRELAUNCH_GATE.md` | Yahoo / yfinance ToS posture (Sections 4.1, 4.2, 4.3); Mode A vs Mode B display-mode distinction (Section 6.4); server-side caching risk (Sections 6.5, 11.3); commercial / non-commercial determination (Section 11.4); legal counsel touchpoints (Section 11); risks (Section 13); recommended 5G sequencing (Section 12); status RECOMMEND-PHASED-IMPLEMENTATION (Section 15). |
| Phase 5G-2 template | `md_library/shared/2026-05-08_PHASE_5G_2_DATA_PROVIDER_DECISION_RECORD_TEMPLATE.md` | Decision Summary Table structure (Section 3); Provider Candidate Decision Matrix (Section 4); Final Decision Block expectations (Section 9). The template's Status line is TEMPLATE / PENDING PETER + COUNSEL INPUT; this record fills in a narrow accepted-risk path for the current Mode B public-promotion question without claiming counsel review. |
| Phase 5 honest-validation report | `md_library/shared/2026-06-01_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT.md` | Phase 5 evidence packaging complete; n_strategies_tested = 8; n_strategies_reported = 4; n_strategies_survived_empirical = 4; Section 8 of that report explicitly states Phase 5G remains a separate public-launch gate. Stable SHA-256: `48efeb072c11a2abfe10eebfccde01604b74fd25f22392e414c8ab30a422e4bd`. |
| React publish / deploy contract | `md_library/shared/2026-05-31_REACT_PUBLISH_DEPLOY_CONTRACT.md` | Public-vs-private fork (Section 2); artifact-boundary rule that the React app consumes only a static `k6_mtf_ranking_v1` JSON artifact, never recomputes metrics, and never reads raw engine state; `validation_results` / Phase 5 hook semantics in the promotion manifest. |
| Promotion helper | `utils/react_publish/promote_k6_mtf_artifact.py` | Public-mode safety check `_verify_phase5_inputs` verifies Phase 5 report path under `<PROJECT_DIR>` and the report SHA-256 against `--phase5-sha256`. The helper does NOT enforce Phase 5G in code; Phase 5G is therefore process / documentation / operator-gate enforced. This record is that operator gate for the narrow Mode B posture. |
| Current public fixture / React artifact boundary | `frontend/public/fixtures/k6_mtf_ranking.json` (schema `k6_mtf_ranking_v1`); `frontend/public/fixtures/README.md`; `frontend/src/loadArtifact.ts`; `test_scripts/shared/test_k6_mtf_fixture_schema.py` | The committed fixture's per-secondary fields are derived analytics (ranks, signal tokens, capture / Sharpe / win counts, low-sample warnings, `ccc_series` of cumulative capture pct over time, K=6 stack member tickers + protocols). No raw OHLCV (`open`/`high`/`low`/`close`/`volume`) fields appear anywhere in the fixture. The React loader performs only a static-asset `fetch` of the committed fixture; it does not call a backend, does not call a provider, and does not recompute metrics. |

---

## 3. Operator Decision Summary

The operator's decision for the current public-promotion question:

- **Public surface is Mode B derived-only.** The public surface shows PRJCT9-computed K=6 MTF rankings, signal-token snapshots, validation status, K=6 MTF strategy identifiers, ticker labels (as identifiers, not as keys to provider data), and derived analytics (Sharpe, total / average capture pct, win / loss counts, `ccc_series` of cumulative strategy capture pct over time, low-sample warnings).
- **No raw OHLCV displayed.** The public surface MUST NOT show raw `open` / `high` / `low` / `close` / `volume` values, price charts, price tables, downloadable raw price series, or any public endpoint that lets a visitor reconstruct provider price data.
- **No monetization while yfinance remains in the data pipeline.** No ads, subscriptions, paid tiers, affiliate links, sponsorships, or donation prompts on the public surface unless a later Phase 5G amendment clears that posture.
- **yfinance remains the private / internal data source for now.** Raw provider data continues to feed the private compute pipeline (Spymaster / ImpactSearch / OnePass / StackBuilder / TrafficFlow / signal_library / Global Ticker Library validator). Raw provider data is NOT published to the public surface.
- **Public output consists of PRJCT9-derived analytics only.** Rankings, signals, validation statuses, and historical strategy-performance metrics derived by the K=6 MTF construction and the Phase 5C honest validation engine.
- **Public promotion may proceed only while this boundary is true.** Any change to the public surface that breaches the controls in Section 5 below requires a Phase 5G amendment before the change reaches the public surface.
- **This is operator accepted risk, not legal clearance.** Qualified counsel has not reviewed or cleared this posture. The Phase 5G-1 risks remain real and remain owned by the operator.

---

## 4. Decision Summary Table

The table mirrors the Phase 5G-2 template's Decision Summary Table (Section 3 of the template) so the operator's accepted-risk decisions are visible against the same row structure the template defined.

| Decision Area | Decision | Status | Evidence / Source | Caveat |
|---|---|---|---|---|
| Public launch data provider | yfinance retained for private / internal computation feeding Mode B derived-only public output. | ACCEPTED RISK | Phase 5G-1 Sections 4.1, 4.2, 4.3, 6.4 | Not legal clearance; provider switch remains the long-term fallback per Section 9 of this record. |
| Private research data provider | yfinance status quo. | ACCEPTED | Phase 5G-1 Sections 4.1, 4.2 | Matches yfinance README's stated personal-use intent; remains internal. |
| Public display mode | Mode B derived metrics only. | DECIDED | Phase 5G-1 Section 6.4; this record Section 5 | Hard exclusions: raw OHLCV, charts of provider prices, price tables, downloadable provider data, public API endpoints that return provider data. |
| Server-side caching posture | Existing private / local caches (`cache/results/`, `signal_library/data/stable/`, `output/` artifacts, controlled-compute manifests, validation sidecars, validation ledger) may continue for computation. Caches are private / local computation artifacts and are NOT public web assets. | ACCEPTED RISK | Phase 5G-1 Sections 4.3.2 (YDN 24-hour storage), 6.5, 11.3 | The persistent-cache question raised by Phase 5G-1 is NOT resolved by this record. The operator accepts the residual risk for the initial narrow Mode B public surface. Future counsel review may require cache shortening, purging, or rebuild from a different provider. |
| Monetization posture | None while yfinance remains in the public-launch data pipeline. | DECIDED | Phase 5G-1 Sections 6.8, 11.4 | Hard exclusions: ads, paid subscriptions, paid tiers, affiliate links, sponsorships, donation prompts on the public surface unless a future Phase 5G amendment clears the posture. |
| Legal counsel review status | Not obtained. | PENDING / NOT CLEARED | Phase 5G-1 Section 11 (counsel touchpoints enumerated) | This record is operator risk acceptance, not counsel clearance. Counsel relationship may be re-scoped as the project's public posture or traffic grows (Phase 5G-1 Section 13 item 7). |
| Provider contract / written permission | No paid provider contract. No Yahoo Finance LLC written permission. | NOT OBTAINED | Phase 5G-1 Sections 8.x, 11.5 | The narrow Mode B public surface is launched without provider contract; provider objection triggers rollback (Section 8 of this record). |
| Yahoo written permission | Not pursued for the initial narrow Mode B non-commercial public launch. | DEFERRED | Phase 5G-1 Section 11.9 | Provider objection triggers rollback / provider switch per Section 8 of this record. |
| Hybrid yfinance-private / paid-provider-public posture | Not adopted at initial launch. The current posture is single-provider (yfinance) internal-only, with Mode B derived-only public output. | NOT ADOPTED | Phase 5G-1 Section 10 (hybrid option ranked but not selected) | Hybrid model remains a future option if the public surface grows, monetizes, or if provider objection requires migration. |
| Provider-tagged validation tracks | Single-track. Validation sidecars and the launch-current ledger are sourced from a single provider (yfinance via the K=6 MTF construction). | NOT ADOPTED | Phase 5G-1 Section 7.4 | Future provider switch would require provider-tagging in the validation ledger to preserve cross-provider comparability. Out of scope for this record. |
| BYO-data for Phase 7+ | Deferred per North Star. | DEFERRED | Phase 5G-1 Section 7.2; North Star "Bring-your-own-data direction" | Phase 7+ contributor-license model remains a future deliverable. Out of scope for this record. |
| Public promotion authorization | Authorized by the operator only for the narrow Mode B derived-only, non-commercial public surface bounded by this record's controls (Section 5). | AUTHORIZED UNDER ACCEPTED RISK | This record Section 5; Section 10 Final Decision Block | The PR #368 promotion helper enforces Phase 5 report SHA in code (`utils/react_publish/promote_k6_mtf_artifact.py` `_verify_phase5_inputs`); it does NOT enforce Phase 5G. This record is the documentation-side gate. |
| Rollback / response plan | If a data provider objection, cease-and-desist, credible counsel concern, or terms change indicates the Mode B yfinance-derived public posture is not acceptable, the operator pulls or disables the public site / promotion promptly and either reverts to private / internal mode or switches to a different data source before relaunch. | DECIDED | This record Section 8 | Audit trail preserved through the existing PR review chain and Phase 5G-1 / 5G-2 documents. |

---

## 5. Public Surface Controls

Binding controls for the public surface under this accepted-risk posture. Any breach reopens Phase 5G and requires an amendment before the change reaches the public surface.

The public surface MAY include:

- The committed / promoted K=6 MTF ranking artifact (`k6_mtf_ranking_v1`) and the derived analytics it carries: ranks, signal-token snapshots, `sharpe_k6_mtf`, `total_capture_pct`, `avg_capture_pct`, `stddev_pct`, `win_pct`, counts (`match_count`, `capture_count`, `trade_count`, `no_trade_count`, `skipped_capture_count`, `win_count`, `loss_count`), `low_sample_warning`, K=6 stack member tickers + protocols (as identifiers), `current_snapshot` 5-tuple, and `ccc_series` (cumulative capture pct + per-bar capture pct + trade-direction labels over time).
- The Phase 5 honest-validation report and any operator-published companion explanation of its methodology and bounded claims.
- Ticker labels used as identifiers for the K=6 MTF strategy unit (e.g., `AAPL`, `SPY`). Ticker labels are identifiers, not provider data values.
- Static-asset hosting of the fixture (single-file fetch of the committed artifact) per the React publish / deploy contract.

The public surface MUST NOT include:

- Raw OHLCV displayed as market-data series: `open`, `high`, `low`, `close`, `volume` from any provider.
- Price charts of any provider's price series.
- Price tables of any provider's price series.
- Downloadable raw provider price series in any format (CSV, JSON, parquet, pickle, image, embedded data URL).
- Any public API or backend endpoint that returns provider OHLCV, even indirectly (e.g., a derived endpoint whose response trivially reveals provider close prices).
- Live provider fetch from the browser (e.g., a `fetch` to a yfinance-backed proxy or to any other provider).
- A backend public route that returns provider / raw data.
- Reconstructable price series via any combination of derived fields. If a derived field combination would let a reasonably motivated visitor reconstruct provider closes (or any OHLCV component), it must be reviewed and either suppressed or amended via Phase 5G before public display.
- Monetization mechanisms while this yfinance-derived posture remains active (ads, paid subscriptions, paid tiers, affiliate links, sponsorships, donation prompts on the public surface).

If a future change adds raw prices, price charts, downloadable price data, a public raw-data API, or monetization to the public surface, that change reopens Phase 5G and requires a Phase 5G amendment recorded as a follow-up decision record before the change reaches the public surface.

---

## 6. Caching and Storage Posture

Honest framing of the caching question under this accepted-risk posture:

- Some transient caches may be short-lived (e.g., per-process in-memory caches in the existing engines).
- Persistent stable signal-library artifacts (`signal_library/data/stable/` PKLs), Spymaster `cache/results/` PKLs, controlled-compute outputs (`output/controlled_compute/`), validation sidecars (`output/validation/`), the validation ledger (`output/validation_ledger/`), and StackBuilder run directories (`output/stackbuilder/`) may persist locally for computation.
- These persistent artifacts are PRIVATE / LOCAL computation artifacts. They are NOT public web assets, are NOT served by any public route under this record, and are NOT published as downloadable artifacts.
- The repository's `project/.gitignore` keeps `output/`, `cache/`, and `price_cache/` out of the version-control surface. The committed K=6 MTF ranking fixture at `frontend/public/fixtures/k6_mtf_ranking.json` is a derived artifact (Mode B); the raw provider artifacts the K=6 MTF construction consumed remain local.
- This record does NOT claim the persistent-cache question raised by Phase 5G-1 Sections 4.3.2 (YDN 24-hour storage) and 11.3 (caching / redistribution rights) is legally resolved. The persistent local computation caches are explicitly an accepted-risk item.
- If qualified counsel later determines persistent local caches are not acceptable, or if a data provider objection raises the question, the operator commits to one or more of the following before relaunch: reviewing the affected caches, purging the affected caches, shortening cache lifetimes, rebuilding affected caches from a different provider whose terms permit local storage, or otherwise remediating as required.
- This posture is operator accepted risk, not a legal conclusion.

---

## 7. Validation and Public-Claim Boundary

- The Phase 5 honest-validation report is COMPLETE and merged at `md_library/shared/2026-06-01_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT.md` with stable SHA-256 `48efeb072c11a2abfe10eebfccde01604b74fd25f22392e414c8ab30a422e4bd`. Public-mode promotion via the PR #368 helper requires that path and SHA.
- Public claims are bounded to the 8 Tier 1 launch universe (AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY, TSLA) and the validated empirical rerun captured at `run_id = 20260601_k6_mtf_phase5_launch_universe_empirical`. Public copy MUST NOT claim coverage, validation, or evidence beyond that tested universe and that tested run.
- The public board MUST NOT imply investment advice, prediction, or guaranteed future performance. Per the Phase 5 honest-validation report Section 7, the report is "self-administered validation evidence, not a proof or guarantee of future performance" and "claims must remain bounded to the tested launch universe and the tested run."
- Recommendation (not implemented by this record): public UI copy should include "research only," "not investment advice," and "past performance does not guarantee future results" -style disclaimers before or alongside public promotion. UI changes are NOT made in this PR; they are recorded here as a recommended Phase 5G amendment or React copy PR that should accompany or precede the first public-mode promotion-helper run.

---

## 8. Rollback and Provider-Switch Triggers

The operator commits to the following response plan if any of the listed triggers occur. Triggers are evaluated by the operator; counsel may be engaged at any time.

| Trigger | Response |
|---|---|
| Data provider cease-and-desist (Yahoo, yfinance maintainers, or any future provider). | Pull or disable public promotion / site promptly; preserve audit trail through the existing PR chain; do not relaunch publicly until either a Phase 5G amendment clears a replacement posture or a different data source is in use. |
| Data provider objection short of cease-and-desist (e.g., a written request to remove derived content). | Same as cease-and-desist response. Document the objection and operator response in a follow-up Phase 5G amendment. |
| Credible legal / counsel concern (counsel engagement, counsel memo, or third-party legal-risk signal the operator deems credible). | Pause public promotion; engage qualified counsel to scope the concern; either revert to private / internal mode or amend Phase 5G with counsel's recommendation before relaunch. |
| Terms-of-service change by Yahoo or yfinance project that materially affects the Mode B derived-only posture (e.g., new prohibition on derived metrics from yfinance-sourced data). | Pause public promotion; review the change with counsel if necessary; either amend Phase 5G or switch provider before relaunch. |
| Addition of monetization (ads, subscriptions, paid tiers, affiliate links, sponsorships, donation prompts on the public surface). | This breach reopens Phase 5G. Require a Phase 5G amendment (likely including provider switch or counsel review of the chosen monetization model under the chosen provider's terms) before the monetization change reaches the public surface. |
| Addition of raw OHLCV, price charts, price tables, downloadable price series, or any public raw-data API on the public surface. | This breach reopens Phase 5G. Require a Phase 5G amendment before the change reaches the public surface. Likely requires provider switch to a provider whose terms expressly permit raw-OHLCV public display. |
| Public traffic / visibility materially exceeding the low-scale open-source launch assumption (e.g., a viral moment, a sustained inbound from large referrer surfaces, or sustained traffic that the operator deems likely to attract provider attention). | Re-evaluate posture. Either amend Phase 5G to confirm the higher-traffic posture is acceptable under accepted risk, or pause public promotion until counsel review or provider switch is in place. |
| Operator decision to reduce risk for any reason. | Pause or disable public promotion at operator discretion; document the decision in a follow-up Phase 5G amendment if the change is durable. |

In all rollback cases the operator commits to: (a) preserving the existing audit trail (PRs, sidecars, manifests, ledger, this decision record), (b) NOT silently mutating the public artifact or report content, and (c) opening a provider-switch or Phase 5G amendment PR before any public relaunch.

---

## 9. Provider Fallback Path

- Paid provider switch remains the cleanest long-term path if the public surface grows, monetizes, encounters provider objection, or counsel later identifies a posture the current Mode B accepted-risk record cannot sustain.
- Candidate providers remain as scoped in Phase 5G-1 Section 8 (Polygon / Massive, EODHD, Tiingo, Alpha Vantage, Twelve Data, Alpaca, FRED, EDGAR) and Section 9 (provider ranking). Each row in the Phase 5G-1 / 5G-2 provider matrices remains PENDING DIRECT PROVIDER VERIFICATION; this record does not promote any row to verified or selected.
- Provider switch is NOT implemented by this record. Phase 5G-3 (provider-abstraction engineering preflight) remains conditional on a future 5G-2 amendment that selects a non-yfinance or multi-provider posture. Phase 5G-3 would require its own Codex preflight + Claude Code implementation track per Phase 5G-1 Section 12.3.
- Any provider switch requires separate engineering work AND a separate validation-envelope review: K=6 MTF reruns would need to be re-validated under the new provider's adjusted-price math, symbology, and history depth, and the validation ledger would need provider-tagging to preserve cross-provider comparability per Phase 5G-1 Section 7.4.

---

## 10. Final Decision Block

| Field | Value |
|---|---|
| Decision status | OPERATOR-AUTHORIZED UNDER ACCEPTED RISK |
| Legal clearance | NOT CLAIMED |
| Counsel review | NOT OBTAINED / PENDING |
| Public promotion authorization | YES, but ONLY for the narrow Mode B derived-only, non-commercial public surface bounded by Section 5 controls of this record |
| Raw OHLCV public display | NO |
| Public monetization while yfinance remains in use | NO |
| Provider objection response | Pull or disable promptly; revert to private / internal mode or switch provider before relaunch (per Section 8) |
| Cache / persistent-storage question | UNRESOLVED legally; ACCEPTED RISK for narrow initial launch (per Section 6) |
| Phase 5 honest-validation report | COMPLETE and merged at `md_library/shared/2026-06-01_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT.md`; stable SHA-256 `48efeb072c11a2abfe10eebfccde01604b74fd25f22392e414c8ab30a422e4bd` |
| React fixture posture | Mode B derived-only (`k6_mtf_ranking_v1`); zero raw-OHLCV fields confirmed by source inspection at this record's date |
| Promotion helper | `utils/react_publish/promote_k6_mtf_artifact.py` enforces Phase 5 report SHA in code; this record is the documentation-side Phase 5G gate |
| Date | 2026-06-01 |
| Recorded by | the operator |

This record does NOT use the word "CLEARED." The decision status is "OPERATOR-AUTHORIZED UNDER ACCEPTED RISK." Future qualified counsel clearance or provider-permission grants would require a follow-up Phase 5G amendment that records the counsel's findings or the provider's grant.

---

## 11. Amendment Rule

Any of the following changes requires a dated Phase 5G amendment recorded as a follow-up decision record before the change takes effect:

- Qualified counsel reviews and clears, or rejects, the posture documented here. The counsel finding must be recorded in a new dated amendment.
- A data provider switch is executed (yfinance is replaced or augmented by Polygon, EODHD, Tiingo, Alpha Vantage, Twelve Data, Alpaca, or any other provider).
- The public surface adds raw OHLCV, raw price charts, price tables, downloadable raw provider price series, or any public raw-data API.
- Public monetization is introduced (ads, subscriptions, paid tiers, affiliate links, sponsorships, donation prompts on the public surface).
- The caching / storage posture changes materially (e.g., adding a public-served cache, shortening or extending persistent cache lifetimes in a way that changes the licensing analysis, or moving artifacts that were local-only to a publicly served surface).
- Phase 6 or Phase 7 scope expands beyond the current K=6 MTF Tier 1 derived board (e.g., the public surface broadens to additional secondaries, additional engines, or BYO-data per the North Star Phase 7+ direction).
- The Phase 5 honest-validation report is amended, superseded, or refreshed with a new SHA-256, and the operator wants to update the promotion-helper inputs accordingly.

Each amendment record SHOULD be named `<date>_PHASE_5G_<sub-phase>_<short-description>.md` and SHOULD be added to the `md_library/README.md` sprint-cursor list when the README convention warrants it.

End of document.
