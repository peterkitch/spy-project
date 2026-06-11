"""Stage 9 -- operator-launched publish tail (fail-closed transaction library).

After Stages 1-8 succeed, this module completes the proven manual publish
sequence WITHOUT another human stop: same-run CCC upload -> combine/proof
assembly -> commit the Phase-5 report pair to md_library/shared -> promote
dry-run gate -> promote write -> commit the publication file allowlist -> push
-> verify the live promotion manifest.

It is an OPERATOR-LAUNCHED program (see CLAUDE.md PART B2): the operator runs it
outside the Claude Code harness, with BLOB_READ_WRITE_TOKEN in the environment
and a non-interactive git credential. It is fail-closed at every gate, never
performs a partial publish, and writes a transaction state file plus a refusal
envelope under the run dir.

Every external effect (Blob upload, combine, promote, git, HTTP, sleep, clock,
env) is an injectable seam so the whole tail is hermetically testable with no
real network, Blob, promote write, or engine. The Blob token is read as a
boolean presence only and never recorded.

ASCII-only. Stdlib + the in-process publish helpers only.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

TOKEN_ENV = "BLOB_READ_WRITE_TOKEN"
LIVE_MANIFEST_URL_DEFAULT = (
    "https://prjct9.com/fixtures/k6_mtf_ranking.promotion_manifest.json")
STAGE9_LOCK_NAME = ".stage9_publish.lock"
PUBLISH_STATE_NAME = "publish_state.json"
PUBLISH_REFUSAL_NAME = "publish_refusal.json"

# Ordered transaction states.
PUBLISH_STATES = (
    "preflight_ok",
    "ccc_uploaded",
    "combined_ok",
    "report_pair_written_to_worktree",
    "promote_dry_run_ok",
    "promote_write_ok",
    "commit_created",
    "push_ok",
    "live_manifest_verified",
)

# The exact CCC records contract field set (mirrors fresh_ccc_blob_upload).
_CCC_RECORD_FIELDS = (
    "secondary", "pathname", "url", "sha256", "byte_size", "points",
    "first_date", "last_date", "reused", "get_verified",
)


class Stage9Error(Exception):
    """Fail-closed refusal raised by the Stage 9 publish tail. Carries the
    stage name and safe diagnostic fields (never a token value)."""

    def __init__(self, stage: str, reason: str, **diag: Any) -> None:
        super().__init__(f"[{stage}] {reason}")
        self.stage = stage
        self.reason = reason
        self.diag = {k: v for k, v in diag.items() if k != "token"}


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class Stage9PublishInputs:
    """All inputs + injectable seams for a Stage 9 publish run.

    Paths are absolute. ``repo_root`` is the git top-level (the publication
    files + report pair live under it). ``run_dir`` holds the run's artifacts,
    state file, refusal, and lock. Seams default to real implementations but
    tests inject fakes so no real Blob/network/promote-write happens.
    """

    repo_root: Path
    run_dir: Path
    run_id: str
    fresh_secondaries: Sequence[str]
    fresh_rows: Sequence[Mapping[str, Any]]
    fresh_validation_sidecar: Mapping[str, Any]
    k6_ranking_path: Path
    prior_fixture_path: Path
    prior_promotion_manifest_path: Path
    prior_validation_sidecar_path: Path
    prior_ccc_verification_manifest_path: Path
    candidate_dir: Path
    fresh_ccc_records_path: Path
    public_fixture_dest: Path
    public_manifest_dest: Path
    md_library_shared_dir: Path
    project_root: Path
    excluded_tickers: Sequence[str] = ()
    live_manifest_url: str = LIVE_MANIFEST_URL_DEFAULT
    poll_timeout_seconds: float = 600.0
    poll_interval_seconds: float = 20.0
    operator_approved: bool = False
    dry_run: bool = False
    # --- injectable seams (None -> real implementation) ---
    env: Optional[Mapping[str, str]] = None
    subprocess_runner: Optional[Callable[..., Any]] = None
    http_getter: Optional[Callable[[str], Any]] = None
    sleeper: Optional[Callable[[float], None]] = None
    clock: Optional[Callable[[], float]] = None
    upload_func: Optional[Callable[..., Mapping[str, Any]]] = None
    combine_func: Optional[Callable[..., Mapping[str, Any]]] = None
    promote_func: Optional[Callable[..., Mapping[str, Any]]] = None
    promote_inputs_cls: Optional[Callable[..., Any]] = None

    # --- seam accessors (lazy real defaults) ---
    def _env(self) -> Mapping[str, str]:
        return self.env if self.env is not None else os.environ

    def _clock(self) -> float:
        return (self.clock or time.monotonic)()

    def _sleep(self, seconds: float) -> None:
        (self.sleeper or time.sleep)(seconds)

    def _upload(self):
        if self.upload_func is not None:
            return self.upload_func
        from fresh_ccc_blob_upload import upload_fresh_ccc  # noqa: PLC0415
        return upload_fresh_ccc

    def _combine(self):
        if self.combine_func is not None:
            return self.combine_func
        from crunch_combine_proof import combine_and_assemble  # noqa: PLC0415
        return combine_and_assemble

    def _promote(self):
        if self.promote_func is not None:
            return self.promote_func
        from utils.react_publish.promote_k6_mtf_artifact import (  # noqa: PLC0415
            promote)
        return promote

    def _promote_inputs_cls(self):
        if self.promote_inputs_cls is not None:
            return self.promote_inputs_cls
        from utils.react_publish.promote_k6_mtf_artifact import (  # noqa: PLC0415
            PromotionInputs)
        return PromotionInputs


# ---------------------------------------------------------------------------
# Small fail-closed helpers
# ---------------------------------------------------------------------------


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".part",
                              dir=str(path.parent))
    os.close(fd)
    tp = Path(tmp)
    try:
        tp.write_bytes(data)
        os.replace(str(tp), str(path))
    finally:
        try:
            if tp.exists():
                tp.unlink()
        except OSError:
            pass


def _atomic_write_json(path: Path, obj: Any) -> None:
    _atomic_write_bytes(
        path, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("ascii"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _lf_sha256_file(path: Path) -> str:
    data = Path(path).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Stage9Error("io", f"{label} not found: {Path(path).name}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise Stage9Error("io", f"{label} unreadable/invalid JSON") from exc


def _repo_rel(repo_root: Path, p: Path) -> str:
    return Path(p).resolve().relative_to(Path(repo_root).resolve()).as_posix()


def _state_path(run_dir: Path) -> Path:
    return Path(run_dir) / PUBLISH_STATE_NAME


def _read_state(run_dir: Path) -> dict:
    p = _state_path(run_dir)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _record_state(inputs: Stage9PublishInputs, state: str, **extra: Any) -> dict:
    """Append a completed transaction state, atomically. Never records a token."""
    assert state in PUBLISH_STATES, state
    cur = _read_state(inputs.run_dir)
    completed = list(cur.get("completed_states") or [])
    if state not in completed:
        completed.append(state)
    safe_extra = {k: v for k, v in extra.items() if k != "token"}
    fields = dict(cur.get("fields") or {})
    fields.update(safe_extra)
    payload = {
        "run_id": inputs.run_id,
        "dry_run": bool(inputs.dry_run),
        "operator_approved": bool(inputs.operator_approved),
        "last_state": state,
        "completed_states": completed,
        "fields": fields,
    }
    _atomic_write_json(_state_path(inputs.run_dir), payload)
    return payload


def _write_refusal(inputs: Stage9PublishInputs, err: Stage9Error,
                   **extra: Any) -> dict:
    env = {
        "schema": "stage9_publish_refusal_v1",
        "run_id": inputs.run_id,
        "stage": err.stage,
        "reason": err.reason,
        "no_partial_publish": True,
    }
    env.update({k: v for k, v in err.diag.items() if k != "token"})
    env.update({k: v for k, v in extra.items() if k != "token"})
    _atomic_write_json(Path(inputs.run_dir) / PUBLISH_REFUSAL_NAME, env)
    return env


# ---------------------------------------------------------------------------
# Run lock (O_CREAT|O_EXCL; held through live verify or refusal; no reclaim)
# ---------------------------------------------------------------------------


def _pid_alive(pid: Any) -> Optional[bool]:
    """True/False if determinable; None if liveness cannot be determined."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def _lock_path(run_dir: Path) -> Path:
    return Path(run_dir) / STAGE9_LOCK_NAME


def acquire_stage9_lock(inputs: Stage9PublishInputs, *, stage: str) -> Path:
    lp = _lock_path(inputs.run_dir)
    lp.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"run_id": inputs.run_id, "pid": os.getpid(), "stage": stage,
         "mode": "publish_dry_run" if inputs.dry_run else "publish"},
        sort_keys=True).encode("ascii")
    try:
        fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = {}
        try:
            holder = json.loads(lp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            holder = {}
        alive = _pid_alive(holder.get("pid"))
        if alive is False:
            raise Stage9Error(
                "lock",
                "stage 9 lock is stale (holder pid not alive); remove "
                f"{STAGE9_LOCK_NAME} in the run dir manually to reclaim",
                holder_pid=holder.get("pid"), lock_stale=True)
        raise Stage9Error(
            "lock", "stage 9 lock held by another run; refusing",
            holder_pid=holder.get("pid"), lock_stale=False)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return lp


def release_stage9_lock(run_dir: Path) -> None:
    try:
        lp = _lock_path(run_dir)
        if lp.is_file():
            lp.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Git seam
# ---------------------------------------------------------------------------


def _default_subprocess_runner(argv: Sequence[str], *, cwd: str,
                               env: Mapping[str, str]) -> Any:
    return subprocess.run(list(argv), cwd=cwd, env=dict(env),
                          capture_output=True, text=True)


class _GitResult:
    """Synthetic failed result returned when the git seam itself raises, so a
    subprocess/runner exception is converted into a normal non-zero result that
    every caller's _git_ok() check turns into a correctly-attributed
    Stage9Error -- the exception never escapes _run_git. Stderr carries the
    exception CLASS name only (never str(exc), which could contain env data)."""

    def __init__(self, stderr: str) -> None:
        self.returncode = 1
        self.stdout = ""
        self.stderr = stderr


def _run_git(inputs: Stage9PublishInputs, args: Sequence[str]) -> Any:
    runner = inputs.subprocess_runner or _default_subprocess_runner
    base_env = dict(inputs._env())
    base_env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return runner(["git", *args], cwd=str(inputs.repo_root), env=base_env)
    except BaseException as exc:  # noqa: BLE001 - never let the seam escape
        return _GitResult(f"git invocation error: {type(exc).__name__}")


def _git_ok(result: Any) -> bool:
    return int(getattr(result, "returncode", 1)) == 0


def _git_out(result: Any) -> str:
    return (getattr(result, "stdout", "") or "").strip()


def _git_err(result: Any) -> str:
    return (getattr(result, "stderr", "") or "").strip()


def _git_required(inputs: Stage9PublishInputs, args: Sequence[str], stage: str,
                  reason: str, **extra: Any) -> Any:
    """Run git and FAIL CLOSED when it does not exit 0. _run_git converts a
    runner/subprocess exception into a synthetic non-zero result with EMPTY
    stdout, so an unchecked caller could read that empty stdout as a real value
    -- the nastiest case being an empty `status --porcelain` reading as a clean
    worktree. Every site where a git failure MUST stop the publish routes
    through here: the non-zero result raises a Stage9Error carrying only the git
    CLASS-name stderr (never raw exception text). ``extra`` keyword fields (e.g.
    deploy_failed_after_push=True) are attached to the Stage9Error so the
    backstop takes the right control-flow path."""
    result = _run_git(inputs, list(args))
    if not _git_ok(result):
        raise Stage9Error(stage, reason, git_stderr=_git_err(result), **extra)
    return result


def _git_out_required(inputs: Stage9PublishInputs, args: Sequence[str],
                      stage: str, reason: str, **extra: Any) -> str:
    """As _git_required, returning stripped stdout that is GUARANTEED to come
    from an exit-0 git command -- never the blank stdout of a swallowed
    failure."""
    return _git_out(_git_required(inputs, args, stage, reason, **extra))


def _seam(stage: str, fn, *args, token_sensitive: bool = False, **kwargs):
    """Call an external/side-effect seam and convert ANY exception (other than a
    Stage9Error, which is already sanitized) into a sanitized Stage9Error so the
    refusal envelope is always written and nothing escapes. For a token-sensitive
    seam (the Blob upload) the reason and diagnostics carry ONLY the exception
    CLASS name -- never str(exc) or its args, which could contain credentials."""
    try:
        return fn(*args, **kwargs)
    except Stage9Error:
        raise
    except BaseException as exc:  # noqa: BLE001
        cls = type(exc).__name__
        if token_sensitive:
            raise Stage9Error(
                stage, f"{stage} seam raised {cls}: details suppressed (may "
                "contain credentials)", exc_type=cls) from None
        raise Stage9Error(
            stage, f"{stage} seam raised an unexpected {cls}", exc_type=cls
        ) from None


# ---------------------------------------------------------------------------
# Step 1: preflight
# ---------------------------------------------------------------------------


def verify_publish_preflight(inputs: Stage9PublishInputs, *,
                             acquire_lock: bool = True) -> dict:
    """Fail-closed preflight. When ``acquire_lock`` is True, acquires the run
    lock LAST (on success) and leaves it held; the caller (run_stage9_publish)
    releases it. The orchestrator invokes this with ``acquire_lock=False`` before
    Stage 1 so a doomed run halts before compute without holding the publish lock
    through the multi-hour build. Records only token_present (boolean) -- never
    the token value."""
    if not inputs.operator_approved:
        raise Stage9Error("preflight",
                          "publish mode requires operator_approved=true")

    token_present = bool(str(inputs._env().get(TOKEN_ENV, "")).strip())
    if not token_present:
        raise Stage9Error("preflight",
                          f"{TOKEN_ENV} is not present in the environment",
                          token_present=False)

    # Non-interactive git probe (argv array; GIT_TERMINAL_PROMPT=0).
    probe = _run_git(inputs, ["ls-remote", "origin", "refs/heads/main"])
    if not _git_ok(probe):
        raise Stage9Error(
            "preflight", "git ls-remote origin probe failed (non-interactive "
            "credential unavailable)", git_stderr=_git_err(probe),
            token_present=True)

    branch = _git_out_required(
        inputs, ["rev-parse", "--abbrev-ref", "HEAD"], "preflight",
        "git rev-parse --abbrev-ref HEAD failed", token_present=True)
    if branch != "main":
        raise Stage9Error("preflight",
                          f"current branch is {branch!r}, not 'main'",
                          token_present=True)

    status = _run_git(inputs, ["status", "--porcelain"])
    if not _git_ok(status):
        raise Stage9Error("preflight", "git status failed",
                          git_stderr=_git_err(status), token_present=True)
    if _git_out(status):
        raise Stage9Error("preflight",
                          "tracked worktree is not clean before publish",
                          token_present=True)

    head = _git_out_required(inputs, ["rev-parse", "HEAD"], "preflight",
                             "git rev-parse HEAD failed", token_present=True)
    origin = _git_out_required(inputs, ["rev-parse", "origin/main"], "preflight",
                               "git rev-parse origin/main failed",
                               token_present=True)
    if origin and head:
        anc = _run_git(inputs, ["merge-base", "--is-ancestor", origin, "HEAD"])
        if not _git_ok(anc):
            raise Stage9Error(
                "preflight",
                "origin/main is not an ancestor of local HEAD (diverged); "
                "refusing", local_head=head, origin_main=origin,
                token_present=True)

    # Prior fixture LF SHA must match prior promotion manifest source_sha256.
    prior_promo = _read_json(inputs.prior_promotion_manifest_path,
                             "prior promotion manifest")
    declared = str(prior_promo.get("source_sha256") or "").strip().lower()
    actual = _lf_sha256_file(inputs.prior_fixture_path)
    if declared != actual:
        raise Stage9Error(
            "preflight",
            "prior fixture LF SHA does not match prior promotion manifest "
            "source_sha256", manifest_source_sha256=declared,
            fixture_lf_sha256=actual, token_present=True)

    # Acquire the run lock LAST; it stays held until release in run_stage9_publish.
    if acquire_lock:
        acquire_stage9_lock(inputs, stage="preflight")
    return {"ok": True, "token_present": True, "local_head": head,
            "origin_main": origin, "branch": branch}


# ---------------------------------------------------------------------------
# Step 2: same-run CCC (upload or reuse) + gate
# ---------------------------------------------------------------------------


def _records_satisfy_contract(records: Any, fresh_secondaries: Sequence[str],
                              run_id: str) -> Optional[str]:
    """Return None if the records satisfy the gate, else a refusal reason."""
    if not isinstance(records, list) or not records:
        return "records file is not a non-empty JSON list"
    want = [str(s).strip().upper() for s in fresh_secondaries]
    seen: list = []
    for rec in records:
        if not isinstance(rec, dict):
            return "a record is not an object"
        missing = [f for f in _CCC_RECORD_FIELDS if f not in rec]
        if missing:
            return f"record missing fields: {missing!r}"
        if rec.get("get_verified") is not True:
            return f"record get_verified is not true for {rec.get('secondary')!r}"
        pn = str(rec.get("pathname") or "")
        if run_id not in pn:
            return f"record pathname does not contain run_id for {rec.get('secondary')!r}"
        sec = str(rec.get("secondary") or "").strip().upper()
        if sec in seen:
            return f"duplicate secondary {sec!r}"
        seen.append(sec)
    if sorted(seen) != sorted(want):
        return (f"record secondaries {sorted(seen)!r} != fresh set "
                f"{sorted(want)!r}")
    return None


def upload_or_reuse_fresh_ccc(inputs: Stage9PublishInputs) -> dict:
    """Validate-only first; reuse an existing satisfying records file; otherwise
    upload (only when operator-approved and not dry-run). Then gate the records
    before combine. The upload seam is injected in tests (no real Blob)."""
    upload = inputs._upload()
    out_path = Path(inputs.fresh_ccc_records_path)
    secs = ",".join(str(s) for s in inputs.fresh_secondaries)

    # Reuse an existing satisfying records file without uploading.
    reused = False
    if out_path.is_file():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if existing is not None and _records_satisfy_contract(
                existing, inputs.fresh_secondaries, inputs.run_id) is None:
            reused = True

    if not reused:
        # Validate-only (no Blob client, no PUT/GET, no file written). The upload
        # seam is token-sensitive: any raised exception is scrubbed to its class
        # name only (its message could echo the Blob token).
        plan = _seam("ccc", upload, token_sensitive=True,
                     k6_ranking_path=str(inputs.k6_ranking_path),
                     secondaries=secs, output_path=str(out_path),
                     confirm_blob_upload=False)
        if not isinstance(plan, dict) or plan.get("status") != "validate_only":
            raise Stage9Error("ccc",
                              "fresh-CCC validate-only did not report "
                              "validate_only")
        if inputs.dry_run or not inputs.operator_approved:
            raise Stage9Error(
                "ccc", "fresh CCC records absent and Blob upload is disabled in "
                "dry-run/unapproved mode; refusing")
        # Real upload (token + Blob boundary -- operator-launched only).
        result = _seam("ccc", upload, token_sensitive=True,
                       k6_ranking_path=str(inputs.k6_ranking_path),
                       secondaries=secs, output_path=str(out_path),
                       confirm_blob_upload=True)
        if not isinstance(result, dict) or result.get("status") != "uploaded":
            raise Stage9Error("ccc", "fresh-CCC upload did not report uploaded")

    if not out_path.is_file():
        raise Stage9Error("ccc", "fresh CCC records file was not written")
    records = _read_json(out_path, "fresh CCC records")
    reason = _records_satisfy_contract(
        records, inputs.fresh_secondaries, inputs.run_id)
    if reason is not None:
        raise Stage9Error("ccc", "fresh CCC records gate failed: " + reason,
                          record_count=(len(records)
                                        if isinstance(records, list) else None))
    return {"records_path": str(out_path), "reused": reused,
            "record_count": len(records)}


# ---------------------------------------------------------------------------
# Step 3: combine / proof assembly
# ---------------------------------------------------------------------------


def assemble_candidate(inputs: Stage9PublishInputs) -> dict:
    """Call combine_and_assemble (run_self_check=True) with the proven publish
    tail inputs. Any combine/proof failure is a board-level STOP."""
    combine = inputs._combine()
    records = _read_json(inputs.fresh_ccc_records_path, "fresh CCC records")
    sidecar = inputs.fresh_validation_sidecar
    assembled_at = inputs.run_id  # deterministic; mirrors the offline probe
    try:
        summary = combine(
            prior_fixture_path=inputs.prior_fixture_path,
            prior_promotion_manifest_path=inputs.prior_promotion_manifest_path,
            prior_ccc_verification_manifest_path=(
                inputs.prior_ccc_verification_manifest_path),
            fresh_rows=list(inputs.fresh_rows),
            fresh_validation_sidecar=sidecar,
            fresh_ccc_records=records,
            assembly_run_id=inputs.run_id,
            assembled_at_utc=assembled_at,
            output_dir=inputs.candidate_dir,
            excluded_tickers=tuple(inputs.excluded_tickers),
            prior_validation_sidecar_path=inputs.prior_validation_sidecar_path,
            project_root=inputs.project_root,
            run_self_check=True,
            reverify_carried_ccc=False,
        )
    except Exception as exc:  # noqa: BLE001 - any combine failure is board-level
        raise Stage9Error("combine",
                          "combine/proof assembly failed: " + str(exc)) from exc
    if not isinstance(summary, dict):
        raise Stage9Error("combine", "combine did not return a summary dict")
    check = summary.get("promote_self_check") or {}
    if check.get("ran") and any(
            v != "pass" for k, v in check.items() if k != "ran"):
        raise Stage9Error("combine", "combine promote self-check did not pass",
                          promote_self_check=check)
    return summary


# ---------------------------------------------------------------------------
# Step 4: copy the Phase-5 report pair into the worktree (NOT a git commit)
# ---------------------------------------------------------------------------


def _report_pair_names(run_id: str, board_count: int) -> tuple:
    date = f"{run_id[0:4]}-{run_id[4:6]}-{run_id[6:8]}"
    base = f"{date}_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_{board_count}"
    return base + ".md", base + ".manifest.json"


def copy_report_pair_to_worktree(inputs: Stage9PublishInputs,
                                 combine_summary: Mapping[str, Any]) -> dict:
    paths = combine_summary.get("paths") or {}
    rel_report = paths.get("composite_report")
    rel_manifest = paths.get("composite_report_manifest")
    if not rel_report or not rel_manifest:
        raise Stage9Error("report_pair",
                          "combine summary missing composite report paths")
    cand_report = Path(inputs.project_root) / rel_report
    cand_manifest = Path(inputs.project_root) / rel_manifest

    board_count = int(combine_summary.get("merged_row_count") or 0)
    report_name, manifest_name = _report_pair_names(inputs.run_id, board_count)
    dst_report = Path(inputs.md_library_shared_dir) / report_name
    dst_manifest = Path(inputs.md_library_shared_dir) / manifest_name

    report_bytes = cand_report.read_bytes()
    cand_sha = _sha256_bytes(report_bytes)
    expected = str(combine_summary.get("report_sha256") or "").lower()
    if expected and cand_sha != expected:
        raise Stage9Error("report_pair",
                          "candidate report sha does not match combine summary",
                          candidate_sha=cand_sha, summary_sha=expected)
    _atomic_write_bytes(dst_report, report_bytes)
    if _sha256_file(dst_report) != cand_sha:
        raise Stage9Error("report_pair", "copied report sha mismatch")

    # Rewrite the manifest report_path to the committed md_library path.
    manifest = _read_json(cand_manifest, "candidate report manifest")
    committed_report_path = _repo_rel(inputs.project_root, dst_report)
    manifest["report_path"] = committed_report_path
    _atomic_write_json(dst_manifest, manifest)

    # The candidate report sha is bound; the committed report must hash-match.
    if _sha256_file(dst_report) != str(manifest.get("report_sha256") or cand_sha):
        # report_sha256 in the manifest binds the report file content.
        if manifest.get("report_sha256") and (
                manifest.get("report_sha256") != cand_sha):
            raise Stage9Error("report_pair",
                              "manifest report_sha256 does not bind the copied "
                              "report")
    return {
        "report_path": str(dst_report),
        "manifest_path": str(dst_manifest),
        "committed_report_rel": committed_report_path,
        "report_sha256": cand_sha,
        "report_name": report_name,
        "manifest_name": manifest_name,
    }


# ---------------------------------------------------------------------------
# Steps 5-6: promote (dry-run gate, then write)
# ---------------------------------------------------------------------------


def _promotion_inputs(inputs: Stage9PublishInputs,
                      combine_summary: Mapping[str, Any],
                      report_pair: Mapping[str, Any], *, write: bool):
    cls = inputs._promote_inputs_cls()
    paths = combine_summary.get("paths") or {}
    source = Path(inputs.project_root) / paths["merged_fixture"]
    sidecar = Path(inputs.project_root) / paths["composite_sidecar"]
    ccc_manifest = Path(inputs.project_root) / paths["combined_ccc_manifest"]
    sidecar_sha = str(combine_summary.get("composite_sidecar_sha256") or "")
    return cls(
        source_path=source,
        destination_path=Path(inputs.public_fixture_dest),
        manifest_destination_path=Path(inputs.public_manifest_dest),
        project_root=Path(inputs.project_root),
        public_mode=True,
        phase5_report_path=Path(report_pair["report_path"]),
        phase5_report_sha256=str(report_pair["report_sha256"]),
        write=bool(write),
        operator_approved=bool(write and inputs.operator_approved),
        phase5_report_manifest_path=Path(report_pair["manifest_path"]),
        validation_sidecar_path=sidecar,
        validation_sidecar_sha256=sidecar_sha,
        ccc_sidecar_verification_manifest_path=ccc_manifest,
    )


def run_promote_dry_run(inputs: Stage9PublishInputs,
                        combine_summary: Mapping[str, Any],
                        report_pair: Mapping[str, Any]) -> dict:
    promote = inputs._promote()
    pin = _promotion_inputs(inputs, combine_summary, report_pair, write=False)
    try:
        summary = promote(pin)
    except Exception as exc:  # noqa: BLE001
        raise Stage9Error("promote_dry_run",
                          "promote dry-run gate failed: " + str(exc)) from exc
    if not isinstance(summary, dict) or summary.get("dry_run") is not True \
            or summary.get("wrote_destination"):
        raise Stage9Error("promote_dry_run",
                          "promote dry-run did not stay closed (no-write)")
    return summary


def run_promote_write(inputs: Stage9PublishInputs,
                      combine_summary: Mapping[str, Any],
                      report_pair: Mapping[str, Any]) -> dict:
    if inputs.dry_run or not inputs.operator_approved:
        raise Stage9Error("promote_write",
                          "promote write requires operator_approved and not "
                          "dry_run")
    promote = inputs._promote()
    pin = _promotion_inputs(inputs, combine_summary, report_pair, write=True)
    try:
        summary = promote(pin)
    except Exception as exc:  # noqa: BLE001
        raise Stage9Error("promote_write",
                          "promote write failed: " + str(exc)) from exc
    if not (isinstance(summary, dict) and summary.get("wrote_destination")
            and summary.get("wrote_manifest")):
        raise Stage9Error("promote_write",
                          "promote write did not write fixture + manifest")
    return summary


def verify_promote_write_outputs(inputs: Stage9PublishInputs,
                                 combine_summary: Mapping[str, Any]) -> dict:
    """Verify the ACTUAL bytes promote wrote before any git add/commit:
      - written public fixture LF-SHA == planned candidate fixture LF-SHA;
      - == source_sha256 inside the WRITTEN promotion manifest;
      - written manifest parses and source_run_id == this run_id;
      - written README exists and is non-empty.
    Any mismatch is a fail-closed STOP before commit. Returns the verified SHAs
    so resume can compare against them."""
    paths = combine_summary.get("paths") or {}
    cand_fixture = Path(inputs.project_root) / paths["merged_fixture"]
    planned_sha = _lf_sha256_file(cand_fixture)

    dest = Path(inputs.public_fixture_dest)
    if not dest.is_file():
        raise Stage9Error("promote_verify", "written public fixture is missing")
    written_sha = _lf_sha256_file(dest)
    if written_sha != planned_sha:
        raise Stage9Error(
            "promote_verify",
            "written public fixture LF-SHA != planned candidate fixture LF-SHA",
            written_fixture_sha256=written_sha, planned_fixture_sha256=planned_sha)

    manifest = _read_json(inputs.public_manifest_dest, "written promotion manifest")
    man_src = str(manifest.get("source_sha256") or "").strip().lower()
    if man_src != written_sha:
        raise Stage9Error(
            "promote_verify",
            "written manifest source_sha256 != written fixture LF-SHA",
            manifest_source_sha256=man_src, written_fixture_sha256=written_sha)
    if manifest.get("source_run_id") != inputs.run_id:
        raise Stage9Error(
            "promote_verify",
            "written manifest source_run_id != run_id",
            manifest_source_run_id=manifest.get("source_run_id"))

    readme = dest.parent / "README.md"
    if not readme.is_file() or not readme.read_bytes().strip():
        raise Stage9Error("promote_verify",
                          "generated README is missing or empty")
    return {"verified_fixture_lf_sha256": written_sha,
            "verified_manifest_source_run_id": inputs.run_id}


# ---------------------------------------------------------------------------
# Step 7: commit (allowlist-enforced)
# ---------------------------------------------------------------------------


def _publication_allowlist(inputs: Stage9PublishInputs,
                           report_pair: Mapping[str, Any]) -> list:
    return [
        _repo_rel(inputs.repo_root, inputs.public_fixture_dest),
        _repo_rel(inputs.repo_root, inputs.public_manifest_dest),
        _repo_rel(inputs.repo_root,
                  Path(inputs.public_fixture_dest).parent / "README.md"),
        _repo_rel(inputs.repo_root, Path(report_pair["report_path"])),
        _repo_rel(inputs.repo_root, Path(report_pair["manifest_path"])),
    ]


def _porcelain_records(inputs: Stage9PublishInputs) -> tuple:
    """Parse `git status --porcelain=v1 -z --untracked-files=all`. The -z output
    is NUL-delimited (no quoting, so paths with spaces are literal). A rename or
    copy record (XY status containing R or C) carries TWO NUL fields: the new
    path then the original path -- BOTH are consumed and BOTH count as changes.
    Returns (changed_paths, rename_or_copy_seen). --untracked-files=all lists each
    new file individually instead of collapsing a fully-untracked directory."""
    status = _run_git(inputs, ["status", "--porcelain=v1", "-z",
                               "--untracked-files=all"])
    if not _git_ok(status):
        raise Stage9Error("commit", "git status failed",
                          git_stderr=_git_err(status))
    raw = getattr(status, "stdout", "") or ""
    fields = raw.split("\0")
    paths: list = []
    rename_seen = False
    i = 0
    n = len(fields)
    while i < n:
        entry = fields[i]
        i += 1
        if not entry:
            continue
        # Each entry: two status chars, a space, then the path.
        status_code = entry[:2]
        path = entry[3:] if len(entry) > 3 else entry[2:].strip()
        is_rename_copy = ("R" in status_code) or ("C" in status_code)
        paths.append(path)
        if is_rename_copy:
            rename_seen = True
            # the original path follows as the next NUL-terminated field
            if i < n:
                paths.append(fields[i])
                i += 1
    return paths, rename_seen


def enforce_publication_allowlist(inputs: Stage9PublishInputs,
                                  report_pair: Mapping[str, Any]) -> list:
    allow = set(_publication_allowlist(inputs, report_pair))
    changed, rename_seen = _porcelain_records(inputs)
    # A rename or copy is never legitimate in a publication commit, regardless of
    # paths -- refuse outright.
    if rename_seen:
        raise Stage9Error(
            "commit", "rename/copy change present; renames are never part of a "
            "publication commit; refusing", changed_paths=sorted(set(changed)))
    stray = sorted(p for p in set(changed) if p not in allow)
    if stray:
        raise Stage9Error(
            "commit", "out-of-allowlist tracked change(s) present; refusing to "
            "commit", stray_paths=stray, allowlist=sorted(allow))
    return changed


def commit_publication(inputs: Stage9PublishInputs,
                       report_pair: Mapping[str, Any]) -> dict:
    enforce_publication_allowlist(inputs, report_pair)
    allow = _publication_allowlist(inputs, report_pair)
    add = _run_git(inputs, ["add", "--", *allow])
    if not _git_ok(add):
        raise Stage9Error("commit", "git add failed", git_stderr=_git_err(add))
    msg = f"Publish K6 MTF board {inputs.run_id}"
    commit = _run_git(inputs, ["commit", "-m", msg])
    if not _git_ok(commit):
        raise Stage9Error("commit", "git commit failed",
                          git_stderr=_git_err(commit))
    head = _git_out_required(inputs, ["rev-parse", "HEAD"], "commit",
                             "git rev-parse HEAD failed after commit")
    file_shas = {}
    for rel in allow:
        fp = Path(inputs.repo_root) / rel
        if fp.is_file():
            file_shas[rel] = _sha256_file(fp)
    return {"commit_sha": head, "committed_files": allow, "file_shas": file_shas,
            "message": msg}


# ---------------------------------------------------------------------------
# Step 8: push
# ---------------------------------------------------------------------------


def push_publication(inputs: Stage9PublishInputs) -> dict:
    push = _run_git(inputs, ["push", "origin", "main"])
    if not _git_ok(push):
        # Diagnostic-only, best-effort: the push already failed; these rev-parses
        # only enrich the error. They MUST NOT mask the original push stderr or
        # affect control flow -- a swallowed failure here simply yields "".
        local = _git_out(_run_git(inputs, ["rev-parse", "HEAD"]))
        origin = _git_out(_run_git(inputs, ["rev-parse", "origin/main"]))
        raise Stage9Error(
            "push", "git push origin main failed", local_head=local,
            origin_main=origin, git_stderr=_git_err(push))
    head = _git_out_required(inputs, ["rev-parse", "HEAD"], "push",
                             "git rev-parse HEAD failed after push")
    return {"pushed_head": head, "git_stdout": _git_out(push)}


# ---------------------------------------------------------------------------
# Step 9: live verification (post-push; deploy_failed_after_push on failure)
# ---------------------------------------------------------------------------


def _default_http_getter(url: str) -> Any:
    import urllib.request  # noqa: PLC0415
    with urllib.request.urlopen(url, timeout=30) as resp:  # nosec - read-only GET
        return getattr(resp, "status", 200), resp.read().decode("utf-8")


def _live_fields_match(live: Mapping[str, Any],
                       committed: Mapping[str, Any]) -> Optional[str]:
    for key in ("source_sha256", "source_run_id", "per_secondary_count"):
        if live.get(key) != committed.get(key):
            return f"{key} mismatch (live {live.get(key)!r} != committed "
    lv = (live.get("validation_results") or {})
    cv = (committed.get("validation_results") or {})
    for key in ("phase_5_validation_report_path",
                "phase_5_validation_report_sha256"):
        if lv.get(key) != cv.get(key):
            return f"validation_results.{key} mismatch"
    ls = (live.get("ccc_series_storage") or {})
    cs = (committed.get("ccc_series_storage") or {})
    for key in ("sidecar_prefix", "sidecar_prefixes", "sidecar_count",
                "total_sidecar_bytes", "total_sidecar_points"):
        if ls.get(key) != cs.get(key):
            return f"ccc_series_storage.{key} mismatch"
    return None


def verify_live_manifest(inputs: Stage9PublishInputs) -> dict:
    """After push: confirm origin/main == local HEAD, then poll the live
    promotion manifest URL until its fields match the committed manifest or the
    timeout elapses. A failure here is deploy_failed_after_push -- NO further git
    actions (no recommit/repush/retry)."""
    # Post-push: any git failure here is deploy_failed_after_push and takes the
    # no-further-git-action path (never a recommit/repush). A swallowed fetch
    # failure must NOT fall through to HTTP polling against a stale ref.
    _git_required(inputs, ["fetch", "origin", "main"], "live_verify",
                  "git fetch origin main failed after push",
                  deploy_failed_after_push=True)
    local = _git_out_required(inputs, ["rev-parse", "HEAD"], "live_verify",
                              "git rev-parse HEAD failed after push",
                              deploy_failed_after_push=True)
    origin = _git_out_required(inputs, ["rev-parse", "origin/main"],
                               "live_verify",
                               "git rev-parse origin/main failed after push",
                               deploy_failed_after_push=True)
    if local and origin and local != origin:
        raise Stage9Error("live_verify",
                          "origin/main != local HEAD after push",
                          local_head=local, origin_main=origin,
                          deploy_failed_after_push=True)

    committed = _read_json(inputs.public_manifest_dest, "committed manifest")
    getter = inputs.http_getter or _default_http_getter
    deadline = inputs._clock() + float(inputs.poll_timeout_seconds)
    last_reason = "no successful manifest fetch"
    attempts = 0
    while True:
        attempts += 1
        try:
            status, text = getter(inputs.live_manifest_url)
            if int(status) == 200:
                live = json.loads(text)
                reason = _live_fields_match(live, committed)
                if reason is None:
                    return {"verified": True, "attempts": attempts,
                            "url": inputs.live_manifest_url}
                last_reason = reason
            else:
                last_reason = f"http status {status}"
        except Exception as exc:  # noqa: BLE001 - transient fetch/parse
            last_reason = f"fetch error: {type(exc).__name__}"
        if inputs._clock() >= deadline:
            raise Stage9Error(
                "live_verify",
                "live manifest did not match within the poll timeout: "
                + last_reason, attempts=attempts,
                deploy_failed_after_push=True)
        inputs._sleep(float(inputs.poll_interval_seconds))


# ---------------------------------------------------------------------------
# Resume detection
# ---------------------------------------------------------------------------


def _resume_commit_not_pushed(inputs: Stage9PublishInputs) -> Optional[dict]:
    """If state says commit_created but not push_ok, validate the worktree is
    exactly the recorded publish commit (clean allowlist, HEAD == recorded SHA,
    committed file SHAs match) so we may resume at push only. Returns a context
    dict to resume, or None if not a resume case. Raises on a dirty/divergent
    state."""
    state = _read_state(inputs.run_dir)
    completed = set(state.get("completed_states") or [])
    if "commit_created" not in completed or "push_ok" in completed:
        return None
    fields = state.get("fields") or {}
    recorded_sha = fields.get("commit_sha")
    head = _git_out_required(inputs, ["rev-parse", "HEAD"], "resume",
                             "git rev-parse HEAD failed")
    if not recorded_sha or head != recorded_sha:
        raise Stage9Error(
            "resume", "recorded publish commit does not match HEAD; refusing "
            "to resume", recorded_commit=recorded_sha, head=head)
    # Allowlist must be clean (no extra changes since the commit). FAIL CLOSED
    # on a git failure: a swallowed status error yields empty stdout that would
    # otherwise read as a CLEAN worktree and let resume proceed toward push.
    status = _git_out_required(inputs, ["status", "--porcelain"], "resume",
                               "git status failed at recorded publish commit")
    if status:
        raise Stage9Error("resume",
                          "worktree not clean at recorded publish commit; "
                          "refusing to resume", porcelain=status)
    # Committed file SHAs match the recorded plan.
    for rel, sha in (fields.get("file_shas") or {}).items():
        fp = Path(inputs.repo_root) / rel
        if not fp.is_file() or _sha256_file(fp) != sha:
            raise Stage9Error(
                "resume", f"committed file {rel} differs from the recorded "
                "plan; refusing to resume", path=rel)
    return {"resume_at": "push", "commit_sha": recorded_sha}


# ---------------------------------------------------------------------------
# Orchestrating entry point
# ---------------------------------------------------------------------------


def run_stage9_publish(inputs: Stage9PublishInputs) -> dict:
    """Run the full fail-closed Stage 9 publish transaction. Returns a summary
    dict. On any gate failure: writes a refusal envelope, performs NO partial
    publish, and stops. The run lock is held from preflight through live verify
    or refusal."""
    summary: dict = {
        "run_id": inputs.run_id,
        "dry_run": bool(inputs.dry_run),
        "operator_approved": bool(inputs.operator_approved),
        "status": "in_progress",
        "states": [],
        "stage": None,
    }

    # `summary["stage"]` tracks the currently-executing step so that the broad
    # backstop below can attribute (and a sanitized refusal envelope can record)
    # ANY escaping exception -- no exception class escapes run_stage9_publish
    # without the envelope on disk.
    def _refuse(err_or_exc) -> dict:
        if isinstance(err_or_exc, Stage9Error):
            err = err_or_exc
        else:
            cls = type(err_or_exc).__name__
            err = Stage9Error(summary.get("stage") or "internal",
                              f"unexpected {cls}", exc_type=cls)
        summary.update(status="refused", stage=err.stage, reason=err.reason)
        extra = {}
        if err.diag.get("deploy_failed_after_push"):
            summary["deploy_failed_after_push"] = True
            extra["deploy_failed_after_push"] = True
        summary["refusal"] = _write_refusal(inputs, err, **extra)
        return summary

    # --- resume: commit created but not pushed -> resume at push only ---
    summary["stage"] = "resume_detect"
    try:
        resume = _resume_commit_not_pushed(inputs)
    except BaseException as err:  # noqa: BLE001 - envelope-always
        return _refuse(err)

    lock_held = False
    try:
        if resume is not None:
            # Lock + push + live verify only; never a second commit.
            summary["stage"] = "lock"
            acquire_stage9_lock(inputs, stage="resume_push")
            lock_held = True
            summary["resumed_from"] = "commit_created"
            summary["stage"] = "push"
            push = push_publication(inputs)
            _record_state(inputs, "push_ok", pushed_head=push["pushed_head"])
            summary["states"].append("push_ok")
            summary["stage"] = "live_verify"
            live = verify_live_manifest(inputs)
            _record_state(inputs, "live_manifest_verified",
                          live_attempts=live.get("attempts"))
            summary["states"].append("live_manifest_verified")
            summary["status"] = "published"
            return summary

        # --- fresh transaction ---
        summary["stage"] = "preflight"
        verify_publish_preflight(inputs)
        lock_held = True  # preflight acquired the lock on success
        _record_state(inputs, "preflight_ok")
        summary["states"].append("preflight_ok")

        summary["stage"] = "ccc"
        ccc = upload_or_reuse_fresh_ccc(inputs)
        _record_state(inputs, "ccc_uploaded", ccc_reused=ccc["reused"],
                      record_count=ccc["record_count"])
        summary["states"].append("ccc_uploaded")

        summary["stage"] = "combine"
        combine_summary = assemble_candidate(inputs)
        _record_state(inputs, "combined_ok",
                      merged_row_count=combine_summary.get("merged_row_count"),
                      composite_sidecar_sha256=combine_summary.get(
                          "composite_sidecar_sha256"))
        summary["states"].append("combined_ok")
        summary["combine_summary"] = {
            k: combine_summary.get(k) for k in (
                "merged_row_count", "carried_count", "fresh_count",
                "board_validated_count", "promote_self_check")}

        summary["stage"] = "report_pair"
        report_pair = copy_report_pair_to_worktree(inputs, combine_summary)
        _record_state(inputs, "report_pair_written_to_worktree",
                      report_name=report_pair["report_name"],
                      manifest_name=report_pair["manifest_name"])
        summary["states"].append("report_pair_written_to_worktree")

        summary["stage"] = "promote_dry_run"
        run_promote_dry_run(inputs, combine_summary, report_pair)
        _record_state(inputs, "promote_dry_run_ok")
        summary["states"].append("promote_dry_run_ok")

        if inputs.dry_run or not inputs.operator_approved:
            summary["status"] = "dry_run_complete"
            summary["stage"] = None
            summary["note"] = ("dry-run / unapproved: stopped after the promote "
                               "dry-run gate; no write/commit/push")
            return summary

        summary["stage"] = "promote_write"
        run_promote_write(inputs, combine_summary, report_pair)
        # Verify the ACTUAL written bytes before any git add/commit.
        summary["stage"] = "promote_verify"
        verified = verify_promote_write_outputs(inputs, combine_summary)
        _record_state(inputs, "promote_write_ok", **verified)
        summary["states"].append("promote_write_ok")

        summary["stage"] = "commit"
        commit = commit_publication(inputs, report_pair)
        _record_state(inputs, "commit_created", commit_sha=commit["commit_sha"],
                      file_shas=commit["file_shas"])
        summary["states"].append("commit_created")
        summary["commit_sha"] = commit["commit_sha"]

        summary["stage"] = "push"
        push = push_publication(inputs)
        _record_state(inputs, "push_ok", pushed_head=push["pushed_head"])
        summary["states"].append("push_ok")

        summary["stage"] = "live_verify"
        live = verify_live_manifest(inputs)
        _record_state(inputs, "live_manifest_verified",
                      live_attempts=live.get("attempts"))
        summary["states"].append("live_manifest_verified")
        summary["status"] = "published"
        summary["stage"] = None
        return summary

    except BaseException as err:  # noqa: BLE001 - envelope-always backstop
        return _refuse(err)
    finally:
        if lock_held:
            release_stage9_lock(inputs.run_dir)
