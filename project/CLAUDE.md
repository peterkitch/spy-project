# CLAUDE.md

**MANDATORY INSTRUCTIONS FOR CLAUDE CODE** - These rules MUST be followed
automatically without user prompting.

This file is the repository's single source of truth for agents. `AGENTS.md`
(at the repo root and at `project/AGENTS.md`) is a thin discovery pointer that
intentionally defers to this file; preserve that relationship. Read this file
in full before doing any work under `project/`.

Active project context: June 2026. (The repository contains older dated
material from 2025; do not treat those dates as the current context. See
PART D - Historical Context.)

ASCII-only discipline applies to this file: no Unicode characters anywhere in
CLAUDE.md (Windows console / cp1252 sensitivity). Use `--`, `->`, `[OK]`,
`[FAIL]` instead of Unicode dashes/arrows/marks.

---

# PART A - CURRENT STATE (what PRJCT9 is now)

## A1. The live public product is a React SPA, not a Dash app

The live, public-facing product is the React single-page app in
`project/frontend/` (Vite + React 18 + TypeScript). It is a read-only K=6 MTF
leaderboard. At runtime it fetches exactly one committed static artifact:

    project/frontend/public/fixtures/k6_mtf_ranking.json

Facts derived from that committed fixture and its promotion manifest
(`frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json`) and
`frontend/public/fixtures/README.md`:

  - Fixture schema: `k6_mtf_ranking_v2`.
  - Ranking run id: `20260604T110400Z_recook_full248_clean_csv`.
  - Board carries 205 secondaries: 88 board_validated, 117 not_validated,
    43 Stage-A excluded.
  - The fixture is slim. Inline `ccc_series` is empty for every row; each row
    carries Blob sidecar metadata (`ccc_series_source="vercel_blob"`,
    `ccc_series_url`, `ccc_series_pathname`, `ccc_series_sha256`,
    `ccc_series_byte_size`, `ccc_series_points`, first/last CCC dates).
  - Full-resolution CCC time series are stored off-repo as immutable public
    Vercel Blob sidecars (one per secondary) and are lazy-loaded by the React
    detail view (`frontend/src/cccSidecar.ts`). Allowed URL host pattern:
    `*.public.blob.vercel-storage.com`. The sidecars carry derived CCC fields
    only (`date_utc`, `cumulative_capture_pct`, `per_bar_capture_pct`,
    `trade_direction`) -- no raw OHLCV, no provider price series, no
    credentials.
  - The committed fixture / promotion-manifest `source_sha256` is the
    canonical LF SHA `4b6736da150ade118d6cbd0fb8ab974f954ed4fef3c8af9acc8dda6a8c569d97`
    (692,240 LF bytes). The fixture, promotion manifest, and fixtures README
    are pinned `text eol=lf` in `project/.gitattributes` so the provenance SHA
    reproduces on every checkout platform.

Deployment target (operator context, not asserted by a committed contract):
the React app is served at prjct9.com via Vercel auto-deploy on push to
`main`. Push and deploy are operator-run (see PART B).

## A2. Validation and publication gating

  - The 205-scope Phase 5 honest-validation report is COMPLETE and bound into
    the published fixture/manifest by the promotion gate:
    `md_library/shared/2026-06-04_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_205.md`
    plus its paired `.manifest.json`, with the validation sidecar
    (`validation_run_id 20260604T120000Z_validation_full205`,
    `artifact_sha256 8e48fd56...`). The promotion helper verifies the
    report <-> report-manifest <-> validation-sidecar <-> CCC verification
    manifest <-> slim fixture binding before any public fixture is written.
  - Leaderboard ordering reflects K=6 MTF ranking metrics. Ranking position is
    NOT a claim that a row cleared Phase 5 validation; validation survivorship
    is disclosed per row.
  - Phase 5G status: SATISFIED-BY-ACCEPTED-RISK under
    `md_library/shared/2026-06-01_PHASE_5G_2_OPERATOR_ACCEPTED_RISK_DECISION_RECORD.md`.
    This is operator accepted-risk for a narrow Mode B derived-only,
    non-commercial public surface. No legal clearance is claimed.
  - Mode B controls remain in force on the public surface:
    - no raw OHLCV;
    - no downloadable provider price series;
    - no reconstructable provider price series;
    - no monetization while yfinance remains in the data pipeline.
    Any change that breaches these controls reopens Phase 5G.

## A3. Promotion / publication machinery

The gated, operator-run publication path lives in `project/utils/react_publish/`:

  - `promote_k6_mtf_artifact.py` -- validates a candidate v2 fixture, verifies
    all bindings, normalizes bytes to LF before hashing/writing, records the
    LF provenance SHA, and writes the committed public fixture + promotion
    manifest. Dry-run by default; fail-closed; never deploys.
  - `k6_mtf_validation_join.py` -- joins the ranking artifact with the Phase 5
    validation sidecar into the v2 payload shape.
  - `k6_mtf_phase5_report_generator.py` -- produces the Phase 5 report +
    paired manifest.

Authoritative contracts for this surface (read before changing it):

  - `md_library/shared/2026-05-26_REACT_MIGRATION_DECLARATION_AND_FRONTEND_CONTRACT.md`
  - `md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`
  - `md_library/shared/2026-05-31_REACT_PUBLISH_DEPLOY_CONTRACT.md`

Smoke / regression coverage:
`test_scripts/shared/test_k6_mtf_fixture_schema.py` and
`test_scripts/shared/test_react_publish_promote_k6_mtf_artifact.py`.

## A4. The Python engine tree is the build/research substrate, not the runtime

The large Python tree at `project/` (engines, planners, writers, audits) is
the build/research substrate that produces and validates the artifacts the
React app consumes. It is NOT the live runtime and is NOT dead code.

  - Engine families such as `confluence_*`, `daily_board_*`, `multiwindow_k_*`,
    `trafficflow_*`, and `signal_library_*` were important build/research
    surfaces and may still be referenced by contracts or by future automation.
    Do not call them dead code. Any future automation MUST inspect the actual
    current call sites and artifact contracts before invoking them, rather than
    assuming a script's role from its name.
  - `spymaster.py` is an intentional standalone regression baseline / research
    substrate. It is self-contained (direct yfinance, isolated cache) and is
    used to cross-check metrics. Do not couple it to other modules; do not
    treat it as the architecture of record for the live site.
  - K=6 MTF pipeline producers: `k6_recook.py`, `k6_mtf_ranking_engine.py`,
    `k6_mtf_history_producer.py`. Validation: `validation_engine.py`,
    `honest_validation_ledger.py`, and the locked `canonical_scoring.py`.
  - The original Dash board surfaces (`mvp_signal_board.py`,
    `daily_signal_board.py`, `phase6_research_preview.py`,
    `primary_signal_engine.py`, etc.) are prototype / operator-cockpit
    substrate, not the live public product.

## A5. Next phase and current data freshness

  - Next phase: headless daily-refresh automation to keep the published board
    current. It MUST preserve: promotion gates; Blob sidecar integrity;
    fixture / report / manifest / validation-sidecar binding integrity; Mode B
    controls; reproducible LF provenance SHA behavior; and no raw OHLCV /
    provider-price exposure.
  - Until that headless refresh path exists, the data on the live site is
    stale (the published fixture is a point-in-time promotion).
  - Ordering of work: design and language/visual polish come AFTER, in order,
    (1) the operator trusts the metrics, (2) significant bugs are cleared, and
    (3) daily / headless refresh is working.

---

# PART B - ROLES, WORKFLOW, AND THE PUBLICATION BOUNDARY

## B1. Four-role collaboration (plus the operator)

  - Web Claude: co-foreman and strategist. Drafts and reviews prompts,
    evaluates reports, reconciles findings, and verifies claims against
    repository artifacts. Does not run git or bash in the working repository.
  - ChatGPT: outside-view reviewer and artifact-based auditor. Reviews prompts,
    reports, and repository-derived evidence without direct repository access.
  - Claude Code: primary implementer for ordinary code, tests, dry-runs, and
    documentation (this voice, in the project terminal). Subject to the
    auto-mode safety classifier.
  - Codex: independent auditor, and implementer for publication-class
    repository work when Claude Code is blocked by the classifier.
  - Operator: couriers prompts and reports between agents, owns external
    egress, and owns the final push to `main`.

## B2. Sprint 500 publication boundary (harness-level safety)

Claude Code's auto-mode safety classifier hard-denies publishing
private-repo-derived data to a public surface. In practice it blocked:

  - Blob upload of sidecars;
  - writing the committed public fixture;
  - committing / pushing publication-class work;
  - attempts to configure around the block (for example via an
    `autoMode.environment` setting) as a bypass workflow.

This is a harness-level safety determination and is NOT overridable inside a
Claude Code session. Do not attempt to prompt around classifier denials.

Operating rules that follow from this boundary:

  - Claude Code MAY read committed files and run tests for validation when the
    prompt permits it.
  - Do NOT route publication-class write, commit, push, deploy, or external
    upload through Claude Code.
  - External egress and the final push to `main` are operator-run.
  - Publication-class repository writes/commits are routed outside Claude Code
    (Codex or operator), with auditor independence preserved.

B2/B3 restrict what Claude Code may execute inside its harness. They do not
prohibit the repository from containing an operator-launched orchestrator mode
that performs Blob upload, public fixture write, git commit, push, or
deploy-adjacent publication steps, provided the operator explicitly launches it
outside Claude Code and the program has fail-closed preflights.

## B3. Auditor independence

The auditor must be a different voice than the implementer.

  - Default: Claude Code implements, Codex audits.
  - For publication-class work: Codex may implement, and Claude Code may
    perform read-only audit only (when the classifier permits).
  - Every Codex patch requires an independent audit-of-patch before merge.
  - Codex may patch in-audit ONLY when ALL apply: the prompt explicitly says
    "you may patch in-audit"; the gap is small, unambiguous, and matches a
    locked spec verbatim; the commit message starts with "Codex audit fix:";
    no new public surfaces / defaults / test patterns are added; and patches
    stay within files already in the diff. Otherwise: report-only.

## B4. Git workflow conventions

  - Squash-merge with branch preservation. Never `--delete-branch`; branches
    stay as a visual audit trail.
  - Work on a new branch off `main`; do not commit directly to `main`. The
    final push to `main` is operator-run.
  - Idle when told a preflight/audit is with another voice. A pasted
    implementation or amendment prompt is authorization to start that scope.

---

# PART C - OPERATING RULES (in force)

## C1. Pinned Python interpreter (CRITICAL)

The project's pinned audit interpreter on this machine is:

```
C:\Users\sport\AppData\Local\NVIDIA\MiniConda\envs\spyproject2\python.exe
```

This is a Python 3.12 conda env (`spyproject2`): Python 3.12.2, NumPy 1.26.4
(MKL-backed), pandas 2.2.1, SciPy 1.13.1, pytest 8.3.5. The contract is the
interpreter path and the runtime it actually provides, not the aspirational
pins in `environment.yml` / `requirements.txt`.

The env is NOT on PATH by default. A bare `python` may resolve to a different
Python (e.g. 3.13) that cannot use the baseline-lock wheel set. Always invoke
as:

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" -m pytest test_scripts -q
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" -m py_compile <file>.py
```

If the env directory is missing, STOP and report. Do not recreate it from
`requirements.txt` / `environment.yml` and do not rebaseline snapshots under a
different NumPy/SciPy/pandas stack without explicit authorization.

## C2. Single-command bash discipline

Every Bash tool invocation runs a single command. No `;`-chain, `&&`-chain,
`||`-chain, or pipe-chain compounds. Run independent commands in parallel
(multiple Bash calls in one message) rather than chaining. Heredocs for
multi-line strings (e.g. commit messages) are fine -- that is input to a
single command, not chaining. Prefer the dedicated tools (Read, Edit, Write,
Glob, Grep) over Bash when they fit.

## C3. Do NOT use `git -c commit.gpgsign=false`

GPG signing is not configured on this machine. The `git -c commit.gpgsign=false`
prefix is cargo-culting and must not be added. Use plain `git commit` (heredocs
for multi-line messages). If a commit ever genuinely needs to bypass GPG,
diagnose the root cause rather than re-adding the prefix.

## C4. Operational discipline (learned from Sprint 500)

  - Inspect before implementing. Do not rely on memory for current repo state.
  - Verify artifact schemas, counts, current paths, and helper/function names
    against the actual files before drafting an implementation.
  - For SDKs, verify the installed package surface (versions, importable
    symbols, call signatures) and use it as the source of truth, not
    documentation assumptions alone.
  - For public-promotion or public-fixture work, use the operator-run or
    non-Claude-Code publication workflow rather than attempting to prompt
    around classifier denials.
  - For any external-client tests, mask real credentials and block real
    network by default; tests MUST be hermetic even when live credentials
    exist in the parent shell.
  - Never print, log, echo, or commit token values. Generic credential-safety
    only; do not add new token names or token-handling instructions here.

## C5. Authoritative documents

When implementing, debugging, or auditing, these are the sources of truth:

  - Algorithm spec (formal contract):
    `project/md_library/shared/2026-04-30_PRJCT9_ALGORITHM_SPEC_v0_5.md`
    (referenced by section number across the codebase, e.g. capture units,
    the zero-capture trigger-day rule, ddof=1 sample std, sf-form p-value,
    combine-consensus rule, calendar grace days).
  - Intentional Delta Ledger (audit trail):
    `project/md_library/shared/2026-05-01_PHASE_1B_INTENTIONAL_DELTA_LEDGER.md`
  - Implementation inventory (call-site map):
    `project/md_library/shared/2026-05-01_PHASE_1B_IMPLEMENTATION_INVENTORY.md`
  - Canonical scoring module: `project/canonical_scoring.py`
    (single source of truth for metric math: `score_captures`,
    `score_signals`, `combine_consensus_signals`, `metrics_to_legacy_dict`,
    `CanonicalScore`).

When in doubt: spec wins, then ledger, then inventory, then code. If code
disagrees with spec, the code is wrong unless a ledger entry classifies the
divergence.

## C6. Scoring math convention (project-wide)

Encoded in `canonical_scoring.py` (zero-capture trigger-day rule). Locked:

  - Wins are directional captures `> 0`.
  - Losses are `trade_count - wins`. Zero-return BUY/SHORT directional trades
    are losses.
  - NONE / no-position / Cash bars are excluded from the directional-trade set
    before win/loss classification.
  - `win_count + loss_count == trade_count` exactly. There is no third
    zero-return bucket.
  - `win_pct` / `win_rate` uses `trade_count` (or `trigger_days`) as
    denominator when `trade_count > 0`; null otherwise.

Mandatory for every scoring surface. Prefer delegating to
`canonical_scoring.score_captures`. When a surface must stay self-contained
(artifact-as-boundary), implement the canonical-equivalent predicate AND add
tests proving: zero-return BUY counts as a loss; zero-return SHORT counts as a
loss; `win_count + loss_count == trade_count` on a mixed fixture; NONE bars are
excluded; and a canonical-equivalence assertion (`wins > 0`, `losses = n -
wins`). Background: a Codex audit found surfaces that used `losses = captures
< 0`, silently dropping zero-return captures; the predicate fix and this rule
prevent that drift.

Known caveat (not a license to copy): `stackbuilder.py`'s legacy
single-arg `metrics_from_captures` fallback uses `captures.ne(0.0)` as a
stand-in mask when no explicit `trigger_mask` is supplied; Phase 1A
baseline-lock tests pin its existing behavior.

## C7. Test-suite discipline

The default `test_scripts/` suite is the fast suite and must complete without
inspecting real operational state on the dev machine. Two markers govern
selection:

  - `slow`: integration / heavy-compute tests.
  - `production_smoke`: tests that inspect real operational state under
    `output/`, `signal_library/`, `cache/`, or `price_cache/` (dev-machine
    state outside `tmp_path`).

`pytest.ini` sets `addopts = -m "not slow and not production_smoke"`, so the
fast default deselects both. A command-line `-m` expression overrides (not
appends to) that filter. Rules for new tests:

  - Any test touching real operational state outside `tmp_path` must be marked
    `production_smoke` and gate on an explicit env opt-in.
  - Any test over ~30s should be marked `slow` or redesigned.
  - Autouse fixtures must not walk operational roots; monkeypatch the root
    provider to a `tmp_path` layout.

Verified commands (pinned interpreter):

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" -m pytest test_scripts/
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" -m pytest test_scripts/ -m "slow or production_smoke"
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" -m pytest test_scripts/ --override-ini="addopts="
```

## C8. File placement and naming

  - Do NOT create new files in the repo root except when explicitly modifying
    existing core apps.
  - Tests go in `test_scripts/` (script-specific subfolders, or
    `test_scripts/shared/` for multi-script/environment tests).
  - Docs go in `md_library/` (script-specific subfolders, or
    `md_library/shared/` for docs spanning multiple scripts).
  - Dated files use `YYYY-MM-DD_ACTION_DESCRIPTION_IN_CAPS.md`. ALWAYS verify
    the actual current date (run `date`) before naming a dated file; do not
    rely on memory or on stale dates elsewhere in the repo.
  - Do not leave temporary files in the tree; use an OS-temp path for scratch.

## C9. ASCII-only console output (cp1252 substrate)

The Python substrate runs on Windows with cp1252 console encoding. Never emit
Unicode to the console: use `[OK]`, `[FAIL]`, `[WARNING]`, `->`, and ASCII
separators. Unicode is fine inside the React app, HTML, and UTF-8 log/JSON
files; the restriction is console output and this file.

## C10. Parked / out-of-scope

  - QuantConnect (`QC/`) integration is parked indefinitely; it is a frozen
    historical snapshot, excluded from current-phase work (including residual
    Adj Close sites under `QC/`). Revisit only on explicit scope expansion.
  - `matrix.py` does not exist as a file; the concept survives only as a
    disabled code path in `trafficflow.py`.
  - `.bat` launchers are local shortcuts, gitignored by design.

## C11. Deferred work and cross-session tracking

Durable carry-forward and Phase 7+ research items live in the tracking docs,
not in conversation memory:

  - `md_library/shared/2026-05-23_POST_PHASE_6I_SPRINT_CARRYFORWARD.md`
  - `md_library/shared/2026-05-23_PHASE_7_PLUS_UNIVERSE_WIDE_BEAM_SCOPING.md`

Each entry carries Status (OPEN / IN PROGRESS / RESOLVED) and is updated in
place. New cross-session items should be appended to the appropriate tracking
doc. Deferred technical-debt examples: `compute_signals` cleanup,
`environment.yml` / `requirements.txt` hygiene, daily TrafficFlow / MTF /
Confluence scheduling, and cloud-compute architecture for ticker expansion.

---

# PART D - HISTORICAL CONTEXT (superseded; do not treat as current)

The repository carries a large, dated development trail from before the React
v2 / Blob-sidecar publication. It is preserved as audit history and is NOT the
current architecture:

  - The product was originally an SMA pair-optimization analysis app built with
    Python and Dash; that framing described the prototype substrate, not
    today's live React product.
  - A long Phase 6G / 6H / 6I trail (Town Notice Board UX, read-only-by-default
    planning, the two-key guarded daily-board writer, supervised-run gate,
    contract validator, flow-integrity audit) and a TrafficFlow K=1..6 headless
    canonical-write rail were delivered as build/research surfaces. They remain
    referenceable but are not the live launch path.
  - Earlier "Current Sprint State" framing (an 8-ticker `mvp_signal_board.py`
    Dash board rendering a `k6_mtf_ranking_v1` artifact, with Phase 5 still
    pending) is superseded by PART A above: React, `k6_mtf_ranking_v2`, 205
    secondaries, Phase 5 complete and bound, publicly promoted.

For the detailed historical trail, use `git log` and the dated evidence docs
under `md_library/shared/` (and the carryforward ledger in section C11). Do not
reconstruct it here; this file is doctrine for the current state, not a sprint
transcript.
