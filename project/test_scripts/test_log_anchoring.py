"""
Phase 1B-2B: engine log handler anchoring.

The three engines (spymaster, onepass, impactsearch) install
import-time FileHandlers. Before this change those handlers wrote to
``logs/<engine>.log`` relative to the current working directory, so
running pytest from the repo root left a stray ``logs/`` directory
under the repo root. After this change, each engine anchors its log
to ``project/logs/`` via ``Path(__file__).resolve().parent / 'logs'``.

These tests run each engine import in a fresh subprocess from a
temporary cwd outside ``project/`` so prior in-process imports do not
affect the result.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]


def _run_import(module: str, tmp_cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    # Keep imports quiet and offline.
    env.setdefault("IMPACT_TRUST_LIBRARY", "0")
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=str(tmp_cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    "module,log_filename",
    [
        ("spymaster", "spymaster.log"),
        ("onepass", "onepass.log"),
        ("impactsearch", "impactsearch.log"),
    ],
)
def test_engine_log_anchored_to_project_logs(tmp_path, module, log_filename):
    project_logs = PROJECT_DIR / "logs"
    project_log_file = project_logs / log_filename

    result = _run_import(module, tmp_path)

    assert result.returncode == 0, (
        f"Importing {module} failed: stderr=\n{result.stderr}"
    )

    # Primary assertion: the engine must not have created a logs/
    # directory under the subprocess cwd. This is the bug being fixed.
    assert not (tmp_path / "logs").exists(), (
        f"{module} wrote logs/ under the caller cwd ({tmp_path}); "
        "import-time log handler is not anchored to project/logs"
    )

    # Secondary assertion: project/logs/<engine>.log exists. (We can't
    # safely unlink-and-recreate to verify a fresh write because Windows
    # holds the FileHandler open for the duration of the in-process
    # imports earlier in the suite. Existence is enough — the negative
    # check above confirms the anchor.)
    assert project_log_file.exists(), (
        f"{module} did not produce {project_log_file}; "
        "expected anchored FileHandler to land here"
    )
