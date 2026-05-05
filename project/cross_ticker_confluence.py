"""
Phase 4A: Cross-ticker multi-timeframe confluence aggregation engine.

This module is an aggregation/read engine. It consumes Phase 3-verified
producer artifacts (OnePass / multi-timeframe signal libraries, Spymaster
cache PKLs as fallback, and existing StackBuilder run outputs) and
produces a manifest-stamped run directory containing:

    coverage.json         + coverage.json.manifest.json
    rankings.json         + rankings.json.manifest.json
    overlay.json          + overlay.json.manifest.json
    universe_snapshot.json + universe_snapshot.json.manifest.json
    run_manifest.json
    coverage.csv
    rankings.csv

It does NOT rebuild producers, fetch live Yahoo data, or invoke any
producer-side rebuild path. See the Phase 4 Scoping document
(``project/md_library/shared/2026-05-04_PHASE_4_SCOPING.md``) for the
locked behavioral rules; this engine implements them.

Public entry points:

  * ``run_cross_ticker_confluence(config) -> RunResult``
  * ``resolve_universe(config) -> UniverseSnapshot``
  * ``process_series(series, config) -> SeriesResult``
  * ``write_run_outputs(result, output_dir) -> Path``
  * CLI via ``python cross_ticker_confluence.py``
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from provenance_manifest import (
    build_output_manifest,
    file_sha256,
    load_verified_pickle_artifact,
    load_verified_signal_library,
    write_output_manifest,
)


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

ENGINE_NAME = "cross_ticker_confluence"
ENGINE_VERSION = "1.0.0"
SCHEMA_VERSION = 1

ARTIFACT_TYPE_COVERAGE = "cross_ticker_confluence_coverage"
ARTIFACT_TYPE_RANKINGS = "cross_ticker_confluence_rankings"
ARTIFACT_TYPE_OVERLAY = "cross_ticker_confluence_overlay"
ARTIFACT_TYPE_UNIVERSE_SNAPSHOT = "cross_ticker_confluence_universe_snapshot"
ARTIFACT_TYPE_RUN = "cross_ticker_confluence_run"

# top_level_status values (mutually exclusive)
TLS_SCORED_FULL = "scored_full"
TLS_SCORED_PARTIAL = "scored_partial"
TLS_SKIPPED_NO_DAILY = "skipped_no_daily_source"
TLS_SKIPPED_NO_LIBS = "skipped_no_signal_libraries"
TLS_INVALID_SYMBOL = "invalid_universe_symbol"

# issue_codes (additive, zero or more per record)
IC_MISSING_STACKBUILDER_RUN = "missing_stackbuilder_run"
IC_MANIFEST_FAILED = "manifest_failed"
IC_STALE = "stale"
IC_SCHEMA_FAILED = "schema_failed"
IC_PRODUCER_OUTPUT_MISSING = "producer_output_missing"
IC_LEGACY_MANIFEST_USED = "legacy_manifest_used"

# component-level status (per_source_status, per_interval_status)
CS_LOADED_VERIFIED = "loaded_verified"
CS_LOADED_LEGACY = "loaded_legacy"
CS_MISSING = "missing"
CS_MANIFEST_FAILED = "manifest_failed"
CS_SCHEMA_FAILED = "schema_failed"
CS_STALE = "stale"
CS_NOT_REQUESTED = "not_requested"
CS_NOT_APPLICABLE = "not_applicable"

# Source tags for per_interval_status.source
SRC_ONEPASS_DAILY = "onepass_daily"
SRC_MULTI_TIMEFRAME = "multi_timeframe_library"
SRC_SPYMASTER_FALLBACK = "spymaster_fallback"

DEFAULT_INTERVALS = ("1d", "1wk", "1mo", "3mo", "1y")
SIGNAL_VOCAB = ("Buy", "Short", "None")

# Component status -> additive top-level issue_code. ``not_applicable``
# (e.g. spymaster_fallback when OnePass succeeded) and ``not_requested``
# both map to None — they are not failures.
_COMPONENT_STATUS_TO_ISSUE_CODE: Dict[str, Optional[str]] = {
    CS_LOADED_VERIFIED: None,
    CS_LOADED_LEGACY: IC_LEGACY_MANIFEST_USED,
    CS_MISSING: None,
    CS_MANIFEST_FAILED: IC_MANIFEST_FAILED,
    CS_SCHEMA_FAILED: IC_SCHEMA_FAILED,
    CS_STALE: IC_STALE,
    CS_NOT_REQUESTED: None,
    CS_NOT_APPLICABLE: None,
}


def _status_to_issue_code(component_status: str) -> Optional[str]:
    """Map a per-source / per-interval component status to its additive
    top-level issue_code, or None when the status doesn't surface one.
    """
    return _COMPONENT_STATUS_TO_ISSUE_CODE.get(component_status)

# Default paths (resolved against PROJECT_DIR, not cwd)
DEFAULT_SIGNAL_LIBRARY_DIR = PROJECT_DIR / "signal_library" / "data" / "stable"
DEFAULT_SPYMASTER_CACHE_DIR = PROJECT_DIR / "cache" / "results"
DEFAULT_STACKBUILDER_RUNS_DIR = PROJECT_DIR / "output" / "stackbuilder"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "output"
DEFAULT_GTL_MASTER = PROJECT_DIR / "global_ticker_library" / "data" / "master_tickers.txt"

# Filename pattern for signal libraries: <ticker>_stable_v<ver>[_<interval>].pkl
_SIGLIB_RE = re.compile(
    r"^(?P<ticker>[A-Za-z0-9._^=+-]+)_stable_v(?P<ver>[0-9_]+)"
    r"(?:_(?P<interval>1wk|1mo|3mo|1y))?\.pkl$"
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunConfig:
    """Frozen configuration for one aggregation run."""
    universe_mode: str = "gtl-active"
    tickers: Tuple[str, ...] = ()
    intervals: Tuple[str, ...] = DEFAULT_INTERVALS
    output_dir: Path = DEFAULT_OUTPUT_DIR
    strict_manifests: bool = False
    max_workers: int = 4
    history_days: int = 365
    max_input_age_days: int = 45
    signal_library_dir: Path = DEFAULT_SIGNAL_LIBRARY_DIR
    spymaster_cache_dir: Path = DEFAULT_SPYMASTER_CACHE_DIR
    stackbuilder_runs_dir: Path = DEFAULT_STACKBUILDER_RUNS_DIR
    gtl_master_file: Path = DEFAULT_GTL_MASTER
    run_date: Optional[str] = None  # ISO YYYY-MM-DD; defaults to UTC today


@dataclass(frozen=True)
class UniverseSeriesEntry:
    position: int
    series_id: str
    series_kind: str
    source_symbol: str
    normalized_symbol: str
    valid_symbol: bool
    invalid_reason: Optional[str]


@dataclass(frozen=True)
class UniverseSnapshot:
    universe_mode: str
    resolved_at: str  # UTC ISO
    source_kind: str
    source_path: str
    source_file_sha256: Optional[str]
    series: Tuple[UniverseSeriesEntry, ...]
    universe_hash: str
    counts: Mapping[str, int]


@dataclass(frozen=True)
class IntervalStatus:
    status: str
    signal: Optional[str]  # "Buy"/"Short"/"None"/None
    source: Optional[str]
    path: Optional[str] = None
    content_hash: Optional[str] = None
    manifest_hash: Optional[str] = None
    age_days: Optional[float] = None


@dataclass(frozen=True)
class SourceStatus:
    status: str
    path: Optional[str] = None
    content_hash: Optional[str] = None
    manifest_hash: Optional[str] = None
    run_id: Optional[str] = None  # for stackbuilder
    age_days: Optional[float] = None


@dataclass(frozen=True)
class SeriesResult:
    series_entry: UniverseSeriesEntry
    coverage_record: Dict[str, Any]
    ranking_record: Optional[Dict[str, Any]]
    overlay_record: Optional[Dict[str, Any]]
    input_artifact_refs: Tuple[Dict[str, Any], ...]
    input_manifest_hashes: Tuple[str, ...]


@dataclass
class RunResult:
    config: RunConfig
    run_id: str
    run_date: str
    universe: UniverseSnapshot
    series_results: List[SeriesResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


# ---------------------------------------------------------------------------
# Universe resolution
# ---------------------------------------------------------------------------


_VALID_SYMBOL_RE = re.compile(r"^[A-Z0-9.\^=+-]+$")


def _normalize_symbol(raw: str) -> Tuple[str, bool, Optional[str]]:
    """Return (normalized, is_valid, invalid_reason)."""
    if raw is None:
        return ("", False, "empty_symbol")
    sym = str(raw).strip()
    if not sym:
        return ("", False, "empty_symbol")
    upper = sym.upper()
    if len(upper) > 32:
        return (upper, False, "symbol_too_long")
    if not _VALID_SYMBOL_RE.match(upper):
        return (upper, False, "symbol_chars_invalid")
    return (upper, True, None)


def _read_gtl_master(path: Path) -> List[str]:
    """Read symbols from a GTL master file.

    Mirrors ``stackbuilder.load_master_universe``: real GTL exports are
    comma-separated (sometimes mixed with whitespace and newlines), not
    one-symbol-per-line. We split on ``[\\s,]+`` so a file like
    ``AAA, BBB,CCC\\nDDD EEE`` resolves to five symbols.

    Tokens that begin with ``#`` are treated as comment fragments and
    dropped — that is a Phase 4A convenience over StackBuilder's bare
    splitter, since real GTL exports don't carry comments and our
    fixtures sometimes do. Returns an empty list when the file is
    missing or empty.
    """
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    toks = re.split(r"[\s,]+", raw.upper())
    return [t for t in toks if t and not t.startswith("#")]


def _scorable_filter(
    symbols: Sequence[str],
    config: RunConfig,
) -> List[str]:
    """For ``--universe-mode=scorable``: keep only symbols whose daily
    source file is present (no verification yet — just file presence).

    Daily candidates: ``<ticker>_stable_v*.pkl`` directly under
    ``signal_library_dir`` (no _<interval> suffix) OR a Spymaster
    fallback PKL under ``spymaster_cache_dir``.
    """
    sig_dir = config.signal_library_dir
    spy_dir = config.spymaster_cache_dir
    keep: List[str] = []
    for sym in symbols:
        if _signal_library_candidates(sig_dir, sym, interval="1d"):
            keep.append(sym)
            continue
        spy_path = spy_dir / f"{sym}_precomputed_results.pkl"
        if spy_path.exists():
            keep.append(sym)
    return keep


def resolve_universe(config: RunConfig) -> UniverseSnapshot:
    """Resolve the requested universe according to ``config.universe_mode``.

    For ``gtl-active`` and ``scorable`` modes, an unreadable or empty GTL
    source is fatal — callers should treat a ``RuntimeError`` as a
    run-level fatal failure and not write a partial run directory.
    """
    mode = config.universe_mode
    resolved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if mode == "tickers":
        if not config.tickers:
            raise RuntimeError(
                "--universe-mode=tickers requires --tickers"
            )
        raw_symbols = list(config.tickers)
        source_kind = "cli_tickers"
        source_path = "<cli>"
        source_file_sha = None
        # Preserve user-supplied order.
        ordered_raw = list(raw_symbols)
        ordering = "user"
    elif mode == "gtl-active":
        raw_symbols = _read_gtl_master(config.gtl_master_file)
        if not raw_symbols:
            raise RuntimeError(
                f"GTL master file unreadable or empty: "
                f"{config.gtl_master_file}"
            )
        source_kind = "gtl_master_file"
        source_path = str(config.gtl_master_file)
        source_file_sha = file_sha256(config.gtl_master_file)
        ordered_raw = list(raw_symbols)
        ordering = "lex"
    elif mode == "scorable":
        gtl_syms = _read_gtl_master(config.gtl_master_file)
        if not gtl_syms:
            raise RuntimeError(
                f"GTL master file unreadable or empty: "
                f"{config.gtl_master_file}"
            )
        # Pre-normalize before scorable filter; filter looks at file
        # presence keyed by normalized symbol.
        normalized = []
        for raw in gtl_syms:
            norm, valid, _reason = _normalize_symbol(raw)
            if valid:
                normalized.append(norm)
        scorable = _scorable_filter(normalized, config)
        if not scorable:
            raise RuntimeError(
                "scorable mode resolved an empty universe (no daily "
                "source files found for any GTL active symbol)."
            )
        source_kind = "gtl_master_file_scorable_subset"
        source_path = str(config.gtl_master_file)
        source_file_sha = file_sha256(config.gtl_master_file)
        ordered_raw = scorable
        ordering = "lex"
    else:
        raise ValueError(f"unknown universe_mode: {mode!r}")

    # Build series entries with normalization + validity.
    entries_raw: List[UniverseSeriesEntry] = []
    seen_norm: set = set()
    for pos, raw in enumerate(ordered_raw):
        norm, valid, reason = _normalize_symbol(raw)
        # Per-position dedupe: keep first occurrence of a normalized
        # symbol; later ones become invalid_universe_symbol with reason
        # "duplicate_symbol".
        if valid and norm in seen_norm:
            valid = False
            reason = "duplicate_symbol"
        if valid:
            seen_norm.add(norm)
        entries_raw.append(UniverseSeriesEntry(
            position=pos,
            series_id=norm if norm else str(raw),
            series_kind="yfinance_ticker",
            source_symbol=str(raw),
            normalized_symbol=norm,
            valid_symbol=valid,
            invalid_reason=reason,
        ))

    if ordering == "lex":
        # Stable lexicographic ordering by normalized_symbol; invalid
        # symbols sort by source_symbol so they remain deterministic.
        def _sort_key(e: UniverseSeriesEntry) -> Tuple[str, str]:
            return (
                e.normalized_symbol or "~~~" + e.source_symbol,
                e.source_symbol,
            )
        entries_raw.sort(key=_sort_key)

    # Reassign positions so position == final ordering index.
    final = tuple(
        replace(e, position=i) for i, e in enumerate(entries_raw)
    )

    # Universe hash: deterministic over (position, series_kind,
    # normalized_symbol, source_symbol, valid). Changing any of these
    # changes the hash; that's the point.
    h = hashlib.sha256()
    for e in final:
        h.update(
            f"{e.position}\x1f{e.series_kind}\x1f{e.normalized_symbol}"
            f"\x1f{e.source_symbol}\x1f{int(e.valid_symbol)}\n".encode("utf-8")
        )
    universe_hash = "sha256:" + h.hexdigest()

    counts = {
        "requested": len(final),
        "valid": sum(1 for e in final if e.valid_symbol),
        "invalid": sum(1 for e in final if not e.valid_symbol),
    }
    return UniverseSnapshot(
        universe_mode=mode,
        resolved_at=resolved_at,
        source_kind=source_kind,
        source_path=source_path,
        source_file_sha256=source_file_sha,
        series=final,
        universe_hash=universe_hash,
        counts=counts,
    )


# ---------------------------------------------------------------------------
# Source discovery
# ---------------------------------------------------------------------------


def _signal_library_candidates(
    library_dir: Path, ticker: str, interval: str,
) -> List[Path]:
    """Return matching candidate paths for a given ticker/interval.

    Daily (``interval='1d'``): files matching
    ``<ticker>_stable_v*.pkl`` with no embedded ``_<interval>`` suffix.

    Non-daily: files matching ``<ticker>_stable_v*_<interval>.pkl``.
    """
    if not library_dir.exists():
        return []
    out: List[Path] = []
    target_ticker_upper = ticker.upper()
    try:
        entries = list(library_dir.iterdir())
    except OSError:
        return []
    for p in entries:
        if not p.is_file() or p.suffix != ".pkl":
            continue
        m = _SIGLIB_RE.match(p.name)
        if not m:
            continue
        if m.group("ticker").upper() != target_ticker_upper:
            continue
        m_interval = m.group("interval")
        if interval == "1d":
            if m_interval is None:
                out.append(p)
        else:
            if m_interval == interval:
                out.append(p)
    # Deterministic ordering: highest version first, then lex by name.
    def _ver_key(p: Path) -> Tuple[Tuple[int, ...], str]:
        m = _SIGLIB_RE.match(p.name)
        ver = m.group("ver") if m else ""
        try:
            tup = tuple(int(x) for x in ver.split("_") if x != "")
        except ValueError:
            tup = ()
        return (tup, p.name)
    out.sort(key=_ver_key, reverse=True)
    return out


def _file_age_days(path: Path) -> float:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return 0.0
    return (time.time() - mtime) / 86400.0


def _classify_signal_library_load(
    path: Path, *, strict: bool, max_age_days: int,
) -> Tuple[Optional[Dict[str, Any]], IntervalStatus]:
    """Load + classify a signal-library candidate path.

    Returns ``(library_dict_or_None, IntervalStatus)``. The status's
    ``signal`` and ``source`` fields are filled by the caller if needed
    — this helper only handles load/verify classification.
    """
    age = _file_age_days(path)
    if max_age_days and age > max_age_days:
        return None, IntervalStatus(
            status=CS_STALE, signal=None, source=None,
            path=str(path), age_days=age,
        )
    lib, vresult = load_verified_signal_library(path, strict=strict)
    if lib is None:
        # Hard load error or type error.
        return None, IntervalStatus(
            status=CS_SCHEMA_FAILED, signal=None, source=None,
            path=str(path), age_days=age,
        )
    if vresult.legacy:
        if strict:
            return None, IntervalStatus(
                status=CS_MANIFEST_FAILED, signal=None, source=None,
                path=str(path), age_days=age,
            )
        # Default mode accepts legacy.
        return lib, IntervalStatus(
            status=CS_LOADED_LEGACY, signal=None, source=None,
            path=str(path),
            content_hash=None,
            manifest_hash=None,
            age_days=age,
        )
    if not vresult.ok:
        return None, IntervalStatus(
            status=CS_MANIFEST_FAILED, signal=None, source=None,
            path=str(path), age_days=age,
        )
    embedded = lib.get("_manifest") or {}
    return lib, IntervalStatus(
        status=CS_LOADED_VERIFIED, signal=None, source=None,
        path=str(path),
        content_hash=embedded.get("content_hash"),
        manifest_hash=embedded.get("content_hash"),
        age_days=age,
    )


def _coerce_signal(value: Any) -> Optional[str]:
    """Coerce a single stored signal cell into the string vocabulary."""
    if isinstance(value, str):
        return value if value in SIGNAL_VOCAB else None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return {1: "Buy", -1: "Short", 0: "None"}.get(v)


def _signal_at_run_date(
    lib: Mapping[str, Any], *, run_date: str,
) -> Optional[str]:
    """Latest signal whose date is ``<= run_date`` (Phase 4 locked rule:
    no future-period signal). Returns ``None`` when the artifact has no
    signal at or before run_date.
    """
    sigs = lib.get("primary_signals") or lib.get("signals")
    dates = lib.get("dates") or lib.get("date_index")
    if not sigs or not dates or len(sigs) != len(dates):
        return None
    chosen: Optional[str] = None
    for d, s in zip(dates, sigs):
        d_str = _to_date_str(d)
        if d_str is None or d_str > run_date:
            continue
        coerced = _coerce_signal(s)
        if coerced is None:
            continue
        chosen = coerced  # keep the latest because dates are ascending
    return chosen


def _signal_history(
    lib: Mapping[str, Any], *, history_days: int, run_date: str,
) -> List[Dict[str, Any]]:
    """Build ``{date, signal}`` history rows in the inclusive window
    ``run_date - history_days <= date <= run_date``. Future-dated rows
    are excluded so overlay never leaks post-run_date signals.
    """
    sigs = lib.get("primary_signals") or lib.get("signals") or []
    dates = lib.get("dates") or lib.get("date_index") or []
    if not sigs or not dates or len(sigs) != len(dates):
        return []
    cutoff_low = _date_offset_iso(run_date, -history_days)
    out: List[Dict[str, Any]] = []
    for d, s in zip(dates, sigs):
        d_str = _to_date_str(d)
        if d_str is None or d_str < cutoff_low or d_str > run_date:
            continue
        sig = _coerce_signal(s)
        if sig is None:
            continue
        out.append({"date": d_str, "signal": sig})
    return out


def _to_date_str(value: Any) -> Optional[str]:
    """Best-effort coerce to ISO YYYY-MM-DD."""
    if value is None:
        return None
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d")
        except Exception:
            return None
    if isinstance(value, str):
        # Accept the leading 10 chars of an ISO timestamp.
        if len(value) >= 10 and value[4] == "-" and value[7] == "-":
            return value[:10]
    return None


def _date_offset_iso(date_str: str, delta_days: int) -> str:
    base = datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=timezone.utc
    )
    out = base.timestamp() + (delta_days * 86400)
    return datetime.fromtimestamp(out, tz=timezone.utc).strftime("%Y-%m-%d")


def _classify_spymaster_pkl(
    path: Path, *, strict: bool, max_age_days: int,
) -> Tuple[Optional[Dict[str, Any]], SourceStatus]:
    """Load + classify a Spymaster cache PKL as a fallback daily source.

    Used only when verified OnePass daily source is unavailable.
    """
    if not path.exists():
        return None, SourceStatus(status=CS_MISSING)
    age = _file_age_days(path)
    if max_age_days and age > max_age_days:
        return None, SourceStatus(
            status=CS_STALE, path=str(path), age_days=age,
        )
    data, vresult = load_verified_pickle_artifact(path, strict=strict)
    if data is None:
        return None, SourceStatus(
            status=CS_SCHEMA_FAILED, path=str(path), age_days=age,
        )
    if vresult.legacy:
        if strict:
            return None, SourceStatus(
                status=CS_MANIFEST_FAILED, path=str(path), age_days=age,
            )
        return data, SourceStatus(
            status=CS_LOADED_LEGACY, path=str(path), age_days=age,
        )
    if not vresult.ok:
        return None, SourceStatus(
            status=CS_MANIFEST_FAILED, path=str(path), age_days=age,
        )
    embedded = data.get("_manifest") if isinstance(data, Mapping) else None
    h = embedded.get("content_hash") if embedded else None
    return data, SourceStatus(
        status=CS_LOADED_VERIFIED, path=str(path),
        content_hash=h, manifest_hash=h, age_days=age,
    )


def _spymaster_signal_at_run_date(
    pkl: Mapping[str, Any], *, run_date: str,
) -> Optional[str]:
    """Latest Spymaster active-pairs signal at or before ``run_date``.

    Spymaster cache pickles store ``active_pairs`` as a list of
    ``"Buy"``/``"Short"``/``"None"`` strings paralleling
    ``preprocessed_data.index``. We pick the latest entry whose index
    date is on or before run_date so post-run_date data never bleeds
    into the run-date signal.
    """
    pairs = pkl.get("active_pairs")
    df = pkl.get("preprocessed_data")
    if not pairs or df is None:
        return None
    try:
        idx = list(df.index)
    except AttributeError:
        return None
    if len(idx) != len(pairs):
        return None
    chosen: Optional[str] = None
    for d, s in zip(idx, pairs):
        d_str = _to_date_str(d)
        if d_str is None or d_str > run_date:
            continue
        if isinstance(s, str) and s in SIGNAL_VOCAB:
            chosen = s
    return chosen


def _spymaster_signal_history(
    pkl: Mapping[str, Any], *, history_days: int, run_date: str,
) -> List[Dict[str, Any]]:
    pairs = pkl.get("active_pairs") or []
    df = pkl.get("preprocessed_data")
    if df is None or not pairs:
        return []
    try:
        idx = list(df.index)
    except AttributeError:
        return []
    if len(idx) != len(pairs):
        return []
    cutoff_low = _date_offset_iso(run_date, -history_days)
    out: List[Dict[str, Any]] = []
    for d, s in zip(idx, pairs):
        d_str = _to_date_str(d)
        if d_str is None or d_str < cutoff_low or d_str > run_date:
            continue
        if isinstance(s, str) and s in SIGNAL_VOCAB:
            out.append({"date": d_str, "signal": s})
    return out


# Required fields in a real StackBuilder run_manifest.json. The
# manifest is self-describing (no sidecar) and is written via plain
# ``write_json`` by ``stackbuilder.run_for_secondary``. See
# ``stackbuilder.py`` around lines 2195-2363; ``provenance_manifest.py``
# notes that standalone JSON manifests don't need the sidecar pattern.
_STACKBUILDER_REQUIRED_FIELDS = (
    "secondary",
    "started_at",
    "params",
    "finished_at",
    "elapsed_seconds",
    "outputs",
)


def _classify_stackbuilder_run(
    runs_dir: Path, ticker: str, *, strict: bool, max_age_days: int,
) -> Tuple[Optional[Dict[str, Any]], SourceStatus]:
    """Locate and classify the freshest StackBuilder ``run_manifest.json``
    for the given secondary ticker.

    StackBuilder writes run dirs as
    ``<runs_dir>/<secondary>/<run_name>/run_manifest.json`` and the
    manifest is self-describing — there is no Phase 3 sidecar. We
    therefore parse it with a plain ``json.load`` plus field
    validation rather than ``load_verified_json_artifact`` (which is
    for sidecar-stamped JSON artifacts).

    Component-status mapping:
      - directory missing or no candidate -> CS_MISSING (caller maps
        to ``missing_stackbuilder_run``)
      - mtime older than max_age_days -> CS_STALE (caller maps to
        ``stale``)
      - unreadable or unparseable JSON -> CS_SCHEMA_FAILED
      - missing required field -> CS_SCHEMA_FAILED
      - status field present and != "complete" -> CS_SCHEMA_FAILED
      - otherwise -> CS_LOADED_VERIFIED

    ``strict`` is accepted but currently has no effect here: a
    self-describing run manifest cannot be "legacy" in the sidecar
    sense. The parameter is kept for symmetry with the signal-library
    and pickle classifiers.
    """
    del strict  # reserved for future use; see docstring
    if not runs_dir.exists():
        return None, SourceStatus(status=CS_MISSING)
    sec_dir = runs_dir / ticker.upper()
    if not sec_dir.exists():
        # Fall back to scanning all subdirs; defensive for non-
        # conventional layouts.
        sec_dir = runs_dir
    candidates: List[Path] = []
    if sec_dir.is_dir():
        try:
            for sub in sec_dir.iterdir():
                if not sub.is_dir():
                    continue
                rm = sub / "run_manifest.json"
                if rm.exists():
                    candidates.append(rm)
        except OSError:
            pass
    if not candidates:
        return None, SourceStatus(status=CS_MISSING)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    rm_path = candidates[0]
    age = _file_age_days(rm_path)
    if max_age_days and age > max_age_days:
        return None, SourceStatus(
            status=CS_STALE, path=str(rm_path), age_days=age,
        )
    try:
        with open(rm_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None, SourceStatus(
            status=CS_SCHEMA_FAILED, path=str(rm_path), age_days=age,
        )
    if not isinstance(data, dict):
        return None, SourceStatus(
            status=CS_SCHEMA_FAILED, path=str(rm_path), age_days=age,
        )
    missing = [f for f in _STACKBUILDER_REQUIRED_FIELDS if f not in data]
    if missing:
        return None, SourceStatus(
            status=CS_SCHEMA_FAILED, path=str(rm_path), age_days=age,
        )
    status_field = data.get("status")
    if status_field is not None and status_field != "complete":
        # An incomplete or aborted run shouldn't be consumed; treat as
        # schema_failed so a future producer_output_missing surface can
        # refine the distinction.
        return None, SourceStatus(
            status=CS_SCHEMA_FAILED, path=str(rm_path), age_days=age,
        )
    return data, SourceStatus(
        status=CS_LOADED_VERIFIED, path=str(rm_path),
        run_id=data.get("run_id"), age_days=age,
    )


def _stackbuilder_selected_stack(rm: Mapping[str, Any]) -> List[str]:
    """Best-effort extract of the selected stack members from a
    StackBuilder run_manifest.json. Returns an empty list when the
    field shape isn't recognized.
    """
    members = rm.get("final_members") or rm.get("selected_stack") or []
    if isinstance(members, list):
        return [str(m) for m in members if isinstance(m, (str, int, float))]
    return []


# ---------------------------------------------------------------------------
# process_series
# ---------------------------------------------------------------------------


def _new_status_dict(s: SourceStatus) -> Dict[str, Any]:
    """Canonical-output dict for a per-source status.

    ``age_days`` is intentionally NOT serialized: it is computed from
    wall-clock time and would make canonical output non-deterministic
    between two runs at slightly different start times. The component
    ``status`` enum (``loaded_verified`` / ``stale`` / ...) carries the
    operational meaning; the underlying mtime is reconstructable from
    the ``path`` if a future caller needs it.
    """
    out: Dict[str, Any] = {"status": s.status}
    if s.path:
        out["path"] = s.path
    if s.content_hash:
        out["content_hash"] = s.content_hash
    if s.run_id:
        out["run_id"] = s.run_id
    return out


def _new_interval_dict(s: IntervalStatus) -> Dict[str, Any]:
    """Canonical-output dict for a per-interval status. See
    ``_new_status_dict`` for the rationale on ``age_days`` exclusion.
    """
    out: Dict[str, Any] = {
        "status": s.status,
        "signal": s.signal,
        "source": s.source,
    }
    if s.path:
        out["path"] = s.path
    if s.content_hash:
        out["content_hash"] = s.content_hash
    return out


def process_series(
    series: UniverseSeriesEntry,
    config: RunConfig,
    *,
    run_date: str,
) -> SeriesResult:
    """Process one universe entry: discover sources, classify, build the
    coverage / ranking / overlay records.

    No producer paths are invoked; every load goes through Phase 3
    verified loaders.
    """
    coverage: Dict[str, Any] = {
        "series_id": series.series_id,
        "series_kind": series.series_kind,
        "series_metadata": {
            "ticker": series.normalized_symbol,
            "source": "yfinance",
        },
        "issue_codes": [],
    }
    issue_codes: List[str] = []
    input_refs: List[Dict[str, Any]] = []
    input_manifest_hashes: List[str] = []

    # Invalid symbol: short-circuit.
    if not series.valid_symbol:
        coverage.update({
            "top_level_status": TLS_INVALID_SYMBOL,
            "eligible_for_rankings": False,
            "per_source_status": {},
            "per_interval_status": {},
            "issue_codes": [],
            "invalid_reason": series.invalid_reason,
        })
        return SeriesResult(
            series_entry=series,
            coverage_record=coverage,
            ranking_record=None,
            overlay_record=None,
            input_artifact_refs=(),
            input_manifest_hashes=(),
        )

    ticker = series.normalized_symbol

    # --- Daily source: OnePass library first, Spymaster fallback only
    # if OnePass is missing or unusable.
    daily_lib: Optional[Dict[str, Any]] = None
    daily_source_tag: Optional[str] = None
    daily_signal: Optional[str] = None
    onepass_status: SourceStatus
    spymaster_status: SourceStatus

    op_candidates = _signal_library_candidates(
        config.signal_library_dir, ticker, "1d",
    )
    if op_candidates:
        op_lib, op_istatus = _classify_signal_library_load(
            op_candidates[0],
            strict=config.strict_manifests,
            max_age_days=config.max_input_age_days,
        )
        onepass_status = SourceStatus(
            status=op_istatus.status,
            path=op_istatus.path,
            content_hash=op_istatus.content_hash,
            manifest_hash=op_istatus.manifest_hash,
            age_days=op_istatus.age_days,
        )
        if op_lib is not None and op_istatus.status in (
            CS_LOADED_VERIFIED, CS_LOADED_LEGACY,
        ):
            daily_lib = op_lib
            daily_source_tag = SRC_ONEPASS_DAILY
            daily_signal = _signal_at_run_date(op_lib, run_date=run_date)
            if op_istatus.content_hash:
                input_manifest_hashes.append(op_istatus.content_hash)
            input_refs.append({
                "source": "onepass_signal_library_1d",
                "ticker": ticker,
                "interval": "1d",
                "path": op_istatus.path,
                "content_hash": op_istatus.content_hash,
                "manifest_hash": op_istatus.manifest_hash,
                "verification_status": op_istatus.status,
                "legacy": op_istatus.status == CS_LOADED_LEGACY,
            })
    else:
        onepass_status = SourceStatus(status=CS_MISSING)

    # Spymaster fallback only if no usable verified OnePass daily.
    if daily_lib is None:
        spy_path = (
            config.spymaster_cache_dir / f"{ticker}_precomputed_results.pkl"
        )
        spy_data, spymaster_status = _classify_spymaster_pkl(
            spy_path,
            strict=config.strict_manifests,
            max_age_days=config.max_input_age_days,
        )
        if spy_data is not None and spymaster_status.status in (
            CS_LOADED_VERIFIED, CS_LOADED_LEGACY,
        ):
            daily_lib = spy_data
            daily_source_tag = SRC_SPYMASTER_FALLBACK
            daily_signal = _spymaster_signal_at_run_date(
                spy_data, run_date=run_date,
            )
            if spymaster_status.content_hash:
                input_manifest_hashes.append(spymaster_status.content_hash)
            input_refs.append({
                "source": "spymaster_cache_pkl",
                "ticker": ticker,
                "path": spymaster_status.path,
                "content_hash": spymaster_status.content_hash,
                "manifest_hash": spymaster_status.manifest_hash,
                "verification_status": spymaster_status.status,
                "legacy": spymaster_status.status == CS_LOADED_LEGACY,
            })
    else:
        # OnePass succeeded; spymaster fallback was not exercised.
        spymaster_status = SourceStatus(status=CS_NOT_APPLICABLE)

    # --- Non-daily intervals
    interval_dicts: Dict[str, Dict[str, Any]] = {}
    interval_signal_used: Dict[str, Optional[str]] = {}
    for interval in config.intervals:
        if interval == "1d":
            if daily_signal is not None and daily_source_tag is not None:
                istatus = IntervalStatus(
                    status=(
                        onepass_status.status
                        if daily_source_tag == SRC_ONEPASS_DAILY
                        else spymaster_status.status
                    ),
                    signal=daily_signal,
                    source=daily_source_tag,
                    path=(
                        onepass_status.path
                        if daily_source_tag == SRC_ONEPASS_DAILY
                        else spymaster_status.path
                    ),
                    content_hash=(
                        onepass_status.content_hash
                        if daily_source_tag == SRC_ONEPASS_DAILY
                        else spymaster_status.content_hash
                    ),
                    age_days=(
                        onepass_status.age_days
                        if daily_source_tag == SRC_ONEPASS_DAILY
                        else spymaster_status.age_days
                    ),
                )
            else:
                # daily not loaded: use OnePass status (or fallback status
                # if OnePass was missing AND spymaster was tried).
                istatus = IntervalStatus(
                    status=onepass_status.status if op_candidates
                    else CS_MISSING,
                    signal=None,
                    source=None,
                    path=onepass_status.path,
                    age_days=onepass_status.age_days,
                )
            interval_dicts[interval] = _new_interval_dict(istatus)
            interval_signal_used[interval] = istatus.signal
            continue
        cands = _signal_library_candidates(
            config.signal_library_dir, ticker, interval,
        )
        if not cands:
            interval_dicts[interval] = _new_interval_dict(
                IntervalStatus(
                    status=CS_MISSING, signal=None, source=None,
                )
            )
            interval_signal_used[interval] = None
            continue
        sub_lib, sub_status = _classify_signal_library_load(
            cands[0],
            strict=config.strict_manifests,
            max_age_days=config.max_input_age_days,
        )
        sig: Optional[str] = None
        if sub_lib is not None and sub_status.status in (
            CS_LOADED_VERIFIED, CS_LOADED_LEGACY,
        ):
            sig = _signal_at_run_date(sub_lib, run_date=run_date)
            if sub_status.content_hash:
                input_manifest_hashes.append(sub_status.content_hash)
            input_refs.append({
                "source": f"onepass_signal_library_{interval}",
                "ticker": ticker,
                "interval": interval,
                "path": sub_status.path,
                "content_hash": sub_status.content_hash,
                "manifest_hash": sub_status.manifest_hash,
                "verification_status": sub_status.status,
                "legacy": sub_status.status == CS_LOADED_LEGACY,
            })
        sub_status = IntervalStatus(
            status=sub_status.status,
            signal=sig,
            source=SRC_MULTI_TIMEFRAME if sig is not None else None,
            path=sub_status.path,
            content_hash=sub_status.content_hash,
            manifest_hash=sub_status.manifest_hash,
            age_days=sub_status.age_days,
        )
        interval_dicts[interval] = _new_interval_dict(sub_status)
        interval_signal_used[interval] = sig

    # --- StackBuilder run (does not gate ranking eligibility)
    sb_data, sb_status = _classify_stackbuilder_run(
        config.stackbuilder_runs_dir, ticker,
        strict=config.strict_manifests,
        max_age_days=config.max_input_age_days,
    )
    if sb_status.status == CS_MISSING:
        issue_codes.append(IC_MISSING_STACKBUILDER_RUN)
    elif sb_status.status == CS_STALE:
        issue_codes.append(IC_STALE)
    elif sb_status.status == CS_MANIFEST_FAILED:
        issue_codes.append(IC_MANIFEST_FAILED)
    elif sb_status.status == CS_SCHEMA_FAILED:
        issue_codes.append(IC_SCHEMA_FAILED)
    elif sb_status.status == CS_LOADED_LEGACY:
        issue_codes.append(IC_LEGACY_MANIFEST_USED)
    if sb_status.status == CS_LOADED_VERIFIED and sb_data is not None:
        # Add a content_hash if one was embedded in the stackbuilder
        # run_manifest. The self-describing run_manifest.json does not
        # carry a Phase 3 sidecar, so byte-level provenance is captured
        # separately via file_sha256 over the run_manifest.json bytes.
        sb_hash = sb_data.get("content_hash") if isinstance(sb_data, Mapping) else None
        if sb_hash:
            input_manifest_hashes.append(str(sb_hash))
        sb_file_sha: Optional[str] = None
        if sb_status.path:
            try:
                sb_file_sha = file_sha256(sb_status.path)
            except OSError:
                sb_file_sha = None
        input_refs.append({
            "source": "stackbuilder_run_manifest",
            "ticker": ticker,
            "path": sb_status.path,
            "run_id": sb_data.get("run_id") if isinstance(sb_data, Mapping) else None,
            "manifest_hash": sb_hash,
            "file_sha256": sb_file_sha,
            "verification_status": sb_status.status,
            "legacy": False,
        })

    # Map every component-level status seen across daily / spymaster /
    # non-daily intervals to additive top-level issue_codes via a single
    # shared mapping. OnePass and Spymaster paths are treated uniformly
    # so a Spymaster fallback that fails verification surfaces in
    # issue_codes the same way an OnePass failure does.
    daily_used_status = (
        onepass_status.status if daily_source_tag == SRC_ONEPASS_DAILY
        else (spymaster_status.status if daily_source_tag else None)
    )

    def _add_issue(code: Optional[str]) -> None:
        if code is None:
            return
        if code not in issue_codes:
            issue_codes.append(code)

    _add_issue(_status_to_issue_code(onepass_status.status))
    _add_issue(_status_to_issue_code(spymaster_status.status))
    for interval in config.intervals:
        if interval == "1d":
            continue
        _add_issue(
            _status_to_issue_code(interval_dicts[interval]["status"])
        )

    per_source_status = {
        "onepass_daily": _new_status_dict(onepass_status),
        "spymaster_fallback": _new_status_dict(spymaster_status),
        "stackbuilder_run": _new_status_dict(sb_status),
    }
    coverage["per_source_status"] = per_source_status
    coverage["per_interval_status"] = interval_dicts

    # --- Determine top_level_status
    has_usable_daily = (
        daily_signal is not None
        and daily_used_status in (CS_LOADED_VERIFIED, CS_LOADED_LEGACY)
    )
    requested_non_daily = [iv for iv in config.intervals if iv != "1d"]
    non_daily_loaded = [
        iv for iv in requested_non_daily
        if interval_dicts[iv]["status"] in (
            CS_LOADED_VERIFIED, CS_LOADED_LEGACY,
        )
    ]

    if has_usable_daily:
        if all(
            interval_dicts[iv]["status"] == CS_LOADED_VERIFIED
            or interval_dicts[iv]["status"] == CS_LOADED_LEGACY
            for iv in requested_non_daily
        ):
            top_level = TLS_SCORED_FULL
        else:
            top_level = TLS_SCORED_PARTIAL
    elif non_daily_loaded:
        top_level = TLS_SKIPPED_NO_DAILY
    elif daily_used_status is None and not non_daily_loaded:
        # Truly nothing loaded.
        # Daily candidates absent AND every non-daily missing.
        # If onepass_status itself is something other than missing
        # (e.g. manifest_failed) we still classify as
        # skipped_no_signal_libraries; the issue_code carries the why.
        top_level = TLS_SKIPPED_NO_LIBS
    else:
        # Daily failed verification AND no non-daily loaded -> nothing
        # loaded. (Spymaster fallback also failed if attempted.)
        top_level = TLS_SKIPPED_NO_LIBS

    eligible = top_level in (TLS_SCORED_FULL, TLS_SCORED_PARTIAL)
    coverage["top_level_status"] = top_level
    coverage["eligible_for_rankings"] = eligible
    coverage["issue_codes"] = sorted(set(issue_codes))

    # --- Ranking record
    ranking_record: Optional[Dict[str, Any]] = None
    if eligible:
        # Confluence stats over requested intervals using daily + non-daily
        # signals where available.
        all_signals: Dict[str, Optional[str]] = {}
        for iv in config.intervals:
            if iv == "1d":
                all_signals[iv] = daily_signal
            else:
                all_signals[iv] = interval_signal_used.get(iv)
        usable = [s for s in all_signals.values() if s in SIGNAL_VOCAB]
        active = [s for s in usable if s in ("Buy", "Short")]
        buy_count = sum(1 for s in usable if s == "Buy")
        short_count = sum(1 for s in usable if s == "Short")
        none_count = sum(1 for s in usable if s == "None")
        total_count = len(usable)
        active_count = len(active)
        if total_count > 0:
            alignment_pct = round(
                100.0 * max(buy_count, short_count) / total_count, 2,
            )
        else:
            alignment_pct = 0.0
        is_full = top_level == TLS_SCORED_FULL
        # rank_group classification
        if is_full and total_count == len(config.intervals):
            if buy_count == total_count:
                rank_group = "full_unanimity_buy"
                signal_direction = "Buy"
            elif short_count == total_count:
                rank_group = "full_unanimity_short"
                signal_direction = "Short"
            elif none_count == total_count:
                rank_group = "full_none"
                signal_direction = "None"
            else:
                rank_group = "full_mixed"
                signal_direction = "None"
        else:
            usable_active = [s for s in usable if s != "None"]
            if usable and all(s == "Buy" for s in usable):
                rank_group = "partial_buy"
                signal_direction = "Buy"
            elif usable and all(s == "Short" for s in usable):
                rank_group = "partial_short"
                signal_direction = "Short"
            elif usable and all(s == "None" for s in usable):
                rank_group = "partial_none"
                signal_direction = "None"
            else:
                rank_group = "partial_mixed"
                signal_direction = "None"

        # StackBuilder block (if loaded).
        stackbuilder_block = {
            "status": sb_status.status,
        }
        if sb_status.status == CS_LOADED_VERIFIED and sb_data is not None:
            stackbuilder_block["run_id"] = sb_data.get("run_id")
            stackbuilder_block["selected_stack"] = (
                _stackbuilder_selected_stack(sb_data)
            )
            stackbuilder_block["metrics"] = {}

        ranking_record = {
            "series_id": series.series_id,
            "series_metadata": coverage["series_metadata"],
            "rank": 0,  # filled after sort
            "rank_group": rank_group,
            "signal_direction": signal_direction,
            "run_date_signal": daily_signal,
            "producer_next_session_signal": None,
            "confluence": {
                "active_count": active_count,
                "total_count": total_count,
                "buy_count": buy_count,
                "short_count": short_count,
                "none_count": none_count,
                "alignment_pct": alignment_pct,
            },
            "interval_signals": {
                iv: all_signals[iv] for iv in config.intervals
            },
            "stackbuilder": stackbuilder_block,
        }

    # --- Overlay record (one per scored ticker; bounded by history_days)
    overlay_record: Optional[Dict[str, Any]] = None
    if eligible:
        overlay_intervals: Dict[str, List[Dict[str, Any]]] = {}
        # Daily history first.
        if daily_lib is not None and daily_source_tag == SRC_ONEPASS_DAILY:
            overlay_intervals["1d"] = [
                {**entry, "source": SRC_ONEPASS_DAILY}
                for entry in _signal_history(
                    daily_lib,
                    history_days=config.history_days,
                    run_date=run_date,
                )
            ]
        elif daily_lib is not None and daily_source_tag == SRC_SPYMASTER_FALLBACK:
            overlay_intervals["1d"] = [
                {**entry, "source": SRC_SPYMASTER_FALLBACK}
                for entry in _spymaster_signal_history(
                    daily_lib,
                    history_days=config.history_days,
                    run_date=run_date,
                )
            ]
        else:
            overlay_intervals["1d"] = []
        # Non-daily intervals: re-load via verified loader to get history.
        for interval in config.intervals:
            if interval == "1d":
                continue
            cands = _signal_library_candidates(
                config.signal_library_dir, ticker, interval,
            )
            entries: List[Dict[str, Any]] = []
            if cands:
                lib, istatus = _classify_signal_library_load(
                    cands[0],
                    strict=config.strict_manifests,
                    max_age_days=config.max_input_age_days,
                )
                if lib is not None and istatus.status in (
                    CS_LOADED_VERIFIED, CS_LOADED_LEGACY,
                ):
                    entries = [
                        {**entry, "source": SRC_MULTI_TIMEFRAME}
                        for entry in _signal_history(
                            lib,
                            history_days=config.history_days,
                            run_date=run_date,
                        )
                    ]
            overlay_intervals[interval] = entries
        overlay_record = {
            "series_id": series.series_id,
            "series_metadata": coverage["series_metadata"],
            "intervals": overlay_intervals,
        }

    # Dedupe input_manifest_hashes.
    input_manifest_hashes_dedup: List[str] = sorted(set(input_manifest_hashes))

    return SeriesResult(
        series_entry=series,
        coverage_record=coverage,
        ranking_record=ranking_record,
        overlay_record=overlay_record,
        input_artifact_refs=tuple(input_refs),
        input_manifest_hashes=tuple(input_manifest_hashes_dedup),
    )


# ---------------------------------------------------------------------------
# run_id, ranking sort, output writing
# ---------------------------------------------------------------------------


def _build_run_id(config: RunConfig, universe: UniverseSnapshot) -> str:
    """Deterministic-up-to-timestamp run_id.

    Format: ``YYYYMMDDTHHMMSSZ-<hash8>`` where hash8 is the first 8 hex
    chars of sha256 over the concatenation of universe_hash, intervals,
    universe_mode, history_days, max_input_age_days, strict_manifests.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    h = hashlib.sha256()
    h.update(universe.universe_hash.encode("utf-8"))
    h.update(b"\x1f")
    h.update(",".join(config.intervals).encode("utf-8"))
    h.update(b"\x1f")
    h.update(config.universe_mode.encode("utf-8"))
    h.update(b"\x1f")
    h.update(f"{config.history_days}".encode("utf-8"))
    h.update(b"\x1f")
    h.update(f"{config.max_input_age_days}".encode("utf-8"))
    h.update(b"\x1f")
    h.update(b"strict" if config.strict_manifests else b"default")
    return f"{ts}-{h.hexdigest()[:8]}"


_RANK_GROUP_ORDER = {
    "full_unanimity_buy": 0,
    "full_unanimity_short": 1,
    "full_mixed": 2,
    "full_none": 3,
    "partial_buy": 4,
    "partial_short": 5,
    "partial_mixed": 6,
    "partial_none": 7,
}


def _sort_rankings(records: List[Dict[str, Any]]) -> None:
    """In-place deterministic sort of ranking records.

    Order: rank_group enum -> alignment_pct DESC -> active_count DESC ->
    series_id ASC. Rank values (1-based) are then assigned.
    """
    def _key(r: Mapping[str, Any]) -> Tuple[int, float, int, str]:
        return (
            _RANK_GROUP_ORDER.get(str(r.get("rank_group")), 99),
            -float(r.get("confluence", {}).get("alignment_pct", 0.0)),
            -int(r.get("confluence", {}).get("active_count", 0)),
            str(r.get("series_id", "")),
        )
    records.sort(key=_key)
    for i, r in enumerate(records, start=1):
        r["rank"] = i


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON atomically with sorted keys + 2-space indent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True, indent=2)
    os.replace(tmp, path)


def _write_csv(path: Path, header: Sequence[str],
               rows: Sequence[Sequence[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for row in rows:
            w.writerow(row)


def _write_with_sidecar(
    artifact_path: Path,
    payload: Mapping[str, Any],
    *,
    artifact_type: str,
    config: RunConfig,
    input_manifest_hashes: Sequence[str],
) -> None:
    """Write a JSON artifact + sidecar manifest."""
    _atomic_json_write(artifact_path, payload)
    manifest = build_output_manifest(
        artifact_type=artifact_type,
        producer_engine=ENGINE_NAME,
        engine_version=ENGINE_VERSION,
        params={
            "universe_mode": config.universe_mode,
            "intervals": list(config.intervals),
            "history_days": config.history_days,
            "max_input_age_days": config.max_input_age_days,
            "strict_manifests": bool(config.strict_manifests),
        },
        input_manifest_hashes=list(input_manifest_hashes),
        content_obj=payload,
        repo_root=PROJECT_DIR,
    )
    write_output_manifest(
        artifact_path, manifest, include_file_sha256=True,
    )


def _csv_artifact_entry(name: str, path: Path, derived_from: str) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "name": name,
        "filename": path.name,
        "format": "csv",
        "file_sha256": file_sha256(path),
        "produced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "derived_from": derived_from,
    }
    try:
        entry["size_bytes"] = int(path.stat().st_size)
    except OSError:
        entry["size_bytes"] = None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            line_count = sum(1 for _ in fh)
        entry["row_count"] = max(0, line_count - 1)  # exclude header
    except OSError:
        pass
    return entry


def _json_artifact_entry(name: str, path: Path, *, record_count: int) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "name": name,
        "filename": path.name,
        "format": "json",
        "file_sha256": file_sha256(path),
        "produced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "record_count": int(record_count),
    }
    try:
        entry["size_bytes"] = int(path.stat().st_size)
    except OSError:
        entry["size_bytes"] = None
    return entry


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def run_cross_ticker_confluence(config: RunConfig) -> RunResult:
    """Resolve the universe, process every series, return a RunResult.

    Output writing is deferred to ``write_run_outputs`` so the caller can
    inspect or assert on the result before files are persisted. Fatal
    universe errors raise ``RuntimeError`` from ``resolve_universe``.
    """
    universe = resolve_universe(config)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_date = config.run_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_id = _build_run_id(config, universe)

    # Process each series. ThreadPoolExecutor for IO parallelism; workers
    # return immutable SeriesResult instances and the main thread collates
    # in universe-position order so results are deterministic regardless
    # of completion order.
    results_by_position: Dict[int, SeriesResult] = {}
    if config.max_workers <= 1:
        for entry in universe.series:
            results_by_position[entry.position] = process_series(
                entry, config, run_date=run_date,
            )
    else:
        with ThreadPoolExecutor(max_workers=config.max_workers) as ex:
            futures = {
                ex.submit(process_series, entry, config, run_date=run_date): entry
                for entry in universe.series
            }
            for fut in as_completed(futures):
                entry = futures[fut]
                results_by_position[entry.position] = fut.result()

    series_results = [
        results_by_position[pos]
        for pos in sorted(results_by_position.keys())
    ]
    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return RunResult(
        config=config,
        run_id=run_id,
        run_date=run_date,
        universe=universe,
        series_results=series_results,
        started_at=started_at,
        finished_at=finished_at,
    )


def write_run_outputs(result: RunResult, output_dir: Path) -> Path:
    """Write the canonical run directory and return its path."""
    run_dir = (
        Path(output_dir) / "cross_ticker_confluence" / result.run_id
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    config = result.config
    intervals = list(config.intervals)

    # --- universe_snapshot.json
    snapshot_payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE_UNIVERSE_SNAPSHOT,
        "run_id": result.run_id,
        "universe_mode": result.universe.universe_mode,
        "resolved_at": result.universe.resolved_at,
        "source": {
            "kind": result.universe.source_kind,
            "path": result.universe.source_path,
            "file_sha256": result.universe.source_file_sha256,
        },
        "universe_hash": result.universe.universe_hash,
        "counts": dict(result.universe.counts),
        "series": [
            {
                "position": e.position,
                "series_id": e.series_id,
                "series_kind": e.series_kind,
                "source_symbol": e.source_symbol,
                "normalized_symbol": e.normalized_symbol,
                "valid_symbol": e.valid_symbol,
                "invalid_reason": e.invalid_reason,
            }
            for e in result.universe.series
        ],
    }
    snapshot_path = run_dir / "universe_snapshot.json"
    _write_with_sidecar(
        snapshot_path, snapshot_payload,
        artifact_type=ARTIFACT_TYPE_UNIVERSE_SNAPSHOT,
        config=config,
        input_manifest_hashes=[],
    )

    # --- coverage.json
    coverage_records = [r.coverage_record for r in result.series_results]
    coverage_counts = {
        TLS_SCORED_FULL: 0,
        TLS_SCORED_PARTIAL: 0,
        TLS_SKIPPED_NO_DAILY: 0,
        TLS_SKIPPED_NO_LIBS: 0,
        TLS_INVALID_SYMBOL: 0,
    }
    issue_counts: Dict[str, int] = {}
    for rec in coverage_records:
        coverage_counts[rec["top_level_status"]] = (
            coverage_counts.get(rec["top_level_status"], 0) + 1
        )
        for ic in rec.get("issue_codes", []):
            issue_counts[ic] = issue_counts.get(ic, 0) + 1
    coverage_payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE_COVERAGE,
        "run_id": result.run_id,
        "run_date": result.run_date,
        "universe_mode": result.universe.universe_mode,
        "universe_hash": result.universe.universe_hash,
        "intervals": intervals,
        "records": coverage_records,
    }
    coverage_path = run_dir / "coverage.json"
    all_input_hashes = sorted({
        h for r in result.series_results for h in r.input_manifest_hashes
    })
    _write_with_sidecar(
        coverage_path, coverage_payload,
        artifact_type=ARTIFACT_TYPE_COVERAGE,
        config=config,
        input_manifest_hashes=all_input_hashes,
    )

    # --- rankings.json
    ranking_records = [
        r.ranking_record for r in result.series_results
        if r.ranking_record is not None
    ]
    _sort_rankings(ranking_records)
    rankings_payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE_RANKINGS,
        "run_id": result.run_id,
        "run_date": result.run_date,
        "universe_mode": result.universe.universe_mode,
        "universe_hash": result.universe.universe_hash,
        "intervals": intervals,
        "records": ranking_records,
    }
    rankings_path = run_dir / "rankings.json"
    _write_with_sidecar(
        rankings_path, rankings_payload,
        artifact_type=ARTIFACT_TYPE_RANKINGS,
        config=config,
        input_manifest_hashes=all_input_hashes,
    )

    # --- overlay.json
    overlay_records = [
        r.overlay_record for r in result.series_results
        if r.overlay_record is not None
    ]
    overlay_records.sort(key=lambda r: r["series_id"])
    overlay_payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE_OVERLAY,
        "run_id": result.run_id,
        "run_date": result.run_date,
        "history_days": config.history_days,
        "records": overlay_records,
    }
    overlay_path = run_dir / "overlay.json"
    _write_with_sidecar(
        overlay_path, overlay_payload,
        artifact_type=ARTIFACT_TYPE_OVERLAY,
        config=config,
        input_manifest_hashes=all_input_hashes,
    )

    # --- coverage.csv (one row per ticker; flat summary of statuses)
    cov_csv = run_dir / "coverage.csv"
    cov_csv_rows: List[List[Any]] = []
    for rec in coverage_records:
        cov_csv_rows.append([
            rec["series_id"],
            rec["top_level_status"],
            int(rec.get("eligible_for_rankings", False)),
            ";".join(rec.get("issue_codes", [])),
            rec.get("per_source_status", {}).get("onepass_daily", {}).get("status", ""),
            rec.get("per_source_status", {}).get("spymaster_fallback", {}).get("status", ""),
            rec.get("per_source_status", {}).get("stackbuilder_run", {}).get("status", ""),
        ])
    _write_csv(
        cov_csv,
        ["series_id", "top_level_status", "eligible_for_rankings",
         "issue_codes", "onepass_daily_status",
         "spymaster_fallback_status", "stackbuilder_run_status"],
        cov_csv_rows,
    )

    # --- rankings.csv
    rank_csv = run_dir / "rankings.csv"
    rank_csv_rows: List[List[Any]] = []
    for rec in ranking_records:
        rank_csv_rows.append([
            rec["rank"],
            rec["series_id"],
            rec["rank_group"],
            rec["signal_direction"],
            rec.get("run_date_signal"),
            rec["confluence"]["active_count"],
            rec["confluence"]["total_count"],
            rec["confluence"]["alignment_pct"],
        ])
    _write_csv(
        rank_csv,
        ["rank", "series_id", "rank_group", "signal_direction",
         "run_date_signal", "active_count", "total_count",
         "alignment_pct"],
        rank_csv_rows,
    )

    # --- run_manifest.json (Phase 3-style, self-describing)
    output_artifacts: List[Dict[str, Any]] = [
        _json_artifact_entry("coverage", coverage_path,
                             record_count=len(coverage_records)),
        _json_artifact_entry("rankings", rankings_path,
                             record_count=len(ranking_records)),
        _json_artifact_entry("overlay", overlay_path,
                             record_count=len(overlay_records)),
        _json_artifact_entry("universe_snapshot", snapshot_path,
                             record_count=len(snapshot_payload["series"])),
        _csv_artifact_entry("coverage_csv", cov_csv,
                            derived_from="coverage.json"),
        _csv_artifact_entry("rankings_csv", rank_csv,
                            derived_from="rankings.json"),
    ]
    input_artifacts = []
    seen_paths: set = set()
    for r in result.series_results:
        for ref in r.input_artifact_refs:
            key = (ref.get("source"), ref.get("path"))
            if key in seen_paths:
                continue
            seen_paths.add(key)
            input_artifacts.append(dict(ref))
    input_artifacts.sort(
        key=lambda x: (str(x.get("source", "")), str(x.get("path", "")))
    )
    base_manifest = build_output_manifest(
        artifact_type=ARTIFACT_TYPE_RUN,
        producer_engine=ENGINE_NAME,
        engine_version=ENGINE_VERSION,
        params={
            "universe_mode": config.universe_mode,
            "intervals": intervals,
            "history_days": config.history_days,
            "max_input_age_days": config.max_input_age_days,
            "strict_manifests": bool(config.strict_manifests),
            "max_workers": config.max_workers,
        },
        input_manifest_hashes=all_input_hashes,
        repo_root=PROJECT_DIR,
    )
    run_manifest = {
        **base_manifest,
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "output",
        "artifact_type": ARTIFACT_TYPE_RUN,
        "producer_engine": ENGINE_NAME,
        "engine_version": ENGINE_VERSION,
        "run_id": result.run_id,
        "run_date": result.run_date,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "status": "complete",
        "universe": {
            "universe_hash": result.universe.universe_hash,
            "universe_mode": result.universe.universe_mode,
            "snapshot_path": "universe_snapshot.json",
            "source": {
                "kind": result.universe.source_kind,
                "path": result.universe.source_path,
                "file_sha256": result.universe.source_file_sha256,
            },
            "counts": dict(result.universe.counts),
        },
        "coverage_counts": coverage_counts,
        "issue_counts": issue_counts,
        "input_artifacts": input_artifacts,
        "input_manifest_hashes": all_input_hashes,
        "output_artifacts": output_artifacts,
    }
    _atomic_json_write(run_dir / "run_manifest.json", run_manifest)
    return run_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=ENGINE_NAME,
        description=(
            "Phase 4A cross-ticker multi-timeframe confluence "
            "aggregation engine."
        ),
    )
    p.add_argument(
        "--universe-mode",
        choices=("gtl-active", "tickers", "scorable"),
        default="gtl-active",
    )
    p.add_argument("--tickers", default="")
    p.add_argument(
        "--intervals", default=",".join(DEFAULT_INTERVALS),
    )
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument(
        "--strict-manifests", action="store_true", default=False,
    )
    p.add_argument(
        "--max-workers", type=int,
        default=min(8, os.cpu_count() or 4),
    )
    p.add_argument("--history-days", type=int, default=365)
    p.add_argument("--max-input-age-days", type=int, default=45)
    p.add_argument(
        "--signal-library-dir", default=str(DEFAULT_SIGNAL_LIBRARY_DIR),
    )
    p.add_argument(
        "--spymaster-cache-dir", default=str(DEFAULT_SPYMASTER_CACHE_DIR),
    )
    p.add_argument(
        "--stackbuilder-runs-dir",
        default=str(DEFAULT_STACKBUILDER_RUNS_DIR),
    )
    p.add_argument(
        "--gtl-master-file", default=str(DEFAULT_GTL_MASTER),
    )
    p.add_argument(
        "--run-date", default=None,
        help="Override run_date (YYYY-MM-DD); otherwise UTC today.",
    )
    return p


def config_from_args(args: argparse.Namespace) -> RunConfig:
    intervals = tuple(
        x.strip() for x in str(args.intervals).split(",") if x.strip()
    )
    tickers = tuple(
        x.strip().upper() for x in str(args.tickers).split(",") if x.strip()
    )
    return RunConfig(
        universe_mode=args.universe_mode,
        tickers=tickers,
        intervals=intervals,
        output_dir=Path(args.output_dir),
        strict_manifests=bool(args.strict_manifests),
        max_workers=int(args.max_workers),
        history_days=int(args.history_days),
        max_input_age_days=int(args.max_input_age_days),
        signal_library_dir=Path(args.signal_library_dir),
        spymaster_cache_dir=Path(args.spymaster_cache_dir),
        stackbuilder_runs_dir=Path(args.stackbuilder_runs_dir),
        gtl_master_file=Path(args.gtl_master_file),
        run_date=args.run_date,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)
    config = config_from_args(args)
    try:
        result = run_cross_ticker_confluence(config)
    except RuntimeError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2
    run_dir = write_run_outputs(result, config.output_dir)
    print(f"[OK] wrote run directory: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
