# Phase 6I-26: Supervised SPY Confluence patch writer DRY-RUN evidence

Sprint date: **2026-05-13** (evidence captured **2026-05-14T01:24Z UTC**; the resolved trading cutoff date rolled forward from 2026-05-12 to **2026-05-13** between Phase 6I-25 merge and this evidence pass).

Branch: `phase-6i-26-spy-confluence-patch-writer-dry-run-evidence`.
Doc: this file. **Docs-only PR — no code/test changes.**

This is the supervised **read-only / dry-run** evidence
pass for the Phase 6I-25 Confluence patch writer on SPY.
**No production write was performed.** The writer was
invoked WITHOUT `--write` and WITHOUT
`PRJCT9_AUTOMATION_WRITE_AUTH`. Production roots are
byte-identical before and after the dry-run.

---

## 0. Verdict (TL;DR)

| Check | Result |
|---|---|
| Production roots mutated | **No** (0/0/0 added/removed/changed across all five roots; 83,021 files) |
| Writer ran with `--write` | **No** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` set | **No** |
| Writer `wrote_artifact` | `false` |
| Writer SHA-256 pre / post | **Identical** (`db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f`) |
| Phase 6I-20 audit `has_true_multiwindow_k_engine_outputs` | **Still `false`** (unchanged before / after) |
| Phase 6I-24 planner `patch_ready` | **`false`** (`payload_not_ready` → `build_payload_first`) |
| Future artifact-write command preparation | **BLOCKED** — see § 9 |

**Future artifact-write command preparation is BLOCKED** in this evidence pass because the upstream Phase 6I-23 payload builder reports `adapter_not_ready` and the Phase 6I-24 planner consequently reports `payload_not_ready` / `build_payload_first`. **These are the only diagnostics directly captured in this evidence pass.** The Phase 6I-22 input adapter's specific per-cell skip reasons were not exercised through an explicit adapter-diagnostic CLI run here; the likely root cause inferred from prior Phase 6I-22 documentation (§ 6) is the `missing_target_close` gap (production signal libraries carry `dates` + `signals` but not always a `close` series), but this evidence pass does not directly prove that — see § 12.2 for the qualified inference and § 12.3 for the explicit next-step suggestion (an adapter diagnostic run, or a fix-with-tests). The writer correctly refuses to mutate regardless of the root cause.

---

## 1. Repo state

```
$ git status
On branch main
Your branch is up to date with 'origin/main'.

nothing to commit, working tree clean

$ git log --oneline -5
10b535b Phase 6I-25: guarded Confluence artifact patch writer for the multi-window K engine payload (#242)
e62cb5a Phase 6I-24: read-only Confluence artifact patch planner for the multi-window K engine payload (#241)
948c961 Phase 6I-23: in-memory multi-window K engine payload builder (#240)
66599c7 Phase 6I-22: read-only adapter from StackBuilder rows + OnePass interval libraries into multi-window K engine core inputs (#239)
3bce8aa Phase 6I-21: true multi-window K engine core evaluator (first real slice) (#238)

$ git rev-parse HEAD
10b535bbaf6006915d1b397d02028f34504b6cdd
```

Main HEAD `10b535b` matches the expected post-Phase-6I-25 commit. Phase 6I-26 branch created from this commit.

---

## 2. Temp evidence directory

All evidence outputs were written to a temp directory **outside** any production root:

```
C:\Users\sport\AppData\Local\Temp\phase_6i26_spy_confluence_patch_writer_dry_run\
├── snapshot_helper.py                                 (read-only walker; written here to keep tooling
│                                                       outside the repo)
├── diff_helper.py                                     (read-only diff)
├── 00_snapshot_before.json                            (production-root snapshot BEFORE)
├── 01_cache_cutoff_watcher.json                       (Phase 6H-2 watcher output)
├── 02_gap_audit_before.json                           (Phase 6I-20 audit BEFORE)
├── 03_planner.json                                    (Phase 6I-24 planner)
├── 04_writer_dry_run.json                             (Phase 6I-25 writer dry-run, no --write)
├── 05_gap_audit_after.json                            (Phase 6I-20 audit AFTER)
├── 99_snapshot_after.json                             (production-root snapshot AFTER)
├── 99b_snapshot_diff.json                             (diff: 0/0/0)
└── phase_6i26_spy_patch_writer_dry_run.jsonl          (writer execution-log; one JSONL row)
```

Pinned interpreter for every invocation: `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

---

## 3. Production-root snapshot BEFORE

```
$ "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe" \
    "<TEMP>/snapshot_helper.py" \
    "<TEMP>/00_snapshot_before.json"
```

| Root | File count |
|---|---|
| `cache/results` | 3,239 |
| `cache/status` | 1,634 |
| `output/research_artifacts` | 35 |
| `output/stackbuilder` | 5,214 |
| `signal_library/data/stable` | 72,899 |
| **TOTAL** | **83,021** |

Snapshot strategy: `relative_path -> (size_bytes, mtime_seconds)` per file under each root (the same Phase 6I-10 production-root snapshot strategy).

---

## 4. Cache-cutoff watcher

```
$ "<pinned-interp>" cache_cutoff_watcher.py --ticker SPY
```

Output (rc=0):

```json
{
  "current_as_of_date": "2026-05-13",
  "states": [
    {
      "ticker": "SPY",
      "cache_exists": true,
      "cache_date_range_end": "2026-05-12",
      "current_as_of_date": "2026-05-13",
      "cache_ahead_of_cutoff": false,
      "cache_equal_to_cutoff": false,
      "cache_behind_cutoff": true,
      "recommended_operator_action": "refresh_source_cache",
      "issue_codes": []
    }
  ],
  "ready_tickers": []
}
```

**Operational state has rolled** from the Phase 6I-25 close state. The resolved trading cutoff advanced to `2026-05-13` and the existing on-disk cache (`cache_date_range_end="2026-05-12"`) is now **behind cutoff** (no longer in STATE C / equal-cutoff; this is the STATE 4 / cache-behind case). `recommended_operator_action="refresh_source_cache"` (no provider fetch was triggered by the watcher).

This evidence pass deliberately does **NOT** invoke the source refresher — the Phase 6I-26 scope is read-only / dry-run only.

---

## 5. Phase 6I-20 gap audit BEFORE

```
$ "<pinned-interp>" multiwindow_k_engine_gap_audit.py --ticker SPY
```

Summary (rc=0):

```json
{
  "has_true_multiwindow_k_engine_outputs": false,
  "has_per_window_k_metrics": false,
  "has_build_wide_all_members_all_windows_signal": false,
  "daily_k_artifacts_present": true,
  "mtf_bridge_artifacts_present": true,
  "confluence_artifact_present": true,
  "missing_capabilities": [
    "missing_per_window_k_metrics",
    "missing_build_wide_window_alignment_fields",
    "missing_true_multiwindow_k_engine"
  ],
  "confluence_last_date": "2026-05-08",
  "observed_timeframes": ["1d", "1wk", "1mo", "3mo", "1y"],
  "observed_k_values": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
}
```

The existing daily-K + Phase 6D-2 MTF bridge + Phase 6D-3 Confluence artifact are all present for SPY (all 5 canonical windows + all 12 canonical K values observed), but the future-engine fields `per_window_k_metrics` and `build_wide_window_alignment` are NOT on disk — exactly the gap the Phase 6I-25 writer was designed to close.

---

## 6. Phase 6I-24 patch planner

```
$ "<pinned-interp>" multiwindow_k_confluence_patch_planner.py \
    --ticker SPY \
    --artifact-root output/research_artifacts \
    --stackbuilder-root output/stackbuilder \
    --signal-library-dir signal_library/data/stable \
    --current-as-of-date 2026-05-13
```

Result (rc=0):

```json
{
  "payload_ready": false,
  "patch_ready": false,
  "artifact_path": "output\\research_artifacts\\confluence\\SPY\\SPY__MTF_CONSENSUS.research_day.json",
  "artifact_exists": true,
  "fields_to_add": [],
  "fields_to_replace": [],
  "planned_payload_keys": [],
  "issue_codes": ["payload_not_ready"],
  "recommended_next_action": "build_payload_first",
  "existing_field_summary": {
    "has_per_window_k_metrics": false,
    "has_build_wide_window_alignment": false,
    "has_multiwindow_k_engine_payload_metadata": false,
    "artifact_version": "research_day_v1",
    "engine": "confluence",
    "target_ticker": "SPY",
    "last_date": "2026-05-08",
    "top_level_key_count": 12
  }
}
```

Embedded `payload_summary` (from Phase 6I-23 builder):

```json
{
  "payload_ready": false,
  "cell_count": 0,
  "per_window_k_metrics_count": 0,
  "build_wide_window_alignment_window_count": 0,
  "issue_codes": ["adapter_not_ready"]
}
```

### 6.1 Why the planner refused

**Directly observed in this evidence pass:**

- The Phase 6I-23 builder returned `payload_ready=False` with `issue_codes=["adapter_not_ready"]` (captured in `04_writer_dry_run.json` via the planner's `payload_summary` and in `03_planner.json` via the embedded summary).
- The Phase 6I-24 planner returned `patch_ready=False` with `issue_codes=["payload_not_ready"]` and `recommended_next_action="build_payload_first"` (captured directly in `03_planner.json`).
- The Confluence artifact exists at `output\research_artifacts\confluence\SPY\SPY__MTF_CONSENSUS.research_day.json` with 12 top-level keys; none of the three planned keys (`per_window_k_metrics` / `build_wide_window_alignment` / `multiwindow_k_engine_payload_metadata`) is present yet.

**Inferred likely root cause (prior documented context — NOT directly captured by Phase 6I-26 probes):**

- Phase 6I-22 doc § 6 documents a `missing_target_close` limitation of the production signal-library shape: the saved `.pkl` files carry `dates` and `signals` but do not always carry a `close` series, which would prevent the Phase 6I-22 adapter's strict full-member-coverage gate from preparing the canonical 60-cell input map.
- This is the most likely explanation for the `adapter_not_ready` flag observed here, but **the Phase 6I-26 probes did not run an explicit adapter diagnostic** (`multiwindow_k_input_adapter.py` does not currently expose a CLI surface that would print per-cell skip reasons for SPY). The inference is supported by prior documentation but not directly proven by the captured evidence in this pass.
- A future phase that targets this gap should either:
  - **(a) run an explicit adapter diagnostic** that prints the Phase 6I-22 `MultiWindowKInputAdapterReport.skipped_cells` reasons for SPY across all 60 canonical `(K, window)` pairs and verifies whether `missing_target_close` is in fact the dominant reason; OR
  - **(b) implement the close-source join / signal-library extension** as a code change with tests that prove the adapter prepares the full canonical 60-cell input set for SPY end-to-end.

Because `patch_ready=false`, no canonical SHA-256 of a `planned_payload` is computed — the planner returns an empty `planned_payload`.

---

## 7. Phase 6I-25 patch writer — DRY-RUN (no `--write`)

```
$ "<pinned-interp>" multiwindow_k_confluence_patch_writer.py \
    --ticker SPY \
    --artifact-root output/research_artifacts \
    --stackbuilder-root output/stackbuilder \
    --signal-library-dir signal_library/data/stable \
    --current-as-of-date 2026-05-13 \
    --execution-log "<TEMP>/phase_6i26_spy_patch_writer_dry_run.jsonl"
```

**No `--write` flag was passed.** **`PRJCT9_AUTOMATION_WRITE_AUTH` was NOT set.**

Result (rc=0):

```json
{
  "write_requested": false,
  "write_authorized": false,
  "planner_patch_ready": false,
  "wrote_artifact": false,
  "fields_added": [],
  "fields_replaced": [],
  "planned_payload_keys": [],
  "issue_codes": ["write_not_requested"],
  "recommended_next_action": "dry_run_review_patch_plan",
  "pre_write_sha256":  "db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f",
  "post_write_sha256": "db10e089f3b681984eb4c454b2c9bfd7459abbd718317626fbeabd2b63da977f",
  "execution_log_path": "C:\\Users\\sport\\AppData\\Local\\Temp\\phase_6i26_spy_confluence_patch_writer_dry_run\\phase_6i26_spy_patch_writer_dry_run.jsonl"
}
```

Dry-run contract satisfied:

| Check | Expected | Observed |
|---|---|---|
| `write_requested` | `false` | `false` |
| `write_authorized` | `false` | `false` |
| `wrote_artifact` | `false` | `false` |
| `issue_codes` contains `write_not_requested` | yes | yes |
| `recommended_next_action` | `dry_run_review_patch_plan` | `dry_run_review_patch_plan` |
| `planner_patch_ready` | mirror planner (`false`) | `false` |
| `pre_write_sha256 == post_write_sha256` | yes | yes |
| Artifact bytes unchanged | yes | yes (SHA identical) |

The writer correctly short-circuited at the **first** gate (`write=False`) — it did not even need to evaluate the second gate (env auth) or the third gate (planner patch_ready / writer-side consistency).

The Confluence artifact's SHA-256 (`db10e089...`) is the byte-identity proof: a byte-for-byte hash of `output\research_artifacts\confluence\SPY\SPY__MTF_CONSENSUS.research_day.json` taken once before and once after the writer call; they're equal.

---

## 8. Phase 6I-20 gap audit AFTER dry-run

```
$ "<pinned-interp>" multiwindow_k_engine_gap_audit.py --ticker SPY
```

The post-dry-run audit's per-ticker state for SPY is **byte-identical to the pre-dry-run state** in the load-bearing fields:

| Field | Before | After |
|---|---|---|
| `has_true_multiwindow_k_engine_outputs` | `false` | `false` |
| `has_per_window_k_metrics` | `false` | `false` |
| `has_build_wide_all_members_all_windows_signal` | `false` | `false` |
| `missing_capabilities` | `[missing_per_window_k_metrics, missing_build_wide_window_alignment_fields, missing_true_multiwindow_k_engine]` | (same) |

**Production `has_true_multiwindow_k_engine_outputs` is unchanged at `false`.**

---

## 9. Production-root diff (before / after)

```
$ "<pinned-interp>" "<TEMP>/diff_helper.py" \
    "<TEMP>/00_snapshot_before.json" \
    "<TEMP>/99_snapshot_after.json"
```

Result:

```json
{
  "cache/results":                 {"added": 0, "removed": 0, "changed": 0},
  "cache/status":                  {"added": 0, "removed": 0, "changed": 0},
  "output/research_artifacts":     {"added": 0, "removed": 0, "changed": 0},
  "output/stackbuilder":           {"added": 0, "removed": 0, "changed": 0},
  "signal_library/data/stable":    {"added": 0, "removed": 0, "changed": 0},
  "TOTAL":                         {"added": 0, "removed": 0, "changed": 0}
}
```

| Production root | Files | Added | Removed | Changed |
|---|---|---|---|---|
| `cache/results` | 3,239 | 0 | 0 | 0 |
| `cache/status` | 1,634 | 0 | 0 | 0 |
| `output/research_artifacts` | 35 | 0 | 0 | 0 |
| `output/stackbuilder` | 5,214 | 0 | 0 | 0 |
| `signal_library/data/stable` | 72,899 | 0 | 0 | 0 |
| **TOTAL** | **83,021** | **0** | **0** | **0** |

**Zero added / zero removed / zero changed across all 83,021 files in all five production roots.** No production root was touched by this evidence pass.

---

## 10. Execution-log temp JSONL

The writer was invoked with `--execution-log <TEMP>/phase_6i26_spy_patch_writer_dry_run.jsonl`. After the dry-run:

- **Line count: 1** (exactly one JSONL row).
- The row parses as JSON; its `target_ticker` / `wrote_artifact` / `recommended_next_action` / `pre_write_sha256` / `post_write_sha256` fields match the writer's stdout result byte-for-byte.
- Stored at `C:\Users\sport\AppData\Local\Temp\phase_6i26_spy_confluence_patch_writer_dry_run\phase_6i26_spy_patch_writer_dry_run.jsonl` — outside any production root.

`output/automation_logs/` was **NOT** touched.

---

## 11. No-production-activity confirmation

| Activity | Performed? |
|---|---|
| `--write` flag passed to the writer | **No** |
| `PRJCT9_AUTOMATION_WRITE_AUTH` env var set | **No** |
| Authorized launcher script created | **No** |
| Source refresh (`signal_engine_cache_refresher`) | **No** |
| `yfinance` fetch | **No** |
| `confluence_pipeline_runner` invocation | **No** |
| StackBuilder batch | **No** |
| OnePass batch | **No** |
| ImpactSearch batch | **No** |
| TrafficFlow batch | **No** |
| Spymaster batch | **No** |
| Confluence batch | **No** |
| Production data write | **No** |
| Subprocess invocations | **No subprocess launched by production modules.** Direct operator commands included pinned-interpreter Python script invocations of `cache_cutoff_watcher.py` / `multiwindow_k_engine_gap_audit.py` / `multiwindow_k_confluence_patch_planner.py` / `multiwindow_k_confluence_patch_writer.py` and standard `git` / `gh` housekeeping for the branch + PR. |
| Execution-log path | temp dir only; `output/automation_logs/` untouched |

The Phase 6H-5 two-key writer gate, the Phase 6I-9 supervised gate, the Phase 6I-10 production-root snapshot strategy (`relative_path_size_mtime`), the Phase 6I-12 ProviderFetchTelemetry four-surface contract, the Phase 6I-15 source-availability advisory contract, the Phase 6I-20 gap audit, the Phase 6I-21 engine core, the Phase 6I-22 input adapter, the Phase 6I-23 payload builder, the Phase 6I-24 patch planner, and the Phase 6I-25 patch writer are all unchanged.

---

## 12. Future artifact-write command preparation — **BLOCKED**

The readiness conditions for preparing a future artifact-write command (per the Phase 6I-26 spec) are:

| Condition | Required | Observed |
|---|---|---|
| planner `patch_ready=True` | yes | **NO** (`patch_ready=false`) |
| writer dry-run `planner_patch_ready=True` | yes | **NO** (mirrors planner) |
| writer dry-run `wrote_artifact=false` | yes | yes |
| writer dry-run `issue_codes` includes `write_not_requested` | yes | yes |
| Production roots unchanged 0/0/0 | yes | yes |

**Two checklist rows failed, both from the same upstream root condition**: the planner was not patch-ready because the payload builder reported `adapter_not_ready`. The writer dry-run row's `planner_patch_ready=false` is **not** an independent failure — it correctly mirrors the planner's verdict (the writer reads the planner's `patch_ready` flag and surfaces it as `planner_patch_ready`). **No future write command is prepared in this evidence pass.**

### 12.1 Blocking issue codes + recommended actions

**Directly observed** in `03_planner.json` / `04_writer_dry_run.json`:

| Layer | Issue (directly captured) | Recommended next action |
|---|---|---|
| Phase 6I-23 builder | `adapter_not_ready` | Surface per-cell adapter diagnostics (see § 12.3) or fix the upstream gap. |
| Phase 6I-24 planner | `payload_not_ready` | `build_payload_first`. |
| Phase 6I-25 writer | `write_not_requested` (correct — dry-run was intentional) | `dry_run_review_patch_plan`. |

**Inferred (prior documented context, NOT directly observed by Phase 6I-26 probes):**

| Layer | Issue (inferred) | Notes |
|---|---|---|
| Phase 6I-22 adapter | `missing_target_close` is the **most likely** explanation per Phase 6I-22 doc § 6 (production signal libraries carry `dates` + `signals` but not always `close`). | Not directly captured here; would need an explicit adapter-diagnostic run (see § 12.3). |

### 12.2 What this evidence pass leaves un-prepared

A future supervised authorized-write phase will need both:

1. **Upstream adapter fix** — resolve whatever the Phase 6I-22 adapter's actual per-cell skip reason is for SPY (most likely the `missing_target_close` limitation documented in Phase 6I-22 doc § 6, but **not directly proven by this evidence pass**) so the adapter can prepare a full canonical 60-cell input set for SPY end-to-end.
2. **Source refresh** — separately, `cache_date_range_end=2026-05-12` is now **behind** `current_as_of_date=2026-05-13`. The Phase 6I-15 / 6I-17 / 6I-18 source-availability discipline applies — a `signal_engine_cache_refresher` dry-run + supervised refresh would be the standard path. That is a separate phase from this writer-evidence pass.

Once **both** upstream gaps close, the planner should report `patch_ready=true` and the future write command can be prepared (in a subsequent phase, with Codex sign-off, per the Phase 6I-25 doc § 12 5-step Phase 6I-11-pattern).

### 12.3 Next-phase suggestion: confirm or fix the inferred root cause

To convert the inferred `missing_target_close` explanation into a directly-observed fact (or rule it out), the next phase should pick ONE of:

- **(a) Adapter diagnostic evidence run** — invoke a Phase 6I-22 adapter diagnostic (extending `multiwindow_k_input_adapter.py`'s CLI if needed, or via a one-shot Python script) that prints the `MultiWindowKInputAdapterReport.skipped_cells` per-cell skip reasons for SPY across all 60 canonical `(K, window)` pairs. If `missing_target_close` is in fact the dominant skip reason, the inference is confirmed. If a different reason dominates (e.g. `incomplete_member_coverage`, `missing_member_library`, length-mismatch), that becomes the load-bearing gap.
- **(b) Fix-with-tests** — implement the close-source join / signal-library extension as a code change with new tests proving the adapter prepares the full canonical 60-cell input set for SPY end-to-end. If the planner subsequently reports `patch_ready=true` for SPY, the gap is closed by definition.

Either path produces the evidence needed to either prepare a future write command (option b) or refine the next-phase plan (option a).

**This evidence pass deliberately does NOT include a "Future command candidate" code block** — per the Phase 6I-26 spec, that section only appears when ALL readiness conditions are met. They are not.

---

## 13. Operational state carried forward

- Operational state has rolled from the Phase 6I-25 close state. New verdict: **cache_behind_cutoff** (STATE 4 in the Phase 6I-17 4-state list, not STATE C / equal-cutoff). `cache_date_range_end=2026-05-12`, resolved `current_as_of_date=2026-05-13`.
- Production `has_true_multiwindow_k_engine_outputs` — still `false` for SPY.
- `real_confluence_pipeline_runner_write` — still open.
- `real_post_pipeline_validation_on_writer_path` — still open.
- Writer-surface provider telemetry — still pending.
- The Phase 6I-25 writer is in place and correctly refused to mutate the artifact in dry-run mode. **The writer code path is healthy** — this dry-run did not surface a writer bug; it surfaced an upstream `adapter_not_ready` → `payload_not_ready` chain whose most likely root cause (per Phase 6I-22 doc § 6) is the pre-existing `missing_target_close` gap, but the per-cell adapter diagnostic was not run in this pass; § 12.3 names the next-phase options for confirming or fixing it.

---

## 14. Validation

- `git diff --check`: clean (only the new Markdown doc is tracked; no whitespace errors).
- `git diff --stat`: 1 file added (this doc); zero code/test changes.
- Pinned interpreter used on every Python invocation:
  `C:/Users/sport/AppData/Local/NVIDIA/MiniConda/envs/spyproject2/python.exe`.

---

## 15. Reference paths

- Phase 6I-25 writer: `project/multiwindow_k_confluence_patch_writer.py` + `project/md_library/shared/2026-05-13_PHASE_6I25_MULTIWINDOW_K_CONFLUENCE_PATCH_WRITER.md` (§ 12 enumerates the future supervised-run 5-step pattern this evidence pass was a prerequisite for).
- Phase 6I-24 planner: `project/multiwindow_k_confluence_patch_planner.py` + `project/md_library/shared/2026-05-13_PHASE_6I24_MULTIWINDOW_K_CONFLUENCE_PATCH_PLANNER.md`.
- Phase 6I-23 builder: `project/multiwindow_k_engine_payload_builder.py` + `project/md_library/shared/2026-05-13_PHASE_6I23_MULTIWINDOW_K_ENGINE_PAYLOAD_BUILDER.md`.
- Phase 6I-22 input adapter (where `missing_target_close` is documented): `project/multiwindow_k_input_adapter.py` + `project/md_library/shared/2026-05-13_PHASE_6I22_MULTIWINDOW_K_INPUT_ADAPTER.md` § 6.
- Phase 6I-21 engine core: `project/multiwindow_k_engine_core.py`.
- Phase 6I-20 gap audit: `project/multiwindow_k_engine_gap_audit.py`.
- Phase 6I-18 next-probe handoff (operational-state discipline): `project/md_library/shared/2026-05-13_PHASE_6I18_SOURCE_WAIT_HANDOFF.md`.
- Phase 6I-17 SPY source-ready recheck (4-state list): `project/md_library/shared/2026-05-13_PHASE_6I17_SPY_SOURCE_READY_RECHECK.md`.
- CLAUDE.md § 6 — current sprint state.
- Temp evidence directory: `C:\Users\sport\AppData\Local\Temp\phase_6i26_spy_confluence_patch_writer_dry_run\` (this directory is OUTSIDE production roots and OUTSIDE the repo; nothing in it is committed).
