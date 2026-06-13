# Re-Rank -- As-Built Operations

- **Status:** AS-BUILT (verified against code on `main` and the real artifacts of
  the first autonomous Re-Rank publish).
- **Authored:** 2026-06-12 (named per the verified current date, CLAUDE.md C8).
- **Proven run:** `20260612T223250Z` -- whole-board re-score, `--target-as-of
  2026-06-11`, exit 0, Stage 9 `status=published`, publication commit
  `a1b0ae4` "Publish K6 MTF board 20260612T223250Z" (the first commit the
  pipeline itself authored and pushed).
- **main at authoring:** `a1b0ae45b82dc1f052482da7a105587d6d81c9d9` (that proven
  run's own allowlisted publish commit).

ASCII-only. Repo-relative paths throughout. The pinned interpreter path appears
once and is marked machine-specific. No token/secret values.

Accuracy rule for this doc: every load-bearing factual claim is cited to current
`main` source (`file:line`) or to a real artifact path. Re-Rank reuses the Stage
9 publish tail wholesale, so its transaction/failure machinery is the same as
Build-and-Rank; for the shared tail this doc points at the Build-and-Rank
runbook rather than restating it.

---

## 1. Overview

Re-Rank re-scores the WHOLE live board to ONE current as-of date and republishes.
There is no ticker question -- the board IS the selection
(`rerank_driver.py` module docstring). It runs ONE batch `k6_recook` restage
(no OnePass / ImpactSearch / StackBuilder; member discovery + selection are a
separate periodic rebuild), then the same Stage 9 publish tail Build-and-Rank
uses. Every surviving secondary is freshly re-scored and re-stamped with this
run's `validation_run_id`; quarantined secondaries are carried with their prior
verdict and prior provenance.

It differs from **Build-and-Rank** (out of scope here -- see
`md_library/shared/2026-06-11_BUILD_AND_RANK_OPERATIONS.md`): Build-and-Rank
refreshes a few operator-chosen secondaries and inserts them against the live
board, carrying every other row unchanged; Re-Rank refreshes the whole board on
its own cadence with an (almost) empty carried set. Tonight's run carried
exactly one row (`^TNX`, quarantined -- section 5) and freshly re-scored the
other 206.

The flow proven end to end: operator prep checks (token, clean tree, HEAD ==
origin/main, prior-input `Test-Path`) -> `rerank_driver.py --publish
--operator-approved-publish ...` -> recook -> validation -> Stage 9 (preflight,
fresh CCC Blob upload + GET, combine/proof, promote write, commit-allowlist
gate, publication commit, push `origin main`, Vercel deploy, live-verify poll)
-> `status published`.

---

## 2. Operator runbook (as proven)

### 2.1 Prerequisites

- **Run in the OPERATOR'S own PowerShell, never an agent shell.** The driver
  performs real Blob upload, public-fixture write, git commit, and push; those
  are operator-launched actions outside the Claude Code harness (CLAUDE.md PART
  B2 clarification paragraph). Blob/publish operations never go through an agent
  terminal.
- `BLOB_READ_WRITE_TOKEN` present in the launch session (presence only; never
  printed). The driver halts at its token preflight if absent
  (`rerank_driver.py:595-599`, returns 2), and Stage 9 re-checks token presence
  in the fail-fast preflight (`stage9_publish.py:431` `verify_publish_preflight_fast`).
  Set it ONCE with `setx BLOB_READ_WRITE_TOKEN "<value>"`; `setx` updates only
  NEW terminals, so the launch must happen in a window opened AFTER `setx` ran.
- **Clean tree with `HEAD == origin/main`.** Stage 9 preflight refuses a dirty
  worktree ("tracked worktree is not clean before publish") and a diverged
  origin; the publication commit must fast-forward push. A clean baseline is
  also what lets the commit-allowlist gate see ONLY the publication files.
- Prevent machine sleep for the run duration (tonight ~58m; budget guard 480
  min). Keep the PowerShell window open through push + deploy + live verify.

### 2.2 Prep checks (the exact 6-command block proven on 2026-06-12)

Run in the NEW window, in the project directory. The two prior-input paths point
at the run that produced the CURRENT live board; they advance each publish (read
them from the live promotion manifest's `validation_metadata` /
`ccc_series_storage`). For the 2026-06-12 run the prior board was
`20260611T105546Z`:

```
[bool]$env:BLOB_READ_WRITE_TOKEN
git rev-parse HEAD
git rev-parse origin/main
git status --porcelain
Test-Path output/crunch_runs/20260611T105546Z/publish_candidate/composite_validation_sidecar.json
Test-Path output/crunch_runs/20260611T105546Z/publish_candidate/combined_ccc_sidecar_verification.json
```

Pass criteria: token `True`; the two `rev-parse` SHAs identical; `git status
--porcelain` empty; both `Test-Path` `True`. The prior validation sidecar is
required whenever any row is carried and is SHA-bound to the prior fixture; the
prior CCC verification manifest is required unconditionally (these are resolved
for you by `rerank_driver.resolve_prior_inputs` from the live promotion
manifest, but Test-Path them first so a missing input fails in seconds, not
mid-run).

Pinned interpreter (MACHINE-SPECIFIC -- CLAUDE.md C1; do not hardcode
elsewhere): `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

### 2.3 Rehearsal (dry-run) -- ALWAYS run first

```
& "<PINNED_PYTHON>" rerank_driver.py `
  --publish-dry-run `
  --operator-approved-publish `
  --target-as-of 2026-06-11 `
  --duration-budget-minutes 480 `
  --operator-budget-label "rerank-dryrun" `
  --max-quarantine-fraction 0.10
```

Terminal on success: `Stage 9 status: dry_run_complete`
(`stage9_publish.py:1190`). The dry-run runs the full chain THROUGH the promote
dry-run gate and (when `--operator-approved-publish` is passed) DOES perform the
real fresh CCC Blob upload + GET round-trip, but it **STOPS at
`promote_dry_run_ok` and NEVER executes the commit gate, the publication commit,
or any push** (the dry-run terminal at `stage9_publish.py:1190` returns before
`promote_write` / commit / push). Consequence to state plainly: a dry-run cannot
rehearse commit-allowlist or push defects -- those gates run only on the real
publish. (This is exactly the gap that surfaced as the publish-1 commit-allowlist
refusal; see section 4.)

### 2.4 Real publish (the exact proven command, run 20260612T223250Z)

```
& "<PINNED_PYTHON>" rerank_driver.py `
  --publish `
  --operator-approved-publish `
  --target-as-of 2026-06-11 `
  --duration-budget-minutes 480 `
  --operator-budget-label "rerank-publish-2" `
  --max-quarantine-fraction 0.10
```

Terminal on success: `Stage 9 status: published`. `--publish` requires
`--operator-approved-publish` at parse time (`rerank_driver.py:556-557`).

### 2.5 MANDATORY FLAGS (do not omit)

1. **`--target-as-of <YYYY-MM-DD>` must be pinned EXPLICITLY.** Unpinned, the
   driver DERIVES the target from the wall clock, and the derivation flips to the
   new session after 16:00 ET (`rerank_driver.py:140` `derive_target_as_of`,
   `close_hour = MARKET_CLOSE_HOUR_ET = 16` at `:85`; derivation call at `:680`,
   `target_source="derived"`). An evening launch without the pin therefore
   replays an UNREHEARSED target -- different data, different validation -- than
   the dry-run you just ran. Always pin the same date you rehearsed (the proven
   run pinned `2026-06-11`, `target_source="overridden"`).
2. **`--max-quarantine-fraction 0.10` must ALWAYS be passed.** The code DEFAULT
   is `0.25` (`rerank_driver.py:540`); the production posture is `0.10`. The guard
   halts the publish if the quarantined fraction exceeds the ceiling
   (`rerank_driver.py:731`, `status=halted_quarantine_guard`, exit 7) -- a mass
   quarantine is a data/target problem for operator review, never an
   auto-publish. Tonight 1/207 = 0.48% was far under 0.10.

### 2.6 Expected runtime

Tonight ~58m end to end (recook 681s, publish 2801s, wall 3484s; section 6),
dominated by full-board validation. The `--duration-budget-minutes 480` guard
was not hit.

### 2.7 Success markers

- driver exits **0**, `status=published`
  (`output/rerank/latest_status.json`, `run_id=20260612T223250Z`);
- Stage 9 `status=published`, transaction state ends at `live_manifest_verified`
  -- all nine states
  (`output/crunch_runs/20260612T223250Z/publish_state.json`,
  `09_stage9_publish.json` `states`);
- `ccc_fresh_upload=true`, `dry_run=false`, `operator_approved=true` on the Stage
  9 summary (`09_stage9_publish.json`);
- a new `promoted_at_utc` + `source_run_id` + `source_sha256` on the live
  promotion manifest after Vercel deploy
  (`frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json`).

### 2.8 Standing halt rule + where the evidence lives

On ANY halt or refusal: **change nothing, run NO git, do not retry**, leave the
worktree and the live site as-is, and bring the evidence back for independent
audit. Gather: the screen output, `output/rerank/latest_status.json`, and (under
`output/crunch_runs/<RUN>/`) `publish_refusal.json`, `publish_state.json`, and
`09_stage9_publish.json`. The fail-closed envelope carries
`no_partial_publish=true` (`stage9_publish.py:257`).

---

## 3. As-built behaviors (Re-Rank specifics)

The Stage 9 transaction (state sequence, fail-closed envelope, output
verification, commit allowlist, live-verify, push-only resume, run lock) is
IDENTICAL to Build-and-Rank; see
`md_library/shared/2026-06-11_BUILD_AND_RANK_OPERATIONS.md` section 3.1. The
Re-Rank-specific behaviors:

- **Strict git-toplevel fail-fast BEFORE recook.** The driver resolves the git
  toplevel once at step 0 and refuses in seconds on any failure -- no fallback,
  exit 6 `status=refused_no_git_toplevel`, `halted_at=git_toplevel_preflight`
  (`rerank_driver.py:395` `_resolve_git_toplevel`, refusal at `:649-650`). This is
  a deliberate divergence from the crunch helper's parent-dir fallback: a
  git-less run cannot publish anyway, so guessing would only waste a ~55m
  recook+validation. (Fix commit `cea65bf`.)
- **The publish seam's `repo_root` is the git TOPLEVEL, not the `project/`
  subdir.** The commit-allowlist gate compares `git status --porcelain` paths
  (always toplevel-rooted, e.g. `project/frontend/...`) against the allowlist;
  the toplevel `repo_root` makes them match. Passing the `project/` dir was the
  publish-1 defect (section 4). (Fix commit `cea65bf`;
  `stage9_publish.py:892` `enforce_publication_allowlist`.)
- **Approved dry-run MAY perform the real fresh CCC Blob upload + GET.** When
  `--operator-approved-publish` is set, the dry-run's CCC step uploads fresh
  sidecars and verifies them rather than refusing; only an UNAPPROVED run is held
  to validate-only-then-refuse (`stage9_publish.py:555` `upload_or_reuse_fresh_ccc`).
  (Fix commit `ccad929`.) The dry-run still stops at the promote dry-run gate
  (section 2.3).
- **CCC wall is scope-gated to the records file's tickers.** The fresh CCC upload
  covers exactly the fresh secondaries and the records gate requires the records
  set to equal the fresh set (`upload_or_reuse_fresh_ccc`); carried rows reuse
  their prior immutable sidecars. Tonight: 206 fresh CCC records, all
  `get_verified=true` (`output/crunch_runs/20260612T223250Z/fresh_ccc_records.json`).
- **`walk_forward_n_folds` is advisory, never hard-locked.** The composite proof
  treats fold count as data-derived/advisory (mixed by validation cohort) while
  the locked methodology params (alpha, BH, bonferroni, 10000 permutations /
  bootstrap, CI 0.95) are verified against the prior sidecar
  (`output/crunch_runs/20260612T223250Z/publish_candidate/composite_phase5_report.md`).
- **Fail-fast publish preflight runs BEFORE the multi-hour validation.** The
  rerank seam runs the cheap, network-free approval + token-presence subset
  before Stage 5 so a missing approval/token refuses in seconds, not after the
  full validation (`crunch_rebuild_orchestrator.py:2203`
  `verify_publish_preflight_fast`, defined at `stage9_publish.py:431`).
  (Fix commit `ccad929`.)

---

## 4. Failure law (Re-Rank halts and the resume boundary)

- **Mid-Stage-9 failures never require operator git.** Every gate is fail-closed
  and writes `publish_refusal.json` (`no_partial_publish=true`,
  `stage9_publish.py:257`). The operator's job at any halt is forensics, not
  repair: change nothing, run no git.
- **Pre-commit refusals (e.g. the publish-1 commit-allowlist refusal).** If the
  commit-allowlist gate refuses (`stage9_publish.py:892`
  `enforce_publication_allowlist`), the promote write may have updated the
  working-tree fixture/manifest but NOTHING was committed or pushed (HEAD and
  origin unchanged). This is the failure mode that the `cea65bf` toplevel fix
  removed; if a new pre-commit refusal appears, capture the refusal envelope and
  audit -- do not hand-stage or hand-commit anything.
- **Post-commit, pre-push has a purpose-built resume.** If a run reached
  `commit_created` but not `push_ok`, a relaunch VALIDATES the worktree is
  exactly the recorded publish commit (HEAD == recorded SHA, clean allowlist,
  committed file SHAs match) and resumes at PUSH ONLY -- never a second commit
  (`stage9_publish.py:1043` `_resume_commit_not_pushed`). Do not improvise a push;
  relaunch and let the resume validate.
- **Post-push deploy/live-verify failure is `deploy_failed_after_push` and is
  explicitly NON-ROLLBACK.** After the push succeeds, any live-verify failure
  takes the no-further-git path -- never a recommit, never a repush, never a
  revert (`stage9_publish.py:985` `verify_live_manifest`, region `:988-1034`,
  `deploy_failed_after_push=True`). The commit is already public; the remedy is
  forensics (Vercel deploy state, manifest poll), not manual git cleanup.

---

## 5. Blob facts

- **Sidecars are immutable.** Each CCC sidecar is uploaded under a run-prefixed,
  content-addressed pathname and is never mutated. A republish writes NEW
  sidecars for the freshly re-scored rows under the new run prefix; carried rows
  keep their original sidecars.
- **Aborted runs leave harmless orphans by design.** A run that uploads fresh
  CCC sidecars and then refuses later leaves those sidecars in Blob storage; they
  are immutable, unreferenced by the live manifest, and safe -- there is no
  cleanup step and none is needed.
- **Blob operations outside the pipeline stay in the operator's own PowerShell.**
  Any ad-hoc Blob inspection or maintenance is an operator action with the
  operator's token; it never routes through an agent shell.
- **`^TNX` carry.** `^TNX` is currently quarantined at Stage B
  (`member_library_unavailable`, a member library cannot be built) and is carried
  on the public board with its PRIOR validation provenance and prior CCC sidecar
  (its `validated_as_of_utc` and `ccc_series_last_date` are older than the fresh
  rows). It is excluded from this run's fresh CCC upload (206 fresh, not 207).
  Disposition: replace its unbuildable member in the periodic rebuild; until
  then it carries honestly.

---

## 6. Proven-run record

Run `20260612T223250Z` (`output/rerank/latest_status.json`,
`output/crunch_runs/20260612T223250Z/`):

- launch flags: `--publish --operator-approved-publish --target-as-of 2026-06-11
  --duration-budget-minutes 480 --operator-budget-label "rerank-publish-2"
  --max-quarantine-fraction 0.10`; `target_source=overridden`.
- result: exit 0, `status=published`; Stage 9 all nine states through
  `live_manifest_verified`; `ccc_fresh_upload=true`, `dry_run=false`.
- publication commit: `a1b0ae4` "Publish K6 MTF board 20260612T223250Z" -- the
  pipeline's own allowlisted commit, pushed to `origin/main` by Stage 9.
- timings: recook 681s, publish (validation + CCC + combine/proof + promote +
  commit + push + live-verify) 2801s, wall 3484s.
- board: 207 rows -- 206 freshly re-scored + 1 carried (`^TNX`); 90
  board_validated, 117 not_validated; promote self-check all `pass`
  (`09_stage9_publish.json` `combine_summary`).
- live provenance after deploy: `source_run_id=20260612T223250Z`,
  `promoted_at_utc=2026-06-12T23:30:30Z`, `source_sha256=5f159e85...`,
  `per_secondary_count=207`
  (`frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json`).

---

## 7. Pointers

- Build-and-Rank operations (the sibling flow + the shared Stage 9 transaction
  detail): `md_library/shared/2026-06-11_BUILD_AND_RANK_OPERATIONS.md`.
- Re-Rank scoping / design history (SUPERSEDED by this doc):
  `md_library/shared/2026-06-12_RERANK_AUTONOMOUS_SCOPING.md`.
- Source of record:
  - `rerank_driver.py` -- the question-free Re-Rank driver (board enumeration,
    target derivation, recook, quarantine guard, toplevel preflight, publish
    seam wiring).
  - `stage9_publish.py` -- the Stage 9 fail-closed publish transaction (shared).
  - `crunch_rebuild_orchestrator.py` -- `run_rerank_publish` seam
    (`:2159`) + fail-fast publish preflight (`:2203`).
  - `crunch_combine_proof.py` -- prior-board + fresh-row assembly, carry-forward.
  - `utils/react_publish/promote_k6_mtf_artifact.py` -- public fixture / manifest
    / README write (LF, fail-closed, never deploys).
- Suites at HEAD (`a1b0ae4`, all green): `test_scripts/test_rerank_driver.py` 52;
  `test_scripts/test_stage9_publish.py` 50;
  `test_scripts/test_crunch_rebuild_orchestrator.py` 160;
  `test_scripts/k6_recook/test_k6_recook.py` 134.
