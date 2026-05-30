"""
Phase 5B Item 1 regression tests: StackBuilder vestigial CLI flag
deprecation warnings.

Three CLI flags were classified as ``deprecate-with-warning`` per the
locked Phase 5A cleanup ledger Item 1:

  * ``--alpha``                  (no longer changes scoring)
  * ``--min-marginal-capture``   (no effect in current search path)
  * ``--fail-on-missing-cache``  (no effect; superseded by manifest flags)

Each remains parseable with its existing default. When the flag is
explicitly supplied on the command line, ``parse_args`` emits an ASCII
``[STACKBUILDER:DEPRECATED]``-prefixed line on stderr exactly once per
deprecated flag per parse. This file pins:

  A. each deprecated flag emits its warning when explicitly supplied;
  B. a default invocation (no deprecated flags) emits NO warning;
  C. the active flag set Codex preflight enumerated emits NO warning;
  D. the ``--flag=value`` form is detected as well as ``--flag value``.

ASCII-only assertion messages per CLAUDE.md cp1252 discipline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import stackbuilder  # noqa: E402


_DEPRECATED_PREFIX = "[STACKBUILDER:DEPRECATED]"


# ---------------------------------------------------------------------------
# A — each deprecated flag emits its warning when explicitly supplied
# ---------------------------------------------------------------------------


def test_alpha_explicitly_supplied_emits_deprecation_warning(capsys):
    ns = stackbuilder.parse_args(["--secondary", "SPY", "--alpha", "0.05"])
    err = capsys.readouterr().err
    assert "[STACKBUILDER:DEPRECATED] --alpha:" in err, (
        f"expected --alpha deprecation line on stderr; got:\n{err}"
    )
    # Default must still apply (the flag is still parseable).
    assert ns.alpha == 0.05


def test_min_marginal_capture_explicitly_supplied_emits_deprecation_warning(
    capsys,
):
    ns = stackbuilder.parse_args(
        ["--secondary", "SPY", "--min-marginal-capture", "0.0"],
    )
    err = capsys.readouterr().err
    assert "[STACKBUILDER:DEPRECATED] --min-marginal-capture:" in err, (
        f"expected --min-marginal-capture deprecation line on stderr; "
        f"got:\n{err}"
    )
    assert ns.min_marginal_capture == 0.0


def test_fail_on_missing_cache_explicitly_supplied_emits_deprecation_warning(
    capsys,
):
    ns = stackbuilder.parse_args(
        ["--secondary", "SPY", "--fail-on-missing-cache"],
    )
    err = capsys.readouterr().err
    assert "[STACKBUILDER:DEPRECATED] --fail-on-missing-cache:" in err, (
        f"expected --fail-on-missing-cache deprecation line on stderr; "
        f"got:\n{err}"
    )
    assert ns.fail_on_missing_cache is True


# ---------------------------------------------------------------------------
# B — default invocation emits no deprecation warning
# ---------------------------------------------------------------------------


def test_default_parse_emits_no_deprecation_warning(capsys):
    ns = stackbuilder.parse_args(["--secondary", "SPY"])
    err = capsys.readouterr().err
    assert _DEPRECATED_PREFIX not in err, (
        f"unexpected deprecation warning on a default parse:\n{err}"
    )
    # Defaults intact.
    assert ns.alpha == 0.05
    assert ns.min_marginal_capture == 0.0
    assert ns.fail_on_missing_cache is False


# ---------------------------------------------------------------------------
# C — active flag set must not trigger any deprecation warning
# ---------------------------------------------------------------------------


def test_active_ledger_example_flags_emit_no_deprecation_warning(capsys):
    """Codex preflight enumerated this active set; none of these
    should trigger a deprecation warning today.

    Phase 6I-73: --seed-by / --optimize-by now only accept
    ``total_capture``; the prior assertions on Sharpe are reframed
    as parse-time refusals below.
    """
    ns = stackbuilder.parse_args([
        "--secondary", "SPY",
        "--allow-decreasing",
        "--exhaustive-k", "4",
        "--both-modes",
        "--k-patience", "1",
        "--save-stats",
        "--serve",
        "--port", "8054",
        "--optimize-by", "total_capture",
        "--seed-by", "total_capture",
    ])
    err = capsys.readouterr().err
    assert _DEPRECATED_PREFIX not in err, (
        f"active-flag invocation unexpectedly produced a deprecation "
        f"warning:\n{err}"
    )
    # Sanity: a couple of the active flags actually parsed.
    assert ns.allow_decreasing is True
    assert ns.exhaustive_k == 4
    assert ns.both_modes is True
    assert ns.k_patience == 1
    assert ns.save_stats is True
    assert ns.serve is True
    assert ns.port == 8054
    assert ns.optimize_by == "total_capture"
    assert ns.seed_by == "total_capture"


# ---------------------------------------------------------------------------
# Phase 6I-73: Sharpe is refused at CLI parse time
# ---------------------------------------------------------------------------


def test_stackbuilder_cli_refuses_seed_by_sharpe():
    with pytest.raises(SystemExit):
        stackbuilder.parse_args(["--secondary", "SPY", "--seed-by", "sharpe"])


def test_stackbuilder_cli_refuses_optimize_by_sharpe():
    with pytest.raises(SystemExit):
        stackbuilder.parse_args(["--secondary", "SPY", "--optimize-by", "sharpe"])


# ---------------------------------------------------------------------------
# D — equals-form detection (--flag=value)
# ---------------------------------------------------------------------------


def test_alpha_equals_form_emits_deprecation_warning(capsys):
    """``--alpha=0.05`` (equals form) must trigger the deprecation
    warning the same way ``--alpha 0.05`` does."""
    ns = stackbuilder.parse_args(["--secondary", "SPY", "--alpha=0.10"])
    err = capsys.readouterr().err
    assert "[STACKBUILDER:DEPRECATED] --alpha:" in err, (
        f"expected --alpha deprecation line for equals-form; got:\n{err}"
    )
    assert ns.alpha == 0.10


# ---------------------------------------------------------------------------
# E - allow-decreasing default and opt-out (carryforward item #3)
# ---------------------------------------------------------------------------


def test_engine_allow_decreasing_default_is_true_when_no_flag():
    """Carryforward item #3 (operator-decided): the engine CLI defaults
    allow_decreasing to True when neither --allow-decreasing nor
    --no-allow-decreasing is passed."""
    ns = stackbuilder.parse_args(["--secondary", "SPY"])
    assert ns.allow_decreasing is True


def test_engine_no_allow_decreasing_opts_out_to_false():
    """--no-allow-decreasing is the explicit opt-out from the new
    default-True contract; it sets allow_decreasing to False."""
    ns = stackbuilder.parse_args(["--secondary", "SPY", "--no-allow-decreasing"])
    assert ns.allow_decreasing is False


def test_engine_allow_decreasing_flag_remains_true_for_backcompat():
    """The legacy --allow-decreasing flag remains parseable and still
    resolves to True (backward compatibility for existing invocations)."""
    ns = stackbuilder.parse_args(["--secondary", "SPY", "--allow-decreasing"])
    assert ns.allow_decreasing is True
