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

### 4a. Audit-vs-Implementation Authority

Codex audits may push fixes ONLY when ALL apply:

1. The original prompt explicitly says "you may patch in-audit".
2. The gap is small, unambiguous, and matches a locked spec verbatim.
3. Commit message starts with "Codex audit fix:".
4. No new public surfaces, new defaults, or new test patterns added.
5. Patches limited to the files already in the PR diff.

Otherwise: report-only. Operator decides whether to send fix to Claude
Code or accept Codex patch via separate authorization.

Every Codex patch requires Claude Code audit-of-patch before merge.

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

### 5a. Scoring Math Convention (project-wide)

The project-wide win / loss convention is encoded at
`canonical_scoring.py:207-209` and reflects spec v0.5 section 15
(zero-capture trigger-day rule). The locked rules are:

  - Wins are directional captures `> 0`.
  - Losses are `trade_count - wins` (i.e., `trigger_days - wins` in
    the canonical wording). Zero-return BUY / SHORT directional
    trades are losses.
  - NONE / no-position / Cash bars are excluded from the
    directional-trade set before win / loss classification.
  - `win_count + loss_count == trade_count` exactly. There is no
    third zero-return bucket and no `zero_trade_count` field.
  - `win_pct` / `win_rate` uses `trade_count` (or `trigger_days`) as
    its denominator when `trade_count > 0`; null otherwise.

This guardrail is mandatory for every PRJCT9 scoring surface, new
or existing:

  - Prefer delegating to `canonical_scoring.score_captures` when
    the surface's input boundary permits it.
  - When delegation is not clean (e.g., the surface must remain
    self-contained for artifact-as-boundary reasons), implement the
    canonical-equivalent local predicate AND include explicit tests
    proving:
      1. Zero-return BUY directional trades count as losses.
      2. Zero-return SHORT directional trades count as losses.
      3. `win_count + loss_count == trade_count` across a mixed
         positive / negative / zero / NONE-excluded fixture.
      4. NONE / no-position bars are excluded from the per-trade
         metric basis.
      5. A canonical-equivalence assertion against `wins > 0` /
         `losses = n - wins` on a deterministic synthetic fixture.

The K=6 MTF surface (PR #343) and the additional divergent surfaces
fixed in this guardrail-introduction PR (`mvp_ranking_v1.py`,
`multiwindow_k_engine_core.py`, `confluence_mtf_artifact_builder.py`,
`trafficflow_multitimeframe_bridge.py`, `research_artifacts.py`,
two `spymaster.py` completed-trade sites) all carry these tests.
Future scoring code MUST follow the same pattern.

Background: a Codex audit during PR #343 (K=6 MTF metric-basis
correction) found several sprint-created scoring surfaces had
locally reimplemented win / loss math using `losses = captures < 0`
(i.e., strict less-than zero), which silently dropped zero-return
directional captures into a third "neither" bucket. PR #343 fixed
K=6 MTF; the follow-up guardrail-introduction PR propagated the
predicate fix to the remaining divergent surfaces and added this
section so the convention cannot drift again without an explicit
contract amendment.

Out-of-scope for this guardrail: the `stackbuilder.py` legacy
`metrics_from_captures(captures)` single-arg fallback at L1007 still
uses `captures.ne(0.0)` as a stand-in mask when callers do not
supply an explicit `trigger_mask`. The docstring at L1012-1022
documents this as a known deviation pending a follow-up PR to plumb
real trigger masks through every caller. Until then, the fallback
silently drops zero-capture trigger days. Phase 1A baseline-lock
tests pin its existing behavior, so a fix requires an explicit
re-baseline authorization. Treat this as a known caveat, not a
license to copy the divergent predicate into new code.

### 5b. Test Suite Discipline (project-wide)

The default `test_scripts/` suite is the fast suite. It must
complete reliably without inspecting real operational state on
the developer machine. Two pytest markers govern selection:

  - `slow` is for integration or heavy-compute tests.
  - `production_smoke` is for tests that inspect real
    operational state under `output/`, `signal_library/`,
    `cache/`, or `price_cache/` (i.e., dev-machine state
    outside `tmp_path`).

The `pytest.ini` at the project root sets
`addopts = -m "not slow and not production_smoke"`, so the fast
default deselects both classes. A pytest-style command-line `-m`
expression overrides this addopts entry rather than appending to
it (verified in-session at PR introduction time, see Validation
section below for exact commands).

Rules for new tests:

  - Any new test that touches real operational state outside
    `tmp_path` must be marked `@pytest.mark.production_smoke` and
    should gate on an explicit environment opt-in (the
    introductory PR uses `PRJCT9_RUN_PRODUCTION_SMOKES=1` for the
    `output/impactsearch` reader).
  - Any new test that takes more than 30 seconds individually
    should be marked `@pytest.mark.slow` or redesigned.
  - Autouse fixtures must not recursively walk operational
    roots; either drop autouse and make the fixture opt-in, or
    monkeypatch the production-root provider to a `tmp_path`
    layout inside the fixture.
  - Production-root refusal tests should prefer a direct call to
    the narrow guard function (e.g.,
    `runner._path_is_inside_production_root`) rather than
    invoking the full runner entry point per parametrization
    round.

Verified commands (validated at PR introduction time against the
pinned `spyproject2` interpreter):

  - Fast default (skips `slow` + `production_smoke`):
    ```
    pytest test_scripts/
    ```
  - Opt-in slow / production-smoke subset only:
    ```
    pytest test_scripts/ -m "slow or production_smoke"
    ```
  - Full validation across every marker class (clears the
    `addopts` `-m` filter from `pytest.ini`):
    ```
    pytest test_scripts/ --override-ini="addopts="
    ```

Operational note: an opt-in `production_smoke` test that walks
real operational state on a populated developer machine can
take significantly longer than the fast suite. The introductory
PR's `output/impactsearch` reader exceeded a 300-second bounded
validation window in-session when opted in. This is expected
operational-state dependency, not a hang. Treat `production_smoke`
runtime as unbounded relative to the fast-default budget; the
fast default never selects these tests, so the operational-state
cost does not affect default CI / contributor workflow.

Background: a Codex audit at PR introduction time identified one
primary full-suite stall and three secondary timeout hazards in
which production state under `output/`, `signal_library/`,
`cache/`, or `price_cache/` was making the default suite hang
for hours. This section is the durable rule that prevents the
same drift; the introductory PR adds the marker infrastructure,
gates the four hazard tests, and documents the verified
commands.

### 6. Current Sprint State (post Phase E PR Epsilon)

**Phase 6I sprint closed** (historical context). Production StackBuilder outputs for the 8 PRJCT9 secondaries (AAPL, AMZN, GOOGL, META, MSFT, NVDA, SPY, TSLA) live under canonical `output/stackbuilder/` and each `selected_build.json` points to its production run directory. These are runtime artifacts, not staged in git.

**TrafficFlow headless development sprint substantially complete.** Phases delivered, in order:

- **Phase A** -- scoping doc and execution-surface contract for the headless runner.
- **Phase B** -- dry-run preflight scaffold (`trafficflow_runner.py`); reads `selected_build.json` per secondary, classifies input readiness, emits a single sanitized JSON envelope on stdout, never imports `trafficflow`.
- **Phase C** -- isolated-output write capability: `--write` to a non-canonical directory invokes the lazy compute path with a pinned engine surface and atomically writes `board_rows_k=<K>.{json,csv}` plus a Phase C run manifest. Canonical `output/trafficflow/` writes remain structurally refused at this phase.
- **Phase D** -- full-K performance characterization across the 8 secondaries; established the K=1..6 vs K=7..12 cost separation that anchors the heavy-stage gate.
- **Phase E** -- daily-cadence canonical-write contract:
  - Alpha CLI guardrails (`--canonical-write`, `--heavy-stage`, `PHASE_E_RUN_MANIFEST_SCHEMA`, fail-closed refusal logic).
  - Beta single-secondary canonical writer (per-secondary `board_rows_k=*.{json,csv}`, `secondary_manifest.json`, zero-byte `.done`; quarantine on failure; pre-loop eligibility validation; path-scrubbed `failure.json`).
  - Gamma orchestrator / finalizer (`trafficflow_canonical_orchestrator.py`): fans out single-secondary canonical-write worker subprocesses, owns run-level `progress.json` / `run_status.json` / `run_manifest.json` and the global `selected_output.json` pointer, supports `--resume` and `--allow-partial-publish`.
  - Delta first real canonical-write smoke (SPY + AAPL, K=1..6, `--workers 2`).
  - Epsilon broader real smoke for all 8 secondaries at K=1..6 with `--workers 4`.

**Current operational baseline.** TrafficFlow K=1..6 canonical writes are proven at full 8-secondary scale via the orchestrator. The downstream discovery pointer is `output/trafficflow/selected_output.json`; it references the most recent canonical run root under `output/trafficflow/runs/<UTC_TIMESTAMP>/`, which is the single source of truth for that run's `progress.json`, `run_status.json`, `run_manifest.json`, and per-secondary `board_rows_k=*.{json,csv}` + `secondary_manifest.json` + `.done`.

**Deferred to a future phase.** Heavy-stage K=7..12 is gated by `--heavy-stage` at both the runner and the orchestrator and is not yet exercised against real canonical writes. 250-500 secondary real runs remain inference-only.

**Next named sprint direction: MTF and Confluence integration with canonical TrafficFlow output**, in service of the 250-500 secondary launch universe. Downstream consumers should read the canonical contract via `output/trafficflow/selected_output.json` -> run root -> per-secondary board_rows; they should not import `trafficflow` directly.

**Authoritative source-of-truth.** Run `git log -10 --oneline main` for current head; sprint cursor lives in the auto-memory `sprint_state.md`. PR-level detail and per-PR amendment cycles are recorded in `md_library/shared/*PHASE_*` evidence docs and do not belong in this file.

---

#### Product North Star / Do Not Drift (Phase 6I-37)

The current SPY path is a **pilot/proof path**, not the final product. **SPY remains parked until source readiness flips.**

The final Confluence goal is a **multi-ticker, TrafficFlow-style ranking board over a large and growing ticker universe**, scoring and comparing many tickers using **all five canonical windows: 1d / 1wk / 1mo / 3mo / 1y**.

The per-ticker 60-cell payload (Phase 6I-20 contract: `per_window_k_metrics` + `build_wide_window_alignment` + `multiwindow_k_engine_payload_metadata`) is the **building block**, not the user-facing destination.

The key user question the website must answer every day:

> *"What tickers/builds are firing now across multiple windows, and what has historically happened when they fired?"*

The website must therefore surface, per ticker and per `(K, window)` cell:

1. **Which tickers/builds are firing now.**
2. **Which K builds are firing now.**
3. **Which canonical windows are firing** (1d / 1wk / 1mo / 3mo / 1y).
4. **How many build members agree with the current signal** (alignment ratio, all-members-aligned flag).
5. **Historical performance when that (K, window) condition fired** (total capture, avg daily capture, Sharpe, trigger days, wins/losses).
6. **Chart / freshness / blocker status** so stale or incomplete signals are obvious.

**Current signal state** comes from the latest combined signal + member counts (`latest_combined_signal` + `latest_buy_count` / `latest_short_count` / `latest_none_count` / `latest_missing_count` / `member_count` on each Phase 6I-23 cell). **Historical performance** comes from the per-window K cell metrics (`total_capture_pct`, `avg_daily_capture_pct`, `sharpe_ratio`, `trigger_days`, `wins`, `losses`).

**TrafficFlow parity gap (honest):** legacy TrafficFlow `compute_build_metrics_spymaster_parity` averages metrics across all non-empty subsets (`2^N - 1`) of active build members per build — its K is a *subset size* with subset-averaging. The Phase 6I-23 multi-window K engine emits one cell per `(K, window)` where K is a *combine threshold* (`n`-of-`N` agreement). The Phase 6I-37 `current_build_signals` matrix answers the user-facing current-signal + per-cell-historical-performance question, but it does **NOT** reproduce legacy TrafficFlow subset-average semantics. A future scoring / parity phase may close that gap; until then, the website surface honestly carries combine-threshold K cells, not subset-averaged K builds.

Stay aligned with the existing script family — **OnePass** / **ImpactSearch** / **StackBuilder** / **TrafficFlow** / **MultiTimeframe** / **Confluence**. Do not invent vague replacement language that hides which data came from which layer. When future phases name a new module, the name should make it obvious whether it consumes StackBuilder's K rows, the OnePass interval libraries, the TrafficFlow multi-timeframe surface, or the Confluence multi-ticker ranking surface.

**Do not drift.** A "single-ticker SPY pass works end-to-end" verdict is **not** a website launch. A payload-only data contract that hides current signal clarity is **not** a website launch. The website launch verdict is: *"the multi-ticker ranking board can score and rank every qualifying ticker in the universe on the canonical 1d / 1wk / 1mo / 3mo / 1y window grid, surface current signal state plus historical performance per `(K, window)` cell, AND surface honest freshness / blocker fields for the rest."*

---

#### Recent merged phase trail (post Phase 6I-79)

For the recent phase trail, run `git log --oneline main --grep="Phase 6I"`. Phase 6I-77 through Phase 6I-79 are described in the current-sprint-state block above; earlier Phase 6I phases are preserved in the historical sub-sections (sub-section 6.0, sub-section 6.1, and the 2026-05-10 / 2026-05-08 blocks) further down.

---

### 6.0. (Historical — superseded by 2026-05-14 / Phase 6I-33 sprint state above) Phase 6I-17 sprint state (as of 2026-05-13)

**(historical) main / origin/main HEAD:** `ec3658e` — `Phase 6I-17: SPY source-ready recheck (STATE C; writer NOT prepared) (#234)`.

**Sprint trajectory (Phase 6H + Phase 6I, top-to-bottom by phase number):**

The Phase 6G UX baseline (the Town Notice Board reskin + persist-skip-lag honest-recommendation contract) has shipped and is preserved verbatim in the demoted section below for future UI / UX review. Phase 6H added the **read-only-by-default planning + guarded-writer foundation** (most of the chain is read-only — watcher, preflight, dry-run executor, root plumbing, runbook — but the guarded writer module itself is write-capable, gated behind the two-key authorization `--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit`). Phase 6I added the **data/evidence and authorization layers** that screen any production writer invocation. **The current state is post-Phase 6I-17 — STATE C / WAIT.** Both the existing-cache predicate and the source-availability predicate observe equality (`cache_date_range_end == resolved current_as_of_date == new_cache_date_range_end == "2026-05-12"`); the supervised gate emits `wait_for_cache_ahead_of_cutoff`; `safe_to_authorize_writer_now=false`; `source_ready_for_supervised_refresh` did not fire (correctly, source is NOT ready); no writer script was prepared; production roots are `0/0/0` across all five roots; **no production writes are authorized**.

**Phase 6H — read-only-by-default planning + guarded-writer foundation (all merged):** every module below is read-only except `daily_board_automation_writer.py` (Phase 6H-5 + 6H-6), which is write-capable but **two-key gated** (`--write` CLI flag + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` env var; both required).

  - **Phase 6H-1** (PR #211) — Daily Signal Board launch / design handoff doc (`md_library/shared/2026-05-12_PHASE_6H_DAILY_SIGNAL_BOARD_LAUNCH_HANDOFF.md`).
  - **Phase 6H-2** (PR #212) — `cache_cutoff_watcher.py`: read-only cache-vs-cutoff watcher; strict-inequality predicate (`cache_date_range_end > current_as_of_date`) drives the `pipeline_output_lags_persist_skip` action used downstream by the launch audit, the freshness preflight, the supervised gate, and the writer's post-refresh recheck.
  - **Phase 6H-3** (PR #213) — `daily_board_automation_preflight.py` read-only preflight planner (`md_library/shared/2026-05-12_PHASE_6H3_DAILY_BOARD_AUTOMATION_PREFLIGHT.md`).
  - **Phase 6H-4** (PR #214) — `daily_board_automation_executor.py` dry-run executor (`md_library/shared/2026-05-12_PHASE_6H4_DAILY_BOARD_AUTOMATION_DRY_RUN_EXECUTOR.md`).
  - **Phase 6H-5** (PR #215) — `daily_board_automation_writer.py` two-key guarded writer foundation: `--write` CLI flag + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` env var both required (`md_library/shared/2026-05-12_PHASE_6H5_GUARDED_WRITE_EXECUTOR_FOUNDATION.md`).
  - **Phase 6H-6** (PR #216) — writer root plumbing: `--cache-dir`, `--status-dir`, `--artifact-root`, `--stackbuilder-root`, `--signal-library-dir`, `--execution-log` (`md_library/shared/2026-05-12_PHASE_6H6_LIVE_WRITER_ROOT_PLUMBING.md`).
  - **Phase 6H-7** (PR #217) — production runbook + operator command manifest (`md_library/shared/2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md`).

**Phase 6I — data/evidence and authorization layers (all merged):**

  - **Phase 6I-1** (PR #218) — `confluence_ranking_contract_validator.py`: seven per-ticker contract booleans (cache, stackbuilder, daily_k, mtf, confluence, readiness, board_row) + leader-eligibility + ranking-blocked-reason verdict.
  - **Phase 6I-2** (PR #219) — manual workflow → Confluence pipeline migration map: documents what the legacy TrafficFlow / Spymaster manual workflow used to do and how the Phase 6H + 6I automation chain replaces each step. Explicitly clarifies that **`agreement_ratio` is NOT a Sharpe-ratio successor** — it is a Group-A signal-breadth metric (active alignment checks ÷ available alignment checks) that lives alongside, not in place of, the Group-B performance-quality metrics carried by Phase 6I-3.
  - **Phase 6I-3** (PR #220) — `confluence_ranking_emitter.py`: cross-ticker ranking emission with Group A signal-breadth + Group B performance-quality + three tails (positive / negative / low_buy).
  - **Phase 6I-4** (PR #221) — `upstream_research_input_audit.py`: upstream trio audit (OnePass / ImpactSearch / StackBuilder) with 12 stable issue codes + 11 primary-blocker strings + three predictive flags (`can_build_daily_trafficflow_k` / `can_project_multitimeframe` / `can_build_confluence`).
  - **Phase 6I-5** (PR #222) — `daily_board_universe_planner.py`: discovers the StackBuilder universe (248 tickers as of merge) and joins per-ticker upstream/preflight/ranking state with three predictive handoff flags.
  - **Phase 6I-6** (PR #223) — `daily_board_execution_queue_planner.py`: action-first 7-queue classification (`pipeline_only_queue` / `refresh_source_cache_then_pipeline_queue` / `wait_for_cache_ahead_queue` / `manual_stackbuilder_queue` / `upstream_blocked_queue` / `downstream_gap_queue` / `current_leader_eligible_queue`) with advisory writer commands on the two write-ready queues.
  - **Phase 6I-7** (PR #224) — `spymaster_master_audit.py`: Spymaster collapsed-by-default master-audit panel consuming the Phase 6I-6 planner read-only. No write button. No daily_board_automation_writer import.
  - **Phase 6I-8** (PR #225) — writer post-pipeline contract validation: the Phase 6I-1 validator is invoked read-only AFTER every authorized pipeline write that returns normally; `ContractValidationOutcome` carries the seven booleans + the `CONTRACT_VALIDATOR_FUNCTION_MARKER` in `functions_executed`.
  - **Phase 6I-9** (PR #226) — `daily_board_supervised_run_gate.py`: pre-decision **SCREEN** ("Is it safe to authorize the guarded writer right now, for which tickers, and why or why not?"). Seven `ACTION_*` constants + seven `BLOCKING_*` reason constants. Action-first decision cascade. **The writer's two-key gate is unchanged**; this screen sits in front of it.
  - **Phase 6I-10** (PR #227) — `daily_board_flow_integrity_audit.py`: read-only end-to-end flow audit walking 6 named stages (`STAGE_UPSTREAM` / `STAGE_CONTRACT` / `STAGE_RANKING` / `STAGE_QUEUE_AND_GATE` / `STAGE_WRITER_STATIC` / `STAGE_SPYMASTER_HELPER`). Production-root snapshot (`relative_path_size_mtime` strategy) before/after the audit. Composite verdict `safe_to_consider_authorized_run_after_review` is True iff every stage passes AND gate is safe AND production roots stayed untouched. Five `known_simulated_or_inferred_steps` named: `real_authorized_writer_run`, `real_signal_engine_cache_refresher_invocation`, `real_confluence_pipeline_runner_write`, `real_yfinance_fetch`, `real_post_pipeline_validation_on_writer_path`.
  - **Phase 6I-11** (PR #228) — **first supervised authorized SPY writer run.** Writer invoked exactly once from `project/` via a one-shot temp launcher script (deleted before commit) with the pinned `spyproject2` interpreter + the two-key authorization. **The refresher callable ran** (in-process Python function `signal_engine_cache_refresher.refresh_signal_engine_cache` recorded in `functions_executed`; the writer has **no subprocess path**) and advanced cache `date_range_end` from `2026-05-11` to `2026-05-12`. **The pipeline was withheld** by the persist-skip-lag honest contract because, after the refresh, `cache_date_range_end == current_as_of_date == 2026-05-12` is not strictly greater than cutoff. `final_recommended_action="refresh_executed_pipeline_withheld"`. Surgical inventory diff: 3 files changed in `cache/results/` + `cache/status/`; 0 changes across `output/research_artifacts/`, `signal_library/data/stable/`, `output/stackbuilder/`. Closed two of five evidence gaps (`real_authorized_writer_run`, `real_signal_engine_cache_refresher_invocation`); three remained open.
  - **Phase 6I-12** (PR #229 / squash `5dfd054`) — additive code-backed evidence improvements. **Scope A** added a four-case selector to the flow-audit `recommended_next_evidence_step` so it distinguishes stage-failure / roots-touched / gate-not-safe-with-all-stages-pass / supervised-run-ready. **Scope B** added `ProviderFetchTelemetry` (provider_name / fetch_attempted / fetch_succeeded / ticker / rows / date_range_start / date_range_end / elapsed_seconds / error) to the refresher's result surface, persisted it onto the refresher's per-ticker status JSON for write runs (Codex amendment), and threaded the same JSON shape through the writer's `RefreshOutcome` JSON serializer to stdout + JSONL execution log. **Fetch-attempt/result telemetry, NOT HTTP-level telemetry.** At Phase 6I-12 close the telemetry was unfired; Phase 6I-16 then captured `provider_fetch_telemetry` on the source-availability probe's `SourceAvailabilityState` output surface (re-captured by Phase 6I-17). The writer-side surfaces (writer stdout / JSONL row / per-ticker status JSON) remain unfired until a future supervised writer run actually invokes a refresh.
  - **Phase 6I-13** (PR #230 / squash `303e826`) — **supervised-run evidence attempt; writer NOT run.** Five required pre-run read-only probes captured `2026-05-13T06:56Z UTC` against main `5dfd054`. The resolver returned `current_as_of_date=2026-05-12` for those probes. Gate verdict: `safe_to_authorize_writer_now=false`, `recommended_operator_action="wait_for_cache_ahead_of_cutoff"`, SPY in `wait_for_cache_ahead_tickers` and absent from `authorization_candidate_tickers`. Writer dry-run: `initial_recommended_action="wait_for_cache_ahead_of_cutoff"` (neither `run_pipeline_only` nor `refresh_source_cache_then_pipeline`). Three of seven spec preconditions failed (gate-safe / SPY-in-candidates / writer-action-actionable), all on the same root cause: `cache_date_range_end == current_as_of_date == 2026-05-12`. Per spec, **no temp launcher script, no `PRJCT9_AUTOMATION_WRITE_AUTH` env var set, no writer `--write`**. The Phase 6I-12 Scope A wording fix is verified live in the flow audit's case-3 text. Three real-evidence gaps remain open (see § 6.1 below).

**Five-precondition checklist before any future writer authorization** (from Phase 6I-13 spec; an attempted run halts and writes a docs-only branch if ANY fails):

  1. `gate.safe_to_authorize_writer_now == true`.
  2. `gate.authorization_candidate_tickers` contains the target ticker.
  3. Writer dry-run `initial_recommended_action` is `run_pipeline_only` OR `refresh_source_cache_then_pipeline` (neither `wait_for_cache_ahead_of_cutoff` nor any manual/blocker action).
  4. Flow audit: all 6 stages pass AND `production_roots_untouched == true`.
  5. Contract validator: all 7 contract booleans `true`; no manual/blocker recommended action.

**The one operational condition that opens the gate (the hard predicate):** `cache_date_range_end > resolved current_as_of_date` strictly. Wall-clock advance alone does not open the gate; a fresh refresh must land a trading day strictly past the cutoff in the source cache. **The predicate is the contract — re-run the read-only probes; do not infer readiness from any wall-clock event.**

**Two distinct predicates, two distinct probes:**

  - **Existing-cache predicate** (what the five standard probes inspect): `current cache_date_range_end > resolved current_as_of_date`. Reported by `cache_cutoff_watcher.py` (`cache_ahead_of_cutoff` boolean) and consumed by the supervised gate. **When this predicate is false because of equality** (`cache_equal_to_cutoff=true`, current state post-Phase-6I-17), the gate emits `wait_for_cache_ahead_of_cutoff`. The five existing probes by themselves **do not prove that a newly fetchable trading day is available** — they only inspect existing on-disk cache / gate / validator state.
  - **Source-availability predicate** (what a separate, explicit dry-run probe inspects): `new_cache_date_range_end > resolved current_as_of_date` where `new_cache_date_range_end` is the date that a no-write refresh attempt **would** land on the cache if authorized. Reported by `signal_engine_cache_refresher.py --ticker SPY --dry-run` (`new_cache_date_range_end` field on `SignalEngineRefreshResult.to_json_dict()`) or by `source_freshness_preflight.py --ticker SPY` (read-only mode). Use this probe to check whether a future authorized refresh **would** flip the existing-cache predicate from `equal` to `strictly-greater`.

**Conservative operator discipline for the post-Phase-6I-17 equal-cache state (framing established by the Phase 6I-14 next-run handoff and re-verified by the Phase 6I-15 → 6I-17 sequence):**

  1. **If the five standard probes already show `gate.safe_to_authorize_writer_now=true` and SPY is in `authorization_candidate_tickers`**, proceed to normal supervised authorization review (the Phase 6I-11 supervised-run pattern).
  2. **If `cache_cutoff_watcher` shows `cache_equal_to_cutoff=true` and the gate emits `wait_for_cache_ahead_of_cutoff`**, **do NOT** authorize the writer merely because time has passed. The equal-cache verdict will not change without an explicit refresh; assuming "next market close fixed it" is exactly the failure mode the predicate-first discipline is meant to prevent.
  3. In the equal-cache state, first run the read-only source-availability probe (`signal_engine_cache_refresher.py --ticker SPY --dry-run` or `source_freshness_preflight.py --ticker SPY`).
  4. **If the source-availability probe does not show `new_cache_date_range_end > resolved current_as_of_date`**, halt — there is no productive refresh available yet; record the probe output and wait.
  5. **If the source-availability probe DOES show `new_cache_date_range_end > resolved current_as_of_date`**, record that evidence and then either:
     a. use an already-existing documented supervised path that consumes that predicate (e.g. the Phase 6I-11 pattern with a fresh round of the five standard probes after refresh), **OR**
     b. stop and open a follow-up implementation PR to wire that predicate into the supervised gate / authorization flow.

     **Do NOT invent an undocumented writer authorization path** based on the source-availability probe alone. The two-key writer gate (Phase 6H-5) and the supervised-run gate (Phase 6I-9) are the only currently-merged authorization surfaces.

**Test baseline:** full regression **1,550 passed in 343.68 s, 60 pre-existing pandas fragmentation warnings** (Phase 6I-12 baseline; the Phase 6I-13 through Phase 6I-18 docs-only / evidence phases did not move the baseline).

**No production writes are currently authorized.** **Next operational action: WAIT.** Re-run the same **8-probe suite** established in Phase 6I-16 / 6I-17 (`cache_cutoff_watcher` / `source_availability_probe` / `daily_board_supervised_run_gate` × 2 modes / `daily_board_flow_integrity_audit` × 2 modes / `daily_board_automation_writer` `--dry-run` / `confluence_ranking_contract_validator`) at a later point. **Do not authorize a writer run merely because time has passed.** Trust the observed predicate; the gate moves only when the probes report `new_cache_date_range_end > resolved current_as_of_date` strictly. See § "Remaining real-evidence gaps after Phase 6I-17" below for the live gap list.

**Confirm before assuming current state:** run `git log -10 --oneline main`. This block may lag reality if a Phase 6I-19 or later PR landed without a refresh.

**Current next-probe handoff doc:** `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md` (post-Phase-6I-17 closed-state snapshot, the exact future trigger that would justify preparing a reviewed writer script, remaining gaps). **Historical predicate-foundation handoff:** `project/md_library/shared/2026-05-13_PHASE_6I14_SPRINT_STATE_AND_NEXT_RUN_HANDOFF.md` (the predicate-first discipline + five-precondition checklist + existing-cache vs source-availability distinction were established here; preserved as historical context, not the current next-run handoff).

**Sprint progression Phase 6I-15 → Phase 6I-17 (all merged):**

  - **Phase 6I-15** (PR #232, squash `756fb5f`) — read-only source-availability gate integration. New module `source_availability_probe.py` exposes `evaluate_source_availability(...)` / `evaluate_source_availability_many(...)` + CLI; calls the Phase 6E-5 refresher with `write=False` through an injectable callable. New advisory action `ACTION_SOURCE_READY_FOR_SUPERVISED_REFRESH` added to `daily_board_supervised_run_gate.ALL_ACTIONS`; emitted only when the gate would otherwise produce `wait_for_cache_ahead_of_cutoff` AND a no-write refresh dry-run shows `new_cache_date_range_end > resolved current_as_of_date` strictly. **The advisory action NEVER flips `safe_to_authorize_writer_now` to `true`** — it is pre-decision evidence telling the operator that running a refresh would be productive, not authorization to write. Default `include_source_availability=False` on **both** the supervised gate and the flow integrity audit; the gate CLI and the flow-audit CLI both opt in via `--include-source-availability`. In opt-in mode, the flow audit remains **no-write** but may invoke `source_availability_probe` → `signal_engine_cache_refresher` with `write=False` and therefore may trigger a **read-only provider fetch** through the refresher's default yfinance-backed callable. Flow audit `recommended_next_evidence_step` extended to 5 cases with new case 3b: "A supervised refresh CAN BE PREPARED" when source-ready and gate emits the new advisory action. Doc: `project/md_library/shared/2026-05-13_PHASE_6I15_SOURCE_AVAILABILITY_GATE_INTEGRATION.md`.
  - **Phase 6I-16** (PR #233, squash `ae8095d`) — docs-only SPY source-availability evidence probe. Ran the 8-probe suite from `project/` against main `756fb5f` with the pinned `spyproject2` interpreter; verdict STATE 3 (`source_equal_cutoff_wait`); production roots untouched 0/0/0 across all 5 roots; **first sprint capture of live `yfinance` provider telemetry** through the Phase 6I-12 instrumentation surface (`provider_fetch_telemetry` captured on the source-availability probe's `SourceAvailabilityState` output only — 8,378 SPY rows, `1993-01-29` → `2026-05-12`, elapsed 2.516 s). Doc: `project/md_library/shared/2026-05-13_PHASE_6I16_SPY_SOURCE_AVAILABILITY_EVIDENCE.md`.
  - **Phase 6I-17** (PR #234, squash `ec3658e`) — docs-only SPY source-ready recheck. Re-ran the same 8-probe suite against main `ae8095d`; verdict **STATE C** (per Phase 6I-17 spec's 4-state list: cache not ahead AND source not ahead; continue waiting); both predicates remain equal (`cache_date_range_end == resolved current_as_of_date == new_cache_date_range_end == "2026-05-12"`); gate WITH `--include-source-availability` did NOT upgrade to `source_ready_for_supervised_refresh`; flow audit case-3b did NOT fire; production roots untouched 0/0/0 across all 5 roots; `provider_fetch_telemetry` re-captured (identical payload to Phase 6I-16; faster elapsed 0.843 s consistent with yfinance client-side caching). **No writer script prepared** (per State-C branch of the Phase 6I-17 spec). Doc: `project/md_library/shared/2026-05-13_PHASE_6I17_SPY_SOURCE_READY_RECHECK.md`. **Next operational action: WAIT** — re-run the same 8-probe suite at a later point. The gate moves only when the probes observe `new_cache_date_range_end > resolved current_as_of_date` strictly; the predicate is the contract; wall-clock events are at most context, never an authorization signal in their own right.

**Phase 6I-18 next-probe handoff doc:** `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md` (post-Phase-6I-17 docs-only refresh; explicit closed-state snapshot + future trigger + remaining evidence gaps).

**Remaining real-evidence gaps after Phase 6I-17:**

  - `real_confluence_pipeline_runner_write` — still open. Closes on a future supervised run where `cache_date_range_end > resolved current_as_of_date` strictly.
  - `real_post_pipeline_validation_on_writer_path` — still open. Same future condition.
  - **Provider telemetry on the source-availability probe surface** (`SourceAvailabilityState.provider_fetch_telemetry`) — **captured in Phase 6I-16 and re-captured in Phase 6I-17.**
  - **Provider telemetry on writer stdout / JSONL row / per-ticker status JSON surfaces** — **still pending.** Awaits a future supervised writer run that actually invokes a refresh.

**Phase 6E-2 preflight doc:** `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md` (§ 6.8 details the persist-skip-lag `pipeline_output_lags_persist_skip` action).

**Phase 6G baseline doc:** `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md` (§ 7 details the persist-skip-lag contract from the UX side).

**Testing root:** `C:\Users\sport\Documents\PythonProjects\spy-project` (the primary repo on `main`). The stale emdash worktree at `C:\Users\sport\emdash\worktrees\spy-project\emdash\sprint-continued-qlpvw` is many phases behind and is NOT a valid test target.

**PRJCT9 north star:** "PRJCT9 is a pattern-discovery engine. The MVP front door is the Daily Signal Board — a public read-only leaderboard of saved-research alignment by ticker."

---

### 6.1. (Historical — superseded by 2026-05-13 / Phase 6I-17 sprint state above) Daily Signal Board visual baseline (as of 2026-05-12, Phase 6G-5)

The Phase 6G-5 / Town Notice Board section below is preserved verbatim because the Daily Signal Board's seven-section hierarchy, public-meaning framing, and SPY pilot pinned-cutoff recipe are still load-bearing for visual / UX review work. Production-automation state has since moved through Phase 6H + Phase 6I and is documented in § 6 above; **for current automation state, follow § 6, not this section.**

**main / origin/main HEAD (when this section was current):** `576b676` — `Phase 6G-5: SPY currentness gap audit + persist-skip-lag honest recommendation (#210)`.

**Two baselines, separated:**

  - **Semantic / public-meaning baseline:** anchored at `24990f0` (Phase 6G-1). This is when the seven-section hierarchy and "Consensus / No consensus / Saved Research Archive" public framing locked in. The framing has been stable since.
  - **Visual review baseline at the time of this section:** `576b676` — `Phase 6G-5: SPY currentness gap audit + persist-skip-lag honest recommendation (#210)`. (This was main HEAD when the Phase 6G-5 sprint state was authored. Current main has since moved through Phase 6H + Phase 6I-1..13 and is named in § 6 above; for live state always trust § 6.) Phase 6G-4 (#209) reskinned the board to the Town Notice Board direction (warm-dark page, paper section cards, sage primary, neon `#80ff00` reserved for the current-leader accent + CSS-drawn pin/chip + Evidence Trail stamp glyphs). Phase 6G-5 (#210) layered the persist-skip-lag honest recommendation onto the launch audit + freshness preflight. Phase 6G-3 (#208) was docs-only.

**How to review the historical Phase 6G pinned-cutoff UI baseline:** check out the historical snapshot `576b676` (Phase 6G-5) and boot with `PRJCT9_RESEARCH_AS_OF_DATE=2026-05-08` pinned (Windows + spyproject2 interpreter; recipe in `2026-05-12_PHASE_6H_DAILY_SIGNAL_BOARD_LAUNCH_HANDOFF.md` § 2). The pinned env reproduces SPY as rank-1 leader-eligible against the on-disk artifacts from the Phase 6G-5 era; the Phase 6G-5 code paths render the Town Notice Board polish. (For current live state, follow § 6 above — not this historical recipe.)

**Screenshot references:**

  - Phase 6G-2 screenshots (`C:\Users\sport\AppData\Local\Temp\phase_6g_2_audit\`) are **pre-polish** — captured before #209 — and are NOT the Phase 6G-5 visual target.
  - Phase 6G-4 screenshots (`C:\Users\sport\AppData\Local\Temp\phase_6g_4_audit\`) are the closest existing screenshot reference for the Phase 6G-5 Town Notice Board direction; **the Phase 6G-5 snapshot at `576b676` is the source for this historical visual baseline; current live state is § 6 above.** Refresh screenshots from a Phase-6G-5-pinned-cutoff boot of `576b676` if a static reference for the historical visual baseline is needed.

**No production data writes are currently authorized.**

**Testing root:** `C:\Users\sport\Documents\PythonProjects\spy-project` (the primary repo on `main`). The stale emdash worktree at `C:\Users\sport\emdash\worktrees\spy-project\emdash\sprint-continued-qlpvw` is many phases behind and is NOT a valid test target.

**PRJCT9 north star:** "PRJCT9 is a pattern-discovery engine. The MVP front door is the Daily Signal Board — a public read-only leaderboard of saved-research alignment by ticker."

**Daily Signal Board front-door sections (top-to-bottom):**

  1. **Today's Board Status** (`section-current-pilot`) — the hero card for the current full-pipeline pilot ticker. Non-directional copy when consensus is None. Carries the Phase 6G-4 CSS-drawn pin + `current-pilot-chip` (leader-highlight neon, the only place the legacy `#80ff00` survives on the page along with the leader row's 3px borderLeft).
  2. **Town Hall Scoreboard** (`section-scoreboard`) — only leader-eligible rows. Column header reads "Consensus" (was "Signal"). Visible cell for `signal=None` renders "No consensus"; the `data-signal` attribute keeps the canonical `"None"`/`"Buy"`/`"Short"` value. Coverage cells render as wax-seal pills (Phase 6G-4).
  3. **Saved Research Archive** (`section-archive` / `section-archive-details`) — `<details>` collapsible (open=false), holds the long alphabetical tail of Partial/Stale rows (currently ~1,628). Disclosure copy "Open the saved-research drawer ({count} tickers)" (Phase 6G-4).
  4. **Featured High Score** (`section-featured`) — Signal Engine chart + headline numbers, plus a one-line two-signal explainer (`featured-two-signal-explainer`). Featured `confluence_status_fmt` reads `"{active} of {total} alignment checks active"` (60 = 12 K × 5 timeframes, not 60 timeframes). "Today's pilot" prefix above the demoted ticker glyph (Phase 6G-4).
  5. **Evidence Trail** (`section-evidence-trail`) — seven station cards: Seed Field / Trading Post / Workshop / Rail Yard / Calendar House / Town Hall / Watchtower. Each card carries a two-letter stamp glyph (SF/TP/WK/RY/CH/TH/WT) tinted by presence state (Phase 6G-4). Prefixed by `evidence-trail-intro` explaining that stale upstream stations are historical reference and don't block the current leader gate unless flagged.
  6. **What PRJCT9 Is** (`section-what-prjct9-is`).
  7. **What It Is Not** (`section-what-it-is-not`).

**SPY pilot state — historical Phase 6G pinned cutoff (visual baseline at `576b676` with `PRJCT9_RESEARCH_AS_OF_DATE=2026-05-08`)** — the values below are **Phase 6G-5-era snapshot values**, not current live state; for current live state, follow § 6 above:

  - **(Phase 6G-5-era)** Signal Engine cache `date_range.end` = `2026-05-11` (post Phase 6F-2 authorized refresh).
  - **(Phase 6G-5-era)** Confluence MTF consensus `last_date` = `2026-05-08` (post Phase 6F-5 authorized pipeline write).
  - **(Phase 6G-5-era)** Resolved `current_as_of_date` = `2026-05-08`.
  - **(Phase 6G-5-era)** Readiness: `leader_eligible=True`, `issue_codes=()`, `data-rank="1"`, `data-leader-eligible="true"`, `data-ranking-blocked-reason=""`, `data-signal="None"`.
  - **(Phase 6G-5-era)** Board consensus: **No directional consensus** (7 of 60 alignment checks active).
  - **(Phase 6G-5-era)** Signal Engine state: **Short 11,5**.
  - **(Phase 6G-5-era)** Visible scoreboard row: `SPY / No consensus / 7/60 / Full / 2026-05-08`.

**SPY pilot state — historical Phase 6G unpinned production boot (behavior as of `576b676`, by design at that time)** — the values below are **Phase 6G-5-era snapshot values**, not current live state; for current live state, follow § 6 above:

  - **(Phase 6G-5-era)** Cache `last_date` `2026-05-11`; pipeline tree (daily K / MTF K / Confluence) trimmed to `2026-05-08` by Phase 6D-1 `persist_skip_bars=1` safety.
  - **(Phase 6G-5-era)** Resolved `current_as_of_date` = `2026-05-11` (UTC has advanced past the trading day the pipeline tree was written for at the time of the Phase 6G-5 snapshot).
  - **(Phase 6G-5-era)** Readiness: `leader_eligible=False`, `ranking_blocked_reason="stale_confluence_day_artifact"`. SPY demotes to the Saved Research Archive on a bare boot.
  - **(Phase 6G-5-era)** Launch audit + freshness preflight: `recommended_action = recommended_next_action = "pipeline_output_lags_persist_skip"`; `safe_to_attempt_refresh=False`; `safe_to_run_pipeline_after_refresh=False`; pilot manifest excludes SPY. **This was the honest behavior of the existing contract at that time, not a regression.**
  - **(Phase 6G-5-era)** The gap closes when the source cache acquires a trading day **strictly after** `current_as_of_date` (cache-vs-cutoff strict inequality, not a wall-clock event). Until that happened in the Phase 6G-5 timeline, no operator action would move the verdict; pinning `PRJCT9_RESEARCH_AS_OF_DATE=2026-05-08` against the `576b676` snapshot was the only way to reproduce SPY as rank-1 against the on-disk artifacts of that era. (Phase 6I-11 subsequently authorized a refresh that advanced the cache to `2026-05-12`; see § 6 above for the post-Phase-6I-17 state.)

**Known limitations (Phase 6G-5-era):**

  - **(Phase 6G-5-era)** Only SPY was production-pilot-current at all (pinned). Every other ticker in the discovered universe was `coverage=Partial / signal=None` (saved-research-only).
  - **(Phase 6G-5-era)** Broader-universe refresh + pipeline automation was unbuilt; the single-ticker tooling existed (`signal_engine_cache_refresher.py`, `confluence_pipeline_runner.py`) but there was no scheduler / orchestrator.
  - **(Phase 6G-5-era)** ImpactSearch / StackBuilder day artifacts could remain legacy / stale. They are dated `research_day` evidence stations and may render stale-or-fresh; under the Phase 6C-8 leader gate that was in effect, their staleness did not block the Confluence leader verdict. (The StackBuilder *leaderboard directory* is presence-only; the day artifact is not.)
  - **(Phase 6G-5-era)** Mobile (≤ ~390 px wide) scoreboard table used contained internal horizontal scroll inside `scoreboard-table-wrapper` (Phase 6F-7). The page itself never grew horizontal scroll.

**Next recommended work — as recorded at the Phase 6G-5 snapshot** (for current live state and next-step guidance, follow § 6 above; this list is preserved as historical context only):

  - For historical pinned-cutoff visual review, the Phase 6G-5 snapshot at `576b676` booted with `PRJCT9_RESEARCH_AS_OF_DATE=2026-05-08` reproduces the on-disk SPY rank-1 leader-eligible state under the Town Notice Board polish. Phase 6G-4 screenshots (`C:\Users\sport\AppData\Local\Temp\phase_6g_4_audit\`) are the closest existing screenshot reference if a static artifact is needed, and Phase 6G-2 screenshots are pre-polish. See `2026-05-12_PHASE_6H_DAILY_SIGNAL_BOARD_LAUNCH_HANDOFF.md` for the full operator handoff.
  - **(Phase 6G-5-era)** Optional public-copy polish iteration (all visible strings still route through `BOARD_COPY`; the centralization test catches them).
  - **(Phase 6G-5-era)** Universe-automation scoping was out of band of the MVP polish track at that time; Phase 5D-2 / 5D-3 territory.

**Phase 6G-5-era test baseline:** full regression **1,213 passed, 60 pre-existing pandas fragmentation warnings**. No new warnings since Phase 6C-5. (Subsequent phases have moved the baseline; see § 6 above for the latest figure.)

**Phase 6G baseline doc:** `project/md_library/shared/2026-05-11_PHASE_6G_DAILY_SIGNAL_BOARD_BASELINE.md` (§ 7 details the persist-skip-lag contract).

**Phase 6E-2 preflight doc:** `project/md_library/shared/2026-05-11_PHASE_6E2_SOURCE_FRESHNESS_PREFLIGHT.md` (§ 6.8 details the new `pipeline_output_lags_persist_skip` action).

**Phase 6H-1 launch / design handoff doc:** `project/md_library/shared/2026-05-12_PHASE_6H_DAILY_SIGNAL_BOARD_LAUNCH_HANDOFF.md`.

---

### 6. (Historical — superseded by 2026-05-11 / Phase 6G-1 baseline above) Sprint state (as of 2026-05-10)

**main / origin/main HEAD:** `d4ee3a8` — `Phase 6C-5: Primary Signal Engine MVP reset (#191)`.

**Current phase:** Phase 6C-5 is CLOSED / MERGED. Phases 4 / 5C / 5D-1 / 5G / 5G-2 / 6A / 6B-1..4 / 6C-1..5 have all landed since the 2026-05-08 snapshot captured below.

**Testing root for 6C-5 work:** `C:\Users\sport\Documents\PythonProjects\spy-project` (the primary repo on `main`).

**Stale emdash worktree warning:** the worktree at
`C:\Users\sport\emdash\worktrees\spy-project\emdash\sprint-continued-qlpvw`
is pinned to `8081f73` (Phase 3 close, PR #144 from 2026-05-03) and is **47 commits behind `origin/main`**. It is NOT valid for Phase 6C-5 testing unless explicitly synced.

**PRJCT9 north star:**
"PRJCT9 is a pattern-discovery engine. The MVP front door is: Pick a ticker. See PRJCT9's saved Signal Engine history and what it says now."

**Practical current app behavior (Phase 6C-5):**

  - The local research preview opens directly on the cache-first Primary Signal Engine view (chart + Current Signal + Active SMA Pair + metric strip + last 15 history rows), read from `project/cache/results/<TICKER>_precomputed_results.pkl`. No yfinance, no OnePass / ImpactSearch / StackBuilder / Confluence / TrafficFlow on boot or on "View ticker" click.
  - The cross-ticker cockpit — catalogue browser, catalogue health, performance row, per-ticker dashboard, StackBuilder, Confluence, Traffic Flow — lives behind a collapsed-by-default `advanced-research-catalogue-details` Advanced section.
  - `btn-view-signal-engine` (first-screen primary button) is wired ONLY to `signal-engine-store`. It is fully isolated from `btn-load`, which now lives inside Advanced as "Load cross-ticker study" and is the only path that triggers `_on_action`.
  - `boot-trigger` no longer auto-loads the legacy cockpit — `_on_action`'s boot branch returns `(no_update, no_update, no_update)`. Initial page boot is Signal-Engine-only.
  - `catalogue-snapshot-store` and `catalogue-health-store` require explicit Refresh clicks (`prevent_initial_call=True`). The heavy `_real_confluence_snapshot_for_target` only runs after the user explicitly loads cross-ticker from Advanced.

**Phase 6C-5 test baseline:** full regression **954 passed, 0 failed, 60 pre-existing pandas fragmentation warnings**. 21 new in `test_primary_signal_engine.py`; +26 in `test_phase6_research_preview.py`.

---

### 6. (Historical — superseded by Current Sprint State as of 2026-05-10 above) Sprint state (as of 2026-05-08)

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

Sprint-relevant carry-forward items (CLAUDE.md sprint-state
drift, TrafficFlow refresh callback scope, defaults-diff
audit, monthly StackBuilder rebuild cadence) and Phase 7+
research items (B11 `compute_signals` cleanup,
`environment.yml` / `requirements.txt` hygiene, OnePass error
UX, ImpactSearch error taxonomy, StackBuilder progress JSON,
TickerDash global single-job model, pre-computed closing-price
threshold caching, daily TrafficFlow / MTF / Confluence
scheduling, real-time data feed selection, cloud compute
architecture for ticker expansion, Spymaster build history UI,
universe-wide beam K-search research) are tracked in the
durable cross-session tracking docs referenced in sub-section 8 below.

The following entry is preserved here as a residual
code-reference note that is not currently captured in either
tracking doc:

  - **QC clone Adj Close sites:** at
    `project/QC/Clone of Project 9/main.py:103, 918, 1509`.
    QC clone is a frozen historical snapshot, intentionally
    excluded from the Entry 1 (Adj Close removal) sweep.
    Revisit only on explicit scope expansion.

### 8. Tracking Documents

Sprint-relevant carry-forward items are tracked in:

    md_library/shared/2026-05-23_POST_PHASE_6I_SPRINT_CARRYFORWARD.md

Phase 7+ research and parking-lot items are tracked in:

    md_library/shared/2026-05-23_PHASE_7_PLUS_UNIVERSE_WIDE_BEAM_SCOPING.md

These docs are the durable source-of-truth for work that spans
sessions. New items discovered between sessions should be
appended to the appropriate tracking doc rather than left in
conversation memory. Each tracking-doc entry carries Status
(OPEN / IN PROGRESS / RESOLVED) and is updated in place when
work begins or completes; resolved entries remain in the doc
as historical record.

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
- **global_ticker_library/tickerdash.py**: Global Ticker Library per-ticker dashboard with single-job model
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

## Sprint State (as of 2026-05-08 — HISTORICAL, superseded by § 6 "Current Sprint State as of 2026-05-10" above)

The PRJCT9 sprint reached Phase 5D-1 onboarding closure on this date. Snapshot below preserved for fast context recovery on session start; for the current trajectory through Phase 6C-5, refer to the 2026-05-10 block in section 6.

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
