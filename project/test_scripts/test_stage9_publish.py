"""Hermetic tests for stage9_publish.

No real Blob, no real network, no real promote write to frontend/public, no
engines. Git tests use throwaway tmp repos with a local bare origin. Blob,
combine, promote, HTTP, clock, and sleep are injected seams.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import stage9_publish as s9  # noqa: E402


TOKEN_VALUE = "super-secret-token-value-XYZ"


# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _lf_sha(path: Path) -> str:
    data = Path(path).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def _clean_git_map(head="aaaa", origin="aaaa"):
    """A scripted git runner map representing a clean, on-main, in-sync repo."""
    return {
        ("ls-remote",): _Result(0, "ref\n"),
        ("rev-parse", "--abbrev-ref", "HEAD"): _Result(0, "main\n"),
        ("status", "--porcelain"): _Result(0, ""),
        ("rev-parse", "HEAD"): _Result(0, head + "\n"),
        ("rev-parse", "origin/main"): _Result(0, origin + "\n"),
        ("merge-base", "--is-ancestor"): _Result(0, ""),
    }


def _scripted_git(mapping):
    def runner(argv, *, cwd, env):
        assert argv[0] == "git"
        # token must never leak into git env in a way tests can observe as value
        args = tuple(argv[1:])
        best = None
        for key, val in mapping.items():
            if args[: len(key)] == key and (best is None or len(key) > len(best[0])):
                best = (key, val)
        if best is None:
            return _Result(0, "", "")
        _, val = best
        return val(args) if callable(val) else val
    return runner


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _prior_pair(tmp_path: Path):
    """A prior fixture + promotion manifest whose source_sha256 binds (LF)."""
    fx = tmp_path / "prior" / "k6_mtf_ranking.json"
    fixture = {"schema_version": "k6_mtf_ranking_v2", "run_id": "PRIORRUN",
               "per_secondary": [{"secondary": "AAA"}]}
    _write_json(fx, fixture)
    promo = tmp_path / "prior" / "k6_mtf_ranking.promotion_manifest.json"
    _write_json(promo, {"source_sha256": _lf_sha(fx)})
    sidecar = tmp_path / "prior" / "validation.json"
    _write_json(sidecar, {"run_id": "PRIORVAL"})
    ccc = tmp_path / "prior" / "ccc_verification.json"
    _write_json(ccc, {"schema_version": "k6_mtf_ccc_sidecar_verification_v1"})
    return fx, promo, sidecar, ccc


def _records(run_id, secs):
    out = []
    for s in secs:
        sha = hashlib.sha256(f"{s}-ccc".encode()).hexdigest()
        out.append({
            "secondary": s,
            "pathname": f"k6-mtf/{run_id}/ccc-series/{s}.{sha}.json",
            "url": f"https://h.public.blob.vercel-storage.com/k6-mtf/{run_id}/"
                   f"ccc-series/{s}.{sha}.json",
            "sha256": sha, "byte_size": 10, "points": 5,
            "first_date": "2010-01-04", "last_date": "2026-06-03",
            "reused": False, "get_verified": True,
        })
    return out


def _inputs(tmp_path, **over):
    fx, promo, sidecar, ccc = _prior_pair(tmp_path)
    run_id = over.pop("run_id", "20260610T221108Z")
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    fixtures = tmp_path / "fe" / "frontend" / "public" / "fixtures"
    kwargs = dict(
        repo_root=tmp_path / "fe",
        run_dir=run_dir,
        run_id=run_id,
        fresh_secondaries=("IHI", "SCHG"),
        fresh_rows=[{"secondary": "IHI"}, {"secondary": "SCHG"}],
        fresh_validation_sidecar={"run_id": run_id},
        k6_ranking_path=tmp_path / "k6_ranking.json",
        prior_fixture_path=fx,
        prior_promotion_manifest_path=promo,
        prior_validation_sidecar_path=sidecar,
        prior_ccc_verification_manifest_path=ccc,
        candidate_dir=run_dir / "publish_candidate",
        fresh_ccc_records_path=run_dir / "fresh_ccc_records.json",
        public_fixture_dest=fixtures / "k6_mtf_ranking.json",
        public_manifest_dest=fixtures / "k6_mtf_ranking.promotion_manifest.json",
        md_library_shared_dir=tmp_path / "fe" / "md_library" / "shared",
        project_root=tmp_path / "fe",
        excluded_tickers=("CDTX",),
        operator_approved=True,
        dry_run=False,
        env={"BLOB_READ_WRITE_TOKEN": TOKEN_VALUE},
        subprocess_runner=_scripted_git(_clean_git_map()),
        http_getter=lambda url: (200, "{}"),
        sleeper=lambda s: None,
        clock=lambda: 0.0,
    )
    kwargs.update(over)
    return s9.Stage9PublishInputs(**kwargs)


# ---------------------------------------------------------------------------
# Preflight / refusal
# ---------------------------------------------------------------------------


def test_preflight_token_absent_halts_records_presence_false(tmp_path):
    inp = _inputs(tmp_path, env={})  # no token
    with pytest.raises(s9.Stage9Error) as ei:
        s9.verify_publish_preflight(inp, acquire_lock=False)
    assert ei.value.stage == "preflight"
    assert ei.value.diag.get("token_present") is False


def test_run_token_absent_writes_refusal_no_side_effects(tmp_path):
    called = {"upload": 0}
    inp = _inputs(tmp_path, env={},
                  upload_func=lambda **k: called.__setitem__(
                      "upload", called["upload"] + 1))
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "preflight"
    refusal = json.loads((inp.run_dir / "publish_refusal.json").read_text())
    assert refusal["no_partial_publish"] is True
    assert refusal.get("token_present") is False
    assert called["upload"] == 0
    assert not (inp.run_dir / "fresh_ccc_records.json").exists()
    assert not (inp.run_dir / "publish_state.json").exists()


def test_preflight_git_probe_nonzero_halts(tmp_path):
    m = _clean_git_map()
    m[("ls-remote",)] = _Result(1, "", "could not read")
    inp = _inputs(tmp_path, subprocess_runner=_scripted_git(m))
    with pytest.raises(s9.Stage9Error):
        s9.verify_publish_preflight(inp, acquire_lock=False)


def test_preflight_dirty_worktree_halts(tmp_path):
    m = _clean_git_map()
    m[("status", "--porcelain")] = _Result(0, " M something\n")
    inp = _inputs(tmp_path, subprocess_runner=_scripted_git(m))
    with pytest.raises(s9.Stage9Error):
        s9.verify_publish_preflight(inp, acquire_lock=False)


def test_preflight_not_on_main_halts(tmp_path):
    m = _clean_git_map()
    m[("rev-parse", "--abbrev-ref", "HEAD")] = _Result(0, "feature\n")
    inp = _inputs(tmp_path, subprocess_runner=_scripted_git(m))
    with pytest.raises(s9.Stage9Error):
        s9.verify_publish_preflight(inp, acquire_lock=False)


def test_preflight_origin_not_ancestor_halts(tmp_path):
    m = _clean_git_map(head="aaaa", origin="bbbb")
    m[("merge-base", "--is-ancestor")] = _Result(1, "")
    inp = _inputs(tmp_path, subprocess_runner=_scripted_git(m))
    with pytest.raises(s9.Stage9Error):
        s9.verify_publish_preflight(inp, acquire_lock=False)


def test_preflight_prior_sha_mismatch_halts(tmp_path):
    inp = _inputs(tmp_path)
    bad = json.loads(inp.prior_promotion_manifest_path.read_text())
    bad["source_sha256"] = "0" * 64
    _write_json(inp.prior_promotion_manifest_path, bad)
    with pytest.raises(s9.Stage9Error) as ei:
        s9.verify_publish_preflight(inp, acquire_lock=False)
    assert "source_sha256" in ei.value.reason


def test_preflight_not_operator_approved_halts(tmp_path):
    inp = _inputs(tmp_path, operator_approved=False)
    with pytest.raises(s9.Stage9Error):
        s9.verify_publish_preflight(inp, acquire_lock=False)


def test_preflight_lock_held_halts(tmp_path):
    inp = _inputs(tmp_path)
    # pre-create a lock with a live pid (this process) -> held -> refuse
    (inp.run_dir / s9.STAGE9_LOCK_NAME).write_text(
        json.dumps({"run_id": "x", "pid": os.getpid(), "stage": "preflight"}))
    with pytest.raises(s9.Stage9Error) as ei:
        s9.verify_publish_preflight(inp, acquire_lock=True)
    assert ei.value.stage == "lock"


# ---------------------------------------------------------------------------
# CCC gate
# ---------------------------------------------------------------------------


def _upload_writing(records):
    def up(*, k6_ranking_path, secondaries, output_path, confirm_blob_upload,
           **kw):
        if not confirm_blob_upload:
            return {"status": "validate_only", "would_upload_count": 2}
        _write_json(Path(output_path), records)
        return {"status": "uploaded", "record_count": len(records)}
    return up


def test_ccc_missing_secondary_halts(tmp_path):
    inp = _inputs(tmp_path,
                  upload_func=_upload_writing(_records("20260610T221108Z", ["IHI"])))
    with pytest.raises(s9.Stage9Error) as ei:
        s9.upload_or_reuse_fresh_ccc(inp)
    assert ei.value.stage == "ccc"


def test_ccc_extra_secondary_halts(tmp_path):
    recs = _records("20260610T221108Z", ["IHI", "SCHG", "EXTRA"])
    inp = _inputs(tmp_path, upload_func=_upload_writing(recs))
    with pytest.raises(s9.Stage9Error):
        s9.upload_or_reuse_fresh_ccc(inp)


def test_ccc_duplicate_secondary_halts(tmp_path):
    recs = _records("20260610T221108Z", ["IHI", "IHI"])
    inp = _inputs(tmp_path, upload_func=_upload_writing(recs))
    with pytest.raises(s9.Stage9Error):
        s9.upload_or_reuse_fresh_ccc(inp)


def test_ccc_get_verified_false_halts(tmp_path):
    recs = _records("20260610T221108Z", ["IHI", "SCHG"])
    recs[0]["get_verified"] = False
    inp = _inputs(tmp_path, upload_func=_upload_writing(recs))
    with pytest.raises(s9.Stage9Error):
        s9.upload_or_reuse_fresh_ccc(inp)


def test_ccc_pathname_missing_run_id_halts(tmp_path):
    recs = _records("OTHERRUN", ["IHI", "SCHG"])  # run_id not the current one
    inp = _inputs(tmp_path, upload_func=_upload_writing(recs))
    with pytest.raises(s9.Stage9Error):
        s9.upload_or_reuse_fresh_ccc(inp)


def test_ccc_valid_existing_reused_without_upload(tmp_path):
    inp = _inputs(tmp_path)
    _write_json(inp.fresh_ccc_records_path,
                _records(inp.run_id, ["IHI", "SCHG"]))
    called = {"n": 0}

    def up(**kw):
        called["n"] += 1
        return {"status": "validate_only"}

    inp.upload_func = up
    res = s9.upload_or_reuse_fresh_ccc(inp)
    assert res["reused"] is True and called["n"] == 0


# ---------------------------------------------------------------------------
# Real-git world (commit / push / allowlist / resume)
# ---------------------------------------------------------------------------


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, check=check)


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "tester")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / ".gitignore").write_text("output/\n", encoding="utf-8")
    # placeholder public fixtures (tracked; the fake promote overwrites them)
    fx = repo / "frontend" / "public" / "fixtures"
    fx.mkdir(parents=True)
    (fx / "k6_mtf_ranking.json").write_text("{}\n", encoding="utf-8")
    (fx / "k6_mtf_ranking.promotion_manifest.json").write_text(
        "{}\n", encoding="utf-8")
    (fx / "README.md").write_text("old readme\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    _git(repo, "branch", "-M", "main")
    origin = tmp_path / "origin.git"
    _git(repo, "init", "--bare", str(origin))  # cwd ignored for bare init target
    subprocess.run(["git", "init", "--bare", str(origin)],
                   capture_output=True, text=True)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return repo, origin


def _committed_manifest(run_id, fixture_sha):
    return {
        "source_sha256": fixture_sha, "source_run_id": run_id,
        "per_secondary_count": 207,
        "validation_results": {
            "phase_5_validation_report_path":
                "md_library/shared/2026-06-10_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_207.md",
            "phase_5_validation_report_sha256": "a" * 64},
        "ccc_series_storage": {
            "sidecar_prefix": None,
            "sidecar_prefixes": [{"prefix": "k6-mtf/x/ccc-series/",
                                  "sidecar_count": 207}],
            "sidecar_count": 207, "total_sidecar_bytes": 1, "total_sidecar_points": 1},
    }


def _real_world(tmp_path, run_id="20260610T221108Z", **over):
    repo, origin = _init_repo(tmp_path)
    run_dir = repo / "output" / "crunch_runs" / run_id  # gitignored
    cand = run_dir / "publish_candidate"
    cand.mkdir(parents=True)
    # candidate report + manifest the report-pair step copies into md_library
    report = cand / "composite_phase5_report.md"
    report.write_text("# report\n", encoding="utf-8")
    report_sha = hashlib.sha256(report.read_bytes()).hexdigest()
    cand_manifest = cand / "composite_phase5_report.manifest.json"
    _write_json(cand_manifest, {"report_manifest_schema": "x",
                                "report_sha256": report_sha,
                                "report_path": "output/old.md"})
    # prior pair (gitignored under output/)
    fx = run_dir / "prior_fixture.json"
    _write_json(fx, {"schema_version": "k6_mtf_ranking_v2"})
    promo = run_dir / "prior_promo.json"
    _write_json(promo, {"source_sha256": _lf_sha(fx)})

    # The merged fixture bytes the combine writes; promote copies them verbatim
    # (LF) to the public dest, and the promotion manifest records their LF SHA --
    # so the content-verification step (fixture LF-SHA == manifest source_sha256
    # == planned candidate SHA) is satisfied by self-consistent fakes.
    merged_text = '{"schema_version": "k6_mtf_ranking_v2", "run_id": "%s"}\n' % run_id
    merged_lf = hashlib.sha256(merged_text.encode("utf-8")).hexdigest()
    committed_manifest = _committed_manifest(run_id, merged_lf)

    def fake_combine(**kw):
        out = Path(kw["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        # merged fixture + sidecar + ccc manifest live under the candidate
        (out / "merged_k6_mtf_ranking_v2.json").write_text(merged_text)
        (out / "composite_validation_sidecar.json").write_text("{}\n")
        (out / "combined_ccc_sidecar_verification.json").write_text("{}\n")
        rel = lambda p: Path(p).resolve().relative_to(
            Path(kw["project_root"]).resolve()).as_posix()
        return {
            "merged_row_count": 207, "carried_count": 205, "fresh_count": 2,
            "board_validated_count": 90,
            "composite_sidecar_sha256": "s" * 64, "report_sha256": report_sha,
            "promote_self_check": {"ran": True,
                                   "validate_k6_mtf_ranking_v2_payload": "pass",
                                   "verify_v2_promotion_binding": "pass",
                                   "validate_ccc_verification_against_fixture": "pass"},
            "paths": {
                "merged_fixture": rel(out / "merged_k6_mtf_ranking_v2.json"),
                "composite_sidecar": rel(out / "composite_validation_sidecar.json"),
                "composite_report": rel(report),
                "composite_report_manifest": rel(cand_manifest),
                "combined_ccc_manifest": rel(out / "combined_ccc_sidecar_verification.json"),
            },
        }

    fixtures = repo / "frontend" / "public" / "fixtures"

    def fake_promote(pin):
        if not pin.write:
            return {"dry_run": True, "wrote_destination": False,
                    "wrote_manifest": False}
        # write the SAME merged bytes promote's _safe_copy would (LF), and a
        # manifest whose source_sha256 binds them.
        Path(pin.destination_path).parent.mkdir(parents=True, exist_ok=True)
        Path(pin.destination_path).write_text(merged_text, encoding="utf-8")
        _write_json(Path(pin.manifest_destination_path), committed_manifest)
        (Path(pin.destination_path).parent / "README.md").write_text(
            "generated readme\n", encoding="utf-8")
        return {"dry_run": False, "wrote_destination": True,
                "wrote_manifest": True, "wrote_readme": True}

    kwargs = dict(
        repo_root=repo, run_dir=run_dir, run_id=run_id,
        fresh_secondaries=("IHI", "SCHG"),
        fresh_rows=[{"secondary": "IHI"}, {"secondary": "SCHG"}],
        fresh_validation_sidecar={"run_id": run_id},
        k6_ranking_path=run_dir / "k6_ranking.json",
        prior_fixture_path=fx, prior_promotion_manifest_path=promo,
        prior_validation_sidecar_path=run_dir / "vs.json",
        prior_ccc_verification_manifest_path=run_dir / "cv.json",
        candidate_dir=cand,
        fresh_ccc_records_path=run_dir / "fresh_ccc_records.json",
        public_fixture_dest=fixtures / "k6_mtf_ranking.json",
        public_manifest_dest=fixtures / "k6_mtf_ranking.promotion_manifest.json",
        md_library_shared_dir=repo / "md_library" / "shared",
        project_root=repo, excluded_tickers=(),
        operator_approved=True, dry_run=False,
        env={"BLOB_READ_WRITE_TOKEN": TOKEN_VALUE},
        upload_func=_upload_writing(_records(run_id, ["IHI", "SCHG"])),
        combine_func=fake_combine, promote_func=fake_promote,
        promote_inputs_cls=lambda **kw: types.SimpleNamespace(**kw),
        http_getter=lambda url: (200, json.dumps(committed_manifest)),
        sleeper=lambda s: None, clock=lambda: 0.0,
    )
    kwargs.update(over)
    inp = s9.Stage9PublishInputs(**kwargs)
    return inp, repo, origin, committed_manifest


def test_happy_path_publishes_and_records_states_in_order(tmp_path):
    inp, repo, origin, _ = _real_world(tmp_path)
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "published", summary
    assert summary["states"] == list(s9.PUBLISH_STATES)
    # state file recorded in order
    state = json.loads((inp.run_dir / "publish_state.json").read_text())
    assert state["completed_states"] == list(s9.PUBLISH_STATES)
    # commit exists on main + pushed to origin
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    origin_head = _git(repo, "rev-parse", "origin/main").stdout.strip()
    assert head == origin_head
    msg = _git(repo, "log", "-1", "--pretty=%s").stdout.strip()
    assert msg == f"Publish K6 MTF board {inp.run_id}"
    # report pair committed under md_library/shared
    assert (repo / "md_library" / "shared" /
            "2026-06-10_K6_MTF_PHASE_5_HONEST_VALIDATION_REPORT_207.md").is_file()


def test_dry_run_stops_after_promote_dry_run_gate(tmp_path):
    inp, repo, origin, _ = _real_world(tmp_path, dry_run=True)
    # This variant prewrites a valid same-run records file to exercise the REUSE
    # path (the approved upload path is covered separately below).
    _write_json(inp.fresh_ccc_records_path, _records(inp.run_id, ["IHI", "SCHG"]))
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "dry_run_complete"
    assert "promote_dry_run_ok" in summary["states"]
    assert "promote_write_ok" not in summary["states"]
    assert summary["ccc_fresh_upload"] is False  # reused, no fresh upload
    # no commit beyond init
    n = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert n == "1"


# ---------------------------------------------------------------------------
# F1: approved dry-run performs the real CCC round-trip (no records prewrite)
# ---------------------------------------------------------------------------


def test_approved_dry_run_uploads_fresh_ccc_then_completes(tmp_path):
    # Approved dry-run with NO records file: the CCC step performs the real
    # (injected) Blob upload + GET, proceeds through combine/proof, and stops at
    # the promote dry-run gate -- publication stays CLOSED (no fixture/promote/
    # commit/push).
    inp, repo, origin, _ = _real_world(tmp_path, dry_run=True)
    assert not Path(inp.fresh_ccc_records_path).is_file()  # nothing prewritten
    # Capture the live fixture/manifest bytes (if present) to prove they are not
    # modified by the dry-run.
    fx_dest, mf_dest = Path(inp.public_fixture_dest), Path(inp.public_manifest_dest)
    fx_before = fx_dest.read_bytes() if fx_dest.is_file() else None
    mf_before = mf_dest.read_bytes() if mf_dest.is_file() else None
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "dry_run_complete"
    assert summary["dry_run"] is True and summary["operator_approved"] is True
    # the fresh CCC upload happened (disclosure) and records were written
    assert summary["ccc_fresh_upload"] is True
    assert "ccc_uploaded" in summary["states"]
    assert "promote_dry_run_ok" in summary["states"]
    assert Path(inp.fresh_ccc_records_path).is_file()
    # PUBLICATION CLOSED: no promote write/push state, fixture/manifest bytes
    # unchanged, no new commit.
    assert "promote_write_ok" not in summary["states"]
    assert "push_ok" not in summary["states"]
    assert (fx_dest.read_bytes() if fx_dest.is_file() else None) == fx_before
    assert (mf_dest.read_bytes() if mf_dest.is_file() else None) == mf_before
    # no commit beyond the init commit
    n = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert n == "1"


def test_upload_or_reuse_approved_dry_run_uploads(tmp_path):
    # Unit-level: approved dry-run with no records -> fresh upload (confirm=True).
    inp = _inputs(tmp_path, dry_run=True, operator_approved=True,
                  upload_func=_upload_writing(
                      _records("20260610T221108Z", ["IHI", "SCHG"])))
    assert not Path(inp.fresh_ccc_records_path).is_file()
    out = s9.upload_or_reuse_fresh_ccc(inp)
    assert out["reused"] is False and out["uploaded"] is True
    assert Path(inp.fresh_ccc_records_path).is_file()


def test_upload_or_reuse_unapproved_dry_run_refuses_verbatim(tmp_path):
    # Unapproved dry-run with no records -> today's validate-only-then-refuse,
    # byte-for-byte (no upload, no records file written).
    inp = _inputs(tmp_path, dry_run=True, operator_approved=False,
                  upload_func=_upload_writing(
                      _records("20260610T221108Z", ["IHI", "SCHG"])))
    with pytest.raises(s9.Stage9Error) as ei:
        s9.upload_or_reuse_fresh_ccc(inp)
    assert ei.value.stage == "ccc"
    assert ei.value.reason == (
        "fresh CCC records absent and Blob upload is disabled in "
        "dry-run/unapproved mode; refusing")
    assert not Path(inp.fresh_ccc_records_path).is_file()  # validate-only, no write


def test_upload_or_reuse_approved_dry_run_reuse_no_double_upload(tmp_path):
    # Approved dry-run WITH a valid records file -> reuse; the upload seam must
    # never be invoked (no double upload).
    def _no_upload(*, confirm_blob_upload, **kw):
        raise AssertionError("upload seam must not run when records are reusable")
    inp = _inputs(tmp_path, dry_run=True, operator_approved=True,
                  upload_func=_no_upload)
    _write_json(Path(inp.fresh_ccc_records_path),
                _records("20260610T221108Z", ["IHI", "SCHG"]))
    out = s9.upload_or_reuse_fresh_ccc(inp)
    assert out["reused"] is True and out["uploaded"] is False


# ---------------------------------------------------------------------------
# F2: verify_publish_preflight_fast (cheap subset: approval + token presence)
# ---------------------------------------------------------------------------


def test_preflight_fast_missing_approval_raises():
    with pytest.raises(s9.Stage9Error) as ei:
        s9.verify_publish_preflight_fast(
            operator_approved=False, env={"BLOB_READ_WRITE_TOKEN": TOKEN_VALUE})
    assert ei.value.stage == "preflight"
    assert ei.value.reason == "publish mode requires operator_approved=true"


def test_preflight_fast_missing_token_raises():
    with pytest.raises(s9.Stage9Error) as ei:
        s9.verify_publish_preflight_fast(operator_approved=True, env={})
    assert ei.value.stage == "preflight"
    assert ei.value.diag.get("token_present") is False


def test_preflight_fast_happy_passes():
    # Approval + token present -> returns None, no raise, no side effects.
    assert s9.verify_publish_preflight_fast(
        operator_approved=True,
        env={"BLOB_READ_WRITE_TOKEN": TOKEN_VALUE}) is None


def test_allowlist_blocks_out_of_allowlist_change(tmp_path):
    inp, repo, origin, _ = _real_world(tmp_path)
    # an unrelated tracked change present at commit time
    (repo / "frontend" / "public" / "fixtures" / "README.md").write_text(
        "x\n", encoding="utf-8")  # allowed (README)
    (repo / "stray.txt").write_text("stray\n", encoding="utf-8")
    _git(repo, "add", "stray.txt")
    report_pair = {"report_path": str(
        repo / "md_library" / "shared" / "r.md"),
        "manifest_path": str(repo / "md_library" / "shared" / "r.manifest.json")}
    with pytest.raises(s9.Stage9Error) as ei:
        s9.enforce_publication_allowlist(inp, report_pair)
    assert ei.value.stage == "commit"
    assert "stray.txt" in ei.value.diag.get("stray_paths", [])


def _break_origin_push(origin):
    """Install a pre-receive hook that rejects every push. ls-remote/fetch are
    unaffected (they never run pre-receive), so preflight passes but push fails
    deterministically."""
    hooks = origin / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    try:
        os.chmod(hook, 0o755)
    except OSError:
        pass


def _fix_origin_push(origin):
    hook = origin / "hooks" / "pre-receive"
    if hook.exists():
        hook.unlink()


def test_push_failure_writes_refusal_with_heads_no_retry(tmp_path):
    inp, repo, origin, _ = _real_world(tmp_path)
    _break_origin_push(origin)  # accepts preflight probe, rejects the push
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "push"
    refusal = json.loads((inp.run_dir / "publish_refusal.json").read_text())
    assert "local_head" in refusal and "git_stderr" in refusal
    # commit happened but no second commit / no push success state
    state = json.loads((inp.run_dir / "publish_state.json").read_text())
    assert "commit_created" in state["completed_states"]
    assert "push_ok" not in state["completed_states"]


def test_resume_commit_not_pushed_pushes_same_commit(tmp_path):
    inp, repo, origin, _ = _real_world(tmp_path)
    _break_origin_push(origin)  # first run fails at push (commit created)
    first = s9.run_stage9_publish(inp)
    assert first["stage"] == "push"
    commit_after_first = _git(repo, "rev-parse", "HEAD").stdout.strip()
    # un-break origin and relaunch -> resume at push, no second commit
    _fix_origin_push(origin)
    second = s9.run_stage9_publish(inp)
    assert second.get("resumed_from") == "commit_created"
    assert second["status"] == "published"
    commit_after_second = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert commit_after_first == commit_after_second  # no new commit
    assert _git(repo, "rev-parse", "origin/main").stdout.strip() == commit_after_second
    n = _git(repo, "rev-list", "--count", "HEAD").stdout.strip()
    assert n == "2"  # init + the single publish commit


def test_resume_head_mismatch_stops(tmp_path):
    inp, repo, origin, _ = _real_world(tmp_path)
    # forge a state file claiming a commit_created at a bogus SHA
    s9._record_state(inp, "commit_created", commit_sha="deadbeef" * 5,
                     file_shas={})
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "resume"


# ---------------------------------------------------------------------------
# Live verify
# ---------------------------------------------------------------------------


def test_live_verify_match(tmp_path):
    inp, repo, origin, manifest = _real_world(tmp_path)
    s9.run_stage9_publish(inp)  # full happy path already verified above
    # call verify directly with a matching manifest after writing committed dest
    _write_json(inp.public_manifest_dest, manifest)
    res = s9.verify_live_manifest(inp)
    assert res["verified"] is True


def test_live_verify_mismatch_deploy_failed(tmp_path):
    inp, repo, origin, manifest = _real_world(
        tmp_path, poll_timeout_seconds=0,
        http_getter=lambda url: (200, json.dumps({"source_sha256": "z"})))
    _write_json(inp.public_manifest_dest, manifest)
    with pytest.raises(s9.Stage9Error) as ei:
        s9.verify_live_manifest(inp)
    assert ei.value.diag.get("deploy_failed_after_push") is True


def test_live_verify_timeout_deploy_failed(tmp_path):
    ticks = {"t": 0.0}

    def clock():
        return ticks["t"]

    def sleeper(s):
        ticks["t"] += 1000.0  # jump past the timeout immediately

    inp, repo, origin, manifest = _real_world(
        tmp_path, http_getter=lambda url: (503, "unavailable"),
        clock=clock, sleeper=sleeper, poll_timeout_seconds=10,
        poll_interval_seconds=1)
    _write_json(inp.public_manifest_dest, manifest)
    with pytest.raises(s9.Stage9Error) as ei:
        s9.verify_live_manifest(inp)
    assert ei.value.diag.get("deploy_failed_after_push") is True


def test_run_summary_live_mismatch_marks_deploy_failed_after_push(tmp_path):
    inp, repo, origin, manifest = _real_world(
        tmp_path, poll_timeout_seconds=0,
        http_getter=lambda url: (200, json.dumps({"source_sha256": "z"})))
    summary = s9.run_stage9_publish(inp)
    assert summary.get("deploy_failed_after_push") is True
    assert summary["stage"] == "live_verify"
    # pushed, but no further git mutation after the failed verify
    assert _git(repo, "rev-parse", "origin/main").stdout.strip() == \
        _git(repo, "rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------------------
# Token safety
# ---------------------------------------------------------------------------


def test_token_value_never_appears_in_artifacts(tmp_path):
    inp, repo, origin, _ = _real_world(tmp_path)
    summary = s9.run_stage9_publish(inp)
    blob = json.dumps(summary)
    for name in ("publish_state.json", "publish_refusal.json"):
        p = inp.run_dir / name
        if p.is_file():
            blob += p.read_text()
    assert TOKEN_VALUE not in blob


# ---------------------------------------------------------------------------
# Fix 1/2: seam-failure envelope + token-safe sanitization
# ---------------------------------------------------------------------------


def _raiser(msg):
    def f(*a, **k):
        raise RuntimeError(msg)
    return f


def _all_run_dir_text(run_dir):
    blob = ""
    for p in Path(run_dir).rglob("*"):
        if p.is_file():
            try:
                blob += p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return blob


@pytest.mark.parametrize("seam,exp_stage", [
    ("ccc", "ccc"), ("combine", "combine"),
    ("promote_dry", "promote_dry_run"), ("promote_write", "promote_write"),
])
def test_generic_seam_exception_refuses_with_envelope_no_side_effect(
        tmp_path, seam, exp_stage):
    over = {}
    if seam == "ccc":
        over["upload_func"] = _raiser("boom-ccc")
    elif seam == "combine":
        over["combine_func"] = _raiser("boom-combine")
    else:
        def prom(pin):
            if seam == "promote_dry" and not pin.write:
                raise RuntimeError("boom-dry")
            if seam == "promote_write" and pin.write:
                raise RuntimeError("boom-write")
            return {"dry_run": True, "wrote_destination": False,
                    "wrote_manifest": False}
        over["promote_func"] = prom
    inp, repo, origin, _ = _real_world(tmp_path, **over)
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused"
    assert summary["stage"] == exp_stage
    refusal = json.loads((inp.run_dir / "publish_refusal.json").read_text())
    assert refusal["no_partial_publish"] is True
    # no subsequent side effect: no publish commit, origin unchanged
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == \
        _git(repo, "rev-parse", "origin/main").stdout.strip()
    if seam in ("ccc", "combine"):  # promote never ran -> dest untouched
        assert (repo / "frontend" / "public" / "fixtures" /
                "k6_mtf_ranking.json").read_text() == "{}\n"


def test_report_pair_io_exception_hits_backstop(tmp_path):
    # A non-Stage9Error (missing candidate report -> FileNotFoundError) escaping a
    # step must still be caught by the broad backstop and produce the envelope.
    inp, repo, origin, _ = _real_world(tmp_path)
    (inp.candidate_dir / "composite_phase5_report.md").unlink()
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "report_pair"
    assert (inp.run_dir / "publish_refusal.json").is_file()
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_git_subprocess_exception_converted_to_refusal(tmp_path):
    # If the subprocess runner itself raises on the commit, _run_git returns a
    # synthetic failed result and the commit step refuses (no exception escapes).
    real = s9._default_subprocess_runner

    def runner(argv, *, cwd, env):
        if len(argv) > 1 and argv[1] == "commit":
            raise OSError("git binary exploded")
        return real(argv, cwd=cwd, env=env)

    inp, repo, origin, _ = _real_world(tmp_path, subprocess_runner=runner)
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "commit"
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_upload_exception_sentinel_never_in_any_artifact(tmp_path):
    sentinel = "ghp_FAKE_SENTINEL_TOKEN_zzz999"
    inp, repo, origin, _ = _real_world(
        tmp_path,
        upload_func=_raiser(f"blob PUT failed: auth header token={sentinel}"))
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "ccc"
    assert (inp.run_dir / "publish_refusal.json").is_file()
    haystack = json.dumps(summary) + _all_run_dir_text(inp.run_dir)
    assert sentinel not in haystack
    # the sanitized reason records only the class name + a fixed safe summary
    assert "RuntimeError" in summary["reason"]
    assert "details suppressed" in summary["reason"]


# ---------------------------------------------------------------------------
# Fix 3: promote output content verification before commit
# ---------------------------------------------------------------------------


def test_promote_writes_wrong_content_halts_before_commit(tmp_path):
    def wrong_promote(pin):
        if not pin.write:
            return {"dry_run": True, "wrote_destination": False,
                    "wrote_manifest": False}
        Path(pin.destination_path).parent.mkdir(parents=True, exist_ok=True)
        Path(pin.destination_path).write_text('{"tampered": true}\n')
        _write_json(Path(pin.manifest_destination_path),
                    {"source_sha256": "0" * 64, "source_run_id": "WRONG"})
        (Path(pin.destination_path).parent / "README.md").write_text("x\n")
        return {"dry_run": False, "wrote_destination": True,
                "wrote_manifest": True, "wrote_readme": True}

    inp, repo, origin, _ = _real_world(tmp_path, promote_func=wrong_promote)
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "promote_verify"
    assert "promote_write_ok" not in summary["states"]  # never recorded
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"  # no commit


def test_promote_empty_readme_halts_before_commit(tmp_path):
    inp, repo, origin, _ = _real_world(tmp_path)
    # wrap the world's promote to blank the README after it writes correctly
    base = inp.promote_func

    def wrap(pin):
        out = base(pin)
        if getattr(pin, "write", False):
            (Path(pin.destination_path).parent / "README.md").write_text("")
        return out

    inp.promote_func = wrap
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "promote_verify"
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"


# ---------------------------------------------------------------------------
# Fix 4: allowlist parsing hardening (-z, renames/copies, spaces)
# ---------------------------------------------------------------------------


def test_allowlist_rename_onto_allowlisted_name_stops(tmp_path):
    inp, repo, origin, _ = _real_world(tmp_path)
    # a tracked out-of-allowlist file, renamed onto a (new) ALLOWLISTED report
    # path -> a real R status -> STOP by the rename policy regardless of dest.
    (repo / "evil.txt").write_text("evil content\n")
    _git(repo, "add", "evil.txt")
    _git(repo, "commit", "-m", "add evil")
    (repo / "md_library" / "shared").mkdir(parents=True, exist_ok=True)
    _git(repo, "mv", "evil.txt", "md_library/shared/REPORT.md")  # new dest -> rename
    report_pair = {
        "report_path": str(repo / "md_library" / "shared" / "REPORT.md"),
        "manifest_path": str(repo / "md_library" / "shared" / "REPORT.manifest.json")}
    with pytest.raises(s9.Stage9Error) as ei:
        s9.enforce_publication_allowlist(inp, report_pair)
    assert ei.value.stage == "commit"
    assert "rename" in ei.value.reason


def test_allowlist_path_with_spaces_parsed(tmp_path):
    inp, repo, origin, _ = _real_world(tmp_path)
    spaced = repo / "a file with spaces.txt"
    spaced.write_text("x\n")
    _git(repo, "add", "--", "a file with spaces.txt")
    report_pair = {
        "report_path": str(repo / "md_library" / "shared" / "r.md"),
        "manifest_path": str(repo / "md_library" / "shared" / "r.manifest.json")}
    with pytest.raises(s9.Stage9Error) as ei:
        s9.enforce_publication_allowlist(inp, report_pair)
    assert "a file with spaces.txt" in ei.value.diag.get("stray_paths", [])


# ---------------------------------------------------------------------------
# Fix 5: flag mutual exclusivity (argparse/validation seam)
# ---------------------------------------------------------------------------


def test_publish_and_publish_dry_run_mutually_exclusive():
    import crunch_rebuild_orchestrator as orch
    with pytest.raises(SystemExit):
        orch.main(["--execute", "--operator-approved-publish",
                   "--publish", "--publish-dry-run"])


# ---------------------------------------------------------------------------
# Fix 6: fail closed on swallowed git failures (unchecked _git_out reads)
# ---------------------------------------------------------------------------


def _runner_raising_on(predicate, base=None):
    """A subprocess_runner that RAISES (exercising _run_git's synthetic-failed
    conversion -> rc=1, empty stdout) when ``predicate(argv_tuple)`` is true,
    else delegates to the real git runner."""
    real = base or s9._default_subprocess_runner

    def runner(argv, *, cwd, env):
        if predicate(tuple(argv)):
            raise RuntimeError("synthetic git seam failure")
        return real(argv, cwd=cwd, env=env)
    return runner


def test_preflight_head_rev_parse_synthetic_failure_refuses_before_compute(tmp_path):
    # A swallowed preflight `rev-parse HEAD` (empty stdout) must not pass through
    # as a blank HEAD; it must refuse at preflight before any CCC/compute.
    calls = {"upload": 0}
    base_upload = _upload_writing(_records("20260610T221108Z", ["IHI", "SCHG"]))

    def counting_upload(**kw):
        calls["upload"] += 1
        return base_upload(**kw)

    inp, repo, origin, _ = _real_world(
        tmp_path, upload_func=counting_upload,
        subprocess_runner=_runner_raising_on(
            lambda a: a[1:3] == ("rev-parse", "HEAD")))
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "preflight"
    assert "rev-parse HEAD" in summary["reason"]
    assert calls["upload"] == 0  # did not proceed to CCC
    assert _git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_live_verify_fetch_synthetic_failure_deploy_failed_no_polling(tmp_path):
    # Post-push `git fetch` swallowed failure must record deploy_failed_after_push
    # and must NOT fall through to HTTP polling against a stale ref.
    polls = {"n": 0}

    def counting_getter(url):
        polls["n"] += 1
        return (200, "{}")

    inp, repo, origin, _ = _real_world(
        tmp_path, http_getter=counting_getter,
        subprocess_runner=_runner_raising_on(lambda a: a[1] == "fetch"))
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "live_verify"
    assert summary.get("deploy_failed_after_push") is True
    assert polls["n"] == 0  # fetch failed -> no HTTP polling
    # the push itself succeeded before the failed verify
    assert _git(repo, "rev-parse", "origin/main").stdout.strip() == \
        _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_resume_status_synthetic_failure_refuses_no_push(tmp_path):
    # THE regression: resume cleanliness `status --porcelain` swallowed failure
    # (rc!=0, empty stdout) pre-fix read as a CLEAN worktree and proceeded to
    # push. Post-fix it must refuse at resume and never push.
    inp, repo, origin, _ = _real_world(tmp_path)
    _break_origin_push(origin)
    first = s9.run_stage9_publish(inp)
    assert first["stage"] == "push"  # commit created, not pushed
    _fix_origin_push(origin)  # origin would now accept a push
    origin_before = _git(repo, "rev-parse", "origin/main").stdout.strip()
    inp.subprocess_runner = _runner_raising_on(
        lambda a: a[1:3] == ("status", "--porcelain"))
    second = s9.run_stage9_publish(inp)
    assert second["status"] == "refused" and second["stage"] == "resume"
    # despite origin being fixed, the refusal stopped before any push
    assert _git(repo, "rev-parse", "origin/main").stdout.strip() == origin_before


def test_post_commit_rev_parse_failure_refuses_at_commit_no_push(tmp_path):
    # Post-commit `rev-parse HEAD` swallowed failure must refuse at commit (no
    # blank commit_sha recorded) and must never push.
    state = {"committed": False}
    real = s9._default_subprocess_runner

    def runner(argv, *, cwd, env):
        a = tuple(argv)
        if a[1] == "commit":
            res = real(argv, cwd=cwd, env=env)
            state["committed"] = True
            return res
        if state["committed"] and a[1:3] == ("rev-parse", "HEAD"):
            raise RuntimeError("synthetic post-commit rev-parse failure")
        return real(argv, cwd=cwd, env=env)

    inp, repo, origin, _ = _real_world(tmp_path, subprocess_runner=runner)
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "commit"
    assert "commit_created" not in summary["states"]
    # commit landed locally but was never pushed (origin still at init)
    assert _git(repo, "rev-list", "--count", "origin/main").stdout.strip() == "1"


def test_push_failure_with_failing_diagnostics_still_reports_push_error(tmp_path):
    # Push fails AND both diagnostic rev-parses also fail: the refusal must still
    # report the ORIGINAL push error; absent diagnostics must not mask it.
    inp, repo, origin, _ = _real_world(tmp_path)
    _break_origin_push(origin)
    state = {"pushed": False}
    real = s9._default_subprocess_runner

    def runner(argv, *, cwd, env):
        a = tuple(argv)
        if a[1] == "push":
            state["pushed"] = True
            return real(argv, cwd=cwd, env=env)  # rejected by pre-receive hook
        if state["pushed"] and a[1] == "rev-parse":
            raise RuntimeError("synthetic diagnostic rev-parse failure")
        return real(argv, cwd=cwd, env=env)

    inp.subprocess_runner = runner
    summary = s9.run_stage9_publish(inp)
    assert summary["status"] == "refused" and summary["stage"] == "push"
    refusal = json.loads((inp.run_dir / "publish_refusal.json").read_text())
    assert refusal.get("git_stderr")  # original push stderr preserved
    assert refusal.get("local_head") == ""  # diagnostics absent, not masking
