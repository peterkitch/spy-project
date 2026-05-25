# TrafficFlow Runner Phase E Canonical-Write Contract (Scoping)

Session date (UTC): 2026-05-25
Session directory (gitignored): `<SESSION_DIR>` =
`logs/trafficflow_phase_e_scoping/20260525T011534Z/`
Branch: `trafficflow-runner-phase-e-canonical-write-contract`

This doc defines the **Phase E canonical-write contract** the
TrafficFlow runner must satisfy before any code amendment lifts the
current Phase C canonical-write refusal at `trafficflow_runner.py`
lines 116-121 / 2151-2169. The contract covers run identity,
per-secondary directory layout, atomicity, partial publishing,
progress / status / completion markers, external process fan-out
safety, downstream handoff, heavy-stage policy for K=7..12, and
the bounded PR sequence that implements it.

**Headline recommendation.** PASS WITH NOTES. Phase E
implementation must proceed as **bounded PRs (Alpha through
Epsilon)** with PR Alpha landing the `--canonical-write` /
`--heavy-stage` flags and the new `phase_e_v1` schema constants
under tests only, before any real canonical-write run. The runner
write surface as it stands today (per-secondary subdirectories,
atomic `<file>.tmp` + `os.replace`, `run_manifest.json` /
`run.stdout.json` at the top of the output dir, selected-build
pinning, PR #308 network/cache surface block) is a strong
foundation; the additions needed are an explicit canonical mode
gate, per-secondary `.done` markers, a `progress.json` +
`run_status.json` shared between workers and a finalizer, a
`selected_output.json` pointer, and a heavy-stage refusal for
K > 6. Daily-cadence canonical writes are **K=1..6 only**; K=10..12
remains deferred to Phase F or later with its own chunking /
resumability requirements.

---

## 1. Scope and Non-Goals

In scope:

- Static review of the runner's current write surface, manifest
  schema, sanitization, and guardrails.
- Definition of the Phase E canonical-write contract for the
  daily-cadence K=1..6 surface at 250-500 secondary scale.
- External process fan-out safety requirements.
- Atomicity, partial publishing, progress / status / completion
  markers.
- Downstream handoff policy via `selected_output.json`.
- Heavy-stage policy for K=7..12 / K=10..12.
- Enumeration of required runner amendments before any real
  canonical-write run.
- Implementation checklist as a bounded PR sequence.

Out of scope (NOT performed):

- Implementing Phase E (no `--canonical-write` flag exists today;
  no canonical writer; no orchestrator).
- Modifying `trafficflow.py`, `trafficflow_runner.py`,
  `signal_engine_cache_refresher.py`, or any test file.
- Canonical writes to `output/trafficflow/` (still structurally
  refused).
- Heavy-stage implementation (deferred).
- Runner-internal threading amendment (deferred per PR #315).
- Any runner invocation or compute run.

---

## 2. References

- Phase A scoping doc -
  `md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_EXECUTION_SURFACE.md`
- PR #306 - Phase C isolated-write implementation (lazy compute
  loader, atomic writes, manifest + stdout sidecar)
- PR #308 - engine network / price-cache write surface block
- PR #309 - SPY/AAPL Phase C network-block re-validation
- PR #310 - broader Phase C smoke (8 secondaries, K=1..6)
- PR #313 - Phase D full-K re-measurement (K=10..12 dominates ~89
  percent of full-K wall-clock)
- PR #314 - headless speed-parity audit (PASS WITH NOTES)
- PR #315 - ThreadPool feasibility benchmark (external process
  fan-out is the safe concurrency model; in-process ThreadPool
  unsafe under current monkey-patch guardrails)
- PR #316 - at-scale performance inference (K=1..6 daily-cadence
  feasible at 250-500 scale; K=10..12 multi-hour to overnight)

---

## 3. Current Runner Write Surface

Static review of `trafficflow_runner.py` on `main` at `6a241c2`.

### 3.1 `--write` dispatch and Phase C canonical guardrail

- `--write` parsed at line 245 with help text "Phase B refuses
  --write. Reserved for Phase E."
- `CANONICAL_OUTPUT_FORBIDDEN_FOR_PHASE_C = ("output/trafficflow",)`
  at lines 116-121.
- `is_isolated_output_dir()` at lines 544-584: returns False when
  the resolved `--output-dir` is `output/trafficflow` or any
  descendant; True otherwise (including absolute paths outside
  project root).
- `main()` refusal block at lines 2151-2169: when `--write` is
  passed and `effective_config["canonical_write_blocked"]` is
  true, the runner emits `canonical_write_forbidden_in_phase_c`
  warning + error, sets `verdict=REFUSED`, exits with
  `EXIT_REFUSED` **before any preflight or compute**. The
  refusal path explicitly does not import `trafficflow`.
- `PHASE_C_RUN_MANIFEST_SCHEMA = "trafficflow_runner_phase_c_v1"`
  at line 123.

### 3.2 Atomic write helpers and file-write sites

- `_atomic_write_bytes()` at line 1755: writes to
  `path.with_name(path.name + ".tmp")` then `os.replace(tmp, path)`.
- `_atomic_write_json()` at line 1764: serializes payload via
  `json.dumps(payload, indent=2, default=str)` then delegates to
  `_atomic_write_bytes`.
- Board-row writes at lines 1910-1911 inside `_execute_isolated_write`:
  `_atomic_write_json(json_p, rows)` and
  `_atomic_write_bytes(csv_p, _board_rows_to_csv_bytes(rows))`.
- Per-secondary subdirectory construction at line 1905:
  `sec_dir = output_dir / str(secondary)`. Per-K board files at
  lines 1907-1908: `board_rows_k=<K>.json` / `board_rows_k=<K>.csv`.
- Run-level files at lines 2331-2352:
  `manifest_path = output_dir / "run_manifest.json"`,
  `stdout_path = output_dir / "run.stdout.json"`. Manifest is
  written first via `_write_run_manifest()` (line 1957), then the
  envelope is sanitized and `_emit_json` runs to stdout; the
  stdout sidecar is the same JSON written under output_dir.

### 3.3 Privacy sanitization

- `sanitize_for_json()` at line 485: recursive
  dict/list/scalar walk that calls `path_for_output()` on
  path-like leaves.
- `path_for_output()` at line 434: converts under-project-root
  paths to repo-relative POSIX (`output/stackbuilder/SPY/...`);
  absolute paths outside the project root are redacted to
  `<ABSOLUTE_PATH_REDACTED>`. Drive-letter and username tokens
  are removed.
- Applied to the manifest payload (line 1990) and the envelope
  emitted to stdout (line 2077, line 2353).
- Canonical write under `output/trafficflow/` would emit
  repo-relative POSIX paths that contain the literal string
  `output/trafficflow/...`; this is operationally fine and
  matches the existing stackbuilder convention.

### 3.4 Lazy compute wrapper and selected-build pinning

- `_default_compute_loader()` at line 1670: imports `trafficflow
  as tf`, pins `tf._find_latest_combo_table` to the resolved
  leaderboard path (line 1737), applies `_patch_engine_network_surface(tf)`
  (line 1739, PR #308) when `--allow-network-fetch` is not
  passed, then calls `tf.build_board_rows(...)` at line 1740 and
  restores both pins in a `finally` block.
- Module-level rebind pattern: PR #315 established that this
  pinning is **not thread-safe** under concurrent per-secondary
  compute in the same Python process. Phase E external process
  fan-out is the safe shape; in-process ThreadPool needs a
  guardrail redesign (PR #315 Section 3.5) before it can be
  considered.

### 3.5 Manifest + stdout sidecar schema

`run_manifest.json` schema (Phase C, built in `_write_run_manifest`,
lines 1971-1990):

- `schema_version` = `"trafficflow_runner_phase_c_v1"`
- `started_at`, `ended_at`, `elapsed_seconds` (carried from
  envelope)
- `effective_config` (write_mode, write_authorized,
  output_dir_isolated, canonical_write_blocked,
  allow_network_fetch, secondaries, secondaries_file, k_range,
  stackbuilder_root, output_dir, ...)
- `write_summary` (artifacts_written_count, cells_requested,
  cells_eligible, cells_written, cells_skipped, cells_errored,
  short_circuited_after_consecutive_errors)
- `canonical_artifacts_referenced` (one entry per
  secondary: `secondary`, `selected_build_path`,
  `selected_build_sha256`, `explicit_build_override`,
  `selected_run_dir`, `combo_leaderboard_path`)
- `per_cell_summary` (per (secondary, k) `elapsed_seconds` plus
  `classification` and `error_kind` / `error_message` when set)
- `output_dir` (sanitized, repo-relative POSIX)
- `artifacts_written` (list of sanitized repo-relative POSIX
  paths)

`run.stdout.json`: written under the output dir as the on-disk
mirror of the JSON emitted to stdout; overlaps with manifest plus
the broader envelope (`per_secondary_results`,
`benchmark_eligibility`, `next_stage_ready`, `verdict`, `warnings`,
`errors`, `git_head`). Diagnostic mirror.

### 3.6 Board-row artifact format

- `board_rows_k=<K>.json`: list of row dicts (one row per
  qualifying combination at K under the current Phase 6I-79
  density, typically 1 row per cell).
- `board_rows_k=<K>.csv`: produced by `_board_rows_to_csv_bytes`
  (line 1769). Column order preserved from the first row that
  defines a column; additional rows that introduce new columns
  extend the header. Missing values render as empty string.
- Empty rows list yields `b""` (no header).

---

## 4. Phase E Daily-Cadence Canonical Contract (K=1..6)

### 4.1 Canonical output root and run identity

Canonical root remains:

    output/trafficflow/

Run root pattern:

    output/trafficflow/runs/<RUN_ID>/

where `<RUN_ID>` is a UTC timestamp of the form
`YYYYMMDDTHHMMSSZ`. Rationale: matches the existing convention
under `logs/.../<UTC_TIMESTAMP>/` and is sortable as a string.
UUID is unnecessary at this scale; a single second resolution is
sufficient because canonical runs are operator-supervised and
sequential. If two canonical runs ever start in the same second,
the orchestrator MUST fail-closed (refuse to overwrite) rather
than mint a duplicate ID.

Selected/current pointer:

    output/trafficflow/selected_output.json

Updated atomically by the orchestrator only after run finalization
(see Section 8). The `selected_output.json` file is the
authoritative downstream-consumer handoff; do not introduce a
`latest/` symlink, junction, or copy at this stage. Symlinks are
brittle across platforms (the audit hardware is Windows; CLAUDE.md
notes a Conda env on Windows 11) and would add cross-platform
maintenance burden without benefit over a JSON pointer.

Historical run retention: out of scope for this scoping. Historical
canonical runs accumulate under `output/trafficflow/runs/<RUN_ID>/`;
retention/pruning is deferred to operator policy.

### 4.2 Per-secondary directory layout (minimum required)

    output/trafficflow/
        selected_output.json
        runs/
            <RUN_ID>/
                progress.json                  [required]
                run_status.json                [required]
                run_manifest.json              [required, written at finalization]
                run.stdout.json                [optional / diagnostic]
                <SECONDARY>/                   [one per secondary]
                    board_rows_k=1.json        [required]
                    board_rows_k=1.csv         [required]
                    board_rows_k=2.json        [required]
                    board_rows_k=2.csv         [required]
                    board_rows_k=3.json        [required]
                    board_rows_k=3.csv         [required]
                    board_rows_k=4.json        [required]
                    board_rows_k=4.csv         [required]
                    board_rows_k=5.json        [required]
                    board_rows_k=5.csv         [required]
                    board_rows_k=6.json        [required]
                    board_rows_k=6.csv         [required]
                    secondary_manifest.json    [deferred / PR Beta decision]
                    .done                      [required]
                .quarantine/
                    <SECONDARY>/               [present only on failure]
                        failure.json           [failure record]

Minimum required files (PR Alpha + Beta scope):

- 12 board-row files per successful secondary (6 JSON + 6 CSV).
- Per-secondary `.done` marker (zero-byte file or single-line
  JSON; recommendation: zero-byte for atomic create simplicity).
- `progress.json` (orchestrator-owned).
- `run_status.json` (orchestrator-owned).
- `run_manifest.json` (orchestrator-owned, written at finalization).

Deferred optional files (PR Beta / Gamma decision):

- `secondary_manifest.json` per secondary: defer unless downstream
  consumers need per-secondary provenance independent of the
  run-level manifest. The run-level
  `canonical_artifacts_referenced` already carries per-secondary
  provenance; a separate per-secondary manifest is duplicative.
  Recommendation: defer to a future PR if a real consumer need
  appears.

### 4.3 Atomicity (per-secondary)

Per-secondary atomicity is the core unit. Each secondary's
directory must transition from "absent" to "complete with all 12
board-row files and `.done` marker" in a way that downstream
consumers cannot observe an intermediate state.

Recommended flow (worker subprocess, one secondary):

1. Write 12 board-row files atomically per-file via the existing
   `_atomic_write_bytes` / `_atomic_write_json` helpers
   (`<file>.tmp` then `os.replace(tmp, file)`).
2. After all 12 files succeed validation (file exists, file size
   > 0 for JSON; CSV may be `b""` for empty rows but still must
   exist), write the `.done` marker via the same atomic pattern.
3. If any board-row file fails, do not write `.done`. Move the
   partially-written secondary directory under
   `<RUN_ID>/.quarantine/<SECONDARY>/` and write
   `.quarantine/<SECONDARY>/failure.json` with the failure
   record.

Platform caveat: `os.replace` is atomic for files on the same
filesystem. Directory-level atomic rename is not portable across
all platforms (the audit hardware is Windows). The recommended
pattern is therefore **per-file atomic writes plus a final
zero-byte `.done` marker** that gates downstream consumption.
Downstream consumers must NOT consume a `<SECONDARY>/` directory
that lacks `.done`. This avoids the directory-rename portability
trap.

Alternative considered but rejected for the minimum slice: write
to `<RUN_ID>/.staging/<SECONDARY>/` and atomically rename
`.staging/<SECONDARY>` to `<SECONDARY>/`. The directory rename
under Windows is not guaranteed atomic when the destination
exists, requires destination-removal first, and adds an extra
filesystem operation per secondary. Per-file atomic writes plus a
`.done` gate is simpler, well-tested, and matches the existing
runner pattern.

### 4.4 Partial publishing policy

- Completed secondaries (`<SECONDARY>/.done` exists) MAY be
  consumed mid-run.
- Incomplete or failed secondaries (`.done` absent) MUST NOT be
  consumed.
- Downstream consumers MUST treat `<SECONDARY>/` directories
  without `.done` as either incomplete or quarantined and skip
  them.
- The run-level status (`run_status.json`) reflects whether the
  full run finished `complete`, `partial`, `failed`, or
  `interrupted`. `selected_output.json` is updated only at
  terminal status per Section 8.

### 4.5 Progress / status / completion markers

`progress.json` (orchestrator-owned, written / overwritten
atomically as secondaries progress):

    {
      "schema_version": "trafficflow_runner_phase_e_v1",
      "run_id": "<RUN_ID>",
      "started_at": "<UTC ISO>",
      "k_range": [1, 2, 3, 4, 5, 6],
      "secondaries": [
        {"secondary": "SPY",  "status": "complete",
         "started_at": "...", "ended_at": "...",
         "worker_id": 3},
        {"secondary": "AAPL", "status": "in_progress",
         "started_at": "...", "ended_at": null,
         "worker_id": 1},
        ...
      ]
    }

Per-secondary status values:

- `pending` (not yet assigned to a worker)
- `in_progress`
- `complete` (`.done` written successfully)
- `failed` (worker exited non-zero or completion validation
  failed)
- `quarantined` (failed and moved under `.quarantine/`)
- `skipped` (operator-excluded for this run)

`run_status.json` (orchestrator-owned, terminal):

    {
      "schema_version": "trafficflow_runner_phase_e_v1",
      "run_id": "<RUN_ID>",
      "status": "complete" | "partial" | "failed" | "interrupted",
      "started_at": "<UTC ISO>",
      "ended_at": "<UTC ISO>",
      "elapsed_seconds": ...,
      "secondaries_requested": <N>,
      "secondaries_complete": <N>,
      "secondaries_failed": <N>,
      "secondaries_quarantined": <N>,
      "secondaries_skipped": <N>,
      "k_range": [1, 2, 3, 4, 5, 6]
    }

Terminal run statuses:

- `complete`: every requested secondary terminated in `complete`.
- `partial`: at least one `complete` and at least one
  non-`complete` (`failed` or `quarantined`).
- `failed`: zero `complete`.
- `interrupted`: orchestrator killed or timed out; some
  secondaries may be in `in_progress`.

`run_manifest.json` (orchestrator-owned, written at terminal
status): the existing Phase C schema extended to
`trafficflow_runner_phase_e_v1` with:

- All Phase C fields (timing, effective_config, write_summary,
  per_cell_summary, canonical_artifacts_referenced, output_dir,
  artifacts_written).
- Plus `run_status` (matches `run_status.json` `status` field).
- Plus `quarantined_secondaries` (list of secondary names with
  failure summaries).
- Plus orchestrator-level provenance (orchestrator version,
  invocation args, worker count, worker_assignments).

`run.stdout.json` for Phase E:

- Optional / diagnostic in canonical mode. Recommendation: keep
  writing it for parity with Phase C (downstream tooling that
  already consumes the Phase C stdout sidecar continues to
  work), but mark it explicitly diagnostic in the schema docs.
- Phase E orchestrator emits its own sanitized envelope to
  stdout; the on-disk `run.stdout.json` mirrors that envelope.

`.done` marker per secondary: zero-byte file at
`<RUN_ID>/<SECONDARY>/.done`. Single canonical signal that
downstream consumers gate on. Cannot be confused with valid JSON;
cannot be partially written.

### 4.6 Selected-build provenance at canonical scale

Provenance lives in the run-level `run_manifest.json` under
`canonical_artifacts_referenced` (one entry per secondary). The
Phase C schema already carries:

- `selected_build_path`
- `selected_build_sha256`
- `explicit_build_override`
- `selected_run_dir`
- `combo_leaderboard_path`

Phase E should add:

- `k_range` (the requested K cells for this secondary)
- `stackbuilder_root` (so a future audit can reproduce the
  selected-build resolution)
- `runner_version` (runner module version string or commit-SHA)
- `schema_version` of the per-entry record itself, for forward
  compatibility

Per-secondary `secondary_manifest.json` is **deferred** unless a
downstream consumer needs per-secondary provenance separable from
the run-level manifest.

### 4.7 Artifact list completeness

The canonical artifact enumeration in `run_manifest.json`
`artifacts_written` must include, for each completed secondary:

- 6 board-row JSON files
- 6 board-row CSV files
- `.done` marker

And at the run-level:

- `progress.json` (final state)
- `run_status.json`
- `run_manifest.json` (self-reference)
- `run.stdout.json` (if retained)

Failed / quarantined secondaries must NOT appear in
`artifacts_written`. A separate top-level `failure_artifacts` list
should enumerate the `.quarantine/<SEC>/failure.json` files. This
keeps the success path artifact list a single source of truth for
downstream consumers while preserving an audit trail.

`selected_output.json` is updated by the orchestrator separately
(Section 8) and lives at `output/trafficflow/selected_output.json`,
not under the run root. It should be listed in `artifacts_written`
only when the orchestrator updates it as part of this run.

---

## 5. External Process Fan-Out Safety Requirements

PR #315 / PR #316 established that external process fan-out is
the safe concurrency model. Phase E must support multiple runner
subprocesses writing distinct secondaries under the same canonical
`<RUN_ID>/` root.

### 5.1 Per-secondary collision safety

- Each `<SECONDARY>/` subdirectory is owned by exactly one worker
  subprocess at a time.
- The orchestrator MUST refuse to assign the same secondary to
  two workers.
- If the orchestrator detects a pre-existing `<SECONDARY>/`
  directory under the run root that does not match a known
  worker assignment, it MUST quarantine or refuse rather than
  overwrite.

### 5.2 Run-level collision safety

Multiple worker subprocesses MUST NOT concurrently write any
shared run-level files. The following are orchestrator-owned and
written by the orchestrator process only:

- `progress.json`
- `run_status.json`
- `run_manifest.json`
- `run.stdout.json`
- `selected_output.json` (and never under the run root - it
  lives at the canonical root)

Workers write only:

- `<RUN_ID>/<SECONDARY>/board_rows_k=<K>.{json,csv}` (per assigned secondary)
- `<RUN_ID>/<SECONDARY>/.done` (after success)
- `<RUN_ID>/.quarantine/<SECONDARY>/failure.json` (after
  controlled failure)

### 5.3 Worker contract

- Receives exactly one secondary (or a small batch the
  orchestrator pre-assigns and does not overlap with other
  workers).
- Writes only its assigned secondary directory and per-secondary
  failure record if quarantined.
- Reports success/failure to the orchestrator via process exit
  code and per-worker stdout / stderr.
- Does not read or update shared run-level files.
- Does not update `selected_output.json`.
- Uses the same `_default_compute_loader` pinning pattern the
  current Phase C runner uses; PR #308 surface block remains in
  effect.

### 5.4 Orchestrator / finalizer contract

The orchestrator:

- Creates the run root and the empty `progress.json` /
  `run_status.json` skeletons.
- Assigns secondary ownership to workers in a way that does not
  overlap.
- Spawns runner worker subprocesses with explicit
  `--canonical-write` and a per-worker `--secondaries <SEC>`
  argument.
- Monitors completion (per-worker exit codes; per-secondary
  `.done` markers as a secondary check).
- Periodically updates `progress.json` atomically (`<file>.tmp` +
  `os.replace`).
- Writes the final `run_manifest.json` and `run_status.json`
  after every worker has terminated.
- Updates `output/trafficflow/selected_output.json` only if the
  run reaches an acceptable terminal status (Section 8).
- Handles resume / retry / quarantine per Section 6.

### 5.5 Runner / orchestrator boundary

Recommendation: introduce **both**

- runner-level `--canonical-write` for single-secondary worker
  writes (the runner already runs one secondary at a time when
  given a single `--secondaries` value); and
- separate orchestrator script for fan-out, progress, status,
  manifest, and `selected_output.json` finalization.

Reason: the runner already does the per-secondary work correctly
in isolated mode (PR #309 / PR #310 evidence). Adding
`--canonical-write` lifts the canonical-root refusal under a new
mode bit; the orchestrator owns everything that must NOT be
written by concurrent workers.

The runner MUST refuse `--canonical-write` if more than one
secondary is passed in a single invocation. This is the minimum
safety guard against an operator running `trafficflow_runner.py
--canonical-write --secondaries SPY,AAPL,AMZN,...` directly,
which would race shared run-level files.

---

## 6. Failure Handling and Resumability

### 6.1 Failure handling

Worker-level failures:

- Worker exits non-zero -> orchestrator marks secondary `failed`
  in `progress.json`.
- Orchestrator inspects worker stdout / stderr, builds a failure
  record at `<RUN_ID>/.quarantine/<SECONDARY>/failure.json` with:
  - `secondary`
  - `worker_id`
  - `exit_code`
  - `stderr_tail` (last N lines, sanitized)
  - `started_at`, `ended_at`, `elapsed_seconds`
  - `assigned_k_range`
  - failure category (`compute_error`, `validation_failed`,
    `timeout`, `process_killed`, `unknown`)
- Marks secondary `quarantined` after failure.json is written.

Completion-validation failures:

- Worker exited 0 but missing one or more required board-row
  files at validation time -> orchestrator treats as
  `validation_failed`, quarantines per above.

Orchestrator-level failures:

- Orchestrator is killed mid-run -> on next invocation with
  `--resume <RUN_ID>` (Phase E PR Gamma scope), reads
  `progress.json` and rebuilds worker assignments from
  remaining `pending` / `in_progress` secondaries. `in_progress`
  is treated as orphaned and re-attempted; the orchestrator
  must verify the prior worker is no longer running before
  re-assignment.

### 6.2 Resumability

- A canonical run root is resumable from `progress.json` plus
  per-secondary `.done` markers.
- Operator command: `--resume <RUN_ID>` skips any secondary
  whose `.done` exists, re-attempts any `pending` /
  `in_progress` / `failed` / `quarantined` secondary unless
  `--no-retry-failed` is also passed.
- Resume is bounded by the original `k_range` recorded in
  `progress.json`; the operator cannot change the K-range
  mid-run.

---

## 7. Heavy-Stage Policy for K=7..12 / K=10..12

Phase E daily-cadence canonical writes are **K=1..6 only**.

The runner MUST refuse canonical writes when the requested
K-range contains any K > 6, with stable reason code
`canonical_write_heavy_stage_requires_flag`. The refusal MUST
fire before any compute, mirroring the Phase C canonical-root
refusal pattern.

A future flag (working name `--heavy-stage`) authorizes K > 6
canonical writes, but heavy-stage implementation is
**deferred to Phase F or later**. Heavy-stage canonical writes
have qualitatively different requirements:

- Chunked invocation (one secondary at a time, or one
  (secondary, K-subset) at a time).
- Per-(secondary, K) atomicity, not just per-secondary, because
  a single K=12 cell can run 14+ minutes (PR #313 MSFT K=12
  863.30 s) and partial writes within a secondary are likely.
- Resumability per cell, not just per secondary.
- Partial publishing per cell.
- Cadence: weekly or on-demand, not daily.

Phase E daily-cadence design must NOT contaminate the K=1..6
path with heavy-stage policies; the two paths are operated
separately.

---

## 8. Downstream Handoff / `selected_output.json` Policy

`output/trafficflow/selected_output.json` is the primary
downstream-consumer handoff.

### 8.1 Schema

    {
      "schema_version": "trafficflow_selected_output_v1",
      "selected_run_id": "<RUN_ID>",
      "selected_run_root": "output/trafficflow/runs/<RUN_ID>",
      "selected_at_utc": "<UTC ISO>",
      "run_completed_at_utc": "<UTC ISO>",
      "run_status": "complete" | "partial",
      "k_range": [1, 2, 3, 4, 5, 6],
      "secondary_count": <N requested>,
      "successful_secondary_count": <N complete>,
      "failed_secondary_count": <N failed + N quarantined>,
      "manifest_path": "output/trafficflow/runs/<RUN_ID>/run_manifest.json",
      "provenance_summary": {
        "stackbuilder_root": "output/stackbuilder",
        "runner_version": "...",
        "orchestrator_version": "...",
        "git_head": "..."
      },
      "generated_by": "<operator | scheduled job | etc>"
    }

### 8.2 Update policy

- Updated only after run finalization (orchestrator writes
  `run_status.json` first, then conditionally updates
  `selected_output.json`).
- Updated on `run_status == "complete"`: always.
- Updated on `run_status == "partial"`: only if the operator
  explicitly passes `--allow-partial-publish` to the
  orchestrator. Default behavior: do NOT update
  `selected_output.json` on partial runs (the previous run
  remains the selected one until a fresh successful run lands).
- Never updated on `failed` or `interrupted`.
- Atomic update via `<file>.tmp` + `os.replace`. The destination
  is `output/trafficflow/selected_output.json`; the tmp lives at
  `output/trafficflow/selected_output.json.tmp`. Same-volume
  rename is reliable.

### 8.3 Symlink / latest-pointer policy

Defer. No `latest/` symlink or junction in the minimum Phase E
slice. Downstream consumers read `selected_output.json` and follow
its `selected_run_root` field. This is platform-agnostic and
race-free.

---

## 9. Required Runner Amendments Before Implementation

Enumerated in dependency order.

### 9.1 CLI: `--canonical-write`

- New flag, distinct from `--write`.
- Allows `output/trafficflow/` (and only `output/trafficflow/`)
  as the resolved output dir.
- All other current canonical-root refusals (cache, status,
  price_cache, signal_library, etc.) remain in effect.
- The PR #308 network / cache-write surface block remains in
  effect; canonical write does NOT imply `--allow-network-fetch`.
- The runner MUST refuse `--canonical-write` if more than one
  secondary is passed in a single invocation (worker-mode
  invariant). Reason code:
  `canonical_write_multi_secondary_unsupported_use_orchestrator`.

### 9.2 CLI: `--heavy-stage`

- Required when the requested K-range contains K > 6 under
  canonical mode.
- Without it: refuse with
  `canonical_write_heavy_stage_requires_flag`.
- The flag itself does NOT enable any new heavy-stage code path
  in Phase E; it merely lifts the K>6 refusal. Phase F or later
  is the actual heavy-stage implementation.

### 9.3 Per-secondary canonical writer

- New helper or extended `_execute_isolated_write` to handle
  canonical-mode writes.
- Uses the existing atomic per-file pattern.
- After all 12 board-row files succeed and validate, writes the
  zero-byte `.done` marker via `_atomic_write_bytes`.
- On any board-row write failure, moves the partial secondary
  directory under `.quarantine/<SECONDARY>/` and writes
  `failure.json`.

### 9.4 Orchestrator / finalizer (new module)

- New file (e.g. `trafficflow_canonical_orchestrator.py`)
  separate from `trafficflow_runner.py`.
- Owns: run root creation, progress.json, run_status.json, final
  run_manifest.json, selected_output.json, worker subprocess
  fan-out, resume.
- Uses the same `--canonical-write` runner CLI as its worker
  invocation surface.
- Defaults to external process fan-out (PR #315 / PR #316
  evidence). Worker count tunable via `--workers N` with safe
  default 4 (per PR #316 model).

### 9.5 Schema version

- Introduce `PHASE_E_RUN_MANIFEST_SCHEMA = "trafficflow_runner_phase_e_v1"`
  alongside the existing `PHASE_C_RUN_MANIFEST_SCHEMA`.
- Canonical-mode manifests use the new schema; isolated-mode
  manifests keep the Phase C schema.
- Downstream consumers must reject unknown schema versions.

### 9.6 Test coverage

Required tests (all mockable, none require real canonical
writes):

- `--canonical-write` refused for non-`output/trafficflow/` paths.
- `--canonical-write` accepted for `output/trafficflow/`.
- `--canonical-write` refused when multiple secondaries passed.
- K > 6 refused without `--heavy-stage` under
  `--canonical-write`.
- K > 6 accepted with both flags (acceptance is gate-lift; no
  actual K > 6 canonical writer in Phase E).
- Per-secondary atomic success path:
  `<SECONDARY>/{board_rows*, .done}` all present after a
  mocked-compute run.
- Per-secondary failure path: partial directory moved to
  `.quarantine/<SECONDARY>/` with `failure.json`.
- `.done` marker semantics: downstream consumers gate on its
  presence.
- Progress / status manifest schema validation.
- `selected_output.json` atomic update on complete run.
- `selected_output.json` NOT updated on partial run without
  `--allow-partial-publish`.
- Privacy sanitization holds for canonical paths (repo-relative
  POSIX renders cleanly).
- Concurrent worker collision: orchestrator refuses to assign
  the same secondary to two workers.

---

## 10. Phase E Implementation Checklist (Bounded PR Sequence)

PR Alpha (CLI + schema, no real writes):

- `--canonical-write` and `--heavy-stage` CLI flags.
- `canonical_write_blocked` becomes a tri-state: `forbidden_phase_c`
  (default), `allowed_canonical_write` (when `--canonical-write`
  is passed), `allowed_heavy_stage` (when both flags are passed).
- Phase C canonical-root refusal lifted only under
  `--canonical-write` mode; refusals for all other canonical
  roots remain.
- `PHASE_E_RUN_MANIFEST_SCHEMA` constant.
- Reason codes:
  `canonical_write_multi_secondary_unsupported_use_orchestrator`,
  `canonical_write_heavy_stage_requires_flag`.
- Tests only with mocked compute / tmp_path.
- No real canonical write.

PR Beta (canonical writer mechanics, no orchestrator):

- Per-secondary canonical writer reuses
  `_execute_isolated_write` shape with canonical-mode
  awareness.
- `.done` marker write.
- Failure path: move to `.quarantine/<SECONDARY>/` and write
  `failure.json`.
- Phase E `run_manifest.json` schema extensions.
- Tests with `tmp_path` canonical roots and mocked compute.
- Still no real `output/trafficflow/` writes.

PR Gamma (orchestrator / finalizer):

- New `trafficflow_canonical_orchestrator.py` module.
- External process fan-out with `--workers N` (default 4).
- `progress.json`, `run_status.json`, final `run_manifest.json`,
  `selected_output.json` (atomic update).
- Worker ownership / collision guard.
- `--resume <RUN_ID>` support.
- Tests with mocked runner workers (subprocess simulated via a
  fake worker that writes the expected output structure).

PR Delta (first real canonical-write smoke):

- SPY and AAPL only, K=1..6.
- Real compute, real `output/trafficflow/runs/<RUN_ID>/`.
- Supervised, pre/post canonical safety snapshots.
- Evidence-only doc.

PR Epsilon (broader real canonical-write smoke):

- All 8 Phase 6I-79 secondaries, K=1..6.
- External process fan-out via PR Gamma orchestrator.
- Evidence-only doc.

PR Zeta (downstream consumer handoff validation, if not already
complete):

- Validate `selected_output.json` consumed correctly by the
  first downstream consumer (MTF bridge, Confluence ranking, or
  whichever lands first).

Heavy stage (K > 6): deferred to Phase F or later. Out of scope
for the daily-cadence path.

---

## 11. Findings

11.1 The runner today is well-structured for a clean Phase E
addition. The Phase C canonical-root refusal at
`trafficflow_runner.py:116-121, 2151-2169` is a single gate that
can be lifted under a new `--canonical-write` mode without
rewriting the write surface.

11.2 The atomic write helpers (`_atomic_write_bytes`,
`_atomic_write_json`) and per-file `.tmp` + `os.replace` pattern
are exactly what Phase E needs at the per-secondary level. No new
atomic primitives required.

11.3 The privacy sanitization layer (`sanitize_for_json`,
`path_for_output`) handles repo-relative POSIX paths under
`output/trafficflow/` correctly without modification.

11.4 The lazy compute wrapper's module-level monkey-patch
pattern remains the binding constraint on in-process threading.
External process fan-out via the orchestrator (Section 5)
sidesteps this constraint cleanly.

11.5 The current `run_manifest.json` schema is a strong foundation;
Phase E adds `run_status`, `quarantined_secondaries`, and
orchestrator-level provenance to the same shape under a new
schema version.

11.6 K=10..12 must remain physically separated from the daily
path via the `--heavy-stage` refusal. The daily K=1..6 surface
must not contaminate heavy-stage policy and vice versa.

11.7 `selected_output.json` is the right downstream handoff;
symlinks / junctions add cross-platform burden and are
unnecessary at this scale.

11.8 Bounded PRs (Alpha through Epsilon) keep risk auditable.
PR Alpha is purely CLI + tests; the first real canonical write
does not land until PR Delta.

---

## 12. Recommendation

**PASS WITH NOTES.** The Phase E canonical-write contract is
defined; implementation should proceed as the bounded PR sequence
above, starting with PR Alpha. Heavy-stage K=7..12 is explicitly
deferred to Phase F or later.

Direct answers to the seven scoping questions:

a. **Can Phase E implementation begin immediately, or are runner
   amendments needed first?** Runner amendments are needed first.
   PR Alpha (CLI flags + schema constants + refusal logic, tests
   only) MUST land before any real canonical-write run is
   attempted.

b. **What is the minimum safe Phase E implementation slice?** PR
   Alpha + PR Beta + PR Gamma. PR Alpha lifts the Phase C refusal
   under an explicit mode. PR Beta adds the per-secondary atomic
   writer. PR Gamma adds the orchestrator / finalizer. PR Delta
   is the first real smoke (SPY + AAPL only).

c. **What files / directories should canonical writes produce?**
   See Section 4.2. Minimum required: per-secondary
   `board_rows_k=1..6.{json,csv}` (12 files) plus `.done`;
   run-level `progress.json`, `run_status.json`,
   `run_manifest.json`, and atomically-updated
   `output/trafficflow/selected_output.json`. `run.stdout.json`
   retained for parity. `.quarantine/<SEC>/failure.json` on
   failure. Per-secondary manifest deferred.

d. **Is external process fan-out safe with the proposed
   contract?** Yes. Workers own their secondary directories;
   orchestrator owns shared run-level files; collision safety is
   enforced by the orchestrator's worker-assignment scheme and
   by the runner's refusal of `--canonical-write` with multi-
   secondary input. PR #315 / PR #316 already validated the
   external-process-fan-out concurrency model at 4 and 16
   workers respectively.

e. **What must downstream consumers rely on as completion /
   progress markers?** Two-level gating:
   - Per-secondary: `<RUN_ID>/<SECONDARY>/.done` zero-byte
     marker. If absent, do not consume that secondary.
   - Run-level: `<RUN_ID>/run_status.json` `status` field. If
     not `complete` or `partial`, do not promote to selected.
   `selected_output.json` at the canonical root names the
   currently selected run.

f. **Should `selected_output.json` be implemented now or
   deferred?** Implement in PR Gamma alongside the orchestrator.
   It is the downstream-consumer handoff and Phase E cannot
   ship without it; deferring it would leave consumers without
   a contract.

g. **How should K=10..12 be kept out of daily cadence?** Two
   mechanisms in concert: (1) The runner's `--canonical-write`
   mode REFUSES canonical writes if the requested K-range
   contains K > 6, with refusal reason
   `canonical_write_heavy_stage_requires_flag`. The refusal
   fires before any compute. (2) The daily orchestrator does
   not pass `--heavy-stage` to its worker subprocesses;
   heavy-stage is its own separate orchestrator track (Phase F
   or later) with its own chunking / resumability /
   partial-publishing requirements.

---

This was a scoping task. No code or test changes were made. No
canonical writes were performed. Contract recommendations are
operator-decision inputs, not unilateral commitments. Phase E
implementation should follow as bounded PRs starting with PR
Alpha (CLI + schema + tests only). Heavy-stage K=7..12 is
deferred to Phase F or later. All session evidence under
`<SESSION_DIR>` is gitignored. The current runner's atomic write
helpers, privacy sanitization, selected-build pinning, and
PR #308 network / cache-write surface block are direct re-uses
in canonical mode; the additions Phase E needs are an explicit
mode flag, per-secondary `.done` markers, an orchestrator /
finalizer for shared run-level files, and the
`selected_output.json` downstream handoff. The runner-internal
threading amendment described in PR #315 Section 3.5 remains a
deferrable optimization, NOT a Phase E gate.
