"""Standalone fresh-CCC Blob upload tool.

Produces the verified fresh CCC records file consumed by the crunch
orchestrator's ``--publish-dry-run-fresh-ccc-records-file``. This is the
explicit, operator-gated Blob-boundary utility for FRESHLY built K6 MTF rows
(e.g. a 2-secondary supervised test set). It uploads ONLY the CCC series
sidecars of the explicitly requested secondaries, GET-verifies them, and
writes the records file.

It reuses promote's existing CCC Blob helpers UNCHANGED:

  * ``VercelBlobClient``                 -- the Blob client boundary
  * ``extract_ccc_to_blob_sidecars``     -- per-row PUT + GET-verify + records
  * ``build_ccc_verification_manifest``  -- optional fresh-only audit manifest

It NEVER promotes, writes a public fixture, commits, pushes, deploys, or
touches carried prior-board sidecars. Carried rows are not re-uploaded or
re-GET-verified -- the publish dry-run / combine assemble the full-board CCC
manifest later from the prior verification manifest plus these fresh records.

Privacy: the tool builds a MINIMAL fresh-only v2 payload carrying only
``secondary`` and ``ccc_series`` per row. Raw K6 ranking rows may contain
absolute local path fields; those are deliberately NOT carried into the
upload payload (CCC extraction ignores them anyway). The Blob token is read
lazily by ``VercelBlobClient`` at PUT time only (env var name
``BLOB_READ_WRITE_TOKEN``); this tool never reads, logs, or prints its value.

ASCII-only. No side effects on import. CLI guarded by ``__main__``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

RANKING_SCHEMA_V1 = "k6_mtf_ranking_v1"
V2_SCHEMA = "k6_mtf_ranking_v2"
# The 10 fields every emitted record must carry (matches promote's
# extract_ccc_to_blob_sidecars output and combine's record contract).
RECORD_FIELDS = (
    "secondary", "pathname", "url", "sha256", "byte_size", "points",
    "first_date", "last_date", "reused", "get_verified",
)


class FreshCccError(Exception):
    """Fail-closed refusal raised by the fresh-CCC upload tool."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm(ticker: Any) -> str:
    return str(ticker).strip().upper()


def _load_ranking(path: Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise FreshCccError(f"k6 ranking file not found: {p.as_posix()}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FreshCccError(
            f"k6 ranking unreadable/invalid JSON: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise FreshCccError("k6 ranking root is not a JSON object")
    if data.get("schema_version") != RANKING_SCHEMA_V1:
        raise FreshCccError(
            "k6 ranking schema_version must be "
            f"{RANKING_SCHEMA_V1!r}; got {data.get('schema_version')!r}")
    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise FreshCccError("k6 ranking is missing a string run_id")
    rows = data.get("per_secondary")
    if not isinstance(rows, list) or not rows:
        raise FreshCccError("k6 ranking per_secondary must be a non-empty list")
    return data


def _parse_secondaries(csv: str) -> list:
    if not isinstance(csv, str):
        raise FreshCccError("--secondaries must be a comma-separated string")
    out: list = []
    seen: set = set()
    for tok in csv.split(","):
        s = _norm(tok)
        if not s:
            continue
        if s in seen:
            raise FreshCccError(f"duplicate requested secondary: {s!r}")
        seen.add(s)
        out.append(s)
    if not out:
        raise FreshCccError("--secondaries is empty after parsing")
    return out


def _select_rows(ranking: dict, requested: Sequence[str]) -> list:
    """Index ranking rows by normalized secondary (duplicate -> STOP), then
    select exactly the requested secondaries (missing -> STOP). Returns
    minimal fresh-only rows: only secondary + non-empty ccc_series."""
    by_sec: dict = {}
    for r in ranking.get("per_secondary") or []:
        if not isinstance(r, dict):
            raise FreshCccError("ranking per_secondary entry is not an object")
        sec = _norm(r.get("secondary"))
        if not sec:
            raise FreshCccError("ranking per_secondary entry has no secondary")
        if sec in by_sec:
            raise FreshCccError(f"duplicate secondary in ranking: {sec!r}")
        by_sec[sec] = r
    selected: list = []
    for sec in requested:
        row = by_sec.get(sec)
        if row is None:
            raise FreshCccError(
                f"requested secondary not found in ranking: {sec!r}")
        series = row.get("ccc_series")
        if not isinstance(series, list) or not series:
            raise FreshCccError(
                f"secondary {sec!r} has empty/missing ccc_series")
        # Minimal, path-free fresh row (Mode-B point validation is enforced
        # by build_ccc_sidecar inside extract_ccc_to_blob_sidecars).
        selected.append({"secondary": sec, "ccc_series": series})
    return selected


def _build_fresh_payload(run_id: str, fresh_rows: list) -> dict:
    return {
        "schema_version": V2_SCHEMA,
        "run_id": run_id,
        "per_secondary": fresh_rows,
    }


def _refuse_frontend_public(path: Any, label: str) -> None:
    p = Path(path)
    candidates = [p]
    try:
        candidates.append(p.resolve())
    except OSError:
        pass
    for c in candidates:
        parts = [str(x).lower() for x in c.parts]
        for i in range(len(parts) - 1):
            if parts[i] == "frontend" and parts[i + 1] == "public":
                raise FreshCccError(
                    f"{label} must not be under frontend/public: "
                    f"{Path(path).as_posix()}")


def _assert_records(records: Any, requested: Sequence[str]) -> None:
    if not isinstance(records, list) or not records:
        raise FreshCccError("extraction returned no records")
    want = list(requested)
    seen: list = []
    for rec in records:
        if not isinstance(rec, dict):
            raise FreshCccError("emitted record is not an object")
        for f in RECORD_FIELDS:
            if f not in rec:
                raise FreshCccError(f"emitted record missing field {f!r}")
        if rec.get("get_verified") is not True:
            raise FreshCccError(
                f"emitted record get_verified must be true for "
                f"{rec.get('secondary')!r}")
        sec = _norm(rec.get("secondary"))
        if sec not in want:
            raise FreshCccError(
                f"emitted record for non-requested secondary: {sec!r}")
        if sec in seen:
            raise FreshCccError(f"duplicate emitted secondary: {sec!r}")
        seen.append(sec)
    if set(seen) != set(want):
        missing = sorted(set(want) - set(seen))
        extra = sorted(set(seen) - set(want))
        raise FreshCccError(
            f"emitted records do not match requested set; missing {missing!r}, "
            f"extra {extra!r}")


def _atomic_write_json(path: Path, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".part",
                              dir=str(p.parent))
    os.close(fd)
    tp = Path(tmp)
    try:
        tp.write_bytes(data)
        os.replace(str(tp), str(p))
    finally:
        try:
            if tp.exists():
                tp.unlink()
        except OSError:
            pass


def _make_default_client():
    """Construct the real Blob client. Imported lazily so importing this tool
    has no side effects and does not pull promote unless an upload runs."""
    from utils.react_publish.promote_k6_mtf_artifact import (  # noqa: PLC0415
        VercelBlobClient)
    return VercelBlobClient()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def upload_fresh_ccc(
    *,
    k6_ranking_path: Any,
    secondaries: Any,
    output_path: Any,
    confirm_blob_upload: bool,
    ranking_run_id: str | None = None,
    write_verification_manifest: Any = None,
    client: Any = None,
) -> dict:
    """Validate inputs, build a minimal fresh-only v2 payload, and (only when
    ``confirm_blob_upload`` is True) upload + GET-verify the requested
    secondaries' CCC sidecars and write the records file. Without confirmation
    NO Blob client is constructed and NO PUT/GET occurs -- it returns a
    would-upload plan. Fail-closed; never promotes/commits/pushes/deploys."""
    requested = (_parse_secondaries(secondaries)
                 if isinstance(secondaries, str) else
                 _parse_secondaries(",".join(str(s) for s in secondaries)))
    ranking = _load_ranking(Path(k6_ranking_path))
    run_id = ranking["run_id"]
    if ranking_run_id is not None and str(ranking_run_id) != run_id:
        raise FreshCccError(
            "--ranking-run-id does not match the artifact run_id: "
            f"{ranking_run_id!r} != {run_id!r}")

    # Output-path safety (checked even in validate-only).
    _refuse_frontend_public(output_path, "records output path")
    if write_verification_manifest is not None:
        _refuse_frontend_public(write_verification_manifest,
                                "verification manifest path")

    fresh_rows = _select_rows(ranking, requested)
    payload = _build_fresh_payload(run_id, fresh_rows)

    if not confirm_blob_upload:
        # Validate-only: NO client, NO PUT/GET, NO file written.
        return {
            "status": "validate_only",
            "run_id": run_id,
            "requested_secondaries": list(requested),
            "would_upload_count": len(fresh_rows),
            "blob_client_constructed": False,
            "records_written": False,
            "note": ("validation passed; supply --confirm-blob-upload to PUT + "
                     "GET-verify CCC sidecars and write the records file"),
        }

    # ---- Blob boundary (only past explicit confirmation) ----
    from utils.react_publish.promote_k6_mtf_artifact import (  # noqa: PLC0415
        extract_ccc_to_blob_sidecars, build_ccc_verification_manifest)
    blob_client = client if client is not None else _make_default_client()
    slim_payload, records = extract_ccc_to_blob_sidecars(
        payload, client=blob_client, ranking_run_id=run_id)
    _assert_records(records, requested)

    out = Path(output_path)
    _atomic_write_json(out, records)

    manifest_path = None
    if write_verification_manifest is not None:
        manifest = build_ccc_verification_manifest(
            payload, records, ranking_run_id=run_id)
        manifest_path = Path(write_verification_manifest)
        _atomic_write_json(manifest_path, manifest)

    return {
        "status": "uploaded",
        "run_id": run_id,
        "requested_secondaries": list(requested),
        "record_count": len(records),
        "secondaries_emitted": sorted(_norm(r["secondary"]) for r in records),
        "reused_count": sum(1 for r in records if r.get("reused") is True),
        "records_path": out.as_posix(),
        "verification_manifest_path": (manifest_path.as_posix()
                                       if manifest_path is not None else None),
        "blob_client_constructed": client is None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fresh_ccc_blob_upload",
        description=(
            "Upload the CCC series sidecars of explicitly requested FRESH K6 "
            "MTF secondaries to Vercel Blob, GET-verify them, and write the "
            "verified fresh CCC records file consumed by the crunch "
            "orchestrator's --publish-dry-run-fresh-ccc-records-file. No "
            "promote, no public fixture, no commit/push/deploy; carried "
            "prior-board sidecars are never touched."))
    p.add_argument("--k6-ranking", required=True,
                   help="run-id-bound K6 v1 ranking artifact "
                        "(e.g. output/k6_mtf/<run_id>/k6_mtf_ranking.json)")
    p.add_argument("--secondaries", required=True,
                   help="explicit fresh secondary allow-list, CSV "
                        "(e.g. IHI,SCHG)")
    p.add_argument("--output", required=True,
                   help="records file to write (e.g. "
                        "output/crunch_runs/<run_id>/fresh_ccc_records.json); "
                        "must not be under frontend/public")
    p.add_argument("--ranking-run-id", default=None,
                   help="optional; must equal the artifact run_id if supplied")
    p.add_argument("--write-verification-manifest", default=None,
                   help="optional fresh-only CCC verification manifest path "
                        "(audit-only; OFF by default; not consumed by the "
                        "dry-run)")
    p.add_argument("--confirm-blob-upload", action="store_true",
                   help="REQUIRED to cross the Blob boundary (PUT + GET-verify "
                        "+ write records). Without it the tool validates inputs "
                        "and reports a would-upload plan only.")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = upload_fresh_ccc(
            k6_ranking_path=args.k6_ranking,
            secondaries=args.secondaries,
            output_path=args.output,
            confirm_blob_upload=args.confirm_blob_upload,
            ranking_run_id=args.ranking_run_id,
            write_verification_manifest=args.write_verification_manifest,
        )
    except FreshCccError as exc:
        print(json.dumps({"status": "refused", "error": str(exc)},
                         indent=2, sort_keys=True))
        return 2
    except Exception as exc:  # noqa: BLE001 - fail closed; token-safe
        # Type name only; never include exception text that could echo a token.
        print(json.dumps({"status": "error",
                          "error_type": type(exc).__name__},
                         indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
