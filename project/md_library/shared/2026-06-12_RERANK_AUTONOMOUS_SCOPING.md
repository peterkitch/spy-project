# Re-rank -- Autonomous Nightly Whole-Board Re-score (Phase Scoping / Preflight)

- **Status:** SCOPING (this doc IS the Re-rank phase preflight, per project
  convention). Future implementation prompts are cut from it.
- **Authored:** 2026-06-12
- **Base commit:** `main` @ `029c4c8`.
- **Predecessor (proven):** Build-and-Rank shipped its first hands-off autonomous
  publish today, run `20260611T105546Z` (IHI+SCHG refresh, exit 0,
  `status=completed_publish`). As-built operations:
  `md_library/shared/2026-06-11_BUILD_AND_RANK_OPERATIONS.md`.
- **Template:** `md_library/shared/2026-06-11_STAGE9_AUTONOMOUS_PUBLISH_SCOPING.md`.

ASCII-only. Repo-relative paths. Every architectural claim about existing code is
cited `file:line`. Where this doc must propose rather than report it is labeled
**PROPOSAL** with one recommendation and rationale -- never a menu.

---

## 0. Purpose, scope, and the autonomy boundary

Build-and-Rank refreshes a few operator-chosen secondaries and inserts them
against the live board; carried rows keep their older data era
(`2026-06-11_BUILD_AND_RANK_OPERATIONS.md`, section 1). Re-rank makes **freshness
autonomous**: re-score the WHOLE board to ONE current as-of date on a nightly
cadence, with zero ticker selection (the board is the selection). It reuses the
Stage 9 publish tail and the combine primitive wholesale; the new surface is a
thin, question-free **re-rank driver** plus a scheduler.

**Autonomy boundary (unchanged from Stage 9).** CLAUDE.md PART B2 binds the
Claude Code interactive session, not an operator-launched (or scheduler-launched)
program running with `BLOB_READ_WRITE_TOKEN` and a non-interactive git credential
in its environment (B2 clarification paragraph). The re-rank driver is exactly
such a program. Authoring/reviewing it (read-only + docs) is valid Claude Code
work; running it is an operator/scheduler action.

**Scope discipline.** Bias to the smallest change set that achieves an honest
nightly whole-board re-score. The driver is a new CALLER of existing primitives;
this phase adds no new public artifact shapes and no new engines.

---

## 1. Pipeline shape (the central design question)

### 1.1 What a full nightly rebuild would cost (infeasible)

The orchestrator runs four execute stages whose argv is fixed in
`build_stage_commands` (`crunch_rebuild_orchestrator.py:513-604`):

- Stage 1 OnePass (`:530-539`): refreshes the signal library for the FULL allowed
  universe (`--tickers-file <allowed_universe>` = master minus blocked, ~37,251
  tickers). Proven fresh cost ~13.5h.
- Stage 2 ImpactSearch (`:540-561`): per-secondary primary-scoring + workbook
  build over the `--secondaries` set -- member DISCOVERY.
- Stage 3 StackBuilder (`:562-588`): K1-K12 beam search to SELECT the K=6 members
  per secondary (`--k-max 12 --search beam --beam-width 12`).
- Stage 4 k6_recook (`:589-603`): re-score the selected stacks (`--restage-all`,
  reads `--stackbuilder-root`, honors `--target-as-of`).

Today's proof: 2 tickers took ~2h19m end to end with OnePass REUSED (0 build
cost), so Stages 2-4 for 2 secondaries were ~2h -- roughly ~1h/secondary for the
discovery + selection + recook chain. Extrapolated honestly to 207 board rows:
**~207h (~8.6 days)** for Stages 2-4 alone. A full nightly rebuild of all 207 is
**infeasible** by one to two orders of magnitude, even allowing batch warm-cache
gains. Member DISCOVERY and SELECTION cannot run nightly.

### 1.2 The minimal honest re-score path (PROPOSAL)

**PROPOSAL -- the nightly re-rank runs k6_recook restage + validation only;
it does NOT run OnePass, ImpactSearch, or StackBuilder.** Rationale, grounded in
the engines:

- k6_recook reads the EXISTING `selected_build.json` per secondary
  (`k6_recook.py:124,:455-460`) and, under `--restage-all` + `--allow-network-
  fetch` + `--target-as-of`, refreshes the price data and rebuilds the per-member
  signal library it needs itself (`k6_recook.py:698-743` fetch-to-target,
  `:1124-1146` points and builds the signal library, `:1207-1235` rebuilds
  `price_cache/daily`). It therefore does not depend on Stages 1-3 having just run
  -- it re-scores the already-selected stacks against current data.
- OnePass exists for universe-wide member DISCOVERY (it feeds ImpactSearch's
  primary scoring). A re-rank that REUSES existing selections does no discovery,
  so the ~13.5h full OnePass is unnecessary nightly.
- ImpactSearch/StackBuilder exist to DISCOVER and SELECT members -- exactly the
  step a re-rank deliberately does not repeat.

So the nightly driver = **k6_recook `--restage-all` over all ~207 board
secondaries (existing `output/stackbuilder/<sec>/selected_build.json`) with
`--allow-network-fetch` and a derived `--target-as-of`, then Stage 5 validation
(or its daily metrics-only variant, section 4), then the Stage 9 publish tail.**

**Required implementation gate (do not skip).** This recommendation is inferred
from code structure, not a measured full-board run. v1 implementation MUST begin
with a measured pilot: a k6_recook-only restage of the full board confirming
(a) fresh metrics through `--target-as-of` for all rows, and (b) a wall-clock that
fits the chosen nightly window. Today's per-stage artifacts carry no wall-clock
timing (`output/crunch_runs/20260611T105546Z/04_k6_recook.json` has none), so the
recook-only cost for 207 stacks is currently UNMEASURED and must be measured
before binding a cadence.

### 1.3 The honesty boundary -- three distinct as-of dates

Because a re-rank re-scores but does not re-discover or (daily) re-validate, every
published row carries up to three different "as-of" dates that MUST be disclosed
distinctly and never conflated:

- **data-as-of / `history_as_of_date`** -- nightly (current close, section 3).
- **selection-as-of** -- the date the K=6 members were last chosen by
  StackBuilder (a periodic Build-and-Rank-style rebuild, NOT nightly). Members are
  current as of the last rebuild, not tonight.
- **`validated_as_of_utc`** -- the last full honest validation (weekly, section 4),
  NOT the nightly data date.

Honesty of each published number under nightly re-rank: the **metrics**
(capture %, sharpe, win rate, CCC series, and therefore RANK) are current; the
**member composition** is as-of the last rebuild; the **validation survivorship**
is as-of the last weekly validation. The published surface must state all three so
"as-of" is never overstated. This is the core honesty contribution of the phase.

---

## 2. Stage 9 tail + combine reuse (re-rank combine semantics)

The publish tail (`stage9_publish.py`, states at `:43`) and the combine primitive
are caller-neutral by design and are reused verbatim. The re-rank driver's combine
inputs differ from Build-and-Rank only in the fresh/carried split:

- **fresh set = the entire board; carried set = empty.** `combine_and_assemble`
  is explicitly documented for this: "Build-and-Rank passes a subset of fresh
  rows; Re-rank may pass all rows" (`crunch_combine_proof.py:308-311`). The carried
  set is `carried_secs = {s for s in prior_by_sec if s not in fresh_by_sec}`
  (`:405-406`); when the fresh set covers the whole board it is empty.
- **prior fixture always exists** (the live published board) and is still passed
  for upsert/ordering reference; defaults resolve it
  (`crunch_rebuild_orchestrator.py:1524-1527,:1529-1533`).
- **the all-fresh path is supported as built:** the prior validation sidecar is
  REQUIRED only when carried rows are present; "Prior-sidecar-absent assembly is
  allowed ONLY when there are no carried rows (all-fresh / Re-rank-style)"
  (`crunch_combine_proof.py:427-434`). With an empty carried set the re-rank does
  not depend on a prior sidecar for carry-forward provenance.

**PROPOSAL -- supply the PRIOR night's CCC verification manifest (bound to the
live board) as the manifest input, and tonight's fresh CCC as fresh records; never
conflate them.** The chain is exact and must be wired FORWARD, not backward:

- The prior validation SIDECAR may be omitted in all-fresh mode -- it is required
  only when carried rows exist (`crunch_combine_proof.py:405-434`).
- The prior CCC VERIFICATION MANIFEST is still mandatory, and it must be the one
  bound to the CURRENT live board, NOT tonight's output. Combine loads
  `prior_ccc_verification_manifest_path` unconditionally
  (`crunch_combine_proof.py:507-510`), and `_validate_prior_ccc_manifest` requires
  its `ranking_run_id` to equal the prior fixture's `run_id` and its record set to
  match the prior fixture's Blob rows exactly (`:1039-1081`). It is the PRIOR
  night's manifest -- it is never "the fresh one".
- Tonight's freshly uploaded same-run CCC records are supplied SEPARATELY as
  `fresh_ccc_records` (`crunch_combine_proof.py:397-403`), and fresh rows are
  stamped from them (`:539-544`).
- Combine then assembles the NEW combined CCC verification manifest with
  `ranking_run_id = assembly_run_id` (`crunch_combine_proof.py:789-805`), which
  becomes the PRIOR manifest the NEXT night consumes.

So each nightly run CONSUMES the live board's CCC manifest and EMITS its successor.
The driver must point `--publish-prior-ccc-verification-manifest` at the currently
published board's manifest (the prior run's output), never at tonight's run dir.
No combine code change is needed -- only correct input wiring.

---

## 3. Freshness policy

### 3.1 OnePass cadence (PROPOSAL: not nightly)

**PROPOSAL -- do NOT run a fresh full OnePass nightly.** Per 1.2, the nightly
re-score does not need universe-wide discovery; freshness comes from k6_recook's
own member-data refresh. Reserve the ~13.5h full OnePass for the PERIODIC member
rebuild (Build-and-Rank-style, e.g. weekly or on demand), which is the only step
that legitimately needs it. Rationale: a nightly 13.5h OnePass would force an
overnight build window and publish only mid-morning, and it would buy nothing for
a re-score that reuses selections. If a future decision wants member selections to
also refresh on a faster cadence, that is a rebuild-cadence decision (Decision D5),
not a nightly-re-rank requirement.

### 3.2 trust-library OFF for fresh published runs (bind it)

The Stage 9 scoping doc recommends trust-library OFF for fully fresh published
runs. As built, the orchestrator INJECTS `IMPACT_TRUST_LIBRARY=1` into the
ImpactSearch stage (`crunch_rebuild_orchestrator.py:495-502`) -- correct for
Build-and-Rank's OnePass-reuse path, but ImpactSearch does not run in the nightly
re-rank, so that injection does not apply. **PROPOSAL -- the nightly re-rank must
not trust a stale library: it relies on k6_recook with `--allow-network-fetch` and
a current `--target-as-of`, so each stack's data is fetched fresh to the target
(`k6_recook.py:698-743`).** When a PERIODIC rebuild runs (3.1), bind
`IMPACT_TRUST_LIBRARY=0` (or a short max-age) for that run so the library
re-discovers on current data. Rationale: the re-rank is freshest precisely because
it bypasses the trust-library ImpactSearch path that anchored Build-and-Rank's
published series to the reused 06-06 library era.

### 3.3 --target-as-of derivation

**PROPOSAL -- derive `--target-as-of` as the latest completed US trading close
(today-minus-one-trading-day in US/Eastern), not a typed date.** k6_recook already
defaults a target and validates the format (`k6_recook.py:52` `DEFAULT_TARGET_AS_OF`,
`:179-185`), and treats data as fresh only when the fetched end reaches the target
(`:743` `fresh_enough = new_end >= target_as_of`). The driver computes the date
from a US trading calendar at launch and passes it explicitly. Rationale:
deterministic, audit-friendly, and it makes a data shortfall fail-closed (a stack
whose data cannot reach the target is surfaced, not silently published stale).

### 3.4 Global 6-member stack close-intersection (structural honesty boundary)

A K=6 stack bounds to the close-INTERSECTION of its members: a deep multi-member
global stack ends at the EARLIEST common close, so a member in a market that
trails the US close (timezone or holiday) trims the published series to the last
common bar. The nightly target may therefore not be reachable for every global
stack on every night. **PROPOSAL -- publish each row's true `history_as_of_date`
(already per-row) and disclose the close-intersection / next-close trim in the
methodology, never asserting a uniform board as-of that overstates the trailing
global stacks.** Rationale: "one current as-of" is the GOAL, but per-row truth is
the HONEST representation; the board-level as-of must be the minimum (most
conservative) common date or explicitly disclosed as a per-row field.

---

## 4. Validation cadence (decision: daily metrics, weekly validation)

**DECISION -- adopt the hybrid: a daily metrics/data refresh and a weekly full
honest validation.** What each publish may claim:

- **Daily re-rank** refreshes data-as-of and the metrics/ranking. It may NOT
  advance `validated_as_of_utc` and may NOT change a row's validation
  survivorship (board_validated / not_validated / Stage-A) -- those carry from the
  last weekly validation. A daily publish claims: "metrics current to <data-as-of>;
  validation as of <validated_as_of_utc> (<= 7 days old)."
- **Weekly re-rank** additionally runs the full honest validation
  (`validation_engine` via Stage 5) and advances `validated_as_of_utc` and the
  survivorship buckets.

### 4.1 The validated_as_of_utc surfacing (bring into scope; mostly built)

As-built today (NOT fully pending), this field is already wired across surfaces:

- **join writes it:** `utils/react_publish/k6_mtf_validation_join.py` emits
  `validated_as_of_utc`.
- **combine writes/propagates it:** fresh rows take the fresh evaluation time
  (`crunch_combine_proof.py:545`), carried rows keep their own and fall back to
  the board value (`:515-528`); it is one of only two carried-mutable fields
  (`:875` `_CARRIED_MUTABLE = {"rank", "validated_as_of_utc"}`).
- **promote requires it:** it is in `V2_VALIDATION_METADATA_REQUIRED`
  (`utils/react_publish/promote_k6_mtf_artifact.py:373`), so a fixture without it
  fails the gate.
- **React shows it:** `frontend/src/components/ValidationStamp.tsx:25` renders
  "Validated as of: ...", typed at `frontend/src/types.ts:130`, mounted in
  `frontend/src/App.tsx:106`.

**PROPOSAL -- a daily metrics-only re-rank is IMPLEMENTATION WORK, not just a
semantic convention; the all-fresh path cannot be reused as-is for it.** As built,
all-fresh combine stamps EVERY fresh row's `validated_as_of_utc` from the fresh
sidecar's evaluation time (`crunch_combine_proof.py:537-545`,
`row["validated_as_of_utc"] = fresh_sc.get("evaluation_time")`), and the join emits
the board-level `validated_as_of_utc` from the validation payload's evaluation time
(`utils/react_publish/k6_mtf_validation_join.py:528-530`). So a daily metrics-only
run that reuses the all-fresh path unchanged would OVERWRITE `validated_as_of_utc`
with tonight's date -- silently implying a validation that did not run and breaking
the D1 policy. The daily driver therefore REQUIRES one of (scope it in this phase):
(a) a mechanism that preserves the last-weekly validation fields
(`validated_as_of_utc` and the survivorship buckets) while replacing only
metrics / CCC / rank, or (b) a new combine/join "metrics-only" mode that takes the
fresh metrics + fresh CCC but carries the prior validation stamp forward. The
**WEEKLY** full-validation run can use the current all-fresh behavior as-is (it
legitimately advances the stamp). Separately, the board view must present
data-as-of and validated-as-of as DISTINCT fields -- that DISPLAY plumbing exists
(4.1: `ValidationStamp` mounted at board level, `App.tsx:106`, per-row detail in
`DetailModal`); what does NOT exist is the WRITE-SIDE plumbing to hold
`validated_as_of_utc` steady on a metrics-only night. Rationale: the risk is a
daily publish silently implying nightly validation, and avoiding it is real code,
not a convention.

---

## 5. Scheduling and credentials

### 5.1 Scheduler

**PROPOSAL -- Windows Task Scheduler, nightly trigger, run whether or not the
operator is logged on, WITHOUT `/IT`.** The task runs the re-rank driver under the
pinned interpreter from the project directory. Rationale: `/IT` (interactive-only)
defeats unattended operation; a non-interactive scheduled task with stored
credentials is the minimal viable unattended trigger on this single-operator box.

### 5.2 Credentials (upgrade from setx)

Build-and-Rank reads `BLOB_READ_WRITE_TOKEN` from the session env (set once via
`setx`, new-window required) and the git push uses Git Credential Manager
(`2026-06-11_BUILD_AND_RANK_OPERATIONS.md`, section 2). A scheduled task has no
interactive session, so a user env var is fragile. **PROPOSAL -- store the Blob
token in Windows Credential Manager / DPAPI and have the driver retrieve it at
launch (presence-only, never logged), falling back to the env var if absent.**
Git push continues via Credential Manager (already non-interactive). Rationale:
DPAPI binds the secret to the user account for unattended retrieval; it is the
scoping doc's deferred "scheduled-task credential story." The token value is never
printed (CLAUDE.md C4); the driver checks presence only.

### 5.3 How the operator learns a nightly run refused (minimal viable)

**PROPOSAL -- refusal envelopes plus one stable latest-status pointer; nothing
fancier.** On any halt/refusal the run dir already carries
`publish_refusal.json`, `publish_state.json`, `09_stage9_publish.json`, and
`RUN_SUMMARY.json` (`2026-06-11_BUILD_AND_RANK_OPERATIONS.md`, section 2.7). Add a
single stable pointer the operator checks each morning -- a
`output/rerank/latest_status.json` (run id, status, halted_at, refusal path) and
its mtime -- written at the end of every run, success or refusal. Rationale: a
fixed-path status file the operator (or a trivial check) reads each day is the
minimum viable "did last night work?"; email/alerting is out of scope.

---

## 6. Failure isolation prerequisite (load-bearing for 207 rows)

A 207-row nightly cannot be all-or-nothing: a single bad secondary must not sink
the whole board. As built, Stages 1-4 are batched and `_require_ok` hard-stops the
whole run on any stage failure
(`2026-06-11_BUILD_AND_RANK_OPERATIONS.md`, section 6, "all-or-nothing"); only the
Stage 9 tail has a transaction resume. Per-ticker quarantine and a build-stage
resume ledger are already flagged as required before large batches.

**PROPOSAL -- v1 minimal isolation, two pieces:**

1. **Per-secondary quarantine in the recook step.** Run k6_recook per secondary
   (or in failure-isolating batches) so one secondary's allowable failure (Stage-A
   allowable unavailability, fetch shortfall to target) quarantines THAT
   secondary -- it carries its prior row forward unchanged and is reported -- while
   the rest re-score and publish. Board-level / systemic failures (combine
   methodology lock, promote gate, push) still halt with no partial publish.
2. **A per-secondary resume ledger** under the run dir recording, per secondary,
   whether its recook + validation completed, so a killed nightly relaunch
   re-scores only the unfinished secondaries (a crash at row 150 does not redo
   149). This mirrors the Stage 9 transaction-resume idea at per-ticker grain.

**Defers to a later iteration:** cross-night ledger persistence, automatic retry
of quarantined secondaries, and partial-board "diff" publishes. v1 quarantines +
single-run resume only.

---

## 7. Operator surface

### 7.1 Zero-question launch contract (PROPOSAL)

**PROPOSAL -- the re-rank driver asks ZERO questions; the board is the selection.**
It enumerates the current board's secondaries from the live fixture
(`frontend/public/fixtures/k6_mtf_ranking.json`) as the fresh set, derives
`--target-as-of` (3.3), and runs the recook + cadence-appropriate validation +
publish path (full validation weekly, metrics-only daily per D1). Required
execute gates still apply (`crunch_rebuild_orchestrator.py:1026-1033` enforce
`--allow-network-fetch`, `--duration-budget-minutes`, `--operator-budget-label`,
`--target-as-of`; halt at `:1214`), so the driver supplies them itself:
`--operator-budget-label "rerank-nightly"`, a duration budget sized from the
measured pilot (1.2), `--allow-network-fetch`, and the derived target. Publication
still requires `--operator-approved-publish` -- the scheduled task supplies it as a
standing operator authorization for the nightly job. Rationale: a question-free
contract is the whole point of autonomy; the only human acts are the one-time
scheduler + credential setup.

### 7.2 Coexistence with operator-launched Build-and-Rank (the lock)

The orchestrator holds a SINGLE exclusive lock for any run:
`output/crunch_runs/.crunch.lock`, `O_CREAT|O_EXCL`
(`crunch_rebuild_orchestrator.py:66` `LOCK_NAME`, `:692` lock path,
`:255` O_EXCL open, `:1176` acquire at stage 0, `:1233` release). Stage 9 adds its
own `O_CREAT|O_EXCL` run lock with no reclaim (`stage9_publish.py:293,:301`). So if
a nightly re-rank and an operator Build-and-Rank fire together, **the second to
start fails to acquire the crunch lock and halts cleanly** -- no interleaving, no
partial publish. **PROPOSAL -- accept this as the coexistence rule and make it
legible:** the driver, on a lock-busy halt, writes the standard refusal + the
latest-status pointer (5.3) noting "lock held by run <id>", and the operator
simply reruns or lets the next night's trigger fire. Rationale: the existing
exclusive lock already gives correct mutual exclusion; the only addition is making
the "skipped because busy" outcome visible. (Confirm the lock's stale-reclaim
behavior at `crunch_rebuild_orchestrator.py:247` does not auto-steal a live
peer's lock during implementation.)

---

## 8. Out of scope (logged for later -- do not lose)

- **The 41-secondary backlog** (and any operator-curated additions): pure usage of
  Build-and-Rank, not Re-rank. The buckets surface in the launcher
  (`2026-06-11_BUILD_AND_RANK_OPERATIONS.md`, section 4); inclusion stays manual.
- **Faster member re-selection cadence** (re-running ImpactSearch/StackBuilder more
  often): a rebuild-cadence question (Decision D5), not nightly re-rank.
- **Volunteer / distributed compute** and any **non-finance domains.**
- **Alerting beyond the latest-status pointer** (email, dashboards).
- **Cross-night ledger persistence and quarantine auto-retry** (section 6 defers).

---

## 9. Decision register (operator decisions, one recommendation each, ordered)

Bring these to the operator one at a time, in order:

- **D1 -- Validation cadence.** Recommendation: daily metrics refresh + weekly full
  validation; daily never advances `validated_as_of_utc` (section 4).
- **D2 -- Nightly pipeline shape.** Recommendation: k6_recook restage only
  (metrics / CCC / rank refresh), no nightly OnePass/ImpactSearch/StackBuilder,
  after a measured pilot confirms freshness + runtime (section 1.2). Full honest
  validation runs on the WEEKLY cadence per D1, not nightly; a daily run must not
  advance `validated_as_of_utc` (section 4).
- **D3 -- target-as-of derivation.** Recommendation: latest completed US trading
  close, computed at launch (section 3.3).
- **D4 -- Board as-of representation.** Recommendation: per-row `history_as_of_date`
  truth + disclosed close-intersection; no overstated uniform board as-of
  (section 3.4).
- **D5 -- Member rebuild cadence.** Recommendation: periodic (e.g. weekly or
  on-demand) full OnePass + ImpactSearch + StackBuilder, separate from nightly;
  trust-library OFF for that run (sections 3.1-3.2).
- **D6 -- Scheduler.** Recommendation: Windows Task Scheduler, nightly, run-whether-
  logged-on, no `/IT` (section 5.1).
- **D7 -- Credential storage.** Recommendation: Blob token in Credential Manager /
  DPAPI with env fallback; git via Credential Manager (section 5.2).
- **D8 -- Failure isolation v1.** Recommendation: per-secondary quarantine in
  recook + single-run resume ledger; board-level failures still halt (section 6).
- **D9 -- Refusal visibility.** Recommendation: existing refusal envelopes + one
  stable `output/rerank/latest_status.json` pointer (section 5.3).
- **D10 -- Coexistence.** Recommendation: rely on the existing exclusive
  `.crunch.lock`; make the "skipped because busy" outcome legible (section 7.2).

---

## Appendix -- inspection citations (load-bearing)

- Stage commands / costs: `crunch_rebuild_orchestrator.py:513-604` (OnePass
  `:530-539`, ImpactSearch `:540-561`, StackBuilder `:562-588`, k6_recook
  `:589-603`); execute gates `:1026-1033,:1214`; reuse window `:73`,
  freshness `:950-963`; prior fixture/promo defaults `:1524-1533`;
  ImpactSearch trust-library inject `:495-502`; lock `:66,:692,:255,:1176,:1233`,
  stale-reclaim `:247`.
- k6_recook self-refresh: `k6_recook.py:124,:455-460` (reads selected_build),
  `:698-743` (fetch to target), `:1124-1146` (build signal library),
  `:1207-1235` (rebuild price_cache), `:52` default target, `:179-185` validation.
- Combine re-rank semantics: `crunch_combine_proof.py:308-311` (caller-neutral),
  `:405-406` (carried_secs), `:427-434` (all-fresh allowed), `:508` (CCC manifest),
  `:515-545,:875` (validated_as_of_utc carry rules).
- Stage 9 tail: `stage9_publish.py:43` (states), `:293,:301` (run lock).
- validated_as_of_utc surfaces: `utils/react_publish/k6_mtf_validation_join.py`
  (join), `crunch_combine_proof.py:515-545,:597,:693`,
  `utils/react_publish/promote_k6_mtf_artifact.py:373` (required),
  `frontend/src/components/ValidationStamp.tsx:25`, `frontend/src/types.ts:130`,
  `frontend/src/App.tsx:106`.
- Proven predecessor run: `output/crunch_runs/20260611T105546Z/` (2 tickers,
  ~2h19m, OnePass reused).
