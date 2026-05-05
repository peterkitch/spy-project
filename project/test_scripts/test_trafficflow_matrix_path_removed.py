"""
Phase 5B Item 2 regression guard: TrafficFlow disabled-matrix code path
must remain absent.

Phase 5A cleanup ledger Item 2 classified the rejected matrix path as
``delete``. This guard pins the deletion: if any of the matrix-path
constants, helpers, dead-branch markers, or banner substring reappear
in ``project/trafficflow.py``, this test fails loudly so the
regression is caught at PR time rather than during operator use.

ASCII-only output per CLAUDE.md (Windows cp1252 console rule).
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

TRAFFICFLOW_PATH = PROJECT_DIR / "trafficflow.py"

# All forbidden strings from Phase 5B Item 2 implementation prompt.
# Each represents a piece of the rejected matrix path that must not
# return: env-var constants, helper functions, the dead-branch guard
# pattern, the hard-off comment, and the startup-banner remnant.
_FORBIDDEN: tuple = (
    "TF_MATRIX_PATH",
    "TF_MATRIX_MAX_K",
    "TF_MATRIX_DTYPE",
    "_members_signals_df_and_returns",
    "_averages_via_matrix",
    "if False and TF_MATRIX_PATH",
    "Matrix path hard-off",
    "Matrix=REMOVED",
)


def test_trafficflow_matrix_path_removed():
    """``project/trafficflow.py`` must not contain any matrix-path
    artifact deleted in Phase 5B Item 2.
    """
    assert TRAFFICFLOW_PATH.exists(), (
        "trafficflow.py is missing; this guard cannot run if the "
        "module has been moved or removed."
    )
    text = TRAFFICFLOW_PATH.read_text(encoding="utf-8")
    hits: list = []
    for needle in _FORBIDDEN:
        if needle in text:
            hits.append(needle)
    assert not hits, (
        "[5B-Item2] TrafficFlow matrix-path deletion regression: "
        "the following forbidden strings reappeared in trafficflow.py:\n"
        + "\n".join(f"  - {h}" for h in hits)
        + "\n\nLedger: 2026-05-05_PHASE_5A_CLEANUP_LEDGER.md Item 2 "
          "classified this path as delete. If a future PR genuinely "
          "needs to reintroduce matrix-style structures, amend the "
          "ledger first with date and rationale."
    )
