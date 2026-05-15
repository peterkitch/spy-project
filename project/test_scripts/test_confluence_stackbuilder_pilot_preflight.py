"""Phase 6I-53 tests for the StackBuilder pilot-batch
preflight module.

Pins:

  * Schema-version constant is stable.
  * Five candidate cache paths match the
    ``stackbuilder.load_secondary_prices`` order
    exactly:
      <PCD>/<T>.parquet
      <PCD>/<T>.csv
      <PCD>/<T_no_caret>.parquet
      <PCD>/<T_no_caret>.csv
      <PCD>/<T>/daily.parquet
  * Pass classification when ANY candidate exists.
  * Skip-missing classification when NONE exist
    (would_fetch_yfinance=True).
  * Caret-stripped variant catches ``^GSPC``-style index
    tickers cached as ``GSPC.parquet``.
  * Subdirectory ``<PCD>/<T>/daily.parquet`` form is
    detected.
  * Aggregate counts + pass/skip ticker lists are
    consistent.
  * PRICE_CACHE_DIR env var override is honored via the
    injectable ``env_overrides`` parameter.
  * Default ticker universe matches the Phase 6I-52
    pilot universe (25 tickers, SPY first) via deferred
    import.
  * No forbidden top-level imports (no subprocess,
    yfinance, writer / engine / stackbuilder modules).
  * ``--output`` path guard rejects production-root
    paths.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_stackbuilder_pilot_preflight as pf  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")  # zero-byte file is enough for
                           # exists()/is_file() checks


# ---------------------------------------------------------------------------
# 1. Schema + status taxonomy stability
# ---------------------------------------------------------------------------


def test_schema_and_status_constants_are_stable():
    assert (
        pf.SCHEMA_VERSION
        == "confluence_stackbuilder_pilot_preflight_v1"
    )
    assert (
        pf.DEFAULT_PRICE_CACHE_DIR_RELATIVE
        == "price_cache/daily"
    )
    assert pf.PREFLIGHT_STATUS_PASS == "pass"
    assert (
        pf.PREFLIGHT_STATUS_SKIP_MISSING_CACHE
        == "skip_missing_cache_would_fetch_yfinance"
    )
    assert (
        pf.PREFLIGHT_STATUS_PASS
        in pf.ALL_PREFLIGHT_STATUSES
    )
    assert (
        pf.PREFLIGHT_STATUS_SKIP_MISSING_CACHE
        in pf.ALL_PREFLIGHT_STATUSES
    )


# ---------------------------------------------------------------------------
# 2. Five candidate paths match stackbuilder.py:530-556.
# ---------------------------------------------------------------------------


def test_candidate_paths_match_stackbuilder_order(
    tmp_path,
):
    cands = pf._candidate_paths_for_ticker(
        "SPY", price_cache_dir=tmp_path,
    )
    names = [c.name for c in cands]
    # The first four are flat files in price_cache_dir.
    assert names[:4] == [
        "SPY.parquet",
        "SPY.csv",
        "SPY.parquet",
        "SPY.csv",
    ]
    # The fifth is a subdirectory form.
    assert cands[4] == tmp_path / "SPY" / "daily.parquet"


def test_candidate_paths_caret_stripped_for_index_ticker(
    tmp_path,
):
    """``^GSPC`` -> the caret-stripped variant resolves to
    ``GSPC.parquet`` / ``GSPC.csv``, matching how the
    on-disk cache typically stores index tickers."""
    cands = pf._candidate_paths_for_ticker(
        "^GSPC", price_cache_dir=tmp_path,
    )
    names = [c.name for c in cands]
    assert names[:4] == [
        "^GSPC.parquet",
        "^GSPC.csv",
        "GSPC.parquet",
        "GSPC.csv",
    ]
    assert cands[4] == tmp_path / "^GSPC" / "daily.parquet"


# ---------------------------------------------------------------------------
# 3. Pass classification when ANY candidate exists.
# ---------------------------------------------------------------------------


def test_pass_when_any_candidate_exists(tmp_path):
    """A single ``SPY.parquet`` in the price-cache dir
    must classify SPY as pass."""
    _touch(tmp_path / "SPY.parquet")
    table = pf.build_preflight_table(
        ["SPY"],
        price_cache_dir=tmp_path,
        env_overrides={},  # ignore real env
    )
    assert table["pass_count"] == 1
    assert table["skip_count"] == 0
    assert table["tickers_passing_preflight"] == ["SPY"]
    row = table["rows"][0]
    assert row["preflight_status"] == pf.PREFLIGHT_STATUS_PASS
    assert row["local_price_cache_available"] is True
    assert row["would_fetch_yfinance"] is False
    assert "SPY.parquet" in row["resolved_cache_path"]


def test_pass_via_subdirectory_form(tmp_path):
    """The fifth candidate is ``<PCD>/<T>/daily.parquet``;
    confirm the preflight detects it."""
    _touch(tmp_path / "AAPL" / "daily.parquet")
    table = pf.build_preflight_table(
        ["AAPL"],
        price_cache_dir=tmp_path,
        env_overrides={},
    )
    assert table["pass_count"] == 1
    row = table["rows"][0]
    assert "daily.parquet" in row["resolved_cache_path"]


def test_pass_via_caret_stripped_form(tmp_path):
    """A ``^GSPC`` ticker with only the caret-stripped
    ``GSPC.parquet`` on disk still classifies as pass."""
    _touch(tmp_path / "GSPC.parquet")
    table = pf.build_preflight_table(
        ["^GSPC"],
        price_cache_dir=tmp_path,
        env_overrides={},
    )
    assert table["pass_count"] == 1


# ---------------------------------------------------------------------------
# 4. Skip-missing classification when NONE exist.
# ---------------------------------------------------------------------------


def test_skip_when_no_candidate_exists(tmp_path):
    """An empty price-cache dir classifies every ticker
    as skip-missing-cache + would_fetch_yfinance=True."""
    table = pf.build_preflight_table(
        ["XYZ"],
        price_cache_dir=tmp_path,
        env_overrides={},
    )
    assert table["pass_count"] == 0
    assert table["skip_count"] == 1
    row = table["rows"][0]
    assert (
        row["preflight_status"]
        == pf.PREFLIGHT_STATUS_SKIP_MISSING_CACHE
    )
    assert row["local_price_cache_available"] is False
    assert row["resolved_cache_path"] is None
    assert row["would_fetch_yfinance"] is True


def test_skip_when_price_cache_dir_does_not_exist(
    tmp_path,
):
    """An entirely missing price-cache dir is the
    production state today; every ticker must skip."""
    nonexistent = tmp_path / "no_such_dir"
    table = pf.build_preflight_table(
        ["SPY", "AAPL", "MSFT"],
        price_cache_dir=nonexistent,
        env_overrides={},
    )
    assert table["price_cache_dir_exists"] is False
    assert table["pass_count"] == 0
    assert table["skip_count"] == 3
    for row in table["rows"]:
        assert (
            row["preflight_status"]
            == pf.PREFLIGHT_STATUS_SKIP_MISSING_CACHE
        )


# ---------------------------------------------------------------------------
# 5. Aggregate counts + pass/skip ticker lists are
#    consistent across mixed-state universes.
# ---------------------------------------------------------------------------


def test_aggregate_counts_consistent_for_mixed_universe(
    tmp_path,
):
    _touch(tmp_path / "SPY.parquet")
    _touch(tmp_path / "AAPL.csv")
    # MSFT, TSLA missing -> skip.
    table = pf.build_preflight_table(
        ["SPY", "AAPL", "MSFT", "TSLA"],
        price_cache_dir=tmp_path,
        env_overrides={},
    )
    assert table["ticker_count"] == 4
    assert table["pass_count"] == 2
    assert table["skip_count"] == 2
    assert table["tickers_passing_preflight"] == [
        "AAPL", "SPY",
    ]
    assert table["tickers_skipped_missing_cache"] == [
        "MSFT", "TSLA",
    ]
    # The rows list keeps the input order (post-normalize).
    row_tickers = [r["ticker"] for r in table["rows"]]
    assert row_tickers == ["SPY", "AAPL", "MSFT", "TSLA"]


# ---------------------------------------------------------------------------
# 6. PRICE_CACHE_DIR env-var override.
# ---------------------------------------------------------------------------


def test_env_override_honored(tmp_path):
    """``env_overrides`` (which the CLI populates from
    os.environ) must take precedence over the default
    path."""
    custom = tmp_path / "custom_price_cache"
    _touch(custom / "SPY.parquet")
    table = pf.build_preflight_table(
        ["SPY"],
        env_overrides={"PRICE_CACHE_DIR": str(custom)},
    )
    assert table["pass_count"] == 1
    assert (
        table["price_cache_dir_used"]
        == str(custom)
    )


def test_explicit_price_cache_dir_overrides_env(
    tmp_path,
):
    """Explicit kwarg beats the env-var override."""
    via_env = tmp_path / "from_env"
    via_arg = tmp_path / "from_arg"
    _touch(via_env / "SPY.parquet")  # would pass via env
    # But via_arg is empty so SPY should skip when the
    # explicit dir wins.
    table = pf.build_preflight_table(
        ["SPY"],
        price_cache_dir=via_arg,
        env_overrides={"PRICE_CACHE_DIR": str(via_env)},
    )
    assert table["pass_count"] == 0
    assert table["price_cache_dir_used"] == str(via_arg)


# ---------------------------------------------------------------------------
# 7. Default ticker universe matches Phase 6I-52 pilot.
# ---------------------------------------------------------------------------


def test_default_universe_is_phase_6i_52_pilot():
    table = pf.build_preflight_table(
        price_cache_dir="some/nonexistent/path",
        env_overrides={},
    )
    # 25 tickers, SPY first (continuity anchor from the
    # Phase 6I-52 pilot universe).
    assert table["ticker_count"] == 25
    row_tickers = [r["ticker"] for r in table["rows"]]
    assert row_tickers[0] == "SPY"
    # Spot-check a few more.
    assert "AAPL" in row_tickers
    assert "MCD" in row_tickers
    assert "BRK-B" in row_tickers


# ---------------------------------------------------------------------------
# 8. Static guard: no forbidden top-level imports.
# ---------------------------------------------------------------------------


_FORBIDDEN_TOP_LEVEL_IMPORTS = frozenset({
    "subprocess",
    "yfinance",
    "dash",
    "signal_engine_cache_refresher",
    "signal_library_stable_promotion_writer",
    "multiwindow_k_confluence_patch_writer",
    "confluence_pipeline_runner",
    "daily_board_automation_writer",
    "daily_board_automation_executor",
    "spymaster",
    "trafficflow",
    "stackbuilder",
    "onepass",
    "impactsearch",
    "confluence",
    "cross_ticker_confluence",
    "daily_signal_board",
})


def test_no_forbidden_top_level_imports():
    here = Path(__file__).resolve().parent.parent
    src = (
        here
        / "confluence_stackbuilder_pilot_preflight.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_level_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for n in node.names:
                top_level_names.add(
                    n.name.split(".")[0],
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_names.add(
                    node.module.split(".")[0],
                )
    leaked = (
        top_level_names & _FORBIDDEN_TOP_LEVEL_IMPORTS
    )
    assert not leaked, (
        f"Forbidden top-level imports in preflight: "
        f"{sorted(leaked)}"
    )


# ---------------------------------------------------------------------------
# 9. --output rejects production-root paths.
# ---------------------------------------------------------------------------


def test_output_path_guard_rejects_production_root_paths(
    tmp_path, capsys,
):
    forbidden_outputs = [
        "cache/results/preflight.json",
        "cache\\status\\preflight.json",
        "output/research_artifacts/preflight.json",
        "output/stackbuilder/preflight.json",
        "signal_library/data/stable/preflight.json",
    ]
    for forbidden in forbidden_outputs:
        rc = pf.main(["--output", forbidden])
        err = capsys.readouterr().err
        assert rc == 2
        assert "output_path_inside_production_root" in err


# ---------------------------------------------------------------------------
# 10. Production-state smoke: against the real on-disk
#     price_cache/daily (which does NOT exist today), every
#     Phase 6I-52 pilot ticker must classify as
#     skip-missing-cache. This is the actual Phase 6I-53
#     authorization gate.
# ---------------------------------------------------------------------------


def test_production_state_all_pilot_tickers_currently_skip():
    """The current production state has no
    ``price_cache/daily/`` directory; every Phase 6I-52
    pilot ticker must classify as skip. Phase 6I-53's
    supervised batch is therefore evidence-only until
    the cache is rebuilt."""
    # Use the real default resolution path (env var or
    # ``price_cache/daily``); do NOT inject env_overrides.
    table = pf.build_preflight_table()
    assert table["ticker_count"] == 25
    assert table["pass_count"] == 0
    assert table["skip_count"] == 25
    # Every row carries the SKIP status + the
    # would_fetch_yfinance flag.
    for row in table["rows"]:
        assert (
            row["preflight_status"]
            == pf.PREFLIGHT_STATUS_SKIP_MISSING_CACHE
        )
        assert row["would_fetch_yfinance"] is True
        assert row["resolved_cache_path"] is None
