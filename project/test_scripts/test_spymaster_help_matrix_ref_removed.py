"""
Phase 5B Item 3 regression guard: Spymaster Help UI must not reference
the nonexistent ``matrix.py`` module.

Phase 5A cleanup ledger Item 3 classified the misleading Help-text
references as ``doc-only`` and required them to be replaced with
language pointing operators to the existing Multi-Primary Signal
Aggregator surface. This guard pins that cleanup: if any future PR
reintroduces ``matrix.py`` into the active Spymaster Dash source, the
test fails loudly so the regression is caught at PR time.

Scope: scans only ``project/spymaster.py``. Historical references in
md_library docs are intentionally NOT scanned (they remain as audit
trail for the rejected matrix experiment).

ASCII-only output per CLAUDE.md.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

SPYMASTER_PATH = PROJECT_DIR / "spymaster.py"


def test_spymaster_help_ui_matrix_py_reference_removed():
    """``project/spymaster.py`` must not contain the literal string
    ``matrix.py``. The Help UI was the last live reference; Phase 5B
    Item 3 removed it.
    """
    assert SPYMASTER_PATH.exists(), (
        "spymaster.py is missing; this guard cannot run if the module "
        "has been moved or removed."
    )
    text = SPYMASTER_PATH.read_text(encoding="utf-8")
    assert "matrix.py" not in text, (
        "[5B-Item3] Spymaster Help UI matrix.py regression: the "
        "literal string 'matrix.py' reappeared in spymaster.py. "
        "Ledger 2026-05-05_PHASE_5A_CLEANUP_LEDGER.md Item 3 "
        "classified this as doc-only deletion. If a real matrix.py "
        "module is ever introduced, amend the ledger first with "
        "date and rationale."
    )
