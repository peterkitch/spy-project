#!/usr/bin/env python3
"""K=6 MTF ranking engine.

Consumes per-secondary ``k6_mtf_history_v1`` artifacts emitted by
``k6_mtf_history_producer.py`` and produces one top-level
``k6_mtf_ranking_v1`` artifact at
``<run-dir>/k6_mtf_ranking.json``.

Design authority:
``md_library/shared/2026-05-27_K6_MTF_LAUNCH_PATH_CONTRACT.md``,
sections: Match Rule, Trade Direction, Capture, Honest Sharpe, CCC
Time Series, Ranking, Artifact Contracts (Ranking Artifact /
``k6_mtf_ranking_v1``), Fail-Closed Behavior.

Strict input boundary: at ranking runtime, this module reads
**only** ``k6_mtf_history.json`` artifacts from the supplied run
directory. It does not open local Spymaster caches, member signal
libraries, secondary-own signal libraries, vendor data, or any
outputs from TrafficFlow / StackBuilder / OnePass-MTF surfaces.
The history artifact is the stable scoring boundary.

Per-secondary scoring flow:

  1. Validate the loaded artifact schema.
  2. ``current_snapshot = bars[-1].snapshot``; candidate bars are
     ``bars[0]`` through ``bars[n-2]``.
  3. For each candidate bar, apply the wildcard match rule
     (current ``BUY`` allows candidate ``BUY`` / ``NONE`` /
     ``UNAVAILABLE``; current ``SHORT`` allows candidate
     ``SHORT`` / ``NONE`` / ``UNAVAILABLE``; current
     ``NONE`` / ``UNAVAILABLE`` allows anything; all five slots
     must pass).
  4. If matched, take ``trade_direction`` from the candidate bar's
     own ``1d`` slot (BUY / SHORT / no-trade). The current snapshot
     does NOT control direction.
  5. Compute capture from
     ``raw_return_pct = (bars[i+1].secondary_close /
     bars[i].secondary_close - 1.0) * 100``. ``BUY`` capture is the
     raw return, ``SHORT`` is its negative, no-trade is ``0.0``. If
     either close is missing/non-finite/non-positive, the bar is
     skipped (counted in ``match_count`` and
     ``skipped_capture_count`` but excluded from metrics and CCC).
  6. Compute honest Sharpe over capture-count bars only:
     ``(avg / stddev) * sqrt(252)`` with ddof=1 sample stddev.
     Undefined Sharpe is null (never 0.0).
  7. ``low_sample_warning = capture_count < 30``.

Ranking: numeric-Sharpe records sorted by Sharpe desc, total
capture desc, secondary alphabetical. Null-Sharpe records sort
below all numeric-Sharpe records. Failed records appear in
``per_secondary`` with ``rank = null`` and are excluded from
``secondaries_ranked``.

Importing this module has no side effects.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RANKING_SCHEMA_VERSION = "k6_mtf_ranking_v1"
HISTORY_SCHEMA_VERSION = "k6_mtf_history_v1"

TIMEFRAMES: Tuple[str, ...] = ("1d", "1wk", "1mo", "3mo", "1y")

SIGNAL_BUY = "BUY"
SIGNAL_SHORT = "SHORT"
SIGNAL_NONE = "NONE"
SIGNAL_UNAVAILABLE = "UNAVAILABLE"

WILDCARD_SIGNAL_VALUES = frozenset({SIGNAL_NONE, SIGNAL_UNAVAILABLE})

TRADE_DIRECTION_BUY = "BUY"
TRADE_DIRECTION_SHORT = "SHORT"
TRADE_DIRECTION_NONE = "NONE"  # no-trade bar

TRADING_DAYS_PER_YEAR = 252
LOW_SAMPLE_THRESHOLD = 30

STATUS_RANKED = "ranked"
STATUS_UNRANKED = "unranked"
STATUS_FAILED = "failed"

HISTORY_ARTIFACT_FILENAME = "k6_mtf_history.json"
RANKING_ARTIFACT_FILENAME = "k6_mtf_ranking.json"


# ---------------------------------------------------------------------------
# Internal helpers (mirrored, not imported, from mvp_ranking_v1)
# ---------------------------------------------------------------------------


def _safe_positive_close(value: Any) -> Optional[float]:
    """Return ``float(value)`` only when ``value`` is numeric, finite,
    and positive. Booleans (``True`` / ``False``) are rejected even
    though they are ``int`` subclasses in Python; close prices are
    not boolean.
    """
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


def _sample_stddev(values: Sequence[float]) -> float:
    """Sample standard deviation (ddof=1). Returns 0.0 for n<2 as a
    defensive default; callers must check n<2 before invoking this
    helper when an undefined-stddev signal is required."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(var)


def _bar_matches_alignment(
    candidate_snapshot: Any,
    current_snapshot: Dict[str, str],
) -> bool:
    """Apply the contract's wildcard match rule slot by slot.

    - If the current slot is ``BUY``: candidate slot passes when
      it is ``BUY``, ``NONE``, or ``UNAVAILABLE``.
    - If the current slot is ``SHORT``: candidate slot passes when
      it is ``SHORT``, ``NONE``, or ``UNAVAILABLE``.
    - If the current slot is ``NONE`` or ``UNAVAILABLE``: the slot
      is unconstrained.
    - All five slots must pass.

    ``NONE`` and ``UNAVAILABLE`` are both wildcards on both sides.
    """
    if not isinstance(candidate_snapshot, dict):
        return False
    for tf in TIMEFRAMES:
        cur = current_snapshot.get(tf)
        cand = candidate_snapshot.get(tf)
        if cur in WILDCARD_SIGNAL_VALUES:
            continue
        if cand in WILDCARD_SIGNAL_VALUES:
            continue
        if cand != cur:
            return False
    return True


def _candidate_trade_direction(candidate_snapshot: Dict[str, str]) -> str:
    """Return the trade direction from the candidate bar's own 1d slot.

    ``1d == BUY`` -> ``BUY``; ``1d == SHORT`` -> ``SHORT``;
    everything else -> ``NONE`` (no-trade). The current snapshot
    does NOT influence direction.
    """
    one_d = candidate_snapshot.get("1d")
    if one_d == SIGNAL_BUY:
        return TRADE_DIRECTION_BUY
    if one_d == SIGNAL_SHORT:
        return TRADE_DIRECTION_SHORT
    return TRADE_DIRECTION_NONE


# ---------------------------------------------------------------------------
# Artifact validation
# ---------------------------------------------------------------------------


_REQUIRED_HISTORY_TOP_LEVEL = (
    "schema_version", "generated_at_utc", "run_id", "secondary",
    "history_as_of_date", "source_paths", "k6_stack",
    "timeframe_set", "bars", "issues",
)
_REQUIRED_BAR_FIELDS = ("date_utc", "secondary_close", "snapshot")


def _validate_history_artifact(artifact: Any) -> Optional[str]:
    """Return ``None`` if the artifact passes structural validation.
    Otherwise return a string describing the first reason the
    artifact is unusable. Used to produce ``failed`` per-secondary
    records."""
    if not isinstance(artifact, dict):
        return f"artifact is not a dict (got {type(artifact).__name__})"
    if artifact.get("schema_version") != HISTORY_SCHEMA_VERSION:
        return (
            f"schema_version mismatch: expected "
            f"{HISTORY_SCHEMA_VERSION!r}, got "
            f"{artifact.get('schema_version')!r}"
        )
    for field in _REQUIRED_HISTORY_TOP_LEVEL:
        if field not in artifact:
            return f"missing required top-level field {field!r}"
    bars = artifact.get("bars")
    if not isinstance(bars, list) or len(bars) == 0:
        return f"bars is empty or not a list (got {type(bars).__name__})"

    # Every bar in the artifact must be schema-shaped. The k6_mtf_history_v1
    # contract requires every bar to carry date_utc, secondary_close, and a
    # snapshot dict with all five canonical timeframe keys. We do NOT
    # validate close positivity here because matched bars with invalid
    # current or next close are handled by skipped_capture_count downstream;
    # this validator covers required-field presence and snapshot shape only.
    for i, bar in enumerate(bars):
        if not isinstance(bar, dict):
            return (
                f"bars[{i}] is not a dict "
                f"(got {type(bar).__name__})"
            )
        for required in _REQUIRED_BAR_FIELDS:
            if required not in bar:
                return (
                    f"bars[{i}] missing required field {required!r}"
                )
        snapshot = bar["snapshot"]
        if not isinstance(snapshot, dict):
            return (
                f"bars[{i}] snapshot is not a dict "
                f"(got {type(snapshot).__name__})"
            )
        for tf in TIMEFRAMES:
            if tf not in snapshot:
                return (
                    f"bars[{i}] snapshot missing timeframe {tf!r}"
                )
    return None


# ---------------------------------------------------------------------------
# Per-secondary scoring (pure)
# ---------------------------------------------------------------------------


def score_history_artifact(
    artifact: Dict[str, Any],
    *,
    history_artifact_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Score one already-loaded history artifact into a per-secondary
    ranking record.

    Always returns a dict carrying every per-secondary required field
    plus ``issues``. The ``status`` field is one of:

    - ``ranked`` if Sharpe is numeric and the record is input-valid;
    - ``unranked`` if Sharpe is null but the record is input-valid;
    - ``failed`` if validation rejected the artifact (most metric
      fields are ``None`` in that case).

    A failed record still includes the artifact path (if known) and
    the ``secondary`` field (if extractable) so it appears in
    ``per_secondary``. The caller assigns the ``rank`` integer for
    ranked records.
    """
    issues: List[Dict[str, Any]] = []

    secondary = (
        artifact.get("secondary")
        if isinstance(artifact, dict)
        else None
    )

    validation_error = _validate_history_artifact(artifact)
    if validation_error is not None:
        issues.append({
            "code": "history_artifact_invalid",
            "message": validation_error,
        })
        return _failed_record(
            secondary=secondary or "",
            history_artifact_path=history_artifact_path or "",
            issues=issues,
        )

    bars: List[Dict[str, Any]] = artifact["bars"]
    n = len(bars)
    current_snapshot = bars[-1]["snapshot"]
    history_as_of_date = artifact.get("history_as_of_date", "")
    k6_stack = artifact.get("k6_stack", {})

    if n < 2:
        # Cannot produce captures: there is no candidate bar.
        issues.append({
            "code": "history_too_short",
            "message": (
                f"history has {n} bar(s); need at least 2 to score"
            ),
        })
        return _unranked_record(
            secondary=secondary or "",
            history_artifact_path=history_artifact_path or "",
            history_as_of_date=history_as_of_date,
            current_snapshot=_normalize_snapshot(current_snapshot),
            k6_stack=k6_stack,
            match_count=0, capture_count=0,
            trade_count=0, no_trade_count=0,
            skipped_capture_count=0,
            captures=[], dates=[], directions=[],
            issues=issues,
        )

    captures: List[float] = []
    dates: List[str] = []
    directions: List[str] = []
    match_count = 0
    capture_count = 0
    trade_count = 0
    no_trade_count = 0
    skipped_capture_count = 0

    # Walk bars[0] .. bars[n-2]. bars[-1] is the current-snapshot bar
    # and is never a candidate. _validate_history_artifact above has
    # already guaranteed every bar is a dict with the required fields
    # and a snapshot containing all five canonical timeframe keys, so
    # the scoring loop can assume that schema is intact.
    for i in range(n - 1):
        cand = bars[i]
        nxt = bars[i + 1]
        cand_snapshot = cand["snapshot"]
        if not _bar_matches_alignment(cand_snapshot, current_snapshot):
            continue
        match_count += 1
        cur_close = _safe_positive_close(cand.get("secondary_close"))
        nxt_close = _safe_positive_close(nxt.get("secondary_close"))
        if cur_close is None or nxt_close is None:
            skipped_capture_count += 1
            issues.append({
                "code": "capture_skipped_invalid_close",
                "message": (
                    f"bar {cand.get('date_utc')}: missing/invalid "
                    f"current or next secondary_close "
                    f"(current={cand.get('secondary_close')!r}, "
                    f"next={nxt.get('secondary_close')!r})"
                ),
            })
            continue
        raw_return_pct = (nxt_close / cur_close - 1.0) * 100.0
        direction = _candidate_trade_direction(cand_snapshot)
        if direction == TRADE_DIRECTION_BUY:
            capture = raw_return_pct
            trade_count += 1
        elif direction == TRADE_DIRECTION_SHORT:
            capture = -raw_return_pct
            trade_count += 1
        else:
            capture = 0.0
            no_trade_count += 1
        capture_count += 1
        captures.append(capture)
        dates.append(str(cand.get("date_utc")))
        directions.append(direction)

    # Defensive count-invariant check; should always hold.
    assert match_count == capture_count + skipped_capture_count
    assert capture_count == trade_count + no_trade_count

    metrics = _compute_metrics(captures)
    if metrics["sharpe_k6_mtf"] is None and capture_count > 0:
        issues.append({
            "code": "sharpe_undefined",
            "message": metrics.get(
                "sharpe_undefined_reason", "undefined",
            ),
        })

    ccc_series = _compute_ccc_series(captures, dates, directions)

    status = (
        STATUS_RANKED
        if metrics["sharpe_k6_mtf"] is not None
        else STATUS_UNRANKED
    )

    record: Dict[str, Any] = {
        "secondary": secondary,
        "rank": None,  # caller assigns for ranked records
        "status": status,
        "history_artifact_path": history_artifact_path or "",
        "history_as_of_date": history_as_of_date,
        "current_snapshot": _normalize_snapshot(current_snapshot),
        "k6_stack": k6_stack,
        "sharpe_k6_mtf": metrics["sharpe_k6_mtf"],
        "total_capture_pct": metrics["total_capture_pct"],
        "avg_capture_pct": metrics["avg_capture_pct"],
        "stddev_pct": metrics["stddev_pct"],
        "match_count": match_count,
        "capture_count": capture_count,
        "trade_count": trade_count,
        "no_trade_count": no_trade_count,
        "skipped_capture_count": skipped_capture_count,
        "win_count": metrics["win_count"],
        "loss_count": metrics["loss_count"],
        "win_pct": metrics["win_pct"],
        "low_sample_warning": capture_count < LOW_SAMPLE_THRESHOLD,
        "ccc_series": ccc_series,
        "issues": issues,
    }
    return record


def _normalize_snapshot(snapshot: Any) -> Dict[str, str]:
    """Coerce a snapshot dict to a copy that carries exactly the five
    canonical timeframe keys."""
    out: Dict[str, str] = {}
    if isinstance(snapshot, dict):
        for tf in TIMEFRAMES:
            value = snapshot.get(tf)
            if isinstance(value, str):
                out[tf] = value
            else:
                out[tf] = SIGNAL_UNAVAILABLE
    else:
        for tf in TIMEFRAMES:
            out[tf] = SIGNAL_UNAVAILABLE
    return out


def _compute_metrics(captures: List[float]) -> Dict[str, Any]:
    """Compute honest-Sharpe metrics over the per-bar capture list.

    Returns a dict with keys ``total_capture_pct``,
    ``avg_capture_pct``, ``stddev_pct``, ``sharpe_k6_mtf``,
    ``win_count``, ``loss_count``, ``win_pct``,
    ``sharpe_undefined_reason``. Undefined Sharpe is ``None`` (never
    ``0.0``).
    """
    n = len(captures)
    out: Dict[str, Any] = {
        "total_capture_pct": None,
        "avg_capture_pct": None,
        "stddev_pct": None,
        "sharpe_k6_mtf": None,
        "win_count": None,
        "loss_count": None,
        "win_pct": None,
        "sharpe_undefined_reason": None,
    }
    if n == 0:
        out["sharpe_undefined_reason"] = "no_captures"
        return out
    total = sum(captures)
    avg = total / n
    out["total_capture_pct"] = total
    out["avg_capture_pct"] = avg
    out["win_count"] = sum(1 for c in captures if c > 0)
    out["loss_count"] = sum(1 for c in captures if c < 0)
    out["win_pct"] = out["win_count"] / n * 100.0
    if n < 2:
        out["sharpe_undefined_reason"] = "n_less_than_two"
        return out
    stddev = _sample_stddev(captures)
    out["stddev_pct"] = stddev
    if stddev == 0.0:
        out["sharpe_undefined_reason"] = "stddev_zero"
        return out
    out["sharpe_k6_mtf"] = (
        (avg / stddev) * math.sqrt(TRADING_DAYS_PER_YEAR)
    )
    return out


def _compute_ccc_series(
    captures: Sequence[float],
    dates: Sequence[str],
    directions: Sequence[str],
) -> List[Dict[str, Any]]:
    """Cumulative capture series over the captureable bars in their
    chronological order. No-trade 0.0 bars appear in the series as
    flat segments. Skipped bars are absent."""
    out: List[Dict[str, Any]] = []
    running = 0.0
    for d, c, direction in zip(dates, captures, directions):
        running += c
        out.append({
            "date_utc": d,
            "cumulative_capture_pct": running,
            "per_bar_capture_pct": c,
            "trade_direction": direction,
        })
    return out


def _failed_record(
    *,
    secondary: str,
    history_artifact_path: str,
    issues: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Per-secondary record for a malformed/missing artifact."""
    return {
        "secondary": secondary,
        "rank": None,
        "status": STATUS_FAILED,
        "history_artifact_path": history_artifact_path,
        "history_as_of_date": None,
        "current_snapshot": None,
        "k6_stack": None,
        "sharpe_k6_mtf": None,
        "total_capture_pct": None,
        "avg_capture_pct": None,
        "stddev_pct": None,
        "match_count": 0,
        "capture_count": 0,
        "trade_count": 0,
        "no_trade_count": 0,
        "skipped_capture_count": 0,
        "win_count": None,
        "loss_count": None,
        "win_pct": None,
        "low_sample_warning": True,
        "ccc_series": [],
        "issues": issues,
    }


def _unranked_record(
    *,
    secondary: str,
    history_artifact_path: str,
    history_as_of_date: str,
    current_snapshot: Dict[str, str],
    k6_stack: Any,
    match_count: int,
    capture_count: int,
    trade_count: int,
    no_trade_count: int,
    skipped_capture_count: int,
    captures: List[float],
    dates: List[str],
    directions: List[str],
    issues: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Per-secondary record for an input-valid artifact that produces
    no scoreable captures (e.g. ``capture_count < 2``)."""
    metrics = _compute_metrics(captures)
    return {
        "secondary": secondary,
        "rank": None,
        "status": STATUS_UNRANKED,
        "history_artifact_path": history_artifact_path,
        "history_as_of_date": history_as_of_date,
        "current_snapshot": current_snapshot,
        "k6_stack": k6_stack,
        "sharpe_k6_mtf": None,
        "total_capture_pct": metrics["total_capture_pct"],
        "avg_capture_pct": metrics["avg_capture_pct"],
        "stddev_pct": metrics["stddev_pct"],
        "match_count": match_count,
        "capture_count": capture_count,
        "trade_count": trade_count,
        "no_trade_count": no_trade_count,
        "skipped_capture_count": skipped_capture_count,
        "win_count": metrics["win_count"],
        "loss_count": metrics["loss_count"],
        "win_pct": metrics["win_pct"],
        "low_sample_warning": capture_count < LOW_SAMPLE_THRESHOLD,
        "ccc_series": _compute_ccc_series(captures, dates, directions),
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Disk I/O on history artifacts
# ---------------------------------------------------------------------------


def load_and_score(path: Any) -> Dict[str, Any]:
    """Load a history artifact from ``path`` and score it. Always
    returns a per-secondary record (failed records on load /
    validation errors). The runtime input boundary stays at
    ``k6_mtf_history.json``."""
    p = Path(path)
    rel = str(p).replace("\\", "/")
    if not p.exists():
        return _failed_record(
            secondary=_secondary_from_path(p),
            history_artifact_path=rel,
            issues=[{
                "code": "history_artifact_missing",
                "message": f"history artifact not found: {rel}",
            }],
        )
    try:
        artifact = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return _failed_record(
            secondary=_secondary_from_path(p),
            history_artifact_path=rel,
            issues=[{
                "code": "history_artifact_unreadable",
                "message": f"failed to load {rel}: {exc!r}",
            }],
        )
    record = score_history_artifact(
        artifact, history_artifact_path=rel,
    )
    if not record.get("secondary"):
        # Fallback secondary from the path's parent dir name when the
        # artifact was malformed and did not carry a secondary field.
        record["secondary"] = _secondary_from_path(p)
    return record


def _secondary_from_path(p: Path) -> str:
    """Extract the secondary name from a path like
    ``output/k6_mtf/<RUN>/<SEC>/k6_mtf_history.json``. Returns the
    parent directory name; empty string if not determinable."""
    try:
        return p.parent.name
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Multi-secondary runner
# ---------------------------------------------------------------------------


def _discover_secondaries(run_dir: Path) -> List[str]:
    """Discover secondaries under a run directory by listing
    sub-directories that contain a ``k6_mtf_history.json``. Returns
    them sorted alphabetically."""
    found: List[str] = []
    if not run_dir.exists():
        return found
    for child in sorted(run_dir.iterdir()):
        if child.is_dir():
            if (child / HISTORY_ARTIFACT_FILENAME).exists():
                found.append(child.name)
    return found


def _rank_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort numeric-Sharpe records by Sharpe desc, total capture
    desc, secondary asc, then null-Sharpe records by secondary asc.
    Failed records are passed through unsorted by this helper; the
    caller appends them separately. Mutates ``rank`` on numeric-Sharpe
    records in place starting at 1."""
    numeric = [
        r for r in records
        if r["status"] == STATUS_RANKED
        and r.get("sharpe_k6_mtf") is not None
    ]
    nulls = [
        r for r in records
        if r["status"] == STATUS_UNRANKED
    ]
    numeric.sort(
        key=lambda r: (
            -float(r["sharpe_k6_mtf"]),
            -float(r.get("total_capture_pct") or 0.0),
            str(r.get("secondary") or ""),
        ),
    )
    for i, r in enumerate(numeric, 1):
        r["rank"] = i
    nulls.sort(key=lambda r: str(r.get("secondary") or ""))
    return numeric + nulls


def run(
    run_dir: Any,
    *,
    secondaries: Optional[Sequence[str]] = None,
    output_path: Optional[Any] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Score all requested (or discovered) secondaries under
    ``run_dir`` and write ``k6_mtf_ranking.json``.

    Returns a summary dict with ``ranking_artifact_path`` (or
    ``None`` when all-fail produced no artifact), the ranking
    artifact payload, and a failure list.
    """
    run_dir_path = Path(run_dir)
    if secondaries is None:
        secondaries_list = _discover_secondaries(run_dir_path)
    else:
        secondaries_list = list(secondaries)

    records: List[Dict[str, Any]] = []
    failed_records: List[Dict[str, Any]] = []
    for secondary in secondaries_list:
        history_path = (
            run_dir_path / secondary / HISTORY_ARTIFACT_FILENAME
        )
        rec = load_and_score(history_path)
        if not rec.get("secondary"):
            rec["secondary"] = secondary
        if rec["status"] == STATUS_FAILED:
            failed_records.append(rec)
        else:
            records.append(rec)

    ranked_and_unranked = _rank_records(records)
    # Failed records appended last with rank=None already set.
    per_secondary_ordered = ranked_and_unranked + failed_records
    secondaries_ranked = [
        r["secondary"] for r in ranked_and_unranked
        if r["status"] == STATUS_RANKED
    ]

    run_id_final = run_id or run_dir_path.name
    generated_at_utc = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(),
    )
    artifact: Dict[str, Any] = {
        "schema_version": RANKING_SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc,
        "run_id": run_id_final,
        "secondaries_requested": list(secondaries_list),
        "secondaries_ranked": secondaries_ranked,
        "per_secondary": per_secondary_ordered,
        "issues": [],
    }

    # All-fail policy: if every requested secondary failed validation
    # / load, do NOT write the ranking artifact.
    all_failed = (
        len(secondaries_list) > 0
        and len(failed_records) == len(secondaries_list)
    )
    if all_failed:
        artifact["issues"].append({
            "code": "all_secondaries_failed",
            "message": (
                f"{len(failed_records)} of {len(secondaries_list)} "
                f"secondaries failed; ranking artifact not written"
            ),
        })
        return {
            "ranking_artifact_path": None,
            "artifact": artifact,
            "failed_records": failed_records,
            "all_failed": True,
        }

    if output_path is None:
        out_path = run_dir_path / RANKING_ARTIFACT_FILENAME
    else:
        out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(artifact, indent=2, default=str),
        encoding="utf-8",
    )
    return {
        "ranking_artifact_path": str(out_path).replace("\\", "/"),
        "artifact": artifact,
        "failed_records": failed_records,
        "all_failed": False,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "K=6 MTF ranking engine. Scores per-secondary "
            "k6_mtf_history_v1 artifacts in a run directory and "
            "emits a single k6_mtf_ranking_v1 artifact."
        ),
    )
    parser.add_argument(
        "--run-dir", required=True,
        help=(
            "Run directory containing per-secondary "
            "<SEC>/k6_mtf_history.json artifacts."
        ),
    )
    parser.add_argument(
        "--secondaries", default=None,
        help=(
            "Optional comma-separated secondaries to score. "
            "Defaults to all secondaries discovered in --run-dir."
        ),
    )
    parser.add_argument(
        "--output-path", default=None,
        help=(
            "Optional output path for the ranking artifact. "
            "Defaults to <run-dir>/k6_mtf_ranking.json."
        ),
    )
    parser.add_argument(
        "--run-id", default=None,
        help="Optional run id override; defaults to <run-dir>.name.",
    )
    args = parser.parse_args(argv)

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )

    if args.secondaries:
        secondaries = [
            s.strip() for s in args.secondaries.split(",") if s.strip()
        ]
    else:
        secondaries = None

    summary = run(
        args.run_dir,
        secondaries=secondaries,
        output_path=args.output_path,
        run_id=args.run_id,
    )
    print(json.dumps({
        "ranking_artifact_path": summary["ranking_artifact_path"],
        "all_failed": summary["all_failed"],
        "secondaries_requested": summary["artifact"]["secondaries_requested"],
        "secondaries_ranked": summary["artifact"]["secondaries_ranked"],
        "failed_secondaries": [
            r["secondary"] for r in summary["failed_records"]
        ],
    }, indent=2))
    return 1 if summary["all_failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
