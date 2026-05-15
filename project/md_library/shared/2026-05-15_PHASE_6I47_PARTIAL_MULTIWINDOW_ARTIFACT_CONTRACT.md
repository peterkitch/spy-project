# Phase 6I-47: partial multi-window artifact contract

**Date:** 2026-05-15
**Scope:** define a SEPARATE namespaced top-level key on the on-disk
Confluence artifact for the Phase 6I-46 TrafficFlow-compatible partial
payload, plus a guarded planner / writer / ranking-export path that
treats the partial surface as **display-only** unless the operator
explicitly opts in with **both** the `--allow-partial-payload-plan`
flag AND the existing two-key `--write` + `PRJCT9_AUTOMATION_WRITE_AUTH`
authorization.
**Authorization basis:** dry-run / planner / export / renderer
readiness only. **No production Confluence artifact write.** **No
stable promotion write.** Default CLI behaviour is unchanged
(strict-only). No `PRJCT9_AUTOMATION_WRITE_AUTH` set in this phase.
No source refresh. No yfinance. No `confluence_pipeline_runner`. No
batch engines.
**Verdict:** **PARTIAL_ARTIFACT_CONTRACT_READY_FOR_REVIEWED_WRITE.**
The partial namespaced block schema is defined, the planner emits it
under explicit opt-in, the writer dry-run carries it through cleanly,
the ranking export classifies a partial-only artifact as
`partial_multiwindow`, and the website export / view / static
renderer / overlays inherit the warning surface without code change.
A separate Phase 6I-48 supervised single-ticker partial-artifact
write (for SPY only) is the natural next step.

---

## 0. Top-line summary

| Stage | Result |
|---|---|
| Schema constants exposed | `PARTIAL_PAYLOAD_METADATA_KEY = "multiwindow_k_partial_payload_metadata"`, `PARTIAL_PAYLOAD_SCHEMA_VERSION = "phase_6i_47_partial_multiwindow_v1"`, `PARTIAL_PAYLOAD_REASON = "partial_payload_not_promotable"`, `PARTIAL_PLANNED_PAYLOAD_KEYS = (PARTIAL_PAYLOAD_METADATA_KEY,)`. Disjoint from strict `PLANNED_PAYLOAD_KEYS`. |
| Planner strict default (`allow_partial_payload_plan=False`) | UNCHANGED. Partial / blocked upstream → `recommended_next_action='partial_payload_not_promotable'`, `patch_ready=False`. |
| Planner partial mode (`allow_partial_payload_plan=True`) | Emits partial namespaced block (under `multiwindow_k_partial_payload_metadata`); `partial_patch_ready=True`; `recommended_next_action='ready_for_reviewed_partial_artifact_write'`; strict `patch_ready` stays False; strict `planned_payload` stays empty. |
| Writer default (no `--allow-partial-payload-plan`) | UNCHANGED. Strict cascade unchanged. Operator requesting `--write` with a partial-only plan gets `issue_codes ⊇ {'partial_write_not_allowed_by_planner_flag'}`; no mutation. |
| Writer partial dry-run (`allow_partial_payload_plan=True`, no `--write`) | `recommended_next_action='dry_run_review_partial_patch_plan'`; `wrote_artifact=False`; `partial_wrote_artifact=False`; `strict_wrote_artifact=False`; pre SHA == post SHA. |
| Writer partial write (`allow_partial_payload_plan=True` AND `--write` AND env var AND `partial_patch_ready=True`) | Merges `multiwindow_k_partial_payload_metadata` only; existing top-level keys preserved; strict keys never touched; `partial_wrote_artifact=True`, `strict_wrote_artifact=False`, `wrote_artifact=True`. **Not exercised in this phase except against tmp_path fixtures.** |
| Ranking export `data_status` taxonomy | Adds `partial_multiwindow`; `ranking_blocked_reason` adds `partial_multiwindow_only`. Default member-completeness provider auto-reads the partial namespaced block. |
| SPY/TEF evidence chain end-to-end | Strict planner: `patch_ready=False`, `recommended_next_action='partial_payload_not_promotable'`. Partial planner: `partial_patch_ready=True`, `recommended_next_action='ready_for_reviewed_partial_artifact_write'`. Partial writer dry-run: `wrote_artifact=False`, pre SHA == post SHA. Tmp artifact fixture carrying only the partial block: ranking export → `data_status='partial_multiwindow'` + `ranking_blocked_reason='partial_multiwindow_only'`. |
| Production-root diff (pre / post phase) | 0 / 0 / 0 across all 5 roots. |
| Phase 6I-47 focused tests | **31 / 31 passed**. |
| Full regression | **2,219 / 2,219 passed** (was 2,188 in Phase 6I-46 baseline; +31 new). 165 pre-existing pandas-fragmentation warnings, unchanged. |

---

## 1. Phase 6I-46 dependency summary

Phase 6I-46 (PR #263, merged 2026-05-15 at `2d26d1e`) added the
TrafficFlow-compatible invalid-member surface: `original_members_by_K`
/ `effective_members_by_K` / `excluded_members_by_K` /
`incomplete_member_detail` / `data_completeness_status` /
`data_warning_symbol` / `partial_payload_available`. The chain emits
an honest partial verdict for SPY/TEF (`data_completeness_status =
'partial'`, `data_warning_symbol = '!'`, 30/60 cells skipped with
`unprepared_due_to_excluded_members`). The strict Phase 6I-20
complete-payload contract was preserved verbatim. Partial payloads
are display-only at Phase 6I-46 close — they cannot mutate any
production Confluence artifact.

**Phase 6I-47 builds on that base**: the partial state now has a
designated on-disk landing spot (a separate namespaced block, **not**
under the strict Phase 6I-20 keys), an explicit opt-in planner /
writer path with the existing two-key authorization preserved, and
ranking-export recognition so a future authorized partial write
auto-renders on the website without further code changes.

---

## 2. Exact partial artifact schema

### 2.1 Key + version

| Constant | Value |
|---|---|
| `PARTIAL_PAYLOAD_METADATA_KEY` | `"multiwindow_k_partial_payload_metadata"` |
| `PARTIAL_PAYLOAD_SCHEMA_VERSION` | `"phase_6i_47_partial_multiwindow_v1"` |
| `PARTIAL_PAYLOAD_REASON` | `"partial_payload_not_promotable"` |
| `PARTIAL_PLANNED_PAYLOAD_KEYS` | `(PARTIAL_PAYLOAD_METADATA_KEY,)` |

### 2.2 Block fields

The block is a single dict at the top level of the on-disk Confluence
artifact, under `multiwindow_k_partial_payload_metadata`. It carries:

```
{
  "schema_version":          "phase_6i_47_partial_multiwindow_v1",
  "generated_at":            "<ISO 8601 UTC>",
  "target_ticker":           "<TICKER>",
  "current_as_of_date":      "<YYYY-MM-DD or null>",
  "selected_run_dir":        "<StackBuilder run dir or null>",
  "selected_run_id":         "<StackBuilder run id or null>",
  "data_completeness_status":"partial" | "blocked",
  "data_warning_symbol":     "!",
  "original_members_by_K":   { "<K>": [[ticker, proto], ...], ... },
  "effective_members_by_K":  { "<K>": [[ticker, proto], ...], ... },
  "excluded_members_by_K":   { "<K>": [{ticker, reason, telemetry_reason, source_classification}, ...], ... },
  "incomplete_member_detail":[{K, ticker, reason, telemetry_reason, source_classification}, ...],
  "prepared_cell_count":     <int>,
  "skipped_cell_count":      <int>,
  "expected_canonical_cell_count": 60,
  "counts_by_skipped_reason":{ "<reason>": <int>, ... },
  "skipped_cells":           [[K, window, reason], ...],
  "partial_payload_available": true,
  "strict_payload_ready":    false,
  "strict_patch_ready":      false,
  "reason":                  "partial_payload_not_promotable"
}
```

### 2.3 Strict-vs-partial key separation (load-bearing invariant)

The partial block **must not** carry any of the strict Phase 6I-20
keys:

  * `per_window_k_metrics`
  * `build_wide_window_alignment`
  * `multiwindow_k_engine_payload_metadata`

A website reader that reads only the strict keys can NEVER
accidentally pick up partial data. The planner-side and writer-side
consistency validators both refuse a partial block that violates this
invariant.

`strict_payload_ready` and `strict_patch_ready` are pinned to `False`
on every partial block as an extra failsafe. A writer-side validator
rejects any partial block where either is anything other than
`False`.

---

## 3. Planner mode table

| Mode | `allow_partial_payload_plan` | Upstream status | `patch_ready` | `partial_patch_ready` | `recommended_next_action` | Notes |
|---|:---:|---|:---:|:---:|---|---|
| Strict default complete | False (default) | `complete` + valid 60-cell payload + artifact exists | **True** | False | `ready_for_reviewed_artifact_write` | Unchanged Phase 6I-25 behaviour. |
| Strict default partial | False (default) | `partial` | False | False | `partial_payload_not_promotable` | Unchanged Phase 6I-46 behaviour. Partial is display-only. |
| Partial mode complete | True | `complete` + valid 60-cell payload | True | False | `ready_for_reviewed_artifact_write` | The strict path still wins when upstream is complete. |
| Partial mode partial | True | `partial` + artifact exists + valid partial block | False | **True** | `ready_for_reviewed_partial_artifact_write` | NEW. Partial planned-payload populated under the namespaced key only. Strict fields stay empty. |
| Partial mode blocked | True | `blocked` (zero cells) | False | False | `partial_payload_not_promotable` | Partial not promotable when zero cells prepared. |
| Partial mode partial-unavailable | True | `partial_payload_available=False` | False | False | `partial_payload_not_promotable` | Partial mode is a no-op when upstream lacks an available partial payload. |

---

## 4. Writer gate table

| Step | `allow_partial_payload_plan` | `--write` | Env var `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` | Planner state | Outcome |
|---|:---:|:---:|:---:|---|---|
| 1 | False (default) | False | any | strict not ready | `wrote_artifact=False`; `recommended_next_action='dry_run_review_patch_plan'`. Unchanged. |
| 2 | False (default) | True | any | strict not ready | `wrote_artifact=False`; strict cascade refuses (`patch_plan_not_ready`). Unchanged. |
| 3 | False (default) | True | set | partial ready, strict not ready | `wrote_artifact=False`; `issue_codes ⊇ {'partial_write_not_allowed_by_planner_flag'}`. NEW. |
| 4 | True | False | any | partial ready | `wrote_artifact=False`; `partial_wrote_artifact=False`; `recommended_next_action='dry_run_review_partial_patch_plan'`. NEW. |
| 5 | True | True | unset / wrong | partial ready | `wrote_artifact=False`; `issue_codes ⊇ {'env_authorization_missing_or_invalid'}`. NEW (same gate as strict). |
| 6 | True | True | set | partial ready + valid block + readable artifact | `partial_wrote_artifact=True`, `wrote_artifact=True`, `strict_wrote_artifact=False`; `recommended_next_action='partial_artifact_write_complete'`; artifact gains only `multiwindow_k_partial_payload_metadata`; strict keys untouched. NEW. Exercised only against tmp_path fixtures in this phase. |
| 7 | True | True | set | partial plan invalid (writer-side validator rejects) | `wrote_artifact=False`; `issue_codes ⊇ {'partial_patch_plan_contract_invalid'}`. NEW. |

**Invariants enforced by `_writer_partial_payload_is_consistent`:**

  1. `plan.partial_patch_ready == True`.
  2. `plan.partial_planned_payload` is a Mapping containing exactly the single key `PARTIAL_PAYLOAD_METADATA_KEY`.
  3. The block under that key carries `schema_version == PARTIAL_PAYLOAD_SCHEMA_VERSION`, `data_completeness_status ∈ {"partial", "blocked"}`, `strict_payload_ready is False`, `strict_patch_ready is False`, and ALL required scalar fields.
  4. The block does NOT contain ANY of the strict `PLANNED_PAYLOAD_KEYS`.
  5. `plan.partial_planned_payload_keys` equals `(PARTIAL_PAYLOAD_METADATA_KEY,)`.
  6. `plan.partial_fields_to_add` and `plan.partial_fields_to_replace` partition `PARTIAL_PLANNED_PAYLOAD_KEYS` exactly and are disjoint.

---

## 5. SPY / TEF dry-run evidence

### 5.1 Inputs

| Parameter | Value |
|---|---|
| Target ticker | `SPY` |
| Staged signal libraries | `_phase_6i_47_staged_libraries/` (70 PKLs for the 14 non-TEF tickers; built freshly via the Phase 6I-46 harness) |
| `invalid_members` | `{"TEF": {"reason": "invalid_or_delisted", "telemetry_reason": "provider_fetch_failed_zero_rows", "source_classification": "phase_6i_43_invalid_or_delisted"}}` |
| Cache dir | `cache/results` (read-only) |
| StackBuilder root | `output/stackbuilder` (read-only) |
| Artifact root for planner / writer | **Tmp directory** (copy of the existing SPY artifact); the on-disk production artifact root was NOT touched. |
| Pinned interpreter | `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe` |

### 5.2 Payload builder

| Field | Value |
|---|---|
| `payload_ready` | False (strict gate preserved) |
| `data_completeness_status` | `partial` |
| `data_warning_symbol` | `!` |
| `partial_payload_available` | True |
| `incomplete_member_detail` length | 6 (one record per K row K∈{7..12} authoring TEF) |

### 5.3 Strict planner (`allow_partial_payload_plan=False`)

| Field | Value |
|---|---|
| `patch_ready` | False |
| `partial_patch_ready` | False |
| `recommended_next_action` | **`partial_payload_not_promotable`** |
| `issue_codes` | `['payload_not_ready', 'partial_payload_not_promotable']` |
| `partial_planned_payload` | `{}` |

Phase 6I-46 strict-default behaviour preserved verbatim.

### 5.4 Partial planner (`allow_partial_payload_plan=True`)

| Field | Value |
|---|---|
| `patch_ready` | False (strict gate UNCHANGED) |
| `partial_patch_ready` | **True** |
| `recommended_next_action` | **`ready_for_reviewed_partial_artifact_write`** |
| `partial_planned_payload_keys` | `["multiwindow_k_partial_payload_metadata"]` |
| `partial_planned_payload` keys | exactly `{"multiwindow_k_partial_payload_metadata"}` |
| `partial_fields_to_add` / `partial_fields_to_replace` | partition `(PARTIAL_PAYLOAD_METADATA_KEY,)` |
| Block `schema_version` | `phase_6i_47_partial_multiwindow_v1` |
| Block `data_completeness_status` | `partial` |
| Block `data_warning_symbol` | `!` |
| Block `strict_payload_ready` / `strict_patch_ready` | False / False |
| Block `incomplete_member_detail` | 6 TEF records |
| Block presence of strict keys (`per_window_k_metrics` etc.) | **None** (writer-side validator enforces) |

### 5.5 Partial writer dry-run (`allow_partial_payload_plan=True`, `write=False`, env var UNSET)

| Field | Value |
|---|---|
| `allow_partial_payload_plan` | True |
| `partial_planner_patch_ready` | True |
| `write_requested` | False |
| `wrote_artifact` | **False** |
| `partial_wrote_artifact` | False |
| `strict_wrote_artifact` | False |
| `recommended_next_action` | **`dry_run_review_partial_patch_plan`** |
| `pre_write_sha256` | `db10e089...d2b63da977f` |
| `post_write_sha256` | `db10e089...d2b63da977f` |
| **Pre / post SHA byte-identical?** | **Yes** |

The tmp artifact (an in-memory copy of the production SPY artifact)
was NOT modified. The on-disk production SPY artifact was never read
in a mutation context, never opened for write, and remains untouched.

### 5.6 Ranking export against a tmp artifact carrying ONLY the partial block

A separate tmp directory was populated with a single SPY artifact
file containing **only** the partial namespaced block (no strict
Phase 6I-20 keys, no other multi-window content). Running
`build_multiwindow_ranking_export` against that tmp root produces:

| Field | Value |
|---|---|
| `inspected_count` | 1 |
| `eligible_count` | 0 |
| `blocked_count` | 1 |
| `blocked_rows[0].data_status` | **`partial_multiwindow`** |
| `blocked_rows[0].ranking_blocked_reason` | **`partial_multiwindow_only`** |
| `blocked_rows[0].rank_eligible` | False |
| `blocked_rows[0].data_completeness.has_incomplete_build_members` | True (after the planner's incomplete_member_detail records are merged) |
| Row count (eligible + blocked) | **1** (one ticker, one row) |
| Sort values | `total_capture_pct=None`, `sharpe_ratio=None`, `rank=None`, `trigger_days=0` |

The website export package / reader / static board renderer / overlays
inherit the partial state through the existing Phase 6I-40
`data_completeness` block plumbing — no code change needed at those
layers. A renderer smoke run (with and without local overlays)
against the production artifact root completed cleanly (52,164-byte
HTML each, rc=0, empty stderr) — the renderer continues to handle
the existing daily-only state without regression.

---

## 6. Temp artifact renderer preview summary

The end-to-end partial-only path proves:

  * A future supervised partial-artifact write would land
    `multiwindow_k_partial_payload_metadata` under the canonical
    Confluence artifact, leaving every other top-level key intact.
  * Without strict keys present, the ranking export classifies the
    ticker as `data_status='partial_multiwindow'` with
    `ranking_blocked_reason='partial_multiwindow_only'` — NOT
    rank-eligible, but visible.
  * The row carries `data_completeness.data_warning_symbol='!'` (via
    the existing Phase 6I-40 plumbing) and `incomplete_members`
    surfaced from the new block.
  * Sort values for the partial row are None / safe (the renderer
    cannot accidentally rank a partial row above a strict-complete
    row by treating zero-as-best).

The current production artifact for SPY still carries a daily-only
shape from a pre-Phase-6I-25 baseline, so the live website still
reports the same `daily_only` blocked state observed in Phase 6I-46.
A future Phase 6I-48 supervised partial-artifact write (for SPY
only) would flip the live row from `daily_only` to
`partial_multiwindow_only` without any further code change.

---

## 7. Production-root diff

```
PRE  : 3239 / 1634 / 35 / 5226 / 72899
POST : 3239 / 1634 / 35 / 5226 / 72899

cache/results:               modified 0  added 0  removed 0
cache/status:                modified 0  added 0  removed 0
output/research_artifacts:   modified 0  added 0  removed 0
output/stackbuilder:         modified 0  added 0  removed 0
signal_library/data/stable:  modified 0  added 0  removed 0
```

Zero production-root activity. All staged libraries and all evidence
artifacts lived under the working-tree `_phase_6i_47_*` paths
(gitignored, deleted at end of phase). All planner / writer
operations against a Confluence artifact used a tmp directory
populated by an in-memory copy of the SPY artifact; the on-disk
production artifact was never opened for write.

---

## 8. Verdict — PARTIAL_ARTIFACT_CONTRACT_READY_FOR_REVIEWED_WRITE

  * Partial namespaced block schema is defined, namespaced, and
    disjoint from the strict Phase 6I-20 keys.
  * Planner emits the partial block under explicit
    `allow_partial_payload_plan=True` opt-in. Strict default
    behaviour is byte-identical to Phase 6I-46.
  * Writer dry-run carries the partial block through cleanly. Pre /
    post SHA byte-identical on the tmp artifact. Production artifact
    untouched.
  * Writer-side `_writer_partial_payload_is_consistent` validator
    refuses malformed partial blocks (missing keys / wrong
    schema_version / status outside {`partial`, `blocked`} /
    strict_payload_ready or strict_patch_ready not False / strict
    keys appearing in the block / `partial_planned_payload_keys` not
    matching the partition).
  * Partial write requires ALL of: `allow_partial_payload_plan=True` +
    `--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` +
    `partial_patch_ready=True` + partial-consistency validator. Any
    missing gate refuses with a stable issue code.
  * Ranking export classifies a partial-only artifact as
    `data_status='partial_multiwindow'` +
    `ranking_blocked_reason='partial_multiwindow_only'`. One
    ticker remains one row. Sort values are None / safe.
  * Strict Phase 6I-20 complete-payload contract is preserved
    verbatim: partial payloads never flip strict `payload_ready`,
    `patch_ready`, or `can_evaluate_full_60_cell_grid`. The strict
    surface is unaffected.
  * 2,219 / 2,219 regression tests pass (+31 new Phase 6I-47 tests
    on top of the 2,188 Phase 6I-46 baseline). 165 pre-existing
    pandas-fragmentation warnings unchanged. No new warnings.

---

## 9. Next step

**If approved:** open a separate supervised Phase 6I-48 prompt to
write SPY's partial-artifact block to the production Confluence
artifact, via:

```
"C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
  multiwindow_k_confluence_patch_writer.py \
  --ticker SPY \
  --artifact-root output/research_artifacts \
  --stackbuilder-root output/stackbuilder \
  --signal-library-dir <Phase 6I-47 staged dir> \
  --cache-dir cache/results \
  --current-as-of-date 2026-05-14 \
  --invalid-members-json '{"TEF": {"reason": "invalid_or_delisted", "telemetry_reason": "provider_fetch_failed_zero_rows", "source_classification": "phase_6i_43_invalid_or_delisted"}}' \
  --allow-partial-payload-plan \
  --write
```

with `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` set in the
environment of that supervised session ONLY. Phase 6I-48 would also
re-run the ranking export against the now-partial production
artifact to confirm the live website surface flips from `daily_only`
to `partial_multiwindow_only` for SPY.

**If blocked / declined:** the partial-payload contract is staged
behind `--allow-partial-payload-plan` and stays display-only. Phase
6I-47 contributes the schema + dry-run path with no production
impact; any future authorization of a supervised partial write can
use the same CLI surface without further code changes.

**Smallest fix candidate if Phase 6I-47 itself were rejected:** none
required — the implementation is additive, defaults preserve every
pre-Phase-6I-47 contract verbatim, and the regression baseline is
clean.

---

## 10. Tests run

  * **Full regression**: `pytest test_scripts -q` → **2,219 passed**
    (was 2,188 in Phase 6I-46 baseline; +31 new Phase 6I-47 tests).
    165 pre-existing pandas-fragmentation warnings, unchanged.

  * **Phase 6I-47 focused suite**:
    `pytest test_scripts/test_phase_6i47_partial_multiwindow_artifact_contract.py -q`
    → **31 / 31 passed**.
    Covers:
    * schema constants disjoint from strict keys (× 2);
    * writer re-exports constants (× 1);
    * new planner / writer actions + issue codes listed in `ALL_*`
      aggregations (× 3);
    * planner default behaviour preserved when
      `allow_partial_payload_plan=False` (× 1);
    * planner partial mode emits the namespaced block + does not
      touch strict fields + no-op when partial unavailable (× 3);
    * writer-side partial-consistency validator accepts good plans
      and rejects 5 distinct malformed plans (× 6);
    * writer default refuses partial-write request (× 1);
    * writer partial dry-run does not mutate, pre/post SHA equal
      (× 1);
    * writer partial write requires env authorization (× 1);
    * writer partial write with full authorization writes the
      partial block ONLY (no strict keys) (× 1);
    * strict cascade unchanged when partial mode is off (× 1);
    * classifier returns `partial_multiwindow` for partial-only
      artifacts; does NOT demote artifacts with strict keys (× 2);
    * default member-completeness provider auto-reads the partial
      block (× 2);
    * `partial_multiwindow_only` blocked reason + status are in
      the public taxonomy (× 2);
    * end-to-end: ranking export against tmp partial-only artifact
      surfaces `data_status='partial_multiwindow'` +
      `ranking_blocked_reason='partial_multiwindow_only'`; one row
      per ticker preserved; sort values safe (× 3);
    * static guard: partial block schema does not carry strict
      keys (× 1).

  * **Touched-module focused suite** (planner / writer / payload
    builder / adapter / diagnostic / ranking export / website
    package / reader / static board renderer / overlays /
    staging-readiness / Phase 6I-46 contract): **410 / 410 passed**.
    No regression in any pre-existing test.

  * **`py_compile`** clean on all 5 changed modules
    (`multiwindow_k_confluence_patch_planner`,
    `multiwindow_k_confluence_patch_writer`,
    `confluence_multiwindow_ranking_export`, plus the existing
    Phase 6I-46 module surface continues to compile cleanly).

  * **`git diff --check`** clean (no whitespace / conflict markers).

---

## 11. No-production-activity confirmation

| Surface | Touched? |
|---|---|
| `cache/results` | **No** (0 / 0 / 0 diff vs pre-phase) |
| `cache/status` | **No** (0 / 0 / 0 diff) |
| `output/research_artifacts` | **No** (0 / 0 / 0 diff). The on-disk SPY artifact was COPIED to a tmp directory; the original was never opened for write. |
| `output/stackbuilder` | **No** (0 / 0 / 0 diff) |
| `signal_library/data/stable` | **No** (0 / 0 / 0 diff) |
| Tmp `tempfile.mkdtemp` directory | Yes — copied SPY artifact + partial-only fixture artifact; deleted at end of evidence run. NEVER touched any production root. |
| `_phase_6i_47_staged_libraries/` (working tree, gitignored) | Yes — 70 PKLs + 70 manifests for the 14 non-TEF tickers × 5 intervals; deleted before commit. |
| Confluence patch writer (`multiwindow_k_confluence_patch_writer`) | dry-run only against the tmp artifact; `wrote_artifact=False`; pre SHA == post SHA. |
| Signal-library promotion writer | Not invoked. |
| `PRJCT9_AUTOMATION_WRITE_AUTH` env var | **Never set** in the planner / writer evidence runs. |
| Source refresh / `signal_engine_cache_refresher` | **Not invoked**. |
| `yfinance` fetch | **None** (`--skip-source-availability`; cache-only reads). |
| `confluence_pipeline_runner` | **Not invoked**. |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster batch | **Not invoked**. |

---

## 12. Evidence artifact index (working-tree, not committed)

| Artifact | Purpose |
|---|---|
| `_phase_6i_47_pre_snapshot.json` | Pre-phase production-root snapshot. |
| `_phase_6i_47_post_snapshot.json` | Post-phase production-root snapshot. |
| `_phase_6i_47_snapshot_tool.py` | Snapshot helper (read-only). |
| `_phase_6i_47_evidence_runner.py` | End-to-end evidence runner (read-only against production; mutates tmp_path only). |
| `_phase_6i_47_evidence_runner.stdout` | Tmp pre/post SHA equality proof + ranking-export verdict on tmp partial-only fixture. |
| `_phase_6i_47_staged_libraries/` | 70 PKLs + 70 manifests for the 14 non-TEF tickers × 5 intervals (Phase 6I-45 staging path, re-used). |
| `_phase_6i_47_staging_readiness.json` | Phase 6I-32 harness output with `--invalid-members-json` (Phase 6I-46 surface continuing to operate cleanly). |
| `_phase_6i_47_payload.json` | Phase 6I-23 payload report carrying the Phase 6I-46 partial fields. |
| `_phase_6i_47_planner_strict.json` | Strict planner output (partial unavailable). |
| `_phase_6i_47_planner_partial.json` | Planner output with `allow_partial_payload_plan=True` — partial namespaced block + `partial_patch_ready=True`. |
| `_phase_6i_47_writer_partial_dryrun.json` | Writer dry-run partial mode against tmp artifact; `wrote_artifact=False`; pre/post SHA equal. |
| `_phase_6i_47_ranking_partial_only.json` | Ranking export against tmp artifact carrying ONLY the partial block. |
| `_phase_6i_47_board.html` / `_phase_6i_47_board_with_overlays.html` | Static board renderer smoke outputs (52,164 bytes each). |

These files are intentionally NOT committed; the authoritative
record of the phase is this markdown plus the code + test changes.

---

## 13. Files changed

| File | Change |
|---|---|
| `project/multiwindow_k_confluence_patch_planner.py` | New `PARTIAL_PAYLOAD_METADATA_KEY` / `PARTIAL_PAYLOAD_SCHEMA_VERSION` / `PARTIAL_PAYLOAD_REASON` / `PARTIAL_PLANNED_PAYLOAD_KEYS` constants; new `ACTION_READY_FOR_REVIEWED_PARTIAL_ARTIFACT_WRITE`; new `_build_partial_payload_block()` + `_planner_partial_payload_is_valid()` helpers; new `allow_partial_payload_plan` parameter; new partial-mode branch on `plan_multiwindow_k_confluence_patch()`; new `partial_*` fields on `MultiWindowKConfluencePatchPlan`; new CLI flag `--allow-partial-payload-plan` + `--invalid-members-json`. |
| `project/multiwindow_k_confluence_patch_writer.py` | Re-export of partial constants; new partial-mode `allow_partial_payload_plan` parameter; new `_writer_partial_payload_is_consistent()` + `_merge_partial_planned_payload()` helpers; new writer branch that handles the partial cascade (writes only `multiwindow_k_partial_payload_metadata`, leaves strict keys untouched); new result fields `strict_wrote_artifact` / `partial_wrote_artifact` / `partial_planner_patch_ready` / `partial_fields_added` / `partial_fields_replaced` / `partial_planned_payload_keys` / `allow_partial_payload_plan`; new issue codes `partial_patch_plan_not_ready` / `partial_patch_plan_contract_invalid` / `partial_write_not_allowed_by_planner_flag`; new actions `dry_run_review_partial_patch_plan` / `partial_artifact_write_complete` / `resolve_partial_patch_plan_first`; CLI flags `--allow-partial-payload-plan` + `--invalid-members-json`. |
| `project/confluence_multiwindow_ranking_export.py` | New `DATA_STATUS_PARTIAL_MULTIWINDOW` (in `ALL_DATA_STATUSES`); new `RANKING_BLOCKED_REASON_PARTIAL_MULTIWINDOW_ONLY` (in `ALL_RANKING_BLOCKED_REASONS`); `_classify_artifact_data_status()` detects partial-only artifacts (strict keys absent + partial namespaced block present) and returns `partial_multiwindow`; row-construction maps that status to the new blocked reason; default member-completeness provider also reads from `multiwindow_k_partial_payload_metadata`. |
| `project/test_scripts/test_phase_6i47_partial_multiwindow_artifact_contract.py` | **New** focused-test module with 31 tests (schema constants, planner default + partial mode, writer-side consistency validator, writer cascade behaviour, ranking-export partial-only classification, end-to-end with a tmp artifact). |
| `project/test_scripts/test_confluence_multiwindow_ranking_export.py` | Single-line bump: `ALL_RANKING_BLOCKED_REASONS` taxonomy size 9 → 10 to reflect the new `partial_multiwindow_only` entry. |
| `project/md_library/shared/2026-05-15_PHASE_6I47_PARTIAL_MULTIWINDOW_ARTIFACT_CONTRACT.md` | **New** evidence doc (this file). |

The website export package, reader/view, static board renderer, and
overlays modules required NO code change — their existing Phase 6I-40
`data_completeness` plumbing surfaces the new partial state
automatically via the ranking export's row-level `data_status` and
`data_completeness` fields.
