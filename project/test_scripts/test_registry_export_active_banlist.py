"""Phase V8-operational-master-handoff tests for the
``global_ticker_library.registry.export_active`` ban-list
guardrail.

The guardrail (added 2026-05-15) filters any symbol listed
in a JSON ban-list out of the ``master_tickers.txt``
write so operator-removed tickers (``master - V8``) cannot
be silently reintroduced by future scraper / batch /
dashboard runs.

Tests use ``tmp_path`` exclusively -- they do NOT depend
on the real ``global_ticker_library/data/`` files. A
synthetic in-memory-style SQLite registry is constructed
per-test so the guardrail's behavior is verifiable
without driving OnePass / yfinance / dashboards /
production data.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


from global_ticker_library import registry  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic registry fixture
# ---------------------------------------------------------------------------


def _seed_db(
    db_path: Path,
    rows: list[tuple[str, str]],
) -> None:
    """Create a minimal tickers table and insert ``rows`` of
    ``(symbol, status)``. Matches the SCHEMA columns the
    real registry uses, but only the two we care about.
    """
    con = sqlite3.connect(db_path)
    try:
        con.executescript(registry.SCHEMA)
        con.executemany(
            "INSERT INTO tickers (symbol, status) VALUES (?, ?)",
            rows,
        )
        con.commit()
    finally:
        con.close()


def _read_master_file(p: Path) -> list[str]:
    text = p.read_text(encoding="utf-8")
    return [s.strip().upper() for s in text.split(",") if s.strip()]


def _write_banlist(
    p: Path,
    banned: list[str],
    *,
    schema_version: str = (
        "v8_removed_from_master_banlist_v1"
    ),
    extra: dict | None = None,
) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": schema_version,
        "banned_removed_tickers": banned,
    }
    if extra:
        payload.update(extra)
    p.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. Backwards compatibility: no ban-list -> normal export
# ---------------------------------------------------------------------------


def test_export_active_without_banlist_exports_all_active(
    tmp_path,
):
    db = tmp_path / "registry.db"
    master = tmp_path / "master_tickers.txt"
    banlist = tmp_path / "absent_banlist.json"  # does not exist

    _seed_db(
        db,
        [
            ("AAPL", "active"),
            ("MSFT", "active"),
            ("SOMECRYPTO-USD", "active"),
            ("STALEONE", "stale"),
            ("INVONE", "invalid"),
            ("CAND1", "candidate"),
        ],
    )

    rc = registry.export_active(
        master_path=master,
        db_path=db,
        banlist_path=banlist,
    )
    assert rc == 3
    assert _read_master_file(master) == [
        "AAPL", "MSFT", "SOMECRYPTO-USD",
    ]


def test_export_active_banlist_none_param_exports_all(
    tmp_path,
):
    """Explicit ``banlist_path=None`` is also backwards-
    compatible."""
    db = tmp_path / "registry.db"
    master = tmp_path / "master_tickers.txt"
    _seed_db(db, [("AAPL", "active"), ("MSFT", "active")])
    rc = registry.export_active(
        master_path=master,
        db_path=db,
        banlist_path=None,
    )
    assert rc == 2
    assert _read_master_file(master) == ["AAPL", "MSFT"]


# ---------------------------------------------------------------------------
# 2. Ban-list excludes matching active tickers from the export file
# ---------------------------------------------------------------------------


def test_export_active_banlist_filters_banned_active_symbols(
    tmp_path,
):
    db = tmp_path / "registry.db"
    master = tmp_path / "master_tickers.txt"
    banlist = tmp_path / "curation" / "banlist.json"

    _seed_db(
        db,
        [
            ("AAPL", "active"),
            ("MSFT", "active"),
            ("00-USD", "active"),         # banned
            ("^VIX", "active"),            # banned
            ("^SPX", "active"),            # banned
            ("STALEONE", "stale"),
            ("CAND1", "candidate"),
        ],
    )
    _write_banlist(
        banlist, ["00-USD", "^VIX", "^SPX"],
    )

    rc = registry.export_active(
        master_path=master,
        db_path=db,
        banlist_path=banlist,
    )
    assert rc == 2  # only AAPL + MSFT survived
    exported = _read_master_file(master)
    assert exported == ["AAPL", "MSFT"]
    # Sample banned tickers absent from the exported file.
    assert "00-USD" not in exported
    assert "^VIX" not in exported
    assert "^SPX" not in exported


def test_export_active_banlist_case_insensitive_match(
    tmp_path,
):
    """Ban-list matching is case-insensitive (upper-
    folded) so a DB row that disagrees with the ban-list
    on case still gets filtered."""
    db = tmp_path / "registry.db"
    master = tmp_path / "master_tickers.txt"
    banlist = tmp_path / "banlist.json"
    _seed_db(
        db,
        [
            ("aapl", "active"),    # lowercase in DB
            ("vix", "active"),     # lowercase, ban-list has "VIX"
            ("MSFT", "active"),
        ],
    )
    _write_banlist(banlist, ["VIX"])
    rc = registry.export_active(
        master_path=master,
        db_path=db,
        banlist_path=banlist,
    )
    assert rc == 2
    # The case the DB used is preserved for the rows that
    # survive the filter -- only the banned row is dropped.
    exported = _read_master_file(master)
    assert "VIX" not in exported
    assert "AAPL" in exported
    assert "MSFT" in exported


# ---------------------------------------------------------------------------
# 3. Ban-list does NOT mutate registry status
# ---------------------------------------------------------------------------


def test_export_active_banlist_does_not_mutate_registry_status(
    tmp_path,
):
    db = tmp_path / "registry.db"
    master = tmp_path / "master_tickers.txt"
    banlist = tmp_path / "banlist.json"
    rows = [
        ("AAPL", "active"),
        ("00-USD", "active"),
        ("^VIX", "active"),
        ("STALEONE", "stale"),
        ("INVONE", "invalid"),
    ]
    _seed_db(db, rows)
    _write_banlist(banlist, ["00-USD", "^VIX"])
    registry.export_active(
        master_path=master,
        db_path=db,
        banlist_path=banlist,
    )
    # Re-query: every row's status is exactly what we
    # inserted -- the guardrail filters the export file
    # only, never the DB.
    con = sqlite3.connect(db)
    try:
        cur = con.execute(
            "SELECT symbol, status FROM tickers "
            "ORDER BY symbol COLLATE NOCASE"
        )
        result = sorted([(s, st) for s, st in cur.fetchall()])
    finally:
        con.close()
    expected = sorted(rows)
    assert result == expected


# ---------------------------------------------------------------------------
# 4. Missing / malformed ban-list behavior
# ---------------------------------------------------------------------------


def test_helper_load_master_export_banlist_missing_file_returns_empty(
    tmp_path,
):
    banlist = tmp_path / "does_not_exist.json"
    assert (
        registry._load_master_export_banlist(banlist)
        == set()
    )


def test_helper_load_master_export_banlist_none_returns_empty():
    assert (
        registry._load_master_export_banlist(None)
        == set()
    )


def test_helper_load_master_export_banlist_returns_upper_set(
    tmp_path,
):
    banlist = tmp_path / "banlist.json"
    _write_banlist(
        banlist, ["aapl", "MSFT", "  spy  ", ""],
    )
    out = registry._load_master_export_banlist(banlist)
    assert out == {"AAPL", "MSFT", "SPY"}


def test_helper_load_master_export_banlist_rejects_unknown_schema(
    tmp_path,
):
    banlist = tmp_path / "banlist.json"
    _write_banlist(
        banlist,
        ["AAPL"],
        schema_version="some_other_schema_v1",
    )
    with pytest.raises(ValueError):
        registry._load_master_export_banlist(banlist)


def test_helper_load_master_export_banlist_rejects_missing_list(
    tmp_path,
):
    banlist = tmp_path / "banlist.json"
    banlist.write_text(
        json.dumps(
            {
                "schema_version": (
                    "v8_removed_from_master_banlist_v1"
                ),
                # no banned_removed_tickers key
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        registry._load_master_export_banlist(banlist)


def test_helper_load_master_export_banlist_rejects_non_object_root(
    tmp_path,
):
    banlist = tmp_path / "banlist.json"
    banlist.write_text(
        json.dumps(["AAPL", "MSFT"]), encoding="utf-8",
    )
    with pytest.raises(ValueError):
        registry._load_master_export_banlist(banlist)


def test_export_active_with_malformed_banlist_raises(
    tmp_path,
):
    """Malformed ban-list (bad schema) raises rather than
    silently exporting banned symbols. This is the
    documented fail-safe behavior."""
    db = tmp_path / "registry.db"
    master = tmp_path / "master_tickers.txt"
    banlist = tmp_path / "banlist.json"
    _seed_db(db, [("AAPL", "active")])
    _write_banlist(
        banlist,
        ["AAPL"],
        schema_version="wrong_schema_v1",
    )
    with pytest.raises(ValueError):
        registry.export_active(
            master_path=master,
            db_path=db,
            banlist_path=banlist,
        )
    # Critical: the export file was NOT created because
    # the ban-list load raised BEFORE the write.
    assert not master.exists()


# ---------------------------------------------------------------------------
# 5. Real production ban-list schema integration check
# ---------------------------------------------------------------------------


def test_production_banlist_path_loads_when_present(
    tmp_path,
):
    """If the real ban-list file is staged in the
    worktree, the helper loads it and reports a non-empty
    set with the documented 36,395 entries. Skips
    cleanly when the file is absent (cacheless Codex
    worktree)."""
    here = Path(__file__).resolve().parent.parent
    banlist = (
        here
        / "global_ticker_library"
        / "curation"
        / "v8_removed_from_master_banlist.json"
    )
    if not banlist.exists():
        pytest.skip(
            "v8_removed_from_master_banlist.json absent "
            "in this worktree; production smoke skipped."
        )
    banned = registry._load_master_export_banlist(banlist)
    assert isinstance(banned, set)
    assert len(banned) > 0
    # Sample banned symbols from the doc.
    for sample in ("00-USD", "^VIX", "^SPX"):
        assert sample.upper() in banned


# ---------------------------------------------------------------------------
# 6. Export file format pinning
# ---------------------------------------------------------------------------


def test_export_active_writes_comma_separated_no_trailing_newline(
    tmp_path,
):
    """master_tickers.txt format: comma-separated, no
    trailing newline (matches the existing repo
    convention preserved through this phase)."""
    db = tmp_path / "registry.db"
    master = tmp_path / "master_tickers.txt"
    _seed_db(
        db,
        [
            ("AAPL", "active"),
            ("MSFT", "active"),
            ("ZZZ", "active"),
        ],
    )
    rc = registry.export_active(
        master_path=master,
        db_path=db,
        banlist_path=None,
    )
    assert rc == 3
    raw = master.read_text(encoding="utf-8")
    # COLLATE NOCASE ordering: AAPL, MSFT, ZZZ.
    assert raw == "AAPL,MSFT,ZZZ"
    assert not raw.endswith("\n")
