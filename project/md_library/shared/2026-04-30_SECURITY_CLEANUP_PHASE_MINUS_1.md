# Phase -1 Security Cleanup

Document date: 2026-04-30
Branch: phase-minus-1-security-cleanup
Sprint plan reference:
project/md_library/shared/2026-04-30_PRJCT9_SPRINT_PLAN.md

## Purpose

Make the public repo at github.com/peterkitch/spy-project safe to be
public. After this phase, a stranger cloning the repo learns nothing
personal beyond the GitHub username and the intentional Gmail address
per sprint plan Section 3 Locked Decisions.

## Scope

This phase audited and cleaned three surfaces:

  - Part B: hardcoded Windows paths and other environment-coupled
    strings across all tracked files.
  - Part C: tracked artifacts with extensions .pkl, .json, .jsonl,
    .csv, .xlsx, .parquet, .db, .sqlite.
  - Part D: public commit history (refs reachable from origin) for
    credential/PII patterns, plus ever-committed-then-removed
    artifacts.

Out of scope (sprint plan Section 3 / phase boundaries):

  - Algorithmic behavior. Default path locations may visibly move
    by design; computed metrics, signals, and outputs are unchanged.
  - History rewrite. The history scan delivered here is a report
    only.
  - Adj Close elimination, canonical scoring, StackBuilder bugs,
    ImpactSearch dedupe — Phase 1.
  - Algorithm spec authoring, full test harness — Phase 0.
  - test_scripts/ gitignore status — carried forward to Phase 0/2.

## Findings

### Part B: hardcoded path matches

Discovery used `git grep` over tracked files only (no raw filesystem
walk). Patterns: `C:\Users\sport`, `Users\sport`, `Users/sport`,
`AppData`, `MiniConda`, `spy-project\project`, `spy-project/project`.

Matches by category:

  - Type 1/2 (code defaults): 3 lines in 2 files.
      project/stackbuilder.py:51 (DEFAULT_SIGNAL_LIB_DIR fallback)
      project/stackbuilder.py:60 (DEFAULT_IMPACT_XLSX_DIR)
      project/trafficflow.py:75 (SPYMASTER_PKL_DIR)
  - Type 3 (documentation): 8 markdown files, ~24 lines total.
      project/CLAUDE.md (2 lines)
      project/md_library/confluence/2025-10-19_MULTI_TIMEFRAME_CONFLUENCE_IMPLEMENTATION_PLAN.md (1 line)
      project/md_library/shared/2025-10-22_CLAUDE_TESTING_WINDOWS_PATH_SOLUTION.md (3 lines)
      project/md_library/shared/2025-11-13_CONDA_ACTIVATION_IN_BASH_TOOL_SOLUTION.md (~17 lines)
      project/md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md (1 line)
      project/md_library/spymaster/2025-10-13_PROCESSPOOL_IMPLEMENTATION_READY_FOR_TESTING.md (1 line)
      project/md_library/spymaster/2025-10-13_PROCESSPOOL_PARALLEL_OPTIMIZATION_SUCCESS_16_WORKERS.md (2 lines)
      project/md_library/trafficflow/2025-10-07_TRAFFICFLOW_OPTIMIZATION_STATUS_REPORT.md (1 line)
  - Type 4 (intentional): the Gmail address at
    global_ticker_library/gl_config.py:70 — left in place per
    sprint plan Section 3.

### Part C: tracked artifact audit

Inventory across the eight target extensions, sourced from
`git ls-files`:

  - .pkl: 0
  - .json: 0
  - .jsonl: 1 (signal_library/data/changelog/changelog_20250813.jsonl)
  - .csv: 0
  - .xlsx: 0
  - .parquet: 0
  - .db: 0
  - .sqlite: 0

Categorization (conservative; extension alone is not grounds to
untrack):

  - Safe: 1
  - Sanitize: 0
  - Untrack: 0

The single artifact is a 10-line signal-library rebuild changelog.
Sample row: `{"timestamp": "...", "ticker": "SPY", "action":
"full_rebuild", "version": "3.0.0", "reason": "...",
"acceptance_level": "REBUILD"}`. No PII, no credentials, no portfolio
or account data. Legitimate project reference; remains tracked.

### Part D: public-history scan

Refs scanned: `--remotes=origin` only (plus this phase branch).
Local stashes and local-only branches were not part of this scan
and are reported separately under Local Hygiene below.

Token regex used (case-insensitive `-G`):
`password|api_key|apikey|secret|token|credential|bearer|broker|account_id|account_number|login|oauth|private_key|ssh-rsa|BEGIN.RSA.PRIVATE|BEGIN.OPENSSH|AKIA|AIza|xoxb`

Findings, by category:

  - Cosmetic-only matches: 6 commits, all identifier-name or
    feature-label coincidences (e.g., `TOKEN_RE` regex for ticker
    parsing, `revision_token` Dash callback identifier, "Secret
    Treat: Top Performer" UI feature copy). The sprint plan doc
    itself matches because it discusses the audit categories using
    the same vocabulary.
  - Sensitive matches: 0.

Ever-committed-then-removed artifacts (`--diff-filter=A`/`D` over
the eight extensions):

  - signal_library/data/changelog/changelog_20250813.jsonl — added,
    still tracked. Audited as safe in Part C.
  - project/^GSPC_status.json — added, then deleted. Sample content:
    a runtime error message with internal SMA index names. Cosmetic.
  - project/spy_prices_up_to_2_15_2024.csv — added (~408 KB), then
    deleted. Public S&P 500 daily close history (Date,Close pairs
    starting 1927-12-30). Public market data; not sensitive.

Cross-references to prior findings:

  - Windows path leakage in trafficflow.py and stackbuilder.py:
    confirmed cosmetic. Forward fix is in this PR. Not present in
    history sensitivity threshold.
  - Intentional Gmail address: confirmed cosmetic per sprint plan
    Section 3 Locked Decisions.

### Local hygiene (NOT part of public history)

Local refs surfaced during tooling and excluded from the public
sensitivity scan:

  - 4 stash entries with `!!GitHub_Desktop<...>` markers, dating
    from various local-only branches (main-qc, signal-matrix-script,
    Automated-Optimization-Function, Extending-Default-Timeout).
  - Many local-only branches (refs/heads/...) that were never
    pushed to origin.

These are not visible to the public repo and are not part of any
rewrite recommendation. Peter may choose to clean them locally
(`git stash drop`, `git branch -D ...`) outside this phase. No
further action here.

## Decisions

  - Gmail at global_ticker_library/gl_config.py:70 remains in code
    (the intentional Gmail address per sprint plan Section 3).
  - Public history is NOT rewritten this phase. The history scan
    above is delivered as a report. Recommendation: do not rewrite.
    Reasoning: zero credential/PII matches; cosmetic-only findings;
    rewrite cost (broken commit refs in PRs, broken forks, forced
    re-clones) outweighs the benefit of erasing cosmetic noise.
  - QC out-of-sprint items continue to live in
    `.git/info/exclude` (machine-local), not in the tracked
    `.gitignore`. The tracked `.gitignore` would advertise the
    QC folder names publicly.
  - Conservative untracking standard applied: extension alone is
    not grounds to untrack a tracked artifact. The single tracked
    artifact (the signal-library changelog) is a legitimate
    reference file and stays.
  - The dead `.git/info/exclude` line for the previously-moved
    EXECUTION_OVERHAUL MD is left in place per the sprint plan;
    harmless, removing it has no functional benefit.

## Changes applied

### Code

  - project/stackbuilder.py: introduced `_PROJECT_DIR =
    Path(__file__).resolve().parent` anchor. Replaced the hardcoded
    Windows fallback for `DEFAULT_SIGNAL_LIB_DIR` with
    `_PROJECT_DIR / 'signal_library' / 'data' / 'stable'`. Replaced
    the hardcoded `DEFAULT_IMPACT_XLSX_DIR` with an env-var-overridable
    default rooted at `_PROJECT_DIR / 'output' / 'impactsearch'`.
  - project/trafficflow.py: introduced `_PROJECT_DIR =
    Path(__file__).resolve().parent` anchor. Replaced the hardcoded
    `SPYMASTER_PKL_DIR` with an env-var-overridable default rooted
    at `_PROJECT_DIR / 'cache' / 'results'`.

Anchoring rule: both files are root scripts directly under
`project/`, so `Path(__file__).resolve().parent` IS the project
directory. No multi-level `parents[N]` walks were needed in this
phase.

### Documentation

Replaced personal-username path segments with neutral placeholders
that preserve instructional intent:

  - `Users\sport`     -> `Users\<USERNAME>` (Windows backslash form)
  - `Users/sport`     -> `Users/<USERNAME>` (forward-slash / bash form)
  - `C:\Users\sport\AppData\...`        -> `%USERPROFILE%\AppData\...`
  - `C:\Users\sport\Documents\...`      -> `%USERPROFILE%\Documents\...`
  - CLAUDE.md "Working directory" line: rewritten to
    `<your local clone>\spy-project\project (e.g.,
    %USERPROFILE%\Documents\PythonProjects\spy-project\project)`.

Files edited (all under `project/`):

  - CLAUDE.md
  - md_library/confluence/2025-10-19_MULTI_TIMEFRAME_CONFLUENCE_IMPLEMENTATION_PLAN.md
  - md_library/shared/2025-10-22_CLAUDE_TESTING_WINDOWS_PATH_SOLUTION.md
  - md_library/shared/2025-11-13_CONDA_ACTIVATION_IN_BASH_TOOL_SOLUTION.md
  - md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md
  - md_library/spymaster/2025-10-13_PROCESSPOOL_IMPLEMENTATION_READY_FOR_TESTING.md
  - md_library/spymaster/2025-10-13_PROCESSPOOL_PARALLEL_OPTIMIZATION_SUCCESS_16_WORKERS.md
  - md_library/trafficflow/2025-10-07_TRAFFICFLOW_OPTIMIZATION_STATUS_REPORT.md

### .gitignore

No tracked-`.gitignore` changes in this phase. No artifacts were
untracked.

## New environment variables

Two new env vars were introduced under the `PRJCT9_<PURPOSE>_DIR`
naming convention. Both are optional. If unset, the default resolves
to a project-relative path on the cloning machine.

  - `PRJCT9_IMPACT_XLSX_DIR`
      Used in: project/stackbuilder.py
      Purpose: override the directory where StackBuilder reads
        ImpactSearch xlsx outputs.
      Default: `<project>/output/impactsearch`
  - `PRJCT9_SPYMASTER_PKL_DIR`
      Used in: project/trafficflow.py
      Purpose: override the directory where TrafficFlow reads
        SpyMaster pkl outputs.
      Default: `<project>/cache/results`

Existing env vars touched (no rename, defaults still respected):

  - `SIGNAL_LIBRARY_DIR` (project/stackbuilder.py)
      Default updated from a hardcoded Windows path to
      `<project>/signal_library/data/stable`. Env-var name unchanged
      to keep existing setups working.

## Future contributor guidance

  - Do NOT commit personal paths (e.g., `C:\Users\<you>\...`) or
    PII into tracked files. Use `Path(__file__).resolve().parent`
    or analogous parent walks for repo-relative defaults.
  - When a path legitimately varies per machine (caches, output
    roots, custom data locations), introduce an env var under the
    `PRJCT9_<PURPOSE>_DIR` convention and provide a project-relative
    default.
  - Markdown that documents local setup must use neutral
    placeholders (`%USERPROFILE%`, `<USERNAME>`, `<your local
    clone>`) rather than literal user paths.
  - Do not add machine-local artifacts (live-execution scripts,
    local backtests, machine-specific configs) to the tracked
    `.gitignore`. Use `.git/info/exclude` instead so the patterns
    do not leak publicly.
  - Run a `git grep` over tracked files for `Users\<USER>` /
    `Users/<USER>` before pushing if you have made path-related
    edits.
