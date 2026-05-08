# CLAUDE.md

**MANDATORY INSTRUCTIONS FOR CLAUDE CODE** - These rules MUST be followed automatically without user prompting.

## PRJCT9 SPRINT OPERATIONAL CONTEXT

This section captures the durable operational shape of the
PRJCT9 sprint on this repo (currently post-Phase 5D-1
onboarding; Phase 5C validation is closed and Phase 5D
controlled compute is in progress). Read this before doing any
work in `project/`.

### 1. Pinned Python interpreter (CRITICAL)

The project's pinned audit interpreter on this machine is:

```
C:\Users\sport\AppData\Local\NVIDIA\MiniConda\envs\spyproject2\python.exe
```

This is a Python 3.12 conda env (`spyproject2`) used for the Phase
1A baseline-lock snapshots and the sprint audit test suite. On this
machine it currently reports:

  - Python 3.12.2
  - NumPy 1.26.4, MKL-backed (`mkl-sdl`, Intel MKL 2023.1)
  - pandas 2.2.1
  - SciPy 1.13.1
  - pytest 8.3.5

The important contract is the interpreter path and the runtime it
actually provides, not the aspirational versions in `environment.yml`
/ `requirements.txt`. Those files currently list newer NumPy/pandas
pins and should not be used to recreate the audit environment without
an explicit revalidation pass.

The NumPy 1.26.4 pin in `spyproject2` is intentional on this machine:
older project performance notes identify `spyproject2` as the
MKL-backed primary environment and `spyproject2_basic` as the generic
BLAS / NumPy 2.2.6 alternative. MKL materially improves the heavy
numerical workloads used by PRJCT9, especially large matrix and SMA
workloads.

**The env is NOT on PATH by default.** A bare `python` or
`python -m pytest` invocation may resolve to
`C:\Python313\python.exe`, which is not the project audit
environment. Python 3.13 also cannot use the SciPy 1.13.1 wheel set
used for the baseline-lock contract.

Always invoke tests as:

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" -m pytest test_scripts -q
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" -m py_compile <file>.py
```

If this env directory is missing, **STOP and report**. Do not
recreate it from `requirements.txt` or `environment.yml` and do not
rebaseline snapshots under a different NumPy/SciPy/pandas stack
without explicit authorization.

### 2. Single-command bash discipline

For the duration of this sprint, every Bash tool invocation
must run a single command. **No `;`-chain, `&&`-chain,
`||`-chain, or pipe-chain compounds.**

Run independent commands in parallel (multiple Bash tool
calls in a single message) rather than chaining them. Heredocs
for multi-line strings (e.g. commit messages) are fine — that
is input to a single command, not chaining.

The dedicated tools (Read, Edit, Write, Glob, Grep) are
preferred over Bash whenever they fit.

### 3. Do NOT use `git -c commit.gpgsign=false`

GPG signing is not configured anywhere on this machine
(verified at all scopes via `git config --get`). The
`git -c commit.gpgsign=false` prefix is cargo-culting that
slipped in earlier in the sprint; it has been removed.

Use plain `git commit` (with heredocs for multi-line
messages). The `Bash(git -c:*)` allowlist entry in
`project/.claude/settings.local.json` stays as a defensive
provision but should not be exercised routinely. If a
future commit ever genuinely needs to bypass GPG, diagnose
the root cause (config drift, hook misconfig) rather than
adding the prefix back as a workaround.

### 4. Three-voice sprint workflow

The sprint runs as a three-voice collaboration. Each PR
cycles through:

  1. **web Claude** drafts a Codex preflight prompt for the
     upcoming PR scope.
  2. **Codex** runs the preflight in a parallel session and
     returns scope confirmations, risk callouts, and any
     out-of-scope items to defer.
  3. **web Claude** drafts a Claude Code implementation
     prompt based on Codex's preflight.
  4. **Claude Code** (this voice, in the project's terminal)
     implements according to the prompt.
  5. **Codex** audits the resulting PR and returns findings.
  6. Amendment cycles between web Claude, Claude Code, and
     Codex as needed.
  7. **Merge via squash, preserve branch** (no
     `--delete-branch`). Branches stay on origin as a
     visual audit trail of the sprint.

When the user signals "preflight is with Codex" or "audit
prompt is with Codex," idle. Don't run anything. When the
user pastes an implementation or amendment prompt, that's
authorization to start the work. Merge only on explicit
authorization ("PR #N MERGE"). Squash-merge command:
`gh pr merge <N> --squash` — never `--delete-branch`. After
merge, `git checkout main` then `git pull --ff-only origin
main`, and respond with the standard 7-point template
(squash confirmation, merge commit hash, title, local HEAD,
status, branch preserved on origin, surprises).

### 5. Authoritative documents

When implementing, debugging, or auditing, these are the
sources of truth:

  - **Algorithm spec (formal contract):**
    `project/md_library/shared/2026-04-30_PRJCT9_ALGORITHM_SPEC_v0_5.md`
    Spec sections are referenced by § number throughout the
    codebase (e.g. §13 capture units, §15 zero-capture
    trigger-day rule, §16 ddof=1 sample std, §17 sf-form
    p-value, §18 combine-consensus rule, §20 calendar grace
    days default).
  - **Intentional Delta Ledger (audit trail):**
    `project/md_library/shared/2026-05-01_PHASE_1B_INTENTIONAL_DELTA_LEDGER.md`
    Every numerically-observable behavior change made during
    the sprint is recorded here as a numbered Entry with old
    behavior, new behavior, affected tests/snapshots, ELI5
    paragraph, and status.
  - **Implementation inventory (call-site map):**
    `project/md_library/shared/2026-05-01_PHASE_1B_IMPLEMENTATION_INVENTORY.md`
    Cross-references engine call sites by spec section.
  - **Canonical scoring module:**
    `project/canonical_scoring.py`
    Single source of truth for metric math (`score_captures`,
    `score_signals`, `combine_consensus_signals`,
    `metrics_to_legacy_dict`, `CanonicalScore`). Engine
    helpers delegate here. Inline Sharpe / std / p-value /
    win-rate math has been removed from engines wherever a
    canonical score is in scope; documented exceptions live
    in the ledger preamble.

When in doubt: spec wins, then ledger, then inventory, then
code. If code disagrees with spec, the code is wrong unless
an explicit ledger entry classifies the divergence.

### 6. Sprint state (as of 2026-05-08)

Phase 0 -> Phase 5D-1 onboarding merged to `main`:

  - #128 Phase -1 (token sweep)
  - #129 Phase 0 (spec v0.5, env, import smoke)
  - #130 Phase 1A (baseline lock + snapshots + coverage)
  - #131 Phase 1B-1 (inventory + canonical scoring module +
    ledger skeleton)
  - #132 Phase 1B-2A (canonical scoring rewire + Adj Close
    removal)
  - #133 Phase 1B-2B (backlog cleanup)
  - #134 Phase 2A (test infrastructure + static guards)
  - #135 Phase 2B-1 (lookahead audits + canonical correctness
    expansion + confluence smoke)
  - #136 Phase 2B-2A (parity suites + StackBuilder
    `_score_primary` zero-capture fix)
  - #137 Phase 2B-2B (grace plumbing refactor +
    rank_inverse structural fix in normal path and xlsx
    fast-path + xlsx loud-fail test pinning)
  - #140 Phase 3A (signal-library provenance manifests)
  - #142 Phase 3B-1 (manifest perf cache + central loader +
    B12 tightening)
  - #143 Phase 3B-2A (output manifest helper + StackBuilder
    run manifests + Spymaster PKLs)
  - #144 Phase 3B-2B (XLSX upsert manifests + strict-mode
    CLI + Phase 3 close)
  - #145 Post Phase 3 Sprint Bug Cleanup (Spymaster
    annualized_return + Confluence dedupe + StackBuilder
    stale-XLSX message + regression tests)
  - #146 PRJCT9 North Star + Phase 4 Scoping docs
  - #147 Phase 4A (cross-ticker multi-timeframe confluence
    aggregation engine)
  - #148 Phase 4B (cross-ticker confluence operator Dash)
  - #149 Phase 5 Pre-Flight (validation, cleanups,
    controlled compute + Path 2 backend, pre-launch
    hardening)
  - #166 Phase 5C validation methodology lock
  - #167-#169 Phase 5C-2a validation engine foundation,
    empirical layer, sidecars, manifest hook, baseline persistence
  - #170 Phase 5C-2b ImpactSearch validation integration
  - #171 Phase 5C-2c StackBuilder validation integration
  - #172 Phase 5C-2d Spymaster prep extraction
  - #173 Phase 5C-2d Spymaster validation integration
  - #174 Phase 5C-2e Confluence validation integration
  - #175 Phase 5C-3 honest validation report ledger
  - #176 Phase 5D-1 local controlled compute orchestrator
  - #177 Phase 5D-1 operational onboarding

**Phase 5C: Honest validation framework - CLOSED.**
The locked methodology lives at
`project/md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md`.
All four PRJCT9 apps are wired through validation_contract_v1
where their tier supports it, and `honest_validation_ledger.py`
aggregates durable validation sidecars.

**Phase 5D: Controlled compute - IN PROGRESS.**
5D-1 local orchestration and onboarding are merged. 5D-2
distributed cluster and 5D-3 cloud burst are not started and need
fresh scoping plus Peter input before implementation.

**Phase 5G: Data licensing pre-launch gate - NOT STARTED.**
Runs in parallel with later 5D work and gates Phase 6 launch
scoping.

**Phase 6: PRJCT9.com - NOT STARTED.**
Public-facing UX / website. Gated by 5G.

Confirm current state with `git log -10 --oneline main`;
this section may lag reality if PRs land without an update.

### 7. Deferred work items

These items have been intentionally deferred. When working
on the named gate phase, surface them so they can be
scheduled or explicitly re-deferred.

  - **B11 `compute_signals` delete-or-shift-correct
    (deferred through Phase 3 / Phase 4; classified for
    Phase 5B in
    `2026-05-05_PHASE_5A_CLEANUP_LEDGER.md` Item 6):** the
    function in spymaster has a dead-code static guard
    (`test_b11_spymaster_compute_signals_uncalled` in
    `project/test_scripts/test_lookahead_guards.py`) but
    the function body has a shift-correctness question.
    Either delete the function or fix the shift and change
    the guard from "uncalled" to "shift-correct." Phase 5B
    preflight picks one before code change begins.
  - **QC clone Adj Close sites:** at
    `project/QC/Clone of Project 9/main.py:103, 918, 1509`.
    QC clone is a frozen historical snapshot, intentionally
    excluded from the Entry 1 (Adj Close removal) sweep.
    Revisit only on explicit scope expansion.

## AUTOMATIC BEHAVIORS - DO NOT DEVIATE

### File Creation Rules (NEVER VIOLATE)
1. **NEVER create files in the root directory** except when explicitly modifying core apps (spymaster.py, impactsearch.py, onepass.py)
2. **ALWAYS place test scripts in `test_scripts/`** subdirectories:
   - Spymaster tests → `test_scripts/spymaster/`
   - ImpactSearch tests → `test_scripts/impactsearch/`
   - OnePass tests → `test_scripts/onepass/`
   - GTL tests → `test_scripts/gtl/`
   - Multi-script/environment tests → `test_scripts/shared/`
3. **ALWAYS place documentation in `md_library/`** subdirectories:
   - Use script-specific folders for script-specific docs
   - Use `md_library/shared/` ONLY for docs affecting multiple scripts
4. **ALWAYS check current date before creating dated files**: Use `date` command
   - Current date is September 2025, NOT January or August 2025
   - Format: `YYYY-MM-DD_ACTION_DESCRIPTION_IN_CAPS.md`

### Testing Rules (AUTOMATIC)
1. **NEVER use Unicode in console output** - Windows uses cp1252 encoding
   - Use `[OK]` not `✅`, `[FAIL]` not `❌`, `->` not `→`
2. **ALWAYS follow Selenium cache clearing procedure**:
   - Kill Python processes → Clear disk cache → Restart app → Run test
   - See: `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`
3. **ALWAYS verify functionality**, not just compilation

### Code Modification Rules (STRICT)
1. **NEVER modify spymaster.py's standalone architecture** - It's the regression baseline
2. **ALWAYS use the optimized launcher** for performance testing:
   - Location: `local_optimization/batch_files/LAUNCH_OPTIMIZED_V4.bat`
3. **ALWAYS enable ImpactSearch FastPath** unless testing slow path:
   - Set `IMPACT_TRUST_LIBRARY=1`
   - See: `md_library/impactsearch/2025-09-16_IMPACTSEARCH_FASTPATH_OPTIMIZATION_IMPLEMENTATION.md`

### Clean Repository Rules (MAINTAIN AT ALL TIMES)
1. **DELETE temporary files immediately** after use
2. **MOVE misplaced files immediately** when discovered
3. **FOLLOW naming conventions strictly** - no exceptions

## Project Overview

This is a quantitative trading analysis web application built with Python and Dash. It implements an adaptive simple moving average (SMA) pair optimization system for systematic trading analysis and mean reversion strategies.

## Important Principles

### Symbol Validity
- **There is no such thing as a "junk symbol" if it has a 'max' period that we can download**
- Any symbol that returns data from Yahoo Finance is valid and valuable
- Symbols ending in MM (money market), X (mutual funds), or with dots are legitimate
- Do not dismiss symbols as "obscure" or "junk" based on their format
- The system should give all symbols equal opportunity for validation

## Development Environment

**Operating System**: Windows (platform: win32) — Linux/macOS clones should also work; the Windows-specific notes below only apply on win32.
**Local audit hardware**: Intel Core i7-13700F (16C/24T), 192GB DDR5 RAM, RTX 2080 Ti + RTX 4060 Ti GPUs.
**Recommended Shell**: PowerShell 7+ (`pwsh`). CMD and Git Bash are supported but PowerShell is the canonical contributor shell. Older Git Bash workarounds are preserved as historical notes in `md_library/shared/2025-11-13_CONDA_ACTIVATION_IN_BASH_TOOL_SOLUTION.md` and `md_library/shared/2025-10-22_CLAUDE_TESTING_WINDOWS_PATH_SOLUTION.md`.
**Python Environments**:
  - **spyproject2** (Primary) - Has Intel MKL, NumPy 1.26.4, optimized BLAS
  - **spyproject2_basic** (Alternative) - Generic BLAS, NumPy 2.2.6, no MKL
    - Note: This was formerly named `spyproject2_mkl` (misleading name has been corrected)
**Python environment setup**: create from `project/environment.yml` using Conda, Mamba, or Micromamba (`conda env create -f project/environment.yml`). Activate with `conda activate spyproject2`. Do not assume any particular Conda install location; the activate command works once your shell has Conda initialized. A pip-only path is available via `project/requirements.txt`.

### CRITICAL DATE AWARENESS ISSUE

**IMPORTANT**: The system may show incorrect dates. When creating MD files with date prefixes:

- **ALWAYS verify the actual current date** using `date` command before naming any dated file.
- **Format**: `YYYY-MM-DD_ACTION_DESCRIPTION_IN_CAPS.md`.
- **Do not rely on memory or training data for the current date.**

### Windows shell notes:
- Environment variables (PowerShell): `$env:VAR = "value"; command` (or `;` between statements)
- Environment variables (CMD legacy): `set VAR=value && command`
- File paths: Use backslashes (escape in Python strings) or forward slashes; both resolve under Windows
- Console encoding: cp1252 (avoid Unicode characters in console output; see CLAUDE.md Unicode rule)
- Working directory: `<your local spy-project clone>/project`

## Development Commands

### Environment Setup
```bash
# Create and activate conda environment
conda env create -f environment.yml
conda activate spyproject2
```

### Running Tests
```bash
# From the repository root, enter project/ so the engines' relative
# import-time log writes land under project/logs/, which is already
# ignored by project/.gitignore.
cd project
python -m pytest test_scripts -q
```

Phase 1 should anchor engine log handlers to project/logs instead of the current working directory; the `cd project` step is a temporary workaround.

### Running Applications

#### Using the Optimized Launcher (Recommended)
```bash
# Navigate to launcher directory
cd local_optimization\batch_files

# Run the optimized launcher with system detection
LAUNCH_OPTIMIZED_V4.bat
```

The launcher provides:
- Automatic CPU core detection (P-cores vs E-cores for Intel 13th gen)
- Performance profiles: Conservative (25%), Balanced (50%), Performance (75%), Maximum (100%)
- MKL threading optimization (MKL_NUM_THREADS, OMP_NUM_THREADS, etc.)
- ImpactSearch FastPath configurations

#### Direct Application Launch
```bash
# Main trading analysis dashboard (default port 8050)
python spymaster.py

# Impact search analysis tool (port 8051)
python impactsearch.py

# Single-pass analysis
python onepass.py

# Global Ticker Library validation
cd global_ticker_library
python run.py --validate-manual
```

#### ImpactSearch FastPath Configuration
Critical environment variables for ImpactSearch optimization:
- `IMPACT_TRUST_LIBRARY=1` - Enable fastpath (reduces API calls from 73,000+ to 1)
- `IMPACT_TRUST_MAX_AGE_HOURS=720` - Production: 30 days cache validity
- `IMPACT_TRUST_MAX_AGE_HOURS=168` - Conservative: 7 days cache validity
- `IMPACT_CALENDAR_GRACE_DAYS=10` - Grace period for calendar adjustments

**Note**: See `md_library/impactsearch/2025-09-16_IMPACTSEARCH_FASTPATH_OPTIMIZATION_IMPLEMENTATION.md` for fastpath gate mismatch fix details

### Building Executable
```bash
# Create standalone executable using PyInstaller
pyinstaller spymaster.spec
```

## Architecture

### CRITICAL: Spymaster.py Standalone Design (Regression Testing Baseline)

**IMPORTANT**: Spymaster.py is **intentionally standalone** by design. This is a FEATURE, not a bug!

#### Key Architectural Principles
1. **Complete Independence**
   - NO dependencies on other project modules (signal_library, global_ticker_library, onepass, impactsearch)
   - Direct yfinance calls for all data fetching
   - Isolated caching system in `cache/results/` and `cache/status/`
   - Self-contained calculations for all metrics

2. **Regression Testing Role**
   - Serves as the **gold standard** for metric verification
   - Provides baseline metrics for comparison
   - Ensures new implementations match expected results
   - Acts as the "source of truth" for trading metrics

3. **Why This Matters**
   - **Stability**: Changes to signal_library or other modules don't affect spymaster
   - **Reliability**: Known-good implementation for testing against
   - **Verification**: Can cross-check results from integrated scripts
   - **Independence**: Can run without any other project components

#### Development Rules for Spymaster.py
**DO NOT:**
- Add imports from signal_library to spymaster.py
- Integrate global_ticker_library into spymaster.py
- Create dependencies between spymaster and other scripts
- Share cache files between spymaster and other modules

**DO:**
- Keep spymaster.py completely self-contained
- Use spymaster.py to verify metrics from new scripts
- Maintain spymaster's direct yfinance implementation
- Preserve the isolated caching system

#### Testing Workflow
1. Run analysis in spymaster.py → Get baseline metrics
2. Run same analysis in new/modified script → Get test metrics
3. Compare results → Verify accuracy
4. If discrepancies found → Debug the new script (not spymaster)

### Core Structure

Per-app surfaces:

- **spymaster.py**: Main Dash dashboard (STANDALONE - regression baseline; black background, #80ff00 green accent "PRJCT9 branding")
- **impactsearch.py**: Cross-asset pattern discovery; primary-secondary signal correlation; durable XLSX export tier per locked 5C-1 methodology
- **onepass.py**: Signal generation and ticker library construction
- **stackbuilder.py**: Multi-primary stack construction with full-refit walk-forward validation; durable-only tier per locked 5C-1 methodology
- **confluence.py**: Multi-primary confluence engine; interactive-tier validation per locked 5C-1 methodology
- **trafficflow.py**: Cross-asset traffic flow analysis (contains the disabled matrix.py code path)
- **tickerdash.py**: Per-ticker dashboard with single-job model
- **run.py**: Ticker universe construction

Validation infrastructure (Phase 5C track):

- **validation_engine.py**: Canonical validation contract foundation; walk-forward fold generation; BH + Bonferroni multiple comparisons; empirical permutation/bootstrap layer; sidecar emission via write_validation_sidecar
- **canonical_scoring.py**: Locked scoring contract (score_captures, combine_consensus_signals)
- **provenance_manifest.py**: Output manifest schema with optional validation_summary participation
- **honest_validation_ledger.py**: Cross-app validation_contract_v1 sidecar aggregation; produces validation_ledger_v1 markdown + JSON

Compute infrastructure (Phase 5D track):

- **controlled_compute.py**: Local subprocess + ProcessPoolExecutor orchestrator; budget controls; sidecar verification (exact-path or discovery modes); compute_manifest_v1

Cross-cutting:

- **shared_*.py / signal_library/**: Shared utilities consumed by per-app modules
- **md_library/shared/**: Locked methodology + scoping docs; reference these directly when source-of-truth is needed

### Data Flow
1. **Market Data**: Fetched via yfinance API into pandas DataFrames
2. **Signal Processing**: SMA calculations with configurable windows and thresholds
3. **Statistical Analysis**: Computation of Sharpe ratios, capture ratios, win/loss statistics
4. **Caching Layer**: Results stored as pickle files (`{TICKER}_precomputed_results.pkl`)
5. **Status Tracking**: JSON files track processing progress (`{TICKER}_status.json`)

### Key Technologies
- **Web Framework**: Dash with Bootstrap Components on Flask backend
- **Data Processing**: pandas, numpy, scipy for vectorized calculations
- **Visualization**: Plotly for interactive charts
- **Concurrency**: Threading for parallel ticker processing
- **Caching**: joblib Memory and pickle serialization

### UI Components
- Dark theme with green text on black background
- Multi-section input forms for batch processing
- Interactive result tables and charts
- Built-in help modal system
- Real-time progress tracking

### Performance Considerations
- Heavy use of caching to avoid redundant calculations
- Vectorized operations using scipy for speed
- Multi-threaded processing for concurrent ticker analysis
- Progress bars (tqdm) for long-running operations
- Optimized interval updates from 5 seconds to 3 seconds for faster chart loading

### Data Files
- **Input**: Market data fetched from yfinance
- **Cache**: `*.pkl` files for precomputed results
- **Status**: `*.json` files tracking analysis progress
- **Output**: Excel files for detailed analysis exports
- **Logs**: `debug.log`, `analysis.log` for troubleshooting

## Sprint State (as of 2026-05-08)

The PRJCT9 sprint reached Phase 5D-1 onboarding closure on this date. Snapshot below for fast context recovery on session start.

### Three-voice workflow doctrine (non-negotiable)

PRJCT9 work flows through three agents:

- **Web Claude (Claude Desktop)**: co-foreman; drafts implementation prompts for Claude Code, drafts audit prompts for Codex, evaluates responses, iterates.
- **Claude Code (this agent)**: implementer; full repo access; reads CLAUDE.md automatically.
- **Codex via Emdash**: independent auditor; read-only against the repo; references AGENTS.md.

Peter is courier between agents. All git/bash work flows through Claude Code. Squash-merge with branch preservation (NEVER `--delete-branch`). Single-command bash invocations; no GPG prefix workaround. Doc/scoping PRs may skip Codex draft-review and go directly to Claude Code.

### Phase 5 track (in progress)

**Phase 5C - validation framework - CLOSED:**

- 5C-1 (#166): validation_methodology_v1 LOCKED in md_library/shared/2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md (walk-forward 5y/1y/1y; BH primary + Bonferroni supplementary; hybrid empirical layer; honest-scope 11 discipline).
- 5C-2 (#167-#174): per-app validation integration across all four apps (ImpactSearch, StackBuilder, Spymaster, Confluence). Cross-app contract parity via validate_validation_contract_v1.
- 5C-3 (#175): honest_validation_ledger.py cross-app sidecar aggregation; produces validation_ledger_v1 (markdown + JSON).

**Phase 5D - controlled compute - partial:**

- 5D-1 orchestrator (#176): controlled_compute.py with subprocess + ProcessPoolExecutor; budget controls; exact-path sidecar verification; compute_manifest_v1.
- 5D-1 onboarding (#177): sidecar discovery mode for apps that generate validation run_ids internally (e.g., StackBuilder); StackBuilder example spec at project/examples/controlled_compute/stackbuilder_onboarding_job_spec.json; operator runbook at md_library/shared/2026-05-08_PHASE_5D_1_OPERATIONAL_ONBOARDING.md.
- 5D-2 distributed cluster: NOT STARTED (LAN multi-machine coordination; needs scoping + Peter input on architecture).
- 5D-3 cloud burst: NOT STARTED (cloud workers + cost monitoring; needs scoping + Peter input).

**Phase 5G - data licensing pre-launch gate:** NOT STARTED. Parallel sub-phase that gates Phase 6. Research/legal scope; yfinance ToS review + alternative data source survey.

### Future phases

- **Phase 6**: public-facing UX / website. Gated by 5G.
- **Phase 7+**: volunteer compute, BYO-data ingestion, full Wikipedia/crowdsourcing layer per North Star vision (md_library/shared/2026-05-04_PRJCT9_NORTH_STAR.md).

### Test baseline

566 tests passing on main HEAD da7244e. Zero failures, zero new skips. 60 unchanged pre-existing pandas fragmentation warnings in test_lookahead_poison.py:49 (carried since Phase 5B-MP).

### Locked methodology references

Source-of-truth docs in md_library/shared/:

- `2026-05-04_PRJCT9_NORTH_STAR.md` - Phase 7+ vision
- `2026-05-04_PHASE_4_SCOPING.md` - Phase 4 controlled compute commitment
- `2026-05-05_PHASE_5_PRE_FLIGHT.md`
- `2026-05-06_PHASE_5C_PRE_FLIGHT.md`
- `2026-05-06_PHASE_5C_VALIDATION_METHODOLOGY.md` - locked validation contract + methodology
- `2026-05-08_PHASE_5D_1_OPERATIONAL_ONBOARDING.md` - controlled compute operator runbook

Reference these directly when source-of-truth is needed; do not paraphrase locked content from memory.

### Carry-forward technical debt (tracked but not blocking)

- B11 compute_signals cleanup (deferred)
- environment.yml/requirements.txt hygiene (deferred)
- Deferred UI/operational issues from Post Phase 3 Codex audit (OnePass error UX, TrafficFlow refresh callback, ImpactSearch error taxonomy, StackBuilder progress JSON, TickerDash global single-job model)
- ImpactSearch capture-metric integrity audit (parked; affects Core Club / direction-flip integrity / geography tiers / cross-asset signals findings until resolved)
- ProcessPoolExecutor parallelism for empirical layer inside validation_engine itself (deferred per Codex 5C-2a preflight)

### Key principles to preserve across phases

- **matrix.py does not exist as a file.** The concept survives only as a disabled code path in trafficflow.py.
- **.bat files** are local shortcuts, gitignored by design.
- **QC clone is parked indefinitely.** Exclude from all current phase work.
- **Scoping docs follow decisions, not precede them.** Drafting a scoping doc before underlying decisions are Codex-reviewed creates rework cycles.
- **Per-app preflight justified ONLY when parent scoping defers questions to it.** Default flow: scoping doc -> Web Claude drafts implementation prompt -> Codex sign-off -> Claude Code implements.

## Known Issues to Address
- Position return calculation needs actual entry price tracking
- Confidence calculation could incorporate more factors
- Price threshold visualization could be more interactive

## Code Quality Notes
- PerformanceMetrics class successfully modularized (750+ lines)
- Visual components are reusable and consistent
- Boolean flags reduce redundant calculations
- Error handling improved throughout

## Testing Checklist for Next Session
- [ ] Verify position return calculations with real data
- [ ] Test threshold parsing with edge cases
- [ ] Confirm position transition warnings appear correctly
- [ ] Validate confidence scores across different scenarios
- [ ] Check all visual components render properly

## SMA Pair Optimization Notes

### Important Considerations for SMA Pair Analysis
- The script should not rely on phrases like "fast" or "slow" when discussing SMA properties
- SMA 1 and SMA 2 refer to the first and second inputs for buy pairs
  - A top buy pair can have various SMA configurations (e.g., 10,1 or 1,10)
  - SMA 1 and SMA 2 cannot be the same value
- SMA 3 and SMA 4 refer to the short pair
  - Similar flexibility applies to short pair configurations (e.g., 10,1 or 1,10)
  - SMA 3 and SMA 4 cannot be the same value
- Verify that metric reporting and dashboard visuals accurately reflect these flexible SMA pair configurations

### Understanding SMA Pair Signal Logic
**CRITICAL**: The same pair (e.g., 114,113) can be used for both buy and short signals with opposite comparison operators:
- **Buy signal for pair (A,B)**: Triggered when SMA_A > SMA_B
- **Short signal for pair (A,B)**: Triggered when SMA_A < SMA_B
- Example: Pair (114,113) on day 0:
  - Buy (114,113): Buy when SMA_114 > SMA_113
  - Short (114,113): Short when SMA_114 < SMA_113
  - These are opposite conditions using the same pair!
- The "top" buy/short pair is the one with the highest cumulative capture for its respective signal type
- Sentinel initialization values:
  - Spymaster uses (MAX_SMA_DAY, MAX_SMA_DAY-1) = (114, 113) for both buy and short on day 0
  - This represents impossible conditions initially (SMA_114 can't be both > and < SMA_113 simultaneously)

## Development Guidelines & Best Practices

### MANDATORY Repository Organization

**YOU MUST automatically enforce these rules WITHOUT being asked:**

#### File Placement (ENFORCE IMMEDIATELY)
- **Root directory = FORBIDDEN for new files** (only modify existing core apps)
- **Test scripts = MUST go in `test_scripts/[appropriate_subfolder]/`**
- **Documentation = MUST go in `md_library/[appropriate_subfolder]/`**
- **Temporary files = DELETE IMMEDIATELY after use**
- **Utilities = MUST go in `utils/[appropriate_subfolder]/`**

#### When Creating ANY File, You MUST:
1. **CHECK**: Is this a test? → `test_scripts/[app_name]/`
2. **CHECK**: Is this documentation? → `md_library/[app_name]/`
3. **CHECK**: Is this temporary? → Create, use, DELETE immediately
4. **CHECK**: Current date with `date` command before naming
5. **NEVER**: Place in root unless modifying existing core files

#### Automatic Cleanup Actions:
- **If you see a test file in root** → Move it immediately
- **If you see an MD file in root** → Move it immediately
- **If you create a temporary file** → Delete it before session ends
- **If you see wrongly dated files** → Note in response and fix if possible

### Documentation Organization & Reference Guide

#### Where to Store Documentation
- **NEVER place new markdown files in the root project folder** (except CLAUDE.md)
- **ALWAYS use date prefix and descriptive uppercase title for markdown filenames**: `YYYY-MM-DD_DESCRIPTION_IN_CAPS.md`
  - Date format: YYYY-MM-DD (ISO 8601) - **VERIFY CURRENT DATE FIRST**
  - Description: Use UPPERCASE with underscores, be specific about the content
  - Include action words like: INVESTIGATION, FIX, ENHANCEMENT, REFACTOR, IMPLEMENTATION, ANALYSIS
  - Good examples:
    - `2025-09-16_UNICODE_AND_SELENIUM_TEST_ISSUE_INVESTIGATIONS.md` (investigation into problems)
    - `2025-09-14_ADAPTIVE_INTERVAL_PERFORMANCE_6X_FASTER.md` (performance improvement)
    - `2025-09-15_CODE_CLEANUP_667_LINES_REMOVED.md` (refactoring work)
  - Avoid vague terms like: FINDINGS, NOTES, CHANGES, UPDATE
- All documentation should be organized in the `md_library/` directory structure:
  - **IMPORTANT: Store MD files directly in their associated directories - NO SUBDIRECTORIES**
  - `md_library/spymaster/` - Spymaster-specific documentation
  - `md_library/impactsearch/` - ImpactSearch documentation
  - `md_library/onepass/` - OnePass documentation
  - `md_library/shared/` - Documentation that affects multiple scripts:
    - Signal library fixes (used by both ImpactSearch and OnePass)
    - Environment/MKL optimization (affects all scripts)
    - NumPy compatibility issues (cross-cutting)
    - Testing procedures (general testing guidelines)
  - `md_library/global_ticker_library/` - GTL documentation
- Text files (.txt) for quick changes/notes can remain in root temporarily but should be cleaned up promptly

#### Documentation Quick Access Map
**USE THE "MANDATORY DOCUMENTATION LOOKUPS" SECTION ABOVE** for detailed guidance on:
- Which documents to read BEFORE starting any task
- Exact file paths for critical documentation
- Search commands to find relevant docs
- Automatic documentation check protocol

**Key principle**: NEVER start a task without checking for existing documentation first

### Git Branch Naming Conventions
- **Be specific about scope and purpose** - branches should clearly indicate what they affect
- **Use descriptive prefixes** to identify the area of work:
  - `claude-` for CLAUDE.md or Claude behavior updates
  - `spymaster-` for spymaster.py changes
  - `impactsearch-` for impactsearch.py changes
  - `onepass-` for onepass.py changes
  - `docs-` for general documentation (but be specific about which docs)
- **Good branch name examples**:
  - `claude-testing-guidelines` - Updates to Claude's testing behavior
  - `spymaster-unicode-fix` - Fixing Unicode issues in spymaster.py
  - `impactsearch-performance-optimization` - Performance improvements
  - `onepass-sma-calculation-bug` - Specific bug fix in onepass
- **Avoid vague branch names**:
  - `docs/testing-guidelines` - Whose testing guidelines?
  - `fix/bug` - Which bug? Where?
  - `update/readme` - Which readme? What update?
  - `feature/new` - What feature? For which component?
- **Use hyphens**, not underscores or slashes (except for feature/ or bugfix/ prefixes if using git flow)

### Git Diff Request Guidelines
When handling git diff requests, pay attention to whether the user wants a file or just output:

**CREATE a .txt file when user says:**
- "Create a git diff file"
- "Provide a git diff .txt file"  
- "Generate a git diff and save it"
- "Produce a git diff text file"
- Any request explicitly mentioning ".txt", "file", or "document"

**DO NOT create files when user says:**
- "Run a git diff and provide a summary"
- "Show me the git diff"
- "What are the changes?"
- "Run git diff" (without mentioning a file)

**If creating a file:**
1. **Always create ONE single .txt file** - No multiple attempts or versions
2. **Include full file contents** - Use standard git diff format showing all changes
3. **For untracked files** - Use `git add -N` temporarily to include them in diff, then `git reset HEAD` after
4. **Naming convention** - Use descriptive names like `global_ticker_library_full_diff.txt`

**If just displaying output:**
- Run git diff and show results in terminal/chat
- Provide summary or highlights as requested
- No files should be created

### Testing Guidelines (MANDATORY PROCEDURES)

#### YOU MUST AUTOMATICALLY:
1. **Place ALL test scripts in `test_scripts/` subdirectories** - NO EXCEPTIONS
2. **Use ASCII characters in console output** - NO UNICODE
3. **Clear both cache layers for Selenium** - disk AND session
4. **Verify actual functionality** - compilation is not enough

#### Unicode Handling (AUTOMATIC REPLACEMENT)
**When writing ANY console output, you MUST automatically use**:
  - Windows console uses cp1252 encoding which cannot display Unicode characters
  - This causes `UnicodeEncodeError` when Python tries to print Unicode to the Windows terminal
  - Use ASCII alternatives: [OK], [FAIL], [WARNING] instead of ✅, ❌, ⚠️
  - Use simple separators: ===, ---, ### instead of fancy Unicode boxes
  - Use ASCII arrows: -> instead of → (U+2192)
- **Unicode IS safe to use in:**
  - Dash web interfaces (HTML/browser handles Unicode perfectly)
  - Log files written with UTF-8 encoding
  - Internal Python string processing
  - Web-based outputs (JSON, HTML, etc.)
- **The issue is ONLY with Windows console output (cmd.exe, PowerShell)**

#### Selenium Testing Procedures
- **CRITICAL: Two-Layer Cache System** - See `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`
- **Before running Selenium tests, you MUST**:
  1. Kill all running Python processes: `taskkill /F /IM python.exe`
  2. Clear disk cache completely: `rmdir /S /Q cache` and `del *.pkl *.json`
  3. Restart spymaster fresh: `python spymaster.py`
  4. Only then run Selenium test: `python utils\spymaster\selenium_tests\test_spymaster_comprehensive.py`
- **Cache contamination warning**: Spymaster maintains both disk cache and session cache - clearing disk alone is insufficient
- **Test coverage includes all 7 ticker input locations** in spymaster

#### Test Verification Requirements
- All tests should include verification of newly implemented metrics, visuals, functions, or other components
- It is not enough that the app compiles - verify actual functionality
- Use regression testing with spymaster.py as the baseline (it's intentionally standalone)

### Callback & Interval Handling
- **Interval updates are critical for chart loading** (currently 3 seconds)
  - Do NOT block interval callbacks - they enable progressive data loading
  - Charts depend on these intervals to populate properly
  - Optimized from 5 seconds to 3 seconds for better responsiveness
- **Variable scope in callbacks**
  - Variables defined in callback functions are NOT automatically accessible in nested functions
  - Use proper parameter passing or closure patterns
  - The `should_log` pattern requires careful scope management
- **Callback context detection**
  - Use `dash.callback_context` to identify trigger source
  - Distinguish between user actions (ticker changes) and interval updates
  - Apply different logic based on trigger type

### Debugging Dash Applications
- **Console logging control**
  - Separate logging logic from data processing logic
  - Use conditional logging based on callback trigger type
  - Prevent log spam from interval updates while maintaining functionality
- **Data flow understanding**
  - Trace complete execution paths before implementing fixes
  - Understand how data moves through callbacks and updates
  - Consider caching strategies for expensive computations

### Recent Bug Fixes & Lessons Learned

#### Ticker Processing Loop Fix (2025-01-11)
**Problem:** After processing one ticker, entering a new ticker caused repeated logging every 3 seconds (formerly 5 seconds)

**Root Cause:** The `update_dynamic_strategy_display` callback runs on both ticker changes AND interval updates. The `should_log` variable was defined locally but wasn't accessible to all logging code paths.

**Key Lessons:**
1. Variable scope matters - local callback variables don't propagate to nested function calls
2. Interval updates must continue running for charts to load properly
3. Logging should be controlled separately from data processing
4. The 3-second intervals are essential for dashboard functionality

**Solution Applied:**
- Proper callback context detection to identify trigger source
- Conditional logging based on whether ticker changed vs interval update
- Maintained all data processing while controlling console output
- Preserved the critical 3-second refresh cycle for chart updates

## QUICK REFERENCE - AUTOMATIC ACTIONS

### When Starting ANY Task:
1. **CHECK root directory** - Move any misplaced files immediately
2. **CHECK date** - Run `date` command before creating dated files
3. **CHECK file placement** - Never save to root

### When Creating Files:
- **Test script?** → `test_scripts/[app]/test_*.py`
- **Documentation?** → `md_library/[app]/YYYY-MM-DD_*.md`
- **Temporary?** → Create, use, delete immediately
- **In root?** → STOP, move to correct location

### When Testing:
- **Console output** → Use [OK], [FAIL], [WARNING] - NO Unicode
- **Selenium test** → Kill processes, clear cache, restart app
- **Performance test** → Use LAUNCH_OPTIMIZED_V4.bat

### When Documenting:
- **Script-specific** → `md_library/[script_name]/`
- **Affects multiple** → `md_library/shared/`
- **Date format** → YYYY-MM-DD (verify current date first!)

### Common Locations:
- **Launcher**: `local_optimization/batch_files/LAUNCH_OPTIMIZED_V4.bat`
- **Selenium guide**: `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`
- **FastPath docs**: `md_library/impactsearch/2025-09-16_IMPACTSEARCH_FASTPATH_OPTIMIZATION_IMPLEMENTATION.md`

### FORBIDDEN ACTIONS - NEVER DO THESE:
- **NEVER save test files to root** - Always use `test_scripts/`
- **NEVER save MD files to root** - Always use `md_library/`
- **NEVER use Unicode in console** - Always use ASCII
- **NEVER skip cache clearing for Selenium** - Always do full clear
- **NEVER modify spymaster.py's standalone nature** - It's the baseline
- **NEVER trust the system date** - Always verify with `date` command
- **NEVER leave temporary files** - Always clean up immediately
- **NEVER place files randomly** - Always follow structure

## MANDATORY DOCUMENTATION LOOKUPS

### Before ANY Task, You MUST Check These Resources:

#### For Selenium Testing:
**MUST READ FIRST**: `md_library/spymaster/2025-01-23_SELENIUM_TESTING_COMPREHENSIVE_GUIDE.md`
- Contains: Two-layer cache system details, nuclear clear procedure, all 7 ticker input locations
- Critical: Session cache vs disk cache distinction
- Element IDs: All 9 test coverage areas documented
- **DO NOT attempt Selenium testing without reading this first**

#### For ImpactSearch FastPath:
**MUST READ FIRST**: `md_library/impactsearch/2025-09-16_IMPACTSEARCH_FASTPATH_OPTIMIZATION_IMPLEMENTATION.md`
- Contains: Gate mismatch fix, module flag propagation solution
- Critical: Environment variable requirements
- Performance: Reduces API calls from 73,000+ to 1
- **DO NOT modify ImpactSearch without understanding fastpath**

#### For Performance/Threading:
**MUST READ FIRST**:
- `md_library/shared/2025-01-15_MKL_THREAD_OPTIMIZATION_BASELINE_TESTS.md`
- `md_library/shared/2025-01-15_COMPLETE_P8_OPTIMIZATION_ALL_SCRIPTS.md`
- Contains: P-core vs E-core detection, MKL threading configurations
- Critical: Intel 13th gen optimization settings

#### For NumPy Compatibility Issues:
**MUST READ FIRST**:
- `md_library/shared/2025-09-16_NUMPY_PICKLE_COMPATIBILITY_SHIMS_IMPLEMENTATION.md`
- `md_library/shared/2025-09-16_ROBUST_NUMPY_SHIMS_VERIFIED_WORKING.md`
- Contains: Cross-version pickle loading fixes
- Critical: numpy.core vs numpy._core aliasing

#### For Unicode/Console Issues:
**MUST READ FIRST**: `md_library/shared/2025-08-16_UNICODE_AND_SELENIUM_TEST_ISSUE_INVESTIGATIONS.md`
- Contains: cp1252 encoding details, ASCII replacement patterns
- Critical: Why Unicode fails in Windows console
- Solutions: Complete ASCII alternative mapping

#### For Signal Library Problems:
**MUST READ FIRST**: Any file matching these patterns:
- `md_library/shared/2025-08-20_REBUILD_FIX_*.md` - Rebuild reduction
- `md_library/shared/2025-08-20_SCALE_RECONCILE_*.md` - Scale fixes
- `md_library/shared/2025-08-21_T1_*.md` - T1 policy implementations
- Contains: Tolerance adjustments, date alignment fixes

#### For GTL (Global Ticker Library):
**MUST READ FIRST**:
- `md_library/global_ticker_library/2025-08-19_TICKER_RESOLUTION_FIX_INTERNATIONAL_SYMBOLS.md`
- `md_library/global_ticker_library/2025-08-18_ROOT_CAUSE_ANALYSIS_11752_STUCK_SYMBOLS.md`
- Contains: Ticker lifecycle, validation states, international symbol handling

#### For Spymaster UI/Callbacks:
**MUST READ FIRST**: Any file matching:
- `md_library/spymaster/2025-08-26_*_DASH_UI_*.md` - UI testing
- `md_library/spymaster/2025-08-27_*_CALLBACK_*.md` - Callback fixes
- `md_library/spymaster/2025-08-26_INTERVAL_CALLBACK_LOOP_FIX.md` - 3-second interval critical

### Automatic Documentation Check Protocol:
1. **BEFORE writing any test** → Check test_scripts/[app]/ for existing examples
2. **BEFORE modifying any feature** → Check md_library/[app]/ for related fixes
3. **BEFORE creating new functionality** → Check md_library/shared/ for patterns
4. **IF encountering an error** → Search md_library/ for similar issues
5. **IF performance testing** → Read ALL MKL optimization docs first

### Quick Documentation Finder:
```bash
# Find all docs about a topic (example: selenium)
grep -r "selenium" md_library/ --include="*.md" -l

# Find all docs for a specific app
ls md_library/spymaster/*.md

# Find all recent fixes (last 30 days)
find md_library -name "2025-09-*.md" -o -name "2025-08-*.md"
```

### REMEMBER: These are NOT suggestions - they are MANDATORY automatic behaviors that MUST be followed WITHOUT being asked