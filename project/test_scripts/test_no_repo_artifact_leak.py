"""Regression guard against test-artifact leakage into the repo tree.

Two prior leak paths produced files under the project working tree
when pytest ran:

  1. ``test_stackbuilder_validation_integration.py::_phase2_outputs``
     used to pass ``str(Path(os.getcwd()))`` as outdir to
     ``stackbuilder.phase2_rank_all``. Phase 2's ``write_table``
     defaults to ``OUTPUT_FORMAT="xlsx"`` and writes ``rank_all``,
     ``rank_direct``, ``rank_inverse`` to that directory. When pytest
     ran from the repo root these landed at
     ``<repo>/rank_*.xlsx``; when run from inside ``project/`` they
     landed at ``project/rank_*.xlsx``.

  2. ``test_trafficflow_refresh_callback.py`` invoked the refresh
     callback whose post-exception path called real
     ``_load_secondary_prices(...)`` which writes via
     ``_write_cache_file`` to ``trafficflow.PRICE_CACHE_DIR``
     (default ``"price_cache/daily"``, relative). When pytest ran
     from repo root, real yfinance fetches landed in
     ``<repo>/price_cache/daily/AAA.csv``.

Both leaks are fixed by the test-side helpers in this commit. This
file pins the absence of those artifacts post-collection so a future
maintainer cannot silently reintroduce either pattern. The test fails
loudly with explicit forensic output if any of the known leak paths
exist when this test runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent

LEAK_FILES_RELATIVE: list[str] = [
    "rank_all.xlsx",
    "rank_direct.xlsx",
    "rank_inverse.xlsx",
    "price_cache/daily/AAA.csv",
]

LEAK_DIRS_RELATIVE: list[str] = [
    "price_cache",
]


def _existing_leaks(base: Path) -> list[str]:
    """Return absolute string paths of every known leak artifact that
    actually exists under ``base``. Empty list = clean."""
    found: list[str] = []
    for rel in LEAK_FILES_RELATIVE:
        p = base / rel
        if p.exists() and p.is_file():
            found.append(str(p))
    for rel in LEAK_DIRS_RELATIVE:
        p = base / rel
        # Only flag if the directory exists AND contains anything.
        # An empty (or absent) directory is fine.
        if p.exists() and p.is_dir():
            try:
                non_empty = any(p.iterdir())
            except OSError:
                non_empty = False
            if non_empty:
                found.append(str(p))
    return found


@pytest.mark.production_smoke
def test_no_rank_xlsx_or_price_cache_leak_under_project_or_repo_root():
    """Pin: regression tests must NOT generate rank_*.xlsx or
    price_cache artifacts under either the project tree or the repo
    root. If this test fails, identify the offending test (start with
    ``test_stackbuilder_validation_integration.py`` and
    ``test_trafficflow_refresh_callback.py``) and route its output to
    pytest's ``tmp_path`` instead of cwd.

    This guard walks the real project and repo roots, so it is
    sensitive to real operator-created operational state under
    ``price_cache/`` or similar paths that exist on a populated
    developer machine but not on a clean worktree. It is opt-in:
    skipped by default in the fast suite (the ``production_smoke``
    marker is deselected by ``pytest.ini`` addopts) and additionally
    requires ``PRJCT9_RUN_PRODUCTION_SMOKES=1`` to actually run when
    the marker is opted in.
    """
    import os
    if os.environ.get("PRJCT9_RUN_PRODUCTION_SMOKES") != "1":
        pytest.skip(
            "production smoke opt-in not set "
            "(PRJCT9_RUN_PRODUCTION_SMOKES=1)"
        )
    repo_leaks = _existing_leaks(REPO_ROOT)
    project_leaks = _existing_leaks(PROJECT_DIR)
    leaks = repo_leaks + project_leaks
    assert not leaks, (
        "Test-artifact leak detected. The following paths exist after "
        "the test session reached this guard:\n  - "
        + "\n  - ".join(leaks)
        + "\nIf this fired in CI / a clean run, find the test that "
          "wrote each path and route its output through pytest "
          "tmp_path instead of os.getcwd() / a relative default. "
          "Do NOT fix this by adding broader .gitignore rules."
    )
