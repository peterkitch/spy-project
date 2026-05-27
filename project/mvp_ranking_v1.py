"""MVP v1 ranking engine.

Phase 3b of the MVP Ranking Contract
(``md_library/shared/2026-05-25_MVP_RANKING_CONTRACT.md``).

The engine consumes Phase 3a ``v1_history.json`` artifacts plus the
Phase E ``board_rows_k=6.json`` artifacts from an explicit
``--run-root`` and emits ``<output-dir>/mvp_ranking_v1.json``. It does
NOT read ``selected_output.json``, raw signal libraries, price caches,
or cache PKLs, and it does NOT call ``mvp_ranking_v0`` at runtime,
modify Phase E, render UI, launch Dash, run any pipeline component,
or start React work.

Public surface:

    build_mvp_ranking_v1(*, run_root, output_dir, secondaries=None,
                          project_root=None) -> tuple[int, dict]

    main(argv: Optional[Sequence[str]] = None) -> int

Schema and ranking math are pinned by Steps v1.1 through v1.9 of the
MVP Ranking Contract.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from trafficflow_runner import (
    _atomic_write_json,
    _scrub_embedded_absolute_paths,
    path_for_output,
    sanitize_for_json,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "mvp_ranking_v1"
ARTIFACT_FILENAME = "mvp_ranking_v1.json"

V1_HISTORY_SCHEMA_VERSION = "mvp_v1_history_v1"

LOW_SAMPLE_THRESHOLD = 30
TRADING_DAYS_PER_YEAR = 252

TIMEFRAMES: tuple[str, ...] = ("1d", "1wk", "1mo", "3mo", "1y")

SIGNAL_BUY = "BUY"
SIGNAL_SHORT = "SHORT"
SIGNAL_NONE = "NONE"
SIGNAL_UNAVAILABLE = "UNAVAILABLE"
ALLOWED_SIGNAL_VALUES: tuple[str, ...] = (
    SIGNAL_BUY, SIGNAL_SHORT, SIGNAL_NONE, SIGNAL_UNAVAILABLE,
)
WILDCARD_SIGNAL_VALUES: frozenset[str] = frozenset(
    {SIGNAL_NONE, SIGNAL_UNAVAILABLE}
)

OPTIONAL_PHASE_E_STATUS_KEYS: tuple[str, ...] = (
    "Today", "Now", "NEXT", "TMRW", "MIX",
)

ERROR_MISSING_V1_HISTORY = "missing_v1_history"
ERROR_MISSING_BOARD_ROWS_K6 = "missing_board_rows_k6"
ERROR_MISSING_K6_ROW = "missing_k6_row"
ERROR_V1_HISTORY_SCHEMA_MISMATCH = "v1_history_schema_mismatch"
ERROR_V1_HISTORY_MALFORMED = "v1_history_malformed"
ERROR_V1_HISTORY_SECONDARY_MISMATCH = "v1_history_secondary_mismatch"
ERROR_BOARD_ROWS_K6_MALFORMED = "board_rows_k6_malformed"

EXIT_OK = 0
EXIT_GLOBAL_FAILURE = 2
EXIT_ALL_SECONDARIES_FAILED = 3


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mvp_ranking_v1",
        description=(
            "Read Phase 3a v1_history.json and Phase E board_rows_k=6.json "
            "artifacts from an explicit --run-root and emit the MVP v1 "
            "ranking artifact (Phase 3b of the MVP Ranking Contract)."
        ),
    )
    p.add_argument(
        "--run-root", required=True,
        help=(
            "Path to a Phase E run root containing per-secondary "
            "artifacts (e.g. output/trafficflow/runs/<UTC_TS>)."
        ),
    )
    p.add_argument(
        "--output-dir", required=True,
        help=(
            "MVP v1 run output directory; e.g. "
            "output/mvp/runs/<UTC_TS>."
        ),
    )
    p.add_argument(
        "--secondaries", default=None,
        help=(
            "Comma-separated secondary tickers. Default: discover "
            "subdirectories of --run-root containing v1_history.json."
        ),
    )
    return p.parse_args(list(argv) if argv is not None else None)


# ---------------------------------------------------------------------------
# Coercion / IO helpers
# ---------------------------------------------------------------------------


def _coerce_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if value is None:
        return None
    try:
        if isinstance(value, str):
            if "." in value:
                f = float(value)
                if not f.is_integer():
                    return None
                return int(f)
            return int(value)
        if isinstance(value, float):
            if not value.is_integer():
                return None
            return int(value)
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_secondaries(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tok in str(raw).split(","):
        t = tok.strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _discover_secondaries(run_root: Path) -> list[str]:
    if not run_root.is_dir():
        return []
    out: list[str] = []
    for child in sorted(run_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith(".") or name.startswith("_"):
            continue
        if (child / "v1_history.json").is_file():
            out.append(name)
    return out


def _make_issue(
    secondary: Optional[str],
    error_code: str,
    message: Optional[str] = None,
) -> dict:
    raw = "" if message is None else str(message)[:240]
    rec: dict[str, Any] = {
        "error_code": error_code,
        "message_sanitized": _scrub_embedded_absolute_paths(raw),
    }
    if secondary is not None:
        rec["secondary"] = secondary
    return rec


def _normalize_members(value: Any) -> list[str]:
    """Accept ``"AAA, BBB"`` or ``["AAA","BBB"]`` and return a deduped
    upper-cased list, preserving first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    if value is None:
        return out
    if isinstance(value, str):
        tokens = [t.strip() for t in value.split(",")]
    elif isinstance(value, (list, tuple)):
        tokens = [str(t).strip() for t in value]
    else:
        return out
    for t in tokens:
        if not t:
            continue
        u = t.upper()
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _extract_phase_e_status(row: dict) -> dict:
    out: dict[str, Any] = {}
    for key in OPTIONAL_PHASE_E_STATUS_KEYS:
        if key in row and row[key] is not None:
            out[key] = row[key]
    return out


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def _select_k6_row(rows: Any) -> Optional[dict]:
    if not isinstance(rows, list) or not rows:
        return None
    for r in rows:
        if isinstance(r, dict):
            k_val = _coerce_int(r.get("K"))
            if k_val == 6:
                return r
    return None


def _validate_v1_history(doc: Any) -> tuple[Optional[dict], Optional[str]]:
    """Return ``(doc, None)`` if ``doc`` matches the v1 history schema,
    else ``(None, error_code)`` where ``error_code`` is either
    ``v1_history_schema_mismatch`` or ``v1_history_malformed``.
    """
    if not isinstance(doc, dict):
        return None, ERROR_V1_HISTORY_MALFORMED
    sv = doc.get("schema_version")
    if sv != V1_HISTORY_SCHEMA_VERSION:
        return None, ERROR_V1_HISTORY_SCHEMA_MISMATCH
    bars = doc.get("bars")
    if not isinstance(bars, list) or len(bars) == 0:
        return None, ERROR_V1_HISTORY_MALFORMED
    tf_cov = doc.get("timeframes_covered")
    if not isinstance(tf_cov, list) or tuple(tf_cov) != TIMEFRAMES:
        return None, ERROR_V1_HISTORY_MALFORMED
    # Validate the last bar minimally: it must carry a signals dict
    # with all five timeframe keys.
    last = bars[-1]
    if not isinstance(last, dict):
        return None, ERROR_V1_HISTORY_MALFORMED
    sigs = last.get("signals")
    if not isinstance(sigs, dict):
        return None, ERROR_V1_HISTORY_MALFORMED
    for tf in TIMEFRAMES:
        if tf not in sigs:
            return None, ERROR_V1_HISTORY_MALFORMED
        if sigs[tf] not in ALLOWED_SIGNAL_VALUES:
            return None, ERROR_V1_HISTORY_MALFORMED
    return doc, None


# ---------------------------------------------------------------------------
# Step v1.1 - trade direction
# ---------------------------------------------------------------------------


def _step_trade_direction(k6_total_capture_pct: float) -> tuple[str, bool]:
    if k6_total_capture_pct > 0:
        return SIGNAL_BUY, False
    if k6_total_capture_pct < 0:
        return SIGNAL_SHORT, False
    return SIGNAL_BUY, True


# ---------------------------------------------------------------------------
# Step v1.2 - current alignment state
# ---------------------------------------------------------------------------


def _step_current_alignment(v1_hist: dict) -> dict[str, str]:
    bars = v1_hist["bars"]
    last_signals = bars[-1]["signals"]
    return {tf: last_signals[tf] for tf in TIMEFRAMES}


# ---------------------------------------------------------------------------
# Step v1.4 - match rule
# ---------------------------------------------------------------------------


def _bar_matches_alignment(
    bar_signals: Any,
    current_alignment: dict[str, str],
) -> bool:
    if not isinstance(bar_signals, dict):
        return False
    for tf in TIMEFRAMES:
        cur = current_alignment.get(tf)
        hist = bar_signals.get(tf)
        if cur in WILDCARD_SIGNAL_VALUES:
            continue
        if hist in WILDCARD_SIGNAL_VALUES:
            continue
        if hist != cur:
            return False
    return True


# ---------------------------------------------------------------------------
# Step v1.5 - per-bar capture
# ---------------------------------------------------------------------------


def _safe_positive_close(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    f = float(value)
    if not math.isfinite(f):
        return None
    if f <= 0:
        return None
    return f


def _collect_matching_captures(
    v1_hist: dict,
    direction: str,
    current_alignment: dict[str, str],
) -> tuple[list[float], list[str]]:
    """Walk ``v1_hist['bars']`` and return ``(captures, dates)`` for
    each historical bar that (a) matches the current alignment under
    the v1.4 match rule, and (b) has usable current and next closes.
    The last bar is never a match candidate.
    """
    bars = v1_hist["bars"]
    captures: list[float] = []
    dates: list[str] = []
    n = len(bars)
    for i in range(n - 1):
        bar = bars[i]
        if not isinstance(bar, dict):
            continue
        if not _bar_matches_alignment(bar.get("signals"), current_alignment):
            continue
        cur_close = _safe_positive_close(bar.get("close"))
        nxt_close = _safe_positive_close(bars[i + 1].get("close")
                                         if isinstance(bars[i + 1], dict)
                                         else None)
        if cur_close is None or nxt_close is None:
            continue
        raw_pct = (nxt_close / cur_close - 1.0) * 100.0
        cap = raw_pct if direction == SIGNAL_BUY else -raw_pct
        captures.append(cap)
        dates.append(str(bar.get("date_utc")))
    return captures, dates


# ---------------------------------------------------------------------------
# Step v1.6 - v1 metrics
# ---------------------------------------------------------------------------


def _sample_stddev(values: Sequence[float]) -> float:
    """Population-corrected sample standard deviation with ddof=1.

    Matches the broader PRJCT9 scoring convention (canonical_scoring.py
    uses sample stddev for capture-quality metrics). Returns 0.0 for
    n<2 callers; the engine checks n<2 explicitly before invoking, so
    this is a defensive default.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(var)


def _compute_v1_metrics(captures: list[float]) -> dict[str, Any]:
    n = len(captures)
    metrics: dict[str, Any] = {
        "v1_n": n,
        "v1_total_capture_pct": None,
        "v1_avg_capture_pct": None,
        "v1_stddev_pct": None,
        "v1_sharpe": None,
        "v1_win_count": None,
        "v1_loss_count": None,
        "v1_win_pct": None,
        "low_sample_warning": n < LOW_SAMPLE_THRESHOLD,
        "sharpe_undefined_reason": None,
    }
    if n == 0:
        return metrics
    total = sum(captures)
    avg = total / n
    metrics["v1_total_capture_pct"] = total
    metrics["v1_avg_capture_pct"] = avg
    metrics["v1_win_count"] = sum(1 for c in captures if c > 0)
    metrics["v1_loss_count"] = sum(1 for c in captures if c < 0)
    metrics["v1_win_pct"] = metrics["v1_win_count"] / n * 100.0
    if n < 2:
        metrics["sharpe_undefined_reason"] = "n_less_than_two"
        return metrics
    stddev = _sample_stddev(captures)
    metrics["v1_stddev_pct"] = stddev
    if stddev == 0.0:
        metrics["sharpe_undefined_reason"] = "stddev_zero"
        return metrics
    metrics["v1_sharpe"] = (avg / stddev) * math.sqrt(TRADING_DAYS_PER_YEAR)
    return metrics


# ---------------------------------------------------------------------------
# Step v1.8 - CCC series
# ---------------------------------------------------------------------------


def _compute_ccc_series(
    captures: Sequence[float], dates: Sequence[str],
) -> list[dict]:
    out: list[dict] = []
    running = 0.0
    for d, c in zip(dates, captures):
        running += c
        out.append({"date_utc": d, "cumulative_capture_pct": running})
    return out


# ---------------------------------------------------------------------------
# Step v1.9 - ranking
# ---------------------------------------------------------------------------


def _rank_records(records: list[dict]) -> list[dict]:
    """Sort and assign ``rank`` to input-valid records.

    Numeric-Sharpe records come first, ordered by Sharpe desc, then
    total capture desc, then secondary alphabetically. Null-Sharpe
    input-valid records follow alphabetically. Failed records are not
    in this list (they are appended separately by the caller with
    ``rank = None``).
    """
    numeric = [r for r in records if r.get("v1_sharpe") is not None]
    nulls = [r for r in records if r.get("v1_sharpe") is None]
    numeric.sort(
        key=lambda r: (
            -float(r["v1_sharpe"]),
            -float(r.get("v1_total_capture_pct") or 0.0),
            str(r["secondary"]),
        )
    )
    nulls.sort(key=lambda r: str(r["secondary"]))
    ordered = numeric + nulls
    for idx, r in enumerate(ordered, start=1):
        r["rank"] = idx
    return ordered


# ---------------------------------------------------------------------------
# Per-secondary processing
# ---------------------------------------------------------------------------


def _build_k6_metrics(k6_row: dict) -> dict[str, Any]:
    return {
        "k": 6,
        "sharpe": _coerce_float(k6_row.get("Sharpe")),
        "total_capture_pct": _coerce_float(k6_row.get("Total %")),
        "triggers": _coerce_int(k6_row.get("Trigs")),
        "wins": _coerce_int(k6_row.get("Wins")),
        "losses": _coerce_int(k6_row.get("Losses")),
        "win_pct": _coerce_float(k6_row.get("Win %")),
        "avg_capture_pct": _coerce_float(k6_row.get("Avg %")),
        "stddev_pct": _coerce_float(k6_row.get("StdDev %")),
        "p_value": _coerce_float(k6_row.get("p")),
        "low_sample_warning": (
            (_coerce_int(k6_row.get("Trigs")) or 0) < LOW_SAMPLE_THRESHOLD
        ),
    }


def _process_secondary(
    secondary: str, run_root: Path,
) -> tuple[Optional[dict], Optional[dict]]:
    """Return ``(record, issue)`` for one requested secondary.

    On input failure, returns ``(None, issue)``. On input success,
    returns ``(record, None)``; the record may still carry
    ``processing_status == 'unranked'`` if no matching bars were found
    or if Sharpe is undefined.
    """
    sec_dir = run_root / secondary
    v1_path = sec_dir / "v1_history.json"
    rows_path = sec_dir / "board_rows_k=6.json"
    sec_manifest_path = sec_dir / "secondary_manifest.json"

    record_issues: list[dict] = []

    if not v1_path.is_file():
        return None, _make_issue(secondary, ERROR_MISSING_V1_HISTORY)
    if not rows_path.is_file():
        return None, _make_issue(secondary, ERROR_MISSING_BOARD_ROWS_K6)

    try:
        v1_doc = _read_json(v1_path)
    except Exception as exc:
        return None, _make_issue(
            secondary, ERROR_V1_HISTORY_MALFORMED, repr(exc),
        )
    v1_hist, v1_err = _validate_v1_history(v1_doc)
    if v1_hist is None:
        return None, _make_issue(secondary, v1_err or ERROR_V1_HISTORY_MALFORMED)

    # Codex audit fix: the v1 history artifact's embedded ``secondary``
    # field must match the requested per-secondary directory name
    # (uppercased) per the MVP v1 History Artifact Contract. A
    # mismatch indicates the artifact does not describe this
    # secondary; fail closed before doing any v1 math.
    embedded_secondary = v1_hist.get("secondary")
    embedded_norm = (
        str(embedded_secondary).strip().upper()
        if isinstance(embedded_secondary, str) else None
    )
    if embedded_norm != secondary:
        return None, _make_issue(
            secondary, ERROR_V1_HISTORY_SECONDARY_MISMATCH,
            f"v1_history secondary {embedded_secondary!r} "
            f"does not match requested {secondary!r}",
        )

    try:
        rows = _read_json(rows_path)
    except Exception as exc:
        return None, _make_issue(
            secondary, ERROR_BOARD_ROWS_K6_MALFORMED, repr(exc),
        )
    if not isinstance(rows, list):
        return None, _make_issue(secondary, ERROR_BOARD_ROWS_K6_MALFORMED)

    k6_row = _select_k6_row(rows)
    if k6_row is None:
        return None, _make_issue(secondary, ERROR_MISSING_K6_ROW)

    k6_total = _coerce_float(k6_row.get("Total %"))
    if k6_total is None:
        return None, _make_issue(secondary, ERROR_BOARD_ROWS_K6_MALFORMED)

    if not sec_manifest_path.is_file():
        record_issues.append(_make_issue(
            secondary, "missing_secondary_manifest",
        ))

    direction, zero_default = _step_trade_direction(k6_total)
    current_alignment = _step_current_alignment(v1_hist)
    captures, cap_dates = _collect_matching_captures(
        v1_hist, direction, current_alignment,
    )
    v1_metrics = _compute_v1_metrics(captures)
    n = v1_metrics["v1_n"]
    if n == 0:
        processing_status = "unranked"
        record_issues.append(_make_issue(secondary, "no_matching_bars"))
    elif v1_metrics["v1_sharpe"] is None:
        processing_status = "unranked"
        record_issues.append(_make_issue(
            secondary, "sharpe_undefined",
            v1_metrics.get("sharpe_undefined_reason"),
        ))
    else:
        processing_status = "ranked"

    ccc_series = _compute_ccc_series(captures, cap_dates)

    record: dict[str, Any] = {
        "rank": None,
        "secondary": secondary,
        "processing_status": processing_status,
        "trade_direction": direction,
        "zero_capture_direction_default": zero_default,
        "current_alignment_state": current_alignment,
        "members": _normalize_members(k6_row.get("Members")),
        "k6_metrics": _build_k6_metrics(k6_row),
        "phase_e_status": _extract_phase_e_status(k6_row),
        "v1_sharpe": v1_metrics["v1_sharpe"],
        "v1_total_capture_pct": v1_metrics["v1_total_capture_pct"],
        "v1_avg_capture_pct": v1_metrics["v1_avg_capture_pct"],
        "v1_stddev_pct": v1_metrics["v1_stddev_pct"],
        "v1_n": v1_metrics["v1_n"],
        "v1_win_count": v1_metrics["v1_win_count"],
        "v1_loss_count": v1_metrics["v1_loss_count"],
        "v1_win_pct": v1_metrics["v1_win_pct"],
        "low_sample_warning": v1_metrics["low_sample_warning"],
        "ccc_series": ccc_series,
        "issues": record_issues,
    }
    return record, None


# ---------------------------------------------------------------------------
# Top-level engine
# ---------------------------------------------------------------------------


def _resolve_run_root(raw: Any) -> tuple[Optional[Path], Optional[dict]]:
    if raw is None or str(raw).strip() == "":
        return None, _make_issue(None, "missing_run_root")
    p = Path(str(raw))
    if not p.exists() or not p.is_dir():
        return None, _make_issue(None, "run_root_not_a_directory")
    return p, None


def _read_run_manifest(run_root: Path) -> tuple[Optional[dict], Optional[dict]]:
    manifest_path = run_root / "run_manifest.json"
    if not manifest_path.is_file():
        return None, _make_issue(None, "missing_run_manifest")
    try:
        doc = _read_json(manifest_path)
    except Exception as exc:
        return None, _make_issue(
            None, "run_manifest_unreadable", repr(exc),
        )
    if not isinstance(doc, dict):
        return None, _make_issue(None, "run_manifest_malformed")
    return doc, None


def build_mvp_ranking_v1(
    *,
    run_root: Any,
    output_dir: Any,
    secondaries: Optional[Sequence[str]] = None,
    project_root: Optional[Path] = None,
) -> tuple[int, dict]:
    """Build the MVP v1 ranking artifact.

    Returns ``(exit_code, payload)``. ``payload`` is the artifact dict
    on success, or a refusal/failure envelope on global failure / all-
    secondaries-failed paths.
    """
    project_root = Path(project_root) if project_root is not None else Path.cwd()

    run_root_path, run_root_issue = _resolve_run_root(run_root)
    if run_root_path is None:
        return EXIT_GLOBAL_FAILURE, {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": _utc_iso(),
            "ranking_status": "failed",
            "exit_code": EXIT_GLOBAL_FAILURE,
            "issues": [run_root_issue] if run_root_issue else [],
        }

    if not output_dir or str(output_dir).strip() == "":
        return EXIT_GLOBAL_FAILURE, {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": _utc_iso(),
            "ranking_status": "failed",
            "exit_code": EXIT_GLOBAL_FAILURE,
            "issues": [_make_issue(None, "missing_output_dir")],
        }
    output_dir_path = Path(str(output_dir))

    if secondaries is None:
        requested = _discover_secondaries(run_root_path)
    elif isinstance(secondaries, str):
        requested = _parse_secondaries(secondaries)
    else:
        # Normalize iterable input the same way --secondaries parses.
        seen: set[str] = set()
        requested = []
        for tok in secondaries:
            t = str(tok).strip().upper()
            if not t or t in seen:
                continue
            seen.add(t)
            requested.append(t)

    if not requested:
        return EXIT_ALL_SECONDARIES_FAILED, {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": _utc_iso(),
            "ranking_status": "failed",
            "exit_code": EXIT_ALL_SECONDARIES_FAILED,
            "issues": [_make_issue(None, "no_secondaries_resolved")],
        }

    run_issues: list[dict] = []
    run_manifest_doc, manifest_issue = _read_run_manifest(run_root_path)
    if manifest_issue is not None:
        run_issues.append(manifest_issue)

    valid_records: list[dict] = []
    failed_records: list[dict] = []
    per_sec_issues: list[dict] = []

    for sec in requested:
        record, issue = _process_secondary(sec, run_root_path)
        if record is not None:
            valid_records.append(record)
        else:
            if issue is not None:
                per_sec_issues.append(issue)
            failed_records.append({
                "rank": None,
                "secondary": sec,
                "processing_status": "failed",
                "trade_direction": None,
                "zero_capture_direction_default": False,
                "current_alignment_state": None,
                "members": [],
                "k6_metrics": None,
                "phase_e_status": {},
                "v1_sharpe": None,
                "v1_total_capture_pct": None,
                "v1_avg_capture_pct": None,
                "v1_stddev_pct": None,
                "v1_n": 0,
                "v1_win_count": None,
                "v1_loss_count": None,
                "v1_win_pct": None,
                "low_sample_warning": False,
                "ccc_series": [],
                "issues": [issue] if issue is not None else [],
            })

    if not valid_records:
        return EXIT_ALL_SECONDARIES_FAILED, {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": _utc_iso(),
            "ranking_status": "failed",
            "exit_code": EXIT_ALL_SECONDARIES_FAILED,
            "trafficflow_run_root": (
                path_for_output(
                    str(run_root_path), project_root=project_root,
                ) or str(run_root_path)
            ),
            "trafficflow_run_id": run_root_path.name,
            "secondaries_requested": list(requested),
            "secondaries_ranked": [],
            "per_secondary": [],
            "issues": per_sec_issues + run_issues,
        }

    ranked_in_display_order = _rank_records(valid_records)
    failed_records.sort(key=lambda r: str(r["secondary"]))

    secondaries_ranked = [
        r["secondary"] for r in ranked_in_display_order
        if r["processing_status"] == "ranked"
    ]

    ranking_status = "complete" if not failed_records else "partial"

    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_iso(),
        "ranking_status": ranking_status,
        "trafficflow_run_root": (
            path_for_output(
                str(run_root_path), project_root=project_root,
            ) or str(run_root_path)
        ),
        "trafficflow_run_id": run_root_path.name,
        "trafficflow_run_status": (
            (run_manifest_doc or {}).get("run_status")
            if run_manifest_doc is not None else None
        ),
        "trafficflow_orchestrator_invocation_id": (
            (run_manifest_doc or {}).get("orchestrator_invocation_id")
            if run_manifest_doc is not None else None
        ),
        "secondaries_requested": list(requested),
        "secondaries_ranked": secondaries_ranked,
        "per_secondary": ranked_in_display_order + failed_records,
        "issues": per_sec_issues + run_issues,
    }

    safe_artifact = sanitize_for_json(artifact, project_root=project_root)

    output_dir_path.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir_path / ARTIFACT_FILENAME
    try:
        _atomic_write_json(artifact_path, safe_artifact)
    except Exception as exc:
        tmp = artifact_path.with_name(artifact_path.name + ".tmp")
        for p in (tmp, artifact_path):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        return EXIT_GLOBAL_FAILURE, {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": _utc_iso(),
            "ranking_status": "failed",
            "exit_code": EXIT_GLOBAL_FAILURE,
            "issues": [_make_issue(None, "artifact_write_failed", repr(exc))],
        }

    return EXIT_OK, safe_artifact


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_GLOBAL_FAILURE
    secondaries = (
        _parse_secondaries(args.secondaries)
        if args.secondaries is not None else None
    )
    rc, payload = build_mvp_ranking_v1(
        run_root=args.run_root,
        output_dir=args.output_dir,
        secondaries=secondaries,
    )
    print(json.dumps(payload, indent=2, default=str))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
