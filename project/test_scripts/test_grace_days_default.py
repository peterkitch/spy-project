"""
Phase 1B-2B: calendar grace days default unification.

Spec §20 mandates a default of 10 days. Previously the non-QC engines
were split (7 / 0). These tests assert each engine's default grace
days is 10 when ``IMPACT_CALENDAR_GRACE_DAYS`` is unset, and that
StackBuilder's runtime override no longer forces the env var to 0
when ``args.grace_days`` is not explicitly supplied.

The constant-default tests use subprocesses so prior in-process
imports don't mask a hardcoded module-level read.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_stackbuilder_run_for_secondary_does_not_force_grace_zero(monkeypatch):
    # Phase 1B-2B: previously run_for_secondary() did
    #   os.environ['IMPACT_CALENDAR_GRACE_DAYS'] = str(getattr(args, 'grace_days', 0) or 0)
    # which forced grace to 0 for any args without an explicit
    # grace_days attribute, defeating DEFAULT_GRACE_DAYS=10.
    #
    # The new behavior leaves the env var untouched unless
    # args.grace_days is explicitly set, so DEFAULT_GRACE_DAYS governs.
    stackbuilder = importlib.import_module("stackbuilder")
    if not hasattr(stackbuilder, "phase1_preflight"):
        spec = importlib.util.spec_from_file_location(
            "stackbuilder", str(PROJECT_DIR / "stackbuilder.py")
        )
        stackbuilder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stackbuilder)

    # Save and restore any pre-existing env var. Production code under
    # test writes to os.environ directly (not via monkeypatch), so we
    # take responsibility for cleanup. run_for_secondary also mutates
    # the module-level COMBINE_INTERSECTION based on args.combine_mode;
    # save/restore so this test does not pollute other tests.
    pre_existing = os.environ.pop("IMPACT_CALENDAR_GRACE_DAYS", None)
    pre_combine = stackbuilder.COMBINE_INTERSECTION
    pre_signal_dir = getattr(stackbuilder, "SIGNAL_LIB_DIR_RUNTIME", None)
    pre_verbose = stackbuilder.VERBOSE

    try:
        # Patch phase1_preflight to short-circuit before doing any IO.
        def _stop(*args, **kwargs):
            raise RuntimeError("stop after env set")

        monkeypatch.setattr(stackbuilder, "phase1_preflight", _stop)

        # args without grace_days attribute -> env var should NOT be set
        args = SimpleNamespace(secondary="X")
        with pytest.raises(RuntimeError, match="stop after env set"):
            stackbuilder.run_for_secondary(args, "X", specified_primaries=["AAA"])

        assert "IMPACT_CALENDAR_GRACE_DAYS" not in os.environ, (
            "run_for_secondary should not write IMPACT_CALENDAR_GRACE_DAYS "
            "when args.grace_days is unset"
        )

        # args.grace_days = 5 -> env var SHOULD be set to '5'
        args2 = SimpleNamespace(secondary="X", grace_days=5)
        with pytest.raises(RuntimeError, match="stop after env set"):
            stackbuilder.run_for_secondary(args2, "X", specified_primaries=["AAA"])
        assert os.environ.get("IMPACT_CALENDAR_GRACE_DAYS") == "5"
    finally:
        os.environ.pop("IMPACT_CALENDAR_GRACE_DAYS", None)
        if pre_existing is not None:
            os.environ["IMPACT_CALENDAR_GRACE_DAYS"] = pre_existing
        stackbuilder.COMBINE_INTERSECTION = pre_combine
        if pre_signal_dir is not None:
            stackbuilder.SIGNAL_LIB_DIR_RUNTIME = pre_signal_dir
        stackbuilder.VERBOSE = pre_verbose
