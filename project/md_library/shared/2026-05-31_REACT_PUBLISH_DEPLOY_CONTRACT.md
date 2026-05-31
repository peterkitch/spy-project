# React Publish / Deploy Contract

**Date:** 2026-05-31

**Status:** Authoritative for the React MVP publish / deploy
posture. Docs-only. Records the current operator decision and
the immediate publish workflow. Does NOT select a deployment
target, does NOT implement any script / manifest / CI / deploy
config, does NOT promote or copy any artifact, and does NOT
deploy anything. Public deployment remains gated by the Phase 5
honest-validation report (see Section 3 below).

**Anchor documents:**

- `project/md_library/shared/2026-05-26_REACT_MIGRATION_DECLARATION_AND_FRONTEND_CONTRACT.md`
  -- artifact-boundary rule, Forbidden Behaviors, "publish step
  is deferred" wording, Dash / React coexistence / cutover.
- `project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`
  -- `k6_mtf_ranking_v1` schema lock; ranking artifact as the
  only board input.
- `project/CLAUDE.md` Section 6 -- live operating contract;
  Phase 5 public-launch gate restatement.
- `project/md_library/shared/2026-05-04_PRJCT9_NORTH_STAR.md`
  -- Phase 5 honest-validation paragraph (the source of the
  gate).
- `project/md_library/shared/2026-04-30_PRJCT9_SPRINT_PLAN.md`
  -- Section 5 Phase 5 detail; PR #365 supersession banner
  reaffirms the gate.

---

## 1. Status and Scope

- This is a **docs-only contract**.
- It **records** the current private / internal publish / deploy
  posture for the React MVP.
- It **settles** the immediate publish posture: **Option A
  committed-fixture, PR-based refresh** (see Section 6).
- It does **NOT** select a deployment target.
- It does **NOT** deploy, promote, copy, or generate any
  artifact.
- It does **NOT** implement scripts, manifests, CI workflows,
  authentication, backends, Vite / config changes, or React
  source changes.
- It does **NOT** weaken or bypass the Phase 5 public-launch
  gate.

This contract exists so the React publish-step deferral
recorded in the React Migration Declaration is replaced by a
concrete, auditable, scope-bounded posture for private /
internal use. The publish step for **public** launch remains
deferred until the Phase 5 honest-validation report lands or
the operator makes a separate explicit sequencing decision.

---

## 2. Public-vs-Private Fork

"Deployment" has two meanings in PRJCT9, and they are
governed by different rules. This section makes the distinction
explicit so neither path is silently routed through the wrong
gate.

- **Public research site.** Reachable by anyone on the open
  internet. This is the eventual PRJCT9.com / public-launch
  posture. It is gated by the Phase 5 honest-validation
  report.
- **Private / internal cockpit extension.** Operator-only or
  small-trusted-group access; an extension of the existing
  Dash operator cockpit, not the public product. Examples:
  local-only / operator machine; an internal LAN host; a
  password-protected static host; a self-hosted private VPS.
  The Phase 5 public-credibility gate does NOT fire for this
  posture.

**Current operator decision: PRIVATE / INTERNAL for now.**

The React MVP is operator-cockpit-extension work today. Public
launch is NOT in scope for this contract.

---

## 3. Phase 5 Public-Launch Gate

This contract records the gate verbatim so future readers
cannot miss it.

**Public deployment of the React MVP remains BLOCKED until one
of the following is true:**

1. The Phase 5 honest-validation report is complete, OR
2. The operator makes a separate explicit sequencing decision
   that scopes public launch despite the report not being
   complete.

In either case, **public launch is a deliberate operator
act**, not an automatic consequence of this contract, of any
future promotion-script PR, of any deployment-config PR, or of
any operator-private deployment that may go live before Phase 5
clears.

This contract does **NOT** weaken or bypass the gate.

The gate is recorded in four places on the current `main`:

- `project/CLAUDE.md` Section 6 "Phase 5 honest validation
  report standing" paragraph (PR #365).
- `project/md_library/shared/2026-05-04_PRJCT9_NORTH_STAR.md`
  Sprint scope section, Phase 5 honest-validation paragraph,
  reaffirmed by the PR #365 status note at the top of the
  file.
- `project/md_library/shared/2026-04-30_PRJCT9_SPRINT_PLAN.md`
  Section 5 Phase 5 body, reaffirmed by the PR #365
  supersession banner at the top of the file.
- `project/md_library/shared/2026-05-26_REACT_MIGRATION_DECLARATION_AND_FRONTEND_CONTRACT.md`
  Trigger Condition Amendment, the final paragraph that
  acknowledges Phase 5 as a separate public-credibility gate
  independent of, and in addition to, the React migration
  trigger.

---

## 4. Current React MVP State

- `project/frontend/` is a Vite + React 18 + TypeScript SPA.
- It reads the committed fixture at
  `project/frontend/public/fixtures/k6_mtf_ranking.json` as a
  static asset at runtime.
- It renders the 8-ticker K=6 MTF board (AAPL, AMZN, GOOGL,
  META, MSFT, NVDA, SPY, TSLA) with the PR #364 Status-column
  hide reproduced.
- The operator has visually reviewed the rendered surface and
  declared it acceptable for cockpit use.
- Dash (`project/mvp_signal_board.py`) remains the operator
  cockpit and the prototype-of-record. Per the React Migration
  Declaration, Dash and React coexist during transition;
  cutover requires operator-declared behavioral parity.

---

## 5. Artifact Boundary (Binding Restatement)

The React app MUST consume the ranking artifact through a
single read-only static-asset fetch and MUST NOT do anything
else with the rest of the engine state at runtime.

Restating the binding rules from the React Migration
Declaration:

- The React app reads **exactly one** JSON artifact at runtime.
- The React app does **NOT** call the Python ranking engine.
- The React app does **NOT** recompute Sharpe, capture, win %,
  p-value, CCC, or any metric.
- The React app does **NOT** sign-flip any value, does **NOT**
  derive BUY/SHORT recommendations, and does **NOT** display
  data not present in the artifact.
- The React app does **NOT** read `output/` at runtime, does
  **NOT** read raw signal libraries, does **NOT** read price
  caches, and does **NOT** read PKLs or Phase E artifacts.

Citations:

- `project/md_library/shared/2026-05-26_REACT_MIGRATION_DECLARATION_AND_FRONTEND_CONTRACT.md`
  "Architecture Target" L93-L104 (publish step deferred,
  static site fetches published JSON, no Python server, no
  recomputation).
- Same doc "Data Contract" L108-L164 (reads only the ranking
  artifact; the artifact is the stable boundary).
- Same doc "Forbidden Behaviors" L206-L220.
- `project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`
  Ranking Artifact section L389-L431 (the `k6_mtf_ranking_v1`
  schema lock and the "ranking artifact is the only Dash
  input" stable-boundary discipline that the React app
  inherits).

**Any future publish step is UPSTREAM of React and does NOT
add a runtime capability to the React app.** A publish step is
permitted to copy / promote / verify the artifact; it is NOT
permitted to introduce a path through which the React app
fetches anything other than a `k6_mtf_ranking_v1` JSON.

---

## 6. Current Served Artifact -- Option A

- **Served artifact path:**
  `project/frontend/public/fixtures/k6_mtf_ranking.json`
- **Provenance recorded at:**
  `project/frontend/public/fixtures/README.md`
- **Mode:** committed fixture; the React app fetches it as a
  static asset.
- **Source of the fixture:** a byte-identical copy of the
  operator-authorized live ranking artifact at
  `output/k6_mtf/<RUN_TIMESTAMP>/k6_mtf_ranking.json` for an
  operator-approved run. The current fixture was copied from
  `output/k6_mtf/20260528T083411Z_post_fix/k6_mtf_ranking.json`
  with SHA-256
  `cf716b0d1e5ea1d92afb30b6ebe85845a4e19ed276f5fe9f27c58be44f9a5dfa`.
- **Why this option for now:** `output/` is gitignored at
  `project/.gitignore:10` and is local-only; the React app
  cannot read it at runtime from a clean checkout / deploy.
  The committed fixture is the smallest faithful stand-in
  consistent with the React Migration Declaration's "publish
  step is deferred" wording and the artifact-boundary rule.

**Future evolution path -- Option B:** when the operator
authorizes it under a separate implementation contract, a
build-time or operator-run copy plus a provenance manifest may
replace the PR-based refresh in Section 7. Option B is NOT
implemented or scheduled by this contract. It needs its own
implementation contract because the build environment must be
able to reach the approved source artifact and because the
manifest needs a writer (see Section 9 for the manifest
schema definition).

---

## 7. PR-Based Refresh / Promotion Workflow -- Option A Current

The Option A workflow for refreshing the committed fixture
when the operator approves a new K=6 MTF ranking artifact:

1. **Operator approves** a new
   `output/k6_mtf/<RUN_TIMESTAMP>/k6_mtf_ranking.json`
   artifact. (The K=6 MTF ranking-engine PR chain produces
   this; that is upstream work, not part of this contract.)
2. **Copy** the approved artifact **verbatim** to
   `project/frontend/public/fixtures/k6_mtf_ranking.json`.
   Do NOT hand-edit the JSON. Do NOT sanitize silently. If any
   value contains a drive letter / backslash / absolute path /
   local username, STOP and report rather than promoting it.
3. **Verify** the copy is byte-identical to the source
   (SHA-256 match).
4. **Update** `project/frontend/public/fixtures/README.md`
   provenance: `generated_at_utc`, `run_id`, source artifact
   path (project-relative), SHA-256.
5. **Run** the fixture-schema smoke test:
   `project/test_scripts/shared/test_k6_mtf_fixture_schema.py`
   under the pinned `spyproject2` interpreter with
   `-p no:cacheprovider`.
6. **Open a PR** against `main`. Single commit. Auto-merge
   OFF. Branch preserved.
7. **Operator review and Codex audit** before merge.
8. **Squash-merge** only after both reviews complete.

**Properties of this workflow:**

- **No automation.** The refresh is operator-supervised
  start-to-finish.
- **No compute.** The K=6 MTF ranking artifact was generated
  upstream by the operator-authorized engine PR chain; the
  refresh PR only copies an already-generated artifact.
- **No `output/` mutation.** The PR reads from `output/`
  (which exists on the operator's machine) and writes to
  `project/frontend/public/fixtures/`. Nothing under
  `output/` is touched by the refresh PR itself.
- **Single-commit, auditable diff.** The PR changes exactly
  the served fixture, the fixture README, and (if a
  fixture-schema smoke test addition is needed) the test
  file. Nothing else.

---

## 8. Required Verification Before Any Artifact Refresh PR

A refresh PR is BLOCKED from merge unless **every** check
below passes. Failure of any check blocks the refresh PR; the
PR description must record the verification results.

1. **SHA-256 match.** SHA-256 of the served fixture at
   `project/frontend/public/fixtures/k6_mtf_ranking.json`
   matches the SHA-256 of the source artifact at
   `output/k6_mtf/<RUN_TIMESTAMP>/k6_mtf_ranking.json` exactly.
2. **Schema version.** `schema_version` field of the served
   fixture equals `"k6_mtf_ranking_v1"`.
3. **Required top-level fields present.** Per
   `project/md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md`
   "Ranking Artifact" section: `schema_version`,
   `generated_at_utc`, `run_id`, `secondaries_requested`,
   `secondaries_ranked`, `per_secondary`, `issues`.
4. **Required per-secondary fields present.** Per the same
   contract section: `secondary`, `rank`, `status`,
   `history_artifact_path`, `history_as_of_date`,
   `current_snapshot`, `k6_stack`, `sharpe_k6_mtf`,
   `total_capture_pct`, `avg_capture_pct`, `stddev_pct`,
   `match_count`, `capture_count`, `trade_count`,
   `no_trade_count`, `skipped_capture_count`, `win_count`,
   `loss_count`, `win_pct`, `low_sample_warning`,
   `ccc_series`, `issues`.
5. **secondaries_ranked count and order recorded.** The
   refresh PR description records the count and the exact
   order so a future audit can diff against the prior fixture.
6. **Path hygiene.** Every path-like field
   (`history_artifact_path`, `k6_stack.selected_build_path`,
   `k6_stack.selected_run_dir`, `k6_stack.combo_k6_path`)
   meets ALL of:
   - is **project-relative**,
   - **starts with `output/`**,
   - contains **no drive letter** (no `^[A-Za-z]:` prefix),
   - contains **no backslash**,
   - does **not start with `/`**,
   - contains **no local username** (e.g., no `Users/` or
     `home/` substring).
7. **Fixture schema smoke test passes.**
   `project/test_scripts/shared/test_k6_mtf_fixture_schema.py`
   passes under the pinned interpreter.

If any check fails, the operator-supervised flow STOPS and the
refresh PR is NOT opened; the issue is reported and resolved
upstream (typically by re-running the K=6 MTF ranking-engine
chain or by a separate contract amendment if the schema itself
must change).

---

## 9. Future Promotion Manifest Schema -- Defined Now, Not Implemented

When Option B (build-time or operator-run copy with a
provenance manifest) is authorized under a separate
implementation contract, every promoted artifact MUST be
accompanied by a JSON manifest with the fields below. This
contract defines the manifest's shape; it does NOT write any
manifest file and does NOT implement a writer.

**Manifest fields:**

- `schema_version` (string). MUST equal `"k6_mtf_ranking_v1"`
  for the current launch path. Future schema versions require a
  contract amendment.
- `source_run_id` (string). The artifact's own `run_id`
  carried through to the manifest as a redundancy check.
- `source_generated_at_utc` (ISO-8601 UTC). The artifact's own
  `generated_at_utc`.
- `source_artifact_path` (string, project-relative). Where the
  promoted copy came from. MUST start with `output/`.
- `source_sha256` (lowercase hex). The SHA-256 of the source
  artifact, recomputed and matched at promote time.
- `promoted_at_utc` (ISO-8601 UTC). When the promotion ran.
- `promoted_by` (string). Role identifier; MUST equal
  `"the operator"` (or another role-style string). MUST NOT
  carry a personal name.
- `operator_approval_marker` (boolean). MUST be `true` for any
  promotion. If `false`, the manifest is rejected and the
  promotion does NOT proceed.
- `secondaries_ranked` (array of ticker strings). Copied from
  the artifact's own `secondaries_ranked` field as a
  redundancy check.
- `per_secondary_count` (integer). Length of the artifact's
  `per_secondary` array; the current K=6 MTF MVP value is 8.
- `validation_results` (object or the string
  `"not_required_for_private_internal_use"`). See
  "validation_results semantics" below.

**`validation_results` semantics (Phase 5 gate hook):**

This field is the structural hook for the Phase 5 public-launch
gate. The manifest writer MUST populate it in one of two
forms:

- **For a PUBLIC promotion:** `validation_results` MUST be an
  object referencing the completed Phase 5 honest-validation
  report. At minimum it carries
  `phase_5_validation_report_path` (project-relative or
  artifact URL), `phase_5_validation_report_sha256`, and
  `operator_acknowledgment_of_public_launch_gate` (boolean
  `true`). A PUBLIC promotion with `validation_results = null`
  or missing is REJECTED.
- **For a PRIVATE / internal promotion:** `validation_results`
  MUST equal the explicit string
  `"not_required_for_private_internal_use"`. Leaving the field
  blank or null is REJECTED; the explicit-string requirement
  makes the gate auditable in code rather than relying on
  prose alone.

A future Option B implementation contract is the natural place
to lock the exact JSON Schema for these fields, the writer
mechanics, the destination path of the manifest, and the
verification commands.

---

## 10. Deployment Target Options -- Selection Deferred

This section presents target classes for awareness only. **No
target is selected by this contract.** Target choice is
deferred to a later operator decision.

### Private / Internal Options

| Target | Public-by-default? | Suitable before Phase 5? | Suitable after Phase 5? |
|---|---|---|---|
| Local-only / operator machine (`npm run dev` or `npm run preview` on `127.0.0.1`) | Private by construction | Yes (already in use as the cockpit) | Yes, though usually superseded by a hosted target |
| Internal / private static host (self-hosted nginx on a private VPS, S3 + signed URL, internal LAN host) | Private by host configuration | Yes | Yes |
| Password-protected Netlify / Vercel / equivalent | Public-by-default but private with explicit access-control configuration | Yes, **only** with access control configured before any deploy | Yes |

### Public Options

| Target | Public-by-default? | Suitable before Phase 5? | Suitable after Phase 5? |
|---|---|---|---|
| GitHub Pages | Public by default | **No** (Phase 5 gate blocks public launch) | Yes; project-site subpath needs base-path consideration |
| Public Netlify / Vercel / equivalent | Public by default | **No** (Phase 5 gate blocks public launch) | Yes |

### Recorded posture

- **Current decision: PRIVATE / INTERNAL.**
- **Specific target: NOT selected.**
- **Target choice: DEFERRED** to a later operator decision.
- GitHub Pages project sites would require a `vite.config.ts`
  `base` change (currently `base: "/"` at
  `project/frontend/vite.config.ts:12`). The React loader
  already resolves the fetch URL via `document.baseURI` at
  `project/frontend/src/loadArtifact.ts:22`, so a future
  `base` change does NOT require component rewrites.
- Public targets are listed for awareness only. Section 3
  blocks them.

---

## 11. Non-Goals

The following are explicitly OUT OF SCOPE for this contract
and for any refresh PR that cites this contract:

- **No scripts.** No promotion script, no deploy script, no
  publish script, no manifest writer.
- **No CI.** No GitHub Actions workflow, no Netlify build
  hook, no Vercel build hook, no cron, no automation.
- **No Vite / config changes.** `vite.config.ts` and
  `tsconfig.json` are unchanged by this contract.
- **No deployment config.** No `netlify.toml`, no
  `vercel.json`, no `Dockerfile`, no `CNAME`, no `gh-pages`
  config.
- **No authentication.** Web-app auth / authorization is
  separate work and is NOT introduced.
- **No backend.** No Python server in the request path; no
  database; no live recomputation.
- **No Tier 2.** Tier 2 growth-queue scoping is separate work
  and is NOT introduced.
- **No artifact promotion.** No artifact is copied from
  `output/` by this contract. No artifact is uploaded
  anywhere. No deploy happens.
- **No compute.** No pipeline stage is invoked.
- **No public deployment until the Phase 5 gate clears** or
  the operator makes a separate explicit sequencing decision.
  Even then, public launch is a deliberate operator act.

---

## 12. Next Implementation Options After This Contract

The options below are presented for awareness. None of them
are scheduled by this contract.

**Direction-neutral / private path candidates (no Phase 5
dependency):**

- An **Option A artifact refresh PR** following the workflow in
  Sections 6, 7, and 8. Each such PR carries a new
  operator-approved K=6 MTF ranking artifact into the
  committed fixture and re-records provenance + SHA in the
  fixture README.
- An **Option B promotion-script + manifest implementation
  contract**, after separate operator authorization. Defines
  the writer, the manifest destination, and the verification
  surface using the schema in Section 9.
- A **`vite.config.ts` `base` adjustment** if a private
  subpath host is later chosen.
- A **private deployment config** (host-specific
  configuration file plus optional CI workflow) after the
  target is selected.

**Gated on Phase 5:**

- Any **public deployment**. This includes any change that
  makes the React MVP reachable on the open internet, whether
  via GitHub Pages, public Netlify / Vercel / equivalent, or
  any other host configuration.

**Separate major workstream:**

- The **Phase 5 honest-validation report** itself. The report
  is the gate; producing it is independent work and is
  recorded as standing in the North Star and Sprint Plan.

---

## Amendment History

- 2026-05-31 (initial). Records the React MVP publish / deploy
  posture as PRIVATE / INTERNAL for now, with public
  deployment gated by the Phase 5 honest-validation report.
  Settles the immediate posture as Option A committed-fixture
  PR-based refresh, defines the Option B promotion-manifest
  schema for future implementation, and presents deployment
  target options without selecting one.
