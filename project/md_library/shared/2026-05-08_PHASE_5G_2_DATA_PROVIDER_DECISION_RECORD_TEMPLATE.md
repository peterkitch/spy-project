# Phase 5G-2 Data Provider Decision Record Template

**Date:** 2026-05-08

**Status:** TEMPLATE / PENDING PETER + COUNSEL INPUT

**Author:** PRJCT9 sprint (Phase 5G-2 decision-record scaffold)

**Phase:** 5G-2 (data provider / licensing decision record; gates 5G-3 provider-abstraction engineering and Phase 6 launch scoping)

> **Disclaimer.** This is a decision-record template, not a final decision and not legal advice. It is designed to be filled in by Peter together with qualified legal counsel and provider sales/support contacts. Until the Final Decision Block (Section 9) is signed and dated by Peter, this document does not authorize any code change, provider switch, or public-launch posture.

---

## 1. Title / Status

- **Title:** Phase 5G-2 Data Provider Decision Record Template
- **Status:** TEMPLATE / PENDING PETER + COUNSEL INPUT
- **Version:** v0 (template; promote to v1 once Peter and counsel sign Section 9)
- **Created:** 2026-05-08
- **Last updated:** 2026-05-08
- **Owner:** Peter (with counsel review)

This document is the durable decision record that Phase 5G-1 (research foundation) defers to. Per `2026-05-08_PHASE_5G_DATA_LICENSING_PRELAUNCH_GATE.md` Section 15, "5G-2 requires Peter / legal counsel / provider decisions captured as a durable decision record" and "5G-3 provider-abstraction engineering is conditional on 5G-2 selecting a non-yfinance or multi-provider posture." Until this record is signed, the locked-authority recommendation is "No code changes recommended until 5G-2 decisions are made."

---

## 2. Source References

This template references but does not rewrite the following source documents. Read them directly when filling in any decision below.

| Source | Path | Used for |
|---|---|---|
| Phase 5G-1 research foundation | `project/md_library/shared/2026-05-08_PHASE_5G_DATA_LICENSING_PRELAUNCH_GATE.md` | Yahoo/yfinance ToS posture; provider candidate survey; codebase yfinance coupling audit; Phase 6 minimum-viable data requirements; Phase 7+ BYO-data implications; recommended 5G sequencing; risks; Peter decision checklist |
| PRJCT9 North Star | `project/md_library/shared/2026-05-04_PRJCT9_NORTH_STAR.md` | Open-source research-commons posture; two user paths (curated default view + manual ticker entry); Phase 7+ crowdsourcing/BYO-data direction; data source posture (yfinance is sprint data; pre-launch licensing review needed) |
| Phase 4 Scoping | `project/md_library/shared/2026-05-04_PHASE_4_SCOPING.md` | Schema isolation principle toward future BYO data; coverage transparency; engine/presentation separation. Phase 4 Scoping does not specify a complete `provider_metadata` field shape. |
| Phase 5C Validation Methodology | `project/md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md` | Validation contract (`validation_methodology_v1`); honest-validation discipline; provider-bound validation envelope implications for cross-provider comparability |

When this template's decisions diverge from anything in the source docs above, capture the divergence with date and rationale in Section 9's "Notes on divergence from source documents" subsection.

---

## 3. Decision Summary Table

Fill in each row as Peter, counsel, and provider contacts make decisions. Leave fields blank (or marked PENDING) until decided. The "Evidence / Source" column should cite Section 5G-1 source-section, counsel memo, or provider contract.

| Decision Area | Options | Current Decision | Owner | Evidence / Source | Follow-up Required |
|---|---|---|---|---|---|
| Phase 6 launch data provider | yfinance / Polygon (Massive) / EODHD / Tiingo / Alpha Vantage / Twelve Data / Alpaca / Other | PENDING | Peter + counsel | 5G-1 §8 (provider candidate survey), §9 (ranking) | Direct provider sales engagement for top candidate(s); counsel review of contract |
| Private research data provider | yfinance (status quo) / paid provider replacement / dual | PENDING | Peter | 5G-1 §10 (hybrid model viability) | Decide whether Peter's local research workflow stays on yfinance or migrates |
| Public display mode | Mode A (raw OHLCV) / Mode B (derived metrics only) / both | PENDING | Peter + counsel | 5G-1 §6.4 (display modes) | Counsel review of whether Mode B materially reduces yfinance ToS exposure |
| Phase 6 launch universe tier | Tier A (curated 50-200) / Tier B (US equities + ETFs) / Tier C (full 73K + international) | PENDING | Peter | 5G-1 §6.1 (universe tiers) | Cost-impact analysis once provider selected |
| Server-side caching posture | PRJCT9 cache (current state) / provider-cache only / no caching | PENDING | Peter + counsel | 5G-1 §6.5 (server-side caching) | Counsel determination of whether caching = "redistribution" / "reproduction" under chosen provider's terms |
| Monetization posture | None / donations / ads / subscription / partnerships / TBD | PENDING | Peter | 5G-1 §6.8 (commercial terms), §11.4 (commercial-use determination) | Counsel review of whether chosen monetization model qualifies as "commercial purpose" under chosen provider's terms |
| Legal counsel review status | Not engaged / engaged / under review / completed | PENDING | Peter | 5G-1 §11 (legal counsel touchpoints) | Identify counsel with data-licensing / TMT / fintech expertise; scope engagement |
| Provider contract / sales contact status | Not contacted / contacted / contract under review / signed | PENDING | Peter | 5G-1 §8.x per-provider (each has "main unknowns" requiring direct outreach) | Direct sales engagement for top candidates; obtain redistribution-license language under NDA if needed |
| Whether Yahoo Finance written permission will be pursued | Yes / No / Deferred | PENDING | Peter + counsel | 5G-1 §11.9 (Yahoo Finance LLC express written permission inquiry) | If yes, counsel scopes the inquiry, expected timeline, expected cost |
| Whether hybrid yfinance-private / paid-provider-public posture is accepted | Yes / No | PENDING | Peter | 5G-1 §10 (hybrid model viability) | If yes, accept the validation-envelope-incomparability tradeoff (see Section 7) |
| Whether provider-tagged validation tracks are accepted | Yes / No / single-track-only | PENDING | Peter | 5G-1 §7.4 (validation envelope comparability) | If yes, validation ledger gains provider-bound track separation; honest-validation discipline preserved |
| Whether BYO-data is required for Phase 7+ or only optional | Required (per North Star) / Optional / Deferred indefinitely | PENDING | Peter | 5G-1 §7 (Phase 7+ BYO-data / volunteer compute implications); North Star "Bring-your-own-data direction" | If required, contributor license model is a Phase 7+ deliverable (see Section 7) |

---

## 4. Provider Candidate Decision Matrix

This matrix synthesizes the 5G-1 provider survey (§8) and ranking (§9) at the access date 2026-05-08. **Do not invent new conclusions beyond 5G-1.** Where 5G-1 marked claims as "direct verification pending" or "unverified third-party context," reproduce that label here. Update with PETER / COUNSEL VERIFIED status only after direct engagement with the provider.

| Provider | Candidate Role | Official Source Confidence | Commercial / Public Display Status | Caching / Redistribution Status | Universe Fit | History Depth Fit | Rate Limit Fit | Estimated Cost Tier | Counsel Review Needed | Peter Decision |
|---|---|---|---|---|---|---|---|---|---|---|
| Yahoo Finance / yfinance | Sprint/private incumbent; possible Phase 6 source if counsel-cleared and Mode-B display | OFFICIAL (umbrella ToS + 3 developer-API documents from `legal.yahoo.com`); yfinance README (official) | RISK + NEEDS LEGAL REVIEW (3 independent umbrella ToS prohibitions; 3 reinforcing developer-API documents; yfinance README "personal use only") | RISK (no caching authorization in any of the four documents; YDN Guidelines §"24-hour storage limit" cited in 5G-1 §4.3.2) | Full (current PRJCT9 ~73K coverage uses yfinance) | Full (`period="max"` on yfinance) | Constrained (rate-limited; aggressive in practice) | $0 monetary; HIGH legal | YES (Yahoo umbrella ToS + Developer API ToU + YDN Guidelines + API T&C) | PENDING |
| Polygon / Massive | #1 candidate to evaluate (5G-1 §9.1) | LOW from official sources at access date (pricing page → `massive.com/pricing` returned near-empty; verification pending) | PENDING DIRECT PROVIDER VERIFICATION | PENDING DIRECT PROVIDER VERIFICATION (tier-differentiated per Polygon's general posture; specific language not verified at access date) | Strong general reputation for US equities/options/forex/crypto; per-tier verification pending | Direct verification pending | Direct verification pending | Direct verification pending | YES (review business-tier license language directly with Polygon under NDA) | PENDING |
| EODHD | #2 candidate to evaluate (5G-1 §9.1) | OFFICIAL pricing-plans page at `eodhd.com` parsed directly | "Personal use plans restrict to individual, non-commercial activities"; "Commercial use requires Startups & Enterprise Data Solution Plan" (5G-1 §8.7) | PENDING DIRECT PROVIDER VERIFICATION (specific redistribution rights vary by plan; not detailed in public pricing page) | US equities + ETFs + international; per-tier verification | 30+ years US equities ("from earliest available, e.g., Ford from June 1972") | 100,000/day, 1,000/min on paid personal tiers; commercial separately | $19.99-99.99/mo personal; commercial separately negotiated | YES (Startups & Enterprise plan terms must be reviewed) | PENDING |
| Tiingo | #3 candidate to evaluate (5G-1 §9.1) | LOW from official sources at access date (pricing pages returned only page titles; verification pending) | PENDING DIRECT PROVIDER VERIFICATION (commercial use requires organization/business plan per Tiingo's general posture; specific language not verified at access date) | Redistribution-by-default prohibited absent license that expressly allows; commercial license framework exists | 32,000 US + Chinese equities EOD per Tiingo product page | Direct verification pending | Direct verification pending | Direct verification pending | YES (review `app.tiingo.com/tos/` directly + commercial-tier contract with Tiingo sales) | PENDING |
| Alpha Vantage | Strong secondary if top-three commercial pricing exceeds tolerance (5G-1 §9.2) | MEDIUM from official sources (premium-page pricing parsed directly; ToS + realtime-policy URLs cited but not parsed in 5G-1) | Personal/non-commercial covered by premium subscription; commercial separately negotiated | PENDING DIRECT PROVIDER VERIFICATION | US equities + global per general docs; per-tier verification pending | Not specified in pricing-page extract; verify directly | $49.99/mo (75 req/min) → $249.99/mo (1200 req/min) on premium personal | $49.99-249.99/mo personal; commercial separately | YES (commercial-license language + verify any exchange-license claims directly with Alpha Vantage; 5G-1 explicitly downgraded "officially licensed by NASDAQ" claims) | PENDING |
| Twelve Data | Considered but eliminated for individual-plan public use (5G-1 §8.6, §9.3) | OFFICIAL pricing page parsed directly | "Personal, internal, and non-commercial purposes" — commercial NOT permitted under individual plans | PENDING DIRECT PROVIDER VERIFICATION (whether commercial / enterprise tiers exist) | US equities + ETFs across all tiers; global on Grow+ | Direct verification pending | Basic 8/min, Grow 377/min, Pro 1597/min, Ultra 10,946/min | $0 / $66 / $191 / $832 mo annual | YES (if a commercial tier exists, request its terms directly) | PENDING |
| Alpaca | Excellent for private research; eliminated for Phase 6 public redistribution per Alpaca's official support article (5G-1 §8.2, §9.3) | MEDIUM from official sources (data-product page + redistribution support article both official) | "Cannot redistribute Alpaca API data for business purpose" per `https://alpaca.markets/support/redistribute-alpaca-api` (Alpaca's own official support article) | RISK (redistribution-prohibition is Alpaca's stated official position; absent a separate commercial / data-vendor license, public Phase 6 use is not permitted) | US equities + ETFs + options + crypto | "7+ years" historical | Free 200/min (15-min delayed via API; real-time via WS); Algo Trader Plus unlimited | $0 / $99 mo | YES (only relevant if Peter pursues a separate Alpaca commercial / data-vendor agreement; otherwise no further engagement needed) | PENDING (likely "private-research-only or eliminated") |
| Other / TBD | Examples: Bluesky API (IEX Cloud successor), Intrinio, Refinitiv, Bloomberg, direct exchange aggregator | NOT SURVEYED in 5G-1 | NOT SURVEYED | NOT SURVEYED | NOT SURVEYED | NOT SURVEYED | NOT SURVEYED | NOT SURVEYED | YES if pursued | PENDING |

**Note on "PENDING DIRECT PROVIDER VERIFICATION."** Every row above with this label means the 5G-1 audit could not parse the official source pages at the access date (typically because they are JS-rendered and returned only titles to the fetch tooling, or because direct fetch returned 4xx) and Peter / counsel must obtain the information directly from the provider before relying on it for any decision. Do not promote these labels to verified status without direct provider engagement.

**Note on Yahoo's four-document developer surface.** The "Counsel Review Needed" cell for Yahoo lists four separate documents because 5G-1 §4.3.4 established that a favorable counsel read on yfinance-applicability would have to clear all four (umbrella ToS + Developer API ToU + YDN Guidelines + API T&C), not just the umbrella ToS. The "API Terms control if inconsistent" rule means stricter standards apply on the API surface than on the umbrella ToS.

---

## 5. Legal Counsel Questions

These are the questions Peter should take to qualified counsel. Counsel should add as needed; this list is the floor, not the ceiling.

1. **yfinance for private research only.** Can PRJCT9 use yfinance for private research only — Peter as the sole consumer, no public site, no redistribution to third parties — without material exposure under Yahoo's umbrella ToS or any of Yahoo's three official developer-API documents (Developer API ToU, YDN Guidelines, API T&C)? If yes, are there cache-retention or volume constraints that should govern this private use?

2. **Public display of Yahoo-sourced raw OHLCV.** Can PRJCT9 publicly display Yahoo-sourced raw OHLCV (charts, price tables, volume) on PRJCT9.com? Identify which Yahoo documents (umbrella ToS §2.4(i)/§2.5/§2.8/§2.13; Developer API ToU; YDN Guidelines; API T&C) bear on this question and rank exposure severity.

3. **Public display of derived metrics.** Can PRJCT9 publicly display derived metrics (Sharpe, capture ratios, signal directions, p-values, validation envelopes) computed from Yahoo-sourced OHLCV but not displaying the raw OHLCV itself? Does the "derivative works" language in Yahoo umbrella ToS §2.8 reach these computed analytics, or do they fall outside the scope of "any portion or use of, or access to, the Services"?

4. **Caching as redistribution / reproduction.** Does server-side caching of provider-sourced OHLCV (Spymaster `cache/results/` PKLs, signal-library stable PKLs, Phase 3 manifest-stamped artifacts, controlled-compute run directories) constitute "redistribution" or "reproduction" under the relevant provider terms? Specifically address (a) Yahoo umbrella ToS §2.8 "reproduce" language; (b) YDN Guidelines 24-hour storage limit on Yahoo user data and whether market data falls under "user data"; (c) the chosen paid provider's caching language.

5. **Attribution / disclaimer language.** What attribution or disclaimer language is required on a public PRJCT9.com page that displays provider-sourced data? What user-facing disclaimers are needed to avoid investment-advice confusion under SEC / state securities-law / FTC standards (e.g., "not investment advice," "past performance does not guarantee future results," "no recommendation," "research-only").

6. **Provider contract terms for public display.** What contract terms are required for public web display of delayed EOD OHLCV under each top-candidate provider's standard offering (Polygon business plan, EODHD Startups & Enterprise plan, Tiingo commercial-organization plan, Alpha Vantage commercial agreement)? Are any of these standard, or is each contract individually negotiated?

7. **Provider contract terms for cached server-side delivery.** What contract terms are required for cached server-side delivery (PRJCT9 caches data, then serves it to public visitors from cache rather than re-fetching from provider on every request)? What cache-retention limits apply per provider?

8. **Monetization-mode implications.** What changes if PRJCT9 monetizes through (a) donations only, (b) display advertising, (c) subscription / paywalled tiers, (d) sponsorship / partnership arrangements? Does any of these qualify as "commercial purpose" under Yahoo umbrella ToS §2.5 or under the chosen paid provider's "non-commercial use" boundary?

9. **User-facing disclaimers to avoid investment-advice confusion.** What user-facing disclaimers / warnings / acceptance flows are needed on PRJCT9.com to avoid characterization as investment advice, trading signals, or "best ticker" rankings? Reference the North Star research-honesty guardrail commitment ("the site should distinguish observed historical behavior from prediction") and translate it into specific disclaimer language counsel deems sufficient.

10. **Auditability / record-retention.** What provider/license records must PRJCT9 retain for auditability of Phase 6 launch posture? Examples: provider contract copies, attribution-rendering screenshots at launch, validation-envelope provider-tagging, manifest provider_metadata fields (when implemented), counsel memo capturing the 5G-2 decisions.

11. **International data use / export compliance.** If the chosen Phase 6 universe includes non-US securities (Tier B+ or Tier C from 5G-1 §6.1), what international data-use / export-compliance considerations apply? Different providers have different international-redistribution permissions per 5G-1 §11.8.

12. **Trademark / branding interactions.** What attribution language is permitted for "Yahoo," "Yahoo Finance," and the chosen paid provider's trademarks without crossing trademark-misuse lines? The Yahoo API T&C explicitly prohibits describing a client as "endorsed" / "certified" / "authorized" / "preferred" / "selected" by Yahoo (5G-1 §4.3.3).

---

## 6. Provider Outreach Checklist

For each paid provider candidate Peter pursues (top three: Polygon/Massive, EODHD, Tiingo; secondary: Alpha Vantage; eliminated unless commercial pivot: Twelve Data, Alpaca), use this checklist as the floor for sales/support engagement. Capture answers in the Provider Candidate Decision Matrix (Section 4) with citations.

### 6.1 Display rights

- [ ] Is public web display of delayed EOD OHLCV permitted on a third-party site under the standard tier? Under the commercial / business / enterprise tier?
- [ ] Is public web display of real-time OHLCV permitted? (Real-time is generally subject to per-display-user accounting; expect more restrictive terms.)
- [ ] Is public display of derived analytics (Sharpe, capture ratios, signal directions, p-values) computed from your data permitted? Are derived analytics treated as "your data" or as "derivative works" under your terms?
- [ ] Are charts / tables / equity curves / heatmaps allowed? Any rendering-format restrictions?
- [ ] Are raw CSV / API downloads by anonymous public visitors of PRJCT9.com allowed?
- [ ] Is exporting analytics in a downloadable format (e.g., user clicks "export validation envelope as CSV/PDF") allowed?

### 6.2 Caching and redistribution

- [ ] Is server-side caching of fetched data permitted? If yes, what cache retention limits apply?
- [ ] Is cache-then-serve-to-anonymous-visitors permitted, or must each public request trigger a fresh provider fetch?
- [ ] Is redistribution to anonymous website visitors permitted, or only to authenticated / contracted end-users?
- [ ] If redistribution is permitted, is there a per-display-user fee, a flat fee, a tiered fee?
- [ ] What attribution is required (text, logo, link, position on page, pixel size, branding requirements)?

### 6.3 Coverage and freshness

- [ ] What universe / exchange coverage is included at the standard / commercial / enterprise tier (US equities, ETFs, options, OTC, mutual funds, international, indices)?
- [ ] What historical depth is included? Is depth different per asset class? Is depth different per tier?
- [ ] What freshness is included (real-time, delayed intraday, EOD, T+1)? Is there a difference between API-pull and websocket-stream?
- [ ] Are corporate actions (splits, dividends, mergers) included? How are historical adjustments back-applied? Does the provider supply both adjusted and unadjusted?

### 6.4 Operational limits

- [ ] What rate limits apply per tier (requests/min, requests/day, websocket connections, websocket symbol counts)?
- [ ] Are there burst limits / sustained-rate limits / monthly limits in addition to per-second limits?
- [ ] Is there a sandbox / test endpoint with separate limits?

### 6.5 Pricing and commercial terms

- [ ] What commercial tier is required for PRJCT9.com's planned use (universe, display mode, caching, monetization model)?
- [ ] What monthly / annual cost applies at launch scale (initial Phase 6 traffic estimate)? At scale-up traffic? At anticipated Phase 7+ traffic?
- [ ] What cost triggers apply as traffic grows (per-call fees beyond tier limits, per-display-user fees, overage charges, surge pricing)?
- [ ] What is the contract length, auto-renewal posture, termination-for-convenience clause, and price-change notice?
- [ ] Are there startup / educational / research / open-source discounts available?
- [ ] Are there per-display-user fees (often called "professional vs non-professional" by exchanges)?

### 6.6 Audit and reporting obligations

- [ ] Are there audit / reporting obligations for downstream-user counts, display counts, redistribution volumes?
- [ ] Are there provider-mandated logging or monitoring obligations (e.g., must log every public display)?
- [ ] What termination triggers exist (audit failure, payment delinquency, ToS breach)?

### 6.7 Operational and reliability

- [ ] What is the provider's SLA / uptime commitment / historical reliability?
- [ ] What backup / failover posture does PRJCT9 need (single-provider lock-in vs multi-provider redundancy)?
- [ ] What is the provider's data correction / errata / restatement policy? How are historical corrections distributed?

### 6.8 Per-provider notes

Use this subsection to record provider-specific outreach context that doesn't fit the columnar matrix (Section 4).

- **Polygon / Massive:** Note the corporate rebrand (`polygon.io` → `massive.com`); confirm which entity is the contracting party and which name appears on the contract. Verify history depth, redistribution-license language, and pricing directly because 5G-1 source confidence was LOW at access date.
- **EODHD:** Request the "Startups & Enterprise Data Solution Plan" terms directly. Personal-tier terms are insufficient for Phase 6 public use per 5G-1 §8.7.
- **Tiingo:** Read `app.tiingo.com/tos/` directly with counsel; the redistribution-prohibition language identified in 5G-1 came from third-party search results, not direct fetch. Engage Tiingo sales for commercial-tier pricing.
- **Alpha Vantage:** Verify any "officially licensed by exchanges" claims directly; 5G-1 §8.5 explicitly downgraded those claims to counsel-verification-required. Request commercial / public-display agreement language directly.
- **Twelve Data:** Only engage if a commercial tier exists and Peter wants to re-enter consideration; the individual-plan "non-commercial purposes" language eliminates them at the personal-plan level.
- **Alpaca:** Only engage if Peter wants a separate commercial / data-vendor licensing arrangement beyond the standard market-data agreement. The standard Alpaca market-data agreement is broker-dealer-flavored and not a Phase 6 redistribution path per Alpaca's own support article.

---

## 7. Engineering Consequences By Decision

This section summarizes the likely engineering follow-up for each decision-mode outcome. **It does not authorize any of this engineering.** All engineering work depends on Section 9 sign-off and is scoped under 5G-3 (provider-abstraction) or later phases. Cost / effort estimates are directional only.

### 7.1 If yfinance remains private-only and public site uses paid provider (hybrid)

- **Provider routing layer needed.** A `data_loader.py` / `OhlcvLoader` Protocol module is needed so per-app loaders call into a provider-agnostic interface rather than `yf.download` directly. Per 5G-1 §5.4, the cleanest abstraction seam is `signal_library/multi_timeframe_builder.py`'s retry-wrapper, then ImpactSearch's FastPath monkeypatch (which already wraps `yf.download`), then per-app loaders sequentially.
- **Provider identity must be manifest / validation metadata.** Phase 4A locks the schema-isolation principle; the current partial implementation uses `series_id`, `series_metadata`, and `series_kind`. A future provider-aware implementation would likely extend that with a real `provider_metadata` field across loaders, manifests, validation envelopes, and confluence coverage records (5G-1 §5.6 gap). Provenance manifest schema bumps from v1 to v2; existing manifests default to `provider_metadata={"provider_name": "yfinance"}` or similar at load-time.
- **Validation ledger must avoid cross-provider comparability claims.** Phase 5C `honest_validation_ledger.py` aggregates `validation_contract_v1` sidecars across apps. With two providers in play, the ledger gains provider-bound track separation: yfinance-source results and paid-provider-source results sit on separate validation envelopes with explicit provider tags (5G-1 §10.1 "ledger continuity" risk). No cross-provider Sharpe / capture / p-value aggregation; explicit per-source labeling on every public output.
- **Cache-shape split.** Spymaster `cache/results/` and signal-library stable PKLs must encode provider identity in cache-key paths or filenames so two providers' caches can coexist without collision. Phase 3 manifest content hashes already reflect input bytes, so provider identity is implicit in the hash but should be explicit in the manifest payload for human readability.
- **Spymaster standalone-design contract impact.** CLAUDE.md "Spymaster.py Standalone Design" forbids importing shared modules. Any provider-abstraction layer would either require duplicating the abstraction inside spymaster.py (acceptable, increases code surface) or amending the standalone-design contract (decision for Peter).

### 7.2 If all usage migrates to a paid provider (single-provider posture)

- **yfinance loaders need abstraction or replacement.** Per 5G-1 §5.7 "HIGH effort" estimate, yfinance imports/calls span 7 production files at 14+ call sites. Removing yfinance entirely requires migrating each loader to the new provider's API shape, including symbology translation (Yahoo `^GSPC` → Polygon `I:SPX`, etc.), period parameter conventions, OHLCV column names, and adjustment-math reconciliation.
- **Caches need migration policy.** Existing Spymaster `cache/results/` and signal-library stable PKLs are yfinance-derived. Three options: (a) rebuild caches from new provider (validation envelopes change because adjustment math differs); (b) retain yfinance caches as historical snapshots tagged by provider, do not use for new computation; (c) invalidate caches outright and start fresh at provider switch. Each option has provenance-manifest implications.
- **Historical validation comparability changes.** Provider switch changes the historical price series at every prior split / dividend event because adjustment math differs across providers (5G-1 §5.3, §7.4). Phase 5C honest validation must explicitly note provider-switch boundaries in the ledger; pre-switch and post-switch validation envelopes are not directly comparable.
- **Symbology drift exposure.** The PRJCT9 ~73K GTL universe (CLAUDE.md "Symbol Validity") is yfinance-symbology-anchored. Some yfinance-valid symbols have no equivalent on the new provider; some new-provider-valid symbols have no equivalent on yfinance. Phase 4A coverage transparency exposes this as data, but Peter should expect surprise here.
- **GTL validator replacement.** `global_ticker_library/validator_yahoo.py` is yfinance-anchored. A `validator_<provider>.py` replacement (or generalized validator) is needed.

### 7.3 If yfinance is counsel-cleared for some public derived-only use

- **Public surface must enforce derived-only display.** Phase 6 UI must categorically prevent rendering raw OHLCV (no charts of OHLC values, no price tables, no volume display). Only derived metrics (Sharpe, capture, signals, p-values, validation envelopes, equity curves of strategy returns rather than raw price) are public-surface eligible.
- **Raw OHLCV export / display must be blocked unless cleared.** Counsel may identify specific clearance paths: e.g., raw OHLCV display permitted in private operator-only Dash views (Spymaster, ImpactSearch operator UIs), but blocked in public PRJCT9.com pages. Any "export to CSV / Excel / PDF" feature must respect the clearance posture.
- **Cache and attribution rules must be explicit.** Even derived-only display likely requires Yahoo attribution per counsel guidance. Cache rules (does PRJCT9 cache the raw OHLCV from which derived metrics are computed?) need explicit posture.
- **`provider_metadata` still needed for audit trail.** Even single-provider yfinance posture benefits from `provider_metadata` in manifests so audit / honest-validation discipline can show provenance lineage.
- **Engineering surface area is smallest under this branch.** No provider abstraction needed; public-site display layer needs filtering rules; rest of codebase unchanged.

### 7.4 If BYO-data is required for Phase 7+

- **Provider / contributor provenance schema needed.** Beyond `provider_metadata`, a contributor-tier provenance schema is needed: `contributor_id`, `contributor_license_assertion`, `contributor_upload_timestamp`, `contributor_chain_of_custody`. Per-contribution provenance must survive downstream aggregation in confluence rankings.
- **Contributor license assertions needed.** Each upload requires the contributor to assert licensing rights and indemnify PRJCT9. Counsel-drafted CLA / Contributor License Agreement is required (5G-1 §11.6). Per 5G-1 §7.2, this is the largest legal-scope item in the entire project lifetime.
- **Heterogeneous-provider aggregation rules needed.** Phase 7+ confluence rankings will mix yfinance + paid-provider + volunteer data. The validation-envelope-incomparability problem (5G-1 §7.4) is upstream of any engineering: aggregation across heterogeneous providers cannot produce a single canonical Sharpe / capture / p-value because the underlying price series differ. Either (a) per-provider validation tracks, (b) normalized-price intermediate layer, or (c) explicit "validation envelope is provider-bound" labeling on every output.
- **Display-time attribution chain.** Every public Phase 6 page using contributor data must attribute all sources visibly. Attribution rendering becomes part of the UI contract, not just provenance metadata.
- **Takedown / moderation posture.** Counsel-cleared safe-harbor posture (DMCA-style takedown? whitelist-only providers? per-upload license declaration?) is required before any volunteer data is accepted.

---

## 8. provider_metadata Note

`provider_metadata` is a known code gap surfaced by Phase 5G-1 (`2026-05-08_PHASE_5G_DATA_LICENSING_PRELAUNCH_GATE.md` Section 5.6).

**Status:**

- `provider_metadata` is **not implemented** in production code today. `rg "provider_metadata" project/` returns only doc-source matches in `md_library/` (locked authorities anticipating the future need); no production module uses or emits it.
- The Phase 4A scoping doc (`2026-05-04_PHASE_4_SCOPING.md`) Design Principle 8 ("Schema isolation toward future BYO") locks the schema-isolation **principle** but does not specify a complete `provider_metadata` field shape. The principle is implemented partially via `series_id` / `series_metadata` / `series_kind` (the latter is single-literal `"yfinance_ticker"` in production code today).
- Phase 5C validation methodology (`2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md`) identifies `provider_metadata` as the correct location for provider-specific metadata, but the schema remains open and production `validation_contract_v1` does not contain it today.

**Why this PR does NOT implement provider_metadata:**

- Per 5G-1 Section 15: "No code changes recommended until 5G-2 decisions are made."
- The exact `provider_metadata` schema depends on whether PRJCT9 ends up yfinance-only, paid-provider, hybrid, or BYO-oriented. A schema designed for yfinance-only is too narrow for hybrid; a schema designed for hybrid is over-engineered for yfinance-only; a schema designed for BYO must accommodate contributor-license assertions that don't apply to first-party providers.
- Implementing `provider_metadata` before Section 9 of this template is signed would either bake in wrong assumptions or add code surface that may need to be reworked before it ships.

**When `provider_metadata` should be implemented:**

- After Section 9 of this template is signed.
- As part of Phase 5G-3 if Section 9's "5G-3 required?" decision is "yes."
- Alternatively, addressed via a Phase 4 scoping doc amendment that documents what actually shipped vs aspiration, plus a small implementation PR that adds `provider_metadata` to the manifest schema as a single-default-value field (`provider_metadata={"provider_name": "yfinance"}`) without adding any provider-routing logic. This second path is appropriate if Section 9's decision is "yfinance-only, no provider abstraction" but Peter still wants the audit trail.

**What this template captures about `provider_metadata` for now:**

- It is documented as a known gap with a clear ownership trail back to 5G-1 Section 5.6.
- Its schema is not specified here. Section 9 sign-off plus a follow-up scoping cycle (5G-3 or scoping-amendment) is required before specification.

---

## 9. Final Decision Block

Fill in once Peter and counsel have completed the Section 5 questions, the Section 6 outreach, and reviewed the Section 7 engineering consequences. Until this block is signed and dated by Peter, the template's status remains **TEMPLATE / PENDING PETER + COUNSEL INPUT** and no code changes follow from it.

```
Selected Phase 6 provider:
  ____________________________________________________
  (e.g., Polygon business plan / EODHD enterprise / yfinance with counsel clearance / TBD)

Selected private research provider:
  ____________________________________________________
  (e.g., yfinance status quo / Alpaca free tier / paid provider / TBD)

Selected public display mode:
  [ ] Mode A (raw OHLCV display)
  [ ] Mode B (derived metrics only)
  [ ] Mixed (specify scope below)
  Notes: _______________________________________________

Selected launch universe tier:
  [ ] Tier A (curated 50-200)
  [ ] Tier B (US equities + ETFs)
  [ ] Tier C (full 73K + international)
  [ ] Other (specify): __________________________________

Counsel reviewed by:
  Name / firm: _________________________________________
  Engagement scope: ____________________________________

Counsel review date: __________________________________

Provider contract reviewed:
  Provider: ___________________________________________
  Contract version / date: _____________________________
  NDA in place: yes / no _______________________________

Engineering follow-up required:
  [ ] Provider abstraction layer (`data_loader.py` / `OhlcvLoader` Protocol)
  [ ] `provider_metadata` schema finalization + manifest schema bump
  [ ] Per-app loader migration (list affected files)
  [ ] Spymaster standalone-design contract amendment
  [ ] Validation ledger provider-track separation
  [ ] Cache migration policy
  [ ] GTL validator replacement / extension
  [ ] Public-display filter (derived-only enforcement)
  [ ] Attribution rendering contract
  [ ] Other: __________________________________________

5G-3 required? yes / no:
  ___ (yes if any of the engineering items above are checked)

Notes on divergence from source documents:
  _____________________________________________________
  _____________________________________________________

Approved by Peter:
  Signature: __________________________________________

Date:
  _____________________________________________________
```

---

## 10. How to use this template

This template is intended to be filled in iteratively as Peter engages counsel and provider contacts. Suggested sequence:

1. Peter reviews this template alongside `2026-05-08_PHASE_5G_DATA_LICENSING_PRELAUNCH_GATE.md` (the 5G-1 research foundation).
2. Peter answers the seven Section 14 questions in 5G-1 (which align to most of the Section 3 decision-summary rows here) for self-context.
3. Peter identifies and engages counsel; counsel reviews 5G-1 + this template.
4. Peter (with counsel guidance) drafts Section 5 questions for counsel and Section 6 outreach for top-three provider candidates.
5. Counsel returns memo answering Section 5 questions.
6. Peter executes Section 6 provider outreach for top candidates.
7. Peter and counsel review Section 7 engineering consequences against the chosen path.
8. Peter signs Section 9 Final Decision Block.
9. The signed template is committed as v1 (rename to `2026-MM-DD_PHASE_5G_2_DATA_PROVIDER_DECISION_RECORD.md` if desired; keep this template as v0 history).
10. 5G-3 (engineering) or no-op closure follows from Section 9's decisions.

When this template is signed, it becomes the durable decision record that `2026-05-08_PHASE_5G_DATA_LICENSING_PRELAUNCH_GATE.md` Section 15 is waiting on. Until then, no code changes follow from it.

---

*End of Phase 5G-2 decision record template.*
