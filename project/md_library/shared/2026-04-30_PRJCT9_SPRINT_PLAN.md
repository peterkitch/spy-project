# PRJCT9 Sprint Plan

Document date: 2026-04-30
Branch: prjct9-sprint-plan
Status: Source-of-truth coordination document (non-implementation).

This document defines the sprint goal, scope boundaries, locked
decisions, phase structure, per-phase detail, codex audit findings,
working tree policy, and usage protocol. Implementation prompts live
separately and are drafted per-phase by web Claude, then executed in
Claude Code.

---

## Section 1. Sprint Goal

Transform PRJCT9 from a fragmented collection of independent scripts
into a unified, trustworthy research platform that produces a daily
multi-timeframe confluence output across all tickers, with intellectual
honesty about its findings. The end product goes live on PRJCT9.com.

Deliverables:

  1. One deterministic algorithm spec. Defines price basis, T-1
     policy, SMA window range, pair ranking, tie-breaks, signal
     timing, confluence tiers, metric formulas. The code follows the
     spec; the spec is the law.
  2. One canonical scoring function. Used identically across
     SpyMaster, OnePass, ImpactSearch, TrafficFlow, StackBuilder,
     Confluence. No more Phase 2 vs Phase 3 disagreements. No hidden
     globals or env reads silently changing behavior.
  3. One golden test suite. Parity tests across all engines.
     Lookahead-leak audits on every signal-to-return alignment.
     Synthetic dataset tests with known-correct answers.
  4. One reproducible signal-library build. Manifest-tracked output.
     Every result file records source data, settings, calendar grace
     days, git commit, package versions.
  5. One cross-ticker confluence dashboard. Confluence.py upgraded
     from single-ticker display to a daily scrub of all tickers
     across 1-year, 3-month, 1-month, 1-week, and 1-day timeframes.
     Output: a ranked list of tickers showing confluence across
     timeframes.
  6. One honest validation report. Walk-forward, holdout periods,
     baseline comparisons, bootstrap CIs, explicit acknowledgment of
     overfitting risk and search-space size.
  7. PRJCT9.com goes live. Static explainer. Public, defensible,
     intellectually honest.

---

## Section 2. Explicitly Out of Scope

  - Live automated trading execution (separate future project).
  - matrix.py revival (original goal was automated execution; abandon).
  - New signal discovery or algorithm expansion.
  - Multi-secondary refactor unless directly blocking the confluence
    output.
  - UI polish beyond what serves the daily confluence deliverable.

---

## Section 3. Locked Decisions

  - Raw Close as the only price basis. Adj Close eliminated in Phase 1
    across all engines. yfinance Adj Close exhibits micro-drift over
    time as dividends and splits get retroactively reapplied, making
    it unreliable for reproducible research. Raw Close is stable.
  - SMA pair semantics: Buy (A,B) means SMA_A > SMA_B; Short (A,B)
    means SMA_A < SMA_B. Direction comes from the comparison operator
    and the buy/short label, never from "fast" or "slow" wording.
    This convention is the canonical interpretation across all
    engines. CLAUDE.md lines 485-496 are the authoritative reference.
  - Phase 0 completion bar: algorithm spec + env unblocked + smoke
    tests passing. Full golden suite belongs in Phase 2.
  - ELI5 layer location: per-phase ELI5 sections in this sprint plan
    document plus PR descriptions. Commit messages stay short and
    plain.
  - Email decision: a Gmail address in
    global_ticker_library/gl_config.py:70 is intentional, traces to
    a ticker-library setup step that required an email, and remains
    public. No history rewrite for it.

---

## Section 4. Phase Structure

  Phase -1: Public repo security cleanup.
  Phase 0: Foundation and deterministic algorithm spec.
  Phase 1: Canonical scoring function.
  Phase 2: Golden test suite.
  Phase 3: Reproducible build and provenance.
  Phase 4: Cross-ticker confluence dashboard.
  Phase 5: Honest validation report.
  Phase 6: PRJCT9.com goes live.

---

## Section 5. Per-Phase Detail

### Phase -1: Public repo security cleanup

  - Purpose: Remove machine-specific Windows paths and any other
    environment-coupled identifiers that should not ship in a public
    research repo.
  - In Scope:
    - Full grep for hardcoded paths across the entire committed
      surface: .py, .md, .bat, .json, .yml, .yaml, .lock, .txt,
      .gitignore, .gitattributes, .spec, .ini, .cfg, .toml. Anything
      tracked is in scope; nothing is exempted by file type.
    - Replace machine-specific defaults with repo-relative defaults
      plus environment-variable overrides, naming overrides
      `PRJCT9_<PURPOSE>_DIR` so a single convention covers every
      engine.
    - Documentation path cleanup: rewrite any markdown that
      describes the local Windows setup with generic placeholders,
      or remove the path where it serves no instructional purpose.
    - Committed artifact audit: inventory every tracked .pkl,
      .json, .csv, .xlsx, .parquet, .db, .sqlite. Categorize each
      as safe / sanitize / untrack-and-gitignore.
    - Git history scan: report (do not execute rewrite) any
      historical leakage of credentials, broker references, account
      identifiers, or PII beyond the intentional Gmail noted in
      Section 3 Locked Decisions.
    - Phase -1 deliverable doc:
      `md_library/shared/<DATE>_SECURITY_CLEANUP_PHASE_MINUS_1.md`
      documenting findings, decisions, changes applied, and any new
      env vars introduced.
  - Out of Scope:
    - History rewrite for previously committed paths.
    - Removal or rewrite of the gl_config.py email noted in
      Section 3 Locked Decisions (remains public).
    - Any algorithmic change.
  - Acceptance Criteria:
    - A complete stranger cloning the repo learns nothing personal
      beyond Peter's GitHub username.
    - All defaults work from a clean clone in a repo-relative
      layout; engines run unchanged with no path edits required.
    - `SECURITY_CLEANUP_PHASE_MINUS_1.md` committed at the
      documented location.
    - Git history sensitivity report delivered to Peter for a
      separate rewrite decision (out of this phase's scope to act
      on).
    - Tracked artifact audit complete; any newly-untracked patterns
      captured in `.gitignore` (or in `.git/info/exclude` where the
      pattern would otherwise advertise local file structure
      publicly).
    - A short Phase -1 PR description enumerates each replacement
      and links to the security cleanup doc.
  - ELI5: The repository will be public. Anything tied to one
    machine, like a path that names a user folder, leaks a detail
    that does not belong on the internet and breaks the repo for
    anyone else who tries it. This phase finds those snags and
    replaces them with portable substitutes so the project travels
    cleanly across machines and stays safe to open up.
  - Blocking Dependencies: None. This phase runs first because the
    repo is going public and security-clean state is a prerequisite
    for the work that follows.

### Phase 0: Foundation and deterministic algorithm spec

  - Purpose: Author the single deterministic algorithm spec that all
    engines must obey, and unblock the local environment so smoke
    tests pass.
  - In Scope:
    - Algorithm spec document covering price basis, T-1 policy, SMA
      window range, pair ranking, tie-breaks, signal timing,
      confluence tiers, and metric formulas.
    - Environment unblocking sufficient for engines to import and
      run a smoke pass.
    - Smoke-test pass on each engine entry point.
  - Out of Scope:
    - Golden test suite construction (Phase 2).
    - Canonical scoring rewire (Phase 1).
    - Provenance manifests (Phase 3).
  - Acceptance Criteria:
    - Spec document committed at a stable path with versioning
      header.
    - Each engine smoke test runs to completion on the current data
      snapshot.
    - Spec sections cross-referenced from CLAUDE.md.
  - ELI5: Today the engines disagree on small details, like which
    price column to use or how to break ties. Without one written
    rulebook, every engine improvises and the answers drift. Phase 0
    writes that rulebook. Once the rulebook exists, the rest of the
    sprint can point at it whenever a question comes up.
  - Blocking Dependencies: Phase -1 must finish first so the spec is
    authored on a clean public-ready repo.

### Phase 1: Canonical scoring function

  - Purpose: Replace per-engine scoring drift with one shared scoring
    implementation that every engine uses, eliminate Adj Close, and
    fix the verified StackBuilder and ImpactSearch defects that share
    this surgical surface.
  - In Scope:
    - Single canonical scoring module consumed by SpyMaster, OnePass,
      ImpactSearch, TrafficFlow, StackBuilder, Confluence.
    - Adj Close eliminated at every call site listed in the audit
      findings.
    - StackBuilder Phase 2 vs Phase 3 scoring mismatch resolved.
    - StackBuilder Dash multi-secondary closure bug at
      stackbuilder.py:1295 fixed.
    - StackBuilder --outdir argument honored.
    - ImpactSearch xlsx duplicate-row accumulation fixed.
  - Out of Scope:
    - Test harness construction (Phase 2).
    - New algorithm features.
    - Schema expansion of result files (Phase 3).
  - Acceptance Criteria:
    - All listed engines route scoring through one function.
    - Re-running StackBuilder produces consistent K1 and rank
      outputs across phases.
    - ImpactSearch xlsx outputs do not accumulate duplicate rows on
      reruns.
    - StackBuilder Dash multi-secondary view closes cleanly.
    - StackBuilder --outdir writes to the requested directory.
  - ELI5: Right now several engines compute the same idea in slightly
    different ways, and small differences add up to contradictory
    answers. Phase 1 picks the one correct way to score and makes
    every engine call that single function. While we are in those
    files, three known bugs sit on the same surgical surface and get
    fixed in the same pass.
  - Blocking Dependencies: Phase 0 spec must exist so the canonical
    function has a written reference to implement against.

### Phase 2: Golden test suite

  - Purpose: Build the durable test harness that proves engines
    agree, signals do not leak the future, and synthetic inputs
    yield known-correct answers.
  - In Scope:
    - Parity tests across engines.
    - Lookahead-leak audits on every signal-to-return alignment.
    - Synthetic dataset tests with hand-verified expected outputs.
    - Creation of a new tracked test suite under `test_scripts/`.
      Any old stash scripts from prior work are reference-only and
      may inform patterns or fixtures, but do not form the basis of
      the new suite. Build fresh against the canonical scoring
      function extracted in Phase 1.
  - Out of Scope:
    - Algorithm changes.
    - Performance tuning unrelated to test stability.
  - Acceptance Criteria:
    - test_scripts/ tracked in git with a stable layout.
    - Parity suite passes across all engines on the current spec.
    - Lookahead-leak audits pass on every signal-to-return pair.
    - At least one synthetic dataset with full expected-output
      coverage passes end-to-end.
  - ELI5: A test suite is the project's lie detector. If two engines
    disagree, a parity test catches it. If a signal accidentally
    peeks at tomorrow's price, a leak audit catches it. Synthetic
    datasets let us know the right answer ahead of time, so any
    drift surfaces immediately. Phase 2 builds that lie detector.
  - Blocking Dependencies: Phase 1 must finish so the canonical
    scoring function is the single thing under test.

### Phase 3: Reproducible build and provenance

  - Purpose: Make every result file self-describing so any output can
    be traced back to its source data, settings, calendar grace
    days, git commit, and package versions.
  - In Scope:
    - Manifest schema applied uniformly across SpyMaster, OnePass,
      ImpactSearch, TrafficFlow, StackBuilder, Confluence.
    - Backfill of manifests for engines whose current outputs are
      partial or absent (StackBuilder, ImpactSearch xlsx, Confluence
      durable outputs).
    - Reproducible signal-library build path with manifest-tracked
      output.
  - Out of Scope:
    - Cross-ticker dashboard work (Phase 4).
    - Validation report (Phase 5).
  - Acceptance Criteria:
    - Every engine output writes a manifest containing source data,
      settings, calendar grace days, git commit, and package
      versions.
    - A signal-library build is reproducible from manifest alone on
      a clean clone.
  - ELI5: A result without a label is a rumor. A manifest stamps
    each output with where it came from, what knobs were set, what
    code commit produced it, and what library versions were
    installed. With manifests, any future question about an old
    output can be answered without guessing.
  - Blocking Dependencies: Phase 2 should be in place so manifest
    additions do not silently break engine outputs without tests
    catching it.

### Phase 4: Cross-ticker confluence dashboard

  - Purpose: Upgrade Confluence from single-ticker display to a daily
    multi-timeframe scrub across all tickers, producing a ranked
    list as the headline deliverable.
  - In Scope:
    - Daily all-ticker batch orchestration.
    - Multi-timeframe scoring across 1-year, 3-month, 1-month,
      1-week, and 1-day windows.
    - Durable, manifest-stamped daily outputs.
    - Ranked confluence presentation suitable for the public site.
    - Reuse of existing single/multi-primary plumbing where it
      already does the right thing.
  - Out of Scope:
    - New signal discovery.
    - Validation report content (Phase 5).
    - Public site styling (Phase 6).
  - Acceptance Criteria:
    - One command produces the daily all-ticker, multi-timeframe
      output.
    - Outputs land in a stable location with manifests.
    - Ranked list reproduces deterministically on a fixed data
      snapshot.
  - ELI5: Today Confluence answers a question about one ticker at a
    time. The headline deliverable answers a different question:
    across every ticker, where do timeframes line up today? Phase 4
    rebuilds Confluence around that question and writes the answer
    out in a form the public site can show.
  - Blocking Dependencies: Phase 3 manifests must exist so daily
    outputs are reproducible. Phase 1 canonical scoring must exist
    so the daily output is consistent across engines.

### Phase 5: Honest validation report

  - Purpose: Produce a defensible written report that quantifies
    findings and acknowledges the methodological risks.
  - In Scope:
    - Walk-forward analysis.
    - Holdout-period evaluation.
    - Baseline comparisons.
    - Bootstrap confidence intervals.
    - Explicit acknowledgment of overfitting risk and search-space
      size.
  - Out of Scope:
    - New algorithms.
    - Site copy beyond what the public explainer needs (Phase 6).
  - Acceptance Criteria:
    - Report committed at a stable path.
    - Each claim cites the test or computation that backs it.
    - Limitations section is concrete and unflinching.
  - ELI5: Numbers without context can mislead. The validation report
    measures the project against itself and against simple baselines,
    over time, with confidence ranges, and is honest about how big
    the search was and what that means. The report is the answer to
    "should we believe these results?"
  - Blocking Dependencies: Phases 1 through 4 must be complete so
    the report is measuring the final shipping system.

### Phase 6: PRJCT9.com goes live

  - Purpose: Publish a static explainer site that frames the project
    publicly, defensibly, and honestly.
  - In Scope:
    - Hosting setup (greenfield; no existing
      Netlify/Vercel/GitHub Pages configuration).
    - Static explainer pages.
    - Linkage to the daily confluence output and validation report.
    - A maintained public-facing entry point for the project.
  - Out of Scope:
    - Interactive backtesting in the browser.
    - Authenticated areas.
    - Live execution surfaces.
  - Acceptance Criteria:
    - PRJCT9.com resolves and serves the static explainer.
    - Daily confluence output is reachable from the site.
    - Validation report is reachable from the site.
  - ELI5: The site is the front door. People type the URL, see a
    clear explanation of what the project is and how to read the
    daily output, and leave with an honest impression. Phase 6 is
    the last mile: pick a host, ship the static pages, link the
    deliverables.
  - Blocking Dependencies: Phase 5 validation report must exist so
    the site links to substance, not promises.

---

## Section 6. Verified Findings from Codex Audit

  - Hardcoded Windows paths in trafficflow.py and stackbuilder.py:
    Phase -1 blocker.
  - StackBuilder Phase 2 vs Phase 3 scoring: code inspection shows
    the two phases can score through different paths and settings,
    potentially producing a K1 result that disagrees with the same
    folder's rank_direct or rank_inverse. Artifact-level confirmation
    is pending; the audit worktree did not contain project/output, so
    this is a code-verified risk awaiting output-folder verification
    on Peter's machine. Phase 1 blocker either way: canonical scoring
    unification eliminates the divergence by construction.
  - TrafficFlow raw Close pricing: correct under new spec. Other
    engines align to it via Adj Close elimination.
  - Adj Close call sites needing cleanup in Phase 1: spymaster.py,
    onepass.py, impactsearch.py, stackbuilder.py,
    signal_library/multi_timeframe_builder.py,
    signal_library/impact_fastpath.py, stale_check.py,
    QC/Clone of Project 9/main.py.
  - StackBuilder Dash multi-secondary closure bug at
    stackbuilder.py:1295. Important; lands in Phase 1 alongside
    canonical-scoring rewiring.
  - StackBuilder --outdir not honored. Important; lands in Phase 1.
  - ImpactSearch xlsx duplicate-row accumulation. Important; lands
    in Phase 1.
  - test_scripts/ absent from current tracked tree, gitignored.
    Phase 2 builds fresh.
  - Confluence dashboard upgrade: moderate-to-heavy lift, not a full
    rewrite. Reusable single/multi-primary pieces exist; daily
    all-ticker batch orchestration and durable outputs are new work.
  - Scheduler infrastructure: greenfield.
  - PRJCT9.com hosting: greenfield. No existing
    Netlify/Vercel/GitHub Pages setup.
  - Provenance manifests: partial in OnePass and multi-timeframe
    libs; minimal in StackBuilder; partial in SpyMaster pkls; absent
    in ImpactSearch xlsx and Confluence durable outputs. Full
    manifests across all engine outputs are a Phase 3 deliverable.
  - Algorithm spec document: confirmed missing. One status doc says
    spec was "NOT CREATED."

---

## Section 7. Working Tree Policy

Preflight observation, captured 2026-04-30 from `git status --short`
on origin/main:

```
?? "project/QC/BTC ETF EXT MASTER/"
?? "project/QC/BTC ETF MASTER/"
?? "project/QC/BTC TREND MASTER/"
?? project/md_library/shared/2025-12-19_EXECUTION_OVERHAUL_STOP_LOSS_FIX_IMPLEMENTATION.md
```

`git status --untracked-files=all` confirms no additional unignored
files beyond the four QC items listed above. The tracked-file
content under those untracked folders, surfaced by the same command,
is:

```
project/QC/BTC ETF EXT MASTER/btcbot.py
project/QC/BTC ETF MASTER/BTC ETF MASTER.py
project/QC/BTC TREND MASTER/BTC TREND MASTER.py
project/md_library/shared/2025-12-19_EXECUTION_OVERHAUL_STOP_LOSS_FIX_IMPLEMENTATION.md
```

Ignored local artifacts exist on the working machine
(`git ls-files -o -i --exclude-standard` surfaces examples like
`.claude/settings.local.json`, `mcp-server/`, launcher batch files,
QC backtests, QC local config, `__pycache__/`). These are
intentionally ignored, are not visible to the public repo, and must
NOT be added to sprint commits or to repo-level `.gitignore`
changes.

Sprint-relevance notes:

  - `project/QC/BTC ETF EXT MASTER/` (btcbot.py): out-of-sprint.
    QC live execution-related material.
  - `project/QC/BTC ETF MASTER/` (BTC ETF MASTER.py): out-of-sprint.
    QC live execution-related material.
  - `project/QC/BTC TREND MASTER/` (BTC TREND MASTER.py):
    out-of-sprint. QC live execution-related material.
  - `project/md_library/shared/2025-12-19_EXECUTION_OVERHAUL_STOP_LOSS_FIX_IMPLEMENTATION.md`:
    out-of-sprint. Live execution overhaul documentation. Live
    execution is explicitly out of scope for this sprint.

Explicit guidance: the four untracked items above relate to live
execution and are OUT of sprint scope. They MUST NOT be committed
as part of any sprint phase.

Codex noted that two of the untracked QC scripts contain the
intentional Gmail address noted in Section 3 Locked Decisions and
reference broker/account model setup. These traits do not change
the out-of-scope decision, but they reinforce that these files must
NOT enter the public repo in any sprint phase.

Recommended handling (Peter's call, outside the sprint):

  - Move to a private archive directory outside the repo, OR
  - Add to `.git/info/exclude` (machine-local ignore, not
    committed), NOT to the tracked `.gitignore`. The tracked
    `.gitignore` would advertise the file and folder patterns
    publicly, which defeats the purpose of keeping these out of the
    public repo. `.git/info/exclude` achieves the same `git status`
    suppression without any public footprint.

Do not add these QC paths to the tracked `.gitignore`.

Statement: untracked files were observed for the purpose of this
preflight only. This document was created without modifying,
moving, staging, or committing any of them.

---

## Section 8. How This Document Will Be Used

  - This document is the source of truth for the sprint.
  - Each phase has a separate implementation prompt drafted in chat
    by web Claude, then copied into Claude Code for execution.
  - Audit prompts for Codex cite this document by section number.
  - Amendments to this document are explicit, dated, and tagged
    with the phase that introduced them.
