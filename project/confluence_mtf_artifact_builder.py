"""Phase 6D-3: build Confluence research_day_v1 artifacts from
Phase 6D-2 multi-timeframe TrafficFlow / K-build outputs.

Closes the final pipeline stage opened by the Phase 6C-8
readiness contract: once Phase 6D-1 has materialized daily K
TrafficFlow artifacts and Phase 6D-2 has projected them onto
the canonical multi-timeframe set, this module aggregates the
``__K<K>__MTF.research_day.json`` artifacts into a single
``engine="confluence"`` artifact that the existing readiness
layer and Daily Signal Board can read directly. With this
PR applied, ``missing_confluence_day_artifact`` clears on
tickers where the full Phase 6D-1+6D-2 sweep has run; the
stale-confluence gate then follows the source data naturally
(no fake current dates).

Strictly read-only / offline:

  - No yfinance import.
  - No live engine import (trafficflow.py, confluence.py,
    spymaster.py, impactsearch.py, dash).
  - Writes a ``research_day_v1`` artifact ONLY when invoked
    with ``write=True``; the web tier never touches this
    module.

Input discovery
---------------

The builder accepts ONLY Phase 6D-2 MTF artifacts; filenames
must match the documented suffix convention:

    <seed_run_id>__K<K>__MTF.research_day.json

Legacy unsuffixed TrafficFlow artifacts and Phase 6D-1 daily
``__K<K>.research_day.json`` files are silently excluded
(PR #197 audit fix carried forward to Phase 6D-3). The
artifact's internal ``K`` value is verified against the
filename ``K`` suffix; mismatches surface
``input_artifact_k_mismatch`` and are not used.

Combine rule
------------

For each date, every saved MTF artifact contributes one vote
per timeframe stored in its ``timeframe_pressure_signals``
map. Each (K, timeframe) cell is normalized to one of:

    Buy / Short / None / missing

Aggregation per day:

  * ``buy_votes``     = count of Buy cells
  * ``short_votes``   = count of Short cells
  * ``none_votes``    = count of None cells
  * ``missing_votes`` = count of missing cells
  * ``active_count``  = buy_votes + short_votes
  * ``available_count`` = active_count + none_votes
                        (excludes missing - this is the
                        ``agreement_total`` slot count)
  * ``agreement_total`` = available_count
                        (Phase 6D-3 spec alias)

Final-signal rule (strict unanimity over active votes):

  * No active votes (buy_votes == short_votes == 0)
        -> confluence_signal = None, agreement_active = 0
  * All active Buy (short_votes == 0, buy_votes > 0)
        -> confluence_signal = Buy, agreement_active = buy_votes
  * All active Short (buy_votes == 0, short_votes > 0)
        -> confluence_signal = Short, agreement_active = short_votes
  * Mixed Buy + Short
        -> confluence_signal = None, agreement_active = 0

Output artifact
---------------

  * Schema: ``research_day_v1``.
  * Engine: ``confluence``.
  * Path: ``output/research_artifacts/confluence/<SAFE_TARGET>/<SAFE_TARGET>[__<safe_run_id>].research_day.json``
    via ``research_artifacts.artifact_path_for_confluence``.
  * Default ``run_id`` is ``mtf_consensus`` so the artifact
    sits at a stable known path. Callers can override.
  * ``timeframes`` carries the union of timeframes seen on
    the MTF sources.
  * ``last_date`` comes from the SOURCE artifact rows; the
    builder never stamps today's date onto a stale ticker.

Public surface
--------------

    ConfluenceBuildResult                                 # dataclass
    DEFAULT_EXPECTED_K                                    # tuple[int, ...]
    DEFAULT_EXPECTED_TIMEFRAMES                           # tuple[str, ...]
    DEFAULT_RUN_ID                                        # "mtf_consensus"
    DEFAULT_PERSIST_SKIP_BARS                             # 1
    MTF_FILENAME_RX                                       # re.Pattern
    PRESSURE_SIGNAL_BUY / SHORT / NONE / MISSING          # strings
    ISSUE_*                                               # strings

    artifact_run_id_for_mtf_consensus(seed_run_id=None,
                                      run_id=DEFAULT_RUN_ID)
        -> str
    list_mtf_trafficflow_artifacts(artifact_root, target)
        -> list[tuple[Path, int]]
    build_confluence_from_mtf_trafficflow(target, *,
        artifact_root=None, expected_k=DEFAULT_EXPECTED_K,
        expected_timeframes=DEFAULT_EXPECTED_TIMEFRAMES,
        run_id=DEFAULT_RUN_ID, write=False,
        research_as_of_date=None,
        persist_skip_bars=DEFAULT_PERSIST_SKIP_BARS)
        -> ConfluenceBuildResult
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import research_artifacts as _ra


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EXPECTED_K: tuple[int, ...] = tuple(range(1, 13))
DEFAULT_EXPECTED_TIMEFRAMES: tuple[str, ...] = (
    "1d", "1wk", "1mo", "3mo", "1y",
)
DEFAULT_RUN_ID = "mtf_consensus"
DEFAULT_PERSIST_SKIP_BARS = 1

PRESSURE_SIGNAL_BUY = "Buy"
PRESSURE_SIGNAL_SHORT = "Short"
PRESSURE_SIGNAL_NONE = "None"
PRESSURE_SIGNAL_MISSING = "missing"

# Matches the Phase 6D-2 multi-timeframe artifact filename:
# ``<seed_run_id>__K<digits>__MTF.research_day.json``. Phase
# 6D-1 daily-K files end in ``__K<digits>.research_day.json``
# (no ``__MTF``) and legacy unsuffixed TrafficFlow files end in
# just ``.research_day.json`` - neither matches this regex.
MTF_FILENAME_RX = re.compile(
    r"__K(\d+)__MTF\.research_day\.json$",
)

ISSUE_NO_MTF_TRAFFICFLOW_ARTIFACTS = "no_mtf_trafficflow_artifacts"
ISSUE_MISSING_MTF_K_COVERAGE = "missing_mtf_k_coverage"
ISSUE_INPUT_ARTIFACT_UNREADABLE = "input_artifact_unreadable"
ISSUE_INPUT_ARTIFACT_K_MISMATCH = "input_artifact_k_mismatch"
ISSUE_PARTIAL_TIMEFRAME_COVERAGE = "partial_timeframe_coverage"
ISSUE_NO_USABLE_ROWS = "no_usable_rows"
ISSUE_ARTIFACT_WRITE_FAILED = "artifact_write_failed"

ARTIFACT_VERSION = "research_day_v1"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class ConfluenceBuildResult:
    """Outcome of a single ``build_confluence_from_mtf_trafficflow``
    invocation.

    ``attempted_k`` records the K values found across the saved
    MTF inputs (intersected with ``expected_k``). ``built`` is
    True when a Confluence artifact was produced (and persisted
    when ``write=True``). ``artifact_path`` is set only on a
    successful write. ``issue_codes`` is the deduplicated list
    of stable issue strings raised during the build."""

    target: str
    attempted_k: tuple[int, ...]
    built: bool
    artifact_path: Optional[Path]
    issue_codes: tuple[str, ...]
    row_count: int
    last_date: Optional[str]
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _default_artifact_root() -> Path:
    return _project_dir() / "output" / "research_artifacts"


def _filename_safe_ticker(ticker: str) -> str:
    if not ticker:
        return ""
    s = str(ticker).strip().upper()
    if not s:
        return ""
    s = s.replace("^", "_")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
    return "".join(c if c in allowed else "_" for c in s)


def _engine_artifact_dir(
    artifact_root: Path, engine: str, ticker: str,
) -> Optional[Path]:
    if not artifact_root.exists() or not artifact_root.is_dir():
        return None
    base = artifact_root / engine
    if not base.exists() or not base.is_dir():
        return None
    safe = _filename_safe_ticker(ticker)
    real = str(ticker or "").strip().upper()
    for form in (real, safe):
        if not form:
            continue
        p = base / form
        if p.exists() and p.is_dir():
            return p
    return None


def _normalize_signal(value: Any) -> str:
    """Coerce an arbitrary cell into one of the four canonical
    strings. Anything we don't recognize collapses to
    ``missing`` so it stays out of every active count."""
    if value is None:
        return PRESSURE_SIGNAL_MISSING
    s = str(value).strip()
    if not s:
        return PRESSURE_SIGNAL_MISSING
    low = s.lower()
    if low == "buy":
        return PRESSURE_SIGNAL_BUY
    if low == "short":
        return PRESSURE_SIGNAL_SHORT
    if low == "none":
        return PRESSURE_SIGNAL_NONE
    return PRESSURE_SIGNAL_MISSING


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _safe_int(value: Any) -> Optional[int]:
    f = _safe_float(value)
    if f is None:
        return None
    return int(round(f))


def _append_unique(issues: list[str], code: str) -> None:
    if code and code not in issues:
        issues.append(code)


def artifact_run_id_for_mtf_consensus(
    seed_run_id: Optional[str] = None,
    run_id: str = DEFAULT_RUN_ID,
) -> str:
    """Return the run id used as the on-disk Confluence artifact
    identifier. By default returns ``"mtf_consensus"``; callers
    can pass a custom ``run_id`` or scope it to a specific seed
    run via ``seed_run_id``. Empty inputs collapse to the
    default so a missing operator-supplied id can't break the
    write path."""
    rid = str(run_id or DEFAULT_RUN_ID).strip()
    if not rid:
        rid = DEFAULT_RUN_ID
    if seed_run_id:
        return f"{rid}__from__{str(seed_run_id).strip()}"
    return rid


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------


def list_mtf_trafficflow_artifacts(
    artifact_root: Path, target: str,
) -> list[tuple[Path, int]]:
    """Return the Phase 6D-2 MTF artifact paths for the target
    as ``(path, K_from_filename)`` pairs.

    Filters strictly to filenames matching the Phase 6D-2
    convention; legacy unsuffixed TrafficFlow files and Phase
    6D-1 daily ``__K<K>.research_day.json`` files are silently
    excluded.
    """
    ticker_dir = _engine_artifact_dir(
        artifact_root, "trafficflow", target,
    )
    if ticker_dir is None:
        return []
    out: list[tuple[Path, int]] = []
    for p in sorted(ticker_dir.glob("*.research_day.json")):
        match = MTF_FILENAME_RX.search(p.name)
        if match is None:
            continue
        try:
            k_from_name = int(match.group(1))
        except (TypeError, ValueError):
            continue
        out.append((p, k_from_name))
    return out


# ---------------------------------------------------------------------------
# Daily-row aggregation
# ---------------------------------------------------------------------------


def _per_day_votes(
    mtf_inputs: Sequence[tuple[int, _ra.ResearchDayArtifact]],
    expected_timeframes: Sequence[str],
) -> tuple[
    list[str], dict[str, dict[str, Any]], list[str], list[int],
]:
    """Aggregate every (K, timeframe) cell across the given MTF
    artifacts onto a single per-day grid.

    Returns ``(dates_in_order, per_date_record, timeframes_seen,
    K_values_seen)``. ``per_date_record`` maps an ISO date string
    to a dict the row builder consumes."""
    timeframes_seen: list[str] = []
    K_values_seen: list[int] = []
    target_close_by_date: dict[str, float] = {}
    # cell_key -> {date -> raw signal value}
    cells: list[tuple[int, str, dict[str, str]]] = []

    for K, artifact in mtf_inputs:
        if isinstance(K, int) and K not in K_values_seen:
            K_values_seen.append(K)
        art_tfs = list(getattr(artifact, "timeframes", None) or [])
        for tf in art_tfs:
            if tf not in timeframes_seen:
                timeframes_seen.append(tf)
        per_tf_signals: dict[str, dict[str, str]] = {
            tf: {} for tf in art_tfs
        }
        for row in artifact.daily or []:
            if not isinstance(row, Mapping):
                continue
            d = row.get("date")
            if not d:
                continue
            date_iso = str(d)[:10]
            tc = _safe_float(row.get("target_close"))
            if tc is not None and date_iso not in target_close_by_date:
                target_close_by_date[date_iso] = tc
            tf_map = row.get("timeframe_pressure_signals") or {}
            if not isinstance(tf_map, Mapping):
                continue
            for tf in art_tfs:
                per_tf_signals[tf][date_iso] = _normalize_signal(
                    tf_map.get(tf),
                )
        for tf in art_tfs:
            cells.append((K, tf, per_tf_signals[tf]))

    # Preserve the order timeframes appeared in inputs (1d first,
    # etc.). When the expected list is provided, append any
    # missing timeframes the inputs did NOT carry so the row
    # record can mark them ``missing`` consistently.
    for tf in expected_timeframes:
        if tf not in timeframes_seen:
            timeframes_seen.append(tf)

    # Build the union of all dates encountered.
    date_set: set[str] = set()
    for _, _, daymap in cells:
        date_set.update(daymap.keys())
    dates_in_order = sorted(date_set)

    per_date_record: dict[str, dict[str, Any]] = {}
    for d in dates_in_order:
        buy = short = none = missing = 0
        per_cell: list[tuple[int, str, str]] = []
        for K, tf, daymap in cells:
            cell = daymap.get(d, PRESSURE_SIGNAL_MISSING)
            if cell == PRESSURE_SIGNAL_BUY:
                buy += 1
            elif cell == PRESSURE_SIGNAL_SHORT:
                short += 1
            elif cell == PRESSURE_SIGNAL_NONE:
                none += 1
            else:
                missing += 1
            per_cell.append((K, tf, cell))
        # Slots not represented by an input artifact at all
        # (expected timeframes that no MTF carried) are counted
        # as missing per the contract; they appear as additional
        # placeholders so audit tooling can detect the gap.
        for tf in expected_timeframes:
            if not any(c[1] == tf for c in cells):
                missing += 1
                per_cell.append((0, tf, PRESSURE_SIGNAL_MISSING))
        active_count = buy + short
        available_count = active_count + none
        per_date_record[d] = {
            "buy_votes": buy,
            "short_votes": short,
            "none_votes": none,
            "missing_votes": missing,
            "active_count": active_count,
            "available_count": available_count,
            "per_cell": per_cell,
            "target_close": target_close_by_date.get(d),
        }
    return dates_in_order, per_date_record, timeframes_seen, K_values_seen


def _final_signal(buy: int, short: int) -> tuple[str, int]:
    """Apply the strict-unanimity combine rule and return
    ``(confluence_signal, agreement_active)``."""
    if buy == 0 and short == 0:
        return PRESSURE_SIGNAL_NONE, 0
    if short == 0:
        return PRESSURE_SIGNAL_BUY, buy
    if buy == 0:
        return PRESSURE_SIGNAL_SHORT, short
    return PRESSURE_SIGNAL_NONE, 0


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_confluence_from_mtf_trafficflow(
    target: str,
    *,
    artifact_root: Optional[Path] = None,
    expected_k: Iterable[int] = DEFAULT_EXPECTED_K,
    expected_timeframes: Iterable[str] = DEFAULT_EXPECTED_TIMEFRAMES,
    run_id: str = DEFAULT_RUN_ID,
    write: bool = False,
    research_as_of_date: Optional[Any] = None,
    persist_skip_bars: Optional[int] = None,
) -> ConfluenceBuildResult:
    """Aggregate Phase 6D-2 MTF TrafficFlow artifacts for one
    ticker into a Confluence ``research_day_v1`` artifact.

    Read-only by default. ``write=True`` persists the artifact
    via ``research_artifacts.write_research_day_artifact`` at
    the canonical confluence path.

    The function never raises for missing / malformed inputs;
    every failure mode surfaces through
    ``ConfluenceBuildResult.issue_codes``. When the K coverage
    requirement is not met, the function refuses to write to
    avoid materializing a misleading partial Confluence row -
    the spec is explicit on this.

    ``research_as_of_date`` is accepted for telemetry parity
    with other Phase 6D builders but the resulting artifact's
    ``last_date`` ALWAYS comes from the source rows; the
    builder never stamps a fresh date onto a stale ticker.
    """
    t0 = time.perf_counter()
    artifact_d = (
        Path(artifact_root) if artifact_root is not None
        else _default_artifact_root()
    )
    expected_k_tuple = tuple(int(k) for k in expected_k)
    expected_tf_list = list(expected_timeframes)
    issues: list[str] = []

    if research_as_of_date is not None:
        # Telemetry only - resolved value isn't otherwise used.
        # Accept ISO strings or date objects defensively.
        try:
            datetime.strptime(
                (
                    research_as_of_date.isoformat()
                    if isinstance(research_as_of_date, date)
                    else str(research_as_of_date)
                )[:10],
                "%Y-%m-%d",
            )
        except Exception:
            pass

    paths = list_mtf_trafficflow_artifacts(artifact_d, target)
    if not paths:
        return ConfluenceBuildResult(
            target=target,
            attempted_k=(),
            built=False,
            artifact_path=None,
            issue_codes=(ISSUE_NO_MTF_TRAFFICFLOW_ARTIFACTS,),
            row_count=0,
            last_date=None,
            elapsed_seconds=time.perf_counter() - t0,
        )

    mtf_inputs: list[tuple[int, _ra.ResearchDayArtifact]] = []
    seen_k: set[int] = set()
    wanted = set(expected_k_tuple)
    for path, k_from_name in paths:
        try:
            art = _ra.read_research_day_artifact(path)
        except Exception:
            art = None
        if art is None:
            _append_unique(issues, ISSUE_INPUT_ARTIFACT_UNREADABLE)
            continue
        K = art.K
        if not isinstance(K, int) or K != k_from_name:
            _append_unique(issues, ISSUE_INPUT_ARTIFACT_K_MISMATCH)
            continue
        if wanted and K not in wanted:
            continue
        if K in seen_k:
            # Duplicate seed-run for same K - first wins.
            continue
        seen_k.add(K)
        mtf_inputs.append((K, art))

    attempted_k = tuple(sorted(seen_k))
    if wanted and not wanted.issubset(seen_k):
        _append_unique(issues, ISSUE_MISSING_MTF_K_COVERAGE)
        return ConfluenceBuildResult(
            target=target,
            attempted_k=attempted_k,
            built=False,
            artifact_path=None,
            issue_codes=tuple(issues),
            row_count=0,
            last_date=None,
            elapsed_seconds=time.perf_counter() - t0,
        )

    # Partial timeframe coverage is a soft signal: warn but
    # continue. The aggregation marks missing cells per row so
    # the final agreement counts stay honest.
    seen_tfs: set[str] = set()
    for _, art in mtf_inputs:
        for tf in (getattr(art, "timeframes", None) or []):
            seen_tfs.add(tf)
    if not set(expected_tf_list).issubset(seen_tfs):
        _append_unique(issues, ISSUE_PARTIAL_TIMEFRAME_COVERAGE)

    dates_in_order, per_date_record, _tfs_observed, K_values_seen = (
        _per_day_votes(mtf_inputs, expected_tf_list)
    )
    if not dates_in_order:
        _append_unique(issues, ISSUE_NO_USABLE_ROWS)
        return ConfluenceBuildResult(
            target=target,
            attempted_k=attempted_k,
            built=False,
            artifact_path=None,
            issue_codes=tuple(issues),
            row_count=0,
            last_date=None,
            elapsed_seconds=time.perf_counter() - t0,
        )

    # Compute per-day capture math.
    target_close_series: list[Optional[float]] = []
    for d in dates_in_order:
        target_close_series.append(
            per_date_record[d].get("target_close"),
        )
    target_return_series: list[float] = [0.0] * len(dates_in_order)
    for i in range(1, len(dates_in_order)):
        prev = target_close_series[i - 1]
        curr = target_close_series[i]
        if prev is None or curr is None or prev == 0.0:
            target_return_series[i] = 0.0
            continue
        target_return_series[i] = (curr - prev) / prev * 100.0

    # Build daily rows.
    source_run_ids = sorted({
        str(a.run_id or "") for _, a in mtf_inputs if a.run_id
    })
    rows: list[dict[str, Any]] = []
    for i, d in enumerate(dates_in_order):
        rec = per_date_record[d]
        signal, agreement_active = _final_signal(
            rec["buy_votes"], rec["short_votes"],
        )
        signal_value = (
            1 if signal == PRESSURE_SIGNAL_BUY
            else -1 if signal == PRESSURE_SIGNAL_SHORT
            else 0
        )
        ret = target_return_series[i]
        daily_capture = (
            ret if signal == PRESSURE_SIGNAL_BUY
            else -ret if signal == PRESSURE_SIGNAL_SHORT
            else 0.0
        )
        rows.append({
            "date": d,
            "target": target,
            "target_ticker": target,
            "target_close": rec.get("target_close"),
            "target_return_pct": ret,
            "confluence_signal": signal,
            "signal": signal,
            "signal_value": signal_value,
            "agreement_active": int(agreement_active),
            "agreement_total": int(rec["available_count"]),
            "active_count": int(rec["active_count"]),
            "available_count": int(rec["available_count"]),
            "buy_votes": int(rec["buy_votes"]),
            "short_votes": int(rec["short_votes"]),
            "none_votes": int(rec["none_votes"]),
            "missing_votes": int(rec["missing_votes"]),
            "K_values": list(sorted(K_values_seen)),
            "timeframes": list(expected_tf_list),
            "source_trafficflow_mtf_run_ids": list(source_run_ids),
            "daily_capture_pct": daily_capture,
            "is_trigger_day": signal in (
                PRESSURE_SIGNAL_BUY, PRESSURE_SIGNAL_SHORT,
            ),
        })

    # T-1 persist skip + cumulative capture.
    skip = (
        DEFAULT_PERSIST_SKIP_BARS if persist_skip_bars is None
        else int(persist_skip_bars)
    )
    rows_trim = rows[:-skip] if (skip and skip > 0 and len(rows) > skip) else rows
    cum = 0.0
    for r in rows_trim:
        cum += float(r.get("daily_capture_pct") or 0.0)
        r["cumulative_capture_pct"] = cum
    for r in rows[len(rows_trim):]:
        r.setdefault("cumulative_capture_pct", None)

    # Summary stats over the trimmed rows.
    trigger_caps = [
        float(r["daily_capture_pct"]) for r in rows_trim
        if r["is_trigger_day"]
    ]
    n_trigger = len(trigger_caps)
    if n_trigger > 0:
        total_capture_pct = sum(trigger_caps)
        avg = total_capture_pct / n_trigger
        wins = sum(1 for v in trigger_caps if v > 0)
        losses = sum(1 for v in trigger_caps if v < 0)
    else:
        total_capture_pct = 0.0
        avg = 0.0
        wins = 0
        losses = 0
    if n_trigger > 1:
        mean = sum(trigger_caps) / n_trigger
        var = sum((v - mean) ** 2 for v in trigger_caps) / (n_trigger - 1)
        std_dev = math.sqrt(var) if var > 0 else 0.0
        sharpe = (avg / std_dev) if std_dev > 0 else 0.0
    else:
        sharpe = 0.0

    summary = {
        "total_capture_pct": float(total_capture_pct),
        "avg_daily_capture_pct": float(avg),
        "sharpe_ratio": float(sharpe),
        "trigger_days": int(n_trigger),
        "wins": int(wins),
        "losses": int(losses),
        "p_value": None,
        "significant_95": None,
        "tier_counts": {},
    }

    last_date = (
        rows[-1].get("date") if rows else None
    )

    artifact_out = _ra.ResearchDayArtifact(
        artifact_version=ARTIFACT_VERSION,
        engine="confluence",
        target_ticker=str(target).strip().upper(),
        signal_source="",
        run_id=artifact_run_id_for_mtf_consensus(
            seed_run_id=None, run_id=run_id,
        ),
        metric_basis="Close",
        persist_skip_bars=int(skip),
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        summary=summary,
        daily=rows,
        timeframes=list(expected_tf_list),
        min_active=2,
    )

    if not write:
        return ConfluenceBuildResult(
            target=target,
            attempted_k=attempted_k,
            built=True,
            artifact_path=None,
            issue_codes=tuple(issues),
            row_count=len(rows),
            last_date=last_date,
            elapsed_seconds=time.perf_counter() - t0,
        )

    out_path = _ra.artifact_path_for_confluence(
        target,
        run_id=artifact_run_id_for_mtf_consensus(
            seed_run_id=None, run_id=run_id,
        ),
        base_dir=artifact_d,
    )
    if out_path is None:
        _append_unique(issues, ISSUE_ARTIFACT_WRITE_FAILED)
        return ConfluenceBuildResult(
            target=target,
            attempted_k=attempted_k,
            built=False,
            artifact_path=None,
            issue_codes=tuple(issues),
            row_count=len(rows),
            last_date=last_date,
            elapsed_seconds=time.perf_counter() - t0,
        )
    try:
        written = _ra.write_research_day_artifact(
            artifact_out, out_path,
        )
    except Exception:
        _append_unique(issues, ISSUE_ARTIFACT_WRITE_FAILED)
        return ConfluenceBuildResult(
            target=target,
            attempted_k=attempted_k,
            built=False,
            artifact_path=None,
            issue_codes=tuple(issues),
            row_count=len(rows),
            last_date=last_date,
            elapsed_seconds=time.perf_counter() - t0,
        )

    return ConfluenceBuildResult(
        target=target,
        attempted_k=attempted_k,
        built=True,
        artifact_path=Path(written),
        issue_codes=tuple(issues),
        row_count=len(rows),
        last_date=last_date,
        elapsed_seconds=time.perf_counter() - t0,
    )
