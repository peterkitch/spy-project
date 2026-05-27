"""Phase 3a: per-secondary MVP v1 history artifact writer.

Companion writer module invoked from the Phase E per-secondary
canonical-write path in ``trafficflow_runner.py``. Emits
``<RUN_ROOT>/<SEC>/v1_history.json`` per the MVP v1 History Artifact
Contract (``md_library/shared/2026-05-26_MVP_V1_HISTORY_ARTIFACT_CONTRACT.md``,
merged as PR #331).

This module is the only canonical producer of ``v1_history.json``;
discovery flows through the existing ``selected_output.json`` pointer,
so no parallel canonical path or new discovery pointer is introduced.

The writer reads:

  * Per-interval signal libraries under
    ``signal_library/data/stable/<TICKER>_stable_v1_0_0[_<INTERVAL>].pkl``.
  * Daily close prices under ``price_cache/daily/<TICKER>.{parquet,csv}``.

Both roots are overridable via function arguments (preferred) or
module-level constants (test seam). The writer never imports v1 ranking
math, v0 ranking engine code, Dash, confluence, OnePass, ImpactSearch,
or Spymaster modules.
"""

from __future__ import annotations

import csv
import math
import os
import pickle
from datetime import date as _date_cls
from datetime import datetime, timedelta, timezone
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

ARTIFACT_FILENAME = "v1_history.json"
SCHEMA_VERSION = "mvp_v1_history_v1"

TIMEFRAMES_COVERED: tuple[str, ...] = ("1d", "1wk", "1mo", "3mo", "1y")

SIGNAL_BUY = "BUY"
SIGNAL_SHORT = "SHORT"
SIGNAL_NONE = "NONE"
SIGNAL_UNAVAILABLE = "UNAVAILABLE"
ALLOWED_SIGNAL_VALUES = (
    SIGNAL_BUY, SIGNAL_SHORT, SIGNAL_NONE, SIGNAL_UNAVAILABLE,
)

DEFAULT_SIGNAL_LIBRARY_DIR = "signal_library/data/stable"
DEFAULT_PRICE_CACHE_DIR = "price_cache/daily"

SIGNAL_LIBRARY_VERSION_TAG = "stable_v1_0_0"

ISSUE_SIGNAL_LIBRARY_PARTIAL = "signal_library_partial_coverage"
ISSUE_SIGNAL_LIBRARY_MISSING = "signal_library_missing"
ISSUE_SIGNAL_ENCODING_UNRECOGNIZED = "signal_encoding_unrecognized"
ISSUE_PRICE_CACHE_GAP = "price_cache_gap"
ISSUE_PRICE_CACHE_MISSING = "price_cache_missing"
ISSUE_PRICE_CLOSE_UNUSABLE = "price_close_unusable"

ALLOWED_ISSUE_CODES = (
    ISSUE_SIGNAL_LIBRARY_PARTIAL,
    ISSUE_SIGNAL_LIBRARY_MISSING,
    ISSUE_SIGNAL_ENCODING_UNRECOGNIZED,
    ISSUE_PRICE_CACHE_GAP,
    ISSUE_PRICE_CACHE_MISSING,
    ISSUE_PRICE_CLOSE_UNUSABLE,
)

PRICE_CACHE_GAP_BUSINESS_DAYS_THRESHOLD = 5


# ---------------------------------------------------------------------------
# Path / filename helpers
# ---------------------------------------------------------------------------

def signal_library_filename(secondary: str, interval: str) -> str:
    if interval == "1d":
        return f"{secondary}_{SIGNAL_LIBRARY_VERSION_TAG}.pkl"
    return f"{secondary}_{SIGNAL_LIBRARY_VERSION_TAG}_{interval}.pkl"


def _price_cache_paths(secondary: str, price_cache_dir: str) -> tuple[Path, Path]:
    p = Path(price_cache_dir)
    return (p / f"{secondary}.parquet", p / f"{secondary}.csv")


# ---------------------------------------------------------------------------
# Date / signal normalization
# ---------------------------------------------------------------------------

class _Unrecognized(Exception):
    """Raised by :func:`_normalize_signal_value` for values that do not
    map to BUY / SHORT / NONE under the documented mapping."""


def _to_date_string(value: Any) -> str:
    """Coerce a date-like value to a ``YYYY-MM-DD`` string."""
    if value is None:
        return ""
    if isinstance(value, _date_cls) and not isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            pass
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return ""
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return s[:10]
    return str(value)[:10]


def _normalize_signal_value(raw: Any) -> str:
    """Map an upstream signal value to ``BUY`` / ``SHORT`` / ``NONE``.

    Raises :class:`_Unrecognized` for values that do not match the
    documented mapping (caller maps to ``UNAVAILABLE`` and records a
    ``signal_encoding_unrecognized`` issue).
    """
    if raw is None:
        return SIGNAL_NONE
    if isinstance(raw, bool):
        raise _Unrecognized(repr(raw))
    if isinstance(raw, (int, float)):
        try:
            f = float(raw)
        except (TypeError, ValueError):
            raise _Unrecognized(repr(raw))
        if math.isnan(f):
            return SIGNAL_NONE
        if f == 1.0:
            return SIGNAL_BUY
        if f == -1.0:
            return SIGNAL_SHORT
        if f == 0.0:
            return SIGNAL_NONE
        raise _Unrecognized(repr(raw))
    if isinstance(raw, str):
        s = raw.strip()
        u = s.upper()
        if u == "BUY":
            return SIGNAL_BUY
        if u == "SHORT":
            return SIGNAL_SHORT
        if u == "NONE" or s == "":
            return SIGNAL_NONE
    raise _Unrecognized(repr(raw))


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_signal_library(path: Path) -> Optional[tuple[list, list]]:
    """Load a pickled signal library. Returns ``(dates, signals)`` or
    ``None`` if the file does not exist. Raises on structural errors.
    """
    if not path.is_file():
        return None
    with open(path, "rb") as fh:
        lib = pickle.load(fh)
    if not isinstance(lib, dict):
        raise ValueError("signal_library_not_a_dict")
    dates = lib.get("dates")
    if dates is None:
        dates = lib.get("date_index")
    signals = lib.get("signals")
    if signals is None:
        signals = lib.get("primary_signals")
    if dates is None or signals is None:
        raise ValueError("signal_library_missing_dates_or_signals")
    dates_list = list(dates)
    signals_list = list(signals)
    if len(dates_list) != len(signals_list):
        raise ValueError("signal_library_dates_signals_length_mismatch")
    return dates_list, signals_list


def _load_price_cache(
    secondary: str, price_cache_dir: str
) -> Optional[list[tuple[str, Optional[float]]]]:
    """Load daily close prices for ``secondary``.

    Returns a list of ``(date_str, close_float_or_None)`` sorted
    ascending by ``date_str``, or ``None`` if no price cache file
    exists for the secondary. Raises on structural errors.
    """
    parquet_path, csv_path = _price_cache_paths(secondary, price_cache_dir)
    rows: list[tuple[Any, Any]] = []
    # Codex audit fix (Finding 3): canonical layout is parquet-first
    # with CSV as fallback. Prefer parquet whenever the file exists,
    # even if a CSV sibling is also present.
    if parquet_path.is_file():
        import pandas as pd  # type: ignore
        df = pd.read_parquet(parquet_path)
        if "Date" not in df.columns or "Close" not in df.columns:
            raise ValueError("price_cache_parquet_missing_date_or_close")
        for _, r in df.iterrows():
            rows.append((r["Date"], r["Close"]))
    elif csv_path.is_file():
        with open(csv_path, "r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames or []
            if "Date" not in fields or "Close" not in fields:
                raise ValueError("price_cache_csv_missing_date_or_close")
            for row in reader:
                rows.append((row.get("Date"), row.get("Close")))
    else:
        return None

    out: list[tuple[str, Optional[float]]] = []
    for d_raw, c_raw in rows:
        d_str = _to_date_string(d_raw)
        if not d_str:
            continue
        c_val: Optional[float]
        try:
            c_val = float(c_raw)
            if math.isnan(c_val) or math.isinf(c_val):
                c_val = None
        except (TypeError, ValueError):
            c_val = None
        out.append((d_str, c_val))
    out.sort(key=lambda x: x[0])
    return out


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def _forward_fill_signal_at(
    daily_date_str: str,
    library: Optional[tuple[list, list]],
    library_date_strs: Optional[list[str]] = None,
) -> tuple[str, bool, bool]:
    """For ``daily_date_str``, return ``(signal_value, recognized, covered)``.

    ``library`` is ``(dates, signals)`` or ``None``. ``library_date_strs``
    is the precomputed list of ``YYYY-MM-DD`` strings aligned with
    ``library[0]`` (passed by the caller to avoid recomputing on every
    bar).

    Semantics:
      * Library missing -> ``("UNAVAILABLE", True, False)``.
      * Date before library's first bar -> ``("UNAVAILABLE", True, False)``.
      * Otherwise: forward-fill (latest library bar at or before the
        date) and normalize. Unrecognized values map to
        ``("UNAVAILABLE", False, True)`` so the caller can record a
        ``signal_encoding_unrecognized`` issue.
    """
    if library is None:
        return SIGNAL_UNAVAILABLE, True, False
    dates, signals = library
    if library_date_strs is None:
        library_date_strs = [_to_date_string(d) for d in dates]
    if not library_date_strs:
        return SIGNAL_UNAVAILABLE, True, False
    if daily_date_str < library_date_strs[0]:
        return SIGNAL_UNAVAILABLE, True, False
    lo, hi = 0, len(library_date_strs) - 1
    target = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if library_date_strs[mid] <= daily_date_str:
            target = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if target < 0:
        return SIGNAL_UNAVAILABLE, True, False
    raw = signals[target]
    try:
        return _normalize_signal_value(raw), True, True
    except _Unrecognized:
        return SIGNAL_UNAVAILABLE, False, True


def _business_day_count_between(d1_str: str, d2_str: str) -> int:
    """Count weekday dates strictly between ``d1_str`` and ``d2_str``."""
    if d1_str >= d2_str:
        return 0
    d1 = _date_cls.fromisoformat(d1_str)
    d2 = _date_cls.fromisoformat(d2_str)
    cur = d1 + timedelta(days=1)
    count = 0
    while cur < d2:
        if cur.weekday() < 5:
            count += 1
        cur = cur + timedelta(days=1)
    return count


# ---------------------------------------------------------------------------
# Public callables
# ---------------------------------------------------------------------------

def build_v1_history_artifact(
    *,
    secondary: str,
    trafficflow_run_id: Optional[str],
    trafficflow_run_root: Optional[str],
    signal_library_dir: Optional[str] = None,
    price_cache_dir: Optional[str] = None,
    project_root: Optional[Path] = None,
    today_utc_override: Optional[str] = None,
) -> tuple[Optional[dict], list[dict], Optional[str]]:
    """Build the v1_history payload in memory.

    Returns ``(payload, issues, fatal_error_code)``.

    ``payload`` is the sanitized JSON dict ready to write, or ``None``
    when ``fatal_error_code`` is set. ``fatal_error_code`` is one of
    the documented issue codes signalling that no artifact may be
    emitted (e.g. ``price_cache_missing``); callers must surface this
    through the existing Phase E failure path rather than writing a
    partial artifact.

    ``issues`` is the cumulative issue list (the same one referenced
    in the payload).
    """
    if signal_library_dir is None:
        signal_library_dir = os.environ.get(
            "SIGNAL_LIBRARY_DIR", DEFAULT_SIGNAL_LIBRARY_DIR
        )
    if price_cache_dir is None:
        price_cache_dir = DEFAULT_PRICE_CACHE_DIR
    if project_root is None:
        project_root = Path.cwd()

    issues: list[dict] = []

    try:
        price_rows = _load_price_cache(secondary, price_cache_dir)
    except Exception as exc:
        return None, issues, ISSUE_PRICE_CACHE_MISSING
    if price_rows is None or len(price_rows) == 0:
        return None, issues, ISSUE_PRICE_CACHE_MISSING

    library_dir = Path(signal_library_dir)
    libraries: dict[str, Optional[tuple[list, list]]] = {}
    library_date_strs: dict[str, Optional[list[str]]] = {}
    library_first_date: dict[str, Optional[str]] = {}
    for tf in TIMEFRAMES_COVERED:
        lib_path = library_dir / signal_library_filename(secondary, tf)
        try:
            lib = _load_signal_library(lib_path)
        except Exception as exc:
            libraries[tf] = None
            library_date_strs[tf] = None
            library_first_date[tf] = None
            issues.append({
                "error_code": ISSUE_SIGNAL_LIBRARY_MISSING,
                "timeframe": tf,
                "message_sanitized": _scrub_embedded_absolute_paths(
                    f"signal_library_unreadable:{type(exc).__name__}"
                ),
            })
            continue
        libraries[tf] = lib
        if lib is None:
            library_date_strs[tf] = None
            library_first_date[tf] = None
            issues.append({
                "error_code": ISSUE_SIGNAL_LIBRARY_MISSING,
                "timeframe": tf,
                "message_sanitized": (
                    f"signal library file not present for timeframe {tf}"
                ),
            })
        else:
            d_strs = [_to_date_string(d) for d in lib[0]]
            library_date_strs[tf] = d_strs
            library_first_date[tf] = d_strs[0] if d_strs else None

    usable_pairs: list[tuple[str, float]] = []
    unusable_count = 0
    for d_str, c_val in price_rows:
        if (c_val is None
                or not isinstance(c_val, (int, float))
                or isinstance(c_val, bool)
                or (isinstance(c_val, float)
                    and (math.isnan(c_val) or math.isinf(c_val)))
                or c_val <= 0):
            unusable_count += 1
            continue
        usable_pairs.append((d_str, float(c_val)))
    if unusable_count > 0:
        issues.append({
            "error_code": ISSUE_PRICE_CLOSE_UNUSABLE,
            "message_sanitized": (
                f"omitted {unusable_count} price cache rows with "
                "unusable close values"
            ),
        })
    if not usable_pairs:
        return None, issues, ISSUE_PRICE_CACHE_MISSING

    if today_utc_override is None:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    else:
        today_str = today_utc_override

    last_usable_date = usable_pairs[-1][0]
    effective_eval_date = (
        last_usable_date if last_usable_date <= today_str else today_str
    )

    capped_pairs = [p for p in usable_pairs if p[0] <= effective_eval_date]
    if not capped_pairs:
        return None, issues, ISSUE_PRICE_CACHE_MISSING

    unrecognized_recorded: dict[str, bool] = {tf: False for tf in TIMEFRAMES_COVERED}
    partial_recorded: dict[str, bool] = {tf: False for tf in TIMEFRAMES_COVERED}

    bars: list[dict] = []
    for d_str, c_val in capped_pairs:
        signals_obj: dict[str, str] = {}
        any_coverage = False
        for tf in TIMEFRAMES_COVERED:
            sig_val, recognized, covered = _forward_fill_signal_at(
                d_str, libraries.get(tf), library_date_strs.get(tf),
            )
            if not recognized and not unrecognized_recorded[tf]:
                issues.append({
                    "error_code": ISSUE_SIGNAL_ENCODING_UNRECOGNIZED,
                    "timeframe": tf,
                    "message_sanitized": (
                        f"unrecognized signal encoding in timeframe {tf}"
                    ),
                })
                unrecognized_recorded[tf] = True
            if covered:
                any_coverage = True
            else:
                if (libraries.get(tf) is not None
                        and not partial_recorded[tf]):
                    first_tf = library_first_date.get(tf) or d_str
                    issues.append({
                        "error_code": ISSUE_SIGNAL_LIBRARY_PARTIAL,
                        "timeframe": tf,
                        "date_range": [d_str, first_tf],
                        "message_sanitized": (
                            f"timeframe {tf} library begins after "
                            "earlier price cache bars"
                        ),
                    })
                    partial_recorded[tf] = True
            signals_obj[tf] = sig_val
        if not any_coverage:
            continue
        bars.append({
            "date_utc": d_str,
            "close": c_val,
            "signals": signals_obj,
        })

    bars.sort(key=lambda b: b["date_utc"])

    # Codex audit fix (Finding 2): fail closed when no bars survive
    # the inclusion rules. The PR #331 schema requires
    # date_range_start_utc and date_range_end_utc to be YYYY-MM-DD
    # strings, and the Date Range Rule requires at least one timeframe
    # to have coverage on every included date. A zero-bar artifact
    # violates both. If every library is absent we surface
    # ``signal_library_missing`` as the fatal code; if any library is
    # present but no covered bars exist we surface
    # ``signal_library_partial_coverage``.
    if not bars:
        all_libraries_absent = all(
            libraries.get(tf) is None for tf in TIMEFRAMES_COVERED
        )
        fatal_code = (
            ISSUE_SIGNAL_LIBRARY_MISSING
            if all_libraries_absent
            else ISSUE_SIGNAL_LIBRARY_PARTIAL
        )
        return None, issues, fatal_code

    for i in range(1, len(bars)):
        d_prev = bars[i - 1]["date_utc"]
        d_curr = bars[i]["date_utc"]
        bd_gap = _business_day_count_between(d_prev, d_curr)
        if bd_gap >= PRICE_CACHE_GAP_BUSINESS_DAYS_THRESHOLD:
            issues.append({
                "error_code": ISSUE_PRICE_CACHE_GAP,
                "date_range": [d_prev, d_curr],
                "message_sanitized": (
                    f"price cache gap of {bd_gap} business days "
                    "between included bars"
                ),
            })

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "secondary": secondary,
        "generated_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        "trafficflow_run_id": trafficflow_run_id,
        "trafficflow_run_root": trafficflow_run_root,
        "effective_evaluation_date_utc": effective_eval_date,
        "date_range_start_utc": bars[0]["date_utc"],
        "date_range_end_utc": bars[-1]["date_utc"],
        "timeframes_covered": list(TIMEFRAMES_COVERED),
        "bar_count": len(bars),
        "bars": bars,
        "issues": issues,
    }

    safe = sanitize_for_json(payload, project_root=project_root)
    if isinstance(safe, dict):
        safe_issues = []
        for it in safe.get("issues") or []:
            if isinstance(it, dict):
                msg = it.get("message_sanitized")
                if isinstance(msg, str):
                    it = dict(it)
                    it["message_sanitized"] = _scrub_embedded_absolute_paths(msg)
            safe_issues.append(it)
        safe["issues"] = safe_issues
    return safe, issues, None


def write_v1_history_artifact(
    *,
    secondary: str,
    sec_dir: Path,
    trafficflow_run_id: Optional[str],
    trafficflow_run_root: Optional[str],
    signal_library_dir: Optional[str] = None,
    price_cache_dir: Optional[str] = None,
    project_root: Optional[Path] = None,
    today_utc_override: Optional[str] = None,
) -> dict:
    """Build and atomically write ``v1_history.json`` to ``sec_dir``.

    Returns a dict with keys:

      * ``status``: ``"ok"`` or ``"failed"``.
      * ``artifact_path``: repo-relative path on success, ``None`` on
        failure.
      * ``error_code``: ``None`` on success, otherwise one of the
        documented issue codes (``price_cache_missing``,
        ``write_error``) plus a free-form sub-code.
      * ``issues``: list of issue records collected during the build.

    On failure, no ``v1_history.json`` and no ``.tmp`` residue remain
    in ``sec_dir``.
    """
    payload, issues, fatal = build_v1_history_artifact(
        secondary=secondary,
        trafficflow_run_id=trafficflow_run_id,
        trafficflow_run_root=trafficflow_run_root,
        signal_library_dir=signal_library_dir,
        price_cache_dir=price_cache_dir,
        project_root=project_root,
        today_utc_override=today_utc_override,
    )
    sec_dir_path = Path(sec_dir)
    artifact_path = sec_dir_path / ARTIFACT_FILENAME
    tmp_path = artifact_path.with_name(artifact_path.name + ".tmp")

    if fatal is not None or payload is None:
        # Best-effort cleanup of any stale residue.
        for p in (tmp_path, artifact_path):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        return {
            "status": "failed",
            "artifact_path": None,
            "error_code": fatal or "build_failed",
            "issues": issues,
        }

    try:
        _atomic_write_json(artifact_path, payload)
    except Exception as exc:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        try:
            if artifact_path.exists():
                artifact_path.unlink()
        except OSError:
            pass
        return {
            "status": "failed",
            "artifact_path": None,
            "error_code": "write_error",
            "issues": issues,
        }

    rel = path_for_output(str(artifact_path)) or str(artifact_path)
    return {
        "status": "ok",
        "artifact_path": rel,
        "error_code": None,
        "issues": issues,
    }
