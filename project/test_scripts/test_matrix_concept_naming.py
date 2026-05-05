"""
Phase 5B Item 4 regression guard: legacy matrix.py concept and the
rejected TrafficFlow matrix-path symbols must remain absent from active
production Python files.

Phase 5A cleanup ledger Item 4 classified the matrix-concept naming
consolidation as ``cross-app-reconciliation`` and required a static
guard pinning the post-Item-2/Item-3 cleanup state. Items 2 (PR #151)
and 3 (PR #152) deleted the active references; this guard prevents
regression from a future PR re-introducing them.

Scope: scans only production .py files via the project's existing
``production_python_files()`` helper. Test scripts are intentionally
excluded — earlier static guards (test_trafficflow_matrix_path_removed,
test_spymaster_help_matrix_ref_removed) embed the banned terms in
their assertion text by design. md_library historical docs and .bat
files are also excluded; the ledger explicitly preserves md_library
historical references as audit evidence.

Legitimate qualified "matrix" usages (SMA matrix, correlation matrix,
heatmap matrix, risk/reward matrix, availability matrix, scatter
matrix, live Dash component IDs like mp-library-matrix-table) are
permitted by virtue of those terms not appearing on the banned list.

ASCII-only output per CLAUDE.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from phase2_test_utils import production_python_files

# Banned terms. Symbol-level bans pin the legacy matrix.py concept's
# removal precisely; phrase-level bans like "matrix path" / "matrix
# engine" are intentionally NOT included to avoid false positives —
# the symbol-level bans already prevent any meaningful reintroduction.
_BANNED_TERMS: Tuple[str, ...] = (
    "matrix.py",
    "TF_MATRIX_PATH",
    "TF_MATRIX_MAX_K",
    "TF_MATRIX_DTYPE",
    "Matrix=REMOVED",
    "_averages_via_matrix",
    "_members_signals_df_and_returns",
)


def test_matrix_concept_legacy_naming_absent():
    """No active production .py file may contain the banned matrix-
    concept terms.

    Collects every (file, term) violation across the full production
    file set before asserting, so a regression that reintroduces
    multiple terms or hits multiple files surfaces all of them in one
    failure rather than one at a time.
    """
    files = production_python_files()
    assert files, (
        "production_python_files() returned no files; this guard "
        "cannot run if production discovery is broken."
    )
    violations: List[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            rel = path.relative_to(PROJECT_DIR)
        except ValueError:
            rel = path
        rel_str = str(rel).replace("\\", "/")
        for term in _BANNED_TERMS:
            if term in text:
                violations.append(f"  - {rel_str}: {term!r}")
    assert not violations, (
        "[5B-Item4] matrix-concept naming regression: the following "
        "banned terms reappeared in production .py files:\n"
        + "\n".join(violations)
        + "\n\nLedger: 2026-05-05_PHASE_5A_CLEANUP_LEDGER.md Item 4 "
          "pinned the post-Item-2/Item-3 cleanup state. Reintroducing "
          "any banned term requires amending the ledger first with "
          "date and rationale; legitimate qualified 'matrix' usages "
          "(SMA, correlation, heatmap, risk/reward, availability, "
          "scatter) are not on the banned list."
    )
