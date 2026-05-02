"""
Phase 1B-2B: StackBuilder --outdir honoring.

Pre-1B-2B, ``stackbuilder.run_for_secondary()`` hardcoded
``RUNS_ROOT`` as the output root and ignored ``args.outdir``. The
``--outdir`` CLI flag was parsed (and ``ensure_dir(args.outdir)`` was
called in ``main()``), but the actual run directory was always
created under ``RUNS_ROOT``. Dash-launched jobs were similarly stuck
on ``RUNS_ROOT`` regardless of the ``run_dash(outdir, ...)``
parameter.

These tests assert the path-construction logic without standing up
real StackBuilder runs:

  - run_for_secondary uses ``args.outdir`` as the output root when
    set, falling back to ``RUNS_ROOT`` only when args.outdir is
    None or empty.
  - Dash-launched jobs receive the run_dash-supplied outdir in
    their args.outdir field, not RUNS_ROOT.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def _import_stackbuilder():
    sb = importlib.import_module("stackbuilder")
    if not hasattr(sb, "run_for_secondary"):
        spec = importlib.util.spec_from_file_location(
            "stackbuilder", str(PROJECT_DIR / "stackbuilder.py")
        )
        sb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sb)
    return sb


@pytest.fixture
def _sb_state_guard():
    """Save/restore module globals that run_for_secondary mutates."""
    sb = _import_stackbuilder()
    pre_combine = sb.COMBINE_INTERSECTION
    pre_signal_dir = getattr(sb, "SIGNAL_LIB_DIR_RUNTIME", None)
    pre_verbose = sb.VERBOSE
    pre_grace = os.environ.pop("IMPACT_CALENDAR_GRACE_DAYS", None)
    try:
        yield sb
    finally:
        os.environ.pop("IMPACT_CALENDAR_GRACE_DAYS", None)
        if pre_grace is not None:
            os.environ["IMPACT_CALENDAR_GRACE_DAYS"] = pre_grace
        sb.COMBINE_INTERSECTION = pre_combine
        if pre_signal_dir is not None:
            sb.SIGNAL_LIB_DIR_RUNTIME = pre_signal_dir
        sb.VERBOSE = pre_verbose


def test_run_for_secondary_uses_args_outdir(monkeypatch, tmp_path, _sb_state_guard):
    sb = _sb_state_guard

    custom_outdir = tmp_path / "custom_outdir"
    captured_paths = {}

    # Stub out phase1_preflight to return synthetic minimal results,
    # then short-circuit before any IO past the secondary_parent
    # construction so we can observe the path that ensure_dir() got.
    def _fake_preflight(args_, secondary_, specified_primaries_=None):
        # Return (primaries_df, sec_rets, vendor_secondary)
        import pandas as pd
        return (pd.DataFrame({"Primary Ticker": ["AAA"]}),
                pd.Series([0.0, 0.01], dtype=float),
                "SPY")

    real_ensure_dir = sb.ensure_dir

    def _record_ensure_dir(p):
        # Record the first call (the secondary_parent), then raise to
        # short-circuit run_for_secondary before it does real work.
        if "secondary_parent" not in captured_paths:
            captured_paths["secondary_parent"] = str(p)
            raise RuntimeError("stop after secondary_parent")
        return real_ensure_dir(p)

    monkeypatch.setattr(sb, "phase1_preflight", _fake_preflight)
    monkeypatch.setattr(sb, "ensure_dir", _record_ensure_dir)

    args = SimpleNamespace(secondary="SPY", outdir=str(custom_outdir))
    with pytest.raises(RuntimeError, match="stop after secondary_parent"):
        sb.run_for_secondary(args, "SPY", specified_primaries=["AAA"])

    # secondary_parent should be under args.outdir, not RUNS_ROOT.
    assert captured_paths["secondary_parent"] == str(custom_outdir / "SPY")


def test_run_for_secondary_falls_back_to_runs_root_when_outdir_none(
    monkeypatch, _sb_state_guard
):
    sb = _sb_state_guard
    captured_paths = {}

    def _fake_preflight(args_, secondary_, specified_primaries_=None):
        import pandas as pd
        return (pd.DataFrame({"Primary Ticker": ["AAA"]}),
                pd.Series([0.0, 0.01], dtype=float),
                "SPY")

    real_ensure_dir = sb.ensure_dir

    def _record_ensure_dir(p):
        if "secondary_parent" not in captured_paths:
            captured_paths["secondary_parent"] = str(p)
            raise RuntimeError("stop after secondary_parent")
        return real_ensure_dir(p)

    monkeypatch.setattr(sb, "phase1_preflight", _fake_preflight)
    monkeypatch.setattr(sb, "ensure_dir", _record_ensure_dir)

    # args without outdir attribute
    args = SimpleNamespace(secondary="SPY")
    with pytest.raises(RuntimeError, match="stop after secondary_parent"):
        sb.run_for_secondary(args, "SPY", specified_primaries=["AAA"])

    expected = os.path.join(sb.RUNS_ROOT, "SPY")
    assert captured_paths["secondary_parent"] == expected


def test_dash_callback_threads_outdir_into_job_args():
    """The Dash callback should pass run_dash's outdir through to
    each job's args.outdir, not hardcode RUNS_ROOT.

    We exercise the path-construction logic by reading the source
    text directly. A full Dash run would require a server, which
    this offline suite avoids.
    """
    text = (PROJECT_DIR / "stackbuilder.py").read_text(encoding="utf-8")

    # The job-args block inside _run() must use the outdir parameter
    # from run_dash's enclosing scope, not the literal RUNS_ROOT.
    # Find the SimpleNamespace assignment inside _run.
    assert "outdir=_job_outdir" in text, (
        "Dash _run callback should set args.outdir from the run_dash "
        "outdir parameter (via _job_outdir), not RUNS_ROOT"
    )
    assert "_job_outdir = outdir if outdir else RUNS_ROOT" in text

    # main()'s no-args branch should pass args.outdir to run_dash.
    assert "run_dash(args.outdir, port=args.port)" in text, (
        "main() should pass args.outdir into run_dash so the Dash UI "
        "honors --outdir"
    )
    # The legacy hardcoded `run_dash(None, ...)` should be gone.
    assert "run_dash(None," not in text, (
        "main() should no longer call run_dash(None, ...); "
        "args.outdir must thread through"
    )
