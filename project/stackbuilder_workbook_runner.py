"""Phase 6I-70: dry-run-first headless StackBuilder runner scaffold.

Phase B of the StackBuilder headless runner work. Phase A scoping
doc:
``md_library/shared/2026-05-20_PHASE_6I_69_STACKBUILDER_RUNNER_EXECUTION_SURFACE.md``.

Behavior contract:

  * Dry-run is the default. No engine call, no output writes.
  * Write execution requires all of ``--write``,
    ``--allow-network-fetch``, ``--duration-budget-minutes``, and
    ``--operator-budget-label``.
  * No hidden full-universe fallback. Each ``--primary-source``
    documents exactly what it needs.
  * Process-conflict check runs before any write execution.
  * stdout is exactly one JSON object emitted by ``main``.
  * stderr carries tqdm progress, conflict reports, captured engine
    excerpts, and tracebacks.
  * ``stackbuilder`` is imported only inside
    ``_default_stackbuilder_callable``, wrapped in
    ``contextlib.redirect_stdout`` so engine import-time prints and
    per-call ``print`` statements cannot contaminate the runner's
    JSON stdout.
  * Per-secondary ``SystemExit`` is converted to ``status="error"``
    entries; the batch continues.
  * Runner-controlled ``selected_build.json`` writes go through a
    temp partial + ``os.replace`` for atomicity. Operator pins via
    ``selected_build.pinned.json`` block automatic updates unless
    ``--unpin`` is supplied.

No top-level imports of ``stackbuilder``, ``onepass``,
``impactsearch``, ``dash``, ``dash_bootstrap_components``,
``yfinance``, ``plotly``, or ``pandas``.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional, Sequence

try:
    import tqdm as _tqdm  # stdlib-adjacent; safe to import here.
except Exception:  # pragma: no cover - exercised only when tqdm absent
    _tqdm = None


RUNNER_NAME = "stackbuilder_workbook_runner"
RUNNER_VERSION = "0.1.0"
SCHEMA_VERSION = 1

DEFAULT_OUTDIR = "output/stackbuilder"
DEFAULT_OUTPUT_FORMAT = "xlsx"
DEFAULT_IMPACT_XLSX_DIR = "output/impactsearch"
DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS = 45

DEFAULT_K_MAX = 6
DEFAULT_TOP_N = 20
DEFAULT_BOTTOM_N = 20
DEFAULT_EXHAUSTIVE_K = 4
DEFAULT_SEARCH = "beam"
DEFAULT_BEAM_WIDTH = 12
DEFAULT_MIN_TRIGGER_DAYS = 30
DEFAULT_SHARPE_EPS = 0.01
DEFAULT_SEED_BY = "total_capture"
DEFAULT_OPTIMIZE_BY = "auto"
DEFAULT_JOBS = 1

PROCESS_CONFLICT_PATTERNS: tuple[str, ...] = (
    "onepass.py",
    "onepass_workbook_runner.py",
    "impactsearch.py",
    "impactsearch_workbook_runner.py",
    "stackbuilder.py",
    "stackbuilder_workbook_runner.py",
    "trafficflow.py",
    "spymaster.py",
    "confluence.py",
    "multi_timeframe_builder.py",
    "signal_library_stable_promotion_writer.py",
)

SELECTION_POLICY = "v2.total_capture_then_latest"
DEFAULT_TOTAL_CAPTURE_TOLERANCE = 1e-9

_RAW_TICKER_SPLIT_RE = re.compile(r"[,\s]+")
_PKL_TICKER_RE = re.compile(r"^(?P<ticker>.+)_stable_v[0-9_]+\.pkl$")

# Phase 6I-71: characters legal in a runner-owned progress filename.
# Mirrors the engine's ``vendor_secondary_clean = vendor_secondary
# .replace('.', '_')`` sanitization (stackbuilder.py:2337) and extends
# it to strip any other path / shell metacharacters; the caret in
# tickers like ``^GSPC`` is safe on NTFS per the engine's own comment,
# so it's preserved.
_SAFE_SECONDARY_TRANSLATE = str.maketrans({
    ".": "_",
    "/": "_",
    "\\": "_",
    " ": "_",
})


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse runner CLI args. Defaults match Phase A locked v1 values."""
    p = argparse.ArgumentParser(
        prog=RUNNER_NAME,
        description=(
            "Dry-run-first headless StackBuilder runner. Default reads "
            "the ImpactSearch XLSX path and refuses to launch without "
            "explicit budget + network authorization."
        ),
    )
    # Secondaries
    p.add_argument("--secondaries", default=None)
    p.add_argument("--secondaries-file", default=None)

    # Primary source
    p.add_argument(
        "--primary-source",
        choices=("explicit_csv", "file", "impact_xlsx", "signal_library_dir"),
        default="impact_xlsx",
    )
    p.add_argument("--primaries", default=None)
    p.add_argument("--primaries-file", default=None)
    p.add_argument("--signal-library-dir", default=None)
    p.add_argument(
        "--impact-xlsx-dir",
        default=DEFAULT_IMPACT_XLSX_DIR,
    )
    p.add_argument(
        "--impact-xlsx-max-age-days",
        type=int,
        default=DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS,
    )

    # Output
    p.add_argument("--outdir", default=DEFAULT_OUTDIR)
    p.add_argument(
        "--output-format",
        choices=("xlsx", "parquet", "csv"),
        default=DEFAULT_OUTPUT_FORMAT,
    )

    # Engine knobs
    p.add_argument("--k-max", type=int, default=DEFAULT_K_MAX)
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    p.add_argument("--bottom-n", type=int, default=DEFAULT_BOTTOM_N)
    p.add_argument("--exhaustive-k", type=int, default=DEFAULT_EXHAUSTIVE_K)
    p.add_argument(
        "--search", choices=("beam", "exhaustive"), default=DEFAULT_SEARCH,
    )
    p.add_argument("--beam-width", type=int, default=DEFAULT_BEAM_WIDTH)
    p.add_argument(
        "--min-trigger-days", type=int, default=DEFAULT_MIN_TRIGGER_DAYS,
    )
    p.add_argument("--sharpe-eps", type=float, default=DEFAULT_SHARPE_EPS)
    # Phase 6I-73: Sharpe is no longer a supported selection /
    # seed / optimize / ranking criterion. Total Capture is the
    # only supported metric. ``auto`` on --optimize-by is retained
    # for backward compatibility and resolves deterministically to
    # ``total_capture`` (i.e. to ``seed_by``).
    p.add_argument(
        "--seed-by",
        choices=("total_capture",),
        default=DEFAULT_SEED_BY,
    )
    p.add_argument(
        "--optimize-by",
        choices=("total_capture", "auto"),
        default=DEFAULT_OPTIMIZE_BY,
    )
    p.add_argument("--allow-decreasing", action="store_true", default=False)
    p.add_argument(
        "--k-patience",
        type=int,
        default=1,
        help=(
            "Phase 6I-78: how many non-improving / no-valid K levels "
            "the engine will tolerate before stopping. Default 1 "
            "preserves the runner's prior hardcoded behavior."
        ),
    )
    p.add_argument("--grace-days", type=int, default=None)
    p.add_argument("--threads", default=None)
    p.add_argument("--jobs", type=int, default=DEFAULT_JOBS)

    # Write + budget gates
    p.add_argument("--write", action="store_true", default=False)
    p.add_argument("--allow-network-fetch", action="store_true", default=False)
    p.add_argument("--update-selected", action="store_true", default=False)
    p.add_argument("--pin-build", default=None)
    p.add_argument("--unpin", action="store_true", default=False)
    p.add_argument("--duration-budget-minutes", type=int, default=None)
    p.add_argument("--operator-budget-label", default=None)

    p.add_argument(
        "--skip-durable-validation",
        action="store_true",
        default=False,
        help=(
            "Phase 6I-75: skip _prepare_stackbuilder_durable_validation "
            "for every secondary in this run. Default off preserves the "
            "Phase 5C fail-closed contract."
        ),
    )
    p.add_argument("--no-progress", action="store_true", default=False)
    # Phase 6I-71: runner-owned progress directory. Default ``None``
    # resolves to ``<outdir>/_progress`` so an isolated --outdir (e.g.
    # Phase C smoke under logs/) does NOT default progress writes back
    # to canonical ``output/stackbuilder/_progress``.
    p.add_argument("--progress-dir", default=None)
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Ticker / source resolution
# ---------------------------------------------------------------------------


def _parse_ticker_blob(blob: str, *, uppercase: bool = True) -> list[str]:
    """Raw-string parse: split on comma/whitespace/newlines, drop ``#``
    comment lines, dedupe while preserving first-occurrence order.

    Preserves literal ``NA`` / ``NAN`` tickers (no NaN coercion path).
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in str(blob or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for tok in _RAW_TICKER_SPLIT_RE.split(stripped):
            t = tok.strip()
            if not t or t.startswith("#"):
                continue
            if uppercase:
                t = t.upper()
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
    return out


def resolve_secondaries(args: argparse.Namespace) -> dict:
    """Resolve the secondary ticker universe.

    Preserves order and meaningful punctuation (``^GSPC``, ``BRK-B``,
    ``BRK.B``). Preserves literal ``NA`` / ``NAN``. Refuses on empty
    input.
    """
    issues: list[str] = []
    raw_parts: list[str] = []
    if getattr(args, "secondaries", None):
        raw_parts.append(str(args.secondaries))
    secondaries_file = getattr(args, "secondaries_file", None)
    if secondaries_file:
        try:
            raw_parts.append(Path(secondaries_file).read_text(encoding="utf-8"))
        except OSError as exc:
            issues.append(f"secondaries_file_unreadable: {type(exc).__name__}")

    if not raw_parts:
        return {
            "status": "refused",
            "secondaries": [],
            "issues": ["secondaries_required"],
        }

    secs = _parse_ticker_blob("\n".join(raw_parts), uppercase=True)
    if not secs:
        return {
            "status": "refused",
            "secondaries": [],
            "issues": ["secondaries_empty_after_parse"] + issues,
        }
    return {
        "status": "ok",
        "secondaries": secs,
        "issues": issues,
    }


def _scan_signal_library_dir(dirpath: str) -> tuple[list[str], list[str]]:
    """Return (tickers, issues) by scanning ``*_stable_v*.pkl`` under
    ``dirpath`` using a suffix-stripping regex.

    Uses ``os.scandir`` directly; does NOT import pandas / stackbuilder.
    """
    tickers: list[str] = []
    issues: list[str] = []
    seen: set[str] = set()
    root = Path(dirpath)
    if not root.exists() or not root.is_dir():
        issues.append("signal_library_dir_missing")
        return tickers, issues
    try:
        with os.scandir(root) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                m = _PKL_TICKER_RE.match(entry.name)
                if not m:
                    continue
                tk = m.group("ticker")
                if tk in seen:
                    continue
                seen.add(tk)
                tickers.append(tk)
    except OSError as exc:  # pragma: no cover - defensive
        issues.append(f"signal_library_dir_scan_error: {type(exc).__name__}")
    return sorted(tickers), issues


def resolve_primaries(args: argparse.Namespace) -> dict:
    """Resolve the primary ticker universe per --primary-source.

    No hidden full-universe fallback. Each source documents exactly
    what it requires; missing required input is a ``refused`` status.
    """
    source = getattr(args, "primary_source", "impact_xlsx")
    primaries: list[str] = []
    issues: list[str] = []
    source_path: Optional[str] = None
    status = "ok"

    if source == "impact_xlsx":
        impact_dir = getattr(args, "impact_xlsx_dir", DEFAULT_IMPACT_XLSX_DIR)
        source_path = impact_dir
        exists = bool(impact_dir) and Path(impact_dir).is_dir()
        if not exists:
            issues.append("impact_xlsx_dir_missing")
            if bool(getattr(args, "write", False)):
                status = "refused"
        # primaries list intentionally empty under impact_xlsx; engine
        # consumes the XLSX directly.
    elif source == "explicit_csv":
        raw = getattr(args, "primaries", None)
        if not raw:
            return {
                "status": "refused",
                "primary_source": source,
                "primaries": [],
                "primary_count": 0,
                "source_path": None,
                "issues": ["primaries_required_for_explicit_csv"],
            }
        primaries = _parse_ticker_blob(raw, uppercase=True)
        if not primaries:
            issues.append("primaries_empty_after_parse")
            status = "refused"
    elif source == "file":
        path = getattr(args, "primaries_file", None)
        if not path:
            return {
                "status": "refused",
                "primary_source": source,
                "primaries": [],
                "primary_count": 0,
                "source_path": None,
                "issues": ["primaries_file_required_for_file_source"],
            }
        source_path = path
        try:
            blob = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            return {
                "status": "refused",
                "primary_source": source,
                "primaries": [],
                "primary_count": 0,
                "source_path": path,
                "issues": [f"primaries_file_unreadable: {type(exc).__name__}"],
            }
        primaries = _parse_ticker_blob(blob, uppercase=True)
        if not primaries:
            issues.append("primaries_file_empty_after_parse")
            status = "refused"
    elif source == "signal_library_dir":
        path = getattr(args, "signal_library_dir", None)
        if not path:
            return {
                "status": "refused",
                "primary_source": source,
                "primaries": [],
                "primary_count": 0,
                "source_path": None,
                "issues": ["signal_library_dir_required"],
            }
        source_path = path
        scanned, scan_issues = _scan_signal_library_dir(path)
        primaries = scanned
        issues.extend(scan_issues)
        if not primaries:
            issues.append("signal_library_dir_yielded_no_primaries")
            status = "refused"
    else:  # pragma: no cover - argparse should restrict
        return {
            "status": "refused",
            "primary_source": source,
            "primaries": [],
            "primary_count": 0,
            "source_path": None,
            "issues": [f"unknown_primary_source: {source!r}"],
        }

    return {
        "status": status,
        "primary_source": source,
        "primaries": primaries,
        "primary_count": len(primaries),
        "source_path": source_path,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Args namespace for the engine
# ---------------------------------------------------------------------------


def build_stackbuilder_args_namespace(
    args: argparse.Namespace,
    secondary: str,
    primaries_resolution: dict,
    progress_path: Optional[str] = None,
) -> SimpleNamespace:
    """Build a SimpleNamespace compatible with
    ``stackbuilder.run_for_secondary``.

    Mirrors Phase A locked v1 defaults; maps runner ``--k-max`` to the
    engine's ``max_k``; resolves ``optimize_by="auto"`` to ``seed_by``;
    sets ``prefer_impact_xlsx=True`` when the primary source is
    ``impact_xlsx``. Does NOT import ``stackbuilder``.
    """
    # Phase 6I-73: hard-pin to Total Capture. ``sharpe`` is rejected
    # by the CLI parser; if anything sneaks through programmatically
    # we normalize here so downstream engine paths see only the
    # supported value.
    seed_by = getattr(args, "seed_by", DEFAULT_SEED_BY) or DEFAULT_SEED_BY
    if seed_by == "sharpe":
        seed_by = "total_capture"
    optimize_by = getattr(args, "optimize_by", DEFAULT_OPTIMIZE_BY)
    if optimize_by in (None, "auto", "sharpe"):
        optimize_by = seed_by
    prefer_impact_xlsx = (
        (primaries_resolution or {}).get("primary_source", "impact_xlsx")
        == "impact_xlsx"
    )

    return SimpleNamespace(
        secondary=secondary,
        secondaries=None,
        primaries=None,
        top_n=int(getattr(args, "top_n", DEFAULT_TOP_N)),
        bottom_n=int(getattr(args, "bottom_n", DEFAULT_BOTTOM_N)),
        max_k=int(getattr(args, "k_max", DEFAULT_K_MAX)),
        exhaustive_k=int(getattr(args, "exhaustive_k", DEFAULT_EXHAUSTIVE_K)),
        min_trigger_days=int(
            getattr(args, "min_trigger_days", DEFAULT_MIN_TRIGGER_DAYS)
        ),
        sharpe_eps=float(getattr(args, "sharpe_eps", DEFAULT_SHARPE_EPS)),
        seed_by=seed_by,
        optimize_by=optimize_by,
        allow_decreasing=bool(getattr(args, "allow_decreasing", False)),
        prefer_impact_xlsx=prefer_impact_xlsx,
        impact_xlsx_dir=getattr(args, "impact_xlsx_dir", DEFAULT_IMPACT_XLSX_DIR),
        impact_xlsx_max_age_days=int(
            getattr(
                args,
                "impact_xlsx_max_age_days",
                DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS,
            )
        ),
        search=getattr(args, "search", DEFAULT_SEARCH),
        beam_width=int(getattr(args, "beam_width", DEFAULT_BEAM_WIDTH)),
        outdir=getattr(args, "outdir", DEFAULT_OUTDIR),
        output_format=getattr(args, "output_format", DEFAULT_OUTPUT_FORMAT),
        grace_days=getattr(args, "grace_days", None),
        threads=getattr(args, "threads", None) or "auto",
        jobs=int(getattr(args, "jobs", DEFAULT_JOBS)),
        progress_path=progress_path,
        # Carry deprecated engine fields as inert defaults so the engine
        # does not raise on missing attributes; not surfaced via runner
        # CLI.
        alpha=0.05,
        min_marginal_capture=0.0,
        fail_on_missing_cache=False,
        serve=False,
        port=8054,
        save_stats=False,
        verbose=False,
        no_progress=bool(getattr(args, "no_progress", False)),
        both_modes=False,
        k_patience=int(getattr(args, "k_patience", 1)),
        combine_mode="intersection",
        strict_manifests=False,
        signal_lib_dir=None,
        skip_durable_validation=bool(
            getattr(args, "skip_durable_validation", False)
        ),
    )


# ---------------------------------------------------------------------------
# Process-conflict check
# ---------------------------------------------------------------------------


def _process_lines_psutil(own_pid: int) -> Optional[list[tuple[int, str]]]:
    try:
        import psutil  # type: ignore
    except Exception:
        return None
    out: list[tuple[int, str]] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = int(proc.info.get("pid") or -1)
            if pid <= 0 or pid == own_pid:
                continue
            cmdline_parts = proc.info.get("cmdline") or []
            if not cmdline_parts:
                continue
            cmdline = " ".join(str(p) for p in cmdline_parts)
            out.append((pid, cmdline))
        except Exception:
            continue
    return out


def _process_lines_windows(own_pid: int) -> Optional[list[tuple[int, str]]]:
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId, CommandLine | "
            "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
        ),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=20, check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        pid_str, cmdline = parts
        try:
            pid = int(pid_str.strip())
        except ValueError:
            continue
        if pid == own_pid or pid <= 0:
            continue
        cmdline = cmdline.strip()
        if not cmdline:
            continue
        out.append((pid, cmdline))
    return out


def _process_lines_posix(own_pid: int) -> Optional[list[tuple[int, str]]]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,args"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out: list[tuple[int, str]] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, cmdline = parts
        try:
            pid = int(pid_str.strip())
        except ValueError:
            continue
        if pid == own_pid or pid <= 0:
            continue
        out.append((pid, cmdline))
    return out


def check_process_conflicts(
    write_requested: bool = False,
    *,
    patterns: Sequence[str] = PROCESS_CONFLICT_PATTERNS,
    own_pid: Optional[int] = None,
) -> dict:
    """Read-only process enumeration; returns conflicting command lines.

    Excludes the current process PID so the runner does not block
    itself. Falls back through psutil → Windows ``Get-CimInstance`` →
    POSIX ``ps``. On total query failure: when ``write_requested`` is
    True the result is ``status="error"`` (fail-closed for writes); for
    dry-run the result is ``status="unknown"`` (advisory only).
    """
    own_pid = own_pid if own_pid is not None else os.getpid()
    lines: Optional[list[tuple[int, str]]] = _process_lines_psutil(own_pid)
    queried_via = "psutil"
    if lines is None:
        if os.name == "nt":
            lines = _process_lines_windows(own_pid)
            queried_via = "powershell_cim"
        else:
            lines = _process_lines_posix(own_pid)
            queried_via = "ps"

    if lines is None:
        status = "error" if write_requested else "unknown"
        return {
            "status": status,
            "conflicts": [],
            "queried_via": queried_via,
            "error": "process_enumeration_failed",
        }

    conflicts: list[str] = []
    for pid, cmdline in lines:
        if any(pat in cmdline for pat in patterns):
            conflicts.append(f"pid={pid} cmd={cmdline}")
    return {
        "status": "blocked" if conflicts else "ok",
        "conflicts": conflicts,
        "queried_via": queried_via,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_head() -> tuple[Optional[str], Optional[bool]]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        commit = head.stdout.strip() if head.returncode == 0 else None
    except Exception:
        commit = None
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        dirty = (
            bool(status.stdout.strip()) if status.returncode == 0 else None
        )
    except Exception:
        dirty = None
    return commit, dirty


def _safe_secondary_filename(secondary: str) -> str:
    """Return a filesystem-safe stem for ``secondary``.

    Mirrors the engine's ``replace('.', '_')`` sanitization
    (stackbuilder.py:2337) and additionally strips path / whitespace
    metacharacters. Preserves caret (``^GSPC``) per the engine's
    NTFS-safe comment.
    """
    return str(secondary or "").translate(_SAFE_SECONDARY_TRANSLATE)


def _effective_progress_dir(args: argparse.Namespace) -> str:
    """Resolve the runner's progress-write directory.

    - When ``--progress-dir`` is supplied (truthy), use it verbatim.
    - Otherwise default to ``<outdir>/_progress``. **This is the
      Phase 6I-71 invariant**: an isolated ``--outdir`` (e.g. Phase C
      under ``logs/...``) must NOT default progress writes back to
      canonical ``output/stackbuilder/_progress``.
    """
    explicit = getattr(args, "progress_dir", None)
    if explicit:
        return str(explicit)
    outdir = getattr(args, "outdir", DEFAULT_OUTDIR)
    return os.path.join(str(outdir), "_progress")


_PROGRESS_PATH_COUNTER = 0


def build_progress_path(args: argparse.Namespace, secondary: str) -> str:
    """Compute a per-secondary progress-file path under the effective
    progress directory.

    Filename layout::

        <effective_progress_dir>/<SAFE_SECONDARY>_<pid>_<ts_ns>_<seq>.json

    ``ts_ns`` is nanosecond-precision so repeated same-secondary calls
    inside a single process tick still produce distinct filenames;
    ``seq`` is a monotonic in-process counter that defends against the
    rare clock collision under tools that quantize ``time_ns`` to
    microseconds on some platforms.
    """
    global _PROGRESS_PATH_COUNTER
    _PROGRESS_PATH_COUNTER += 1
    safe = _safe_secondary_filename(secondary)
    fname = (
        f"{safe}_{os.getpid()}_{time.time_ns()}_{_PROGRESS_PATH_COUNTER}.json"
    )
    return os.path.join(_effective_progress_dir(args), fname)


def _effective_config(args: argparse.Namespace) -> dict:
    return {
        "k_max": int(getattr(args, "k_max", DEFAULT_K_MAX)),
        "top_n": int(getattr(args, "top_n", DEFAULT_TOP_N)),
        "bottom_n": int(getattr(args, "bottom_n", DEFAULT_BOTTOM_N)),
        "exhaustive_k": int(getattr(args, "exhaustive_k", DEFAULT_EXHAUSTIVE_K)),
        "search": getattr(args, "search", DEFAULT_SEARCH),
        "beam_width": int(getattr(args, "beam_width", DEFAULT_BEAM_WIDTH)),
        "min_trigger_days": int(
            getattr(args, "min_trigger_days", DEFAULT_MIN_TRIGGER_DAYS)
        ),
        "sharpe_eps": float(getattr(args, "sharpe_eps", DEFAULT_SHARPE_EPS)),
        "seed_by": getattr(args, "seed_by", DEFAULT_SEED_BY),
        "optimize_by": getattr(args, "optimize_by", DEFAULT_OPTIMIZE_BY),
        "allow_decreasing": bool(getattr(args, "allow_decreasing", False)),
        "k_patience": int(getattr(args, "k_patience", 1)),
        "grace_days": getattr(args, "grace_days", None),
        "output_format": getattr(args, "output_format", DEFAULT_OUTPUT_FORMAT),
        "outdir": getattr(args, "outdir", DEFAULT_OUTDIR),
        "progress_dir": getattr(args, "progress_dir", None),
        "effective_progress_dir": _effective_progress_dir(args),
        "jobs": int(getattr(args, "jobs", DEFAULT_JOBS)),
        "primary_source": getattr(args, "primary_source", "impact_xlsx"),
        "impact_xlsx_dir": getattr(
            args, "impact_xlsx_dir", DEFAULT_IMPACT_XLSX_DIR,
        ),
        "impact_xlsx_max_age_days": int(
            getattr(
                args,
                "impact_xlsx_max_age_days",
                DEFAULT_IMPACT_XLSX_MAX_AGE_DAYS,
            )
        ),
        "skip_durable_validation": bool(
            getattr(args, "skip_durable_validation", False)
        ),
    }


def build_run_plan(
    args: argparse.Namespace,
    secondaries_resolution: Optional[dict] = None,
    primaries_resolution: Optional[dict] = None,
    process_conflict_result: Optional[dict] = None,
) -> dict:
    """Construct a JSON-safe dry-run / preflight plan."""
    write_requested = bool(getattr(args, "write", False))
    network_authorized = bool(getattr(args, "allow_network_fetch", False))
    budget_minutes = getattr(args, "duration_budget_minutes", None)
    budget_label = getattr(args, "operator_budget_label", None)

    preflight_issues: list[str] = []

    if secondaries_resolution is None:
        secondaries_resolution = {
            "status": "refused",
            "secondaries": [],
            "issues": ["secondaries_required"],
        }
    if primaries_resolution is None:
        primaries_resolution = {
            "status": "refused",
            "primary_source": getattr(args, "primary_source", "impact_xlsx"),
            "primaries": [],
            "primary_count": 0,
            "source_path": None,
            "issues": ["primaries_resolution_missing"],
        }

    hard_refusal = False
    if secondaries_resolution.get("status") != "ok":
        preflight_issues.append("secondaries_resolution_refused")
        hard_refusal = True
    if primaries_resolution.get("status") == "refused":
        preflight_issues.append("primaries_resolution_refused")
        hard_refusal = True

    # Write-gate refusals
    if write_requested:
        if not network_authorized:
            preflight_issues.append("network_fetch_required_but_not_authorized")
        if not isinstance(budget_minutes, int) or budget_minutes <= 0:
            preflight_issues.append("duration_budget_required")
        if not isinstance(budget_label, str) or not budget_label.strip():
            preflight_issues.append("operator_budget_label_required")

    # Decide status
    blocked_by_conflict = bool(
        process_conflict_result
        and process_conflict_result.get("status") == "blocked"
    )

    if blocked_by_conflict:
        status = "blocked_process_conflict"
    elif hard_refusal:
        status = "refused"
    elif preflight_issues:
        status = "refused" if write_requested else "dry_run"
    elif not write_requested:
        status = "dry_run"
    else:
        status = "ready"

    would_call_engine = (
        write_requested
        and network_authorized
        and isinstance(budget_minutes, int)
        and budget_minutes > 0
        and isinstance(budget_label, str)
        and budget_label.strip()
        and not preflight_issues
        and not blocked_by_conflict
    )

    secs = secondaries_resolution.get("secondaries") or []
    effective_progress_dir = _effective_progress_dir(args)
    skip_durable_validation = bool(
        getattr(args, "skip_durable_validation", False)
    )
    per_secondary_plan = [
        {
            "secondary": s,
            "primary_source": primaries_resolution.get("primary_source"),
            "primary_count": primaries_resolution.get("primary_count"),
            "outdir": os.path.join(
                getattr(args, "outdir", DEFAULT_OUTDIR),
                _safe_secondary_filename(s),
            ),
            "planned_progress_path": build_progress_path(args, s),
            "effective_progress_dir": effective_progress_dir,
            "skip_durable_validation": skip_durable_validation,
        }
        for s in secs
    ]

    commit, dirty = _git_head()
    plan: dict = {
        "schema_version": SCHEMA_VERSION,
        "runner": {
            "name": RUNNER_NAME,
            "version": RUNNER_VERSION,
        },
        "status": status,
        "created_at_utc": _utc_iso(),
        "git_commit": commit,
        "git_dirty": dirty,
        "write_requested": write_requested,
        "network_authorized": network_authorized,
        "duration_budget_minutes": budget_minutes,
        "operator_budget_label": budget_label,
        "secondaries_resolution": secondaries_resolution,
        "primaries_resolution": primaries_resolution,
        "effective_config": _effective_config(args),
        "preflight_issues": preflight_issues,
        "process_conflict": process_conflict_result,
        "per_secondary_plan": per_secondary_plan,
        "command_summary": {
            "primary_source": getattr(args, "primary_source", "impact_xlsx"),
            "secondary_count": len(secs),
            "primary_count": primaries_resolution.get("primary_count"),
            "k_max": int(getattr(args, "k_max", DEFAULT_K_MAX)),
            "search": getattr(args, "search", DEFAULT_SEARCH),
        },
        "would_call_engine": bool(would_call_engine),
    }
    return plan


# ---------------------------------------------------------------------------
# Engine callable
# ---------------------------------------------------------------------------


def _default_stackbuilder_callable(
    args_namespace: SimpleNamespace,
    secondary: str,
    primaries: Optional[list[str]] = None,
) -> dict:
    """Lazy-import stackbuilder and invoke ``run_for_secondary``.

    The import and the engine call run inside
    ``contextlib.redirect_stdout(io.StringIO())`` so the engine's
    import-time prints and any per-call ``print`` statements cannot
    contaminate the runner's stdout JSON contract. The captured tail
    is returned in ``captured_stdout_tail`` and ``error`` (when an
    exception fires) for diagnostic visibility — never on stdout.
    """
    captured = io.StringIO()
    started = time.perf_counter()
    run_dir: Optional[str] = None
    err: Optional[str] = None
    status = "ok"

    try:
        with contextlib.redirect_stdout(captured):
            import stackbuilder  # type: ignore
            run_dir = stackbuilder.run_for_secondary(
                args_namespace,
                secondary,
                specified_primaries=(list(primaries) if primaries else None),
            )
    except SystemExit as exc:
        status = "error"
        err = f"SystemExit: {exc.code!r}"
    except Exception as exc:
        status = "error"
        err = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.perf_counter() - started, 3)

    tail = captured.getvalue()
    if len(tail) > 4000:
        tail = tail[-4000:]
    # Mirror the captured engine chatter to stderr for live observability.
    if captured.getvalue():
        sys.stderr.write(captured.getvalue())

    return {
        "status": status,
        "secondary": secondary,
        "run_dir": run_dir,
        "elapsed_seconds": elapsed,
        "captured_stdout_tail": tail,
        "error": err,
    }


# ---------------------------------------------------------------------------
# Run-dir summary
# ---------------------------------------------------------------------------


def _safe_load_json(path: Path) -> tuple[Optional[Any], Optional[str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"json_decode_error: {exc.msg}"
    except OSError as exc:
        return None, f"io_error: {type(exc).__name__}"


def summarize_stackbuilder_run_dir(run_dir: Optional[str]) -> dict:
    """Best-effort, no-pandas summary of a StackBuilder run directory."""
    summary: dict = {
        "run_dir": run_dir,
        "exists": False,
        "issues": [],
        "manifest": None,
        "summary": None,
        "artifacts": {},
        "row_counts": {},
        "k_level_counts": {},
        "best_total_capture": None,
        "best_sharpe": None,
        "created_at": None,
        "finished_at": None,
    }
    if not run_dir:
        summary["issues"].append("run_dir_none")
        return summary
    root = Path(run_dir)
    if not root.exists() or not root.is_dir():
        summary["issues"].append("run_dir_missing")
        return summary
    summary["exists"] = True

    manifest, err = _safe_load_json(root / "run_manifest.json")
    if err:
        summary["issues"].append(f"run_manifest_json: {err}")
    else:
        summary["manifest"] = manifest
        if isinstance(manifest, dict):
            summary["created_at"] = manifest.get("started_at")
            summary["finished_at"] = manifest.get("finished_at")
            outputs = manifest.get("outputs") or {}
            summary["artifacts"]["outputs_map"] = outputs

    summary_json, err = _safe_load_json(root / "summary.json")
    if err and err != "missing":
        summary["issues"].append(f"summary_json: {err}")
    elif isinstance(summary_json, dict):
        summary["summary"] = summary_json
        summary["best_sharpe"] = summary_json.get("best_sharpe")
        summary["best_total_capture"] = summary_json.get("best_capture")

    # Enumerate artifact paths by candidate extension.
    # Phase 6I-73: rank_inverse is no longer produced.
    candidates = (
        "rank_all", "rank_direct", "cohort", "combo_leaderboard",
    )
    for name in candidates:
        for ext in ("xlsx", "parquet", "csv", "json"):
            candidate = root / f"{name}.{ext}"
            if candidate.exists():
                summary["artifacts"][name] = {
                    "path": str(candidate),
                    "format": ext,
                    "size_bytes": candidate.stat().st_size,
                }
                break

    # K-level counts via combo_k=*.json.
    k_counts: dict[str, int] = {}
    try:
        for entry in root.iterdir():
            m = re.match(r"^combo_k=(\d+)\.json$", entry.name)
            if m:
                k_counts[m.group(1)] = k_counts.get(m.group(1), 0) + 1
    except OSError:
        summary["issues"].append("k_level_scan_error")
    summary["k_level_counts"] = k_counts

    return summary


# ---------------------------------------------------------------------------
# Selected-build manifest
# ---------------------------------------------------------------------------


def build_selected_build_payload(
    secondary: str,
    run_summary: dict,
    args: argparse.Namespace,
    selection_policy_context: Optional[dict] = None,
) -> dict:
    """Build the ``selected_build.json`` payload per Phase 6I-69 schema."""
    summary = (run_summary or {}).get("summary") or {}
    manifest = (run_summary or {}).get("manifest") or {}
    parameters = summary.get("parameters") if isinstance(summary, dict) else {}
    selected_k = (
        parameters.get("max_k")
        if isinstance(parameters, dict)
        else None
    )
    if selected_k is None and isinstance(manifest, dict):
        params = manifest.get("params") or {}
        if isinstance(params, dict):
            selected_k = params.get("max_k")

    return {
        "schema_version": SCHEMA_VERSION,
        "secondary": str(secondary),
        "selected_run_id": (
            manifest.get("run_id") if isinstance(manifest, dict) else None
        ),
        "selected_run_dir": (run_summary or {}).get("run_dir"),
        "selected_k": selected_k,
        "selected_metric": getattr(args, "optimize_by", DEFAULT_OPTIMIZE_BY),
        "total_capture": (
            summary.get("best_capture") if isinstance(summary, dict) else None
        ),
        "sharpe_ratio": (
            summary.get("best_sharpe") if isinstance(summary, dict) else None
        ),
        "row_count": (
            summary.get("primaries_tested") if isinstance(summary, dict)
            else None
        ),
        "created_at": (run_summary or {}).get("created_at"),
        "selected_at": _utc_iso(),
        "selection_policy": SELECTION_POLICY,
        "operator_pinned": bool(getattr(args, "pin_build", None)),
        "source_manifest_path": (
            os.path.join((run_summary or {}).get("run_dir") or "", "run_manifest.json")
            if (run_summary or {}).get("run_dir") else None
        ),
        "runner_version": RUNNER_VERSION,
        "selection_policy_context": selection_policy_context or {},
    }


def _atomic_write_json(target: Path, payload: dict) -> Path:
    """Write JSON atomically via ``<stem>.runner_partial<suffix>`` +
    ``os.replace``. Returns the canonical path.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.stem}.runner_partial{target.suffix}")
    with open(partial, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(str(partial), str(target))
    return target


def default_selection_updater(
    args: argparse.Namespace,
    secondary: str,
    run_summary: dict,
    *,
    dry_run: bool = False,
) -> dict:
    """Write ``selected_build.json`` and (optionally) the pin manifest.

    Only writes when ``args.write`` is True, ``dry_run`` is False, and
    either ``args.update_selected`` is True or ``args.pin_build`` is
    set. Respects an existing ``selected_build.pinned.json`` unless
    ``args.unpin`` is supplied.
    """
    issues: list[str] = []
    write_enabled = bool(getattr(args, "write", False)) and not dry_run
    wants_update = bool(getattr(args, "update_selected", False))
    pin_label = getattr(args, "pin_build", None)
    wants_pin = pin_label is not None
    unpin = bool(getattr(args, "unpin", False))

    if not write_enabled or not (wants_update or wants_pin):
        return {
            "status": "skipped",
            "selected_build_path": None,
            "pinned_path": None,
            "payload": None,
            "issues": issues,
        }

    outdir = getattr(args, "outdir", DEFAULT_OUTDIR)
    safe_sec = str(secondary).replace(".", "_")
    secondary_dir = Path(outdir) / safe_sec
    selected_path = secondary_dir / "selected_build.json"
    pinned_path = secondary_dir / "selected_build.pinned.json"

    payload = build_selected_build_payload(secondary, run_summary, args)

    # Respect existing pin unless --unpin
    if pinned_path.exists() and not unpin and wants_update and not wants_pin:
        return {
            "status": "blocked_by_pin",
            "selected_build_path": str(selected_path),
            "pinned_path": str(pinned_path),
            "payload": payload,
            "issues": ["existing_pin_blocks_auto_update"],
        }

    written_selected: Optional[str] = None
    written_pinned: Optional[str] = None

    if wants_update:
        _atomic_write_json(selected_path, payload)
        written_selected = str(selected_path)

    if wants_pin:
        pin_payload = dict(payload)
        pin_payload["operator_pinned"] = True
        pin_payload["operator_pin_label"] = str(pin_label)
        _atomic_write_json(pinned_path, pin_payload)
        written_pinned = str(pinned_path)
        # When pin and update both requested, also refresh the selected
        # pointer with the pinned payload so consumers see the pin.
        if wants_update:
            _atomic_write_json(selected_path, pin_payload)
            payload = pin_payload

    return {
        "status": "written",
        "selected_build_path": written_selected,
        "pinned_path": written_pinned,
        "payload": payload,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Selection policy comparator (helper for tests + Phase E)
# ---------------------------------------------------------------------------


def select_build_per_policy(
    candidates: Iterable[dict],
    *,
    total_capture_tolerance: float = DEFAULT_TOTAL_CAPTURE_TOLERANCE,
) -> Optional[dict]:
    """Apply Phase 6I-73 selection policy to ``candidates``.

    Each candidate must expose ``total_capture``, ``selected_k``, and
    ``created_at`` (ISO-8601 string). Operator pins
    (``operator_pinned=True``) win unconditionally. Otherwise the
    policy first collapses same-K candidates to the latest successful
    run, then compares the remaining K choices by ``total_capture``
    (with tolerance), then falls back to ``created_at`` (latest)
    within the tolerance band. Sharpe is intentionally NOT used as a
    tiebreaker — Phase 6I-73 removed Sharpe from every selection /
    ranking / seeding / optimize / tiebreaker surface.
    """
    cands = list(candidates or [])
    if not cands:
        return None
    pinned = [c for c in cands if c.get("operator_pinned")]
    if pinned:
        # Latest pinned wins if multiple.
        return max(pinned, key=lambda c: c.get("created_at") or "")

    latest_by_k: dict[str, dict] = {}
    for c in cands:
        k = str(c.get("selected_k"))
        prev = latest_by_k.get(k)
        if prev is None or (c.get("created_at") or "") > (
            prev.get("created_at") or ""
        ):
            latest_by_k[k] = c
    cands = list(latest_by_k.values())

    def _f(c: dict, key: str) -> float:
        v = c.get(key)
        try:
            return float(v) if v is not None else float("-inf")
        except (TypeError, ValueError):
            return float("-inf")

    best = cands[0]
    for c in cands[1:]:
        tc_c = _f(c, "total_capture")
        tc_b = _f(best, "total_capture")
        if tc_c > tc_b + total_capture_tolerance:
            best = c
            continue
        if abs(tc_c - tc_b) <= total_capture_tolerance:
            # Within tolerance: latest wins. No Sharpe tiebreaker.
            if (c.get("created_at") or "") > (best.get("created_at") or ""):
                best = c
                continue
    return best


# ---------------------------------------------------------------------------
# execute_run
# ---------------------------------------------------------------------------


def _make_iterator(items: Sequence[Any], *, no_progress: bool):
    """Return a tqdm-wrapped iterator on stderr unless suppressed."""
    if no_progress or _tqdm is None or not hasattr(_tqdm, "tqdm"):
        return iter(items)
    return _tqdm.tqdm(
        items, file=sys.stderr, desc="StackBuilder", unit="secondary",
    )


def execute_run(
    args: argparse.Namespace,
    secondaries: Sequence[str],
    primaries_resolution: dict,
    engine_callable: Callable[..., dict] = _default_stackbuilder_callable,
    selection_updater: Callable[..., dict] = default_selection_updater,
) -> dict:
    """Drive the per-secondary engine loop.

    Per-secondary exceptions (including ``SystemExit``) are caught and
    recorded as ``status="error"`` entries; the batch continues. In
    Phase B, ``--jobs > 1`` is accepted but not parallelized — the
    runner emits a warning and executes sequentially.
    """
    started_iso = _utc_iso()
    t0 = time.perf_counter()
    warnings: list[str] = []
    if int(getattr(args, "jobs", DEFAULT_JOBS)) > 1:
        warnings.append(
            "jobs_gt_1_not_parallelized_in_phase_b; executing sequentially"
        )

    per_results: list[dict] = []
    ok = err = 0
    primaries = list(primaries_resolution.get("primaries") or [])

    iterator = _make_iterator(
        list(secondaries),
        no_progress=bool(getattr(args, "no_progress", False)),
    )
    for sec in iterator:
        progress_path = build_progress_path(args, sec)
        ns = build_stackbuilder_args_namespace(
            args, sec, primaries_resolution, progress_path=progress_path,
        )
        engine_stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(engine_stdout):
                engine_result = engine_callable(ns, sec, primaries or None)
        except SystemExit as exc:
            engine_result = {
                "status": "error",
                "secondary": sec,
                "run_dir": None,
                "elapsed_seconds": None,
                "captured_stdout_tail": "",
                "error": f"SystemExit: {exc.code!r}",
            }
        except Exception as exc:
            tb = traceback.format_exc()
            sys.stderr.write(tb)
            engine_result = {
                "status": "error",
                "secondary": sec,
                "run_dir": None,
                "elapsed_seconds": None,
                "captured_stdout_tail": "",
                "error": f"{type(exc).__name__}: {exc}",
            }

        captured_engine_stdout = engine_stdout.getvalue()
        if captured_engine_stdout:
            sys.stderr.write(captured_engine_stdout)
            existing_tail = str(engine_result.get("captured_stdout_tail") or "")
            combined_tail = existing_tail + captured_engine_stdout
            if len(combined_tail) > 4000:
                combined_tail = combined_tail[-4000:]
            engine_result["captured_stdout_tail"] = combined_tail

        run_dir = engine_result.get("run_dir")
        run_summary = summarize_stackbuilder_run_dir(run_dir) if run_dir else {
            "run_dir": run_dir,
            "exists": False,
            "issues": ["engine_did_not_return_run_dir"]
            if engine_result.get("status") == "ok" else [],
            "manifest": None,
            "summary": None,
            "artifacts": {},
            "row_counts": {},
            "k_level_counts": {},
            "best_total_capture": None,
            "best_sharpe": None,
            "created_at": None,
            "finished_at": None,
        }

        selected_update: dict = {
            "status": "skipped",
            "selected_build_path": None,
            "pinned_path": None,
            "payload": None,
            "issues": [],
        }
        if engine_result.get("status") == "ok":
            try:
                selected_update = selection_updater(
                    args, sec, run_summary, dry_run=False,
                )
            except Exception as exc:
                tb = traceback.format_exc()
                sys.stderr.write(tb)
                selected_update = {
                    "status": "error",
                    "selected_build_path": None,
                    "pinned_path": None,
                    "payload": None,
                    "issues": [f"{type(exc).__name__}: {exc}"],
                }

        record = {
            "secondary": sec,
            "status": engine_result.get("status"),
            "run_dir": run_dir,
            "progress_path": progress_path,
            "elapsed_seconds": engine_result.get("elapsed_seconds"),
            "row_counts": run_summary.get("row_counts"),
            "k_level_counts": run_summary.get("k_level_counts"),
            "best_total_capture": run_summary.get("best_total_capture"),
            "best_sharpe": run_summary.get("best_sharpe"),
            "warnings": run_summary.get("issues") or [],
            "selected_build": selected_update,
            "captured_stdout_tail": engine_result.get("captured_stdout_tail"),
            "error": engine_result.get("error"),
        }
        per_results.append(record)
        if record["status"] == "ok":
            ok += 1
        else:
            err += 1

    elapsed = round(time.perf_counter() - t0, 3)
    total = len(per_results)

    if total == 0:
        status = "refused"
    elif err == 0:
        status = "ok"
    elif ok == 0:
        status = "failed"
    else:
        status = "partial"

    return {
        "status": status,
        "summary": {"ok": ok, "error": err, "total": total},
        "per_secondary_results": per_results,
        "warnings": warnings,
        "started_at": started_iso,
        "ended_at": _utc_iso(),
        "elapsed_seconds": elapsed,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _emit_json(payload: dict) -> None:
    json.dump(payload, sys.stdout, default=str)
    sys.stdout.write("\n")


def main(
    argv: Optional[Sequence[str]] = None,
    engine_callable: Callable[..., dict] = _default_stackbuilder_callable,
    process_conflict_checker: Callable[..., dict] = check_process_conflicts,
    selection_updater: Callable[..., dict] = default_selection_updater,
) -> int:
    args = parse_args(argv)

    secondaries_resolution = resolve_secondaries(args)
    primaries_resolution = resolve_primaries(args)

    write_requested = bool(getattr(args, "write", False))

    # Process-conflict check runs for any invocation; on dry-run we
    # report it advisory only. On write, an enumeration failure
    # fails closed.
    conflict_result = process_conflict_checker(write_requested=write_requested)

    plan = build_run_plan(
        args,
        secondaries_resolution=secondaries_resolution,
        primaries_resolution=primaries_resolution,
        process_conflict_result=conflict_result,
    )

    # Dry-run path — never invokes the engine.
    if not write_requested or plan["status"] != "ready":
        _emit_json(plan)
        if plan["status"] in ("refused", "blocked_process_conflict"):
            return 1
        return 0

    # Write path. Refuse on conflict-enumeration errors.
    if conflict_result.get("status") == "error":
        plan["status"] = "refused"
        plan["preflight_issues"] = list(plan.get("preflight_issues") or []) + [
            "process_conflict_enumeration_failed_write_refused",
        ]
        _emit_json(plan)
        return 1

    # Run.
    result = execute_run(
        args,
        secondaries_resolution["secondaries"],
        primaries_resolution,
        engine_callable=engine_callable,
        selection_updater=selection_updater,
    )

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "runner": plan["runner"],
        "status": result["status"],
        "created_at_utc": plan["created_at_utc"],
        "ended_at_utc": result["ended_at"],
        "elapsed_seconds": result["elapsed_seconds"],
        "git_commit": plan["git_commit"],
        "git_dirty": plan["git_dirty"],
        "write_requested": True,
        "network_authorized": plan["network_authorized"],
        "duration_budget_minutes": plan["duration_budget_minutes"],
        "operator_budget_label": plan["operator_budget_label"],
        "secondaries_resolution": secondaries_resolution,
        "primaries_resolution": primaries_resolution,
        "effective_config": plan["effective_config"],
        "process_conflict": conflict_result,
        "per_secondary_results": result["per_secondary_results"],
        "summary": result["summary"],
        "warnings": result["warnings"],
    }
    _emit_json(envelope)

    if envelope["status"] in ("ok", "partial"):
        return 0 if envelope["status"] == "ok" else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
