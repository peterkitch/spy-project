"""TrafficFlow runner Phase B: dry-run preflight scaffold.

Phase B of the TrafficFlow headless runner work. Phase A scoping doc:
``md_library/shared/2026-05-24_TRAFFICFLOW_RUNNER_EXECUTION_SURFACE.md``.

Behavior contract:

  * Dry-run is the default. The runner never invokes the TrafficFlow
    compute path in Phase B; it never imports ``trafficflow``.
  * ``--write`` is unconditionally refused in Phase B (status
    ``refused`` with reason ``phase_b_write_not_supported``).
  * Per-secondary ``selected_build.json`` is consumed explicitly. The
    runner refuses any secondary whose ``selected_build.json`` is
    missing unless ``--explicit-build`` is supplied (and then only when
    exactly one secondary was requested).
  * The runner never falls back to a latest-by-ctime directory listing.
    It does NOT call ``trafficflow._find_latest_combo_table``.
  * Process-conflict check enumerates command lines and refuses when
    another engine/runner is active.
  * stdout is exactly one JSON object emitted by ``main``.
  * stderr carries human-readable progress, warnings, and tracebacks.
  * Repair flags (``--refresh-missing-pkls`` and
    ``--refresh-stale-prices``) are report-only in Phase B; the runner
    computes ``would_refresh_pkls`` / ``would_refresh_prices`` lists
    but does not invoke ``signal_engine_cache_refresher.py`` and does
    not call ``trafficflow.refresh_secondary_caches``.

No top-level import of ``trafficflow``, ``signal_engine_cache_refresher``,
``stackbuilder``, ``onepass``, ``impactsearch``, ``spymaster``,
``confluence``, ``multi_timeframe_builder``, ``dash``,
``dash_bootstrap_components``, ``yfinance``, ``plotly``.

``pandas`` is imported only inside the leaderboard-reading helper to
keep the dry-run path importable in fixture-free contexts.
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

# Exit codes
EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_ARGPARSE = 2  # argparse exits 2 itself on usage error
EXIT_PROCESS_CONFLICT = 3

_RAW_TICKER_SPLIT_RE = re.compile(r"[,\s]+")


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
# selected_build.json + combo_leaderboard.xlsx
# ---------------------------------------------------------------------------


def _selected_build_path(stackbuilder_root: str, secondary: str) -> Path:
    return Path(stackbuilder_root) / secondary / "selected_build.json"


def read_selected_build(
    stackbuilder_root: str,
    secondary: str,
    explicit_build_path: Optional[str] = None,
) -> dict:
    """Read and validate selected_build.json for a secondary.

    Returns dict with keys:
      status: "ok" | "refused" | "explicit_override"
      reason: explanation when not ok
      sb_path: path inspected (repo-relative-like)
      payload: parsed JSON dict on ok
      explicit_build_override: bool
      selected_run_dir: resolved repo-relative or as-provided string
    """
    if explicit_build_path:
        run_dir = Path(explicit_build_path)
        if not run_dir.exists() or not run_dir.is_dir():
            return {
                "status": "refused",
                "reason": "explicit_build_path_missing",
                "sb_path": None,
                "payload": None,
                "explicit_build_override": True,
                "selected_run_dir": str(explicit_build_path),
            }
        return {
            "status": "explicit_override",
            "reason": None,
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
            "sb_path": str(sb_path),
            "payload": None,
            "explicit_build_override": False,
            "selected_run_dir": None,
        }
    if not isinstance(payload, dict):
        return {
            "status": "refused",
            "reason": "selected_build_not_object",
            "sb_path": str(sb_path),
            "payload": None,
            "explicit_build_override": False,
            "selected_run_dir": None,
        }
    run_dir = payload.get("selected_run_dir")
    if not run_dir:
        return {
            "status": "refused",
            "reason": "selected_build_missing_selected_run_dir",
            "sb_path": str(sb_path),
            "payload": payload,
            "explicit_build_override": False,
            "selected_run_dir": None,
        }
    return {
        "status": "ok",
        "reason": None,
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


def classify_pkl(
    member: str,
    cache_results_dir: str = DEFAULT_CACHE_RESULTS_DIR,
    *,
    max_sma_required: int = DEFAULT_MAX_SMA_DAY,
    pickle_module: Any = pickle,
) -> dict:
    """Classify a Spymaster precomputed-result PKL plus its manifest.

    Returns dict with keys:
      member, classification, max_sma_class, path_rel, manifest_max_sma_day,
      has_SMA_114, missing_fields, issues, declared_inferred
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

    # Top-level classification
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
    """All resolved CLI flags + the locked v1 default snapshot."""
    return {
        "secondaries": getattr(args, "secondaries", None),
        "secondaries_file": getattr(args, "secondaries_file", None),
        "stackbuilder_root": getattr(args, "stackbuilder_root", DEFAULT_STACKBUILDER_ROOT),
        "use_selected_build": bool(getattr(args, "use_selected_build", True)),
        "explicit_build": getattr(args, "explicit_build", None),
        "output_dir": getattr(args, "output_dir", DEFAULT_OUTPUT_DIR),
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
        "write": bool(getattr(args, "write", False)),
        "allow_network_fetch": bool(getattr(args, "allow_network_fetch", False)),
        "duration_budget_minutes": getattr(args, "duration_budget_minutes", None),
        "operator_budget_label": getattr(args, "operator_budget_label", None),
        "no_progress": bool(getattr(args, "no_progress", False)),
        "progress_dir": getattr(args, "progress_dir", None) or DEFAULT_PROGRESS_DIR,
        "strict_inputs": bool(getattr(args, "strict_inputs", False)),
        "skip_secondary_on_input_gate": bool(getattr(args, "skip_secondary_on_input_gate", True)),
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
# Main
# ---------------------------------------------------------------------------


def _emit_json(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, default=str))
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
) -> int:
    """Phase B dry-run entrypoint.

    Always emits exactly one JSON object on stdout. Returns:
      0 on dry_run success;
      1 on refused/error per-invocation;
      3 on process-conflict refusal.
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

    # Phase B unconditionally refuses --write.
    if bool(getattr(args, "write", False)):
        envelope["status"] = "refused"
        envelope["warnings"].append("phase_b_write_not_supported")
        envelope["errors"].append("phase_b_write_not_supported")
        envelope["verdict"] = "REFUSED"
        envelope["ended_at"] = _utc_iso()
        envelope["elapsed_seconds"] = round(time.perf_counter() - start_t, 4)
        _stderr("trafficflow_runner: Phase B refuses --write. "
                "Use Phase E for canonical writes.")
        _emit_json(envelope)
        return EXIT_REFUSED

    # Process-conflict check (advisory in Phase B; refuse on actual blockage)
    write_requested = False
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

    envelope["next_stage_ready"] = False  # Phase B never advances the stage
    envelope["ended_at"] = _utc_iso()
    envelope["elapsed_seconds"] = round(time.perf_counter() - start_t, 4)
    _emit_json(envelope)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
