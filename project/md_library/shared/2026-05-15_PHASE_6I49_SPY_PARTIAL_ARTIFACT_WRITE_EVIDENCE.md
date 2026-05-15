# Phase 6I-49: SPY partial-artifact production write + board verification

**Date:** 2026-05-15
**Scope:** supervised production write of SPY's partial multi-window
metadata block onto the on-disk Confluence artifact, plus end-to-end
verification that the ranking export, website export package,
reader/view, and static board renderer all surface SPY as a
rank-eligible **partial_effective_members** row with the visible
`!` warning.
**Authorization basis:** explicit operator authorization for ONE
single-ticker partial-artifact write for SPY ONLY. TEF, the strict
Phase 6I-20 keys, the stable-promotion writer,
`signal_engine_cache_refresher`, `confluence_pipeline_runner`,
StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster
batch execution, and any write to `signal_library/data/stable`,
`output/stackbuilder`, `cache/results`, or `cache/status` were
explicitly NOT authorized.
**Production-write surfaces touched:** **one file** —
`output/research_artifacts/confluence/SPY/SPY__MTF_CONSENSUS.research_day.json`.
Top-level key added: **`multiwindow_k_partial_payload_metadata`**
ONLY. Strict Phase 6I-20 keys (`per_window_k_metrics`,
`build_wide_window_alignment`,
`multiwindow_k_engine_payload_metadata`) NEVER touched.
**Verdict:** **WRITE_COMPLETE.** SPY now classifies as
`rank_eligible=True`, `data_status='partial_multiwindow'`,
`ranking_eligibility_basis='partial_effective_members'`,
`data_completeness_status='partial'`, `data_warning_symbol='!'`,
`incomplete_members=['TEF']`, `k_cells_available=30`, with
populated `current_build_signals` / `current_build_signal_summary` /
`primary_build_summary` surfaces through the full website chain.

---

## 0. Top-line summary

| Stage | Result |
|---|---|
| Pre-write production-root snapshot | 3239 / 1634 / 35 / 5228 / 72899 |
| Pre-write SHA of SPY MTF artifact | `db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f` |
| Pre-write size of SPY MTF artifact | 18,321,961 bytes |
| Re-stage 14 non-TEF signal libraries to `_phase_6i_49_staged_libraries/` | 70 PKLs written, 0 failed |
| Dry-run writer (no `--write`, no env var) | `partial_planner_patch_ready=True`, `wrote_artifact=False`, `partial_wrote_artifact=False`, `strict_wrote_artifact=False`, pre/post SHA equal, `partial_planned_payload_keys=['multiwindow_k_partial_payload_metadata']`, strict `planner_patch_ready=False`, `recommended_next_action='dry_run_review_partial_patch_plan'` |
| **Authorized write** (`--write` + `PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit` + `--allow-partial-payload-plan`) | `wrote_artifact=True`, **`partial_wrote_artifact=True`**, `strict_wrote_artifact=False`, `partial_fields_added=['multiwindow_k_partial_payload_metadata']`, `fields_added=[]`, `fields_replaced=[]`, strict `planner_patch_ready=False`, `recommended_next_action='partial_artifact_write_complete'`, `issue_codes=[]` |
| Post-write SHA of SPY MTF artifact | `39cd024733ded9eb4ff8490f84cbd2cb4ec1c6e7a53d67d423017c0288e6ec92` |
| Post-write size of SPY MTF artifact | 18,342,570 bytes (+ 20,609 bytes = the partial namespaced block) |
| Pre vs post SHA | **DIFFERENT** (proves the on-disk file actually changed) |
| Re-read artifact → strict Phase 6I-20 keys present? | **No** (all 3 strict keys absent — never written by this writer cascade) |
| Re-read artifact → partial namespaced block present? | **Yes**, with all required Phase 6I-47 schema fields + 30 effective cells + 6 TEF exclusion records |
| Ranking export `SPY.rank_eligible` | **True** (was `False` / `blocked_reason='daily_only'` pre-write) |
| Ranking export `SPY.ranking_eligibility_basis` | **`partial_effective_members`** |
| Ranking export `SPY.data_completeness.{data_completeness_status, data_warning_symbol}` | **`partial` / `!`** |
| Ranking export `SPY.k_cells_available` | **30** (NOT 60 — honest partial coverage) |
| Ranking export `SPY.data_completeness.incomplete_members` | **`["TEF"]`** |
| Ranking export `_GSPC` | `daily_only` (unchanged, as expected — no partial block written for `_GSPC`) |
| Website package SPY surfaces | rank 1 in `ranking_rows`, NOT in `blocked_rows`; `current_build_signal_summary` non-null; `primary_build_summary` non-null; `ticker_details[SPY].current_build_signals` length 30 |
| View model SPY surfaces | in `ranking_table` (not `blocked_table`); ticker_card carries `ranking_eligibility_basis='partial_effective_members'` |
| Static board renderer HTML | 47,770 bytes; carries `Partial (effective members)` badge; carries `data-ranking-eligibility-basis="partial_effective_members"` on the SPY `<tr>`; SPY in ranking-row class; warning surface present |
| Production-root diff | **EXACTLY 1 file modified** in `output/research_artifacts/` (SPY MTF artifact); cache/results, cache/status, output/stackbuilder, signal_library/data/stable all **0 / 0 / 0** |
| Focused tests (7 suites, 220 tests) | **220 / 220 passed** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` env var post-command | **Not present** in shell environment (one-shot injection scope only) |

---

## 1. Pre-write checks

### 1.1 Branch + HEAD

```
$ git checkout -b phase-6i-49-spy-partial-artifact-write-evidence
Switched to a new branch 'phase-6i-49-spy-partial-artifact-write-evidence'
$ git log -1 --oneline
08effc3 Phase 6I-48: partial multi-window ranking eligibility (TrafficFlow-style) (#265)
```

Confirmed main HEAD is `08effc3` with Phase 6I-48 merged.

### 1.2 Production-root snapshot

```
PRE  counts (file totals across the 5 production roots):
  cache/results               3239
  cache/status                1634
  output/research_artifacts   35
  output/stackbuilder         5228
  signal_library/data/stable  72899
```

### 1.3 Pre-write SHA + size of the SPY MTF artifact

| Field | Value |
|---|---|
| Path | `output/research_artifacts/confluence/SPY/SPY__MTF_CONSENSUS.research_day.json` |
| SHA-256 | `db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f` |
| Size | 18,321,961 bytes |

### 1.4 Re-staged signal libraries

```
$ python signal_library_fresh_staging_readiness.py \
    --tickers SPY,AROW,AWR,CLH,CP,EXPO,FCFS,GBCI,HCSG,JNJ,LLY,MO,PRA,PRGO \
    --primary-ticker SPY \
    --staged-dir _phase_6i_49_staged_libraries \
    --cache-dir cache/results \
    --current-as-of-date 2026-05-14 \
    --skip-source-availability \
    --skip-downstream-chain \
    --skip-snapshot-diff
```

Result: 70 PKLs written, 0 failed. 14 non-TEF SPY-K-universe tickers
× 5 canonical intervals = 70 interval-native signal libraries. TEF
correctly excluded (Phase 6I-43 `invalid_or_delisted`).

### 1.5 Dry-run (no `--write`, no env var)

```
$ python multiwindow_k_confluence_patch_writer.py \
    --ticker SPY \
    --artifact-root output/research_artifacts \
    --stackbuilder-root output/stackbuilder \
    --signal-library-dir _phase_6i_49_staged_libraries \
    --cache-dir cache/results \
    --current-as-of-date 2026-05-14 \
    --invalid-members-json '{"TEF": {"reason": "invalid_or_delisted", "telemetry_reason": "provider_fetch_failed_zero_rows", "source_classification": "phase_6i_43_invalid_or_delisted"}}' \
    --allow-partial-payload-plan \
    --execution-log _phase_6i_49_writer_dry_run.jsonl
```

Dry-run result (every required field green):

| Field | Value |
|---|---|
| `write_requested` | `False` |
| `write_authorized` | `False` |
| `planner_patch_ready` (strict) | `False` |
| `partial_planner_patch_ready` | **`True`** |
| `wrote_artifact` | `False` |
| `partial_wrote_artifact` | `False` |
| `strict_wrote_artifact` | `False` |
| `allow_partial_payload_plan` | `True` |
| `partial_planned_payload_keys` | `['multiwindow_k_partial_payload_metadata']` |
| `planned_payload_keys` (strict) | `[]` |
| `recommended_next_action` | `dry_run_review_partial_patch_plan` |
| `pre_write_sha256` | `db10e089...d2b63da977f` |
| `post_write_sha256` | `db10e089...d2b63da977f` (equal) |
| `issue_codes` | `['write_not_requested']` |

Dry-run dynamics:

  * Two-key gate refused mutation (no `--write`, no env var).
  * Partial planner has a fully-validated namespaced block ready.
  * Strict cascade is correctly bypassed (strict `planner_patch_ready=False`).
  * Pre/post SHA byte-identical — on-disk artifact untouched.

The dry-run satisfies every Phase 6I-49 pre-write requirement.

---

## 2. Authorized partial write

### 2.1 Exact command

The env-var was injected via a single-command shell prefix (POSIX
`VAR=value command` style) so it lives ONLY for this one process
invocation. The env var was NOT exported into the shell session.

```
$ PRJCT9_AUTOMATION_WRITE_AUTH=phase_6h5_explicit \
    python multiwindow_k_confluence_patch_writer.py \
      --ticker SPY \
      --artifact-root output/research_artifacts \
      --stackbuilder-root output/stackbuilder \
      --signal-library-dir _phase_6i_49_staged_libraries \
      --cache-dir cache/results \
      --current-as-of-date 2026-05-14 \
      --invalid-members-json '{"TEF": {"reason": "invalid_or_delisted", "telemetry_reason": "provider_fetch_failed_zero_rows", "source_classification": "phase_6i_43_invalid_or_delisted"}}' \
      --allow-partial-payload-plan \
      --write \
      --execution-log _phase_6i_49_writer_authorized.jsonl
```

Post-command check: `PRJCT9_AUTOMATION_WRITE_AUTH` is **not present**
in the shell environment (confirmed via `python -c "import os; print('PRJCT9_AUTOMATION_WRITE_AUTH' in os.environ)"` → `False`).

### 2.2 Authorized-write result

| Field | Value |
|---|---|
| `write_requested` | **`True`** |
| `write_authorized` | **`True`** |
| `planner_patch_ready` (strict) | `False` |
| `partial_planner_patch_ready` | `True` |
| `wrote_artifact` | **`True`** |
| `partial_wrote_artifact` | **`True`** |
| `strict_wrote_artifact` | **`False`** |
| `allow_partial_payload_plan` | `True` |
| `fields_added` (strict) | `[]` |
| `fields_replaced` (strict) | `[]` |
| `partial_fields_added` | **`['multiwindow_k_partial_payload_metadata']`** |
| `partial_fields_replaced` | `[]` |
| `partial_planned_payload_keys` | `['multiwindow_k_partial_payload_metadata']` |
| `planned_payload_keys` (strict) | `[]` |
| `recommended_next_action` | **`partial_artifact_write_complete`** |
| `pre_write_sha256` | `db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f` |
| `post_write_sha256` | **`39cd024733ded9eb4ff8490f84cbd2cb4ec1c6e7a53d67d423017c0288e6ec92`** |
| Pre vs post SHA | **DIFFERENT** |
| `issue_codes` | **`[]`** |

The writer cascade chose the partial branch (because
`use_partial_branch=True`); strict cascade was correctly bypassed.
Only the partial namespaced key was added. Strict Phase 6I-20 keys
NEVER appeared in `fields_added` / `fields_replaced` /
`planned_payload_keys`.

---

## 3. Post-write on-disk verification

```
$ python -c "import hashlib; print(hashlib.sha256(open('output/research_artifacts/confluence/SPY/SPY__MTF_CONSENSUS.research_day.json','rb').read()).hexdigest())"
39cd024733ded9eb4ff8490f84cbd2cb4ec1c6e7a53d67d423017c0288e6ec92
```

Top-level keys present in the artifact: 13 (was 12; gained
`multiwindow_k_partial_payload_metadata`).

| Key (top-level) | Present? |
|---|---|
| `per_window_k_metrics` (strict Phase 6I-20) | **No** ← never written |
| `build_wide_window_alignment` (strict Phase 6I-20) | **No** |
| `multiwindow_k_engine_payload_metadata` (strict Phase 6I-20) | **No** |
| `multiwindow_k_partial_payload_metadata` (Phase 6I-47 / 6I-48) | **Yes** |

Partial namespaced block contents:

| Block field | Value |
|---|---|
| `schema_version` | `phase_6i_47_partial_multiwindow_v1` |
| `data_completeness_status` | `partial` |
| `data_warning_symbol` | `!` |
| `strict_payload_ready` | `False` |
| `strict_patch_ready` | `False` |
| `partial_payload_available` | `True` |
| `reason` | `partial_payload_not_promotable` |
| `prepared_cell_count` | **30** |
| `skipped_cell_count` | **30** |
| `expected_canonical_cell_count` | 60 |
| `effective_cell_count` | **30** |
| `effective_per_window_k_metrics` length | **30** |
| `incomplete_member_detail` (count of TEF records) | **6** (one per K row K∈{7..12} authoring TEF) |
| Defensive — strict keys inside block? | **No** (verified by an `assert` over the 3 forbidden keys) |

---

## 4. Live ranking-export verdict (SPY + `_GSPC` post-write)

```
$ python confluence_multiwindow_ranking_export.py \
    --tickers SPY,_GSPC \
    --artifact-root output/research_artifacts \
    --cache-dir cache/results
```

| Field | SPY | `_GSPC` |
|---|---|---|
| `inspected_count` | 2 | (same) |
| `rank_eligible` | **True** | False |
| `data_status` | **`partial_multiwindow`** | `daily_only` |
| `ranking_eligibility_basis` | **`partial_effective_members`** | `None` |
| `ranking_blocked_reason` | `None` | `daily_only` |
| `k_cells_available` | **30** | 0 |
| `data_completeness.data_completeness_status` | **`partial`** | `blocked` |
| `data_completeness.data_warning_symbol` | **`!`** | `!` |
| `data_completeness.incomplete_members` | **`["TEF"]`** | `[]` |
| Position in export | `ranking_rows[0]` (rank 1) | `blocked_rows` |

`eligible_count = 1`, `blocked_count = 1`. SPY flipped from
`daily_only` (pre-Phase 6I-49) to **rank-eligible partial**;
`_GSPC` unchanged.

---

## 5. Website package / view / static renderer

End-to-end verification against the post-write SPY artifact via
`confluence_website_export_package.build_website_export_package` →
`confluence_website_reader_view.build_view_model` →
`confluence_static_board_renderer.build_static_board_html`:

| Surface | Value |
|---|---|
| `package.ranking_rows[*]` containing SPY | **1** |
| `package.blocked_rows[*]` containing SPY | **0** |
| `package.ranking_rows[SPY].rank` | 1 |
| `package.ranking_rows[SPY].ranking_eligibility_basis` | `partial_effective_members` |
| `package.ranking_rows[SPY].data_completeness.data_warning_symbol` | `!` |
| `package.ranking_rows[SPY].data_completeness.data_completeness_status` | `partial` |
| `package.ranking_rows[SPY].data_completeness.incomplete_members` | `["TEF"]` |
| `package.ranking_rows[SPY].current_build_signal_summary` non-null | **True** |
| `package.ranking_rows[SPY].primary_build_summary` non-null | **True** |
| `package.ticker_details[SPY].ranking_eligibility_basis` | `partial_effective_members` |
| `package.ticker_details[SPY].current_build_signals` length | **30** |
| `package.ticker_details[SPY].current_build_signal_summary` non-null | True |
| `package.ticker_details[SPY].primary_build_summary` non-null | True |
| View model `ranking_table[SPY].ranking_eligibility_basis` | `partial_effective_members` |
| View model `ticker_cards[SPY].ranking_eligibility_basis` | `partial_effective_members` |
| Static renderer HTML size | 47,770 bytes |
| HTML carries `Partial (effective members)` badge | **True** |
| HTML carries `data-ranking-eligibility-basis="partial_effective_members"` attr on SPY `<tr>` | **True** |
| HTML carries SPY in `<tr class="ranking-row" data-detail-key="SPY">` | **True** |
| HTML carries warning surface | **True** |

One ticker, one row, preserved. SPY appears in the ranking table
with the partial badge and `!` warning; the detail card carries the
30-cell signal matrix and the populated primary-build summary.

---

## 6. Production-root diff

```
PRE  counts:  3239 / 1634 / 35 / 5228 / 72899
POST counts:  3239 / 1634 / 35 / 5228 / 72899

cache/results:               modified 0  added 0  removed 0
cache/status:                modified 0  added 0  removed 0
output/research_artifacts:   modified 1  added 0  removed 0
    M confluence/SPY/SPY__MTF_CONSENSUS.research_day.json
        size 18321961 -> 18342570
        mtime_ns 1778536273835459500 -> 1778826043366133300
output/stackbuilder:         modified 0  added 0  removed 0
signal_library/data/stable:  modified 0  added 0  removed 0
```

Exactly the surgical change Phase 6I-49 authorized: **one file
modified** — the SPY MTF Confluence artifact. Every other
production root was 0/0/0.

---

## 7. Proof strict Phase 6I-20 keys untouched

Three separate checks:

  1. **Writer result reports:** `fields_added=[]`, `fields_replaced=[]`,
     `planned_payload_keys=[]`, `strict_wrote_artifact=False`.
  2. **Direct re-read of the artifact JSON:** none of
     `per_window_k_metrics`, `build_wide_window_alignment`,
     `multiwindow_k_engine_payload_metadata` is a top-level key.
  3. **Defensive `assert` inside the partial block:** none of the
     three strict-key names appears inside
     `multiwindow_k_partial_payload_metadata`. The Phase 6I-47
     writer-side `_writer_partial_payload_is_consistent` validator
     would have refused the write otherwise; the runtime defensive
     check confirms.

---

## 8. Proof SPY now partial-ranked with `!` warning end-to-end

Tabulated above in §§ 4 and 5. To summarize:

  * Ranking export: `rank_eligible=True`, `data_status='partial_multiwindow'`,
    `ranking_eligibility_basis='partial_effective_members'`,
    `data_completeness_status='partial'`,
    `data_warning_symbol='!'`,
    `incomplete_members=['TEF']`, `k_cells_available=30`.
  * Website package: SPY is in `ranking_rows[0]`; ticker_details
    carry the basis + the 30-cell signal matrix + populated
    summary + primary build.
  * Reader/view: SPY in `ranking_table` (NOT `blocked_table`); both
    the table row and the ticker card carry
    `ranking_eligibility_basis='partial_effective_members'`.
  * Static renderer: HTML carries the `Partial (effective members)`
    badge in the ticker cell, the
    `data-ranking-eligibility-basis="partial_effective_members"`
    data attribute on the `<tr>`, and the warning column.

---

## 9. Tests

Focused suite (Phase 6I-49 + the surrounding partial / strict +
writer / ranking / package / view / renderer layers): **220 / 220
passed**.

```
$ pytest test_scripts/test_phase_6i48_partial_multiwindow_ranking_eligibility.py \
         test_scripts/test_phase_6i47_partial_multiwindow_artifact_contract.py \
         test_scripts/test_multiwindow_k_confluence_patch_writer.py \
         test_scripts/test_confluence_multiwindow_ranking_export.py \
         test_scripts/test_confluence_website_export_package.py \
         test_scripts/test_confluence_website_reader_view.py \
         test_scripts/test_confluence_static_board_renderer.py -q
220 passed in 2.02s
```

Phase 6I-49 made NO code changes (no `py_compile` runs needed
beyond the pre-write sanity); the entire phase consisted of a
supervised production write of the SPY MTF artifact + verification.
`git diff --check` is clean.

---

## 10. No-other-production-activity confirmation

| Surface | Touched? |
|---|---|
| `cache/results` | **No** (0 / 0 / 0 diff) |
| `cache/status` | **No** (0 / 0 / 0 diff) |
| `output/research_artifacts` | **Yes — exactly one file**: SPY MTF artifact gained the namespaced partial block. No other files added / removed / modified. |
| `output/stackbuilder` | **No** (0 / 0 / 0 diff) |
| `signal_library/data/stable` | **No** (0 / 0 / 0 diff) |
| Signal-library stable promotion writer | **Not invoked** |
| Source refresh / `signal_engine_cache_refresher` | **Not invoked** |
| yfinance fetch | **None** |
| `confluence_pipeline_runner` | **Not invoked** |
| StackBuilder / OnePass / ImpactSearch / TrafficFlow / Spymaster batch | **Not invoked** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` env var | Set **only** in the single-command shell prefix of the authorized-write invocation; verified absent from the shell session after the command returned |

The on-disk SPY artifact under `output/research_artifacts/` is
gitignored (`output/` is in `.gitignore`), so the production write
itself does not appear in `git status`. This is the established
repo convention for production artifacts. This PR commits only the
evidence doc (this file); the on-disk SPY artifact change is the
authoritative record of the production state.

---

## 11. Files changed (committed to the PR)

| File | Change |
|---|---|
| `project/md_library/shared/2026-05-15_PHASE_6I49_SPY_PARTIAL_ARTIFACT_WRITE_EVIDENCE.md` | **New** (this evidence doc). |

The actual production-data change is the on-disk
`output/research_artifacts/confluence/SPY/SPY__MTF_CONSENSUS.research_day.json`
file (gitignored per repo convention). Pre-write SHA, post-write
SHA, and exact byte-level diff are recorded above. Working-tree
evidence files (`_phase_6i_49_*`) are intentionally not
committed — they're intermediate evidence files that this document
summarizes.

---

## 12. Verdict

**Verdict:** **WRITE_COMPLETE.**

  * The SPY production Confluence artifact now carries the
    Phase 6I-47 partial namespaced block populated by the
    Phase 6I-48 effective-member ranking surface (30 effective
    cells, TEF surfaced as `invalid_or_delisted`).
  * The strict Phase 6I-20 complete-payload contract is preserved
    verbatim: the three strict top-level keys are absent from the
    artifact and absent from the partial namespaced block.
  * The live ranking export, website package, reader/view, and
    static board renderer all classify SPY as rank-eligible
    `partial_effective_members` with the visible `!` warning and
    `incomplete_members=['TEF']`. One ticker, one row.
  * `_GSPC` remains `daily_only` (no write was authorized for any
    other ticker).
  * Production-root diff is the surgically expected single-file
    change.
  * No other production write, no source refresh, no yfinance, no
    `confluence_pipeline_runner`, no stable promotion, no batch
    engines.
  * `PRJCT9_AUTOMATION_WRITE_AUTH` was scoped to a single
    invocation and is no longer present in the shell environment.

**Next operational action (Phase 6I-50 or later):** observe the
live board over a trading-day cycle; if `data_status='partial_multiwindow'`
+ `data_warning_symbol='!'` rendering is acceptable for the public
website, the same authorized-write pattern can be extended to other
multi-window tickers as they refresh + stage cleanly. The strict
Phase 6I-20 complete-payload path remains available for any future
ticker whose StackBuilder run can be evaluated to a full 60-cell
strict result; that path is unchanged by this phase.
