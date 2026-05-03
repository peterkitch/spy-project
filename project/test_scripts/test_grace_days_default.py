"""
Phase 1B-2B / 2B-2B: calendar grace days plumbing.

Spec §20 mandates a default of 10 days. Phase 1B-2B unified the
non-QC engine constants. Phase 2B-2B refactored StackBuilder so the
grace value is threaded explicitly through ``run_for_secondary`` ->
``phase2_rank_all`` -> ``_score_primary`` ->
``apply_signals_to_secondary`` and ``phase3_build_stacks`` ->
``_signals_aligned_and_mask``. The env-var mutation in
``run_for_secondary`` was removed; ``args.grace_days=None`` now means
"use ``DEFAULT_GRACE_DAYS=10``" rather than "force env to 0".

These tests cover:
  - module-level defaults are 10 across the non-QC engines (1B-2B)
  - parser ``--grace-days`` default is None (2B-2B)
  - explicit grace=0 / grace=5 reach phase2_rank_all and
    phase3_build_stacks without any env-var write (2B-2B)
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def _run_default_probe(snippet: str) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("IMPACT_CALENDAR_GRACE_DAYS", None)
    env.setdefault("IMPACT_TRUST_LIBRARY", "0")
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(PROJECT_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"probe failed: stderr=\n{result.stderr}"
    return result.stdout.strip()


def test_stackbuilder_default_grace_days_is_10():
    # Use importlib so the project-level stackbuilder.py module wins
    # over the test_scripts/stackbuilder/ namespace package.
    sb = importlib.import_module("stackbuilder")
    if hasattr(sb, "__file__") and sb.__file__ is None:
        # Got the namespace package; reload from PROJECT_DIR explicitly.
        spec = importlib.util.spec_from_file_location(
            "stackbuilder", str(PROJECT_DIR / "stackbuilder.py")
        )
        sb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sb)
    assert sb.DEFAULT_GRACE_DAYS == 10


def test_impact_fastpath_default_grace_days_is_10():
    out = _run_default_probe(
        "from signal_library import impact_fastpath as fp; "
        "print(fp.IMPACT_CALENDAR_GRACE_DAYS)"
    )
    assert out == "10"


def test_impactsearch_default_grace_days_is_10():
    # Check the in-line `os.environ.get(..., '10')` defaults at the two
    # alignment sites by importing the module and verifying that the
    # boot log echoes the new default text. The module-level constant
    # is not exposed; we probe via the boot log line which is the
    # documented user-facing surface.
    snippet = (
        "import io, sys, contextlib;"
        "buf = io.StringIO();\n"
        "with contextlib.redirect_stdout(buf):\n"
        "    import impactsearch\n"
        "out = buf.getvalue()\n"
        "for line in out.splitlines():\n"
        "    if 'IMPACT_CALENDAR_GRACE_DAYS' in line: print(line.strip()); break\n"
    )
    out = _run_default_probe(snippet)
    assert "IMPACT_CALENDAR_GRACE_DAYS=10" in out, (
        f"impactsearch boot log did not echo grace_days=10: {out!r}"
    )


def _force_load_stackbuilder():
    """Resolve the project-level stackbuilder.py module, defeating any
    test_scripts/stackbuilder/ namespace shadow."""
    sb = importlib.import_module("stackbuilder")
    needs_force = not hasattr(sb, "phase1_preflight")
    if not needs_force:
        try:
            mod_file = Path(sb.__file__).resolve() if sb.__file__ else None
        except Exception:
            mod_file = None
        if mod_file != (PROJECT_DIR / "stackbuilder.py").resolve():
            needs_force = True
    if needs_force:
        spec = importlib.util.spec_from_file_location(
            "stackbuilder", str(PROJECT_DIR / "stackbuilder.py")
        )
        sb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sb)
    return sb


_REQUIRED_RUN_ARGS = dict(
    alpha=0.05,
    max_k=1,
    top_n=0,
    bottom_n=0,
    min_trigger_days=30,
    sharpe_eps=1e-6,
    seed_by="total_capture",
    output_format="xlsx",
    signal_lib_dir=None,
    verbose=False,
    combine_mode="intersection",
    outdir=None,
)


def _hydrate_args(ns):
    """Attach the minimum attributes ``run_for_secondary`` reads from
    args between preflight and phase2 (manifest write, progress, etc.).
    Existing attributes on ``ns`` are preserved."""
    for k, v in _REQUIRED_RUN_ARGS.items():
        if not hasattr(ns, k):
            setattr(ns, k, v)
    return ns


def _drive_run_for_secondary(monkeypatch, args, *, capture_grace=True):
    """Drive ``run_for_secondary`` past phase1 and stop before phase2/3
    do work; capture the ``grace_days`` kwarg each phase received.

    Phase 2B-2B helper. Returns ``(phase2_grace, phase3_grace)``. Raises
    if ``run_for_secondary`` stops before either phase is reached.
    """
    sb = _force_load_stackbuilder()
    _hydrate_args(args)
    pre_combine = sb.COMBINE_INTERSECTION
    pre_signal_dir = getattr(sb, "SIGNAL_LIB_DIR_RUNTIME", None)
    pre_verbose = sb.VERBOSE
    pre_env = os.environ.pop("IMPACT_CALENDAR_GRACE_DAYS", None)

    captured = {"phase2": "<unreached>", "phase3": "<unreached>"}

    def _fake_preflight(_args, _sec, _specified_primaries=None):
        # Return an empty primaries DF + empty returns so phase2 has
        # nothing to do; phase3 likewise short-circuits.
        empty_primaries = pd.DataFrame({"Primary Ticker": []})
        sec_rets = pd.Series(dtype=float)
        return empty_primaries, sec_rets, "X"

    def _fake_phase2(*pargs, **pkwargs):
        captured["phase2"] = pkwargs.get("grace_days", "<missing>")
        # Return three empty frames in (rank_all, rank_direct, rank_inverse) shape.
        empty = pd.DataFrame()
        return empty, empty, empty

    def _fake_phase3(*pargs, **pkwargs):
        captured["phase3"] = pkwargs.get("grace_days", "<missing>")
        # Return (leaderboard, members) shape; raise immediately after
        # capture so we don't have to fake out the rest of the pipeline.
        raise RuntimeError("stop after phase3 grace capture")

    try:
        monkeypatch.setattr(sb, "phase1_preflight", _fake_preflight)
        monkeypatch.setattr(sb, "phase2_rank_all", _fake_phase2)
        monkeypatch.setattr(sb, "phase3_build_stacks", _fake_phase3)
        with pytest.raises(RuntimeError, match="stop after phase3 grace capture"):
            sb.run_for_secondary(args, "X", specified_primaries=["AAA"])
        if capture_grace:
            assert "IMPACT_CALENDAR_GRACE_DAYS" not in os.environ, (
                "run_for_secondary must not mutate "
                "IMPACT_CALENDAR_GRACE_DAYS env var (Phase 2B-2B)"
            )
        return captured["phase2"], captured["phase3"]
    finally:
        os.environ.pop("IMPACT_CALENDAR_GRACE_DAYS", None)
        if pre_env is not None:
            os.environ["IMPACT_CALENDAR_GRACE_DAYS"] = pre_env
        sb.COMBINE_INTERSECTION = pre_combine
        if pre_signal_dir is not None:
            sb.SIGNAL_LIB_DIR_RUNTIME = pre_signal_dir
        sb.VERBOSE = pre_verbose


def test_stackbuilder_run_for_secondary_does_not_write_env(monkeypatch):
    """Phase 2B-2B: run_for_secondary no longer mutates
    ``IMPACT_CALENDAR_GRACE_DAYS``. Whether ``args.grace_days`` is unset
    (expect default 10 to flow through) or explicitly set (e.g. 5,
    expect 5 to flow through), the env var must remain untouched."""
    # Case 1: no grace_days attribute -> default DEFAULT_GRACE_DAYS=10
    args = SimpleNamespace(secondary="X")
    p2, p3 = _drive_run_for_secondary(monkeypatch, args)
    assert int(p2) == 10, f"phase2 expected grace_days=10 (default), got {p2!r}"
    assert int(p3) == 10, f"phase3 expected grace_days=10 (default), got {p3!r}"

    # Case 2: explicit grace_days=5 -> 5 reaches both phases, env still untouched
    args2 = SimpleNamespace(secondary="X", grace_days=5)
    p2, p3 = _drive_run_for_secondary(monkeypatch, args2)
    assert int(p2) == 5, f"phase2 expected grace_days=5, got {p2!r}"
    assert int(p3) == 5, f"phase3 expected grace_days=5, got {p3!r}"


def test_stackbuilder_explicit_grace_zero_strict_mode(monkeypatch):
    """Phase 2B-2B: explicit grace=0 (strict mode) reaches both phases
    verbatim and is NOT silently coerced to the default 10."""
    args = SimpleNamespace(secondary="X", grace_days=0)
    p2, p3 = _drive_run_for_secondary(monkeypatch, args)
    assert int(p2) == 0, f"phase2 expected grace_days=0 (strict), got {p2!r}"
    assert int(p3) == 0, f"phase3 expected grace_days=0 (strict), got {p3!r}"


def test_stackbuilder_kwarg_grace_overrides_args(monkeypatch):
    """Phase 2B-2B: explicit ``grace_days=`` kwarg on
    ``run_for_secondary`` itself takes precedence over
    ``args.grace_days``. This is the orchestration override path
    documented in the function docstring; it does not change the
    Dash callback or CLI semantics, but pins the helper-level
    contract."""
    sb = _force_load_stackbuilder()
    pre_combine = sb.COMBINE_INTERSECTION
    pre_signal_dir = getattr(sb, "SIGNAL_LIB_DIR_RUNTIME", None)
    pre_verbose = sb.VERBOSE
    pre_env = os.environ.pop("IMPACT_CALENDAR_GRACE_DAYS", None)
    captured = {"phase2": None, "phase3": None}

    def _fake_preflight(*a, **k):
        return pd.DataFrame({"Primary Ticker": []}), pd.Series(dtype=float), "X"

    def _fake_phase2(*a, **k):
        captured["phase2"] = k.get("grace_days")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    def _fake_phase3(*a, **k):
        captured["phase3"] = k.get("grace_days")
        raise RuntimeError("stop")

    try:
        monkeypatch.setattr(sb, "phase1_preflight", _fake_preflight)
        monkeypatch.setattr(sb, "phase2_rank_all", _fake_phase2)
        monkeypatch.setattr(sb, "phase3_build_stacks", _fake_phase3)
        # args.grace_days=2, but kwarg override=7 wins.
        args = _hydrate_args(SimpleNamespace(secondary="X", grace_days=2))
        with pytest.raises(RuntimeError, match="stop"):
            sb.run_for_secondary(args, "X", specified_primaries=["AAA"], grace_days=7)
        assert int(captured["phase2"]) == 7
        assert int(captured["phase3"]) == 7
        assert "IMPACT_CALENDAR_GRACE_DAYS" not in os.environ
    finally:
        os.environ.pop("IMPACT_CALENDAR_GRACE_DAYS", None)
        if pre_env is not None:
            os.environ["IMPACT_CALENDAR_GRACE_DAYS"] = pre_env
        sb.COMBINE_INTERSECTION = pre_combine
        if pre_signal_dir is not None:
            sb.SIGNAL_LIB_DIR_RUNTIME = pre_signal_dir
        sb.VERBOSE = pre_verbose


def test_parse_args_grace_default_none():
    """Phase 2B-2B: parser ``--grace-days`` default flipped from 0 to
    None so an unset CLI invocation routes through
    ``DEFAULT_GRACE_DAYS=10`` rather than forcing strict mode."""
    sb = _force_load_stackbuilder()
    # No --grace-days on the command line.
    args = sb.parse_args(["--secondary", "SPY"])
    assert args.grace_days is None, (
        f"expected --grace-days default None, got {args.grace_days!r}"
    )
    # Explicit 0 still parses to 0 (strict mode).
    args = sb.parse_args(["--secondary", "SPY", "--grace-days", "0"])
    assert args.grace_days == 0
    # Explicit 5 still parses to 5.
    args = sb.parse_args(["--secondary", "SPY", "--grace-days", "5"])
    assert args.grace_days == 5
