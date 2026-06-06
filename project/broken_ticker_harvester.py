"""Broken-ticker harvester (read-only).

Reconciles two standing on-disk breakage signals into a reviewable
candidate broken-ticker list, and derives which current StackBuilder
selected K=6 secondary stacks are affected by a broken member.

This is NOT the crunch orchestrator. It runs nothing, repairs nothing,
fetches nothing, and mutates no operational state. It only reads:

  Source A -- cache/status/<T>_status.json  (signal-engine cache verdicts)
  Source B -- global_ticker_library/data/registry.db  (GTL ticker registry,
              opened strictly read-only)
  StackBuilder -- output/stackbuilder/<SEC>/selected_build.json
                  -> selected_run_dir -> combo_k=6.json  (K=6 members)

and writes a single run directory of review artifacts:

  proposed_blocked_tickers.json   full reconciled records with provenance
  proposed_blocked_tickers.txt    CONFIDENT_BROKEN tickers only, sorted
  affected_secondaries.json       secondary -> offending members + provenance
  SUMMARY.md                      counts + REVIEW BEFORE APPROVING section

Conservative by construction: a ticker is CONFIDENT_BROKEN only on strong
evidence; transient/ambiguous/old signals stay REVIEW. A CONFIDENT_BROKEN
member makes its secondary a rebuild candidate; REVIEW members never block
a secondary without operator approval.

ASCII-only. Stdlib only. Project-relative defaults; no machine paths baked
into source.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Classification vocabularies (lowercased substring/code matching)
# ---------------------------------------------------------------------------

# Registry error codes/messages that indicate the ticker is genuinely dead
# (no data / delisted / expired), i.e. a real breakage rather than transient.
DEAD_CODES = (
    "not_found",
    "no_price_data",
    "no_data",
    "no_history",
    "expired",
    "delisted",
)
# Transient / ambiguous codes that must NOT be treated as broken on their own.
TRANSIENT_CODES = (
    "rate_limit",
    "timeout",
    "retry",
    "network",
    "temporary",
    "unknown",
)

CONFIDENT_BROKEN = "CONFIDENT_BROKEN"
REVIEW = "REVIEW"

CLASS_DEAD = "DEAD"
CLASS_TRANSIENT = "TRANSIENT"
CLASS_STALE_STATUS = "STALE_STATUS"
CLASS_UNKNOWN = "UNKNOWN_CLASS"

DEFAULT_RECENT_DAYS = 45
DEFAULT_STACKBUILDER_ROOT = "output/stackbuilder"
DEFAULT_CACHE_STATUS_DIR = "cache/status"
DEFAULT_OUTPUT_BASE = "output/broken_ticker_harvest"
COMBO_FILENAME = "combo_k=6.json"
SELECTED_BUILD_FILENAME = "selected_build.json"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc_run_id(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def normalize_ticker(raw: Any) -> str:
    """Canonical key for unioning tickers across sources. Upper-cased and
    stripped; preserves dots / carets / hyphens that are part of the symbol."""
    return str(raw).strip().upper()


def strip_member_protocol(member: Any) -> str:
    """A combo member is 'TICKER[D]' or 'TICKER[I]'. Return the bare ticker."""
    s = str(member).strip()
    if s.endswith("[D]") or s.endswith("[I]"):
        s = s[:-3]
    return s.strip()


def _parse_utc(value: Any) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp. Return None on missing/unparseable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _classify_registry_row(status: str, code: Any, message: Any) -> str:
    """Classify a registry invalid/stale row into DEAD / TRANSIENT /
    STALE_STATUS / UNKNOWN_CLASS using code first, then message text."""
    hay = " ".join(
        str(x).strip().lower() for x in (code, message) if x is not None
    )
    if any(d in hay for d in DEAD_CODES):
        return CLASS_DEAD
    if any(t in hay for t in TRANSIENT_CODES):
        return CLASS_TRANSIENT
    if str(status).strip().lower() == "stale":
        return CLASS_STALE_STATUS
    return CLASS_UNKNOWN


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".part",
                              dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(str(tmp_path), str(path))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _dump_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Source A: cache/status sweep
# ---------------------------------------------------------------------------


def collect_source_a(cache_status_dir: Path) -> tuple[dict, list]:
    """Return ({normalized_ticker: [evidence,...]}, diagnostics).

    Collects records whose status == 'failed' OR cache_status == 'stale'.
    cache_status == 'unknown' alone is NOT a breakage signal.
    """
    records: dict[str, list] = {}
    diagnostics: list = []
    if not cache_status_dir.is_dir():
        diagnostics.append({
            "source": "cache_status",
            "issue": "cache_status_dir_missing",
            "path": cache_status_dir.as_posix(),
        })
        return records, diagnostics
    for entry in sorted(cache_status_dir.glob("*_status.json")):
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            diagnostics.append({
                "source": "cache_status",
                "issue": "unreadable_or_invalid_json",
                "path": entry.as_posix(),
                "error_type": type(exc).__name__,
            })
            continue
        if not isinstance(data, dict):
            diagnostics.append({
                "source": "cache_status",
                "issue": "not_a_json_object",
                "path": entry.as_posix(),
            })
            continue
        status = data.get("status")
        cache_status = data.get("cache_status")
        is_failed = status == "failed"
        is_stale = cache_status == "stale"
        if not (is_failed or is_stale):
            continue
        ticker_field = data.get("ticker")
        stem = entry.name
        if stem.endswith("_status.json"):
            stem = stem[: -len("_status.json")]
        ticker_raw = ticker_field if ticker_field else stem
        norm = normalize_ticker(ticker_raw)
        pft = data.get("provider_fetch_telemetry")
        pft_summary = None
        if isinstance(pft, dict):
            pft_summary = {
                k: pft.get(k) for k in (
                    "provider_name", "fetch_attempted", "fetch_succeeded",
                    "rows", "date_range_end", "elapsed_seconds", "error",
                ) if k in pft
            }
        evidence = {
            "source_path": entry.as_posix(),
            "ticker_raw": str(ticker_raw),
            "status": status,
            "cache_status": cache_status,
            "is_failed": is_failed,
            "is_stale": is_stale,
            "message": data.get("message"),
            "producer": data.get("producer"),
            "error": data.get("error"),
            "error_code": data.get("error_code"),
            "error_message": data.get("error_message"),
            "provider_fetch_telemetry": pft_summary,
        }
        records.setdefault(norm, []).append(evidence)
    return records, diagnostics


# ---------------------------------------------------------------------------
# Source B: registry.db (read-only)
# ---------------------------------------------------------------------------


def collect_source_b(
    registry_db: Path, now: datetime, recent_days: int,
) -> tuple[dict, list, dict]:
    """Return ({normalized_ticker: [evidence,...]}, diagnostics, stats).

    Opens the SQLite DB strictly read-only. Selects status in
    {'invalid','stale'} and classifies each row.
    """
    records: dict[str, list] = {}
    diagnostics: list = []
    stats = {
        "registry_invalid_total": 0,
        "registry_stale_total": 0,
        "windowed_total": 0,
        "class_counts": {
            CLASS_DEAD: 0, CLASS_TRANSIENT: 0,
            CLASS_STALE_STATUS: 0, CLASS_UNKNOWN: 0,
        },
    }
    if not registry_db.is_file():
        diagnostics.append({
            "source": "registry",
            "issue": "registry_db_missing",
            "path": registry_db.as_posix(),
        })
        return records, diagnostics, stats
    uri = "file:" + registry_db.resolve().as_posix() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        diagnostics.append({
            "source": "registry",
            "issue": "registry_open_failed",
            "error_type": type(exc).__name__,
        })
        return records, diagnostics, stats
    try:
        con.row_factory = sqlite3.Row
        cols = {r[1] for r in con.execute("PRAGMA table_info(tickers)").fetchall()}
        if "symbol" not in cols or "status" not in cols:
            diagnostics.append({
                "source": "registry",
                "issue": "unexpected_schema",
                "columns_present": sorted(cols),
            })
            return records, diagnostics, stats
        sel = ["symbol", "status"]
        for opt in ("last_error_code", "last_error_msg", "invalidated_utc",
                    "last_verified_utc", "stale_strikes"):
            if opt in cols:
                sel.append(opt)
        else_missing = [c for c in (
            "last_error_code", "last_error_msg", "invalidated_utc",
            "last_verified_utc", "stale_strikes") if c not in cols]
        if else_missing:
            diagnostics.append({
                "source": "registry",
                "issue": "optional_columns_absent",
                "columns_absent": else_missing,
            })
        query = (
            "SELECT " + ", ".join(sel)
            + " FROM tickers WHERE status IN ('invalid','stale')"
        )
        cutoff = now.timestamp() - recent_days * 86400.0
        for row in con.execute(query):
            d = dict(row)
            status = d.get("status")
            if status == "invalid":
                stats["registry_invalid_total"] += 1
            elif status == "stale":
                stats["registry_stale_total"] += 1
            code = d.get("last_error_code")
            msg = d.get("last_error_msg")
            cls = _classify_registry_row(status, code, msg)
            stats["class_counts"][cls] += 1
            inv = _parse_utc(d.get("invalidated_utc"))
            if inv is None:
                within = None
            else:
                within = inv.timestamp() >= cutoff
            if within is True:
                stats["windowed_total"] += 1
            norm = normalize_ticker(d.get("symbol"))
            evidence = {
                "symbol": d.get("symbol"),
                "status": status,
                "last_error_code": code,
                "last_error_msg": msg,
                "invalidated_utc": d.get("invalidated_utc"),
                "last_verified_utc": d.get("last_verified_utc"),
                "stale_strikes": d.get("stale_strikes"),
                "classification": cls,
                "within_recent_window": within,
            }
            records.setdefault(norm, []).append(evidence)
    finally:
        con.close()
    return records, diagnostics, stats


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def reconcile(source_a: dict, source_b: dict) -> dict:
    """Union A and B by normalized ticker; bucket each into CONFIDENT_BROKEN
    or REVIEW with provenance and a reason. Returns {ticker: record}."""
    out: dict[str, dict] = {}
    for norm in sorted(set(source_a) | set(source_b)):
        a_recs = source_a.get(norm, [])
        b_recs = source_b.get(norm, [])
        a_failed = any(r.get("is_failed") for r in a_recs)
        a_stale = any(r.get("is_stale") for r in a_recs)
        a_present = bool(a_recs)
        b_present = bool(b_recs)
        b_dead = [r for r in b_recs if r.get("classification") == CLASS_DEAD]
        b_dead_recent = any(r.get("within_recent_window") is True for r in b_dead)
        b_dead_unparseable = any(
            r.get("within_recent_window") is None for r in b_dead)
        b_dead_outside = any(
            r.get("within_recent_window") is False for r in b_dead)
        b_transient = any(
            r.get("classification") == CLASS_TRANSIENT for r in b_recs)
        b_stale_status = any(
            r.get("classification") == CLASS_STALE_STATUS for r in b_recs)
        b_unknown = any(
            r.get("classification") == CLASS_UNKNOWN for r in b_recs)

        classifications = sorted({
            r.get("classification") for r in b_recs if r.get("classification")
        })

        b_dead_any = bool(b_dead)
        reasons: list[str] = []
        bucket = REVIEW
        # CONFIDENT rules (any one is sufficient). There is NO generic
        # "both sources present" promotion: corroboration only counts when
        # the registry side is DEAD-classified, so a stale cache file plus a
        # TRANSIENT/STALE_STATUS/UNKNOWN_CLASS registry row stays REVIEW.
        if a_failed:
            # 1) Direct producer failure verdict (strong on its own).
            bucket = CONFIDENT_BROKEN
            reasons.append("cache/status status=='failed'")
        if b_dead_recent:
            # 2) Registry marked the ticker DEAD within the recent window.
            bucket = CONFIDENT_BROKEN
            reasons.append("registry DEAD within recent window")
        if a_present and b_dead_any:
            # 3) DEAD corroboration: a fresh cache failed/stale signal
            #    re-confirms a registry DEAD record (even if its
            #    invalidated_utc is old or unparseable). TRANSIENT/STALE/
            #    UNKNOWN registry records do NOT qualify.
            bucket = CONFIDENT_BROKEN
            reasons.append(
                "cache/status corroborates a registry DEAD record")

        if bucket != CONFIDENT_BROKEN:
            if b_transient and not (a_failed or b_dead_any):
                reasons.append("registry TRANSIENT (not promoted) -> review")
            if b_dead_outside and not (b_dead_recent or a_present):
                reasons.append("registry-only DEAD outside recent window -> review")
            if b_dead_unparseable and not a_present:
                reasons.append(
                    "registry-only DEAD with missing/unparseable "
                    "invalidated_utc -> review")
            if (a_stale or b_stale_status) and not (a_failed or b_dead_any):
                reasons.append("stale-only without failed/dead corroboration -> review")
            if b_unknown and not reasons:
                reasons.append("registry UNKNOWN_CLASS -> review")
            if not reasons:
                reasons.append("ambiguous evidence -> review")

        sources = []
        if a_present:
            sources.append("cache_status")
        if b_present:
            sources.append("registry")

        out[norm] = {
            "ticker": norm,
            "sources": sources,
            "bucket": bucket,
            "reason": "; ".join(reasons),
            "registry_classifications": classifications,
            "recency": {
                "registry_dead_recent": b_dead_recent,
                "registry_dead_outside_window": b_dead_outside,
                "registry_dead_unparseable_invalidated_utc": b_dead_unparseable,
            },
            "source_a": a_recs,
            "source_b": b_recs,
            "confidence_notes": _confidence_note(
                bucket, a_failed, b_dead_recent, a_present and b_dead_any),
        }
    return out


def _confidence_note(bucket, a_failed, b_dead_recent, dead_corroborated) -> str:
    if bucket == CONFIDENT_BROKEN:
        parts = []
        if a_failed:
            parts.append("cache-status failure is a direct producer verdict")
        if b_dead_recent:
            parts.append("registry marked dead recently")
        if dead_corroborated and not (a_failed or b_dead_recent):
            parts.append("cache signal corroborates a registry DEAD record")
        return "; ".join(parts) or "confident"
    return ("kept in REVIEW: evidence is transient, stale-only, old, "
            "unparseable, or ambiguous (no strong DEAD corroboration) -- "
            "needs operator confirmation")


# ---------------------------------------------------------------------------
# Affected-secondary derivation
# ---------------------------------------------------------------------------


def derive_affected_secondaries(
    stackbuilder_root: Path, confident: set, review: set, project_root: Path,
) -> tuple[dict, list]:
    """For each secondary with a selected_build.json, resolve its K=6 members
    and flag offending members. Returns ({secondary: record}, diagnostics)."""
    affected: dict[str, dict] = {}
    diagnostics: list = []
    if not stackbuilder_root.is_dir():
        diagnostics.append({
            "source": "stackbuilder",
            "issue": "stackbuilder_root_missing",
            "path": stackbuilder_root.as_posix(),
        })
        return affected, diagnostics
    for sec_dir in sorted(stackbuilder_root.iterdir(), key=lambda p: p.name):
        if not sec_dir.is_dir():
            continue
        sb_path = sec_dir / SELECTED_BUILD_FILENAME
        secondary = sec_dir.name
        if not sb_path.is_file():
            diagnostics.append({
                "secondary": secondary,
                "issue": "missing_selected_build",
                "path": sb_path.as_posix(),
            })
            continue
        try:
            sb = json.loads(sb_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            diagnostics.append({
                "secondary": secondary,
                "issue": "unreadable_selected_build",
                "path": sb_path.as_posix(),
                "error_type": type(exc).__name__,
            })
            continue
        secondary = sb.get("secondary") or secondary
        run_dir_raw = sb.get("selected_run_dir")
        if not run_dir_raw:
            diagnostics.append({
                "secondary": secondary,
                "issue": "selected_run_dir_missing",
                "path": sb_path.as_posix(),
            })
            continue
        run_dir = _resolve_path(str(run_dir_raw).replace("\\", "/"), project_root)
        combo_path = run_dir / COMBO_FILENAME
        if not combo_path.is_file():
            diagnostics.append({
                "secondary": secondary,
                "issue": "missing_combo_k6",
                "selected_build_path": sb_path.as_posix(),
                "combo_path": combo_path.as_posix(),
            })
            continue
        try:
            combo = json.loads(combo_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            diagnostics.append({
                "secondary": secondary,
                "issue": "unreadable_combo_k6",
                "combo_path": combo_path.as_posix(),
                "error_type": type(exc).__name__,
            })
            continue
        members_raw = combo.get("Members") or combo.get("members") or []
        if not isinstance(members_raw, list) or not members_raw:
            diagnostics.append({
                "secondary": secondary,
                "issue": "combo_members_missing_or_empty",
                "combo_path": combo_path.as_posix(),
            })
            continue
        offending = []
        for m in members_raw:
            bare = strip_member_protocol(m)
            norm = normalize_ticker(bare)
            if norm in confident:
                offending.append({"member": str(m), "ticker": norm,
                                  "bucket": CONFIDENT_BROKEN})
            elif norm in review:
                offending.append({"member": str(m), "ticker": norm,
                                  "bucket": REVIEW})
        if not offending:
            continue
        has_confident = any(o["bucket"] == CONFIDENT_BROKEN for o in offending)
        affected[secondary] = {
            "secondary": secondary,
            "affected_bucket": CONFIDENT_BROKEN if has_confident else REVIEW,
            "rebuild_candidate": has_confident,
            "offending_members": sorted(offending, key=lambda o: o["ticker"]),
            "all_members": [str(m) for m in members_raw],
            "selected_build_path": sb_path.as_posix(),
            "selected_run_dir": run_dir.as_posix(),
            "combo_path": combo_path.as_posix(),
        }
    return affected, diagnostics


def _resolve_path(raw: str, project_root: Path) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    return (project_root / p)


# ---------------------------------------------------------------------------
# Orchestration / output
# ---------------------------------------------------------------------------


def harvest(
    *,
    output_dir: Path,
    cache_status_dir: Path,
    registry_db: Path,
    stackbuilder_root: Path,
    project_root: Path,
    recent_days: int,
    now: datetime,
) -> dict:
    a_records, a_diag = collect_source_a(cache_status_dir)
    b_records, b_diag, b_stats = collect_source_b(registry_db, now, recent_days)
    reconciled = reconcile(a_records, b_records)

    confident = {t for t, r in reconciled.items()
                 if r["bucket"] == CONFIDENT_BROKEN}
    review = {t for t, r in reconciled.items() if r["bucket"] == REVIEW}

    affected, sec_diag = derive_affected_secondaries(
        stackbuilder_root, confident, review, project_root)

    diagnostics = a_diag + b_diag + sec_diag

    source_a_failed = sum(
        1 for recs in a_records.values()
        if any(r.get("is_failed") for r in recs))
    source_a_stale = sum(
        1 for recs in a_records.values()
        if any(r.get("is_stale") for r in recs) and not any(
            r.get("is_failed") for r in recs))

    meta = {
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registry_recent_days": recent_days,
        "inputs": {
            "cache_status_dir": cache_status_dir.as_posix(),
            "registry_db": registry_db.as_posix(),
            "stackbuilder_root": stackbuilder_root.as_posix(),
        },
    }

    blocked_records = {
        "schema_version": "broken_ticker_harvest_v1",
        "metadata": meta,
        "counts": {
            "source_a_failed": source_a_failed,
            "source_a_stale_only": source_a_stale,
            "registry_invalid_total": b_stats["registry_invalid_total"],
            "registry_stale_total": b_stats["registry_stale_total"],
            "registry_windowed_total": b_stats["windowed_total"],
            "registry_class_counts": b_stats["class_counts"],
            "confident_broken": len(confident),
            "review": len(review),
            "affected_secondaries": len(affected),
        },
        "records": [reconciled[t] for t in sorted(reconciled)],
        "diagnostics": diagnostics,
    }

    affected_doc = {
        "schema_version": "broken_ticker_affected_secondaries_v1",
        "metadata": meta,
        "secondaries": {k: affected[k] for k in sorted(affected)},
        "diagnostics": sec_diag,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(output_dir / "proposed_blocked_tickers.json",
                       _dump_json(blocked_records))
    _atomic_write_text(
        output_dir / "proposed_blocked_tickers.txt",
        "".join(t + "\n" for t in sorted(confident)))
    _atomic_write_text(output_dir / "affected_secondaries.json",
                       _dump_json(affected_doc))
    _atomic_write_text(output_dir / "SUMMARY.md",
                       _render_summary(blocked_records, affected, output_dir))
    return blocked_records


def _render_summary(doc: dict, affected: dict, output_dir: Path) -> str:
    c = doc["counts"]
    m = doc["metadata"]
    cc = c["registry_class_counts"]
    confident_affected = sorted(
        s for s, r in affected.items() if r["rebuild_candidate"])
    review_affected = sorted(
        s for s, r in affected.items() if not r["rebuild_candidate"])
    lines = []
    lines.append("# Broken-Ticker Harvest SUMMARY")
    lines.append("")
    lines.append("Read-only reconciliation of cache/status and the GTL")
    lines.append("registry into a reviewable candidate broken-ticker list.")
    lines.append("This is NOT an approval and NOT an action. Nothing was run,")
    lines.append("repaired, fetched, or mutated.")
    lines.append("")
    lines.append("- Generated at (UTC): " + m["generated_at_utc"])
    lines.append("- Registry recent window (days): "
                 + str(m["registry_recent_days"]))
    lines.append("")
    lines.append("## Source A -- cache/status")
    lines.append("- failed: " + str(c["source_a_failed"]))
    lines.append("- stale-only: " + str(c["source_a_stale_only"]))
    lines.append("")
    lines.append("## Source B -- GTL registry (read-only)")
    lines.append("- invalid (full): " + str(c["registry_invalid_total"]))
    lines.append("- stale (full): " + str(c["registry_stale_total"]))
    lines.append("- within recent window: " + str(c["registry_windowed_total"]))
    lines.append("- DEAD: " + str(cc[CLASS_DEAD]))
    lines.append("- TRANSIENT: " + str(cc[CLASS_TRANSIENT]))
    lines.append("- STALE_STATUS: " + str(cc[CLASS_STALE_STATUS]))
    lines.append("- UNKNOWN_CLASS: " + str(cc[CLASS_UNKNOWN]))
    lines.append("")
    lines.append("## Reconciled buckets")
    lines.append("- CONFIDENT_BROKEN: " + str(c["confident_broken"]))
    lines.append("- REVIEW: " + str(c["review"]))
    lines.append("")
    lines.append("## Affected current StackBuilder K=6 secondaries")
    lines.append("- total affected: " + str(c["affected_secondaries"]))
    lines.append("- rebuild candidates (a CONFIDENT_BROKEN member): "
                 + str(len(confident_affected)))
    lines.append("- review-only (a REVIEW member, no confident): "
                 + str(len(review_affected)))
    if confident_affected:
        lines.append("")
        lines.append("Rebuild-candidate secondaries:")
        for s in confident_affected:
            lines.append("  - " + s)
    lines.append("")
    lines.append("## Output files")
    lines.append("- proposed_blocked_tickers.json")
    lines.append("- proposed_blocked_tickers.txt  (CONFIDENT_BROKEN only)")
    lines.append("- affected_secondaries.json")
    lines.append("- SUMMARY.md")
    lines.append("")
    if doc.get("diagnostics"):
        lines.append("## Diagnostics")
        lines.append("- " + str(len(doc["diagnostics"]))
                     + " non-fatal issue(s) recorded in "
                     "proposed_blocked_tickers.json.diagnostics")
        lines.append("")
    lines.append("## REVIEW BEFORE APPROVING")
    lines.append("")
    lines.append("- Only proposed_blocked_tickers.txt (CONFIDENT_BROKEN) is")
    lines.append("  proposed as a blocked-ticker input; REVIEW entries are NOT")
    lines.append("  blocked and require operator confirmation.")
    lines.append("- TRANSIENT (rate_limit/timeout/network) is intentionally")
    lines.append("  kept in REVIEW; do not block transient failures.")
    lines.append("- Registry-only DEAD outside the recent window or with an")
    lines.append("  unparseable invalidated_utc is kept in REVIEW.")
    lines.append("- A CONFIDENT_BROKEN member marks a secondary a rebuild")
    lines.append("  candidate; the operator decides whether to rebuild or drop.")
    lines.append("- This harvester does not run engines, recook, validation,")
    lines.append("  promotion, Blob, or any repair. It is input for a future")
    lines.append("  crunch orchestrator, whose first leg must still reconcile")
    lines.append("  K=6 Stage-A availability.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_registry_default(project_root: Path) -> Path:
    try:
        from global_ticker_library import gl_config  # noqa: PLC0415
        return Path(gl_config.DB_PATH)
    except Exception:
        return project_root / "global_ticker_library" / "data" / "registry.db"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="broken_ticker_harvester",
        description=(
            "Read-only harvester that reconciles cache/status and the GTL "
            "registry into a reviewable candidate broken-ticker list and "
            "derives affected StackBuilder K=6 secondaries. Runs nothing, "
            "repairs nothing, fetches nothing, mutates no operational state."
        ),
    )
    p.add_argument("--output-dir", default=None,
                   help="Run output dir. Default: "
                        "output/broken_ticker_harvest/<UTC_RUN_ID>/")
    p.add_argument("--registry-recent-days", type=int,
                   default=DEFAULT_RECENT_DAYS,
                   help="Recency window applied to registry invalidated_utc.")
    p.add_argument("--stackbuilder-root", default=DEFAULT_STACKBUILDER_ROOT)
    p.add_argument("--cache-status-dir", default=DEFAULT_CACHE_STATUS_DIR)
    p.add_argument("--registry-db", default=None,
                   help="Registry SQLite DB (read-only). Default: "
                        "global_ticker_library.gl_config.DB_PATH.")
    p.add_argument("--project-root", default=None,
                   help="Project root for resolving relative paths. Default: "
                        "this script's directory.")
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    project_root = (Path(args.project_root).resolve() if args.project_root
                    else Path(__file__).resolve().parent)
    now = _utcnow()

    def _resolve(opt: str | None, default_rel: str) -> Path:
        if opt:
            q = Path(opt)
            return q if q.is_absolute() else (project_root / q)
        return project_root / default_rel

    cache_status_dir = _resolve(args.cache_status_dir, DEFAULT_CACHE_STATUS_DIR)
    stackbuilder_root = _resolve(args.stackbuilder_root,
                                 DEFAULT_STACKBUILDER_ROOT)
    if args.registry_db:
        rq = Path(args.registry_db)
        registry_db = rq if rq.is_absolute() else (project_root / rq)
    else:
        registry_db = _resolve_registry_default(project_root)
    if args.output_dir:
        oq = Path(args.output_dir)
        output_dir = oq if oq.is_absolute() else (project_root / oq)
    else:
        output_dir = (project_root / DEFAULT_OUTPUT_BASE / _utc_run_id(now))

    doc = harvest(
        output_dir=output_dir,
        cache_status_dir=cache_status_dir,
        registry_db=registry_db,
        stackbuilder_root=stackbuilder_root,
        project_root=project_root,
        recent_days=args.registry_recent_days,
        now=now,
    )
    c = doc["counts"]
    print(json.dumps({
        "output_dir": output_dir.as_posix(),
        "confident_broken": c["confident_broken"],
        "review": c["review"],
        "affected_secondaries": c["affected_secondaries"],
        "source_a_failed": c["source_a_failed"],
        "source_a_stale_only": c["source_a_stale_only"],
        "registry_invalid_total": c["registry_invalid_total"],
        "registry_stale_total": c["registry_stale_total"],
        "registry_windowed_total": c["registry_windowed_total"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
