"""Hermetic tests for fresh_ccc_blob_upload.

No network, no real Blob, no promote CLI, no engines, no validation, no
frontend/public writes, no operational output/ writes. A fake Blob client
(implementing put/get) exercises the REAL promote helpers
(extract_ccc_to_blob_sidecars -> put_and_verify_sidecar) without network. All
paths under tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fresh_ccc_blob_upload as fcu  # noqa: E402

RID = "20260609T081349Z"


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


def _point(date, *, extra=None, ohlcv=False):
    p = {
        "date_utc": date,
        "cumulative_capture_pct": 1.5,
        "per_bar_capture_pct": 0.25,
        "trade_direction": "BUY",
    }
    if extra:
        p.update(extra)
    if ohlcv:
        p["close"] = 123.45
    return p


def _series(n):
    return [_point(f"2020-01-{i + 1:02d}") for i in range(n)]


def _row(sec, *, points=3, ccc=None, abs_paths=True):
    row = {
        "secondary": sec,
        "status": "ranked",
        "ccc_series": _series(points) if ccc is None else ccc,
    }
    if abs_paths:
        # Absolute-looking path FIELDS (the tool drops these keys entirely;
        # the guard only checks they never reach the upload payload). A
        # non-drive-letter base keeps the test source free of machine paths.
        base = "ABS/output"
        row["history_artifact_path"] = f"{base}/k6_mtf/{RID}/{sec}/k6_mtf_history.json"
        row["k6_stack"] = {
            "selected_build_path": f"{base}/stackbuilder/{sec}/selected_build.json",
            "selected_run_dir": f"{base}/stackbuilder/{sec}/seed_{sec}",
            "combo_k6_path": f"{base}/stackbuilder/{sec}/seed_{sec}/combo_k=6.json",
        }
    return row


def _ranking_file(tmp_path, rows, *, run_id=RID, schema="k6_mtf_ranking_v1"):
    p = tmp_path / "k6_mtf_ranking.json"
    payload = {"schema_version": schema, "run_id": run_id, "per_secondary": rows}
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


class FakeBlobClient:
    """Implements the put/get contract promote.put_and_verify_sidecar needs.
    No network: stores bytes in-memory and returns an allowlisted public URL."""

    def __init__(self, *, tamper_get=False):
        self.put_calls = []
        self.get_calls = []
        self._store = {}
        self._tamper = tamper_get

    def put(self, pathname, data, *, overwrite=False):
        self.put_calls.append((pathname, bytes(data), overwrite))
        url = f"https://abc123.public.blob.vercel-storage.com/{pathname}"
        self._store[url] = bytes(data)
        return {"url": url, "reused": False}

    def get(self, url):
        self.get_calls.append(url)
        data = self._store.get(url, b"")
        return data + b"tampered" if self._tamper else data


def _out(tmp_path):
    return tmp_path / "out" / "fresh_ccc_records.json"


# ---------------------------------------------------------------------------
# 1. PASS: upload two requested rows, write records file
# ---------------------------------------------------------------------------


def test_pass_uploads_two_rows_and_writes_records(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI", points=5), _row("SCHG", points=4)])
    fake = FakeBlobClient()
    out = _out(tmp_path)
    res = fcu.upload_fresh_ccc(
        k6_ranking_path=rk, secondaries="IHI,SCHG", output_path=out,
        confirm_blob_upload=True, client=fake)
    assert res["status"] == "uploaded" and res["record_count"] == 2
    assert res["blob_client_constructed"] is False
    assert out.is_file()
    records = json.loads(out.read_text("utf-8"))
    assert isinstance(records, list) and len(records) == 2  # bare list
    assert {r["secondary"] for r in records} == {"IHI", "SCHG"}
    for r in records:
        assert set(fcu.RECORD_FIELDS).issubset(r.keys())
        assert r["get_verified"] is True
        assert r["pathname"].startswith(f"k6-mtf/{RID}/ccc-series/")
        # no raw ccc_series payload or path fields leaked into the records file
        assert "ccc_series" not in r
        assert "history_artifact_path" not in r and "k6_stack" not in r
    # uploaded sidecar bytes carry NO raw path fields (Guard 13)
    for _pathname, data, _ow in fake.put_calls:
        assert b"history_artifact_path" not in data
        assert b"k6_stack" not in data
    assert len(fake.put_calls) == 2 and len(fake.get_calls) == 2
    # records file shape is a bare non-empty list (the shape
    # crunch_rebuild_orchestrator._load_fresh_ccc_records accepts).
    assert all(isinstance(r, dict) for r in records)


# ---------------------------------------------------------------------------
# 2. PASS: optional fresh-only verification manifest only when requested
# ---------------------------------------------------------------------------


def test_manifest_only_when_requested(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI"), _row("SCHG")])
    out = _out(tmp_path)
    # default: no manifest
    res = fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                              output_path=out, confirm_blob_upload=True,
                              client=FakeBlobClient())
    assert res["verification_manifest_path"] is None

    mpath = tmp_path / "out" / "fresh_ccc_verification.json"
    out2 = tmp_path / "out2" / "fresh_ccc_records.json"
    res2 = fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                               output_path=out2, confirm_blob_upload=True,
                               write_verification_manifest=mpath,
                               client=FakeBlobClient())
    assert res2["verification_manifest_path"] == mpath.as_posix()
    man = json.loads(mpath.read_text("utf-8"))
    assert man["schema_version"] == "k6_mtf_ccc_sidecar_verification_v1"
    assert man["ranking_run_id"] == RID
    assert man["sidecar_count"] == 2
    assert {r["secondary"] for r in man["records"]} == {"IHI", "SCHG"}


# ---------------------------------------------------------------------------
# 3. VALIDATE-ONLY: no confirm -> no client, no PUT/GET, no records file
# ---------------------------------------------------------------------------


def test_validate_only_does_not_touch_blob(tmp_path, monkeypatch):
    rk = _ranking_file(tmp_path, [_row("IHI"), _row("SCHG")])
    out = _out(tmp_path)
    calls = {"n": 0}
    monkeypatch.setattr(fcu, "_make_default_client",
                        lambda: calls.__setitem__("n", calls["n"] + 1))
    fake = FakeBlobClient()
    res = fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                              output_path=out, confirm_blob_upload=False,
                              client=fake)
    assert res["status"] == "validate_only"
    assert res["blob_client_constructed"] is False
    assert res["would_upload_count"] == 2
    assert calls["n"] == 0           # default client never constructed
    assert fake.put_calls == [] and fake.get_calls == []
    assert not out.exists()          # no records file written


# ---------------------------------------------------------------------------
# 4-8. STOP cases
# ---------------------------------------------------------------------------


def test_stop_requested_secondary_missing(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI")])
    with pytest.raises(fcu.FreshCccError):
        fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                             output_path=_out(tmp_path),
                             confirm_blob_upload=True, client=FakeBlobClient())


def test_stop_duplicate_secondary_in_ranking(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI"), _row("IHI"), _row("SCHG")])
    with pytest.raises(fcu.FreshCccError):
        fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                             output_path=_out(tmp_path),
                             confirm_blob_upload=True, client=FakeBlobClient())


def test_stop_empty_ccc_series(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI", ccc=[]), _row("SCHG")])
    with pytest.raises(fcu.FreshCccError):
        fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                             output_path=_out(tmp_path),
                             confirm_blob_upload=True, client=FakeBlobClient())


@pytest.mark.parametrize("bad_ccc", [
    [{"date_utc": "2020-01-01", "cumulative_capture_pct": 1.0,
      "per_bar_capture_pct": 0.1, "trade_direction": "BUY", "close": 9.9}],  # OHLCV
    [{"date_utc": "2020-01-01", "cumulative_capture_pct": 1.0,
      "per_bar_capture_pct": 0.1, "trade_direction": "BUY", "extra": 1}],     # extra
])
def test_stop_modeb_violation_propagates(tmp_path, bad_ccc):
    rk = _ranking_file(tmp_path, [_row("IHI", ccc=bad_ccc), _row("SCHG")])
    fake = FakeBlobClient()
    # build_ccc_sidecar's Mode-B validation raises PromotionError inside extract
    with pytest.raises(Exception):
        fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                             output_path=_out(tmp_path),
                             confirm_blob_upload=True, client=fake)


def test_stop_get_verify_sha_mismatch(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI"), _row("SCHG")])
    fake = FakeBlobClient(tamper_get=True)
    with pytest.raises(Exception):  # BlobClientError from put_and_verify_sidecar
        fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                             output_path=_out(tmp_path),
                             confirm_blob_upload=True, client=fake)
    assert not _out(tmp_path).exists()


# ---------------------------------------------------------------------------
# 9-10. STOP: frontend/public output / manifest refused
# ---------------------------------------------------------------------------


def test_stop_output_under_frontend_public(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI"), _row("SCHG")])
    bad = tmp_path / "frontend" / "public" / "fixtures" / "fresh.json"
    fake = FakeBlobClient()
    with pytest.raises(fcu.FreshCccError):
        fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                             output_path=bad, confirm_blob_upload=True,
                             client=fake)
    assert fake.put_calls == []  # refused before any upload


def test_stop_manifest_under_frontend_public(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI"), _row("SCHG")])
    badman = tmp_path / "frontend" / "public" / "fixtures" / "man.json"
    fake = FakeBlobClient()
    with pytest.raises(fcu.FreshCccError):
        fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                             output_path=_out(tmp_path),
                             write_verification_manifest=badman,
                             confirm_blob_upload=True, client=fake)
    assert fake.put_calls == []


# ---------------------------------------------------------------------------
# 11. STOP: emitted records postcondition (direct)
# ---------------------------------------------------------------------------


def _rec(sec):
    return {"secondary": sec, "pathname": f"k6-mtf/{RID}/ccc-series/{sec}.x.json",
            "url": "https://a.public.blob.vercel-storage.com/x", "sha256": "0" * 64,
            "byte_size": 10, "points": 3, "first_date": "2020-01-01",
            "last_date": "2020-01-03", "reused": False, "get_verified": True}


def test_assert_records_missing_extra_duplicate_and_get_verified(tmp_path):
    # missing
    with pytest.raises(fcu.FreshCccError):
        fcu._assert_records([_rec("IHI")], ["IHI", "SCHG"])
    # extra
    with pytest.raises(fcu.FreshCccError):
        fcu._assert_records([_rec("IHI"), _rec("SCHG"), _rec("AAPB")],
                            ["IHI", "SCHG"])
    # duplicate
    with pytest.raises(fcu.FreshCccError):
        fcu._assert_records([_rec("IHI"), _rec("IHI")], ["IHI", "SCHG"])
    # get_verified not True
    bad = _rec("IHI"); bad["get_verified"] = False
    with pytest.raises(fcu.FreshCccError):
        fcu._assert_records([bad, _rec("SCHG")], ["IHI", "SCHG"])
    # valid
    fcu._assert_records([_rec("IHI"), _rec("SCHG")], ["IHI", "SCHG"])


# ---------------------------------------------------------------------------
# 12. Records file shape accepted by the orchestrator loader (lightweight)
# ---------------------------------------------------------------------------


def test_records_file_shape_is_bare_nonempty_list(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI"), _row("SCHG")])
    out = _out(tmp_path)
    fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                         output_path=out, confirm_blob_upload=True,
                         client=FakeBlobClient())
    data = json.loads(out.read_text("utf-8"))
    # _load_fresh_ccc_records accepts a bare list OR {records:[...]}; a
    # bare non-empty list of dicts is the shape it consumes. (Importing the
    # orchestrator loader requires constructing a full CrunchOrchestrator;
    # the shape is validated here directly.)
    assert isinstance(data, list) and data and all(isinstance(d, dict) for d in data)


# ---------------------------------------------------------------------------
# 14. Artifact row count does not drive upload (requested set drives it)
# ---------------------------------------------------------------------------


def test_only_requested_secondaries_uploaded(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI"), _row("SCHG"), _row("AAPB"),
                                  _row("BBB")])
    fake = FakeBlobClient()
    out = _out(tmp_path)
    res = fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                              output_path=out, confirm_blob_upload=True,
                              client=fake)
    assert res["record_count"] == 2
    assert res["secondaries_emitted"] == ["IHI", "SCHG"]
    uploaded_secs = {pn.split("/")[3].split(".")[0] for pn, _d, _o in fake.put_calls}
    assert uploaded_secs == {"IHI", "SCHG"}  # AAPB/BBB never uploaded
    records = json.loads(out.read_text("utf-8"))
    assert {r["secondary"] for r in records} == {"IHI", "SCHG"}


# ---------------------------------------------------------------------------
# Input-validation extras + CLI surface
# ---------------------------------------------------------------------------


def test_stop_ranking_run_id_mismatch(tmp_path):
    rk = _ranking_file(tmp_path, [_row("IHI"), _row("SCHG")])
    with pytest.raises(fcu.FreshCccError):
        fcu.upload_fresh_ccc(k6_ranking_path=rk, secondaries="IHI,SCHG",
                             output_path=_out(tmp_path), ranking_run_id="OTHER",
                             confirm_blob_upload=True, client=FakeBlobClient())


def test_stop_wrong_schema_and_missing_file(tmp_path):
    bad_schema = _ranking_file(tmp_path, [_row("IHI")], schema="k6_mtf_ranking_v2")
    with pytest.raises(fcu.FreshCccError):
        fcu.upload_fresh_ccc(k6_ranking_path=bad_schema, secondaries="IHI",
                             output_path=_out(tmp_path),
                             confirm_blob_upload=False)
    with pytest.raises(fcu.FreshCccError):
        fcu.upload_fresh_ccc(k6_ranking_path=tmp_path / "nope.json",
                             secondaries="IHI", output_path=_out(tmp_path),
                             confirm_blob_upload=False)


def test_cli_validate_only_returns_zero(tmp_path, capsys):
    rk = _ranking_file(tmp_path, [_row("IHI"), _row("SCHG")])
    out = _out(tmp_path)
    rc = fcu.main(["--k6-ranking", str(rk), "--secondaries", "IHI,SCHG",
                   "--output", str(out)])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "validate_only"
    assert not out.exists()


def test_cli_bad_input_returns_nonzero(tmp_path, capsys):
    rk = _ranking_file(tmp_path, [_row("IHI")])
    rc = fcu.main(["--k6-ranking", str(rk), "--secondaries", "IHI,SCHG",
                   "--output", str(_out(tmp_path))])
    assert rc == 2  # FreshCccError -> refused
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_no_absolute_paths_in_tracked_source():
    bs = chr(92)
    bad = ("c:" + bs + "users", "c:" + "/" + "users", "/" + "users" + "/",
           "/" + "home" + "/", "app" + "data", "mini" + "conda",
           "spy" + "project2")
    src = (PROJECT_ROOT / "fresh_ccc_blob_upload.py").read_text("utf-8").lower()
    for b in bad:
        assert b not in src, "machine path token in source"
