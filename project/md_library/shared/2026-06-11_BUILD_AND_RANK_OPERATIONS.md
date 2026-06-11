# Build-and-Rank -- As-Built Operations

- **Status:** AS-BUILT (verified against code on `main` and the real artifacts of
  the first hands-off autonomous publish).
- **Authored:** 2026-06-11
- **Proven run:** `20260611T105546Z` -- IHI + SCHG refresh, launched
  2026-06-11T10:55:46Z, `promoted_at_utc` 2026-06-11T13:14:49Z (~2h19m), exit 0,
  `status=completed_publish`, Stage 9 `status=published`.
- **main at authoring:** `029c4c8` (the proven run's own allowlisted publish
  commit "Publish K6 MTF board 20260611T105546Z"; the only delta from `8148823`).

ASCII-only. Repo-relative paths throughout. The pinned interpreter path appears
once in the runbook and is marked machine-specific. No token/secret values.

Accuracy rule for this doc: every factual claim is cited to current `main`
source (`file:line`) or to a real artifact path. Where the scoping doc
(`md_library/shared/2026-06-11_STAGE9_AUTONOMOUS_PUBLISH_SCOPING.md`) differs from
as-built behavior, the code wins and the divergence is called out in section 6.

---

## 1. Overview

Build-and-Rank carries a small set of fresh or refreshed secondaries through the
full pipeline (Stages 1-9) and inserts them against the currently-published board
**as-is**: rebuilt rows supersede their prior counterparts in place, and every
other row is carried forward unchanged except for rank and `validated_as_of_utc`.
The carried set is the prior board minus the freshly-rebuilt secondaries
(`crunch_combine_proof.py:405` `carried_secs = {s for s in prior_by_sec if s not
in fresh_by_sec}`); a fresh row for an existing secondary upserts (no duplicate
row). Today's run proved this: the board stayed at 207 rows with exactly one IHI
and one SCHG row, both re-stamped `validation_run_id=20260611T105546Z`
(`frontend/public/fixtures/k6_mtf_ranking.json`).

It differs from the upcoming **Re-rank** (out of scope here, logged in the scoping
doc section 6): Re-rank re-scores the WHOLE board on its own cadence with an empty
carried set, so every row is refreshed. Build-and-Rank refreshes only the
requested secondaries; carried rows keep their original (older) data era. The
Stage 9 publish tail is deliberately caller-neutral so Re-rank reuses it wholesale
(`crunch_combine_proof.py:288` `combine_and_assemble` docstring: "Build-and-Rank
passes a subset of fresh rows; Re-rank may pass all rows").

---

## 2. Operator runbook (as proven)

### 2.1 Prerequisites

- `BLOB_READ_WRITE_TOKEN` present in the launch session (presence only; never
  printed). The launcher reads it at its token preflight and halts before the one
  question if absent (`stage9_launcher.py:313`, returns 2). Set it ONCE with
  `setx BLOB_READ_WRITE_TOKEN "<value>"`; `setx` updates only NEW terminals, so
  the launch must happen in a window opened AFTER `setx` ran.
- Prevent machine sleep for the run duration (today: ~2h19m; budget guard 240
  min). Keep the PowerShell window open through push + deploy + live verify.
- OnePass reuse freshness: the reused OnePass must be within the 168h window
  (`crunch_rebuild_orchestrator.py:73` `DEFAULT_REUSE_ONEPASS_MAX_AGE_HOURS =
  168`; freshness check `:950-963`). Today's reuse of
  `output/crunch_runs/20260606T053735Z` was accepted at age 111.759h
  (`output/crunch_runs/20260611T105546Z/00_onepass_reuse_proof.json`,
  `valid=true`, `timestamp_source=end_timestamp_utc`). Past 168h, reuse is refused
  and a fresh full OnePass (~13.5h) is required.

### 2.2 Prep checks (the exact shape used today)

Run in the NEW window, in the project directory:

- Token presence boolean only (never the value): `[bool]$env:BLOB_READ_WRITE_TOKEN`
- Local main equals origin: `git rev-parse HEAD` and `git rev-parse origin/main`
  must match (so the Stage 9 push fast-forwards).
- Artifact existence (the required prior-publish inputs -- see 2.3):
  `Test-Path frontend/public/fixtures/k6_mtf_ranking.json`
  `Test-Path frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json`
  `Test-Path output/crunch_runs/20260610T221108Z/publish_candidate_samerun_ccc/composite_validation_sidecar.json`
  `Test-Path output/crunch_runs/20260610T221108Z/publish_candidate_samerun_ccc/combined_ccc_sidecar_verification.json`
  `Test-Path operator_inputs/crunch_blocked_tickers.txt`
  `Test-Path global_ticker_library/data/master_tickers.txt`

The blocked/master files are mandatory: the launcher loads them before asking its
question (`stage9_launcher.py:325` master empty/absent -> return 4;
`stage9_launcher.py:330` blocked missing/malformed -> return 5).

### 2.3 Launcher invocation (flag semantics)

The launcher adds these INTERNALLY (the operator must not type them) --
`stage9_launcher.py:207` `build_orchestrator_argv`:

- `--execute`, `--publish`, `--operator-approved-publish`,
  `--rebuild-secondaries-file <written file>` (the launcher writes a timestamped
  gitignored secondaries file from the answer -- see section 4).

Operator-supplied extra args (passed verbatim after the launcher's own args):

- `--reuse-onepass-run-dir output/crunch_runs/20260606T053735Z` (OnePass reuse,
  opt-in; gate at `crunch_rebuild_orchestrator.py:73,:950-963`).
- `--target-as-of <YYYY-MM-DD>` (required for `--execute`).
- `--allow-network-fetch` (required for `--execute`).
- `--duration-budget-minutes <N>` and `--operator-budget-label "<label>"`
  (both required for `--execute`). These four are enforced together:
  `crunch_rebuild_orchestrator.py:1026-1033` builds `missing_execute_gates`; a
  non-empty set HALTS at `:1214`.
- Prior-publish inputs:
  - `--publish-prior-fixture` and `--publish-prior-promotion-manifest` are
    **defaulted** to the committed live board and need not be passed
    (`crunch_rebuild_orchestrator.py:1524-1527` and `:1529-1533`).
  - `--publish-prior-validation-sidecar` is **required** whenever carried rows
    are present: combine raises without it (`crunch_combine_proof.py:430-434`),
    and it is SHA-bound to the prior fixture (`crunch_combine_proof.py:474-487`).
  - `--publish-prior-ccc-verification-manifest` is **required**: combine loads
    and validates it unconditionally (`crunch_combine_proof.py:508`).

There is NO `--publish-fresh-secondaries` flag; the fresh set is derived from the
rebuild-secondaries file. `--publish` also requires `--execute`
(`crunch_rebuild_orchestrator.py:2208`) and `--operator-approved-publish`
(`:2210` at parse time, `:1225` as a runtime halt).

Pinned interpreter (MACHINE-SPECIFIC -- CLAUDE.md C1; do not hardcode elsewhere):
`C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

Invocation shape (paste-ready; run in the NEW window from the project dir):

```
& "<PINNED_PYTHON>" stage9_launcher.py `
  --reuse-onepass-run-dir output/crunch_runs/20260606T053735Z `
  --target-as-of 2026-06-08 `
  --allow-network-fetch `
  --duration-budget-minutes 240 `
  --operator-budget-label "stage9-proof-publish-ihi-schg" `
  --publish-prior-fixture frontend/public/fixtures/k6_mtf_ranking.json `
  --publish-prior-promotion-manifest frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json `
  --publish-prior-validation-sidecar output/crunch_runs/20260610T221108Z/publish_candidate_samerun_ccc/composite_validation_sidecar.json `
  --publish-prior-ccc-verification-manifest output/crunch_runs/20260610T221108Z/publish_candidate_samerun_ccc/combined_ccc_sidecar_verification.json
```

(The prior-input run-dir paths point at the run that produced the CURRENT live
board; they advance each publish.)

### 2.4 The one question

The launcher displays the backlog (section 4) then asks exactly once
(`stage9_launcher.py:350`, single call site, no loop). Answer format -- comma or
space separated:

```
IHI, SCHG
```

### 2.5 Expected runtime

Today: ~2h19m end to end for 2 tickers (launch 10:55:46Z -> `promoted_at_utc`
2026-06-11T13:14:49Z), dominated by StackBuilder + k6_recook; OnePass was reused
(0 build cost, `checkpoints.onepass=reused`). The `--duration-budget-minutes 240`
guard was not hit.

### 2.6 Success markers

- launcher exits **0** (returns the orchestrator process code,
  `stage9_launcher.py:409`);
- run `status=completed_publish`, `halted_at=null`
  (`output/crunch_runs/20260611T105546Z/RUN_SUMMARY.json`);
- Stage 9 `status=published`, transaction state ends at `live_manifest_verified`
  (`output/crunch_runs/20260611T105546Z/publish_state.json`,
  `completed_states` = all nine);
- a new `promoted_at_utc` and updated source metadata on the live promotion
  manifest after Vercel deploy
  (`frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json`).

### 2.7 Standing halt rule + where refusal evidence lives

On any halt or refusal: change nothing, do NOT retry, leave the worktree and the
live site as-is, and bring the refusal evidence back for independent audit. Under
the run dir `output/crunch_runs/<RUN>/`:

- `publish_refusal.json` -- Stage 9 fail-closed envelope: stage, sanitized
  reason, `no_partial_publish=true` (`stage9_publish.py:257`).
- `publish_state.json` -- Stage 9 transaction state (last completed state).
- `09_stage9_publish.json` -- the Stage 9 summary the orchestrator writes
  (`crunch_rebuild_orchestrator.py:2007`).
- `RUN_SUMMARY.json` -- the orchestrator envelope, including `halted_at`
  (`crunch_rebuild_orchestrator.py:735,:1210,:1496,:1514`).

---

## 3. Stage map (as built)

Stages 1-4 run under `--execute` (`crunch_rebuild_orchestrator.py:1385-1473`);
Stage 5 + the Stage 9 publish tail run when `--publish` is set
(`:1481-1483`). Each stage writes a checkpoint artifact under the run dir.

| Stage | Purpose | Artifact (run dir) | Gate / source |
|-------|---------|--------------------|---------------|
| 1 OnePass | Refresh / reuse the allowed universe (master minus blocked). Reused today. | `01_onepass.json` (+ `00_onepass_reuse_proof.json`) | reused write `:1406`; fresh `:1425`; reuse proof `:748`, coverage `:910`, freshness `:950-963` |
| 2 ImpactSearch | Rebuild the requested secondaries' workbooks. | `02_impactsearch.json` | `:1439`; stage env injected `:495-502` |
| 3 StackBuilder | Re-select K=6 stacks for the rebuilt secondaries. | `03_stackbuilder.json` | `:1456` |
| 4 k6_recook | Recook the rebuilt stacks; Stage A authoritative. | `04_k6_recook.json` | `:1473`; Stage-A excluded -> refuse `:1468` |
| 5 Validation | Honest validation over the rebuilt secondaries -> sidecar. | `05_validation_sidecar.json` | `:1744,:1987`; sidecar must cover exactly the built set `:1535` |
| 6 Join | Run-id-bound k6 ranking join + fresh-row path normalize. | (in Stage 9 tail) | `_run_stage9_publish_tail` `:1978` |
| 7 Combine/proof | Assemble the merged v2 board from prior board + fresh rows; self-check. | `07_combine.json` (when produced) | combine `:1821`, `run_self_check=True` `:1836` |
| 8 Promote gate | Public promotion validators (dry-run then write). | (in Stage 9 tail) | see Stage 9 below |
| 9 Publish | Same-run CCC upload -> combine -> promote dry-run -> promote write -> commit -> push -> live verify. | `09_stage9_publish.json` | `stage9_publish.run_stage9_publish` `stage9_publish.py:1061` |

Preflight (Stage 0) writes `00_preflight.json` (`:1114`) and `RUN_SUMMARY.json`
records the final envelope. In `--publish` mode the dry-run-tail closed-boundary
flags `blob_attempted/promotion_attempted/publish_attempted` stay `false`
(today's `RUN_SUMMARY.json`) because the real Blob/promote/commit happen inside
the Stage 9 tail, not the dry-run tail.

### 3.1 Stage 9 transaction (as built)

State sequence written atomically to `publish_state.json`
(`stage9_publish.py:43` `PUBLISH_STATES`), proven end to end today:

`preflight_ok -> ccc_uploaded -> combined_ok -> report_pair_written_to_worktree ->
promote_dry_run_ok -> promote_write_ok -> commit_created -> push_ok ->
live_manifest_verified`.

- **Fail-closed envelope:** any board-level gate failure writes
  `publish_refusal.json` with `no_partial_publish=true` and stops -- no fixture
  write, no commit, no push beyond the failing step (`stage9_publish.py:257`).
  Every external/side-effect seam is wrapped so any exception becomes a sanitized
  `Stage9Error` and the envelope is always written
  (`stage9_publish.py:405` `_seam`; broad backstops in `run_stage9_publish`).
- **Output verification before commit:** the written public fixture's LF-SHA,
  the written manifest `source_sha256` / `source_run_id`, and a non-empty README
  are re-verified before any `git add`/commit (`stage9_publish.py:770`
  `verify_promote_write_outputs`).
- **Commit allowlist with rename/copy STOP:** the commit refuses if any
  out-of-allowlist tracked change is staged, and ANY rename/copy status (R*/C*)
  is an automatic STOP (`stage9_publish.py:868` `enforce_publication_allowlist`,
  `:876` "rename/copy change present ... refusing"; porcelain parsed with
  `--porcelain=v1 -z --untracked-files=all`).
- **Post-push live verify:** after push it fetches, confirms `origin/main ==
  local HEAD`, then polls the live promotion manifest until fields match. Any
  failure here is `deploy_failed_after_push=True` and takes the no-further-git
  path -- never a recommit or repush (`stage9_publish.py:971-1010`).
- **Push-only resume:** if a prior run reached `commit_created` but not
  `push_ok`, a relaunch validates the worktree is exactly the recorded publish
  commit (HEAD == recorded SHA, clean allowlist, committed file SHAs match) and
  resumes at push only -- never a second commit (`stage9_publish.py:1019`
  `_resume_commit_not_pushed`).
- **Run lock:** an `O_CREAT|O_EXCL` lock is acquired (no reclaim) and held
  through live verify or refusal (`stage9_publish.py:293,:301`).
- Git seam failures are fail-closed: unchecked `git` reads route through
  required helpers so a swallowed failure (empty stdout) cannot be read as a
  clean/valid value (the "Fail closed on swallowed git failures" hardening).

---

## 4. Launcher contract (as built)

`stage9_launcher.py` is the only interactive surface. Order of operations:

1. **Token preflight first** (`:313`, return 2) -- before the scan or the
   question; never prints the value.
2. **Universe files preflight** (before the question, fail-closed): master via
   the orchestrator's own `load_master_universe` (empty/absent -> return 4,
   `:325`); blocked via the orchestrator's own `load_symbol_file` (missing /
   empty / malformed -> `_orch.CrunchError` -> return 5, `:330`).
3. **Read-only ledger scan** (`:125` `scan_backlog`): from the live fixture and
   `output/impactsearch/<TICKER>_analysis.xlsx` workbook names it computes two
   buckets, neither ever auto-added:
   - **Bucket A "never attempted":** a workbook ticker NOT ranked and NOT
     Stage-A-disclosed (`:136`).
   - **Bucket B "previously Stage-A-excluded":** a workbook ticker NOT ranked and
     Stage-A-disclosed (`:137`). (Today: bucket A empty; bucket B AAPB, AAPU,
     CURE, DBA.)
4. **One question** (`:350`, single call site).
5. **Parse** (`:148` `parse_tickers`): split on commas/whitespace, strip,
   uppercase, dedupe preserving first occurrence; duplicates are noted.
6. **Universe parity by reuse, stricter-never-looser:** validation imports the
   orchestrator's own `normalize_ticker` / `load_master_universe` /
   `load_symbol_file` (`stage9_launcher.py:42` `import crunch_rebuild_orchestrator
   as _orch`) and derives allowed = master minus blocked exactly as the run's
   preflight does (`:183` `load_known_universe`). A typed ticker not in the
   allowed set is rejected under a fix-or-drop flow, labeled `(blocked)` if in
   the exclusion set else `(unknown)` -- return 3 (`:371`). The launcher can only
   be STRICTER than the run, never looser.
7. **NEW vs REFRESH** (`:377-378`): NEW = not currently ranked; REFRESH = already
   a board row (combine upserts it in place). Today IHI/SCHG were both REFRESH.
8. **Gitignored secondaries file:** the resolved list is written to
   `operator_inputs/crunch_rebuild_secondaries_<UTC>.txt`, covered by the
   repository-root `.gitignore` `*.txt` rule (`stage9_launcher.py:65`
   `GITIGNORE_RULE = "*.txt (repository-root .gitignore)"`).
9. **Launch:** argv composed with ABSOLUTE paths for both the orchestrator script
   and the secondaries file (repo_root resolved to absolute, `:303`), and the
   subprocess runs with `cwd=repo_root` so the orchestrator resolves `output/`
   and other relative paths correctly from any caller cwd
   (`stage9_launcher.py:226-231,:401`). Argv array only; never `shell=True`.

**Exit codes (verified in source):** 0 = orchestrator passthrough (`:409`);
2 = token absent/blank; 3 = unknown/blocked ticker; 4 = master universe
empty/absent; 5 = blocked file missing/malformed; otherwise the orchestrator's
own exit code.

---

## 5. Public provenance (what updates each publish)

All under `frontend/public/fixtures/`, written LF-normalized by the promote
helper (`utils/react_publish/promote_k6_mtf_artifact.py`):

- **`k6_mtf_ranking.json`** -- the slim v2 board the React app fetches. Refreshed
  rows are re-stamped with the new `validation_run_id`; carried rows keep their
  prior data era; 207 rows, no duplicate secondary.
- **`k6_mtf_ranking.promotion_manifest.json`** -- provenance manifest
  (`_build_manifest` `:1864`). Per-publish updates demonstrated today:
  - `promoted_at_utc` -> `2026-06-11T13:14:49Z` (`:2231,:2250`);
  - `source_run_id` -> `20260611T105546Z`;
  - `source_sha256` -> the new fixture LF-SHA
    `1bc633863b1b7552c94440f86ee534a4db9c989127302a022e660eb9624f1b84` (`:2244`);
  - `ccc_series_storage.verification_manifest_sha256` ->
    `658b2fd98fe2e2a969c6358161ab89f6205ce3ac0410fb22f169020b9e160814`;
  - `ccc_series_storage.sidecar_prefixes` -- **supersede-in-place**: only the two
    refreshed sidecars were re-uploaded under the new run prefix; the 205 carried
    sidecars kept their original prefix. Today's manifest carries
    `sidecar_prefix=null` plus itemized prefixes: 205 under
    `k6-mtf/20260604T110400Z_recook_full248_clean_csv/ccc-series/` + 2 under
    `k6-mtf/20260611T105546Z/ccc-series/`, total `sidecar_count=207`
    (`_derive_ccc_storage_summary` `:1667`).
- **`README.md`** -- regenerated deterministically from the manifest + fixture at
  `--write` time, LF (`promote..._artifact.py:1961` render, `:2265-2269` write).
  Today's publish commit rewrote it with zero hand edits.
- **`md_library/shared/2026-06-11_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_207.md`**
  (+ paired `.manifest.json`) -- the honest validation report, regenerated and
  bound by SHA into the promotion gate; referenced by
  `promotion_manifest.validation_results.phase_5_validation_report_path` /
  `_sha256`.

---

## 6. Known limitations as of this doc (and scoping-doc divergences)

- **Freshness deferred to Re-rank.** The published series end is bound by the
  reused library era, not `--target-as-of`. Today both refreshed rows end at
  `history_as_of_date=2026-06-04`, `ccc_series_last_date=2026-06-03` -- the
  reused OnePass / library data end -- and the 205 carried rows are older still.
  A current-to-today whole-board refresh needs the Re-rank driver (out of scope).
- **Build stages are all-or-nothing per run.** Stages 1-4 run over the whole
  rebuild set and any stage failure hard-stops the run; per-ticker quarantine and
  a per-ticker resume ledger are NOT implemented. Only the Stage 9 tail has a
  transaction resume (push-only, section 3.1). DIVERGENCE: scoping doc sections
  2.4 and 2.6 describe a per-ticker ledger / quarantine as the design; as-built
  that does not exist yet.
- **Backlog inclusion is manual by design.** Neither bucket is ever auto-added;
  the operator types each ticker (section 4).
- **DIVERGENCE -- Stage-A disclosure supersede is FIXED (scoping doc section (c)
  listed it OPEN).** As-built, a secondary that is a ranked row -- freshly
  rebuilt OR carried -- is dropped from the carried `stage_a_excluded_secondaries`
  disclosure via a dict/string-aware extractor
  (`crunch_combine_proof.py:664-676`, `_stage_a_secondary` `:186-193`). The fix
  also self-heals an already-published board: today's run dropped IHI and SCHG
  from disclosure, taking the count 43 -> 41 (verified in the live fixture).
- **DIVERGENCE -- no `--publish-fresh-secondaries` flag.** Scoping doc section 2.2
  references one; as-built the fresh set derives from `--rebuild-secondaries-file`
  and no such flag exists in the parser.
- **Prereqs (a) manifest-driven schema test and (b) promote-generated README are
  now landed on `main`** (scoping doc section 3 listed them as pending merge).
  Today's run regenerated the README and validation report with zero hand edits.

---

## 7. Pointers

- Scoping / preflight: `md_library/shared/2026-06-11_STAGE9_AUTONOMOUS_PUBLISH_SCOPING.md`.
- Source of record:
  - `stage9_launcher.py` -- the one-question launcher.
  - `stage9_publish.py` -- the Stage 9 fail-closed publish transaction.
  - `crunch_rebuild_orchestrator.py` -- Stages 0-9 orchestration, reuse gate,
    execute gates, Stage 9 wiring.
  - `crunch_combine_proof.py` -- prior-board + fresh-row assembly, carry-forward,
    Stage-A supersede.
  - `utils/react_publish/promote_k6_mtf_artifact.py` -- public fixture / manifest
    / README write (LF, fail-closed, never deploys).
- Test suites (current counts, all green on this branch, 2026-06-11):
  - `test_scripts/test_stage9_launcher.py` -- 28
  - `test_scripts/test_stage9_publish.py` -- 43
  - `test_scripts/test_crunch_combine_proof.py` -- 73
  - `test_scripts/shared/test_k6_mtf_fixture_schema.py` -- 15
  - `test_scripts/shared/test_react_publish_promote_k6_mtf_artifact.py` -- 201 (+1 skipped)
  - `test_scripts/test_crunch_rebuild_orchestrator.py` -- 148
  - Aggregate: 508 passed, 1 skipped.
