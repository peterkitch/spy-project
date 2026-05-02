"""
Phase 2A shared test infrastructure.

This module provides utilities for the Phase 2A test suite. It is a
plain helper module (no `test_` prefix) so pytest does not collect
test functions from it. Other Phase 2A test modules import from
here.

Sections:
  - Synthetic signal-library builders (A1).
  - Cold subprocess import runner (A2).
  - Engine global / env state guard (A3).
  - Static file scanner with allowlists (A4).
  - Phase 1A `freeze()` re-export shim (A5).
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import pickle
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# A5: re-export Phase 1A freeze for snapshot-style assertions.
from phase1a_snapshot_utils import freeze  # noqa: E402


# ---------------------------------------------------------------------------
# A1: synthetic signal-library builders
# ---------------------------------------------------------------------------


def _default_dates(n: int = 30, start: str = "2024-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


def make_synthetic_close_prices(
    dates: pd.DatetimeIndex,
    *,
    base: float = 100.0,
    drift: float = 0.001,
    seed: int = 1,
) -> pd.Series:
    """Deterministic synthetic Close series."""
    rng = np.random.default_rng(seed)
    pct = rng.normal(loc=drift, scale=0.005, size=len(dates))
    closes = base * np.cumprod(1.0 + pct)
    return pd.Series(closes, index=dates, name="Close", dtype=float)


def make_signal_library_dict(
    dates: pd.DatetimeIndex,
    *,
    max_sma_day: int = 114,
    engine_version: str = "1.0.0",
    price_source: str = "Close",
    parity_hash: Optional[str] = None,
    primary_signals: Optional[Sequence[str]] = None,
    daily_top_buy_pairs: Optional[Mapping] = None,
    daily_top_short_pairs: Optional[Mapping] = None,
    extra: Optional[Mapping] = None,
) -> dict:
    """Build a tiny signal-library dict matching the loader contract.

    Defaults produce a minimal-but-valid library: every day is a
    'None' signal with canonical-sentinel daily_top_*_pairs entries.
    Callers can override any field via kwargs.
    """
    n = len(dates)
    if primary_signals is None:
        primary_signals = ["None"] * n
    if daily_top_buy_pairs is None:
        buy_sentinel = (max_sma_day, max_sma_day - 1)
        daily_top_buy_pairs = {pd.Timestamp(d): (buy_sentinel, 0.0) for d in dates}
    if daily_top_short_pairs is None:
        short_sentinel = (max_sma_day - 1, max_sma_day)
        daily_top_short_pairs = {pd.Timestamp(d): (short_sentinel, 0.0) for d in dates}

    sig_int_map = {"Buy": 1, "Short": -1, "None": 0}
    primary_signals_int8 = np.array(
        [sig_int_map.get(s, 0) for s in primary_signals], dtype=np.int8
    )

    lib = {
        "engine_version": engine_version,
        "max_sma_day": int(max_sma_day),
        "price_source": price_source,
        "dates": list(pd.DatetimeIndex(dates)),
        "date_index": list(pd.DatetimeIndex(dates)),
        "primary_signals": list(primary_signals),
        "primary_signals_int8": primary_signals_int8,
        "daily_top_buy_pairs": dict(daily_top_buy_pairs),
        "daily_top_short_pairs": dict(daily_top_short_pairs),
        "num_days": n,
        "build_timestamp": pd.Timestamp.utcnow().isoformat(),
        "end_date": pd.Timestamp(dates[-1]).isoformat(),
    }
    if parity_hash is not None:
        lib["parity_hash"] = parity_hash
    if extra:
        lib.update(extra)
    return lib


def write_signal_library(
    library_dir: Path,
    ticker: str,
    lib: dict,
    *,
    suffix: str = ".pkl",
) -> Path:
    """Write a signal library to ``library_dir/<ticker>{suffix}``."""
    library_dir.mkdir(parents=True, exist_ok=True)
    out = library_dir / f"{ticker}{suffix}"
    with open(out, "wb") as fh:
        pickle.dump(lib, fh)
    return out


def build_poison_price_series(
    *,
    length: int = 150,
    poison_day: int = 120,
    poison_value: float = 1e6,
    base: float = 100.0,
    drift: float = 0.0005,
    seed: int = 7,
    start: str = "2024-01-02",
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Build matched (un-poisoned, poisoned) price DataFrames.

    Phase 2B-1 lookahead poison fixture.

    Returns (df_clean, df_poisoned, poison_idx). Both DataFrames
    share index and columns; only ``Close`` at ``poison_day``
    differs. ``length`` defaults to 150 business days so SMA_113
    and SMA_114 are well-defined by ``poison_day=120``.

    The poison value is intentionally extreme (1e6) so any
    lookahead bug — i.e. any path where day-T's signal depends
    on day-T's Close — produces an obviously different signal.
    """
    if poison_day >= length:
        raise ValueError(f"poison_day ({poison_day}) must be < length ({length})")
    rng = np.random.default_rng(seed)
    pct = rng.normal(loc=drift, scale=0.005, size=length)
    closes = base * np.cumprod(1.0 + pct)
    dates = pd.bdate_range(start=start, periods=length)
    df_clean = pd.DataFrame({"Close": closes}, index=dates)
    closes_poisoned = closes.copy()
    closes_poisoned[poison_day] = poison_value
    df_poisoned = pd.DataFrame({"Close": closes_poisoned}, index=dates)
    return df_clean, df_poisoned, poison_day


def make_synthetic_interval_library(
    library_dir: Path,
    *,
    ticker: str = "AAA",
    intervals: Sequence[str] = ("1d", "1wk", "1mo", "3mo", "1y"),
    n_bars: int = 30,
    engine_version: str = "1.0.0",
) -> Dict[str, Path]:
    """Phase 2B-1: write synthetic interval libraries for confluence.

    Filenames match `signal_library.confluence_analyzer.load_signal_library_interval`:
      1d:    ``<ticker>_stable_v{ver}.pkl``
      else:  ``<ticker>_stable_v{ver}_{interval}.pkl``
    where ``ver`` is ENGINE_VERSION with dots replaced by underscores
    (e.g. ``1_0_0``).

    Each library uses the canonical-sentinel
    `make_signal_library_dict` builder; the per-interval index is a
    business-day index of length ``n_bars``. Returns a dict mapping
    interval -> on-disk path.
    """
    library_dir.mkdir(parents=True, exist_ok=True)
    ver_tag = engine_version.replace(".", "_")
    paths: Dict[str, Path] = {}
    for interval in intervals:
        if interval == "1d":
            fname = f"{ticker}_stable_v{ver_tag}.pkl"
        else:
            fname = f"{ticker}_stable_v{ver_tag}_{interval}.pkl"
        # Use a per-interval rolling start so the synthetic indices
        # share the same trailing date but legitimately differ on
        # earlier ones.
        dates = pd.bdate_range(end="2024-12-30", periods=n_bars, freq="B")
        # Cycle Buy/Short/None so confluence/alignment has non-trivial
        # input.
        sigs = [["Buy", "Short", "None"][i % 3] for i in range(n_bars)]
        lib = make_signal_library_dict(
            dates,
            engine_version=engine_version,
            primary_signals=sigs,
        )
        # confluence_analyzer normalizes 'signals' / 'dates' keys.
        lib["signals"] = list(sigs)
        lib["interval"] = interval
        lib["ticker"] = ticker
        out = library_dir / fname
        with open(out, "wb") as fh:
            pickle.dump(lib, fh)
        paths[interval] = out
    return paths


def make_synthetic_pkl_for_spymaster(
    dates: pd.DatetimeIndex,
    *,
    max_sma_day: int = 114,
) -> dict:
    """Build a Spymaster-style results PKL with canonical sentinels.

    Used by trafficflow's _processed_signals_from_pkl /
    _next_signal_from_pkl tests (Phase 2A D3).
    """
    closes = make_synthetic_close_prices(dates)
    df = pd.DataFrame({"Close": closes.values}, index=dates)
    buy_sentinel = (max_sma_day, max_sma_day - 1)
    short_sentinel = (max_sma_day - 1, max_sma_day)
    return {
        "preprocessed_data": df,
        "active_pairs": ["None"] * len(dates),
        "daily_top_buy_pairs": {pd.Timestamp(d): (buy_sentinel, 0.0) for d in dates},
        "daily_top_short_pairs": {pd.Timestamp(d): (short_sentinel, 0.0) for d in dates},
        "max_sma_day": int(max_sma_day),
        "engine_version": "1.0.0",
        "price_source": "Close",
        "last_processed_date": pd.Timestamp(dates[-1]),
    }


# ---------------------------------------------------------------------------
# A2: cold subprocess import runner
# ---------------------------------------------------------------------------


@dataclass
class SubprocessResult:
    returncode: int
    stdout: str
    stderr: str


def run_cold_import(
    snippet: str,
    *,
    cwd: Path,
    extra_env: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> SubprocessResult:
    """Run a Python subprocess from `cwd` with PROJECT_DIR on PYTHONPATH."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("IMPACT_TRUST_LIBRARY", "0")
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return SubprocessResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


# ---------------------------------------------------------------------------
# A3: engine global/env state guard
# ---------------------------------------------------------------------------


_DEFAULT_ENV_KEYS = (
    "IMPACT_CALENDAR_GRACE_DAYS",
    "ONEPASS_ALLOW_LIB_BASIS",
    "IMPACTSEARCH_ALLOW_LIB_BASIS",
    "IMPACT_FASTPATH_ALLOW_LIB_BASIS",
    "IMPACT_TRUST_LIBRARY",
    "IMPACT_TRUST_MAX_AGE_HOURS",
)

_DEFAULT_GLOBAL_KEYS = (
    "COMBINE_INTERSECTION",
    "SIGNAL_LIB_DIR_RUNTIME",
    "VERBOSE",
    "RUNS_ROOT",
    "DEFAULT_GRACE_DAYS",
)


@contextmanager
def engine_state_guard(
    module,
    *,
    global_keys: Sequence[str] = _DEFAULT_GLOBAL_KEYS,
    env_keys: Sequence[str] = _DEFAULT_ENV_KEYS,
) -> Iterator[None]:
    """Snapshot/restore selected module globals and env vars.

    Tests that drive ``run_for_secondary`` or other functions that
    mutate module-level state should wrap their body with this
    context manager.
    """
    pre_globals = {}
    for key in global_keys:
        if hasattr(module, key):
            pre_globals[key] = getattr(module, key)

    pre_env = {}
    missing_keys = []
    for key in env_keys:
        if key in os.environ:
            pre_env[key] = os.environ[key]
        else:
            missing_keys.append(key)

    try:
        yield
    finally:
        for key, value in pre_globals.items():
            setattr(module, key, value)
        for key, value in pre_env.items():
            os.environ[key] = value
        for key in missing_keys:
            os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# A4: static file scanner with allowlists
# ---------------------------------------------------------------------------


@dataclass
class ScanHit:
    path: Path
    lineno: int
    line: str

    def short(self) -> str:
        rel = self.path.relative_to(PROJECT_DIR) if self.path.is_relative_to(PROJECT_DIR) else self.path
        return f"{rel}:{self.lineno}: {self.line.rstrip()}"


@dataclass
class ScanRule:
    name: str
    pattern: re.Pattern
    # An allowlist entry can be a (file_relpath, lineno) tuple, a file
    # path string (matches any line), or a regex matched against the
    # source line.
    allowed_locations: Sequence[Tuple[str, int]] = field(default_factory=tuple)
    allowed_files: Sequence[str] = field(default_factory=tuple)
    allowed_line_patterns: Sequence[re.Pattern] = field(default_factory=tuple)
    allow_comments: bool = True
    allow_docstrings: bool = True


def iter_python_files(roots: Sequence[Path]) -> Iterator[Path]:
    """Yield .py files under each root using pathlib (no shell globbing).

    Skips __pycache__ directories.
    """
    for root in roots:
        if root.is_file() and root.suffix == ".py":
            yield root
            continue
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            parts = set(p.parts)
            if "__pycache__" in parts:
                continue
            yield p


def _is_comment_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("#")


def scan_for_pattern(
    rule: ScanRule,
    files: Iterable[Path],
) -> List[ScanHit]:
    """Scan files for a regex, applying the rule's allowlists.

    Returns the list of hits that survived the allowlist.
    """
    hits: List[ScanHit] = []
    allowed_loc_set = {(str(rel), int(line)) for rel, line in rule.allowed_locations}
    allowed_files_set = {str(f) for f in rule.allowed_files}

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = (
            str(path.relative_to(PROJECT_DIR))
            if path.is_relative_to(PROJECT_DIR)
            else str(path)
        )
        if rel in allowed_files_set:
            continue
        rel_posix = rel.replace("\\", "/")
        if rel_posix in allowed_files_set:
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            if not rule.pattern.search(line):
                continue
            if rule.allow_comments and _is_comment_line(line):
                continue
            if (rel, lineno) in allowed_loc_set or (rel_posix, lineno) in allowed_loc_set:
                continue
            if any(p.search(line) for p in rule.allowed_line_patterns):
                continue
            hits.append(ScanHit(path=path, lineno=lineno, line=line))
    return hits


def format_hits(hits: Sequence[ScanHit], *, header: str = "") -> str:
    if not hits:
        return ""
    parts = [header] if header else []
    for h in hits:
        parts.append(h.short())
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Convenience: production root paths used by the static guards.
# ---------------------------------------------------------------------------


def production_python_files() -> List[Path]:
    """Return the list of production *.py files in scope for static guards.

    Excludes:
      - QC clone
      - test_scripts
      - md_library / docs
      - __pycache__
      - dist/build
    """
    roots = [
        PROJECT_DIR / "spymaster.py",
        PROJECT_DIR / "onepass.py",
        PROJECT_DIR / "impactsearch.py",
        PROJECT_DIR / "trafficflow.py",
        PROJECT_DIR / "stackbuilder.py",
        PROJECT_DIR / "confluence.py",
        PROJECT_DIR / "canonical_scoring.py",
        PROJECT_DIR / "stale_check.py",
        PROJECT_DIR / "signal_library",
    ]
    return [p for p in iter_python_files(roots) if "QC" not in p.parts]


# ---------------------------------------------------------------------------
# Phase 2B-2A: parity helpers
# ---------------------------------------------------------------------------
#
# These helpers support the within-engine and cross-engine parity tests
# in test_within_engine_parity.py and test_cross_engine_parity.py. They
# normalize per-engine display dicts to a single canonical key set so a
# single canonical CanonicalScore can be compared against multiple engine
# outputs.


# Canonical key names used by the parity comparison helpers.
_CANONICAL_KEYS = (
    "trigger_days",
    "wins",
    "losses",
    "win_rate",
    "std_dev",
    "sharpe",
    "avg_daily_capture",
    "total_capture",
    "t_statistic",
    "p_value",
)


# Per-engine mapping from display dict keys to canonical key names.
# Engines whose display dicts already match the canonical names get the
# identity mapping; only Confluence's compact display labels need
# remapping.
_ENGINE_KEY_MAPS: Dict[str, Dict[str, str]] = {
    "onepass": {
        "Trigger Days": "trigger_days",
        "Wins": "wins",
        "Losses": "losses",
        "Win Ratio (%)": "win_rate",
        "Std Dev (%)": "std_dev",
        "Sharpe Ratio": "sharpe",
        "Avg Daily Capture (%)": "avg_daily_capture",
        "Total Capture (%)": "total_capture",
        "t-Statistic": "t_statistic",
        "p-Value": "p_value",
    },
    "impactsearch": {
        "Trigger Days": "trigger_days",
        "Wins": "wins",
        "Losses": "losses",
        "Win Ratio (%)": "win_rate",
        "Std Dev (%)": "std_dev",
        "Sharpe Ratio": "sharpe",
        "Avg Daily Capture (%)": "avg_daily_capture",
        "Total Capture (%)": "total_capture",
        "t-Statistic": "t_statistic",
        "p-Value": "p_value",
    },
    "stackbuilder": {
        "Trigger Days": "trigger_days",
        "Wins": "wins",
        "Losses": "losses",
        "Win Ratio (%)": "win_rate",
        "Std Dev (%)": "std_dev",
        "Sharpe Ratio": "sharpe",
        "Avg Daily Capture (%)": "avg_daily_capture",
        "Total Capture (%)": "total_capture",
        "t-Statistic": "t_statistic",
        "p-Value": "p_value",
    },
    "confluence": {
        "Triggers": "trigger_days",
        "Wins": "wins",
        "Losses": "losses",
        "Win %": "win_rate",
        "StdDev %": "std_dev",
        "Sharpe": "sharpe",
        "Avg Cap %": "avg_daily_capture",
        "Total %": "total_capture",
        "t": "t_statistic",
        "p": "p_value",
    },
}


# Per-engine display rounding (decimals). Used by
# assert_score_matches_metrics to choose the right tolerance when
# comparing rounded display fields to a full-precision canonical score.
_ENGINE_DISPLAY_ROUNDING: Dict[str, Dict[str, int]] = {
    "onepass": {
        "win_rate": 2,
        "std_dev": 4,
        "sharpe": 2,
        "avg_daily_capture": 4,
        "total_capture": 4,
        "t_statistic": 4,
        "p_value": 4,
    },
    "impactsearch": {
        # impactsearch.calculate_metrics_from_signals returns
        # full-precision floats (no rounding); _metrics_from_ccc rounds
        # like onepass. We use the rounded form here; full-precision
        # callers can override.
        "win_rate": 2,
        "std_dev": 4,
        "sharpe": 2,
        "avg_daily_capture": 4,
        "total_capture": 4,
        "t_statistic": 4,
        "p_value": 4,
    },
    "stackbuilder": {
        "win_rate": 2,
        "std_dev": 4,
        "sharpe": 2,
        "avg_daily_capture": 4,
        "total_capture": 4,
        "t_statistic": 4,
        "p_value": 4,
    },
    "confluence": {
        "win_rate": 2,
        "std_dev": 4,
        "sharpe": 2,
        "avg_daily_capture": 4,
        "total_capture": 4,
        "t_statistic": 4,
        "p_value": 4,
    },
}


def make_capture_mask_fixture(
    *,
    n_days: int = 10,
    seed: int = 11,
    include_zero_trigger_day: bool = True,
    start: str = "2024-01-02",
) -> Tuple[pd.Series, pd.Series]:
    """Phase 2B-2A: build a deterministic capture series + signal mask.

    Returns (captures_pct, trigger_mask):
      - captures_pct: pd.Series of daily captures in PERCENT POINTS
        (spec §13 unit), index is a business-day DatetimeIndex of length
        n_days.
      - trigger_mask: pd.Series of booleans on the same index. True
        means the day was an active Buy/Short signal day.

    When ``include_zero_trigger_day=True`` (default) at least one
    trigger day in the mask carries a capture of exactly 0.0 (spec
    §15: zero-return trigger day must count as a loss). The fixture
    is used by both within-engine and cross-engine parity tests to
    pin canonical-vs-engine consistency on this corner case.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n_days)
    raw_caps = rng.normal(loc=0.0, scale=1.0, size=n_days)
    # First half are triggers, second half are non-trigger 'None' days.
    mask_arr = np.array([True] * (n_days // 2 + 2) + [False] * (n_days - n_days // 2 - 2), dtype=bool)
    if len(mask_arr) != n_days:
        # Pad/trim defensively.
        mask_arr = mask_arr[:n_days]
        if len(mask_arr) < n_days:
            mask_arr = np.concatenate([mask_arr, np.zeros(n_days - len(mask_arr), dtype=bool)])

    # Force at least one trigger day to carry a zero capture.
    if include_zero_trigger_day:
        # Find the first trigger day and set its capture to 0.0.
        trig_idx = np.where(mask_arr)[0]
        if len(trig_idx) >= 2:
            raw_caps[trig_idx[1]] = 0.0
        elif len(trig_idx) == 1:
            raw_caps[trig_idx[0]] = 0.0

    # Captures are zero on non-trigger days (spec §13/§14 contract).
    caps_arr = np.where(mask_arr, raw_caps, 0.0)
    return (
        pd.Series(caps_arr, index=dates, dtype=float, name="captures"),
        pd.Series(mask_arr, index=dates, dtype=bool, name="trigger_mask"),
    )


def make_price_frame_from_returns(
    returns: pd.Series,
    *,
    base: float = 100.0,
) -> pd.DataFrame:
    """Phase 2B-2A: convert a decimal-returns series to a Close-price frame.

    ``returns`` must be a pd.Series of DECIMAL daily returns
    (e.g. 0.01 for +1%). The first entry is treated as the day-0 return
    and applied to ``base`` (so day-0 Close = base * (1 + returns[0])).

    Returns a single-column DataFrame indexed identically to ``returns``
    with a ``Close`` column suitable for ``onepass``/``impactsearch``
    fixtures.
    """
    if not isinstance(returns, pd.Series):
        raise TypeError("returns must be a pd.Series of decimal daily returns")
    closes = base * np.cumprod(1.0 + returns.astype(float).to_numpy())
    return pd.DataFrame({"Close": closes}, index=returns.index)


def normalize_metric_dict(engine: str, metrics: Mapping) -> Dict[str, object]:
    """Phase 2B-2A: normalize a per-engine display dict to canonical keys.

    Unknown engines pass through with the canonical-key identity map.
    Missing-from-display canonical keys come back absent. ``"N/A"``
    sentinels for ``t_statistic`` / ``p_value`` are passed through
    verbatim so callers can choose how to handle them.
    """
    if metrics is None:
        return {}
    key_map = _ENGINE_KEY_MAPS.get(engine, {})
    out: Dict[str, object] = {}
    for src, val in metrics.items():
        canonical = key_map.get(src, src)
        out[canonical] = val
    return out


def _half_decimal_unit(decimals: int) -> float:
    return 0.5 * (10 ** (-decimals)) + 1e-12


def assert_score_matches_metrics(score, metrics: Mapping, engine: str) -> None:
    """Phase 2B-2A: assert engine display dict matches a canonical
    ``CanonicalScore`` for the same input.

    Comparison policy:
      - Integer fields (``trigger_days``, ``wins``, ``losses``) compared
        exactly.
      - Float display fields compared against ``round(score.field,
        decimals)`` with ``pytest.approx(..., abs=half_decimal_unit +
        epsilon)`` so ULP-level rounding differences don't create false
        diffs.
      - ``t_statistic`` and ``p_value`` compared with the same
        rounded-display tolerance when present and not the ``"N/A"``
        sentinel; if either side is ``None`` / ``"N/A"``, they must
        both signal absence.

    Per-engine rounding is documented in ``_ENGINE_DISPLAY_ROUNDING``
    above.
    """
    import pytest as _pytest  # local import; this helper is test-only

    canon = normalize_metric_dict(engine, metrics)
    rounding = _ENGINE_DISPLAY_ROUNDING.get(engine, {})

    # Integer fields exact.
    for k in ("trigger_days", "wins", "losses"):
        if k in canon:
            assert int(canon[k]) == int(getattr(score, k)), (
                f"[{engine}] {k}: engine={canon[k]!r} vs canonical={getattr(score, k)!r}"
            )

    def _is_na(v) -> bool:
        return v is None or v == "N/A"

    # Float display fields: compare to round(score.field, decimals).
    float_fields = (
        "win_rate", "std_dev", "sharpe", "avg_daily_capture", "total_capture",
    )
    for k in float_fields:
        if k not in canon:
            continue
        decimals = rounding.get(k, 6)
        engine_val = float(canon[k])
        canon_val = float(getattr(score, k))
        rounded_canon = round(canon_val, decimals)
        tol = _half_decimal_unit(decimals)
        assert engine_val == _pytest.approx(rounded_canon, abs=tol), (
            f"[{engine}] {k}: engine={engine_val!r} vs canonical "
            f"rounded@{decimals}={rounded_canon!r} (raw={canon_val!r})"
        )

    # t_statistic / p_value: handle None / "N/A" sentinels both sides.
    for k in ("t_statistic", "p_value"):
        if k not in canon:
            continue
        engine_val = canon[k]
        canon_val = getattr(score, k)
        if _is_na(engine_val) or _is_na(canon_val):
            assert _is_na(engine_val) and _is_na(canon_val), (
                f"[{engine}] {k}: engine={engine_val!r} vs canonical={canon_val!r} "
                "(one is N/A but not both)"
            )
            continue
        decimals = rounding.get(k, 6)
        rounded_canon = round(float(canon_val), decimals)
        tol = _half_decimal_unit(decimals)
        assert float(engine_val) == _pytest.approx(rounded_canon, abs=tol), (
            f"[{engine}] {k}: engine={engine_val!r} vs canonical "
            f"rounded@{decimals}={rounded_canon!r} (raw={canon_val!r})"
        )
