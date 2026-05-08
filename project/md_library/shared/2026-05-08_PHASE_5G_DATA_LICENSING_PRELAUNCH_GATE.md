# Phase 5G-1: Data Licensing Pre-Launch Gate Research

**Date:** 2026-05-08

**Status:** RECOMMEND-PHASED-IMPLEMENTATION

**Author:** PRJCT9 sprint (Phase 5G-1 research/scoping artifact)

**Phase:** 5G-1 (data licensing pre-launch gate; parallel to Phase 5D, gates Phase 6)

> **Disclaimer.** This document is not legal advice. It is a technical and licensing-risk scoping artifact for Peter to review with qualified counsel before any public launch or commercial use. All terms-of-service interpretations, provider pricing, and rate-limit details are sourced from public web pages accessed on the dates noted in the Sources Appendix and may have changed since. Cited language is short-quoted for the limited purpose of identifying material questions for legal counsel. Peter should verify pricing, terms, and licensing posture directly with each provider before any commitment.

---

## 1. Executive Summary

The PRJCT9 codebase currently uses `yfinance` as its sole market-data source, threaded through every per-app loader (Spymaster, ImpactSearch, OnePass, StackBuilder, TrafficFlow, signal_library, GTL validator). yfinance accesses Yahoo Finance via reverse-engineered public endpoints. Yahoo's umbrella Terms of Service prohibit automated access without express written permission and prohibit any commercial reuse of Yahoo content; the yfinance README itself states the project is unaffiliated with Yahoo and that "the Yahoo! finance API is intended for personal use only" (yfinance README, accessed 2026-05-08).

**Current state.** yfinance is acceptable as a sprint/research source while PRJCT9 remains a private development tool used by Peter and the audit toolchain. It is **not** acceptable as the data source for a public, indexable Phase 6 launch without explicit legal review.

**Phase 6 is gated by a licensing decision.** Phase 6 (PRJCT9.com public site) cannot launch on yfinance without written sign-off from counsel that Peter is comfortable with the legal exposure, and even then most likely requires changes to the public surface (e.g., derived metrics only, no raw OHLCV display, no caching that constitutes redistribution, attribution structure that satisfies Yahoo Finance LLC's separate terms). Most ToS-compliant paths involve switching all launch-facing data to a paid commercial provider with explicit redistribution rights.

**Phase 7+ BYO-data and volunteer compute amplify complexity.** The North Star (`2026-05-04_PRJCT9_NORTH_STAR.md`) commits to user-contributed data, volunteer compute, and a research-commons knowledge base. Each of those raises licensing-provenance, contributor-license, and aggregator-liability questions far beyond a single-source decision. The codebase's partial schema isolation (`series_id`, `series_metadata`, `series_kind="yfinance_ticker"` in `cross_ticker_confluence.py`) is a foundation but not sufficient on its own.

**Recommended path: phased 5G, not provider switching in this PR.** This document delivers 5G-1 (research). 5G-2 captures Peter's decisions and counsel's review as a durable decision record. 5G-3 (only if launched) does provider-abstraction engineering. 5G-4 / Phase 7+ tackles BYO-data provenance.

**Final status: RECOMMEND-PHASED-IMPLEMENTATION.**

---

## 2. Scope and Non-Goals

**In scope for this PR (5G-1):**

- Research into Yahoo / yfinance terms-of-service posture
- Survey of viable commercial market-data providers for Phase 6
- Audit of codebase coupling to yfinance and provider-specific assumptions
- Decision framework for Peter and legal counsel
- Phase 6 minimum-viable data requirements at scoping level
- Phase 7+ BYO-data implications at framework level

**Out of scope (deferred to later 5G sub-phases or Phase 6+):**

- Actual provider switch
- Per-app loader refactor or provider-abstraction code
- Modifications to `validation_engine.py` / `controlled_compute.py` / `honest_validation_ledger.py`
- Phase 6 UX implementation
- Phase 7+ volunteer compute implementation
- Legal contract negotiation or user-agreement drafting
- Final selection of a paid provider — this document only ranks candidates for evaluation

This document does not switch providers, alter loaders, or make legal/commercial decisions for Peter.

---

## 3. Locked Authorities

This section summarizes findings from the locked PRJCT9 sprint authorities so 5G research aligns with prior decisions rather than relitigating them.

### 3.1 PRJCT9 North Star (`md_library/shared/2026-05-04_PRJCT9_NORTH_STAR.md`)

The North Star explicitly anticipates the licensing question. From the "Data source posture" section:

- "The sprint uses yfinance (Yahoo Finance via the yfinance Python library) as its data source."
- "Yahoo's API terms restrict the library to personal/research use; commercial or large-scale public redistribution carries legal exposure."
- "Rate limiting is real and aggressive."
- "yfinance is unaffiliated with Yahoo and disclaims production use."
- "These constraints become meaningfully consequential when PRJCT9.com goes public-facing at scale."
- "Pre-launch, a separate data licensing review is needed; post-sprint, alternate data providers (Polygon, EODHD, Bloomberg, etc.) or a carefully scoped derived-research posture should be evaluated."
- "The Wikipedia-of-pattern-finding ultimate vision will likely require alternate or user-supplied data sources regardless of the licensing review outcome."

The North Star also commits to two user paths (curated default view + manual ticker entry), an open-source research-commons posture, Phase 7+ crowdsourcing/BYO-data, an accessibility principle (soccer-mom-and-quant), and a research-honesty guardrail. Each interacts with licensing differently: a curated default-view universe is the smallest licensing surface; manual ticker entry on arbitrary user-supplied symbols is larger; volunteer-contributed data in Phase 7+ is the largest and most complex.

### 3.2 Phase 4 Scoping (`md_library/shared/2026-05-04_PHASE_4_SCOPING.md`)

The Phase 4 scoping doc establishes the manifest-stamped data layer that Phase 6 will eventually consume. Decisions captured (May 4 2026, "Decisions captured" section) include:

- "yfinance is the sprint data source; data licensing review deferred to pre-launch"
- "Schema isolation principle: keep canonical shape generic enough that future BYO-data adapters can plug in without rewriting the engine"

Phase 4A's design principle 8 ("Schema isolation toward future BYO") directly anticipates this 5G work. The Phase 4A acceptance criteria preserve this principle: ticker symbols flow as `series_id`, with provider-specific metadata isolated from the generic confluence/ranking/coverage records "where practical." This commitment is a foundation for any future provider-abstraction work but is incomplete (see Section 5 audit for what the codebase actually does today).

### 3.3 Phase 5 Pre-Flight (`md_library/shared/2026-05-05_PHASE_5_PRE_FLIGHT.md`)

The Phase 5 pre-flight establishes Phase 5G as an explicit sub-phase. From "Scope locked" section item 5:

- "Phase 5G — Pre-launch data licensing gate (parallel; doc/process work; not a numbered implementation sub-phase)"
- "Document yfinance constraints"
- "Evaluate alternate data providers for Phase 6 (Polygon, EODHD, Bloomberg, derived-research posture)"
- "Output: a decision memo md_library/ document; not code"
- "Phase 6 final launch scoping cannot lock until 5G is complete because 5G may constrain public launch scope, data provider posture, or redistribution claims"

Design principle 7 ("Licensing gates Phase 6") confirms 5G can block or constrain Phase 6 scope and runs early/parallel rather than last. The implementation phasing notes 5G "may proceed in parallel as early as 5A/5C" and that "Phase 6 final launch scoping cannot lock until this review is complete."

### 3.4 CLAUDE.md current sprint state (`project/CLAUDE.md`)

The CLAUDE.md "Sprint state (as of 2026-05-08)" snapshot lists Phase 5G as NOT STARTED and "Parallel sub-phase that gates Phase 6. Research/legal scope; yfinance ToS review + alternative data source survey." The "Phase 6: PRJCT9.com - NOT STARTED. Public-facing UX / website. Gated by 5G." line confirms the gate relationship.

CLAUDE.md also documents (in "Project Overview" / "Symbol Validity") that PRJCT9 currently treats "any symbol that returns data from Yahoo Finance" as valid — a yfinance-specific definition. This is part of what 5G has to think about: a provider switch may change which symbols are considered valid.

### 3.5 Reading from the locked authorities

Three commitments propagate from the locked authorities into this 5G work:

1. **Open-source, research-commons posture** (North Star) means PRJCT9 cannot solve the Phase 6 launch problem by paywalling the site or restricting data to a closed user base. Whatever data is displayed to the public must be displayable under the chosen provider's terms.
2. **Phase 4A canonical schema isolation** (`series_id`, `series_metadata`, `series_kind`) is a foundation but is currently single-provider in practice (`series_kind="yfinance_ticker"` is the only literal value in code; `provider_metadata` does not exist as a code-level key today). 5G-3 / Phase 7+ work would need to extend this.
3. **Phase 6 launch scoping cannot lock until 5G is complete.** This document is the start of the 5G track; subsequent 5G sub-phases capture Peter's decisions, legal counsel's review, and (only if applicable) provider-abstraction engineering.

---

## 4. Yahoo Finance Terms / yfinance Posture

This section grounds the licensing question in primary sources and labels each finding by clarity (CLEAR / AMBIGUOUS / RISK / NEEDS LEGAL REVIEW). All citations include URL and access date 2026-05-08.

### 4.1 Yahoo Terms of Service (umbrella)

Source: Yahoo Terms of Service, `https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html` (accessed 2026-05-08; reached via redirect chain `policies.yahoo.com -> policies.oath.com -> verizonmedia.com -> legal.yahoo.com`).

Key clauses material to a third-party publishing market data:

- **§2.4(i) Member Conduct — automated access:** "access or collect data, or attempt to access or collect data, from our Services using any automated means, devices, programs, algorithms or methodologies, including but not limited to robots, spiders, scrapers, data mining tools, or data gathering or extraction tools, for any purpose without our express, prior permission."
  - **Label: RISK + NEEDS LEGAL REVIEW.** This is a blanket prohibition on automated access without explicit prior permission. yfinance is automated access and operates without that permission. Whether Peter has any standing to argue an implied license (because Yahoo Finance publishes pages whose URLs yfinance reads) is a question for counsel.

- **§2.5 Use of Services — commercial use:** "Unless otherwise expressly stated, you may not access or reuse the Services, or any portion thereof, for any commercial purpose."
  - **Label: RISK + NEEDS LEGAL REVIEW.** A public-launched PRJCT9.com that displays Yahoo-sourced data is at minimum arguable commercial reuse. Whether free, ad-free, donation-model, or open-source posture qualifies as "non-commercial" is a counsel question.

- **§2.8 Ownership and Reuse — redistribution:** "you must not reproduce, modify, rent, lease, sell, trade, distribute, transmit, broadcast, publicly perform, create derivative works based on, or exploit for any commercial purposes, any portion or use of, or access to, the Services."
  - **Label: RISK + NEEDS LEGAL REVIEW.** Public display of Yahoo-sourced data plausibly constitutes "distribute" / "transmit" / "publicly perform." Caching Yahoo data on PRJCT9 servers plausibly constitutes "reproduce." Whether derived metrics (signals, capture ratios, p-values computed from Yahoo OHLCV) constitute "derivative works" is ambiguous.

- **§2.13 Anti-Abuse Policy — high-volume activity:** "You may not in connection with the Services engage in commercial activity on non-commercial properties or apps or high volume activity without our prior written consent."
  - **Label: RISK.** Phase 6 universe scaling to anywhere near the 73K-symbol universe via yfinance plausibly qualifies as high-volume activity.

- **§14.2(a)(i) Applicable Yahoo Entity:** "Yahoo Inc., except for Yahoo Finance which is provided by Yahoo Finance LLC."
  - **Label: AMBIGUOUS + NEEDS LEGAL REVIEW.** Yahoo Finance is a separate contracting entity. Whether Yahoo Finance LLC has its own product-specific terms beyond what is in the umbrella ToS, and whether those terms are stricter, is a question for counsel. The umbrella ToS appears to govern Finance unless Finance LLC publishes separate product terms.

A separate Yahoo Finance Community Additional Terms page exists at `https://legal.yahoo.com/us/en/yahoo/terms/product-atos/finance/index.html` (accessed 2026-05-08). Those Additional Terms govern the Yahoo Finance Community user-facing surface — user/profile registration, posted content within the Finance Community feature, portfolio-link displays — and they **do not authorize** automated access, scraping, API use of Yahoo Finance market data, caching, redistribution, or public display of Yahoo Finance content beyond user-posted Community content. The Additional Terms make no reference to yfinance or third-party developer tooling. They do not contradict, weaken, or carve out any of the umbrella ToS prohibitions; they layer on top.

### 4.2 yfinance project README

Source: `https://github.com/ranaroussi/yfinance` README (accessed 2026-05-08).

Key disclaimers:

- "yfinance is **not** affiliated, endorsed, or vetted by Yahoo, Inc."
- "It's an open-source tool that uses Yahoo's publicly available APIs, and is intended for research and educational purposes."
- "You should refer to Yahoo!'s terms of use ... for details on your rights to use the actual data downloaded."
- "Remember - the Yahoo! finance API is intended for personal use only." (bolded in the README)

**Label: CLEAR.** yfinance pushes ToS-compliance responsibility entirely to the user. The library does not represent itself as offering any redistribution rights to its users.

### 4.3 Yahoo Developer API / Web Service Terms (official)

Yahoo also publishes official terms for its Developer API surface and Developer Network. These are distinct from the umbrella ToS (Section 4.1) and from the Finance Community Additional Terms (end of Section 4.1). They were not part of the initial 5G-1 draft and are added in this amendment for completeness.

**Important nuance on yfinance applicability.** yfinance does not use Yahoo's documented Developer APIs. yfinance accesses *unofficial / undocumented* Yahoo Finance endpoints (the same endpoints that back the Yahoo Finance website's interactive panels) and the yfinance README disclaims affiliation with Yahoo and posts a "personal use only" stance (Section 4.2). Whether the official Yahoo Developer API / API T&C / YDN Guidelines apply directly to yfinance's specific endpoint usage is a question for legal counsel — the official terms govern Yahoo's documented Developer surface, and Yahoo's enforcement posture on undocumented endpoints is not stated in any of these documents. **NEEDS LEGAL REVIEW.**

That said, these official documents are highly relevant evidence of Yahoo's overall posture toward automated API access, rate limits, caching, attribution, commercial use, and redistribution. Counsel should read them as the closest published statement of Yahoo's intent for any non-individual / non-personal-research use of Yahoo data, even if their direct contractual reach over yfinance is contested.

#### 4.3.1 Yahoo Developer API Terms of Use

Source: `https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html` (accessed 2026-05-08).

Key clauses:

- **Incorporation of umbrella ToS + control language.** Developers must comply with Yahoo's umbrella Terms of Service. Per the document: *"In the event of any inconsistency between these Terms of Use and the TOS, these Terms of Use control."* Developers also agree to the Yahoo Developer Network Guidelines (Section 4.3.2) and *"any and all API-specific implementation documentation, as referenced in the Guidelines."*
- **Rate limits at Yahoo's discretion.** *"Yahoo's APIs may be subject to rate limits at Yahoo's absolute and sole discretion."* Limits exist *"to ensure the availability of the Yahoo APIs and underlying services for all of our users."*
- **API key + attribution.** Developers must comply with the *"Yahoo Developer Network Attribution Policy."* Application identification information (API Key) must be placed in applications. *"You may only create a single API Key per application or service"* and the key *"must accompany all web services requests."* Creating *"any script or other automated tool that attempts to create multiple API Keys"* is prohibited.
- **Sell / lease / share / transfer / sublicense / income prohibition.** Developers *"SHALL NOT ... Sell, lease, share, transfer, or sublicense the Yahoo APIs or access or access codes thereto or derive income from the use or provision of the Yahoo APIs, whether for direct commercial or monetary gain or otherwise"* unless explicitly permitted in writing by Yahoo.
- **Excessive / abusive volume.** Use must not *"exceed reasonable request volume, constitute excessive or abusive usage, or otherwise fail to comply ... with any part of the Yahoo API documentation"* as determined by Yahoo in its sole discretion.
- **Competing products + children services.** Use is restricted *"in a product or service that competes with products or services offered by Yahoo, unless the API Documents specifically permit otherwise"*; child-directed services require express written agreement.

**Label: RISK + NEEDS LEGAL REVIEW.** Even setting aside the yfinance-applicability question, every clause above describes a Yahoo posture inconsistent with PRJCT9.com publicly redistributing or commercially leveraging Yahoo Finance data without explicit written permission. The "API Terms control if inconsistent with ToS" rule means the API surface is held to *stricter*, not laxer, standards than the umbrella ToS.

#### 4.3.2 Yahoo Developer Network Guidelines

Source: `https://legal.yahoo.com/us/en/yahoo/guidelines/ydn/` (accessed 2026-05-08; WebFetch normalized to `https://policies.yahoo.com/us/en/yahoo/guidelines/index.html` content).

Key clauses:

- **Commercial vs non-commercial differs by API.** *"Our commercial usage policies differ depending on the API or Web Service you are using."* Some APIs permit broader commercial access; others are non-commercial-only.
- **Non-commercial-only APIs — restricted contexts.** For non-commercial-only APIs, developers cannot use the services in *"high traffic, established commercial-oriented or business web sites or applications"*; cannot incorporate them into *"applications or websites monetized indirectly (by advertising, affiliate links) or directly"*; cannot access them through *"productivity tools for businesses created to bring value to a business function."*
- **Storage limits for Yahoo user data.** *"You may not store any user data collected through the Yahoo APIs for more than 24 hours, with the exception of information that is explicitly permitted to be indefinitely stored."* Only the GUID (Global Unique Identifier) and Authenticated Token Value may be stored indefinitely. *"All other user information must be requested from Yahoo each time."*
- **Authentication requirements.** Approved authentication methods (Browser-Based Authentication, OAuth) are required when accessing Yahoo user data. Non-commercial-service applications must be registered.

**Label: RISK + NEEDS LEGAL REVIEW.** PRJCT9.com — once public — would arguably be a "high traffic, established commercial-oriented" web site under the YDN Guidelines' non-commercial-only language, regardless of whether PRJCT9 monetizes. The 24-hour storage limit conflicts directly with PRJCT9's existing manifest-stamped cache architecture (Spymaster `cache/results/`, signal-library stable PKLs, Phase 3 manifest-stamped artifacts) which retains data indefinitely. The storage clause refers specifically to "user data collected through the Yahoo APIs" rather than market data, but counsel should evaluate whether Yahoo Finance market data falls under "user data" or under broader content categories also subject to retention limits via other Yahoo agreements.

#### 4.3.3 Yahoo Application Programming Interface Terms and Conditions

Source: `https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apitnc/index.html` (accessed 2026-05-08).

Key clauses:

- **API Data transfer / display restrictions in commercial-account contexts.** *"You may not disclose, sublicense, distribute or otherwise transfer, in whole or in part, any API Data to any third party except to the applicable Account Owner."* Information *"must be: (i) displayed only within the Yahoo API Client; and (ii) disclosed only to the Account Owner to which such information directly pertains."*
- **Quotas at Yahoo's discretion.** *"Yahoo may, at any time and in its sole discretion, set a quota on your usage of the Yahoo APIs or on the amount of data you can transfer using the Yahoo APIs."* Actions designed to increase or avoid quotas, automated collection tools other than the APIs, and activities imposing *"strain or burden on Yahoo's infrastructure"* are prohibited.
- **Redistribution prohibition.** *"You may not disclose or transfer ... your access to the Yahoo APIs, Yahoo API Specifications or your Yahoo API Credentials to any third party"* unless expressly authorized agents have executed written compliance agreements.
- **Attribution / endorsement language constraints.** API Data may be *"referenced as originating from Yahoo and displayed together with a timestamp."* Clients cannot be described as *"endorsed,"* *"certified,"* *"authorized,"* *"preferred,"* *"selected,"* or *"chosen"* by Yahoo. The phrase *"Yahoo-approved third party service"* requires prior written approval.

**Label: RISK + NEEDS LEGAL REVIEW.** The API T&C's display-restriction language (data displayable only within the Yahoo API Client and only to the Account Owner) is, on its face, incompatible with public-website display of Yahoo-sourced data to anonymous visitors. Whether Yahoo Finance market data qualifies as "API Data" under this T&C, and whether yfinance's reverse-engineered access path is "Yahoo APIs" under this T&C, are both counsel questions.

#### 4.3.4 Synthesis with the umbrella ToS

The official Yahoo Developer API / API T&C / YDN Guidelines reinforce — they do not relax — the umbrella ToS analysis in Section 4.1. The three-document developer surface adds:

- explicit storage-time limits on Yahoo-sourced data (24 hours, with narrow exceptions);
- explicit prohibition on "deriving income" from Yahoo APIs even indirectly;
- explicit "no commercial-oriented website" posture on non-commercial APIs;
- explicit display-restriction language incompatible with public anonymous display.

For 5G-2 decision-making, this means: even if counsel decides the umbrella ToS §2.5 commercial-reuse prohibition is interpretively softer than its plain reading, the Developer API T&C and YDN Guidelines independently restrict the same use cases. A favorable counsel read on yfinance-applicability would have to clear *all four* documents (umbrella ToS, Developer API ToU, YDN Guidelines, API T&C), not just the umbrella ToS. **The realistic 5G-2 path is to assume yfinance cannot be used for Phase 6 public launch without separate written permission from Yahoo, regardless of which Yahoo legal document Peter's counsel finds most relevant.**

### 4.4 Resolving the question dimensions

| Question | Yahoo umbrella ToS / yfinance posture | Label |
|---|---|---|
| Personal/research use (private, single user, no public site) | Plausibly tolerated in practice, but §2.4(i) automated-access prohibition is unambiguous; counsel may still flag as exposure | AMBIGUOUS |
| Public display of raw OHLCV on PRJCT9.com | Plausibly violates §2.8 redistribution; commercial reuse §2.5 also implicated | RISK + NEEDS LEGAL REVIEW |
| Public display of derived metrics (signals, Sharpe, capture ratios) | Less clearly "redistribution"; "derivative works" question is ambiguous | NEEDS LEGAL REVIEW |
| Caching/storing yfinance data on PRJCT9 infrastructure | §2.8 "reproduce" plausibly applies; no explicit caching authorization in ToS | RISK |
| Attribution requirements | Not specified in umbrella ToS for non-RSS content; §2.15 governs RSS only | AMBIGUOUS — defaults to needing explicit Yahoo Finance LLC sign-off |
| Commercial-use restrictions | §2.5 prohibits commercial reuse without express statement; §2.13 governs high-volume activity | CLEAR (prohibited without permission) |
| Scraping / automated access / rate enforcement | §2.4(i) prohibits all automated access without prior permission; rate enforcement happens technically (yfinance users frequently hit rate limits) | CLEAR (prohibited) |
| yfinance's stated posture vs Yahoo's actual terms | yfinance disclaims affiliation and pushes responsibility to user; Yahoo ToS does not grant any safe harbor for automated tooling | CLEAR — user bears risk |

### 4.5 Bottom-line posture for the 5G decision

Yahoo's umbrella ToS contains three independent prohibitions (automated access §2.4(i), commercial reuse §2.5, redistribution §2.8) that each independently make a public-launched PRJCT9.com on yfinance data legally exposed. **No carve-out for attribution, fair use, public benefit, or open-source posture exists in the cited ToS clauses.** The Yahoo Developer API ToU, YDN Guidelines, and API T&C (Section 4.3) reinforce, rather than soften, that posture even if counsel concludes those developer-surface documents do not contractually reach yfinance's specific endpoint usage. The yfinance README explicitly tells users to read Yahoo's ToS and bears no liability for users' compliance choices.

This does not mean public launch on yfinance is impossible — it means the path requires:

- Counsel review of whether Yahoo's enforcement risk is material for a project at PRJCT9's scale and posture.
- Counsel decision on whether derived-metrics-only display (no raw OHLCV) provides a meaningfully better posture than full display.
- Counsel decision on whether Peter wants to seek Yahoo Finance LLC's express written permission (likely costly and slow if granted at all).
- Acceptance that any of these decisions is reversible by Yahoo at any time without notice.

The alternative is moving public-facing data to a paid commercial provider whose terms expressly permit the planned use. Section 8 surveys candidates.

---

## 5. Codebase yfinance Coupling Audit

This section inventories how deeply yfinance is wired into PRJCT9 today, so 5G-3 (if reached) can scope a provider-abstraction effort with realistic expectations.

### 5.1 Search commands used

The audit ran the following searches against `project/` (results captured for citation; see Sources Appendix for the static repo references):

```
rg -n "import yfinance|from yfinance" project/
rg -n "yf\.Ticker|yf\.download|\.download\(|history\(period" project/
rg -n "Adj Close|auto_adjust|provider_metadata|series_metadata|series_id|provider_name" project/
rg -n "series_kind|yfinance_ticker" project/
rg -n "cache/results|_precomputed_results" project/
```

### 5.2 Direct yfinance imports / call surface

Production-code direct imports:

| File | Import / call sites (line numbers) | Role |
|---|---|---|
| `project/spymaster.py` | `import yfinance as yf` (line 1); `yf.download(...)` (lines 3577, 3626, 3832, 3836, 3903, 4088); local re-import inside helper (line 3537) | Core regression-baseline app; standalone-by-design (CLAUDE.md "Spymaster.py Standalone Design") |
| `project/impactsearch.py` | `import yfinance as yf` (line 64); `import yfinance as _yf` (line 621); `_ORIG_YF_DOWNLOAD = _yf.download`; `_yf.download = _wrapped_download`; `yf.download = _wrapped_download` (lines 622-628); `yf.download(...)` (lines 1805, 2084) | Cross-asset pattern discovery; FastPath wraps `yf.download` to short-circuit the data-loading path (CLAUDE.md ImpactSearch FastPath) |
| `project/onepass.py` | `import yfinance as yf` (line 19); `yf.download(...)` (line 1625) | Signal generation + ticker-library construction |
| `project/stackbuilder.py` | `import yfinance as yf` (line 193, conditional); `yf.download(sym, period="max", interval="1d", auto_adjust=False, ...)` (line 510) | Multi-primary stack construction |
| `project/trafficflow.py` | `import yfinance as yf` (line 55, conditional); `yf.download(sym, start=..., interval="1d", auto_adjust=False, ...)` (line 924); `yf.Ticker(sym).history(period="max", interval="1d", auto_adjust=False)` (line 1039); `yf.download(sym, start="1960-01-01", ...)` (line 1047); `yf.download(sym, period="max", ...)` (line 1056); `yf.download(sym, start=..., ...)` (line 1246) | Cross-asset traffic-flow analysis |
| `project/signal_library/multi_timeframe_builder.py` | `import yfinance as yf` (line 24); `yf.download(*args, **kwargs)` inside retry wrapper (line 87) | Multi-timeframe interval libraries (1wk, 1mo, 3mo, 1y) |
| `project/stale_check.py` | `import yfinance as yf` (line 27); `yf.Ticker(sym); hist = t.history(period="max", interval="1d", auto_adjust=False)` (lines 104-105) | Stale-input check tool |
| `project/global_ticker_library/validator_yahoo.py` | `import yfinance as yf` (line 22); `yf.download(period="5d", group_by="ticker")` (line 481); `yf.Ticker(sym).history(period="1mo", ...)` (line 538); module docstring describes the full validation strategy as "Batch check via `yf.download`... Fallback only for missing/ambiguous tickers with concurrent `Ticker().history`" (lines 5-6) | GTL universe validation |
| `project/global_ticker_library/tools/diagnose_rate_limits.py` | `import yfinance as yf` (line 9); `yf.Ticker(...)` calls throughout | Rate-limit diagnostics tool |

Out-of-scope-but-noted:

- `project/QC/Clone of Project 9/main.py` is a parked QC clone (CLAUDE.md "Deferred work" / "QC clone") — frozen historical snapshot; excluded from sprint sweeps.
- `project/test_scripts/test_cross_ticker_confluence.py:736` does `import yfinance` only to skip a network-dependent test when yfinance is not available.

### 5.3 Provider-specific assumptions ubiquitous in code

The audit found provider-specific assumptions woven through every per-app loader:

- **Ticker-symbol convention.** Yahoo's symbol grammar (e.g., `^GSPC`, `BRK-B`, `RY.TO`, `EURUSD=X`) is hard-coded in `cross_ticker_confluence.py` `_VALID_SYMBOL_RE = re.compile(r"^[A-Z0-9.\^=+-]+$")` (line 235) and in CLAUDE.md "Symbol Validity" ("Symbols ending in MM (money market), X (mutual funds), or with dots are legitimate"). Other providers use different symbology (e.g., Polygon's `BRK.B`; Tiingo's `BRKB`; FRED's series codes). A provider switch requires symbol-translation logic.
- **`period="max"` parameter.** Used in `stackbuilder.py:510`, `trafficflow.py:1039`, `trafficflow.py:1056`, `stale_check.py:105`, `signal_library/multi_timeframe_builder.py` — yfinance-specific shorthand for "all available history." Other providers express this as explicit `start_date` parameters.
- **OHLCV column names.** `Open`, `High`, `Low`, `Close`, `Volume`, `Adj Close` are yfinance pandas-DataFrame conventions. `auto_adjust=False` (passed to almost every `yf.download` / `yf.Ticker.history` call) preserves `Adj Close` as a separate column. Other providers return JSON or distinct column conventions (e.g., Polygon's `o/h/l/c/v`, Alpaca's `OpenPrice/HighPrice/...`, Tiingo's `adjOpen/adjHigh/...`).
- **`Adj Close` column usage.** Phase 1B Entry 1 (`md_library/shared/2026-05-01_PHASE_1B_INTENTIONAL_DELTA_LEDGER.md`) removed Adj-Close-based scoring from the production engines, but the **column itself is still requested from yfinance** in many fetchers (every `auto_adjust=False` call site preserves it; `md_library/trafficflow/2025-10-06_TRAFFICFLOW_SPYMASTER_PARITY_REPAIR_GUIDE.md:169` uses `yf.download('SBIT', start='2024-01-01')['Adj Close']` directly). A provider switch requires choosing whether to receive split/dividend-adjusted prices, unadjusted prices, or both, and how to map the chosen provider's adjustment convention onto the existing Phase 1B `Close`-based scoring contract. Polygon, Alpaca, Tiingo, and EODHD all expose this differently (some supply both adjusted and unadjusted in one call; some require separate endpoints; some apply different split/dividend mathematics).
- **Split/dividend adjustment math.** Yahoo uses a specific back-adjustment convention. Provider migration changes the historical price series at every prior split/dividend event, which materially changes every backtest, every Sharpe, every capture ratio, every signal — not just at the boundary but throughout history. This is a big problem for validation comparability across providers (Section 7.4).
- **yfinance cache shapes.** Spymaster's `cache/results/<TICKER>_precomputed_results.pkl` (CLAUDE.md "Data Files") is a yfinance-derived OHLCV cache. Signal libraries (`signal_library/`) store stable PKLs that consume yfinance OHLCV (CLAUDE.md "Data Flow" item 1). Phase 3A provenance manifests (`project/provenance_manifest.py`) hash these caches but do not record provider-specific metadata in a way that would let two providers' caches coexist or be distinguished.

### 5.4 Per-app loader coupling — summary table

| Area | Files / Functions | yfinance Coupling | Provider-Abstraction Readiness | Notes |
|---|---|---|---|---|
| Spymaster | `spymaster.py` 6+ `yf.download` call sites | Direct, deeply embedded; standalone-by-design (CLAUDE.md) | LOW — would have to break standalone contract or duplicate the abstraction inside spymaster | Regression baseline; CLAUDE.md forbids signal_library imports |
| ImpactSearch | `impactsearch.py:64,621-628,1805,2084` | Direct + monkeypatches `yf.download`/`_yf.download` for FastPath | MEDIUM — FastPath already wraps `yf.download`; the wrapper is a natural seam to swap out the underlying provider | FastPath gate pattern is well-isolated; provider swap would need to keep FastPath signature stable |
| OnePass | `onepass.py:19,1625` | Direct | MEDIUM | Single call site; simpler swap surface than spymaster |
| StackBuilder | `stackbuilder.py:193,510` | Direct, conditional import | MEDIUM | Single call site; simpler swap surface |
| TrafficFlow | `trafficflow.py:55,924,1039,1047,1056,1246` | Direct, multiple fallback strategies (`Ticker.history` + `yf.download(start=...)` + `yf.download(period="max")`) | LOW-MEDIUM — multiple call shapes embedded; the fallback ladder is itself yfinance-specific | TrafficFlow has matrix.py disabled-code-path concerns separately |
| OnePass / `run.py` ticker universe | `run.py` (unaudited line-by-line; references GTL universe construction) | Indirect via GTL validator | MEDIUM | Universe construction is yfinance-symbology-anchored |
| signal_library multi-timeframe | `signal_library/multi_timeframe_builder.py:24,87` | Wrapped `yf.download` with timeout/retry | HIGH — wrapper is a clean seam | Best provider-abstraction candidate in the codebase today |
| signal_library other modules | various consumers (per repo audit) | Indirect — consume OHLCV DataFrames produced upstream | HIGH if upstream is abstracted | Consumers don't import yfinance directly |
| GTL validator | `global_ticker_library/validator_yahoo.py:22,481,538` | Direct; module docstring centers Yahoo as the validation source | LOW — an alternative validator would be a parallel module rather than a swap; the file name encodes provider | Provider switch likely means a new `validator_<provider>.py` rather than a refactor |
| GTL rate-limit diagnostics | `global_ticker_library/tools/diagnose_rate_limits.py:9-` | Direct, yfinance-specific tool | N/A — diagnostic tool, not a load path | Tool would be re-written or supplemented |

### 5.5 Cache coupling

- `cache/results/<TICKER>_precomputed_results.pkl` (Spymaster) — yfinance-derived OHLCV cache, written and read by Spymaster only.
- `cache/status/<TICKER>_status.json` — Spymaster job-status tracking.
- Signal-library stable PKLs under `signal_library/` — consumed by OnePass-derived workflows; their content is yfinance-derived OHLCV transformed into signals/manifests via the Phase 3A provenance manifest system.
- TrafficFlow internal caches and the cross_ticker confluence run directories — all yfinance-derived in current production.

A provider switch would require a strategy for these caches: rebuild from new provider, retain as yfinance-tagged historical snapshots, or invalidate. Phase 3A provenance manifests (`project/provenance_manifest.py`) hash the input artifacts but do not currently record provider identity in the manifest payload, so without a manifest-schema bump the system cannot tell yfinance-cached and provider-X-cached artifacts apart at verification time.

### 5.6 Manifest / schema isolation reality check

Phase 4A's "schema isolation toward future BYO" principle is partially implemented:

- `cross_ticker_confluence.py:168` defines `series_id: str` on `UniverseSeriesEntry`.
- `cross_ticker_confluence.py:169` defines `series_kind: str` on the same dataclass.
- `cross_ticker_confluence.py:379` populates `series_kind="yfinance_ticker"` — and this is the **only** literal value of `series_kind` in production code today (test fixtures at `test_scripts/test_cross_ticker_confluence_dash.py:77,146` mirror that single literal).
- `series_metadata` exists as a dict-shaped field in coverage records (`cross_ticker_confluence.py:883,1262-1263,1335-1336`) and in the per-series flow.
- **`provider_metadata` does not exist as a code-level key today.** `rg "provider_metadata"` against `project/` returns only doc-source matches in `md_library/` (locked authorities anticipating the future need); no production module uses or emits it.
- `provenance_manifest.py` likewise does not track provider identity in the manifest payload (no `provider_name` or `provider_metadata` field).

**What this means for 5G-3 (provider-abstraction engineering, if reached):**

- The `series_id` / `series_kind` / `series_metadata` triad is a real foundation that would let a `series_kind="polygon_ticker"` or `series_kind="user_csv"` co-exist with `series_kind="yfinance_ticker"` in confluence outputs.
- But the per-app loader paths (Section 5.4 table) are not currently wired through that abstraction — they call `yf.download` directly, not a provider-agnostic `load_ohlcv(series_id, ...)` interface.
- Adding `provider_metadata` to provenance manifests is a manifest-schema bump (new field; existing manifests would default to `provider_metadata={"provider_name": "yfinance"}` or similar).
- A faithful provider abstraction would require a `data_loader.py`-style module that fronts an `OhlcvLoader` Protocol, and per-app loaders would call into that instead of yfinance directly. This is a non-trivial refactor across 7+ files.

### 5.7 Effort estimate to add a second provider (e.g., Polygon)

**Estimate: HIGH effort.**

- **Why HIGH (not Medium):**
  - Direct `yf.download` / `yf.Ticker` call sites span 7 production files at 14+ call sites.
  - Spymaster's standalone-by-design contract (CLAUDE.md "Spymaster.py Standalone Design") forbids importing shared modules — so any provider-abstraction layer would either have to be duplicated inside spymaster.py or the standalone contract would have to be amended.
  - Symbol-translation (Yahoo `^GSPC` → Polygon `I:SPX`, etc.) is its own subproject. The symbol set would shift; some yfinance-valid symbols have no Polygon equivalent and vice versa.
  - Adjustment-math reconciliation across providers is not a one-time fix — it changes every historical price, which propagates into every signal, every backtest, every metric. Validation comparability (Section 7.4) is a real research-honesty problem, not just an engineering one.
  - Provenance manifest schema bump (`provider_metadata`) plus a careful migration story for existing yfinance caches.
  - GTL validator is yfinance-anchored; a `validator_polygon.py` or generalized validator is needed, plus a strategy for tickers that validate on one provider but not the other.

- **Likely-touched modules (based on the Section 5.4 table):**
  - `spymaster.py`, `impactsearch.py`, `onepass.py`, `stackbuilder.py`, `trafficflow.py`, `signal_library/multi_timeframe_builder.py`, `stale_check.py`, `cross_ticker_confluence.py`, `provenance_manifest.py` (manifest-schema bump), `global_ticker_library/validator_*.py`, plus a new `data_loader.py`-style abstraction module and `series_kind` enum extension.
  - Test infrastructure: every test that consumes yfinance-shaped OHLCV (numerous) would need provider-tagged fixtures.

- **Biggest risks:**
  - Validation envelope incomparability across providers (changes Phase 5C honest-validation outputs).
  - Spymaster standalone-design contract (CLAUDE.md) breakage.
  - Cache-shape migration risk during cutover.
  - Symbology drift surfacing as silently-skipped tickers in confluence coverage reports.
  - Manifest hash drift — every provenance hash changes when the underlying OHLCV source changes.

The "Low / Medium / High" answer is **High**, with the understanding that an incremental approach (one app at a time, starting with the cleanest seam — `signal_library/multi_timeframe_builder.py` — and using the FastPath wrapper pattern as a model for ImpactSearch) reduces risk but extends timeline.

---

## 6. Phase 6 Data Requirements

This section frames minimum-viable Phase 6 launch requirements as a recommendation framework for Peter, not a final selection.

### 6.1 Universe size

Three plausible launch tiers:

- **Tier A (smallest):** ~50-200 well-known US equities + major ETFs (SPY, QQQ, IWM, sector SPDRs) + major indices (^GSPC, ^DJI, ^IXIC). Easiest licensing surface; cheapest provider tier; aligns with the North Star "Phase 6 launches with curated/tiered universe, not full 73K" decision (Phase 4 Scoping captured decision).
- **Tier B (medium):** US equities + ETFs across exchanges (NYSE, Nasdaq) — roughly 8K-12K symbols. Requires US-equities provider plan; may exceed cheapest tiers' query budgets.
- **Tier C (largest):** Full PRJCT9 ~73K-symbol GTL universe including international, OTC, mutual funds. Likely requires enterprise-tier provider; international coverage varies materially across providers.

**Recommendation framework:** Phase 6 minimum-viable launch is Tier A, with Tier B as a stretch and Tier C deferred to Phase 7+ alongside BYO-data direction. This aligns with the locked authorities' "curated/tiered launch" commitment.

### 6.2 History depth

Three plausible launch depths:

- **10-year depth:** Sufficient for most validation-window outcomes (252 trading days × ~10 years gives backtests with >2,000 forward-return observations). Cheapest plan tier on most providers.
- **20-year depth:** Better for full-cycle backtests including 2008 stress; more expensive.
- **Maximum-history depth:** Aligns with current PRJCT9 `period="max"` posture; required for symbols with deep inception dates (e.g., ^GSPC since 1927). Most expensive; some providers cap free / lower tiers at 5-10 years.

**Recommendation framework:** Phase 6 minimum is 10-year depth, with 20-year on launch-blockers' priority list.

### 6.3 Freshness

- **EOD (end-of-day):** Aligns with current Phase 4A daily refresh cadence (Phase 5 Pre-Flight 5D-3 job-type taxonomy: "Daily OnePass refresh + Phase 4A aggregation: nightly"). Sufficient for the "Curated Default View" path.
- **Delayed intraday (15-min):** Could power a richer manual-entry path. Free Alpaca tier offers this.
- **Real-time:** Most expensive; not required for North Star research-tool posture.

**Recommendation framework:** EOD is the floor for Phase 6 launch; delayed intraday is a Phase 7+ enhancement.

### 6.4 Public display modes

The North Star's "research-honesty guardrail" + "engine vs presentation" separation creates two display options:

- **Mode A (raw OHLCV display):** Charts, price tables, volume — looks most like a market-data site. Highest ToS exposure on yfinance; requires explicit redistribution rights from a paid provider for a public site. Best UX.
- **Mode B (derived metrics only):** Sharpe, capture ratios, signal directions, p-values, validation envelopes — does not display raw prices, only PRJCT9-computed analytics. Plausibly lower ToS exposure (the "derivative works" question, §2.8). Aligns with the North Star description that the site is "a tool for finding patterns ... not a leaderboard, not a stock-picking service, not a trading signal feed."

**Recommendation framework:** Counsel should evaluate whether Mode B materially reduces yfinance ToS exposure. If yes, derived-metrics-only is a viable yfinance-launch posture. If counsel sees Mode B as still-exposed, paid-provider switch is the path forward.

### 6.5 Server-side caching

- **PRJCT9 cache (current state):** Spymaster `cache/results/`, signal_library stable PKLs, Phase 3 manifest-stamped artifacts. Yahoo §2.8 "reproduce" prohibition arguably applies; counsel question.
- **Provider-cache only (e.g., Polygon serves data on every request):** Simpler license posture but worse rate-limit and latency profile.
- **No caching:** Not viable at Phase 6 scale — every chart load would re-fetch.

**Recommendation framework:** Server-side caching is operationally required. The licensing question is whether the chosen provider's terms expressly permit it — most paid commercial providers do, with caveats. yfinance does not.

### 6.6 Attribution and branding

The North Star "open-source research commons" posture suggests prominent provider attribution is acceptable and probably desirable. FRED's "Public Domain: Citation requested" and "Copyrighted: Citation required" model (Section 8.8) is a reasonable template. Most paid providers also require attribution.

### 6.7 Rate limits

Phase 6 traffic estimate is highly speculative, but a research-tool with the soccer-mom-and-quant accessibility goal could plausibly serve hundreds to thousands of unique-ticker requests per day at launch. EOD-cadence + server-side caching dramatically reduces this. The provider survey (Section 8) reports per-tier rate limits.

### 6.8 Commercial terms

The North Star posture is open-source, research-commons, public-good — not commercial. But "non-commercial" under most providers' contracts has narrow definitions and may exclude any site with display advertising, donation mechanisms, premium features, or paywalled tiers. Counsel should evaluate whether PRJCT9.com's eventual revenue posture (if any) qualifies as "commercial" under the chosen provider's terms.

### 6.9 Minimum viable Phase 6 launch data surface — recommendation

| Dimension | Phase 6 minimum | Phase 6 stretch | Phase 7+ |
|---|---|---|---|
| Universe | Tier A (curated 50-200) | Tier B (US equities + ETFs) | Tier C (full 73K + international) |
| History depth | 10 years | 20 years | Maximum available |
| Freshness | EOD | Delayed intraday | Real-time |
| Display mode | Mode B (derived metrics) OR Mode A on paid provider | Mode A on paid provider | Mode A + interactive charts |
| Caching | Server-side cache, manifest-stamped, attributed | (same) | Multi-provider distinguishable |
| Provider | Single paid provider with redistribution rights | (same) | Multi-provider + BYO-data adapters |

---

## 7. Phase 7+ BYO-Data / Volunteer Compute Implications

Grounded in the North Star (`md_library/shared/2026-05-04_PRJCT9_NORTH_STAR.md` "Crowdsourcing direction" + "Bring-your-own-data direction" + "How phases reference this document").

### 7.1 Multiple provider support

Once Phase 6 ships on a single paid provider, Phase 7+ would be expected to add:

- yfinance preserved as a private-research source (Peter's existing flows continue).
- Optional second paid provider for redundancy / coverage gaps.
- Volunteer-contributed data via BYO-data adapters.

This requires `provider_metadata` to be a real first-class field across loaders, manifests, validation envelopes, and confluence coverage records. As Section 5.6 noted, this field does not exist in production code today.

### 7.2 User-contributed data and licensing claims

If a volunteer uploads OHLCV data they claim is public-domain, PRJCT9 needs:

- A contributor license agreement (CLA) defining the rights the contributor grants and warrants.
- A liability allocation: who is responsible if uploaded data turns out to be licensed-from-elsewhere?
- A provenance trail: who uploaded what, when, with what claimed source.
- Verification posture: PRJCT9 cannot independently verify every uploader's licensing claim. Counsel guidance is needed on safe-harbor postures (DMCA-style takedown? whitelist-only providers? per-upload license declaration?).

This is the largest legal scope-creep risk in the entire project.

### 7.3 Per-contribution provenance + attribution chain

Phase 3 provenance manifests (`provenance_manifest.py`) handle artifact-level provenance. A contributor layer would need:

- Per-contribution ID linking uploaded data → contributor identity → license declaration.
- Attribution chain that survives downstream aggregation (a confluence ranking computed across yfinance + Polygon + volunteer-X data needs to record all three sources' attribution).
- Display-time attribution rendering: every public Phase 6 page that uses contributor data should attribute it visibly.

### 7.4 Aggregating results across heterogeneous providers — validation envelope comparability

This is the technical-honesty problem. Phase 5C honest validation (`validation_methodology_v1`) computes Sharpe, capture, p-values per signal. If those signals are computed from yfinance OHLCV vs Polygon OHLCV vs volunteer-uploaded OHLCV, the underlying price series will differ at every prior split/dividend event (because each source applies different adjustment math) and the validation envelopes will not be directly comparable.

Implications:

- A volunteer-uploaded backtest result using their own data CANNOT be aggregated into the canonical PRJCT9 validation ledger as a peer of Peter's yfinance-source results. The envelopes are not on the same numerical scale.
- Cross-provider validation comparability would require either (a) per-provider validation tracks, (b) a normalized-price intermediate layer that all providers' raw data is mapped onto first (a substantial research effort), or (c) explicit "validation envelope is provider-bound" labeling on every output.

This is why provider_metadata isolation in the codebase (Section 5.6) helps but is not sufficient alone — the engine could route by provider, but the **statistical comparability** problem is upstream of any engineering abstraction.

### 7.5 Classification of Phase 7+ decisions

| Decision | Must decide before Phase 6 | Can defer to Phase 7+ | Needs legal counsel |
|---|---|---|---|
| Whether Phase 6 launches on yfinance or paid provider | YES | — | YES |
| Whether Phase 6 displays raw OHLCV vs derived metrics | YES | — | YES |
| Which paid provider is chosen | YES (if not yfinance) | — | NO (counsel reviews chosen provider's terms) |
| Whether server-side caching is permitted under chosen terms | YES | — | YES |
| Attribution scheme on Phase 6 site | YES | — | partially |
| Provider abstraction infrastructure (`data_loader.py`, `OhlcvLoader` Protocol) | NO if single-provider launch | YES if multi-provider needed | NO (engineering work) |
| Contributor License Agreement (CLA) shape | NO | YES | YES (heavy) |
| BYO-data ingestion adapter design | NO | YES | partially |
| Volunteer compute attestation / authentication | NO | YES | partially |
| Validation envelope comparability across providers | NO if single-provider launch | YES if multi-provider | NO (research-methodology question, but ties to honest-validation claims posture) |
| User-agreement / disclaimer text for Phase 6 | YES | — | YES |
| International data use / export compliance | depends on universe scope at launch | YES at full Tier C | YES |

---

## 8. Provider Candidate Survey

Each entry includes URL and access date. **Pricing reflects published values at access date; counsel and Peter must verify with each provider directly before any commitment.**

### 8.1 Polygon.io / Massive

- **Source confidence: LOW from official sources at the access date.** The official pricing surface at `https://polygon.io/pricing` redirects to `https://massive.com/pricing`, which returned only a page title to direct fetch tooling at access date 2026-05-08 — likely JS-rendered. The `https://polygon.io/` stocks landing page resolves but did not yield published-tier detail through fetch tooling. **Direct official verification pending.** Peter or counsel must consult the live Polygon / Massive pricing page directly before relying on any specific dollar figure or tier feature claim below.
- **Source URLs:** `https://polygon.io/`, `https://polygon.io/pricing` (redirects to `https://massive.com/pricing`) (accessed 2026-05-08; both surfaces returned little parseable content).
- **Coverage (general posture, official):** Polygon's stocks API surface advertises US equities, options, forex, crypto data. Tiered by asset class — multiple subscriptions may be needed for multi-asset coverage. Specific per-tier coverage requires direct verification.
- **History depth:** Direct official verification pending.
- **Adjusted data:** Both adjusted and unadjusted OHLCV are referenced in Polygon's documentation generally; specific endpoint shapes require direct verification.
- **API limits:** Direct official verification pending.
- **Public display / redistribution clarity:** **Direct official verification pending. NEEDS LEGAL REVIEW.** Polygon publishes tier-differentiated licensing language; counsel should request the current Polygon / Massive license terms directly from the provider (under NDA if needed) and review them against PRJCT9's planned Phase 6 use.
- **Pricing posture:** Direct official verification pending. Specific dollar figures should not be relied on from this document.
- **Python ecosystem:** Mature (`polygon-api-client` package is the official Polygon-published Python client).
- **PRJCT9 fit:** Strong general reputation in PRJCT9-class research projects — Polygon is a frequently-cited candidate for replacing yfinance in commercial contexts. Specific fit depends on tier features and redistribution-license terms that this document could not verify directly. Treat as a leading candidate to *evaluate*, not as a vetted choice.
- **Main unknowns:** Current published price per tier; current redistribution-license exact language; current commercial vs developer tier separation; international coverage; corporate rebrand to "Massive" implications for product roadmap.
- **Unverified third-party context:** Several third-party reviews and comparisons (e.g., `https://www.crackingmarkets.com/comparing-affordable-intraday-data-sources-tradestation-vs-polygon-vs-alpaca/`, `https://medium.com/@yolotrading/a-complete-review-of-the-polygon-io-api-everything-you-wanted-to-know-c79e992a74ff`, `https://api.market/blog/MagicAPI/stock-market-api/best-api-for-stock-market-data-all-over-the-world-2026`, accessed 2026-05-08) describe Polygon's developer tiers and stocks history depth. These third-party claims should NOT drive Peter's decision and are listed only as context. Verification with Polygon directly is required.

### 8.2 Alpaca

- **Source confidence: MEDIUM from official sources** — Alpaca's own data-product page returned tier and rate-limit detail to direct fetch tooling, and Alpaca publishes an official support article on the redistribution question.
- **Source URLs (official):** `https://alpaca.markets/data` (Alpaca official data-product page, accessed 2026-05-08); `https://alpaca.markets/support/redistribute-alpaca-api` (Alpaca official support article on redistribution, accessed 2026-05-08).
- **Coverage:** US equities, ETFs, options (indicative on Free / real-time on paid), crypto.
- **History depth:** "7+ years" of historical data per the official data-product page.
- **Adjusted data:** Both adjusted and unadjusted via API; exact mechanics in docs.
- **API limits:** Free 200/min (15-min delayed via API; real-time via websocket); Algo Trader Plus $99/mo unlimited (real-time) per the official data-product page.
- **Public display / redistribution clarity:** **Per Alpaca's official support article at `https://alpaca.markets/support/redistribute-alpaca-api`, Alpaca API data cannot be redistributed for business purpose under the standard market-data agreement.** This is Alpaca's stated official position, not a third-party characterization. **NEEDS LEGAL REVIEW (interpretation):** Alpaca is a broker-dealer, not a data vendor; their licensing structure assumes the data is consumed by the account holder, not redistributed publicly. Counsel should evaluate whether Alpaca offers any separate commercial / data-vendor licensing arrangement that would permit Phase 6 public-redistribution use. Absent such a separate license, this likely makes Alpaca a poor fit for Phase 6 public display.
- **Pricing posture (official):** Free / $99 mo (Algo Trader Plus).
- **Python ecosystem:** Mature (`alpaca-py`).
- **PRJCT9 fit:** Excellent for *private* research (Peter's own use, replacement for yfinance for that purpose, since redistribution is not implicated when Peter is the sole consumer); poor for *public* Phase 6 display absent a separate, appropriate commercial license obtained directly from Alpaca.
- **Main unknowns:** Whether any commercial / data-vendor agreement is available for redistribution; their current detailed market-data terms beyond the support article.

### 8.3 IEX Cloud

- **Source:** Multiple shutdown notices including `https://iexcloud.org`, `https://www.alphavantage.co/iexcloud_shutdown_analysis_and_migration/`, `https://www.insightbig.com/post/iex-cloud-shutting-down-a-complete-python-migration-guide` (accessed 2026-05-08).
- **Status:** **SHUT DOWN 2024-08-31.** IEX Cloud announced retirement on 2024-05-31 and ceased service 2024-08-31 because IEX Cloud was less than 2% of IEX Group's revenue and operating at a loss. Existing customers were referred to Intrinio; assets were sold to Bluesky API which preserves a similar API schema but with potentially different licensing.
- **PRJCT9 fit:** N/A — provider no longer exists. **Eliminate from candidate set.**
- **Successor consideration:** Bluesky API and Intrinio are mentioned as IEX Cloud successors. Both warrant separate evaluation if the field is widened, but neither was on Codex's preflight candidate list and both should be added as later 5G research items if Polygon / EODHD / Tiingo do not produce a satisfactory candidate.

### 8.4 Tiingo

- **Source confidence: LOW from official sources at the access date.** The official Tiingo pricing pages (`https://www.tiingo.com/pricing`, `https://www.tiingo.com/about/pricing`, `https://www.tiingo.com/products/end-of-day-stock-price-data`) returned only page titles to direct fetch tooling at access date 2026-05-08 — likely JS-rendered. **Direct official verification pending.** Peter must consult Tiingo's pricing pages, terms-of-use page, and (for commercial use) Tiingo sales directly before relying on any specific tier or term claim below.
- **Source URLs (official, fetch-pending):** `https://www.tiingo.com/pricing`, `https://www.tiingo.com/about/pricing`, `https://www.tiingo.com/products/end-of-day-stock-price-data`, `https://app.tiingo.com/tos/` (accessed 2026-05-08; fetch returned only page titles).
- **Coverage (general posture):** End-of-day and real-time APIs for equities, mutual funds, ETFs, forex, and crypto, plus corporate actions, fundamentals, and news per Tiingo's product pages. **Direct verification pending** for current per-tier coverage and limits.
- **History depth:** **Direct verification pending.**
- **Adjusted data:** `adjOpen/adjHigh/adjLow/adjClose` plus raw fields per Tiingo's API documentation generally; specific endpoint shapes require direct verification.
- **API limits:** **Direct verification pending.**
- **Public display / redistribution clarity:** **Direct verification pending.** Tiingo's terms-of-use page (`https://app.tiingo.com/tos/`) is the authoritative source for redistribution language; counsel should review it directly. The structural pattern of free Starter / paid individual / commercial-organization tiers is generally documented by Tiingo. **NEEDS LEGAL REVIEW.**
- **Pricing posture:** **Direct verification pending.** Specific dollar figures should not be relied on from this document.
- **Python ecosystem:** Multiple community wrappers; well-documented API.
- **PRJCT9 fit:** Generally well-regarded for long-history equity data in the research community; specific fit depends on tier features and redistribution-license terms that this document could not verify directly. Treat as a candidate to *evaluate* directly with Tiingo, not as a vetted choice.
- **Main unknowns:** Current commercial-tier pricing; exact redistribution language for equities EOD; whether any liberal-license model exists for equities; current per-tier rate limits.
- **Unverified third-party context:** Third-party reviews and search-result aggregations describe Tiingo's tiers, history depth, and redistribution posture (e.g., references to `https://www.findmymoat.com/tools/tiingo`, `https://www.quantstart.com/articles/evaluating-data-coverage-with-tiingo/`, accessed 2026-05-08). These third-party claims should NOT drive Peter's decision and are listed only as context. Verification with Tiingo directly is required.

### 8.5 Alpha Vantage

- **Source confidence: MEDIUM from official sources** — Alpha Vantage's premium pricing page returned tier and rate-limit detail to direct fetch tooling; ToS and realtime-policy pages are referenced as official URLs but were not parsed directly in this audit.
- **Source URLs (official):** `https://www.alphavantage.co/premium/` (premium pricing, accessed 2026-05-08, content parsed directly); `https://www.alphavantage.co/terms_of_service/`, `https://www.alphavantage.co/realtime_data_policy/` (accessed 2026-05-08; URLs cited as official; content not parsed directly in this audit).
- **Coverage:** US equities + global per Alpha Vantage's general documentation; macro indicators; FX; crypto. Direct per-tier coverage detail requires verification.
- **History depth:** Not specified in pricing page extract; **direct verification pending.**
- **Adjusted data:** Adjusted + unadjusted via TIME_SERIES_DAILY_ADJUSTED endpoint per Alpha Vantage API documentation.
- **API limits (official, from premium page):** $49.99/mo (75 req/min) up through $249.99/mo (1200 req/min); annual plans discounted; "no daily limits" on premium.
- **Public display / redistribution clarity:** Personal / non-commercial use covered by premium subscription; **commercial and public-display use require a separately negotiated agreement.** **Direct verification pending** for the exact commercial-license language; counsel should request Alpha Vantage's commercial agreement directly. Statements that Alpha Vantage is "officially licensed by exchanges" appear in third-party commentary but were not verified directly against an Alpha Vantage official document in this audit; **counsel should verify any exchange-license claims directly with Alpha Vantage rather than relying on third-party characterizations**, since those claims (if true) would materially affect public-display rights cleanliness.
- **Pricing posture (official, from premium page):** Premium $49.99-$249.99/mo for individual / non-commercial use; commercial pricing separately negotiated and not published.
- **Python ecosystem:** `alpha_vantage` Python package and direct REST.
- **PRJCT9 fit:** Premium tier pricing is competitive among surveyed providers; commercial-license pricing is unclear and depends on direct sales engagement. Treat as a candidate worth a focused commercial-tier conversation if top-three candidates' commercial pricing exceeds tolerance.
- **Main unknowns:** Commercial-license pricing for redistribution; history depth on US equities; exact rate limits for the validation-style workloads PRJCT9 produces; whether and which exchange licenses Alpha Vantage actually holds (third-party claims unverified at access date).
- **Unverified third-party context:** Third-party reviews characterize Alpha Vantage as exchange-licensed and as a viable IEX Cloud successor (e.g., `https://www.alphavantage.co/iexcloud_shutdown_analysis_and_migration/` is published by Alpha Vantage but is a marketing-style migration page; third-party reviews like `https://findmymoat.com`-class sources, accessed 2026-05-08). Treat as context only.

### 8.6 Twelve Data

- **Source:** `https://twelvedata.com/pricing` (accessed 2026-05-08).
- **Coverage:** US equities + ETFs across all tiers; global EOD on Grow+; multi-asset.
- **History depth:** Not explicitly specified in the pricing surface; verify directly.
- **Adjusted data:** Adjusted + unadjusted via API.
- **API limits:** Basic 8/min (800/day), Grow 377/min, Pro 1597/min, Ultra 10,946/min.
- **Public display / redistribution clarity:** **"Personal, internal, and non-commercial purposes"** — commercial use **NOT permitted under individual plans** per pricing page extract. The page does not specify redistribution / public-display permissions; counsel should investigate whether a commercial / enterprise tier exists.
- **Pricing posture:** Basic free; Grow $66/mo annual; Pro $191/mo annual; Ultra $832/mo annual.
- **Python ecosystem:** `twelvedata` package.
- **PRJCT9 fit:** Pricing is competitive for individual research, but the "non-commercial purposes" individual-plan restriction makes Phase 6 public-display use a custom-contract conversation.
- **Main unknowns:** Whether commercial tiers exist; their pricing; exact redistribution rights.

### 8.7 EOD Historical Data (EODHD)

- **Source:** `https://eodhd.com/financial-apis/api-pricing-plans/` (accessed 2026-05-08).
- **Coverage:** US equities + ETFs (EOD + intraday); fundamentals via separate plan; international coverage on full plans.
- **History depth:** 30+ years for major US companies; "from earliest available" (e.g., Ford from June 1972) on US stocks/ETFs/funds; 1-min intraday from 2004.
- **Adjusted data:** Adjusted + unadjusted.
- **API limits:** Free 20/day; paid personal tiers 100,000/day, 1,000/min.
- **Public display / redistribution clarity:** Personal-use plans restrict to "individual, non-commercial activities." **Commercial use requires "Startups & Enterprise Data Solution Plan."** Specific redistribution rights vary by plan but are not detailed in the public pricing page; counsel should request the commercial contract terms directly.
- **Pricing posture:** $19.99-99.99/mo personal; commercial/enterprise separately priced.
- **Python ecosystem:** Multiple community wrappers; REST API.
- **PRJCT9 fit:** Long history depth and reasonable personal pricing; commercial public-display requires direct sales engagement.
- **Main unknowns:** Commercial / enterprise plan pricing; exact redistribution-rights language; international universe coverage detail.

### 8.8 FRED (Federal Reserve Economic Data)

- **Source:** `https://fred.stlouisfed.org/legal/`, `https://fred.stlouisfed.org/docs/api/terms_of_use.html` (search-result-cited 2026-05-08; direct fetch returned 403 from fetch tooling — Peter should verify directly).
- **Coverage:** Macroeconomic series (interest rates, GDP, employment, CPI, ...). Some equity-index series (^GSPC daily close historical) are aggregated from third parties.
- **History depth:** Series-dependent.
- **API limits:** Documented per the API key page.
- **Public display / redistribution clarity:** Mixed — series labeled "Public Domain: Citation requested" allow display with attribution; "Copyrighted: Citation required" series still allow internal commercial use and display in textbooks/newsletters/client reports with attribution; **commercial publishers and websites must secure written permissions for copyrighted series**; cannot imply Federal Reserve Bank of St. Louis sponsorship.
- **Pricing posture:** Free.
- **PRJCT9 fit:** **NOT a substitute for equity OHLCV.** FRED is macro and benchmark data; useful as a **supplementary** source for index-level reference series (e.g., risk-free rate, ^GSPC daily close), not for the per-ticker OHLCV that signals StackBuilder, ImpactSearch, Confluence, and Spymaster operate on.
- **Main unknowns:** Which specific equity-index series are public-domain vs copyrighted; attribution-rendering requirements for Phase 6.

### 8.9 SEC EDGAR

- **Source:** `https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data`, `https://www.sec.gov/search-filings/edgar-application-programming-interfaces`, `https://data.sec.gov/` (accessed 2026-05-08).
- **Coverage:** Company filings (10-K, 10-Q, 8-K), insider transactions, XBRL fundamentals.
- **History depth:** Filings back to ~1993 onward; company-by-company.
- **Public display / redistribution clarity:** Public domain by virtue of being SEC-required public filings. Programmatic access requires a User-Agent header declaring company name and email per fair-access policy. Rate-limited at "no more than 10 requests per second" with IP blocking on excess.
- **Pricing posture:** Free.
- **PRJCT9 fit:** **NOT a substitute for equity OHLCV.** EDGAR is filings/fundamentals, not market data. Useful as a *supplementary* source for fundamentals overlays in a future Phase 7+ feature; not a Phase 6 OHLCV source.
- **Main unknowns:** None for the OHLCV-substitution question; the answer is no.

### 8.10 Direct exchange feeds (NYSE / Nasdaq)

High-level only: direct exchange feeds (e.g., NYSE TAQ, Nasdaq TotalView, NYSE Integrated, Nasdaq Basic) ARE OHLCV-capable / market-data sources at the source-of-record tier. They are professionally licensed enterprise products with mid-five-figure to seven-figure annual costs and complex per-display-user accounting. Out of scope for current PRJCT9 economics on cost and operational grounds, not on capability grounds; reachable at Phase 7+ scale only via aggregator partnerships or once Phase 6 traffic and revenue justify direct licensing.

### 8.11 Self-host (yfinance only — keep current posture for private use)

The simplest 5G outcome is "Phase 6 launches on a paid provider, but Peter's private research continues on yfinance." This separates the legal exposure (public site) from the engineering surface (private development) and aligns with yfinance's "personal use only" README posture. The hybrid model is analyzed in Section 10.

---

## 9. Provider Ranking for Phase 6 Evaluation

This is a ranked shortlist for Peter to evaluate. **It is not a final selection.** Counsel should price all candidates against the chosen Phase 6 universe scope (Section 6.1) and display mode (Section 6.4), then make the final decision.

### 9.1 Top 3 candidates

**#1 candidate — Polygon.io (Massive).**

- **Why:** Strongest general reputation among PRJCT9-class research projects; published Python client (`polygon-api-client`); broad US stocks / options / forex / crypto product surface. Polygon is a frequently-cited yfinance replacement in commercial contexts.
- **Reason for the ranking:** Strongest *evaluation* candidate — leading reputation and product breadth. **Source confidence is LOW at access date** (Section 8.1) because the official pricing surface (`polygon.io/pricing` → `massive.com/pricing`) returned only page titles to direct fetch tooling. Specific tier features, history depth, redistribution rights, and dollar pricing must be verified directly with Polygon / Massive before commitment.
- **Cautions:** Corporate rebrand to "Massive" creates documentation-discoverability friction. Redistribution-license language is tier-differentiated; counsel must request and review the current commercial-license terms directly. **Do not rely on third-party blog-post pricing or rate-limit claims to drive the decision.**
- **Score: HIGH on prior reputation, PENDING on direct verification.**

**#2 candidate — EODHD.**

- **Why:** Long history depth (30+ years US equities, "earliest available" historical), reasonable personal pricing, separate commercial/enterprise tier with redistribution rights. Strong alternative if Polygon's commercial pricing is prohibitive.
- **Reason for the ranking:** Second-best balance; strongest history depth; clear personal vs commercial separation.
- **Cautions:** Commercial / enterprise pricing not published; requires direct sales engagement.
- **Score: MEDIUM-HIGH on PRJCT9 fit.**

**#3 candidate — Tiingo.**

- **Why:** Long-standing reputation in the research community; product surface across equities EOD, real-time, fundamentals, corporate actions, news; established framework of free Starter / paid individual / commercial-organization tiers per Tiingo's product pages.
- **Reason for the ranking:** Solid candidate to *evaluate*. **Source confidence is LOW at access date** (Section 8.4) because Tiingo's pricing pages returned only page titles to direct fetch tooling. History-depth and per-tier rate-limit specifics need direct verification. Tiingo's terms-of-use page (`https://app.tiingo.com/tos/`) is the authoritative source for redistribution language and must be read directly by counsel.
- **Cautions:** Commercial-tier pricing must be obtained directly from Tiingo sales; redistribution-by-default for equities EOD is prohibited absent a license that expressly allows it.
- **Score: MEDIUM-HIGH on prior reputation, PENDING on direct verification.**

### 9.2 Alpha Vantage — promising secondary

If top-three candidates' commercial pricing exceeds tolerance, Alpha Vantage's published premium-tier pricing ($49.99-$249.99/mo for individual / non-commercial use, per their official premium page) is the cheapest among surveyed providers. Counsel should verify Alpha Vantage's commercial / public-display licensing arrangement and any exchange-license claims directly with Alpha Vantage. Third-party characterizations of Alpha Vantage as "officially licensed by exchanges" were not verified directly against an Alpha Vantage official document in this audit and should not be relied on without direct confirmation.

### 9.3 Eliminated / not-recommended for Phase 6

- **Alpaca:** Excellent for private research; explicit redistribution prohibition (per Alpaca's own support article, Section 8.2) makes it a poor Phase 6 launch source absent a separate commercial license. Could replace yfinance for Peter's *private* use.
- **Twelve Data:** Individual plans are non-commercial; requires custom commercial conversation; less mature ecosystem than top 3.
- **IEX Cloud:** Shut down. Eliminate.
- **FRED, EDGAR:** Not OHLCV market-price substitutes for PRJCT9's equity/ETF price-history needs. FRED is macroeconomic data with a small set of equity-index series sourced from third parties under separate copyright terms; EDGAR is filings/fundamentals. Both can be supplementary sources at Phase 7+ but neither replaces a per-ticker OHLCV provider.
- **Direct exchange feeds (NYSE / Nasdaq):** ARE market-data / OHLCV-capable sources, but are not practical Phase 6 launch substitutes for current PRJCT9 economics because of institutional pricing (mid-five-figure to seven-figure annual costs), redistribution and per-display-user accounting requirements, contract complexity, and operational burden. Reachable later via aggregator partnerships. Not eliminated on capability grounds; deferred on economic and operational grounds.

### 9.4 "Why not yfinance for public launch without legal review"

Three independent Yahoo umbrella ToS clauses each independently prohibit the planned Phase 6 use without express written permission from Yahoo Finance LLC:

- **§2.4(i)** prohibits automated access (yfinance's entire mode of operation).
- **§2.5** prohibits commercial reuse of Yahoo content (Phase 6 public site is plausibly commercial reuse under most counsel reads).
- **§2.8** prohibits reproduction, distribution, broadcast, public performance, derivative works for commercial purposes (Phase 6 public site does at least some of these).

In addition, Yahoo's official Developer API ToU, YDN Guidelines, and API T&C (Section 4.3) each independently constrain commercial use, redistribution, storage duration (24-hour cap on user data), and public display — and the Developer API ToU explicitly states *"In the event of any inconsistency between these Terms of Use and the TOS, these Terms of Use control."* Whether those documents contractually reach yfinance's specific endpoint usage is a counsel question (Section 4.3); their existence at minimum documents Yahoo's posture toward any non-personal-research data use.

The yfinance README's own language ("personal use only," "research and educational purposes," "not affiliated, endorsed, or vetted by Yahoo") tells us yfinance's own maintainers do not represent the project as authorizing public-launch use. **Counsel review is the only safe path before Phase 6 launches on yfinance, and counsel may well decide that path is not safe at all.**

---

## 10. Hybrid Model Viability

### 10.1 The hybrid: yfinance for private research, paid provider for public Phase 6

**Strengths:**

- Preserves Peter's existing private-research workflow exactly as is.
- Limits Phase 6 launch risk to one provider (the paid one), under terms reviewed by counsel.
- Matches yfinance's stated personal-use intent.
- Cheapest path to Phase 6 launch (no need to migrate Peter's local validation runs).
- Aligns with the North Star "yfinance is the sprint data source; ... pre-launch, a separate data licensing review is needed" posture.

**Weaknesses:**

- Validation envelope incomparability: Peter's private yfinance-source validation results are not directly comparable to the public Phase 6 paid-provider-source validation results (Section 7.4). This is a research-honesty concern that Phase 5C honest-validation discipline forces into the open: every output's manifest must record which provider produced it, and validation envelopes from different providers cannot be aggregated.
- Ledger continuity: Phase 5C `honest_validation_ledger.py` aggregates `validation_contract_v1` sidecars across apps. If Peter's private runs use yfinance and public Phase 6 runs use Polygon, the ledger has two separate provider-tagged tracks — readable but more complex.
- Provider-specific adjusted-close differences propagate into every signal, every metric, every backtest historical (Section 5.3).
- Cost containment: Phase 6 traffic must be served from the paid provider, not yfinance. EOD + caching keeps this manageable.
- Provider routing complexity: every loader needs to know which provider to call for a given context. Section 5.6's note that `provider_metadata` does not yet exist in code is the engineering blocker.
- Legal risk if PRJCT9 ever needs to display *Peter's private yfinance-derived* analytics on the public site (e.g., Peter's preferred backtest result happens to be yfinance-source and a Phase 6 page surfaces it). This must be either categorically prevented or counsel-cleared.

### 10.2 Decision points for Peter

| Question | Option A | Option B | Option C |
|---|---|---|---|
| Public Phase 6 data source | Stay on yfinance, with counsel review and likely Mode-B (derived metrics only) display | Move all launch-facing data to a paid provider with explicit redistribution rights | Hybrid: yfinance private only, paid provider public only |
| Private-research data source | yfinance (status quo) | Replace with paid provider (Alpaca free tier or Polygon developer) | yfinance (status quo) |
| Validation envelope provider posture | Single (yfinance) | Single (paid provider) | Two-track (provider-tagged) |
| Engineering effort | Lowest (Phase 6 wires yfinance through derived-metrics layer) | Highest (provider abstraction across the full codebase) | Medium-High (two-provider routing; abstraction needed; cache shapes split) |
| Legal exposure | Unresolved without counsel review | Lowest (paid provider's terms govern) | Lower than Option A; matches yfinance's intent |
| Comparability of public + private validation envelopes | Best (single source) | Best (single source) | Worst (two sources) |
| Cost | Lowest | Highest | Middle |

**The hybrid model (Option C) is recommended for evaluation, not as a final answer.** It captures most of the legal benefit of Option B at a fraction of the engineering cost, at the price of validation-comparability complexity that Phase 5C's honest-validation discipline is already structured to handle (per-source manifest tagging).

### 10.3 The decision is Peter's

This document does not make this decision. It scopes it. Peter and counsel should weigh:

- Peter's risk tolerance for §2.4(i) / §2.5 / §2.8 exposure.
- Peter's budget tolerance for paid-provider commercial / business / enterprise tiers.
- Peter's preference for engine-comparability (single provider) vs cost-control (hybrid).
- Counsel's read of whether Mode-B-only display materially reduces yfinance exposure.
- Counsel's read of whether seeking Yahoo Finance LLC express written permission is worth pursuing.

---

## 11. Legal Counsel Touchpoints

The following decisions require qualified legal counsel review before Phase 6 launch. This is not an exhaustive list; counsel should add as needed.

1. **Yahoo Terms of Service interpretation** for Sections §2.4(i), §2.5, §2.8, §2.13, and §14.2(a)(i) as applied to PRJCT9's planned Phase 6 surface and Peter's private research workflow.
2. **Public display rights** under Yahoo ToS for raw OHLCV (Mode A) vs derived-metrics-only (Mode B) display modes (Section 6.4).
3. **Caching / redistribution rights** under Yahoo ToS for server-side caches (Spymaster `cache/results/`, signal_library stable PKLs, Phase 3 manifest-stamped artifacts).
4. **Commercial use determination** — whether PRJCT9.com's open-source-research-commons posture, with whatever revenue model is eventually planned (donations / ads / premium / nothing), constitutes "commercial purpose" under §2.5.
5. **Provider contract negotiation** for the chosen Phase 6 paid provider's redistribution license, attribution requirements, rate-limit terms, and termination clauses.
6. **Volunteer / BYO-data terms** — contributor license agreement (CLA), license-warranty allocation, takedown posture, contributor-identity-verification requirements (Section 7.2). This is the largest legal-scope item in the entire project lifetime.
7. **User agreement / disclaimers for Phase 6** — research-honesty guardrail language, no-investment-advice disclaimers, no-trading-signals language, accuracy / staleness / coverage caveats, accessibility-claim limitations.
8. **International data use / export compliance** if the chosen Phase 6 universe scope includes non-US securities (Tier B+ or Tier C). Different providers have different international-redistribution permissions.
9. **Yahoo Finance LLC express written permission inquiry** — whether Peter wants to attempt to obtain such permission, what conditions Yahoo Finance LLC may impose, what the realistic timeline and cost are.
10. **Trademark / branding interactions** — "Yahoo," "Yahoo Finance," provider trademarks; what attribution language can be used without crossing trademark-misuse lines.

---

## 12. Recommended 5G Sequencing

Phase 5G is recommended to unfold as a series of sub-phases, each with a distinct deliverable.

### 12.1 Phase 5G-1 (this PR)

This research/doc PR. Delivers the locked-authority alignment, ToS posture, codebase coupling audit, provider survey, decision framework, sequencing recommendation. **Status: delivered by this document.**

### 12.2 Phase 5G-2

**Decision record.** Captures Peter's decisions on:

- Whether Phase 6 launches on yfinance, a paid provider, or hybrid.
- Which provider is chosen if not yfinance.
- Which Phase 6 universe tier (A / B / C from Section 6.1) the launch targets.
- Which display mode (A raw OHLCV / B derived metrics).
- Caching posture under chosen provider's terms.
- Attribution structure for Phase 6.
- Counsel's review outcome captured as a decision memo.

**Format:** A new `md_library/shared/<date>_PHASE_5G_2_DECISION_RECORD.md`. Doc-only PR. No code.

### 12.3 Phase 5G-3 (conditional)

**Provider-abstraction engineering preflight.** Only executes if 5G-2 selects a multi-provider or non-yfinance provider posture. Otherwise skipped.

If executed:

- Codex preflight + Claude Code implementation of `data_loader.py` / `OhlcvLoader` Protocol.
- Per-app loader migration sequencing (`signal_library/multi_timeframe_builder.py` first as the cleanest seam; ImpactSearch FastPath wrapper next; then `onepass.py`, `stackbuilder.py`, `trafficflow.py`; Spymaster last with explicit standalone-design contract amendment if needed).
- Provenance manifest schema bump for `provider_metadata`.
- GTL validator extension for the new provider's symbology.
- Test infrastructure provider-tagged fixtures.
- Cache migration / cutover plan.

This is a substantial engineering effort (Section 5.7 "HIGH effort" estimate). Should be its own phase track with multiple PRs.

### 12.4 Phase 5G-4 / Phase 7+

**BYO-data provenance + contributor license model.** Defers per North Star. Scope items per Section 7.2-7.3.

### 12.5 Sequencing with 5D-2

- **5G and 5D-2 can proceed in parallel.** 5G is doc/legal; 5D-2 is distributed-cluster engineering.
- **5G gates Phase 6 public launch more directly.** 5D-2 is internally facing (operator/cluster); does not depend on 5G outcomes.
- **5D-2 should avoid cloud / provider assumptions that conflict with unresolved 5G decisions.** If 5D-2 introduces a "cloud worker that fetches from yfinance" pattern, that pattern would be deprecated if 5G-2 selects a paid provider. 5D-2 should prefer pluggable data-loader interfaces from the outset, even if the only loader implemented is yfinance — this avoids 5D-2-induced rework when 5G-3 (if reached) lands.
- **5D-2 should NOT assume volunteer-compute readiness.** Volunteer compute is Phase 7+ per the locked authorities.

---

## 13. Risks

The following risks affect 5G's framing of the public-launch question. Each is owned by Peter and counsel; this document does not propose mitigations.

1. **Yahoo / yfinance ToS enforcement risk.** Yahoo can change ToS, technically block yfinance, or pursue enforcement at any time without notice. Phase 6 on yfinance is reversible by Yahoo unilaterally.
2. **Provider migration risk.** Even after 5G-2 selects a provider, that provider may change pricing / terms / availability (cf. IEX Cloud shutdown, Section 8.3). Phase 6 should not lock to a single provider so tightly that migration is impossible.
3. **Validation comparability risk.** Provider-specific adjusted-price math means moving providers retroactively changes every backtest historical (Section 5.3, Section 7.4). Phase 5C honest validation must record provider identity in every sidecar and ledger entry.
4. **Cost overrun.** Paid-provider tiers can climb quickly when traffic scales. Phase 6 launch should plan for traffic-scaling triggers that re-evaluate provider tier rather than passively over-running budget.
5. **Vendor lock-in.** Provider-abstraction (5G-3, if reached) reduces lock-in but does not eliminate it. Symbology, history depth, adjustment math, and rate-limit profile all create soft lock-in.
6. **BYO-data provenance risk.** Volunteers may upload data they do not have rights to redistribute. PRJCT9 cannot verify every claim. Counsel-cleared safe-harbor posture is required before any volunteer data is accepted (Section 7.2).
7. **Legal scope creep.** As Phase 6 user-base grows, legal questions multiply (international, accessibility, data-protection, contributor-IP, advertising). Counsel relationship must scale with the project.
8. **Rate-limit and operational reliability risk.** yfinance rate enforcement is real and aggressive (per North Star + per `global_ticker_library/tools/diagnose_rate_limits.py`). Paid providers also rate-limit; high-traffic Phase 6 launch could exceed even paid-tier limits.
9. **Symbology drift risk.** Provider switch will silently shift which symbols are present in the universe (Section 5.3). Phase 4A coverage transparency exposes this as data, but it can still surprise users.
10. **Public-display claim risk.** PRJCT9's North Star research-honesty guardrail commits to distinguishing "observed historical behavior" from "prediction." Even with that discipline, public display of validation envelopes can be misread as prediction. User-agreement disclaimer language must be reviewed by counsel.

---

## 14. Peter Decision Checklist

A concise checklist for Peter to review with counsel:

- [ ] Am I comfortable continuing yfinance for private sprint use? (Default: yes; matches yfinance README's stated intent.)
- [ ] Do I want Phase 6 to display raw charts, derived metrics only, or both?
- [ ] What is my monthly data-budget tolerance for the chosen Phase 6 provider?
- [ ] Which Phase 6 universe tier is required at launch (A curated 50-200 / B US equities + ETFs / C full 73K)?
- [ ] Is commercial monetization expected for PRJCT9.com (donations, ads, premium tiers, partnerships, etc.)?
- [ ] Should legal counsel review before any public launch? (Strongly recommended: yes.)
- [ ] Is BYO-data a Phase 7+ requirement or merely a long-term option?
- [ ] If hybrid (Section 10), am I OK with two provider-tagged tracks in the validation ledger?
- [ ] If single-provider, am I OK with the engineering effort to migrate the codebase off yfinance?
- [ ] Have I identified counsel with relevant expertise (data-licensing / TMT / fintech) for the items in Section 11?

---

## 15. Conclusion / Status

**Status: RECOMMEND-PHASED-IMPLEMENTATION**

- 5G-1 delivered by this document.
- 5G-2 requires Peter / legal counsel / provider decisions captured as a durable decision record.
- 5G-3 (provider-abstraction engineering) is conditional on 5G-2 selecting a non-yfinance or multi-provider posture.
- 5G-4 / Phase 7+ tackles BYO-data provenance and contributor-license design.
- No code changes recommended until 5G-2 decisions are made.

The data-licensing question for Phase 6 is real, material, and not solvable by engineering alone. This document scopes it for Peter and counsel.

---

## 16. Sources Appendix

### 16.1 Web sources

**Source-quality conventions used in this appendix.** Rows marked **(official)** were fetched and parsed directly from the publisher's domain at the access date or are explicitly stated by the publisher. Rows marked **(official, fetch-pending)** are official URLs from the publisher's domain, but at the access date the URL returned only a page title to direct fetch tooling (typically because the page is heavily JS-rendered) and direct verification by Peter / counsel is required. Rows marked **(third-party context)** are non-publisher commentary used only as context, not as authoritative citation; they are not load-bearing on any decision in this document.

| Source title | Publisher | URL | Status | Accessed | Used for |
|---|---|---|---|---|---|
| Yahoo Terms of Service (umbrella) | Yahoo Inc. (legal.yahoo.com) | `https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html` | official | 2026-05-08 | §2.4(i), §2.5, §2.8, §2.13, §14.2(a)(i) ToS analysis (Section 4.1) |
| Yahoo Finance Community Additional Terms | Yahoo Finance LLC (legal.yahoo.com) | `https://legal.yahoo.com/us/en/yahoo/terms/product-atos/finance/index.html` | official | 2026-05-08 | Yahoo Finance Community Additional Terms governs Community user/profile/posting/portfolio surface; does NOT authorize automated access, scraping, redistribution, or public display of Yahoo Finance market data; does NOT reference yfinance or third-party developer tooling (Section 4.1) |
| Yahoo Developer API Terms of Use | Yahoo Inc. (legal.yahoo.com) | `https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html` | official | 2026-05-08 | Section 4.3.1: incorporation of umbrella ToS + "API Terms control if inconsistent"; rate limits at Yahoo's discretion; API key + attribution policy; sell/lease/share/transfer/sublicense/income prohibition; abusive-volume restriction; competing-product / children-services restrictions |
| Yahoo Developer Network Guidelines | Yahoo Inc. (legal.yahoo.com) | `https://legal.yahoo.com/us/en/yahoo/guidelines/ydn/` | official (WebFetch normalized to `https://policies.yahoo.com/us/en/yahoo/guidelines/index.html`) | 2026-05-08 | Section 4.3.2: commercial-vs-noncommercial differs by API; non-commercial-only API context restrictions ("high traffic, established commercial-oriented" sites; monetized apps; productivity tools); 24-hour storage limit on Yahoo user data with narrow exceptions (GUID + Authenticated Token Value); auth-method requirements |
| Yahoo Application Programming Interface Terms and Conditions | Yahoo Inc. (legal.yahoo.com) | `https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apitnc/index.html` | official | 2026-05-08 | Section 4.3.3: API Data transfer/display restrictions in commercial-account contexts (display only within Yahoo API Client + only to Account Owner); usage-quota at Yahoo's discretion; redistribution prohibition without authorized agents; attribution / endorsement language constraints |
| yfinance project README | ranaroussi / open-source community | `https://github.com/ranaroussi/yfinance` | official | 2026-05-08 | yfinance disclaimer language (Section 4.2) |
| Polygon.io stocks landing | Polygon.io / Massive | `https://polygon.io/` | official, fetch-pending | 2026-05-08 | Polygon general product surface; specific tier features pending direct verification (Section 8.1) |
| Polygon.io / Massive pricing | Polygon.io / Massive | `https://polygon.io/pricing` (redirects to `https://massive.com/pricing`) | official, fetch-pending | 2026-05-08 | Polygon pricing surface; returned only page title to direct fetch tooling — direct verification required (Section 8.1) |
| Alpaca Market Data | Alpaca Markets | `https://alpaca.markets/data` | official | 2026-05-08 | Alpaca tiers / pricing / coverage / rate-limit detail (Section 8.2) |
| Alpaca redistribution support article | Alpaca Markets | `https://alpaca.markets/support/redistribute-alpaca-api` | official | 2026-05-08 | Alpaca's official position that API data cannot be redistributed for business purpose under standard market-data agreement (Section 8.2 redistribution citation) |
| IEX Cloud retirement page | IEX Group | `https://iexcloud.org/` | official | 2026-05-08 | IEX Cloud shutdown confirmation (Section 8.3) |
| IEX Cloud shutdown migration guide | Alpha Vantage | `https://www.alphavantage.co/iexcloud_shutdown_analysis_and_migration/` | provider-published (Alpha Vantage) | 2026-05-08 | IEX Cloud shutdown context (Section 8.3) — published by Alpha Vantage as marketing-style migration page; treat factual claims about Alpha Vantage as marketing, not independent verification |
| Tiingo pricing | Tiingo Inc. | `https://www.tiingo.com/pricing` | official, fetch-pending | 2026-05-08 | Tiingo pricing surface; returned only page title to direct fetch tooling (Section 8.4) |
| Tiingo about/pricing | Tiingo Inc. | `https://www.tiingo.com/about/pricing` | official, fetch-pending | 2026-05-08 | Tiingo pricing surface; returned only page title to direct fetch tooling (Section 8.4) |
| Tiingo End of Day stock price product | Tiingo Inc. | `https://www.tiingo.com/products/end-of-day-stock-price-data` | official, fetch-pending | 2026-05-08 | Tiingo EOD product page; returned only page title to direct fetch tooling (Section 8.4) |
| Tiingo Terms of Use | Tiingo Inc. | `https://app.tiingo.com/tos/` | official, fetch-pending | 2026-05-08 | Tiingo authoritative redistribution-language source — direct counsel review required (Section 8.4) |
| Alpha Vantage Premium API | Alpha Vantage | `https://www.alphavantage.co/premium/` | official | 2026-05-08 | Alpha Vantage tier pricing + rate-limit detail (Section 8.5) |
| Alpha Vantage Terms of Service | Alpha Vantage | `https://www.alphavantage.co/terms_of_service/` | official, fetch-pending | 2026-05-08 | Alpha Vantage ToS — counsel must review directly for commercial / public-display language (Section 8.5) |
| Alpha Vantage Realtime Data Policy | Alpha Vantage | `https://www.alphavantage.co/realtime_data_policy/` | official, fetch-pending | 2026-05-08 | Alpha Vantage realtime-data policy — counsel must review directly (Section 8.5) |
| Twelve Data pricing | Twelve Data | `https://twelvedata.com/pricing` | official | 2026-05-08 | Twelve Data tiers + "personal, internal, and non-commercial purposes" individual-plan restriction (Section 8.6) |
| EODHD pricing plans | EOD Historical Data | `https://eodhd.com/financial-apis/api-pricing-plans/` | official | 2026-05-08 | EODHD tier pricing / coverage / history (Section 8.7) |
| FRED Legal Notices | Federal Reserve Bank of St. Louis | `https://fred.stlouisfed.org/legal/` | official, fetch-pending (direct fetch returned 403) | 2026-05-08 | FRED redistribution / attribution — counsel must verify directly (Section 8.8) |
| FRED API Terms of Use | Federal Reserve Bank of St. Louis | `https://fred.stlouisfed.org/docs/api/terms_of_use.html` | official, fetch-pending (direct fetch returned 403) | 2026-05-08 | FRED API ToS posture — counsel must verify directly (Section 8.8) |
| SEC EDGAR Accessing Data | SEC | `https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data` | official, fetch-pending (direct fetch returned 403) | 2026-05-08 | EDGAR fair-access policy (Section 8.9) |
| SEC EDGAR APIs | SEC | `https://www.sec.gov/search-filings/edgar-application-programming-interfaces` | official, fetch-pending | 2026-05-08 | EDGAR API context (Section 8.9) |
| SEC data.sec.gov | SEC | `https://data.sec.gov/` | official, fetch-pending | 2026-05-08 | EDGAR data-access context (Section 8.9) |
| Polygon third-party review | Yolo Trading / Medium | `https://medium.com/@yolotrading/a-complete-review-of-the-polygon-io-api-everything-you-wanted-to-know-c79e992a74ff` | third-party context | 2026-05-08 | Context only — third-party characterizations; not load-bearing on any Section 8.1 / 9.1 decision (do not rely without direct Polygon verification) |
| TradeStation vs Polygon vs Alpaca comparison | crackingmarkets.com | `https://www.crackingmarkets.com/comparing-affordable-intraday-data-sources-tradestation-vs-polygon-vs-alpaca/` | third-party context | 2026-05-08 | Context only — third-party comparison; not load-bearing |
| 5 Best APIs for Stock Market Data 2026 | api.market | `https://api.market/blog/MagicAPI/stock-market-api/best-api-for-stock-market-data-all-over-the-world-2026` | third-party context | 2026-05-08 | Context only — third-party comparison; not load-bearing |
| Tiingo review | findmymoat.com | `https://www.findmymoat.com/tools/tiingo` | third-party context | 2026-05-08 | Context only — third-party characterization; not load-bearing |
| Tiingo data-coverage evaluation | QuantStart | `https://www.quantstart.com/articles/evaluating-data-coverage-with-tiingo/` | third-party context | 2026-05-08 | Context only — third-party characterization; not load-bearing |
| IEX Cloud closure analysis | dev.to | `https://dev.to/eva_87b1a75318574919fe929/dissecting-the-iex-cloud-closure-retrospection-outlook-1751` | third-party context | 2026-05-08 | Context only — third-party background on IEX Cloud shutdown |
| IEX Cloud Migration Guide | InsightBig | `https://www.insightbig.com/post/iex-cloud-shutting-down-a-complete-python-migration-guide` | third-party context | 2026-05-08 | Context only — third-party migration guide |

### 16.2 Repo sources

| File path | Reference | Used for |
|---|---|---|
| `project/md_library/shared/2026-05-04_PRJCT9_NORTH_STAR.md` | "Data source posture" section, "Crowdsourcing direction," "Bring-your-own-data direction," "How phases reference this document" | Section 3.1 locked-authority alignment |
| `project/md_library/shared/2026-05-04_PHASE_4_SCOPING.md` | "Decisions captured" section (yfinance is sprint data source; schema isolation principle), Design principle 8 | Section 3.2 locked-authority alignment |
| `project/md_library/shared/2026-05-05_PHASE_5_PRE_FLIGHT.md` | "Scope locked" item 5 (Phase 5G), Design principle 7 (Licensing gates Phase 6), Implementation phasing | Section 3.3 locked-authority alignment |
| `project/CLAUDE.md` | "Sprint state (as of 2026-05-08)" Phase 5G + Phase 6 status; "Project Overview" / "Symbol Validity"; "Spymaster.py Standalone Design" | Section 3.4 locked-authority alignment; Section 5.1 standalone-design context |
| `project/spymaster.py` | line 1 (`import yfinance as yf`); lines 3537, 3577, 3626, 3832, 3836, 3903, 4088 (yfinance call sites) | Section 5.2 inventory |
| `project/impactsearch.py` | line 64; lines 621-628 (yf.download monkeypatch); lines 1805, 2084 | Section 5.2 inventory |
| `project/onepass.py` | line 19; line 1625 | Section 5.2 inventory |
| `project/stackbuilder.py` | line 193; line 510 (`yf.download(sym, period="max", interval="1d", auto_adjust=False, ...)`) | Section 5.2 inventory |
| `project/trafficflow.py` | line 55; lines 924, 1039, 1047, 1056, 1246 | Section 5.2 inventory |
| `project/signal_library/multi_timeframe_builder.py` | line 24; line 87 (retry-wrapper around `yf.download`) | Section 5.2 inventory |
| `project/stale_check.py` | lines 27, 104-105 | Section 5.2 inventory |
| `project/global_ticker_library/validator_yahoo.py` | line 22; lines 481, 538; module docstring lines 5-6 | Section 5.2 inventory |
| `project/global_ticker_library/tools/diagnose_rate_limits.py` | line 9 | Section 5.2 inventory |
| `project/cross_ticker_confluence.py` | line 168 (`series_id`); line 169 (`series_kind`); line 235 (`_VALID_SYMBOL_RE`); line 379 (`series_kind="yfinance_ticker"`); lines 882-883 (series_metadata in coverage); lines 1262-1263, 1335-1336 (series_metadata in flows) | Section 5.6 schema-isolation reality check |
| `project/test_scripts/test_cross_ticker_confluence_dash.py` | lines 77, 146 (`series_kind="yfinance_ticker"` in test fixtures) | Section 5.6 single-literal confirmation |
| `project/provenance_manifest.py` | full file (no `provider_metadata` / `provider_name` field; manifest schema does not currently distinguish providers) | Section 5.6 manifest-schema gap |
| `project/md_library/shared/2026-05-01_PHASE_1B_INTENTIONAL_DELTA_LEDGER.md` | Entry 1 (Adj Close removal from production scoring) | Section 5.3 Adj Close column-usage context |
| `project/md_library/trafficflow/2025-10-06_TRAFFICFLOW_SPYMASTER_PARITY_REPAIR_GUIDE.md` | line 169 (`yf.download('SBIT', start='2024-01-01')['Adj Close']` direct usage in repair-guide example) | Section 5.3 Adj Close column-usage example |

---

*End of Phase 5G-1 research document.*
