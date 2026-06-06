"""Hermetic tests for broken_ticker_harvester.

All inputs are synthesized under tmp_path. No real registry.db, no real
cache/status, no real output/stackbuilder, no network, no engines.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import broken_ticker_harvester as bth  # noqa: E402


NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Builders for synthetic inputs
# ---------------------------------------------------------------------------


def _write_status(cache_dir: Path, ticker: str, payload: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{ticker}_status.json").write_text(
        json.dumps(payload), encoding="utf-8")


def _make_registry(db_path: Path, rows: list[dict]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "CREATE TABLE tickers (symbol TEXT, status TEXT, "
            "last_error_code TEXT, last_error_msg TEXT, invalidated_utc TEXT, "
            "last_verified_utc TEXT, stale_strikes INTEGER)"
        )
        for r in rows:
            con.execute(
                "INSERT INTO tickers (symbol,status,last_error_code,"
                "last_error_msg,invalidated_utc,last_verified_utc,stale_strikes)"
                " VALUES (?,?,?,?,?,?,?)",
                (r.get("symbol"), r.get("status"), r.get("last_error_code"),
                 r.get("last_error_msg"), r.get("invalidated_utc"),
                 r.get("last_verified_utc"), r.get("stale_strikes")),
            )
        con.commit()
    finally:
        con.close()


def _make_secondary(sb_root: Path, secondary: str, members: list[str]) -> None:
    run_dir = sb_root / secondary / ("seed_" + secondary)
    run_dir.mkdir(parents=True, exist_ok=True)
    # selected_run_dir is project-relative (project root == sb_root.parents[1]
    # == tmp_path), matching real selected_build.json on disk
    # (e.g. "output/stackbuilder/<SEC>/seed...").
    (sb_root / secondary / "selected_build.json").write_text(
        json.dumps({
            "schema_version": 1,
            "secondary": secondary,
            "selected_run_dir": run_dir.relative_to(sb_root.parents[1]).as_posix(),
        }), encoding="utf-8")
    (run_dir / "combo_k=6.json").write_text(
        json.dumps({"K": 6, "Members": members}), encoding="utf-8")


def _run(tmp_path: Path, *, recent_days: int = 45, now: datetime = NOW) -> dict:
    """Run harvest with project_root=tmp_path; all roots under tmp_path."""
    out = tmp_path / "out"
    return bth.harvest(
        output_dir=out,
        cache_status_dir=tmp_path / "cache" / "status",
        registry_db=tmp_path / "registry.db",
        stackbuilder_root=tmp_path / "output" / "stackbuilder",
        project_root=tmp_path,
        recent_days=recent_days,
        now=now,
    ), out


def _iso(now: datetime, days_ago: float) -> str:
    return (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Source A
# ---------------------------------------------------------------------------


def test_source_a_detects_failed(tmp_path):
    _write_status(tmp_path / "cache" / "status", "BFIN",
                  {"status": "failed", "message": "Insufficient trading history"})
    doc, _ = _run(tmp_path)
    rec = {r["ticker"]: r for r in doc["records"]}["BFIN"]
    assert rec["bucket"] == bth.CONFIDENT_BROKEN
    assert "cache_status" in rec["sources"]


def test_source_a_detects_stale(tmp_path):
    _write_status(tmp_path / "cache" / "status", "CTRA",
                  {"ticker": "CTRA", "status": "complete", "cache_status": "stale"})
    doc, _ = _run(tmp_path)
    rec = {r["ticker"]: r for r in doc["records"]}["CTRA"]
    # stale-only with no corroboration -> REVIEW
    assert rec["bucket"] == bth.REVIEW


def test_source_a_ignores_unknown(tmp_path):
    _write_status(tmp_path / "cache" / "status", "ZZZZ",
                  {"ticker": "ZZZZ", "status": "complete", "cache_status": "unknown"})
    doc, _ = _run(tmp_path)
    assert all(r["ticker"] != "ZZZZ" for r in doc["records"])


def test_source_a_ticker_from_filename_when_field_absent(tmp_path):
    _write_status(tmp_path / "cache" / "status", "FORD",
                  {"status": "failed", "message": "No data"})
    doc, _ = _run(tmp_path)
    assert "FORD" in {r["ticker"] for r in doc["records"]}


# ---------------------------------------------------------------------------
# Source B classification + windowing
# ---------------------------------------------------------------------------


def test_registry_classifies_dead_vs_transient(tmp_path):
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "DEADO", "status": "invalid", "last_error_code": "not_found",
         "last_error_msg": "no history", "invalidated_utc": _iso(NOW, 5)},
        {"symbol": "RLIM", "status": "invalid", "last_error_code": "rate_limit",
         "last_error_msg": "429", "invalidated_utc": _iso(NOW, 5)},
    ])
    doc, _ = _run(tmp_path)
    recs = {r["ticker"]: r for r in doc["records"]}
    assert recs["DEADO"]["bucket"] == bth.CONFIDENT_BROKEN  # dead + recent
    assert recs["RLIM"]["bucket"] == bth.REVIEW             # transient
    assert doc["counts"]["registry_class_counts"][bth.CLASS_DEAD] == 1
    assert doc["counts"]["registry_class_counts"][bth.CLASS_TRANSIENT] == 1


def test_recent_window_in_vs_out(tmp_path):
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "RECENT", "status": "invalid", "last_error_code": "not_found",
         "last_error_msg": "no history", "invalidated_utc": _iso(NOW, 5)},
        {"symbol": "OLD", "status": "invalid", "last_error_code": "not_found",
         "last_error_msg": "no history", "invalidated_utc": _iso(NOW, 400)},
    ])
    doc, _ = _run(tmp_path, recent_days=45)
    recs = {r["ticker"]: r for r in doc["records"]}
    assert recs["RECENT"]["bucket"] == bth.CONFIDENT_BROKEN
    assert recs["OLD"]["bucket"] == bth.REVIEW
    assert doc["counts"]["registry_windowed_total"] == 1


def test_registry_dead_outside_window_stays_review_unless_corroborated(tmp_path):
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "OLDDEAD", "status": "invalid", "last_error_code": "not_found",
         "last_error_msg": "no history", "invalidated_utc": _iso(NOW, 400)},
    ])
    doc, _ = _run(tmp_path)
    rec = {r["ticker"]: r for r in doc["records"]}["OLDDEAD"]
    assert rec["bucket"] == bth.REVIEW
    # now corroborate via Source A failed -> CONFIDENT
    _write_status(tmp_path / "cache" / "status", "OLDDEAD",
                  {"status": "failed", "message": "No data"})
    doc2, _ = _run(tmp_path)
    rec2 = {r["ticker"]: r for r in doc2["records"]}["OLDDEAD"]
    assert rec2["bucket"] == bth.CONFIDENT_BROKEN


def test_registry_dead_unparseable_invalidated_stays_review(tmp_path):
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "NODATE", "status": "invalid", "last_error_code": "not_found",
         "last_error_msg": "no history", "invalidated_utc": None},
        {"symbol": "BADDATE", "status": "invalid", "last_error_code": "expired",
         "last_error_msg": "expired", "invalidated_utc": "not-a-date"},
    ])
    doc, _ = _run(tmp_path)
    recs = {r["ticker"]: r for r in doc["records"]}
    assert recs["NODATE"]["bucket"] == bth.REVIEW
    assert recs["BADDATE"]["bucket"] == bth.REVIEW


def test_transient_stays_review(tmp_path):
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "TO", "status": "invalid", "last_error_code": "timeout",
         "last_error_msg": "timed out", "invalidated_utc": _iso(NOW, 1)},
    ])
    doc, _ = _run(tmp_path)
    rec = {r["ticker"]: r for r in doc["records"]}["TO"]
    assert rec["bucket"] == bth.REVIEW


def test_stale_status_only_stays_review(tmp_path):
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "STAL", "status": "stale", "last_error_code": None,
         "last_error_msg": None, "invalidated_utc": None, "stale_strikes": 1},
    ])
    doc, _ = _run(tmp_path)
    rec = {r["ticker"]: r for r in doc["records"]}["STAL"]
    assert rec["bucket"] == bth.REVIEW
    assert doc["counts"]["registry_class_counts"][bth.CLASS_STALE_STATUS] == 1


def test_stale_only_plus_registry_transient_stays_review(tmp_path):
    # TIGHTENED POLICY: a stale cache file + registry TRANSIENT must NOT be
    # promoted (no generic both-sources rule).
    _write_status(tmp_path / "cache" / "status", "DUO",
                  {"ticker": "DUO", "status": "complete", "cache_status": "stale"})
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "DUO", "status": "invalid", "last_error_code": "rate_limit",
         "last_error_msg": "429", "invalidated_utc": _iso(NOW, 1)},
    ])
    doc, _ = _run(tmp_path)
    rec = {r["ticker"]: r for r in doc["records"]}["DUO"]
    assert rec["bucket"] == bth.REVIEW
    assert set(rec["sources"]) == {"cache_status", "registry"}


def test_stale_only_plus_registry_stale_status_stays_review(tmp_path):
    _write_status(tmp_path / "cache" / "status", "SS",
                  {"ticker": "SS", "status": "complete", "cache_status": "stale"})
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "SS", "status": "stale", "last_error_code": None,
         "last_error_msg": None, "invalidated_utc": None, "stale_strikes": 2},
    ])
    doc, _ = _run(tmp_path)
    assert {r["ticker"]: r for r in doc["records"]}["SS"]["bucket"] == bth.REVIEW


def test_stale_only_plus_registry_unknown_class_stays_review(tmp_path):
    _write_status(tmp_path / "cache" / "status", "UC",
                  {"ticker": "UC", "status": "complete", "cache_status": "stale"})
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "UC", "status": "invalid", "last_error_code": "validation_failed",
         "last_error_msg": "schema", "invalidated_utc": _iso(NOW, 1)},
    ])
    doc, _ = _run(tmp_path)
    assert {r["ticker"]: r for r in doc["records"]}["UC"]["bucket"] == bth.REVIEW


def test_failed_plus_registry_transient_is_confident(tmp_path):
    # Source A failed is strong on its own, regardless of registry TRANSIENT.
    _write_status(tmp_path / "cache" / "status", "FT",
                  {"status": "failed", "message": "No data"})
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "FT", "status": "invalid", "last_error_code": "rate_limit",
         "last_error_msg": "429", "invalidated_utc": _iso(NOW, 1)},
    ])
    doc, _ = _run(tmp_path)
    assert {r["ticker"]: r for r in doc["records"]}["FT"]["bucket"] == bth.CONFIDENT_BROKEN


def test_stale_only_plus_registry_dead_outside_window_is_confident(tmp_path):
    # RULE 3 (explicit DEAD corroboration): a stale cache signal + a registry
    # DEAD record (even outside the recent window) is CONFIDENT_BROKEN -- NOT
    # via a generic both-sources rule, but because the registry side is DEAD.
    _write_status(tmp_path / "cache" / "status", "DC",
                  {"ticker": "DC", "status": "complete", "cache_status": "stale"})
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "DC", "status": "invalid", "last_error_code": "not_found",
         "last_error_msg": "no history", "invalidated_utc": _iso(NOW, 400)},
    ])
    doc, _ = _run(tmp_path)
    rec = {r["ticker"]: r for r in doc["records"]}["DC"]
    assert rec["bucket"] == bth.CONFIDENT_BROKEN
    assert "corroborates a registry DEAD record" in rec["reason"]


def test_registry_dead_recent_is_confident(tmp_path):
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "DR", "status": "invalid", "last_error_code": "no_price_data",
         "last_error_msg": "no price", "invalidated_utc": _iso(NOW, 3)},
    ])
    doc, _ = _run(tmp_path)
    assert {r["ticker"]: r for r in doc["records"]}["DR"]["bucket"] == bth.CONFIDENT_BROKEN


def test_registry_read_only_open(tmp_path):
    # The registry file must not be mutated; harvest opens it mode=ro.
    db = tmp_path / "registry.db"
    _make_registry(db, [
        {"symbol": "X", "status": "invalid", "last_error_code": "not_found",
         "last_error_msg": "no history", "invalidated_utc": _iso(NOW, 1)},
    ])
    before = db.read_bytes()
    _run(tmp_path)
    assert db.read_bytes() == before


# ---------------------------------------------------------------------------
# Affected-secondary derivation
# ---------------------------------------------------------------------------


def test_selected_build_to_combo_member_mapping(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    _make_secondary(sb_root, "SECA", ["AAA[D]", "BBB[I]", "CCC[D]",
                                      "DDD[I]", "EEE[D]", "FFF[I]"])
    _write_status(tmp_path / "cache" / "status", "CCC",
                  {"status": "failed", "message": "No data"})
    doc, out = _run(tmp_path)
    aff = json.loads((out / "affected_secondaries.json").read_text("utf-8"))
    assert "SECA" in aff["secondaries"]
    rec = aff["secondaries"]["SECA"]
    assert rec["rebuild_candidate"] is True
    assert any(o["ticker"] == "CCC" for o in rec["offending_members"])


def test_affected_flagged_when_member_confident(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    _make_secondary(sb_root, "SECB", ["AAA[D]", "BBB[I]", "GGG[D]",
                                      "DDD[I]", "EEE[D]", "FFF[I]"])
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "GGG", "status": "invalid", "last_error_code": "not_found",
         "last_error_msg": "no history", "invalidated_utc": _iso(NOW, 2)},
    ])
    doc, out = _run(tmp_path)
    aff = json.loads((out / "affected_secondaries.json").read_text("utf-8"))
    assert aff["secondaries"]["SECB"]["affected_bucket"] == bth.CONFIDENT_BROKEN


def test_review_member_not_in_blocked_txt_and_not_rebuild(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    _make_secondary(sb_root, "SECC", ["AAA[D]", "RLT[I]", "CCC[D]",
                                      "DDD[I]", "EEE[D]", "FFF[I]"])
    _make_registry(tmp_path / "registry.db", [
        {"symbol": "RLT", "status": "invalid", "last_error_code": "rate_limit",
         "last_error_msg": "429", "invalidated_utc": _iso(NOW, 1)},
    ])
    doc, out = _run(tmp_path)
    txt = (out / "proposed_blocked_tickers.txt").read_text("utf-8").split()
    assert "RLT" not in txt
    aff = json.loads((out / "affected_secondaries.json").read_text("utf-8"))
    assert aff["secondaries"]["SECC"]["rebuild_candidate"] is False
    assert aff["secondaries"]["SECC"]["affected_bucket"] == bth.REVIEW


def test_missing_selected_build_reported_not_clean(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    (sb_root / "EMPTYSEC").mkdir(parents=True, exist_ok=True)  # no selected_build
    doc, out = _run(tmp_path)
    diags = doc["diagnostics"]
    assert any(d.get("issue") == "missing_selected_build" for d in diags)


def test_missing_combo_reported_not_clean(tmp_path):
    sb_root = tmp_path / "output" / "stackbuilder"
    sec = sb_root / "SECD"
    run_dir = sec / "seed_SECD"
    run_dir.mkdir(parents=True, exist_ok=True)
    sec.joinpath("selected_build.json").write_text(json.dumps({
        "secondary": "SECD",
        "selected_run_dir": run_dir.relative_to(tmp_path).as_posix(),
    }), encoding="utf-8")
    # no combo_k=6.json written
    doc, out = _run(tmp_path)
    assert any(d.get("issue") == "missing_combo_k6" for d in doc["diagnostics"])


# ---------------------------------------------------------------------------
# Robustness + determinism + outputs
# ---------------------------------------------------------------------------


def test_malformed_cache_json_does_not_crash(tmp_path):
    cache = tmp_path / "cache" / "status"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "BAD_status.json").write_text("{not valid json", encoding="utf-8")
    _write_status(cache, "GOOD", {"status": "failed", "message": "No data"})
    doc, _ = _run(tmp_path)
    assert "GOOD" in {r["ticker"] for r in doc["records"]}
    assert any(d.get("issue") == "unreadable_or_invalid_json"
               for d in doc["diagnostics"])


def test_outputs_deterministic_sorted(tmp_path):
    cache = tmp_path / "cache" / "status"
    for t in ("ZULU", "ALFA", "MIKE"):
        _write_status(cache, t, {"status": "failed", "message": "No data"})
    doc, out = _run(tmp_path)
    txt = (out / "proposed_blocked_tickers.txt").read_text("utf-8").split()
    assert txt == sorted(txt) == ["ALFA", "MIKE", "ZULU"]
    j = json.loads((out / "proposed_blocked_tickers.json").read_text("utf-8"))
    tickers = [r["ticker"] for r in j["records"]]
    assert tickers == sorted(tickers)
    # sort_keys serialization is stable across two runs of the same inputs
    a = (out / "proposed_blocked_tickers.json").read_text("utf-8")
    out2 = tmp_path / "out2"
    bth.harvest(output_dir=out2, cache_status_dir=cache,
                registry_db=tmp_path / "registry.db",
                stackbuilder_root=tmp_path / "output" / "stackbuilder",
                project_root=tmp_path, recent_days=45, now=NOW)
    b = (out2 / "proposed_blocked_tickers.json").read_text("utf-8")
    assert a == b


def test_blocked_txt_contains_only_confident(tmp_path):
    cache = tmp_path / "cache" / "status"
    _write_status(cache, "FAILED1", {"status": "failed", "message": "No data"})
    _write_status(cache, "STALE1",
                  {"ticker": "STALE1", "status": "complete", "cache_status": "stale"})
    doc, out = _run(tmp_path)
    txt = (out / "proposed_blocked_tickers.txt").read_text("utf-8").split()
    assert txt == ["FAILED1"]


def test_missing_registry_and_cache_dirs_do_not_crash(tmp_path):
    # No registry.db, no cache/status, no stackbuilder root.
    doc, out = _run(tmp_path)
    assert doc["counts"]["confident_broken"] == 0
    assert (out / "SUMMARY.md").is_file()
    assert any(d.get("issue") == "registry_db_missing" for d in doc["diagnostics"])
    assert any(d.get("issue") == "cache_status_dir_missing"
               for d in doc["diagnostics"])


def test_no_absolute_paths_in_tracked_source():
    # Guard: the committed harvester must not embed machine paths. The
    # forbidden tokens are built from fragments so this test file itself
    # does not contain the literal machine-path substrings it screens for.
    bs = chr(92)
    bad_tokens = (
        "c:" + bs + "users",
        "c:" + "/" + "users",
        "/" + "users" + "/",
        "/" + "home" + "/",
        "app" + "data",
        "mini" + "conda",
        "spy" + "project2",
    )
    src = (PROJECT_ROOT / "broken_ticker_harvester.py").read_text("utf-8").lower()
    for bad in bad_tokens:
        assert bad not in src, "machine path token in source"


def test_classify_helpers_direct():
    assert bth._classify_registry_row("invalid", "not_found", "no history") == bth.CLASS_DEAD
    assert bth._classify_registry_row("invalid", "rate_limit", "429") == bth.CLASS_TRANSIENT
    assert bth._classify_registry_row("stale", None, None) == bth.CLASS_STALE_STATUS
    assert bth._classify_registry_row("invalid", "validation_failed", "?") == bth.CLASS_UNKNOWN
    assert bth.strip_member_protocol("KSB.DE[I]") == "KSB.DE"
    assert bth.strip_member_protocol("600509.SS[D]") == "600509.SS"
    assert bth.normalize_ticker(" aapl ") == "AAPL"
