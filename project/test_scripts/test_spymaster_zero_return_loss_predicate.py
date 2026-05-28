"""PR B (zero-return-loss convention): regression guard for the two
completed-trade win/loss sites in spymaster.py.

Pins the canonical predicate at canonical_scoring.py:207-209
(losses = trade_count - wins, so zero-pnl completed BUY / SHORT
trades count as losses) at the two divergent fallback / display
sites identified by the Codex audit:

  - the recent-positions performance summary in
    update_dynamic_strategy_display
  - the master-audit wins/losses fallback in the snapshot table

Both sites operate on ``completed_trades`` / ``completed`` lists
already filtered to closed (exit_price-set) BUY / SHORT positions,
so zero-pnl entries are directional trades whose canonical
classification is loss (not "neither").

The test is static: it scans spymaster.py for the two predicate
patterns. A dynamic Dash-callback fixture for these sites would be
heavyweight (the spymaster module is the legacy standalone Dash app)
and would obscure the predicate-level guard. The static scan is
consistent with
``test_spymaster_dynamic_strategy_display.py``'s ast-based guard for
the annualized_return regression class.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SPYMASTER_PATH = PROJECT_ROOT / "spymaster.py"


def _spymaster_source() -> str:
    return SPYMASTER_PATH.read_text(encoding="utf-8")


def test_recent_positions_losses_predicate_uses_le_zero():
    """Site 1: the recent-positions performance summary in
    update_dynamic_strategy_display. The losses list comprehension
    must use ``<= 0`` (so zero-pnl completed trades count as losses).
    The old ``< 0`` form must be absent at this site."""
    src = _spymaster_source()
    # The amended predicate.
    assert (
        "losses = [t for t in completed_trades if t.get('pnl', 0) <= 0]"
        in src
    ), (
        "spymaster.py recent-positions losses predicate is missing "
        "the canonical zero-pnl-is-loss form "
        "(losses = [... <= 0])"
    )
    # Regression: the old strict-less-than form must be gone.
    assert (
        "losses = [t for t in completed_trades if t.get('pnl', 0) < 0]"
        not in src
    ), (
        "spymaster.py still uses the old strict-less-than losses "
        "predicate at the recent-positions site"
    )


def test_recent_positions_filters_to_directional_positions():
    """Site 1 (PR #344 amendment): completed_trades must be filtered
    to directional positions (Buy / Short) before win/loss
    classification, so Cash / NONE / no-position rows cannot enter
    the per-trade counts even when upstream emits them with a
    non-null exit_price."""
    src = _spymaster_source()
    # The explicit two-step filter must be present.
    assert "closed_positions = [" in src, (
        "spymaster.py site 1 is missing the explicit closed-positions "
        "filter introduced for the directional-only guard"
    )
    # Directional restriction.
    assert (
        "p.get('position') in ('Buy', 'Short')" in src
    ), (
        "spymaster.py site 1 is missing the directional position "
        "filter (Buy / Short only) at the recent-positions site"
    )


def test_master_audit_fallback_losses_uses_subtraction():
    """Site 2: the master-audit wins/losses fallback in the snapshot
    table. losses_calc must be derived as ``len(completed) -
    wins_calc`` so the count invariant
    ``wins_calc + losses_calc == len(completed)`` holds. The old
    ``sum(1 for ... < 0)`` form must be absent at this site."""
    src = _spymaster_source()
    assert (
        "losses_calc = len(completed) - wins_calc" in src
    ), (
        "spymaster.py master-audit fallback losses_calc is missing "
        "the canonical len(completed) - wins_calc form"
    )
    # Regression: the old strict-less-than counter must be gone.
    bad = re.compile(
        r"losses_calc\s*=\s*sum\(1 for t in completed if\s*"
        r"\(t\.get\(['\"]pnl['\"]\)\s+or\s+0\)\s*<\s*0\)"
    )
    assert not bad.search(src), (
        "spymaster.py still uses the old strict-less-than "
        "losses_calc predicate at the master-audit site"
    )


def test_master_audit_fallback_filters_to_directional_positions():
    """Site 2 (PR #344 amendment): ``completed`` must be filtered to
    directional positions (Buy / Short) before win/loss
    classification, so Cash / NONE / no-position rows cannot enter
    the wins_calc / losses_calc counts."""
    src = _spymaster_source()
    # Find the master-audit fallback's completed list comprehension
    # and require both filters (exit_price-set AND directional).
    fallback_idx = src.find("Fallback for wins/losses/win_ratio")
    assert fallback_idx > -1, (
        "could not locate the master-audit fallback comment"
    )
    window = src[fallback_idx: fallback_idx + 2000]
    assert "completed = [" in window
    assert "p.get('exit_price') is not None" in window
    assert "p.get('position') in ('Buy', 'Short')" in window, (
        "spymaster.py master-audit fallback is missing the "
        "directional position filter (Buy / Short only)"
    )


def test_canonical_equivalence_predicate_on_synthetic_completed_trades():
    """Pure unit-level canonical-equivalence check. Apply the same
    predicate the spymaster sites now use to a synthetic list of
    completed trades covering positive pnl, negative pnl, and
    zero pnl entries. Under the canonical convention the invariant
    ``wins + losses == n`` holds and zero-pnl entries are losses."""
    completed_trades = [
        {"pnl": 2.5, "position": "Buy"},
        {"pnl": -1.0, "position": "Short"},
        {"pnl": 0.0, "position": "Buy"},
        {"pnl": 0.5, "position": "Buy"},
        {"pnl": 0.0, "position": "Short"},
        {"pnl": -0.25, "position": "Short"},
    ]
    # Site 1 predicate.
    wins = [t for t in completed_trades if t.get("pnl", 0) > 0]
    losses = [t for t in completed_trades if t.get("pnl", 0) <= 0]
    assert len(wins) == 2
    assert len(losses) == 4
    assert len(wins) + len(losses) == len(completed_trades)
    # Site 2 predicate.
    wins_calc = sum(1 for t in completed_trades if (t.get("pnl") or 0) > 0)
    losses_calc = len(completed_trades) - wins_calc
    assert wins_calc == 2
    assert losses_calc == 4
    assert wins_calc + losses_calc == len(completed_trades)


def test_cash_position_excluded_from_site_1_predicate():
    """The amended site 1 filter excludes Cash / no-position rows
    even when they carry a non-null exit_price. A synthetic mix of
    Buy, Short, and Cash rows resolves to only the directional
    subset in the wins / losses counts."""
    recent_positions = [
        {"exit_price": 100.0, "pnl": 2.5, "position": "Buy"},
        {"exit_price": 100.0, "pnl": 0.0, "position": "Cash"},
        {"exit_price": 100.0, "pnl": -1.0, "position": "Short"},
        {"exit_price": None, "pnl": None, "position": "Buy"},  # open
        {"exit_price": 100.0, "pnl": 5.0, "position": "Cash"},  # excluded
        {"exit_price": 100.0, "pnl": 0.0, "position": "Short"},
    ]
    # Apply the spymaster site 1 two-step filter.
    closed_positions = [
        p for p in recent_positions if p.get("exit_price") is not None
    ]
    completed_trades = [
        p for p in closed_positions
        if p.get("position") in ("Buy", "Short")
    ]
    assert len(closed_positions) == 5  # the open Buy is dropped
    # Cash rows excluded: 2 closed Cash rows dropped.
    assert len(completed_trades) == 3
    wins = [t for t in completed_trades if t.get("pnl", 0) > 0]
    losses = [t for t in completed_trades if t.get("pnl", 0) <= 0]
    assert len(wins) == 1  # only the +2.5 BUY
    assert len(losses) == 2  # the -1.0 SHORT and the 0.0 SHORT
    assert len(wins) + len(losses) == len(completed_trades)


def test_cash_position_excluded_from_site_2_predicate():
    """Symmetric Cash-exclusion proof for the master-audit fallback
    at site 2."""
    position_history_data = [
        {"exit_price": 100.0, "pnl": 1.0, "position": "Buy"},
        {"exit_price": 100.0, "pnl": 10.0, "position": "Cash"},  # excluded
        {"exit_price": 100.0, "pnl": -0.5, "position": "Short"},
        {"exit_price": None, "pnl": None, "position": "Buy"},  # open
        {"exit_price": 100.0, "pnl": 0.0, "position": "Buy"},
    ]
    completed = [
        p for p in position_history_data
        if p.get("exit_price") is not None
        and p.get("position") in ("Buy", "Short")
    ]
    assert len(completed) == 3  # Cash and open BUY dropped
    wins_calc = sum(1 for t in completed if (t.get("pnl") or 0) > 0)
    losses_calc = len(completed) - wins_calc
    assert wins_calc == 1  # only the +1.0 BUY
    assert losses_calc == 2  # the -0.5 SHORT and the 0.0 BUY
    assert wins_calc + losses_calc == len(completed)
