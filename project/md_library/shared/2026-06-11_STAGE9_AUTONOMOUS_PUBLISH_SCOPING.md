# Stage 9 -- Autonomous Build-and-Rank Publish (Phase Scoping / Preflight / Operator Runbook)

- **Status:** SCOPING (this doc is the Stage 9 preflight per project convention)
- **Authored:** 2026-06-11
- **Base commit:** main @ 30a9799345c94742439bc99707d93565c8baa89f
- **Reference run:** 20260610T221108Z -- first full publish, completed tonight
  (Stages 1-8 unattended; publish tail hand-run by the operator).
- **Live board after reference run:** 207 secondaries (205 carried + 2 fresh
  IHI/SCHG), promoted to prjct9.com and verified.

ASCII-only. Use `--`, `->`, `[OK]`, `[FAIL]`. No Unicode. The pinned interpreter
(CLAUDE.md PART C1) is shown below as `<PY>`.

---

## 0. Purpose, scope, and the autonomy boundary

Tonight a full public board update was produced. The ORCHESTRATOR ran Stages 1-8
unattended (build + publish-dry-run), then a human hand-ran the entire publish
tail. Stage 9 makes the orchestrator perform that tail ITSELF when launched with
explicit publish flags -- one launch, zero intermediate human action, all the way
to the live site -- fail-closed at every board-level gate.

**Scope discipline (Operator Req 9).** The 41-secondary batch is NOT run now.
The phase proof is ONE tiny hands-off publish (an IHI/SCHG refresh). The daily
autonomous re-rank is the NEXT phase and reuses this publish tail wholesale, so
Stage 9 is designed to be callable by a re-rank driver with a carried-set-empty
board. Bias every choice toward the smallest change set that achieves autonomy.

### Autonomy boundary (load-bearing)

CLAUDE.md PART B2 hard-denies a **Claude Code interactive session** from writing
the committed public fixture, uploading Blob, or committing/pushing
publication-class work ("NOT overridable inside a Claude Code session"). That
binds the assistant session. It does **not** bind the **orchestrator program**
the OPERATOR launches with `BLOB_READ_WRITE_TOKEN` in the process environment and
a non-interactive git credential available. Stage 9 is operator-launched program
behavior. Authoring/reviewing Stage 9 (read-only + docs) is valid Claude Code
work; running `--publish` is an operator action. See Open Decision 2 for the one
CLAUDE.md clarification this warrants.

---

## 1. Reference runbook (tonight's proven manual sequence)

Operator fallback AND the spec Stage 9 automates. Prior-board inputs are
abbreviated `<PRIOR_FIXTURE>`, `<PRIOR_PROMO>`, `<PRIOR_VSIDECAR>`, `<PRIOR_CCC>`
(the four live committed/prior artifacts). `<RUN>` = the run id;
`<CANDIDATE>` = `output/crunch_runs/<RUN>/publish_candidate_samerun_ccc/`.

### Step A -- Build + Stages 1-8 (already automated)

```
<PY> crunch_rebuild_orchestrator.py --execute --publish-dry-run \
  --reuse-onepass-run-dir output/crunch_runs/20260606T053735Z \
  --rebuild-secondaries-file operator_inputs/crunch_rebuild_secondaries_TEST.txt \
  --target-as-of 2026-06-08 --duration-budget-minutes 240 \
  --operator-budget-label "publish-dry-run-with-ccc" --allow-network-fetch \
  --publish-prior-fixture <PRIOR_FIXTURE> \
  --publish-prior-promotion-manifest <PRIOR_PROMO> \
  --publish-prior-validation-sidecar <PRIOR_VSIDECAR> \
  --publish-prior-ccc-verification-manifest <PRIOR_CCC> \
  --publish-dry-run-fresh-ccc-records-file <SOME_RECORDS.json>
```

- Gate: RUN_SUMMARY.json `status=completed_publish_dry_run`, `halted_at=null`;
  `07_combine.json combine_called=true`; `08_publish_gate.json status=ok` with
  every closed-boundary flag true. Stage-7 fresh-CCC requirement enforced at
  crunch_rebuild_orchestrator.py:1757-1778; the would-be tail is described by
  `would_be_publish_plan` at crunch_rebuild_orchestrator.py:1559-1565.
- Output of record: `output/k6_mtf/<RUN>/k6_mtf_ranking.json` (v1, inline
  `ccc_series`), `output/crunch_runs/<RUN>/05_validation_sidecar.json`, the
  dry-run `publish_candidate/`. Tonight this candidate referenced a stale
  (different-run) records file; Steps B-C correct it to same-run records.

### Step B -- Same-run CCC upload (two beats; crosses the Blob boundary)

Validate-only (no Blob):
```
<PY> fresh_ccc_blob_upload.py --k6-ranking output/k6_mtf/<RUN>/k6_mtf_ranking.json \
  --secondaries IHI,SCHG --output output/crunch_runs/<RUN>/fresh_ccc_records.json
```
- Gate: `status=validate_only`, `blob_client_constructed=false`,
  `records_written=false`, `would_upload_count` == fresh count
  (fresh_ccc_blob_upload.py:255-266).

Confirm (PUT + GET-verify + write bare records list):
```
<PY> fresh_ccc_blob_upload.py --k6-ranking output/k6_mtf/<RUN>/k6_mtf_ranking.json \
  --secondaries IHI,SCHG --output output/crunch_runs/<RUN>/fresh_ccc_records.json \
  --confirm-blob-upload
```
- Gate: `status=uploaded`, each record `get_verified=true`. Sidecar run id comes
  from the artifact's own `run_id` (fresh_ccc_blob_upload.py:240,273);
  `--ranking-run-id` is only a guard (fresh_ccc_blob_upload.py:241-244). Output is
  a bare records list (fresh_ccc_blob_upload.py:277) accepted by the orchestrator
  loader (crunch_rebuild_orchestrator.py:1579-1594). Requires
  `BLOB_READ_WRITE_TOKEN` in env (read lazily at PUT time; never logged).

### Step C -- Offline combine reassembly with same-run records

Reuses the real functions (no copies): load `05_validation_sidecar.json` + file
sha -> `load_and_build_k6_mtf_ranking_v2(<RUN> k6 ranking, sidecar, sha)` ->
orchestrator `_normalize_publish_fresh_row_paths`
(crunch_rebuild_orchestrator.py:1645-1691) -> `combine_and_assemble(...,
run_self_check=True)` -> `<CANDIDATE>` (combine call shape mirrors
crunch_rebuild_orchestrator.py:1784-1801).
- Gate: COMBINE_OK; merged_row_count == prior + net_new; `promote_self_check`
  all three validators pass; fresh rows reference same-run `k6-mtf/<RUN>/ccc-series/`
  pathnames; board-wide `walk_forward_n_folds` null; exclusion scan clean.

### Step D -- Triple pre-promote audit (read-only)

Direct-import the unchanged promote validators against `<CANDIDATE>`:
`validate_k6_mtf_ranking_v2_payload(for_public_promotion=True)`,
`verify_v2_promotion_binding(...)`, `validate_ccc_verification_against_fixture(...)`
(promote_k6_mtf_artifact.py:640, :946, :1540). Confirm counts/metadata, same-run
CCC equality, live-board diff (exactly the intended additions; carried rows
unchanged except rank + validated_as_of_utc), exclusion clean, no leak/Mode-B
violations.

### Step E -- Commit the Phase-5 report pair to md_library/shared

Copy `<CANDIDATE>/composite_phase5_report.md` byte-identically to
`md_library/shared/2026-06-11_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_207.md`
(verify sha); copy the paired manifest to the matching `...207.manifest.json`
with `report_path` updated to that committed path. promote binds `report_path`
only by path-privacy + allowed prefix (`_ALLOWED_MANIFEST_PATH_PREFIXES =
("output/","md_library/","frontend/")`, promote_k6_mtf_artifact.py:854,
:1022-1029) and binds the report by sha (promote:1031-1036). It does NOT require
the `--phase5-report` arg path to equal `manifest.report_path`. `.gitattributes`
pins these report files to LF (.gitattributes:22-23).

### Step F -- promote dry-run (public gate, no write)

```
<PY> utils/react_publish/promote_k6_mtf_artifact.py \
  --source <CANDIDATE>/merged_k6_mtf_ranking_v2.json \
  --validation-sidecar <CANDIDATE>/composite_validation_sidecar.json \
  --validation-sidecar-sha256 <SIDE_SHA> \
  --phase5-report md_library/shared/2026-06-11_..._207.md --phase5-sha256 <REPORT_SHA> \
  --phase5-report-manifest md_library/shared/2026-06-11_..._207.manifest.json \
  --ccc-sidecar-verification-manifest <CANDIDATE>/combined_ccc_sidecar_verification.json \
  --public
```
- Gate: `dry_run=true`, `wrote_destination=false`, `per_secondary_count` ==
  board size, `source_sha256` == fixture LF sha. v2 public path requires all
  binding inputs (promote_k6_mtf_artifact.py:2026-2061) and NEVER uploads --
  it validates the supplied CCC manifest only (:2079-2098).

### Step G -- promote write (crosses the public-fixture boundary)

Same command + `--write --operator-approved`.
- Gate: `wrote_destination=true`, `wrote_manifest=true`. Writes ONLY
  `frontend/public/fixtures/k6_mtf_ranking.json` and `...promotion_manifest.json`,
  LF-normalized before hashing/writing (promote:2124-2132). `--write` refuses
  without `--operator-approved` (promote:2004-2008). The manifest's
  `ccc_series_storage` is honest for mixed-prefix carry-forward boards
  (`sidecar_prefix=null` + itemized `sidecar_prefixes`,
  promote._derive_ccc_storage_summary:1667+).

### Step H -- Commit (publication-class)

Steady-state publication file set (code already landed): the two fixtures, the
README, and the two committed-report files:
```
frontend/public/fixtures/k6_mtf_ranking.json
frontend/public/fixtures/k6_mtf_ranking.promotion_manifest.json
frontend/public/fixtures/README.md
md_library/shared/<DATE>_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_<N>.md
md_library/shared/<DATE>_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_<N>.manifest.json
```
(Tonight also carried the mixed-prefix code fix + schema-test update because they
shipped in the same commit; steady state does not.)

### Step I -- Push + deploy + verify

`git push origin main`. Remote is HTTPS
(`https://github.com/peterkitch/spy-project.git`), authenticated non-interactively
by Git Credential Manager (`credential.helper=manager`) -- why tonight's push
needed no prompt. Vercel auto-deploys on push to main.
- Post-push verify: `git fetch origin main`; local HEAD == origin/main; GET the
  LIVE `https://prjct9.com/fixtures/k6_mtf_ranking.promotion_manifest.json` and
  confirm `source_sha256`, `per_secondary_count`, `sidecar_prefix=null` +
  `sidecar_prefixes`, and `report_path` match the committed manifest.

---

## 2. Launcher + Stage 9 design

### 2.1 The single-question launcher (Operator Reqs 3, 5, 8)

A thin launcher script is the ONLY interactive surface. It asks EXACTLY ONE
question and then runs hands-off:

```
Which tickers to build/refresh? (comma list)
  [backlog detected -- ImpactSearch workbooks with no board row: <list or none>]
> IHI, SCHG
```

In one interaction the launcher:
1. **Surfaces backlog (Req 8; policy):** runs the ledger scan (2.4) and displays
   the backlog -- ImpactSearch workbooks that are NOT ranked board rows -- in two
   labeled buckets so the operator can choose to include them inside the single
   launch question:
   - bucket A "never attempted": a workbook with NO board presence at all
     (currently EMPTY);
   - bucket B "previously Stage-A-excluded": a workbook present only in
     `stage_a_excluded_secondaries` (currently AAPB, AAPU, CURE, DBA), flagged
     `stage_a_disclosed=true`; these likely need member/data fixes before they
     can rank.
   NEITHER bucket is ever silently auto-added.
2. **Validates input (Req 5):** normalizes the typed list and checks each symbol
   against the known universe (the allowed-universe file the orchestrator already
   derives: master tickers minus the exclusion set) BEFORE any heavy compute.
   Unknown/misspelled symbols are reported at the prompt for fix-or-drop; the run
   does not start with an invalid symbol.
3. **Classifies new vs refresh (Req 4):** partitions the validated list into NEW
   (not currently on the board) vs REFRESH (already a board row) and prints the
   counts in the launch summary. Refresh is handled by combine's existing
   supersede: `carried_secs = {s for s in prior_by_sec if s not in fresh_by_sec}`
   (crunch_combine_proof.py:394) and the fresh upsert at :528-537 -- a fresh row
   for an existing secondary SUPERSEDES the carried one; it is never duplicated.
4. Hands the validated set to the orchestrator `--publish` run. Zero interaction
   afterward (Req 1).

### 2.2 Flags

- `--publish` : opt into the full tail (build + Steps B-I).
- `--operator-approved-publish` : REQUIRED companion for the real write + commit
  + push (mirrors promote's `--write` needing `--operator-approved`). Without it,
  `--publish` runs through the dry-run gate (Steps A-F) and stops.
- `--publish-fresh-secondaries IHI,SCHG` : the validated fresh set (from the
  launcher); asserted equal to the rebuild set and the CCC records.
- Reuse existing `--publish-prior-*` inputs and `--rebuild-secondaries-file`.

### 2.3 Preflight (fail-closed; all pass BEFORE any work -- Req 1, 2)

1. On `main`; tracked worktree clean (no uncommitted publication files).
2. `BLOB_READ_WRITE_TOKEN` present in env (presence check only; never printed).
   HALT before any build if absent (Req 2).
3. Non-interactive git credential available: probe `git ls-remote origin` (no
   mutation) so the later push cannot block on a prompt.
4. The four `--publish-prior-*` inputs exist and bind (prior fixture LF sha ==
   prior promo `source_sha256`; prior sidecar sha == prior fixture
   `validation_metadata.artifact_sha256`).
5. The ticker list validated against the universe (2.1.2).

### 2.4 Per-ticker ledger + crash resume (Req 7)

A per-ticker state ledger `output/crunch_runs/<RUN>/build_ledger.json` records,
for each requested ticker, which artifacts exist:

- **impactsearch** -> `output/impactsearch/<TICKER>_analysis.xlsx` (+ `.manifest.json`)
- **stackbuild** -> `output/stackbuilder/<TICKER>/selected_build.json`
- **k6 row** -> `output/k6_mtf/<RUN>/<TICKER>/` and the row in
  `output/k6_mtf/<RUN>/k6_mtf_ranking.json`
- **ccc record** -> the ticker present in `fresh_ccc_records.json` with
  `get_verified=true`
- **board row** -> present in `<CANDIDATE>` merged fixture / published board

These artifact paths ARE the cheap source of truth (per-ticker, content-addressed
where it matters). On relaunch the orchestrator scans the ledger and builds ONLY
tickers missing/incomplete a stage; a 20-of-41 crash does not redo the 20. Note
the existing run checkpoints (`00_preflight` .. `08_publish_gate`) are RUN-level,
not per-ticker, so the per-ticker ledger is NEW (small) but reads only existing
artifacts. Because Blob PUT is immutable + content-addressed, a resumed CCC step
safely reuses an already-uploaded sidecar (`reused=true`).

### 2.5 In-run ordering (the critical sequencing rule)

Generate + upload SAME-RUN CCC records BEFORE combine, covering EVERY fresh
secondary:

1. Build Stages 1-4 PER TICKER with quarantine (2.6).
2. Validate (Stage 5).
3. **CCC two-beat (Step B)** -> `fresh_ccc_records.json`. ASSERT the records
   cover the full surviving fresh set; a gap HALTS before combine.
4. Join + normalize + **combine** (Step C) using those records -> one canonical
   `<CANDIDATE>` (the same-run-first ordering removes tonight's double candidate
   dir).
5. Self-check in combine (`run_self_check=True`) -> all promote validators pass.
6. Copy report pair to md_library/shared, rewrite `report_path`, verify sha (Step E).
7. **promote dry-run gate** (Step F) -> must pass.
8. **promote write** (Step G) under `--operator-approved-publish` -- which also
   regenerates the README (Prereq b).
9. **Commit** the exact publication file list (Step H).
10. **Push** with the standing fail-safe (2.7).
11. **Post-push verification** (Step I).

### 2.6 Failure isolation: per-ticker quarantine vs board-level halt (Req 6)

ONE ticker failing mid-build is QUARANTINED and reported; the rest continue and
publish. Only board-level integrity failures halt the whole run with NO partial
publish. Gate classification of the EXISTING machinery:

PER-TICKER (Stage 9 quarantines; build the rest):
- ImpactSearch build of a secondary (Stage 2, crunch_rebuild_orchestrator.py:1406-1418).
- StackBuilder build of a secondary (Stage 3, :1421-1435).
- k6_recook Stage-A *allowable* dependency unavailability for a secondary
  (Stage 4) -- quarantine-able, but ONLY via `--allow-stage-a-exclusions` or a
  per-ticker invocation. k6_recook classifies the allowable kinds and gates them
  on that flag (k6_recook.py:2436-2495; allowable-kind constants :113-114).
- Validation of a secondary (Stage 5).
- CCC upload of a secondary (Step B).

BOARD-LEVEL (must HALT, no partial publish):
- k6_recook BLOCKING Stage-A unavailability -- network / provider / systemic
  (retry exhaustion, empty / no-close payload, systemic worker error). This
  ALWAYS halts Stage A, even under `--allow-stage-a-exclusions`
  (k6_recook.py:2427-2431; systemic-always-halt constants :96-100; classifier
  `_classify_stage_a_outcome` :754).
- Combine methodology lock (crunch_combine_proof.py:426-440).
- Combine exclusion scan (crunch_combine_proof.py:790-793).
- Prior fixture <-> promotion-manifest SHA binding (crunch_combine_proof.py:343-349).
- Prior sidecar <-> fixture SHA binding (crunch_combine_proof.py:459-478).
- Prior CCC manifest validation (crunch_combine_proof.py:1000).
- combine self-check / promote validators (run_self_check=True).
- promote dry-run gate + promote write.
- git push + post-push verification.

CURRENT-STATE GAP (must change for Req 6, allowable class only): today the build
is BATCHED and all-or-nothing. `_run_execute` runs
ImpactSearch/StackBuilder/k6_recook over the whole `--secondaries` set and
`_require_ok` (crunch_rebuild_orchestrator.py:1220-1225) makes ANY stage failure
a whole-run hard STOP; and k6_recook runs WITHOUT `--allow-stage-a-exclusions`,
so even an *allowable* per-ticker Stage-A unavailability is a hard STOP today
(crunch_rebuild_orchestrator.py:1077-1078). Stage 9's per-ticker isolation changes
exactly that -- for the ALLOWABLE class ONLY; the blocking / systemic class stays
a board-level halt. A single allowable-failing ticker is dropped to a quarantine
list (written to the run dir, mirroring the existing prior-artifact quarantine
pattern
`quarantine_paths`/`_quarantine_impactsearch`/`_quarantine_stackbuilder`,
crunch_rebuild_orchestrator.py:461,:1871,:1891) and the surviving set proceeds.
The fresh-secondary set passed to combine is then the SURVIVORS, and the
new-vs-refresh / coverage assertions key off survivors.

### 2.7 Fail-closed envelope + push fail-safe

Any board-level gate failure halts with NO partial publish: no fixture write, no
commit, no push. Write `output/crunch_runs/<RUN>/PUBLISH_REFUSAL.json`
{stage, reason, quarantined_tickers, local_head, remote_head_if_known,
no_partial_publish:true}. Routed through the existing `_halt`
(crunch_rebuild_orchestrator.py:720) envelope. On any push error: HALT; report
local HEAD and (if discoverable without mutation) origin/main; emit the refusal
envelope; do NOT retry/force/force-with-lease or reconfigure credentials/GPG.

### 2.8 In-process vs subprocess

Both tail tools expose in-process entry points, so Stage 9 can call them directly
(cleaner error capture, no shell quoting): `upload_fresh_ccc(...)`
(fresh_ccc_blob_upload.py:221) and `promote(PromotionInputs(...))`
(promote_k6_mtf_artifact.py:1995, dataclass :135). Recommend in-process calls
with the same fail-closed handling the CLIs use.

---

## 3. Prerequisites (blocking unattended operation)

### (a) Manifest-driven schema test [BLOCKING; NOT yet in flight]

`test_scripts/shared/test_k6_mtf_fixture_schema.py` hard-codes the board's facts
as literals (EXPECTED_RUN_ID, EXPECTED_*_SHA, counts, the CCC totals, the prefix
list, the report path, the verification-manifest path -- constants block near
:29-71) and reads the live fixture (:20) + manifest (:21). Every publish needs a
hand edit (done by hand tonight). Amendment: derive expectations from the live
fixture + manifest and assert internal consistency (fixture LF sha == manifest
`source_sha256`; each row prefix in `sidecar_prefixes`; counts == summary; report
sha == manifest report sha); keep only true invariants hard-coded (schema
version, host allowlist, Mode-B no-inline-ccc / no-OHLCV).

### (b) promote-generated README [BLOCKING; NOT yet in flight]

`frontend/public/fixtures/README.md` restates volatile facts (run id,
generated/promoted times, count, fixture sha + size, CCC totals + mixed-prefix
list, verification-manifest path + sha, report paths, validation sidecar path +
sha). Hand-edited tonight, including re-syncing `promoted_at_utc` to the
just-written manifest. Amendment: `promote()` writes/refreshes README.md from the
manifest + fixture at `--write` time (it already computes every value).
`.gitattributes:30` pins README.md to LF, so generation must emit LF.

> STATE CHECK (updated): prerequisites (a) and (b) are now IMPLEMENTED on branch
> `selfconsistent-publish-test-and-readme` at b385ce9 (3 files: the manifest-driven
> `test_k6_mtf_fixture_schema.py`, the `promote_k6_mtf_artifact.py` README
> generator, and its hermetic tests; suites green at 15 passed and 201 passed /
> 1 skipped). They are PENDING independent audit and a code-only merge to `main`
> (still 30a9799). When this doc was first scoped the branch carried no
> implementation diff; this corrects that record. The Stage-A filter fix in (c)
> remains OPEN.

### (c) Additional findings inspection surfaced

- **Stage-A disclosure supersede bug [data hygiene].**
  `stage_a_excluded_secondaries` entries are DICTS (keyed `secondary`), but
  combine filters them with `_norm_ticker(s)` which stringifies the whole dict
  (crunch_combine_proof.py:653-655), so a superseded secondary is never removed.
  On the LIVE board, IHI and SCHG appear in BOTH `per_secondary` (ranked,
  board_validated) AND `stage_a_excluded_secondaries` -- a contradictory
  disclosure. Fix: filter by the entry's `secondary` field (string or dict).
  Small, but it compounds every carry-forward publish and should land before
  autonomous runs.
- **Single canonical candidate dir.** Same-run-first ordering (2.5) must produce
  exactly one candidate dir and select it unambiguously for promote (tonight made
  two).
- **Fresh-CCC coverage assertion.** combine must HALT if the records miss any
  surviving fresh secondary (today guaranteed by hand).
- **Deterministic report filename.** Stage 9 derives `<DATE>_..._<N>.md` from run
  date + board size and must confirm it still matches the `.gitattributes`
  REPORT_ LF pattern (:22-23).

---

## 4. Open operator decisions (with recommendations)

### Decision 1 -- Token + git-credential storage (Req 2)

Two credentials: the Blob token and the git push credential.
- **Blob token -- recommend a user-level environment variable**
  (`setx BLOB_READ_WRITE_TOKEN <value>` once). Rationale: the program already
  reads it lazily from `os.environ` at PUT time; an env var is inherited
  automatically with zero extra code and never typed per run. Windows Credential
  Manager is marginally more secure but requires the program to call the
  credential API to retrieve it (more code, more failure surface) -- defer it to
  the scheduled-task phase, where no interactive session exists.
- **Git push -- recommend the existing Git Credential Manager**
  (`credential.helper=manager`), which already stored the GitHub credential and
  served tonight's push non-interactively. The launcher preflight probes
  `git ls-remote origin` to confirm it is non-interactive BEFORE any work.
- Both are PRESENCE-preflighted; absence HALTS before build. (Security note:
  a user env var is readable by any process of that user -- acceptable on a
  single-operator dev box; revisit for multi-user / scheduled contexts.)

### Decision 2 -- Program-performed commit/push vs emit one command

**Recommend program-performed.** The operator-launched orchestrator commits and
pushes itself (with the 2.7 fail-safe), because a single launch -> live board is
the entire acceptance goal and the push is the lowest-risk step (authenticates
non-interactively or halts cleanly). **CLAUDE.md clarification needed:** add one
sentence to PART B2 stating B2 binds the Claude Code interactive session (the
assistant must not perform publication-class writes/commits/pushes), but does NOT
bind an operator-launched program (the Stage 9 orchestrator) running with
`BLOB_READ_WRITE_TOKEN` and a non-interactive git credential in its environment.
This removes the apparent conflict between "automate the tail" and
"publication-class work routes outside Claude Code." (Alternative human-in-loop:
stop after Step F and print Steps G-I; rejected as default -- fails Req 1.)

### Decision 3 -- Data-freshness policy for Build-and-Rank runs

Tonight's board is anchored to early June, NOT current. Observed lag: fresh
IHI/SCHG series end **2026-06-03** (history_as_of 2026-06-04) under
**--target-as-of 2026-06-08**, because the run REUSED OnePass from
20260606T053735Z and ImpactSearch trusted the existing signal library
(`IMPACT_TRUST_LIBRARY=1`, `IMPACT_TRUST_MAX_AGE_HOURS=720`); the library pkl was
built 2026-06-06 with `date_range_end=2026-06-04`, so the data end -- not
target-as-of -- was binding (the raw price cache held through 2026-06-08). A
global 6-member stack also bounds to the close-INTERSECTION of its members, so a
deep multi-member stack ends at the earliest common close. Carried 205 rows are
older still.

**Recommend:** for a PUBLISHED Build-and-Rank run, require a fresh OnePass (no
`--reuse-onepass-run-dir`) OR a short reuse window (<= 24h) AND
`IMPACT_TRUST_LIBRARY=0` (or a short max-age) so the library refreshes to the
latest close; derive `--target-as-of` from the latest available close
(today-minus-one-trading-day) rather than a typed date; and DISCLOSE the
close-intersection / next-close trim in the published methodology so "as-of" is
never overstated. Mark reuse-mode boards as non-current. Refreshing only the
rebuilt tickers does NOT refresh carried rows -- a fresh WHOLE board needs the
re-rank driver (Out of Scope), so Build-and-Rank publishes must label carried-row
staleness explicitly.

---

## 5. Acceptance criteria

1. ONE command + a stored token + the single "which tickers?" answer -> the live
   prjct9.com board is updated, with ZERO intermediate human action afterward.
2. ONE ticker failing mid-run is quarantined and reported; the remaining tickers
   still build and publish (only board-level integrity failures halt the run).
3. Killing the process mid-run and relaunching resumes from the per-ticker ledger
   WITHOUT redoing completed tickers (a 20-of-41 crash does not redo the 20).
4. After publish, `test_k6_mtf_fixture_schema.py` and the promote test suite are
   GREEN and the README is correct, with ZERO hand edits (Prereqs a + b).
5. Every board-level gate is fail-closed: any failure -> NO partial publish + a
   written `PUBLISH_REFUSAL.json`; an interrupted run is safely re-runnable
   (immutable sidecar reuse, no duplicate fixture write).
6. Post-push verification passes automatically (HEAD compare + live-manifest GET
   field match).
7. Proof exercise: a tiny IHI/SCHG refresh -- NOT the 41-batch.

---

## 6. Out of scope (logged for the next phase -- do not lose)

- **Re-rank driver (carried-set-empty):** refresh ALL rows; the daily phase calls
  this same Stage 9 publish tail with an empty carried set.
- **Daily / headless scheduler:** unattended cadence that invokes Stage 9.
- **Scheduled-task credential story:** secure provisioning of the Blob token and
  git credential for an unattended (no interactive session) scheduler -- where
  Windows Credential Manager likely wins over a user env var.
- **Full-board validation-scope decision:** whether/when to re-validate the whole
  board vs carry forward.
- **The 41-secondary batch publish itself** (board -> ~246-248) -- run AFTER the
  tiny proof passes.

---

## Appendix -- Inspection citations (load-bearing)

- Orchestrator: publish tail (Stages 5-8) :1693-1819; Stage-7 fresh-CCC gate
  :1757-1778; combine call :1784-1801; fresh-CCC loader :1579-1594; fresh-row path
  normalization :1645-1691; `would_be_publish_plan` :1559-1565; `_halt` :720;
  `_require_ok` hard-STOP :1220-1225; Stage-A hard-stop note :1077-1078; batched
  `_run_execute` :1358 (Stage 2 :1406-1418, Stage 3 :1421-1435); quarantine
  helpers `quarantine_paths` :461, `_quarantine_impactsearch` :1871,
  `_quarantine_stackbuilder` :1891; publish CLI flags :2018-2032.
- fresh_ccc_blob_upload.py: `upload_fresh_ccc` :221; validate-only :255-266; bare
  records write :277; `--ranking-run-id` guard :241-244; sidecar run id from
  artifact :240,:273; CLI :305-335.
- promote_k6_mtf_artifact.py: `PromotionInputs` :135; `promote()` :1995 (v2 public
  binding path :2026-2061, never-uploads CCC verify-only :2079-2098, write
  :2124-2132, write-needs-approval :2004-2008); `verify_v2_promotion_binding` :946
  (report path-privacy :1022-1029, report sha bind :1031-1036, allowed prefixes
  :854); `_build_manifest` :1864 (report path :1883); `_derive_ccc_storage_summary`
  mixed-prefix :1667; parser `_build_parser` :2155-2255.
- combine supersede: `carried_secs` :394; fresh upsert :528-537; stage_a filter
  bug :653-655; methodology lock :426-440; exclusion scan :790-793; prior-fixture
  SHA :343-349; prior-sidecar SHA :459-478; prior CCC manifest :1000.
- test_k6_mtf_fixture_schema.py: live inputs FIXTURE_PATH :20 / MANIFEST_PATH :21;
  hard-coded constants :29-71.
- .gitattributes: `*.py` LF :2; report .md/.manifest.json LF :22-23;
  fixture/manifest/README LF :28-30.
- Git auth: remote `https://github.com/peterkitch/spy-project.git`;
  `credential.helper=manager` -> non-interactive push.
- Per-ticker artifact layout (read-only scan): impactsearch =
  `output/impactsearch/<TICKER>_analysis.xlsx` (+ `.manifest.json`), FLAT files (14
  workbooks present); stackbuilder = `output/stackbuilder/<TICKER>/` per-ticker
  dirs (250, incl noise `X` and `_PROGRESS`); k6 = `output/k6_mtf/<RUN>/<TICKER>/`.
- Backlog inventory (Req 8), read-only against current main: the 14 ImpactSearch
  workbooks are AAPB, AAPL, AAPU, AMZN, CURE, DBA, GOOGL, IHI, META, MSFT, NVDA,
  SCHG, SPY, TSLA. Ten are ranked board rows (AAPL, AMZN, GOOGL, IHI, META, MSFT,
  NVDA, SCHG, SPY, TSLA). The backlog (workbook AND not a ranked board row) splits
  into bucket A "never attempted" = EMPTY, and bucket B "previously
  Stage-A-excluded" = AAPB, AAPU, CURE, DBA (workbook + present only in
  `stage_a_excluded_secondaries`). The launcher surfaces both buckets at the launch
  question and never silently auto-adds; the ledger scan must keep surfacing any
  future backlog.
- Freshness evidence:
  signal_library/data/stable/{IHI,SCHG}_stable_v1_0_0.pkl.manifest.json
  `date_range_end=2026-06-04`, `build_timestamp=2026-06-06`;
  price_cache/daily/IHI.csv last row 2026-06-08; fixture row history_as_of
  2026-06-04, CCC last 2026-06-03.
