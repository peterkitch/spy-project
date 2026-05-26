"""MVP v0 ranking engine.

Phase 1 of the three-phase rollout described in the MVP Ranking
Contract (PR #325, ``md_library/shared/2026-05-25_MVP_RANKING_CONTRACT.md``).
Reads TrafficFlow Phase E canonical output via
``output/trafficflow/selected_output.json`` and emits one
``mvp_ranking_v0.json`` artifact describing the eight Phase 6I-79
secondaries ranked by K=6 Sharpe descending as emitted by Phase E.

The engine honors the v0 honesty principle from the contract: it
does NOT sign-flip Sharpe or capture values, derive BUY/SHORT
recommendations, recompute SHORT metrics, perform match-rule
scoring, or compute CCC. Those capabilities require the v1 history
artifact that does not exist yet. The engine reads Phase E canonical
output exclusively; it does not read raw signal libraries, price
cache CSVs, or Spymaster cache PKLs, and it does not run
``trafficflow``, ``trafficflow_runner``, ``trafficflow_canonical_orchestrator``,
or any other pipeline component.

All emitted JSON routes through Phase E's existing privacy
sanitization layer (``sanitize_for_json`` for path-typed leaves,
``_scrub_embedded_absolute_paths`` for free-form error messages).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from trafficflow_runner import (  # noqa: E402
    _atomic_write_json,
    _scrub_embedded_absolute_paths,
    path_for_output,
    sanitize_for_json,
)


SCHEMA_VERSION = "mvp_ranking_v0"
ARTIFACT_FILENAME = "mvp_ranking_v0.json"

MVP_V0_DEFAULT_SECONDARIES: tuple[str, ...] = (
    "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "SPY", "TSLA",
)

LOW_SAMPLE_THRESHOLD = 30

EXIT_OK = 0
EXIT_GLOBAL_FAILURE = 2
EXIT_ALL_SECONDARIES_FAILED = 3

OPTIONAL_PHASE_E_STATUS_KEYS: tuple[str, ...] = (
    "Today", "Now", "NEXT", "TMRW", "MIX",
)


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mvp_ranking_v0",
        description=(
            "Read TrafficFlow Phase E canonical output and emit the "
            "MVP v0 ranking artifact (Phase 1 of the MVP Ranking Contract). "
            "Ranks the requested secondaries by K=6 Sharpe descending "
            "as emitted by Phase E; does not sign-flip, recompute, or "
            "synthesize values."
        ),
    )
    p.add_argument(
        "--trafficflow-selected-output", required=True,
        help="Path to output/trafficflow/selected_output.json.",
    )
    p.add_argument(
        "--output-dir", required=True,
        help="MVP v0 run output directory; e.g. output/mvp/runs/<UTC_TS>.",
    )
    p.add_argument(
        "--secondaries", default=None,
        help="Comma-separated secondary tickers. Default: the eight "
             "Phase 6I-79 secondaries (AAPL, AMZN, GOOGL, META, MSFT, "
             "NVDA, SPY, TSLA).",
    )
    return p.parse_args(list(argv) if argv is not None else None)


def _parse_secondaries(raw: Optional[str]) -> list[str]:
    if not raw:
        return list(MVP_V0_DEFAULT_SECONDARIES)
    out: list[str] = []
    seen: set[str] = set()
    for tok in str(raw).split(","):
        t = tok.strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# Numeric coercion
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
    if isinstance(value, int):
        return int(value)
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    if abs(f - round(f)) > 1e-9:
        return None
    return int(round(f))


def _normalize_members(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        return [p for p in parts if p]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    return []


# ---------------------------------------------------------------------------
# Per-secondary processing
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _select_k6_row(rows: Any) -> Optional[dict]:
    """Return the first K=6 row from a board_rows JSON payload.

    Per the contract: ``board_rows_k=6.json`` is expected to be a list
    of dicts; if multiple K=6 rows are present, the first is selected.
    """
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        k_val = _coerce_int(row.get("K"))
        if k_val == 6:
            return row
    return None


def _extract_phase_e_status(row: dict) -> dict:
    """Collect optional Phase E status / signal fields from the K=6 row.

    Returns ``{}`` when none of the documented status keys are present.
    The dict is not synthesized; only keys actually emitted by Phase E
    are forwarded.
    """
    out: dict[str, Any] = {}
    for key in OPTIONAL_PHASE_E_STATUS_KEYS:
        if key in row and row[key] is not None:
            out[key] = row[key]
    return out


def _build_secondary_record(
    secondary: str, k6_row: dict,
) -> tuple[Optional[dict], Optional[str]]:
    """Normalize one K=6 board row into the MVP v0 per-secondary shape.

    Returns ``(record, error_code)``. On malformed metrics, returns
    ``(None, "malformed_metrics")``. The ranking-level fields (rank
    position, sharpe used for sort) are added by the caller.
    """
    sharpe = _coerce_float(k6_row.get("Sharpe"))
    triggers = _coerce_int(k6_row.get("Trigs"))
    total_capture = _coerce_float(k6_row.get("Total %"))
    if sharpe is None or triggers is None or total_capture is None:
        return None, "malformed_metrics"

    wins_raw = k6_row.get("Wins")
    losses_raw = k6_row.get("Losses")
    wins = _coerce_int(wins_raw) if wins_raw is not None else None
    losses = _coerce_int(losses_raw) if losses_raw is not None else None
    if wins_raw is not None and wins is None:
        return None, "malformed_metrics"
    if losses_raw is not None and losses is None:
        return None, "malformed_metrics"

    win_pct = _coerce_float(k6_row.get("Win %"))
    stddev_pct = _coerce_float(k6_row.get("StdDev %"))
    p_value = _coerce_float(k6_row.get("p"))
    avg_capture_pct = _coerce_float(k6_row.get("Avg %"))

    members = _normalize_members(k6_row.get("Members"))

    record: dict[str, Any] = {
        "secondary": secondary,
        "k": 6,
        "members": members,
        "triggers": triggers,
        "wins": wins,
        "losses": losses,
        "win_pct": win_pct,
        "stddev_pct": stddev_pct,
        "sharpe": sharpe,
        "p_value": p_value,
        "avg_capture_pct": avg_capture_pct,
        "total_capture_pct": total_capture,
        "phase_e_status": _extract_phase_e_status(k6_row),
        "low_sample_warning": triggers < LOW_SAMPLE_THRESHOLD,
    }
    return record, None


def _make_issue(
    secondary: str, error_code: str, message: Optional[str] = None,
) -> dict:
    raw = "" if message is None else str(message)[:240]
    return {
        "secondary": secondary,
        "error_code": error_code,
        "message_sanitized": _scrub_embedded_absolute_paths(raw),
    }


def _process_secondary(
    secondary: str, run_root: Path,
) -> tuple[Optional[dict], Optional[dict]]:
    """Return ``(record, issue)`` for one requested secondary.

    Exactly one of ``record`` and ``issue`` is non-None on return.
    """
    sec_dir = run_root / secondary
    manifest_path = sec_dir / "secondary_manifest.json"
    rows_path = sec_dir / "board_rows_k=6.json"

    if not manifest_path.is_file():
        return None, _make_issue(secondary, "missing_secondary_manifest")
    try:
        _read_json(manifest_path)
    except Exception as exc:
        return None, _make_issue(
            secondary, "secondary_manifest_unreadable", repr(exc),
        )

    if not rows_path.is_file():
        return None, _make_issue(secondary, "missing_board_rows")
    try:
        rows = _read_json(rows_path)
    except Exception as exc:
        return None, _make_issue(
            secondary, "board_rows_unreadable", repr(exc),
        )

    k6_row = _select_k6_row(rows)
    if k6_row is None:
        return None, _make_issue(secondary, "missing_k6_row")

    record, err = _build_secondary_record(secondary, k6_row)
    if record is None:
        return None, _make_issue(secondary, err or "malformed_metrics")
    return record, None


# ---------------------------------------------------------------------------
# Top-level engine
# ---------------------------------------------------------------------------


def _resolve_run_root(
    selected_run_root_path: str, project_root: Path,
) -> Path:
    """Resolve a Phase E run-root reference against the project root.

    Per the MVP Ranking Contract: relative paths resolve against the
    project root / cwd, NOT against the selected_output.json parent
    directory.
    """
    p = Path(selected_run_root_path)
    if not p.is_absolute():
        p = (project_root / p)
    return p.resolve(strict=False)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _rank_records(records: list[dict]) -> list[dict]:
    """Sort by Sharpe descending; alphabetical secondary tie-breaker."""
    ranked = sorted(
        records,
        key=lambda r: (-float(r["sharpe"]), str(r["secondary"])),
    )
    for i, rec in enumerate(ranked, start=1):
        rec["rank"] = i
    return ranked


def build_mvp_ranking_v0(
    *,
    selected_output_path: Path,
    output_dir: Path,
    secondaries: Sequence[str],
    project_root: Optional[Path] = None,
) -> tuple[int, dict]:
    """Build the MVP v0 ranking artifact.

    Returns ``(exit_code, summary_dict)``. On success or partial
    success, the artifact is written to ``<output_dir>/mvp_ranking_v0.json``.
    On global failure or all-secondary failure, no artifact is written.

    ``summary_dict`` mirrors the artifact's top-level fields when an
    artifact is produced; otherwise it carries a ``status`` plus an
    ``error_code`` describing the global failure.
    """
    project_root = project_root or Path.cwd()
    selected_output_path = Path(selected_output_path)

    if not selected_output_path.is_file():
        return EXIT_GLOBAL_FAILURE, {
            "status": "refused",
            "error_code": "missing_selected_output",
        }

    try:
        selected_output = _read_json(selected_output_path)
    except Exception as exc:
        return EXIT_GLOBAL_FAILURE, {
            "status": "refused",
            "error_code": "selected_output_unreadable",
            "detail_sanitized": _scrub_embedded_absolute_paths(repr(exc)),
        }
    if not isinstance(selected_output, dict):
        return EXIT_GLOBAL_FAILURE, {
            "status": "refused",
            "error_code": "selected_output_unreadable",
        }

    selected_run_root_path = selected_output.get("selected_run_root_path")
    if not selected_run_root_path:
        return EXIT_GLOBAL_FAILURE, {
            "status": "refused",
            "error_code": "selected_output_missing_run_root_path",
        }

    run_root = _resolve_run_root(str(selected_run_root_path), project_root)
    if not run_root.is_dir():
        return EXIT_GLOBAL_FAILURE, {
            "status": "refused",
            "error_code": "selected_run_root_missing",
        }

    run_manifest_path = run_root / "run_manifest.json"
    if not run_manifest_path.is_file():
        return EXIT_GLOBAL_FAILURE, {
            "status": "refused",
            "error_code": "missing_run_manifest",
        }
    try:
        run_manifest = _read_json(run_manifest_path)
    except Exception as exc:
        return EXIT_GLOBAL_FAILURE, {
            "status": "refused",
            "error_code": "run_manifest_unreadable",
            "detail_sanitized": _scrub_embedded_absolute_paths(repr(exc)),
        }
    if not isinstance(run_manifest, dict):
        return EXIT_GLOBAL_FAILURE, {
            "status": "refused",
            "error_code": "run_manifest_unreadable",
        }

    requested = list(secondaries)
    records: list[dict] = []
    issues: list[dict] = []
    for sec in requested:
        record, issue = _process_secondary(sec, run_root)
        if record is not None:
            records.append(record)
        else:
            issues.append(issue)  # type: ignore[arg-type]

    if not records:
        return EXIT_ALL_SECONDARIES_FAILED, {
            "status": "all_secondaries_failed",
            "issues": [sanitize_for_json(i, project_root=project_root)
                       for i in issues],
        }

    ranked = _rank_records(records)
    ranking_status = "complete" if not issues else "partial"

    selected_run_id = selected_output.get("selected_run_id") or run_root.name
    orchestrator_invocation_id = run_manifest.get(
        "orchestrator_invocation_id"
    )
    trafficflow_run_status = run_manifest.get("run_status")

    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_iso(),
        "ranking_status": ranking_status,
        "trafficflow_run_root": path_for_output(
            str(run_root), project_root=project_root,
        ),
        "trafficflow_run_id": selected_run_id,
        "trafficflow_orchestrator_invocation_id": orchestrator_invocation_id,
        "trafficflow_run_status": trafficflow_run_status,
        "secondaries_requested": requested,
        "secondaries_ranked": [r["secondary"] for r in ranked],
        "per_secondary": ranked,
        "issues": issues,
    }
    safe_artifact = sanitize_for_json(artifact, project_root=project_root)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / ARTIFACT_FILENAME
    try:
        _atomic_write_json(artifact_path, safe_artifact)
    except Exception as exc:
        # Best-effort cleanup of any stray .tmp sibling.
        tmp = artifact_path.with_name(artifact_path.name + ".tmp")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return EXIT_GLOBAL_FAILURE, {
            "status": "refused",
            "error_code": "output_write_failed",
            "detail_sanitized": _scrub_embedded_absolute_paths(repr(exc)),
        }

    return EXIT_OK, safe_artifact


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_GLOBAL_FAILURE

    secondaries = _parse_secondaries(args.secondaries)
    exit_code, summary = build_mvp_ranking_v0(
        selected_output_path=Path(args.trafficflow_selected_output),
        output_dir=Path(args.output_dir),
        secondaries=secondaries,
    )

    safe_summary = sanitize_for_json(summary, project_root=Path.cwd())
    sys.stdout.write(json.dumps(safe_summary, indent=2, default=str) + "\n")
    sys.stdout.flush()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
