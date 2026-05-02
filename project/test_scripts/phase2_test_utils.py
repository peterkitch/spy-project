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
