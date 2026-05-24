"""TrafficFlow runner: dry-run preflight (Phase B) + isolated-output
write support (Phase C).

Phase A scoping doc:
``md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_EXECUTION_SURFACE.md``.

Behavior contract:

  * Dry-run is the default. Without ``--write``, the runner runs the
    Phase B preflight (input readiness classification, max-SMA-day
    verification, freshness gate, eligibility computation) and emits a
    single JSON envelope on stdout. The dry-run path NEVER imports
    ``trafficflow``.
  * ``--write`` is the Phase C isolated-output smoke surface. It is
    structurally refused when the resolved ``--output-dir`` is the
    canonical ``output/trafficflow`` root or any descendant; the
    refusal happens BEFORE any preflight or compute and never imports
    ``trafficflow``. Canonical ``output/trafficflow`` writes remain
    reserved for a later operator-authorized phase.
  * When ``--write`` is authorized for an isolated output directory,
    the runner runs preflight, invokes ``trafficflow.build_board_rows``
    via a wrapper that pins the resolved ``selected_build.json``
    ``combo_leaderboard`` path so the engine cannot fall back to the
    latest-by-ctime directory scan, and atomically writes
    ``board_rows_k=<K>.{json,csv}`` per-(secondary, K), plus
    ``run_manifest.json`` and ``run.stdout.json`` mirrors, all under
    the isolated directory.
  * Per-secondary ``selected_build.json`` is consumed explicitly. The
    runner refuses any secondary whose ``selected_build.json`` is
    missing unless ``--explicit-build`` is supplied (and then only
    when exactly one secondary was requested).
  * The runner never falls back to a latest-by-ctime directory
    listing. It does NOT call ``trafficflow._find_latest_combo_table``
    directly, and during isolated-write compute it monkey-patches that
    helper so the engine cannot fall back either.
  * Process-conflict check enumerates command lines and refuses when
    another engine/runner is active. In write-authorized mode, the
    runner ALSO fails closed when conflict enumeration itself fails
    (``status == "error"``).
  * stdout is exactly one JSON object emitted by ``main``.
  * stderr carries human-readable progress, warnings, and tracebacks.
  * Repair flags (``--refresh-missing-pkls`` and
    ``--refresh-stale-prices``) are report-only across both dry-run
    and isolated-write modes; the runner computes
    ``would_refresh_pkls`` / ``would_refresh_prices`` lists but does
    not invoke ``signal_engine_cache_refresher.py`` and does not call
    ``trafficflow.refresh_secondary_caches``.

No top-level import of ``trafficflow``, ``signal_engine_cache_refresher``,
``stackbuilder``, ``onepass``, ``impactsearch``, ``spymaster``,
``confluence``, ``multi_timeframe_builder``, ``dash``,
``dash_bootstrap_components``, ``yfinance``, ``plotly``.

``pandas`` is imported only inside the leaderboard-reading helper to
keep the dry-run path importable in fixture-free contexts.
``trafficflow`` is imported only inside the Phase C isolated-write
compute path; tests inject a ``compute_callable`` to avoid that
lazy import entirely.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence


RUNNER_NAME = "trafficflow_runner"
RUNNER_VERSION = "0.1.0"
SCHEMA_VERSION = 1
STAGE_NAME = "trafficflow"

DEFAULT_STACKBUILDER_ROOT = "output/stackbuilder"
DEFAULT_OUTPUT_DIR = "output/trafficflow"
DEFAULT_PROGRESS_DIR = "output/trafficflow/_progress"
DEFAULT_CACHE_RESULTS_DIR = "cache/results"
DEFAULT_CACHE_STATUS_DIR = "cache/status"
DEFAULT_PRICE_CACHE_DIR = "price_cache/daily"
DEFAULT_K_RANGE = "1-12"
DEFAULT_JOBS = 1
DEFAULT_MAX_SMA_DAY = 114
DEFAULT_PARALLEL_SUBSETS = 0
DEFAULT_SUBSET_WORKERS = 4
DEFAULT_TF_BITMASK_FASTPATH = 1
PRICE_CACHE_STALE_DAYS = 7

REQUIRED_PKL_FIELDS = (
    "preprocessed_data",
    "active_pairs",
    "daily_top_buy_pairs",
    "daily_top_short_pairs",
)

PROCESS_CONFLICT_PATTERNS: tuple[str, ...] = (
    "trafficflow.py",
    "signal_engine_cache_refresher.py",
    "stackbuilder.py",
    "stackbuilder_workbook_runner.py",
    "onepass.py",
    "onepass_workbook_runner.py",
    "impactsearch.py",
    "impactsearch_workbook_runner.py",
    "spymaster.py",
    "confluence.py",
    "multi_timeframe_builder.py",
)

# Phase C canonical-output guardrail: --write is structurally refused
# when the resolved output dir is the canonical TrafficFlow root or any
# descendant. Phase C is supervised isolated-output smoke only.
CANONICAL_OUTPUT_FORBIDDEN_FOR_PHASE_C: tuple[str, ...] = (
    "output/trafficflow",
)

PHASE_C_RUN_MANIFEST_SCHEMA = "trafficflow_runner_phase_c_v1"

# Exit codes
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ARGPARSE = 2  # argparse exits 2 itself on usage error
EXIT_PROCESS_CONFLICT = 3

_RAW_TICKER_SPLIT_RE = re.compile(r"[,\s]+")

# JSON sanitization patterns. The drive-letter regex is constructed
# from character escapes so the source text itself contains no literal
# example of a drive-letter path like `C:` + slash.
_ABS_PATH_REDACTED = "<ABSOLUTE_PATH_REDACTED>"
_CMDLINE_REDACTED = "<COMMAND_LINE_REDACTED>"
_DRIVE_LETTER_RE = re.compile(
    "^[A-Za-z]" + chr(58) + "[" + chr(92) + chr(92) + chr(47) + "]"
)


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse runner CLI args. Defaults match Phase A locked v1 values."""
    p = argparse.ArgumentParser(
        prog="trafficflow_runner",
        description=(
            "Headless TrafficFlow runner (Phase B dry-run scaffold). "
            "Reads per-secondary StackBuilder selected_build.json, "
            "classifies input readiness, and emits a JSON plan. "
            "Phase B never invokes the TrafficFlow compute path."
        ),
    )

    # Secondary selection
    p.add_argument("--secondaries", default=None,
                   help="Comma/whitespace-separated secondary tickers.")
    p.add_argument("--secondaries-file", default=None,
                   help="Path to a file listing secondaries (one per line; "
                        "comma/whitespace separated also accepted).")

    # StackBuilder input
    p.add_argument("--stackbuilder-root", default=DEFAULT_STACKBUILDER_ROOT)
    p.add_argument(
        "--use-selected-build",
        dest="use_selected_build",
        action="store_true",
        default=True,
    )
    p.add_argument(
        "--no-use-selected-build",
        dest="use_selected_build",
        action="store_false",
    )
    p.add_argument(
        "--explicit-build",
        default=None,
        help="Override selected_run_dir for a single-secondary smoke run.",
    )

    # Output
    p.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)

    # K options
    p.add_argument("--k", type=int, default=None)
    p.add_argument(
        "--k-range",
        default=None,
        help="K range expression: comma list (e.g. 1,2,3,4,6) or "
             "simple range (e.g. 1-12).",
    )
    p.add_argument(
        "--all-selected-k",
        dest="all_selected_k",
        action="store_true",
        default=True,
    )
    p.add_argument(
        "--no-all-selected-k",
        dest="all_selected_k",
        action="store_false",
    )

    # Concurrency / compute toggles
    p.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    p.add_argument("--parallel-subsets", type=int, default=DEFAULT_PARALLEL_SUBSETS,
                   choices=(0, 1))
    p.add_argument("--subset-workers", type=int, default=DEFAULT_SUBSET_WORKERS)
    p.add_argument("--tf-bitmask-fastpath", type=int,
                   default=DEFAULT_TF_BITMASK_FASTPATH, choices=(0, 1))

    # Repair flags (report-only in Phase B)
    p.add_argument(
        "--refresh-missing-pkls",
        action="store_true",
        default=False,
        help="Report-only in Phase B. When set, the runner emits a "
             "would_refresh_pkls list covering MISSING / STALE / "
             "MISMATCH_MAX_SMA / CONFLICTING_MAX_SMA / INVALID / "
             "UNREADABLE / SCHEMA_MISMATCH classifications. The "
             "refresher is never invoked in Phase B.",
    )
    p.add_argument(
        "--max-sma-day",
        type=int,
        default=DEFAULT_MAX_SMA_DAY,
    )
    p.add_argument(
        "--refresh-stale-prices",
        action="store_true",
        default=False,
        help="Report-only in Phase B. When set, the runner emits a "
             "would_refresh_prices list covering MISSING / STALE / "
             "UNREADABLE secondary price caches. "
             "refresh_secondary_caches is never invoked in Phase B.",
    )

    # Write / network gates
    p.add_argument(
        "--write",
        action="store_true",
        default=False,
        help="Phase B refuses --write. Reserved for Phase E.",
    )
    p.add_argument("--allow-network-fetch", action="store_true", default=False)
    p.add_argument("--duration-budget-minutes", type=int, default=None)
    p.add_argument("--operator-budget-label", default=None)

    # Runner output controls
    p.add_argument("--no-progress", action="store_true", default=False)
    p.add_argument("--progress-dir", default=None)
    p.add_argument("--strict-inputs", action="store_true", default=False)
    p.add_argument(
        "--skip-secondary-on-input-gate",
        dest="skip_secondary_on_input_gate",
        action="store_true",
        default=True,
    )
    p.add_argument(
        "--no-skip-secondary-on-input-gate",
        dest="skip_secondary_on_input_gate",
        action="store_false",
    )

    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + os.urandom(3).hex()


def _git_head() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:
        return None
    return None


def _parse_ticker_blob(blob: str, *, uppercase: bool = True) -> list[str]:
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
        return {"status": "refused", "secondaries": [],
                "issues": ["secondaries_required"] + issues}
    secs = _parse_ticker_blob("\n".join(raw_parts), uppercase=True)
    if not secs:
        return {"status": "refused", "secondaries": [],
                "issues": ["secondaries_empty_after_parse"] + issues}
    return {"status": "ok", "secondaries": secs, "issues": issues}


def parse_k_selection(args: argparse.Namespace) -> dict:
    """Resolve the requested K levels into an explicit integer list.

    Returns dict with keys:
      mode: "single" | "list" | "all_selected"
      ks: list[int] (empty if mode == "all_selected" -> resolved later
          per-secondary from leaderboard)
      issues: list[str]
    """
    issues: list[str] = []
    k = getattr(args, "k", None)
    k_range = getattr(args, "k_range", None)
    all_selected = bool(getattr(args, "all_selected_k", True))
    if k is not None:
        if k < 1:
            issues.append(f"invalid_k: {k}")
            return {"mode": "single", "ks": [], "issues": issues}
        return {"mode": "single", "ks": [int(k)], "issues": issues}
    if k_range:
        ks = _parse_k_range_expression(k_range, issues)
        return {"mode": "list", "ks": ks, "issues": issues}
    if all_selected:
        return {"mode": "all_selected", "ks": [], "issues": issues}
    # Neither --k nor --k-range, and --no-all-selected-k -> nothing requested
    return {"mode": "list", "ks": [], "issues": ["no_k_selection_provided"]}


def _parse_k_range_expression(expr: str, issues: list[str]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    s = (expr or "").strip()
    if not s:
        return out
    for part in s.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            lo_s, _, hi_s = token.partition("-")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                issues.append(f"invalid_k_range_segment: {token!r}")
                continue
            if lo < 1 or hi < lo:
                issues.append(f"invalid_k_range_bounds: {token!r}")
                continue
            for k in range(lo, hi + 1):
                if k not in seen:
                    seen.add(k)
                    out.append(k)
        else:
            try:
                k = int(token)
            except ValueError:
                issues.append(f"invalid_k_value: {token!r}")
                continue
            if k < 1:
                issues.append(f"invalid_k_value: {token!r}")
                continue
            if k not in seen:
                seen.add(k)
                out.append(k)
    return sorted(out)


# ---------------------------------------------------------------------------
# JSON output sanitizer (privacy gate before stdout emit)
# ---------------------------------------------------------------------------


def is_absolute_path_like(value: str) -> bool:
    """Best-effort detection of an absolute filesystem path.

    Recognizes:
      * POSIX leading-slash absolute paths.
      * Windows drive-letter paths (a single ASCII letter followed by a
        colon and a path separator).
      * UNC paths beginning with a doubled backslash.
    """
    if not isinstance(value, str) or not value:
        return False
    s = value.strip()
    if not s:
        return False
    if s.startswith(("/", "\\")):
        return True
    if _DRIVE_LETTER_RE.match(s) is not None:
        return True
    if s.startswith("\\\\"):  # UNC
        return True
    return False


def path_for_output(
    value: Any,
    *,
    project_root: Optional[Path] = None,
) -> Optional[str]:
    """Return a JSON-safe path string.

    Rules:
      * ``None`` -> ``None``.
      * Relative path -> normalized POSIX-style relative spelling.
      * Absolute path under the project root -> repo-relative POSIX-style.
        The under-project-root conversion runs first so that a
        sanctioned path which incidentally contains a sensitive-looking
        substring is still surfaced as a repo-relative string.
      * Absolute path outside the project root -> ``<ABSOLUTE_PATH_REDACTED>``.
    """
    if value is None:
        return None
    s = str(value)
    if not s:
        return s
    if is_absolute_path_like(s):
        root = project_root if project_root is not None else Path.cwd()
        try:
            p = Path(s).resolve()
            rel = p.relative_to(root.resolve())
            return rel.as_posix()
        except (ValueError, OSError):
            return _ABS_PATH_REDACTED
        # Defensive: if relative_to succeeded but the result still
        # contains a private token, redact.
    # Relative path: normalize to POSIX form, no leading "./"
    norm = s.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def redact_command_line(value: str) -> str:
    """Drop the raw command line entirely; callers can keep
    ``matched_pattern`` etc. but never the full cmdline."""
    return _CMDLINE_REDACTED


_PATH_LIKE_KEYS = frozenset({
    "selected_run_dir", "combo_leaderboard_path", "path_rel",
    "manifest_path_rel", "stackbuilder_root", "explicit_build",
    "output_dir", "progress_dir", "source_manifest_path", "sb_path",
})


def sanitize_for_json(value: Any, *, project_root: Optional[Path] = None) -> Any:
    """Recursive sanitizer applied to the envelope before
    ``json.dumps``.

    Strategy:
      * dict: sanitize each value; if the key is in
        ``_PATH_LIKE_KEYS``, treat the value as a path-for-output.
        Special-case ``conflicts`` lists to redact raw cmdline.
      * list/tuple: sanitize each element.
      * str: leave intact UNLESS it looks like an absolute path; then
        redact-or-relativize.
      * other scalars: leave intact.
    """
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if k == "conflicts" and isinstance(v, list):
                out[k] = [_sanitize_conflict_entry(e) for e in v]
                continue
            if k == "cmdline" and isinstance(v, str):
                out[k] = redact_command_line(v)
                continue
            if k in _PATH_LIKE_KEYS:
                if v is None:
                    out[k] = None
                else:
                    out[k] = path_for_output(v, project_root=project_root)
                continue
            out[k] = sanitize_for_json(v, project_root=project_root)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize_for_json(item, project_root=project_root) for item in value]
    if isinstance(value, str):
        if is_absolute_path_like(value):
            return path_for_output(value, project_root=project_root)
        return value
    return value


def _sanitize_conflict_entry(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return entry
    out = {
        "pid": entry.get("pid"),
        "matched_pattern": entry.get("matched_pattern"),
        "command_line_redacted": True,
    }
    # If a safe one-word label can be derived, surface it.
    pat = entry.get("matched_pattern")
    if pat:
        out["command_label"] = pat
    return out


# ---------------------------------------------------------------------------
# Phase C canonical-output guardrail
# ---------------------------------------------------------------------------


def is_isolated_output_dir(
    output_dir: Any,
    *,
    project_root: Optional[Path] = None,
) -> bool:
    """Return True when ``output_dir`` is NOT the canonical TrafficFlow
    output root or any descendant.

    Resolves relative paths under ``project_root`` (default: ``Path.cwd()``)
    before comparison. Normalizes separators and case. Absolute paths
    outside the project root resolve as isolated; the sanitizer redacts
    them when they appear in emitted JSON.
    """
    if output_dir is None:
        return False
    root = project_root if project_root is not None else Path.cwd()
    raw = str(output_dir)
    # Resolve absolute or relative to project root.
    p = Path(raw)
    if not p.is_absolute():
        try:
            p = (root / p)
        except Exception:
            return False
    try:
        resolved = p.resolve(strict=False)
    except Exception:
        return False
    try:
        rel = resolved.relative_to(root.resolve())
        rel_posix = rel.as_posix().rstrip("/").lower()
    except (ValueError, OSError):
        # Absolute path outside project root: treat as isolated.
        return True
    for forbidden in CANONICAL_OUTPUT_FORBIDDEN_FOR_PHASE_C:
        f = forbidden.strip("/").lower()
        if rel_posix == f:
            return False
        if rel_posix.startswith(f + "/"):
            return False
    return True


# ---------------------------------------------------------------------------
# selected_build.json + combo_leaderboard.xlsx
# ---------------------------------------------------------------------------


def _selected_build_path(stackbuilder_root: str, secondary: str) -> Path:
    return Path(stackbuilder_root) / secondary / "selected_build.json"


SELECTED_BUILD_REQUIRED_FIELDS = (
    "schema_version",
    "secondary",
    "selected_k",
    "selection_policy",
    "operator_pinned",
    "selected_run_dir",
)


def read_selected_build(
    stackbuilder_root: str,
    secondary: str,
    explicit_build_path: Optional[str] = None,
) -> dict:
    """Read and validate selected_build.json for a secondary.

    Returns dict with keys:
      status: "ok" | "refused" | "explicit_override"
      reason: short refusal reason on non-ok
      missing_fields: list[str] when reason is
          "selected_build_missing_required_fields"
      sb_path: path inspected (raw; sanitizer runs at emit time)
      payload: parsed JSON dict on ok
      explicit_build_override: bool
      selected_run_dir: resolved string (raw; sanitizer runs at emit time)
    """
    if explicit_build_path:
        run_dir = Path(explicit_build_path)
        if not run_dir.exists() or not run_dir.is_dir():
            return {
                "status": "refused",
                "reason": "explicit_build_path_missing",
                "missing_fields": [],
                "sb_path": None,
                "payload": None,
                "explicit_build_override": True,
                "selected_run_dir": str(explicit_build_path),
            }
        return {
            "status": "explicit_override",
            "reason": None,
            "missing_fields": [],
            "sb_path": None,
            "payload": None,
            "explicit_build_override": True,
            "selected_run_dir": str(explicit_build_path),
        }

    sb_path = _selected_build_path(stackbuilder_root, secondary)
    if not sb_path.exists():
        return {
            "status": "refused",
            "reason": "selected_build_missing",
            "missing_fields": [],
            "sb_path": str(sb_path),
            "payload": None,
            "explicit_build_override": False,
            "selected_run_dir": None,
        }
    try:
        payload = json.loads(sb_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "refused",
            "reason": f"selected_build_unreadable: {type(exc).__name__}",
            "missing_fields": [],
            "sb_path": str(sb_path),
            "payload": None,
            "explicit_build_override": False,
            "selected_run_dir": None,
        }
    if not isinstance(payload, dict):
        return {
            "status": "refused",
            "reason": "selected_build_not_object",
            "missing_fields": [],
            "sb_path": str(sb_path),
            "payload": None,
            "explicit_build_override": False,
            "selected_run_dir": None,
        }
    # Schema gate: required fields must all be present.
    missing = [f for f in SELECTED_BUILD_REQUIRED_FIELDS if f not in payload]
    if missing:
        return {
            "status": "refused",
            "reason": "selected_build_missing_required_fields",
            "missing_fields": missing,
            "sb_path": str(sb_path),
            "payload": payload,
            "explicit_build_override": False,
            "selected_run_dir": None,
        }
    # Secondary match.
    declared_sec = str(payload.get("secondary") or "").strip().upper()
    requested = str(secondary or "").strip().upper()
    if declared_sec != requested:
        return {
            "status": "refused",
            "reason": "selected_build_secondary_mismatch",
            "missing_fields": [],
            "sb_path": str(sb_path),
            "payload": payload,
            "explicit_build_override": False,
            "selected_run_dir": None,
        }
    # selected_k must be a positive int.
    sk_raw = payload.get("selected_k")
    try:
        sk = int(sk_raw)
        if sk < 1:
            raise ValueError(f"selected_k<1: {sk}")
    except (TypeError, ValueError):
        return {
            "status": "refused",
            "reason": "selected_build_invalid_selected_k",
            "missing_fields": [],
            "sb_path": str(sb_path),
            "payload": payload,
            "explicit_build_override": False,
            "selected_run_dir": None,
        }
    # selected_run_dir non-empty.
    run_dir = payload.get("selected_run_dir")
    if not run_dir or not str(run_dir).strip():
        return {
            "status": "refused",
            "reason": "selected_build_missing_selected_run_dir",
            "missing_fields": [],
            "sb_path": str(sb_path),
            "payload": payload,
            "explicit_build_override": False,
            "selected_run_dir": None,
        }
    return {
        "status": "ok",
        "reason": None,
        "missing_fields": [],
        "sb_path": str(sb_path),
        "payload": payload,
        "explicit_build_override": False,
        "selected_run_dir": str(run_dir),
    }


def _combo_leaderboard_path(run_dir: str) -> Optional[Path]:
    base = Path(run_dir)
    for fn in ("combo_leaderboard.parquet", "combo_leaderboard.xlsx",
               "combo_leaderboard.csv"):
        p = base / fn
        if p.exists() and p.is_file():
            return p
    return None


def read_combo_leaderboard(
    run_dir: str,
    pandas_module: Optional[Any] = None,
) -> dict:
    """Read the leaderboard with K and Members columns.

    Returns dict with status / path / k_to_members / available_ks / issues.
    Loads pandas lazily; tests may inject a pandas module shim.
    """
    path = _combo_leaderboard_path(run_dir)
    if path is None:
        return {
            "status": "missing", "path": None, "k_to_members": {},
            "available_ks": [], "issues": ["combo_leaderboard_not_found"],
        }
    pd = pandas_module
    if pd is None:
        try:
            import pandas as pd  # type: ignore
        except Exception as exc:  # pragma: no cover - pandas is project-pinned
            return {
                "status": "unreadable", "path": str(path), "k_to_members": {},
                "available_ks": [], "issues": [f"pandas_unavailable: {exc!r}"],
            }
    try:
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            df = pd.read_parquet(path)
        elif suffix in (".xlsx", ".xls"):
            df = pd.read_excel(path, engine="openpyxl")
        else:
            df = pd.read_csv(path)
    except Exception as exc:
        return {
            "status": "unreadable", "path": str(path), "k_to_members": {},
            "available_ks": [], "issues": [f"leaderboard_read_error: {type(exc).__name__}"],
        }
    cols = {str(c).lower(): c for c in df.columns}
    if "k" not in cols or "members" not in cols:
        return {
            "status": "schema_mismatch", "path": str(path), "k_to_members": {},
            "available_ks": [],
            "issues": ["leaderboard_missing_required_columns"],
        }
    k_col = cols["k"]
    m_col = cols["members"]
    k_to_members: dict[int, list[str]] = {}
    available_ks: list[int] = []
    issues: list[str] = []
    for _, row in df.iterrows():
        try:
            k_val = int(row[k_col])
        except (ValueError, TypeError):
            continue
        members = _parse_members_field(row[m_col])
        if k_val not in k_to_members:
            k_to_members[k_val] = members
            available_ks.append(k_val)
    available_ks = sorted(set(available_ks))
    return {
        "status": "ok", "path": str(path), "k_to_members": k_to_members,
        "available_ks": available_ks, "issues": issues,
    }


def _parse_members_field(raw: Any) -> list[str]:
    """Parse a leaderboard Members cell into a list of ticker-mode strings.

    Examples accepted: "['AAA[D]', 'BBB[I]']" and "AAA[D], BBB[I]".
    """
    out: list[str] = []
    s = str(raw).strip()
    if not s:
        return out
    if s.startswith("[") and s.endswith("]"):
        # Best-effort literal parse; avoid eval. Use ast.literal_eval.
        import ast
        try:
            lit = ast.literal_eval(s)
            if isinstance(lit, (list, tuple)):
                for item in lit:
                    text = str(item).strip()
                    if text:
                        out.append(text)
                return out
        except (ValueError, SyntaxError):
            pass
    for tok in s.split(","):
        text = tok.strip().strip("'").strip('"').strip()
        if text and text != "[" and text != "]":
            out.append(text)
    return out


def base_ticker_of(ticker_mode: str) -> str:
    """Strip a trailing ``[I]`` / ``[D]`` mode suffix; uppercase."""
    t = str(ticker_mode or "").strip()
    if "[" in t:
        t = t.split("[", 1)[0].strip()
    return t.upper()


# ---------------------------------------------------------------------------
# Price-cache readiness
# ---------------------------------------------------------------------------


def classify_price_cache(
    secondary: str,
    price_cache_dir: str = DEFAULT_PRICE_CACHE_DIR,
    *,
    today: Optional[datetime] = None,
    stale_days: int = PRICE_CACHE_STALE_DAYS,
    pandas_module: Optional[Any] = None,
) -> dict:
    """Classify the on-disk secondary price cache.

    Returns dict with keys:
      secondary, classification, path_rel, tail_date, rows, issues
    classification in {OK, MISSING, STALE, UNREADABLE, UNKNOWN_USABLE}.
    """
    path: Optional[Path] = None
    for ext in ("csv", "parquet"):
        cand = Path(price_cache_dir) / f"{secondary}.{ext}"
        if cand.exists():
            path = cand
            break
    if path is None:
        return {
            "secondary": secondary,
            "classification": "MISSING",
            "path_rel": None,
            "tail_date": None,
            "rows": 0,
            "issues": [],
        }
    pd = pandas_module
    if pd is None:
        try:
            import pandas as pd  # type: ignore
        except Exception as exc:  # pragma: no cover
            return {
                "secondary": secondary,
                "classification": "UNREADABLE",
                "path_rel": str(path),
                "tail_date": None,
                "rows": 0,
                "issues": [f"pandas_unavailable: {exc!r}"],
            }
    try:
        df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_parquet(path)
    except Exception as exc:
        return {
            "secondary": secondary,
            "classification": "UNREADABLE",
            "path_rel": str(path),
            "tail_date": None,
            "rows": 0,
            "issues": [f"price_cache_read_error: {type(exc).__name__}"],
        }
    rows = int(len(df))
    tail = None
    for cand_col in ("Date", "date", "INDEX", "Index", "index"):
        if cand_col in df.columns:
            try:
                tail = pd.to_datetime(df[cand_col].iloc[-1]).strftime("%Y-%m-%d")
            except Exception:
                pass
            break
    if tail is None:
        return {
            "secondary": secondary,
            "classification": "UNKNOWN_USABLE",
            "path_rel": str(path),
            "tail_date": None,
            "rows": rows,
            "issues": ["price_cache_tail_date_unknown"],
        }
    if today is None:
        today = datetime.now(timezone.utc)
    try:
        tail_dt = datetime.strptime(tail, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return {
            "secondary": secondary,
            "classification": "UNKNOWN_USABLE",
            "path_rel": str(path),
            "tail_date": tail,
            "rows": rows,
            "issues": ["price_cache_tail_date_unparseable"],
        }
    age_days = (today - tail_dt).total_seconds() / 86400.0
    if age_days > stale_days:
        return {
            "secondary": secondary,
            "classification": "STALE",
            "path_rel": str(path),
            "tail_date": tail,
            "rows": rows,
            "issues": [f"price_cache_age_days={age_days:.1f}"],
        }
    return {
        "secondary": secondary,
        "classification": "OK",
        "path_rel": str(path),
        "tail_date": tail,
        "rows": rows,
        "issues": [],
    }


# ---------------------------------------------------------------------------
# PKL readiness (max-SMA-day + schema)
# ---------------------------------------------------------------------------


_PKL_DATE_FIELDS = (
    "last_processed_date", "last_date", "date_range_end",
    "end_date", "latest_date",
)


def _normalize_date_string(value: Any) -> Optional[str]:
    """Best-effort YYYY-MM-DD normalization without importing pandas."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Take the first 10 chars if shape is YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            datetime.strptime(s[:10], "%Y-%m-%d")
            return s[:10]
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(s).strftime("%Y-%m-%d")
    except Exception:
        return None


def _extract_pkl_tail_date(obj: dict) -> Optional[str]:
    """Inspect a loaded PKL dict for the latest usable data date.

    Order of attempts:
      1. preprocessed_data.index.max() when available.
      2. Top-level date fields: last_processed_date, last_date,
         date_range_end, end_date, latest_date.
      3. Embedded manifest fields if present in the PKL.
    """
    df = obj.get("preprocessed_data")
    if df is not None:
        try:
            idx = getattr(df, "index", None)
            if idx is not None and len(idx) > 0:
                # Avoid importing pandas: rely on .max() if it exists.
                last = idx.max()
                d = _normalize_date_string(last)
                if d:
                    return d
        except Exception:
            pass
    for k in _PKL_DATE_FIELDS:
        if k in obj:
            d = _normalize_date_string(obj[k])
            if d:
                return d
    man = obj.get("manifest") if isinstance(obj.get("manifest"), dict) else None
    if man:
        for k in _PKL_DATE_FIELDS:
            if k in man:
                d = _normalize_date_string(man[k])
                if d:
                    return d
    return None


def classify_pkl(
    member: str,
    cache_results_dir: str = DEFAULT_CACHE_RESULTS_DIR,
    *,
    max_sma_required: int = DEFAULT_MAX_SMA_DAY,
    benchmark_as_of_date: Optional[str] = None,
    pickle_module: Any = pickle,
) -> dict:
    """Classify a Spymaster precomputed-result PKL plus its manifest.

    Returns dict with keys including ``classification``, ``max_sma_class``,
    ``data_tail_date``, ``benchmark_as_of_date``, ``freshness_class``,
    ``has_SMA_114``, ``manifest_max_sma_day``, ``missing_fields``,
    ``issues``, ``declared_inferred``.

    More severe classes always win over STALE:
      UNREADABLE / INVALID / SCHEMA_MISMATCH / MISMATCH_MAX_SMA /
      CONFLICTING_MAX_SMA / UNDETERMINABLE_MAX_SMA take precedence over
      STALE.
    """
    pkl_p = Path(cache_results_dir) / f"{member}_precomputed_results.pkl"
    man_p = Path(cache_results_dir) / f"{member}_precomputed_results.pkl.manifest.json"
    out: dict[str, Any] = {
        "member": member,
        "classification": "MISSING",
        "max_sma_class": None,
        "path_rel": str(pkl_p),
        "manifest_path_rel": str(man_p),
        "manifest_max_sma_day": None,
        "has_SMA_114": None,
        "missing_fields": [],
        "issues": [],
        "declared_inferred": False,
        "data_tail_date": None,
        "benchmark_as_of_date": benchmark_as_of_date,
        "freshness_class": None,
    }

    if not pkl_p.exists():
        return out

    declared_msd: Optional[int] = None
    if man_p.exists():
        try:
            man = json.loads(man_p.read_text(encoding="utf-8"))
            if isinstance(man, dict):
                # Old schema: top-level max_sma_day. New schema: params.max_sma_day.
                params = man.get("params") if isinstance(man.get("params"), dict) else {}
                cand = man.get("max_sma_day", params.get("max_sma_day"))
                if cand is None:
                    cand = man.get("existing_max_sma_day", params.get("existing_max_sma_day"))
                if cand is not None:
                    try:
                        declared_msd = int(cand)
                    except (ValueError, TypeError):
                        out["issues"].append("manifest_max_sma_day_unparseable")
                out["manifest_max_sma_day"] = declared_msd
        except (OSError, json.JSONDecodeError) as exc:
            out["issues"].append(f"manifest_unreadable: {type(exc).__name__}")

    # Read the PKL itself
    try:
        with open(pkl_p, "rb") as fh:
            obj = pickle_module.load(fh)
    except Exception as exc:
        out["classification"] = "UNREADABLE"
        out["issues"].append(f"pkl_unreadable: {type(exc).__name__}")
        return out

    if not isinstance(obj, dict):
        out["classification"] = "INVALID"
        out["issues"].append(f"pkl_top_level_not_dict: {type(obj).__name__}")
        return out

    missing = [f for f in REQUIRED_PKL_FIELDS if f not in obj]
    out["missing_fields"] = missing

    # Inspect preprocessed_data columns for SMA_114
    has_114: Optional[bool] = None
    df = obj.get("preprocessed_data")
    if df is not None:
        try:
            cols = [str(c) for c in df.columns]
            has_114 = "SMA_114" in cols
        except Exception:
            has_114 = None
    out["has_SMA_114"] = has_114

    # Extract data tail date for freshness classification.
    tail = _extract_pkl_tail_date(obj)
    out["data_tail_date"] = tail

    # Compute freshness class (advisory; STALE may be overridden by a
    # more severe classification below).
    freshness: Optional[str] = None
    if benchmark_as_of_date and tail:
        if tail < benchmark_as_of_date:
            freshness = "STALE"
        else:
            freshness = "OK"
    elif benchmark_as_of_date and not tail:
        out["issues"].append("pkl_tail_date_unknown")
        freshness = "UNKNOWN"
    out["freshness_class"] = freshness

    # Inline PKL metadata fallback
    if declared_msd is None:
        for k in ("max_sma_day", "existing_max_sma_day"):
            if k in obj:
                try:
                    declared_msd = int(obj[k])
                    break
                except (ValueError, TypeError):
                    pass

    # Determine max-SMA classification
    if declared_msd is not None:
        if int(declared_msd) == int(max_sma_required) and has_114 is True:
            msd_class = "MATCH"
        elif int(declared_msd) == int(max_sma_required) and has_114 is False:
            msd_class = "CONFLICTING_MAX_SMA"
        elif int(declared_msd) != int(max_sma_required) and has_114 is True:
            msd_class = "CONFLICTING_MAX_SMA"
        elif int(declared_msd) != int(max_sma_required):
            msd_class = "MISMATCH_MAX_SMA"
        else:
            msd_class = "UNDETERMINABLE_MAX_SMA"
    else:
        if has_114 is True:
            msd_class = "MATCH"
            out["declared_inferred"] = True
        elif has_114 is False:
            msd_class = "MISMATCH_MAX_SMA"
        else:
            msd_class = "UNDETERMINABLE_MAX_SMA"
    out["max_sma_class"] = msd_class

    # Top-level classification. More severe classes outrank STALE.
    if missing:
        out["classification"] = "SCHEMA_MISMATCH"
        return out
    if msd_class == "MISMATCH_MAX_SMA":
        out["classification"] = "MISMATCH_MAX_SMA"
        return out
    if msd_class == "CONFLICTING_MAX_SMA":
        out["classification"] = "CONFLICTING_MAX_SMA"
        return out
    if msd_class == "UNDETERMINABLE_MAX_SMA":
        out["classification"] = "UNDETERMINABLE_MAX_SMA"
        return out
    # max-SMA is MATCH at this point. Apply freshness gate next.
    if freshness == "STALE":
        out["classification"] = "STALE"
        return out
    if out["declared_inferred"]:
        out["classification"] = "UNKNOWN_USABLE"
        return out
    out["classification"] = "OK"
    return out


# ---------------------------------------------------------------------------
# Process-conflict check
# ---------------------------------------------------------------------------


def _process_lines_psutil(own_pid: int) -> Optional[list[tuple[int, str]]]:
    try:
        import psutil  # type: ignore
    except Exception:
        return None
    out: list[tuple[int, str]] = []
    for proc in psutil.process_iter(attrs=["pid", "cmdline"]):
        try:
            info = proc.info
            pid = int(info.get("pid"))
            if pid == own_pid or pid <= 0:
                continue
            cmdline = " ".join(info.get("cmdline") or [])
            if not cmdline:
                continue
            out.append((pid, cmdline))
        except Exception:
            continue
    return out


def _process_lines_windows(own_pid: int) -> Optional[list[tuple[int, str]]]:
    cmd = [
        "powershell", "-NoProfile", "-Command",
        ("Get-CimInstance Win32_Process | "
         "Select-Object ProcessId, CommandLine | "
         "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=20, check=False)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0].strip())
        except ValueError:
            continue
        if pid == own_pid or pid <= 0:
            continue
        cmdline = parts[1].strip()
        if not cmdline:
            continue
        out.append((pid, cmdline))
    return out


def _process_lines_posix(own_pid: int) -> Optional[list[tuple[int, str]]]:
    try:
        result = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True,
                                text=True, timeout=15, check=False)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    out: list[tuple[int, str]] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0].strip())
        except ValueError:
            continue
        if pid == own_pid or pid <= 0:
            continue
        out.append((pid, parts[1]))
    return out


def check_process_conflicts(
    write_requested: bool = False,
    *,
    patterns: Sequence[str] = PROCESS_CONFLICT_PATTERNS,
    own_pid: Optional[int] = None,
) -> dict:
    """Read-only process enumeration; return conflicting command lines."""
    own_pid = own_pid if own_pid is not None else os.getpid()
    lines = _process_lines_psutil(own_pid)
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
        return {"status": status, "conflicts": [], "queried_via": queried_via,
                "error": "enumeration_unavailable"}
    conflicts: list[dict] = []
    for pid, cmdline in lines:
        for pat in patterns:
            if pat in cmdline:
                conflicts.append({"pid": pid, "cmdline": cmdline, "matched_pattern": pat})
                break
    if conflicts:
        return {"status": "blocked", "conflicts": conflicts,
                "queried_via": queried_via, "error": None}
    return {"status": "ok", "conflicts": [], "queried_via": queried_via, "error": None}


# ---------------------------------------------------------------------------
# Effective config + eligibility computation
# ---------------------------------------------------------------------------


def build_effective_config(args: argparse.Namespace) -> dict:
    """All resolved CLI flags + the locked v1 default snapshot.

    Includes the Phase C write-mode discriminators
    (``write_authorized``, ``output_dir_isolated``,
    ``canonical_write_blocked``, ``write_mode``). These are computed
    here so every emit path - dry-run, refused, isolated-write -
    surfaces the same canonical fields.
    """
    write_flag = bool(getattr(args, "write", False))
    output_dir = getattr(args, "output_dir", DEFAULT_OUTPUT_DIR)
    isolated = is_isolated_output_dir(output_dir)
    if write_flag and not isolated:
        write_mode = "refused"
        canonical_write_blocked = True
        write_authorized = False
    elif write_flag and isolated:
        write_mode = "isolated"
        canonical_write_blocked = False
        write_authorized = True
    else:
        write_mode = "dry_run"
        canonical_write_blocked = False
        write_authorized = False
    return {
        "secondaries": getattr(args, "secondaries", None),
        "secondaries_file": getattr(args, "secondaries_file", None),
        "stackbuilder_root": getattr(args, "stackbuilder_root", DEFAULT_STACKBUILDER_ROOT),
        "use_selected_build": bool(getattr(args, "use_selected_build", True)),
        "explicit_build": getattr(args, "explicit_build", None),
        "output_dir": output_dir,
        "k": getattr(args, "k", None),
        "k_range": getattr(args, "k_range", None),
        "all_selected_k": bool(getattr(args, "all_selected_k", True)),
        "jobs": int(getattr(args, "jobs", DEFAULT_JOBS)),
        "parallel_subsets": int(getattr(args, "parallel_subsets", DEFAULT_PARALLEL_SUBSETS)),
        "subset_workers": int(getattr(args, "subset_workers", DEFAULT_SUBSET_WORKERS)),
        "tf_bitmask_fastpath": int(getattr(args, "tf_bitmask_fastpath", DEFAULT_TF_BITMASK_FASTPATH)),
        "refresh_missing_pkls": bool(getattr(args, "refresh_missing_pkls", False)),
        "max_sma_day": int(getattr(args, "max_sma_day", DEFAULT_MAX_SMA_DAY)),
        "refresh_stale_prices": bool(getattr(args, "refresh_stale_prices", False)),
        "write": write_flag,
        "allow_network_fetch": bool(getattr(args, "allow_network_fetch", False)),
        "duration_budget_minutes": getattr(args, "duration_budget_minutes", None),
        "operator_budget_label": getattr(args, "operator_budget_label", None),
        "no_progress": bool(getattr(args, "no_progress", False)),
        "progress_dir": getattr(args, "progress_dir", None) or DEFAULT_PROGRESS_DIR,
        "strict_inputs": bool(getattr(args, "strict_inputs", False)),
        "skip_secondary_on_input_gate": bool(getattr(args, "skip_secondary_on_input_gate", True)),
        # Phase C write-mode discriminators
        "write_authorized": write_authorized,
        "output_dir_isolated": isolated,
        "canonical_write_blocked": canonical_write_blocked,
        "write_mode": write_mode,
    }


def cell_eligibility(
    price_class: str,
    pkl_classes: Sequence[str],
) -> str:
    """Resolve a (secondary, K) cell eligibility from its inputs."""
    if price_class in ("MISSING", "STALE", "UNREADABLE"):
        return "DATA-GATED"
    if any(c == "MISMATCH_MAX_SMA" or c == "CONFLICTING_MAX_SMA" for c in pkl_classes):
        return "MAX-SMA-GATED"
    if any(c == "STALE" for c in pkl_classes):
        return "STALE-GATED"
    if any(c in ("MISSING", "INVALID", "UNREADABLE", "SCHEMA_MISMATCH",
                 "UNDETERMINABLE_MAX_SMA") for c in pkl_classes):
        return "PKL-GATED"
    if any(c == "UNKNOWN_USABLE" for c in pkl_classes) or price_class == "UNKNOWN_USABLE":
        return "ELIGIBLE_WITH_NOTES"
    return "ELIGIBLE"


# ---------------------------------------------------------------------------
# Per-secondary preflight
# ---------------------------------------------------------------------------


def preflight_secondary(
    secondary: str,
    args: argparse.Namespace,
    k_selection: dict,
    *,
    pandas_module: Optional[Any] = None,
    pickle_module: Any = pickle,
    today: Optional[datetime] = None,
) -> dict:
    """Classify one secondary's readiness end-to-end without compute."""
    stackbuilder_root = getattr(args, "stackbuilder_root", DEFAULT_STACKBUILDER_ROOT)
    explicit_build = getattr(args, "explicit_build", None)
    cache_results_dir = DEFAULT_CACHE_RESULTS_DIR
    price_cache_dir = DEFAULT_PRICE_CACHE_DIR
    max_sma_required = int(getattr(args, "max_sma_day", DEFAULT_MAX_SMA_DAY))

    result: dict[str, Any] = {
        "secondary": secondary,
        "verdict": None,
        "reason": None,
        "selected_build_consumed": None,
        "selected_build_path": None,
        "explicit_build_override": False,
        "selected_run_dir": None,
        "combo_leaderboard_path": None,
        "k_requested": [],
        "k_available": [],
        "k_missing": [],
        "members_by_k": {},
        "price_cache": None,
        "pkl_readiness": [],
        "k_eligibility": {},
        "warnings": [],
        "errors": [],
    }

    sb = read_selected_build(stackbuilder_root, secondary,
                             explicit_build_path=explicit_build)
    result["selected_build_consumed"] = sb.get("payload")
    result["selected_build_path"] = sb.get("sb_path")
    result["explicit_build_override"] = sb.get("explicit_build_override", False)
    result["selected_run_dir"] = sb.get("selected_run_dir")
    if sb["status"] == "refused":
        result["verdict"] = "REFUSED"
        result["reason"] = sb["reason"]
        return result

    # Leaderboard
    run_dir = sb["selected_run_dir"]
    if not run_dir or not Path(run_dir).exists():
        result["verdict"] = "REFUSED"
        result["reason"] = "selected_run_dir_missing"
        return result
    leaderboard = read_combo_leaderboard(run_dir, pandas_module=pandas_module)
    result["combo_leaderboard_path"] = leaderboard.get("path")
    if leaderboard["status"] != "ok":
        result["verdict"] = "REFUSED"
        result["reason"] = "_".join(["combo_leaderboard", leaderboard["status"]])
        result["errors"].extend(leaderboard.get("issues") or [])
        return result

    available_ks = leaderboard["available_ks"]
    k_to_members = leaderboard["k_to_members"]
    # K selection per-secondary
    if k_selection["mode"] == "all_selected":
        ks = list(available_ks)
    else:
        ks = list(k_selection["ks"])
    result["k_requested"] = ks
    result["k_available"] = available_ks
    missing_ks = [k for k in ks if k not in k_to_members]
    result["k_missing"] = missing_ks
    if missing_ks:
        result["warnings"].append(f"k_rows_missing_in_leaderboard: {missing_ks}")
    members_by_k = {k: k_to_members.get(k, []) for k in ks}
    result["members_by_k"] = members_by_k

    # Price cache
    price_class = classify_price_cache(secondary, price_cache_dir,
                                       today=today,
                                       pandas_module=pandas_module)
    result["price_cache"] = price_class
    benchmark_as_of_date = price_class.get("tail_date") if isinstance(price_class, dict) else None

    # Collect unique member set across all requested K rows
    unique_members: list[str] = []
    seen: set[str] = set()
    for k, members in members_by_k.items():
        for tm in members:
            base = base_ticker_of(tm)
            if base and base not in seen:
                seen.add(base)
                unique_members.append(base)

    pkl_results = [
        classify_pkl(m, cache_results_dir,
                     max_sma_required=max_sma_required,
                     benchmark_as_of_date=benchmark_as_of_date,
                     pickle_module=pickle_module)
        for m in unique_members
    ]
    result["pkl_readiness"] = pkl_results
    pkl_by_member = {p["member"]: p for p in pkl_results}

    # Cell eligibility per K
    cell_elig: dict[str, str] = {}
    for k in ks:
        k_pkl_classes = [
            pkl_by_member[base_ticker_of(tm)]["classification"]
            for tm in members_by_k.get(k, [])
            if base_ticker_of(tm) in pkl_by_member
        ]
        if k in missing_ks:
            cell_elig[f"K{k}"] = "PKL-GATED" if not k_pkl_classes else "ELIGIBLE"
            # If the row isn't in the leaderboard, the cell is gated; we mark
            # PKL-GATED conservatively.
            cell_elig[f"K{k}"] = "PKL-GATED"
            continue
        cell_elig[f"K{k}"] = cell_eligibility(price_class["classification"],
                                              k_pkl_classes)
    result["k_eligibility"] = cell_elig

    # Aggregate verdict
    gates_seen = set(cell_elig.values())
    if not cell_elig:
        result["verdict"] = "REFUSED"
        result["reason"] = "no_k_levels_resolved"
    elif gates_seen == {"ELIGIBLE"}:
        result["verdict"] = "ELIGIBLE"
    elif gates_seen <= {"ELIGIBLE", "ELIGIBLE_WITH_NOTES"}:
        result["verdict"] = "ELIGIBLE_WITH_NOTES"
    else:
        # mixed or any gate
        if all(v != "ELIGIBLE" and v != "ELIGIBLE_WITH_NOTES" for v in cell_elig.values()):
            # Pick the most informative single-gate label
            for label in ("DATA-GATED", "MAX-SMA-GATED", "STALE-GATED", "PKL-GATED"):
                if label in gates_seen:
                    result["verdict"] = label
                    break
            if result["verdict"] is None:
                result["verdict"] = "ERROR"
        else:
            result["verdict"] = "ELIGIBLE_WITH_NOTES"

    return result


# ---------------------------------------------------------------------------
# Repair report aggregation
# ---------------------------------------------------------------------------


PKL_REPAIR_CLASSES = (
    "MISSING", "STALE", "MISMATCH_MAX_SMA", "CONFLICTING_MAX_SMA",
    "INVALID", "UNREADABLE", "SCHEMA_MISMATCH",
)
PRICE_REPAIR_CLASSES = ("MISSING", "STALE", "UNREADABLE")


def build_would_refresh_lists(
    per_secondary: list[dict],
    args: argparse.Namespace,
) -> tuple[list[dict], list[dict]]:
    refresh_pkls_flag = bool(getattr(args, "refresh_missing_pkls", False))
    refresh_prices_flag = bool(getattr(args, "refresh_stale_prices", False))
    max_sma_day = int(getattr(args, "max_sma_day", DEFAULT_MAX_SMA_DAY))

    would_refresh_pkls: list[dict] = []
    would_refresh_prices: list[dict] = []

    if refresh_pkls_flag:
        seen_members: set[str] = set()
        for sec_result in per_secondary:
            for pkl in sec_result.get("pkl_readiness") or []:
                cls = pkl.get("classification")
                if cls in PKL_REPAIR_CLASSES and pkl["member"] not in seen_members:
                    seen_members.add(pkl["member"])
                    would_refresh_pkls.append({
                        "ticker": pkl["member"],
                        "classification": cls,
                        "command_shape": (
                            "<PINNED_INTERPRETER> signal_engine_cache_refresher.py "
                            f"--ticker {pkl['member']} --write "
                            "--cache-dir cache/results --status-dir cache/status "
                            f"--max-sma-day {max_sma_day}"
                        ),
                    })
    if refresh_prices_flag:
        for sec_result in per_secondary:
            pc = sec_result.get("price_cache") or {}
            if pc.get("classification") in PRICE_REPAIR_CLASSES:
                would_refresh_prices.append({
                    "secondary": pc["secondary"],
                    "classification": pc["classification"],
                    "helper_call_shape": (
                        "trafficflow.refresh_secondary_caches("
                        f"['{pc['secondary']}'], force=False)"
                    ),
                })
    return would_refresh_pkls, would_refresh_prices


# ---------------------------------------------------------------------------
# Phase C isolated-output write execution
# ---------------------------------------------------------------------------


def _default_compute_loader() -> Callable[..., list]:
    """Lazily resolve a TrafficFlow compute wrapper.

    Returns a callable with signature
    ``(secondary, k, *, run_fence, missing_map, combo_leaderboard_path)``.
    The wrapper:

      1. Lazily imports ``trafficflow``.
      2. Pins ``trafficflow._find_latest_combo_table`` to a function
         that returns the supplied ``combo_leaderboard_path`` for the
         requested secondary. This prevents the engine from falling
         back to its latest-by-ctime directory scan when the runner
         has already resolved the canonical path via
         ``selected_build.json``.
      3. Calls ``trafficflow.build_board_rows(secondary, k=k,
         run_fence=run_fence, missing_map=missing_map)``.
      4. Restores the original ``_find_latest_combo_table`` in a
         ``finally`` block whether or not compute raised.

    Raises ``ValueError`` if ``combo_leaderboard_path`` is missing,
    so a misconfigured caller cannot silently fall through to the
    engine default.

    Only invoked inside the authorized isolated-write execution path.
    Never called during dry-run or canonical-refused paths. Tests inject
    a fake ``compute_callable`` rather than triggering this loader.
    """
    import trafficflow as tf  # local import; not a module-level dependency

    def _wrapped(secondary, k, *, run_fence, missing_map,
                 combo_leaderboard_path):
        if not combo_leaderboard_path:
            raise ValueError(
                "trafficflow_runner Phase C compute requires "
                "combo_leaderboard_path resolved from selected_build.json"
            )
        pinned = Path(str(combo_leaderboard_path))
        original = getattr(tf, "_find_latest_combo_table", None)

        def _pinned_finder(sec, *args, **kwargs):
            # Pin to the resolved path only when the requested
            # secondary matches; otherwise raise so the engine's own
            # cross-secondary lookups cannot silently use a stale path.
            if str(sec).upper() == str(secondary).upper():
                return pinned
            raise RuntimeError(
                "trafficflow_runner Phase C: unexpected "
                "_find_latest_combo_table call for "
                f"{sec!r} (expected {secondary!r})"
            )

        try:
            if original is not None:
                tf._find_latest_combo_table = _pinned_finder
            return tf.build_board_rows(
                secondary, k=k, run_fence=run_fence, missing_map=missing_map
            )
        finally:
            if original is not None:
                tf._find_latest_combo_table = original

    return _wrapped


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a ``.tmp`` sibling + ``replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _atomic_write_json(path: Path, payload: Any) -> None:
    blob = json.dumps(payload, indent=2, default=str).encode("utf-8")
    _atomic_write_bytes(path, blob)


def _board_rows_to_csv_bytes(rows: list[dict]) -> bytes:
    """Serialize a list of board-row dicts to CSV bytes.

    Column order is preserved from the first row that defines a column;
    later rows that introduce new columns extend the header. Missing
    values in a row are rendered as the empty string. An empty rows
    list yields a single newline (no header).
    """
    import csv
    import io as _io
    if not rows:
        return b""
    cols: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r.keys():
            ks = str(k)
            if ks not in seen:
                seen.add(ks)
                cols.append(ks)
    buf = _io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c, "") for c in cols})
    return buf.getvalue().encode("utf-8")


def _execute_isolated_write(
    envelope: dict,
    per_secondary: list[dict],
    args: argparse.Namespace,
    *,
    compute_callable: Optional[Callable[..., list]] = None,
) -> tuple[str, dict, list[str]]:
    """Run TrafficFlow compute for every ELIGIBLE cell and write
    isolated-output artifacts under ``args.output_dir``.

    Returns ``(status, write_summary, artifacts_written)`` where status
    is one of ``"ok"``, ``"partial"``, ``"failed"`` and the artifact
    list contains repo-relative or sanitized paths.
    """
    output_dir = Path(getattr(args, "output_dir", DEFAULT_OUTPUT_DIR))
    output_dir.mkdir(parents=True, exist_ok=True)

    cells_requested = 0
    cells_eligible = 0
    cells_written = 0
    cells_skipped = 0
    cells_errored = 0
    artifacts_written: list[str] = []
    per_cell_summary: list[dict] = []
    consec_errors = 0
    short_circuit = False

    compute = compute_callable if compute_callable is not None else _default_compute_loader()

    for sec_row in per_secondary:
        secondary = sec_row.get("secondary")
        elig_map = sec_row.get("k_eligibility") or {}
        members_by_k = sec_row.get("members_by_k") or {}
        combo_leaderboard_path = sec_row.get("combo_leaderboard_path")
        for k_key, eligibility in elig_map.items():
            cells_requested += 1
            try:
                k_val = int(str(k_key).lstrip("K"))
            except ValueError:
                k_val = None
            cell_record: dict[str, Any] = {
                "secondary": secondary,
                "k": k_val,
                "eligibility": eligibility,
                "status": None,
                "row_count": 0,
                "elapsed_seconds": 0.0,
                "board_rows_json_path": None,
                "board_rows_csv_path": None,
                "error_class": None,
                "error_message": None,
                "skip_reason": None,
            }
            if eligibility != "ELIGIBLE":
                cells_skipped += 1
                cell_record["status"] = "skipped"
                cell_record["skip_reason"] = f"non_eligible:{eligibility}"
                per_cell_summary.append(cell_record)
                continue
            if short_circuit:
                cells_skipped += 1
                cell_record["status"] = "skipped"
                cell_record["skip_reason"] = "short_circuited_after_consecutive_errors"
                per_cell_summary.append(cell_record)
                continue
            cells_eligible += 1
            run_fence = {"global": None, "by_sec": {}}
            missing_map: Optional[dict] = None
            t0 = time.perf_counter()
            try:
                rows = compute(
                    secondary, k_val,
                    run_fence=run_fence, missing_map=missing_map,
                    combo_leaderboard_path=combo_leaderboard_path,
                )
                if rows is None:
                    rows = []
                if not isinstance(rows, list):
                    rows = list(rows)
            except Exception as exc:
                cells_errored += 1
                consec_errors += 1
                cell_record["status"] = "error"
                cell_record["elapsed_seconds"] = round(
                    time.perf_counter() - t0, 4)
                cell_record["error_class"] = type(exc).__name__
                cell_record["error_message"] = repr(exc)[:240]
                per_cell_summary.append(cell_record)
                if consec_errors >= 3:
                    short_circuit = True
                continue

            consec_errors = 0
            cell_record["elapsed_seconds"] = round(
                time.perf_counter() - t0, 4)
            cell_record["row_count"] = len(rows)

            sec_dir = output_dir / str(secondary)
            sec_dir.mkdir(parents=True, exist_ok=True)
            json_p = sec_dir / f"board_rows_k={k_val}.json"
            csv_p = sec_dir / f"board_rows_k={k_val}.csv"
            try:
                _atomic_write_json(json_p, rows)
                _atomic_write_bytes(csv_p, _board_rows_to_csv_bytes(rows))
            except Exception as exc:
                cells_errored += 1
                consec_errors += 1
                cell_record["status"] = "error"
                cell_record["error_class"] = type(exc).__name__
                cell_record["error_message"] = repr(exc)[:240]
                per_cell_summary.append(cell_record)
                if consec_errors >= 3:
                    short_circuit = True
                continue

            cells_written += 1
            cell_record["status"] = "ok"
            json_rel = path_for_output(str(json_p))
            csv_rel = path_for_output(str(csv_p))
            cell_record["board_rows_json_path"] = json_rel
            cell_record["board_rows_csv_path"] = csv_rel
            if json_rel:
                artifacts_written.append(json_rel)
            if csv_rel:
                artifacts_written.append(csv_rel)
            per_cell_summary.append(cell_record)

    write_summary = {
        "cells_requested": cells_requested,
        "cells_eligible": cells_eligible,
        "cells_written": cells_written,
        "cells_skipped": cells_skipped,
        "cells_errored": cells_errored,
        "artifacts_written_count": len(artifacts_written),
        "short_circuited_after_consecutive_errors": short_circuit,
    }

    if cells_errored == 0 and cells_written > 0 and cells_skipped == 0:
        status = "ok"
    elif cells_written == 0:
        status = "failed"
    else:
        status = "partial"

    envelope["per_cell_summary"] = per_cell_summary
    envelope["write_summary"] = write_summary
    return status, write_summary, artifacts_written


def _write_run_manifest(
    output_dir: Path,
    envelope: dict,
    artifacts_written: list[str],
) -> Optional[str]:
    """Write ``run_manifest.json`` to the isolated output directory.

    ``artifacts_written`` is the final list to record in the manifest,
    including the manifest path and the ``run.stdout.json`` path; the
    caller is responsible for assembling that complete list BEFORE
    invoking this writer so both on-disk run files reference the same
    set.
    """
    manifest = {
        "schema_version": PHASE_C_RUN_MANIFEST_SCHEMA,
        "run_id": envelope.get("run_id"),
        "stage": envelope.get("stage"),
        "started_at": envelope.get("started_at"),
        "ended_at": envelope.get("ended_at"),
        "elapsed_seconds": envelope.get("elapsed_seconds"),
        "git_head": envelope.get("git_head"),
        "inputs": envelope.get("inputs"),
        "effective_config": envelope.get("effective_config"),
        "selected_build_consumed": envelope.get("selected_build_consumed"),
        "per_cell_summary": envelope.get("per_cell_summary"),
        "write_summary": envelope.get("write_summary"),
        "canonical_artifacts_referenced": _build_canonical_artifacts_ref(
            envelope.get("per_secondary_results") or []
        ),
        "output_dir": path_for_output(str(output_dir)),
        "write_mode": "isolated",
        "artifacts_written": list(artifacts_written),
    }
    safe = sanitize_for_json(manifest, project_root=Path.cwd())
    path = output_dir / "run_manifest.json"
    try:
        _atomic_write_json(path, safe)
    except Exception:
        return None
    return path_for_output(str(path))


def _build_canonical_artifacts_ref(per_secondary: list[dict]) -> list[dict]:
    """Per-secondary ``selected_build.json`` provenance reference.

    Uses the actual ``selected_build_path`` captured by
    ``preflight_secondary`` for each secondary. Honors any
    ``--stackbuilder-root`` override the operator supplied; does NOT
    recompute from ``DEFAULT_STACKBUILDER_ROOT``. SHA-256 is read from
    the same file the preflight consumed.

    Explicit-build override mode emits a null ``selected_build_path``
    and ``selected_build_sha256`` plus ``explicit_build_override=True``
    and the sanitized ``selected_run_dir``.
    """
    out: list[dict] = []
    for sec_row in per_secondary:
        sec = sec_row.get("secondary")
        explicit_override = bool(sec_row.get("explicit_build_override"))
        sb_path_raw = sec_row.get("selected_build_path")
        run_dir_raw = sec_row.get("selected_run_dir")

        if explicit_override or not sb_path_raw:
            out.append({
                "secondary": sec,
                "selected_build_path": (
                    path_for_output(sb_path_raw) if sb_path_raw else None
                ),
                "selected_build_sha256": None,
                "explicit_build_override": explicit_override,
                "selected_run_dir": (
                    path_for_output(run_dir_raw) if run_dir_raw else None
                ),
            })
            continue

        sb_path = Path(sb_path_raw)
        sha = None
        provenance_warning = None
        try:
            if sb_path.is_file():
                import hashlib
                h = hashlib.sha256()
                with open(sb_path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 16), b""):
                        h.update(chunk)
                sha = h.hexdigest()
            else:
                provenance_warning = "selected_build_path_not_a_file"
        except Exception as exc:
            provenance_warning = f"selected_build_sha_error:{type(exc).__name__}"

        entry = {
            "secondary": sec,
            "selected_build_path": path_for_output(str(sb_path)),
            "selected_build_sha256": sha,
            "explicit_build_override": False,
            "selected_run_dir": (
                path_for_output(run_dir_raw) if run_dir_raw else None
            ),
        }
        if provenance_warning is not None:
            entry["provenance_warning"] = provenance_warning
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _emit_json(payload: dict) -> None:
    """Sanitize the envelope before writing to stdout.

    Path-like values are converted to repo-relative POSIX strings or
    redacted with ``<ABSOLUTE_PATH_REDACTED>``. Raw process command
    lines in ``process_conflict_result.conflicts`` are dropped in favor
    of ``matched_pattern`` + ``command_line_redacted=true``.
    """
    safe = sanitize_for_json(payload, project_root=Path.cwd())
    sys.stdout.write(json.dumps(safe, indent=2, default=str))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _stderr(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    process_conflict_checker: Callable[..., dict] = check_process_conflicts,
    pandas_module: Optional[Any] = None,
    pickle_module: Any = pickle,
    today: Optional[datetime] = None,
    compute_callable: Optional[Callable[..., list]] = None,
) -> int:
    """Phase B dry-run / Phase C isolated-write entrypoint.

    Always emits exactly one JSON object on stdout. Returns:
      0 on dry_run success or isolated-write ok;
      1 on refused, partial, failed, or per-invocation error;
      3 on process-conflict refusal.

    ``compute_callable`` is the test seam for Phase C isolated-write
    mode. When not supplied and ``--write`` is authorized for an
    isolated output directory, the runner lazily imports
    ``trafficflow.build_board_rows``. The dry-run path never resolves
    the compute callable.
    """
    args = parse_args(argv)
    start_t = time.perf_counter()
    started_at = _utc_iso()
    run_id = _run_id()
    git_head = _git_head()
    effective_config = build_effective_config(args)

    # Always include these in the envelope, even on refusal.
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "stage": STAGE_NAME,
        "run_id": run_id,
        "status": "dry_run",
        "started_at": started_at,
        "ended_at": None,
        "elapsed_seconds": None,
        "cwd": "<PROJECT_ROOT>",
        "git_head": git_head,
        "inputs": {
            "secondaries": getattr(args, "secondaries", None),
            "secondaries_file": getattr(args, "secondaries_file", None),
            "stackbuilder_root": effective_config["stackbuilder_root"],
            "output_dir": effective_config["output_dir"],
        },
        "effective_config": effective_config,
        "process_conflict_result": None,
        "input_readiness_summary": {},
        "per_secondary_results": [],
        "selected_build_consumed": [],
        "benchmark_eligibility": {},
        "would_refresh_pkls": [],
        "would_refresh_prices": [],
        "artifacts_written": [],
        "warnings": [],
        "errors": [],
        "next_stage_ready": False,
        "verdict": None,
    }

    write_flag = bool(getattr(args, "write", False))

    # Phase C canonical-output guardrail: --write to canonical
    # ``output/trafficflow`` (or any descendant) is structurally
    # refused before any preflight or compute work. The refusal
    # must NOT import trafficflow.
    if write_flag and effective_config.get("canonical_write_blocked"):
        envelope["status"] = "refused"
        envelope["warnings"].append("canonical_write_forbidden_in_phase_c")
        envelope["errors"].append("canonical_write_forbidden_in_phase_c")
        envelope["verdict"] = "REFUSED"
        envelope["ended_at"] = _utc_iso()
        envelope["elapsed_seconds"] = round(time.perf_counter() - start_t, 4)
        _stderr(
            "trafficflow_runner: Phase C refuses --write to canonical "
            "output/trafficflow. Use an isolated --output-dir for "
            "supervised smoke; canonical writes are reserved for a "
            "later operator-authorized phase."
        )
        _emit_json(envelope)
        return EXIT_REFUSED

    # Process-conflict check. When --write is authorized, the checker
    # is asked to fail-closed (status="error" on enumeration failure);
    # otherwise it returns status="unknown" advisorily.
    write_requested = bool(effective_config.get("write_authorized"))
    conflict_result = process_conflict_checker(write_requested=write_requested)
    envelope["process_conflict_result"] = conflict_result
    if conflict_result.get("status") == "blocked":
        envelope["status"] = "refused"
        envelope["errors"].append("process_conflict_blocked")
        envelope["verdict"] = "REFUSED"
        envelope["ended_at"] = _utc_iso()
        envelope["elapsed_seconds"] = round(time.perf_counter() - start_t, 4)
        _stderr(f"trafficflow_runner: process conflict blocked; "
                f"matched={[c.get('matched_pattern') for c in conflict_result.get('conflicts', [])]}")
        _emit_json(envelope)
        return EXIT_PROCESS_CONFLICT
    # In write-authorized mode, also fail closed on conflict
    # enumeration errors. Dry-run / non-write modes only treat the
    # enumeration as advisory and continue.
    if (write_requested
            and conflict_result.get("status") == "error"):
        envelope["status"] = "refused"
        envelope["errors"].append("process_conflict_enumeration_unavailable")
        envelope["warnings"].append("process_conflict_enumeration_unavailable")
        envelope["verdict"] = "REFUSED"
        envelope["ended_at"] = _utc_iso()
        envelope["elapsed_seconds"] = round(time.perf_counter() - start_t, 4)
        _stderr(
            "trafficflow_runner: process-conflict enumeration "
            "unavailable in write mode; refusing write to fail closed."
        )
        _emit_json(envelope)
        return EXIT_PROCESS_CONFLICT

    # Secondaries resolution
    sec_res = resolve_secondaries(args)
    if sec_res["status"] != "ok":
        envelope["status"] = "refused"
        envelope["errors"].extend(sec_res["issues"])
        envelope["verdict"] = "REFUSED"
        envelope["ended_at"] = _utc_iso()
        envelope["elapsed_seconds"] = round(time.perf_counter() - start_t, 4)
        _emit_json(envelope)
        return EXIT_REFUSED
    secondaries = sec_res["secondaries"]

    # K selection
    k_sel = parse_k_selection(args)
    if k_sel["issues"]:
        envelope["warnings"].extend(k_sel["issues"])

    # If --explicit-build was set, only one secondary may be requested.
    if getattr(args, "explicit_build", None) and len(secondaries) != 1:
        envelope["status"] = "refused"
        envelope["errors"].append("explicit_build_requires_single_secondary")
        envelope["verdict"] = "REFUSED"
        envelope["ended_at"] = _utc_iso()
        envelope["elapsed_seconds"] = round(time.perf_counter() - start_t, 4)
        _emit_json(envelope)
        return EXIT_REFUSED

    # Per-secondary preflight
    per_secondary: list[dict] = []
    for sec in secondaries:
        try:
            r = preflight_secondary(
                sec, args, k_sel,
                pandas_module=pandas_module,
                pickle_module=pickle_module,
                today=today,
            )
        except Exception:  # pragma: no cover - defensive
            tb = traceback.format_exc(limit=4)
            r = {
                "secondary": sec,
                "verdict": "ERROR",
                "reason": "preflight_exception",
                "errors": [tb.splitlines()[-1] if tb else "preflight_exception"],
            }
        per_secondary.append(r)
    envelope["per_secondary_results"] = per_secondary
    envelope["selected_build_consumed"] = [
        {"secondary": r["secondary"],
         "selected_build_payload": r.get("selected_build_consumed"),
         "explicit_build_override": r.get("explicit_build_override", False),
         "selected_run_dir": r.get("selected_run_dir")}
        for r in per_secondary
    ]

    # Aggregate readiness summary
    summary: dict[str, int] = {}
    for r in per_secondary:
        v = r.get("verdict") or "ERROR"
        summary[v] = summary.get(v, 0) + 1
    envelope["input_readiness_summary"] = summary

    # Benchmark eligibility matrix per (secondary, K)
    elig: dict[str, dict[str, str]] = {}
    for r in per_secondary:
        elig[r["secondary"]] = r.get("k_eligibility") or {}
    envelope["benchmark_eligibility"] = elig

    # Would-refresh lists
    pkl_repair, price_repair = build_would_refresh_lists(per_secondary, args)
    envelope["would_refresh_pkls"] = pkl_repair
    envelope["would_refresh_prices"] = price_repair

    # Final verdict
    if all(r.get("verdict") == "ELIGIBLE" for r in per_secondary) and per_secondary:
        envelope["verdict"] = "ALL_ELIGIBLE_DRY_RUN"
    elif any(r.get("verdict") in ("REFUSED", "ERROR") for r in per_secondary):
        envelope["verdict"] = "PARTIAL"
        if bool(getattr(args, "strict_inputs", False)):
            envelope["status"] = "refused"
            envelope["errors"].append("strict_inputs_one_or_more_secondary_gated")
            envelope["ended_at"] = _utc_iso()
            envelope["elapsed_seconds"] = round(time.perf_counter() - start_t, 4)
            _emit_json(envelope)
            return EXIT_REFUSED
    else:
        envelope["verdict"] = "PARTIAL"

    # ------------------------------------------------------------------
    # Phase C isolated-write execution
    # ------------------------------------------------------------------
    # Reached only when --write is authorized AND the output dir is
    # isolated. Dry-run callers (the canonical Phase B contract) skip
    # this block entirely because ``write_authorized`` is False.
    if effective_config.get("write_authorized"):
        try:
            status, write_summary, artifacts_written = _execute_isolated_write(
                envelope, per_secondary, args,
                compute_callable=compute_callable,
            )
        except Exception as exc:
            envelope["status"] = "failed"
            envelope["errors"].append(
                f"isolated_write_exception:{type(exc).__name__}"
            )
            envelope["verdict"] = "ERROR"
            envelope["ended_at"] = _utc_iso()
            envelope["elapsed_seconds"] = round(
                time.perf_counter() - start_t, 4)
            envelope["write_mode"] = "isolated"
            _emit_json(envelope)
            return EXIT_REFUSED
        envelope["status"] = status
        envelope["write_mode"] = "isolated"
        envelope["next_stage_ready"] = False
        envelope["ended_at"] = _utc_iso()
        envelope["elapsed_seconds"] = round(
            time.perf_counter() - start_t, 4)
        # Compute the COMPLETE artifact list before writing either of
        # the two run files, so both on-disk files (run_manifest.json
        # and run.stdout.json) reference the same full list including
        # themselves. Both run files are written under the isolated
        # output dir.
        output_dir = Path(getattr(args, "output_dir", DEFAULT_OUTPUT_DIR))
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = output_dir / "run_manifest.json"
            stdout_path = output_dir / "run.stdout.json"
            manifest_rel = path_for_output(str(manifest_path))
            stdout_rel = path_for_output(str(stdout_path))
            final_artifacts = list(artifacts_written)
            if manifest_rel:
                final_artifacts.append(manifest_rel)
            if stdout_rel:
                final_artifacts.append(stdout_rel)
            envelope["artifacts_written"] = final_artifacts
            # Keep write_summary.artifacts_written_count consistent
            # with the final list now that the two run files are
            # accounted for.
            if isinstance(envelope.get("write_summary"), dict):
                envelope["write_summary"]["artifacts_written_count"] = (
                    len(final_artifacts)
                )
            # Write manifest first; both run files end up referencing
            # the same final artifact list because manifest_rel /
            # stdout_rel were appended to the envelope above before
            # either file was written.
            _write_run_manifest(output_dir, envelope, final_artifacts)
            safe_envelope = sanitize_for_json(
                envelope, project_root=Path.cwd())
            _atomic_write_json(stdout_path, safe_envelope)
        except Exception as exc:  # pragma: no cover - defensive
            envelope["warnings"].append(
                f"manifest_write_exception:{type(exc).__name__}"
            )
        _emit_json(envelope)
        return EXIT_OK if status == "ok" else EXIT_REFUSED

    # Dry-run path
    envelope["next_stage_ready"] = False  # Phase B never advances the stage
    envelope["ended_at"] = _utc_iso()
    envelope["elapsed_seconds"] = round(time.perf_counter() - start_t, 4)
    _emit_json(envelope)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
