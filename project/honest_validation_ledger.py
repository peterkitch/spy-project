#!/usr/bin/env python3
"""
Phase 5C-3: Honest Validation Report Ledger.

Reads durable ``validation_contract_v1`` sidecars produced by the
5C-2 per-app integrations and emits a unified operator-facing
ledger (JSON + Markdown). The ledger preserves full strategy
visibility (BH survivors AND non-survivors, including
``empirical_not_run`` strategies), surfaces failed / partial /
unavailable / oos_skipped / in_sample_only runs, and reports
baseline aggregates + per-strategy baseline deltas per locked
5C-1 §§7-12.

Canonical source = ``project/output/validation/<run_id>/validation.json``.
ImpactSearch XLSX manifests and StackBuilder run_manifest.json
contain only summary fields and are NOT canonical for the ledger.
Spymaster + Confluence interactive-tier validations are in-memory
only and intentionally NOT represented in the durable ledger
(coverage_notes makes this explicit).

Standalone CLI:

    python project/honest_validation_ledger.py \\
        --validation-root project/output/validation \\
        --output-dir project/output/validation_ledger
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

# Make sure the project root is on sys.path when this module is run as a
# script (``python project/honest_validation_ledger.py``) rather than
# imported from inside the package.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from validation_engine import (  # noqa: E402
    validate_validation_contract_v1,
    compute_validation_artifact_hash,
)


_DEFAULT_VALIDATION_ROOT = _HERE / "output" / "validation"
_DEFAULT_OUTPUT_DIR = _HERE / "output" / "validation_ledger"
_LEDGER_VERSION = "validation_ledger_v1"
_INTERACTIVE_TIER_COVERAGE_NOTE = (
    "Spymaster optimization and Confluence multi-primary validation "
    "are interactive tier per locked 5C-1 Section 13.1: in-memory only, no "
    "JSON sidecar emission. They are NOT represented in this durable "
    "ledger. To include them in a future ledger, a follow-up PR would "
    "need to add opt-in durable persistence on those surfaces."
)


# ---------------------------------------------------------------------------
# Discovery + load
# ---------------------------------------------------------------------------


def discover_validation_sidecars(validation_root: Path) -> list:
    """Return sorted ``validation.json`` files under ``validation_root``.

    Returns an empty list when the root does not exist or contains no
    sidecars. Recursive descent so per-run subdirectories
    (``<run_id>/validation.json``) are picked up.
    """
    root = Path(validation_root)
    if not root.exists() or not root.is_dir():
        return []
    return sorted(root.rglob("validation.json"))


def load_validation_sidecar(sidecar_path: Path) -> dict:
    """Load + structurally validate a ``validation_contract_v1`` sidecar.

    Raises ``ValueError`` (with the sidecar path embedded) on JSON
    parse failure or schema-shape violation.
    """
    p = Path(sidecar_path)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            contract = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"failed to read validation sidecar {p}: "
            f"{type(exc).__name__}: {exc}"
        )
    if not isinstance(contract, dict):
        raise ValueError(
            f"validation sidecar {p} did not parse to a dict; got "
            f"{type(contract).__name__}"
        )
    try:
        validate_validation_contract_v1(contract)
    except AssertionError as exc:
        raise ValueError(
            f"validation sidecar {p} failed contract shape check: {exc}"
        )
    return contract


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(x):
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _mean_finite(values):
    finite = [float(v) for v in values if _safe_float(v) is not None]
    if not finite:
        return None
    return sum(finite) / len(finite)


def _format_float(x, *, digits=4):
    f = _safe_float(x)
    if f is None:
        return "n/a"
    return f"{f:.{digits}f}"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Ledger build
# ---------------------------------------------------------------------------


def _run_entry_from_contract(
    contract: Mapping[str, Any],
    *,
    sidecar_path: Path,
    sidecar_sha256: str,
) -> dict:
    agg = contract.get("baseline_aggregate") or {}
    return {
        "run_id": contract.get("run_id"),
        "producer_engine": contract.get("producer_engine"),
        "app_surface": contract.get("app_surface"),
        "validation_status": contract.get("validation_status"),
        "evaluation_time": contract.get("evaluation_time"),
        "data_available_through": contract.get("data_available_through"),
        "in_sample_window_start": contract.get("in_sample_window_start"),
        "in_sample_window_end": contract.get("in_sample_window_end"),
        "oos_window_start": contract.get("oos_window_start"),
        "oos_window_end": contract.get("oos_window_end"),
        "walk_forward_n_folds": contract.get("walk_forward_n_folds"),
        "baseline_method": contract.get("baseline_method"),
        "n_strategies_tested": int(contract.get("n_strategies_tested") or 0),
        "n_strategies_reported": int(contract.get("n_strategies_reported") or 0),
        "n_strategies_survived_empirical": int(
            contract.get("n_strategies_survived_empirical") or 0
        ),
        "multiple_comparisons_control_method": contract.get(
            "multiple_comparisons_control_method"
        ),
        "multiple_comparisons_control_alpha": _safe_float(
            contract.get("multiple_comparisons_control_alpha")
        ),
        "multiple_comparisons_supplementary": contract.get(
            "multiple_comparisons_supplementary"
        ),
        "n_permutations": int(contract.get("n_permutations") or 0),
        "n_bootstrap_samples": int(contract.get("n_bootstrap_samples") or 0),
        "borderline_tolerance_multiplier": _safe_float(
            contract.get("borderline_tolerance_multiplier")
        ),
        "survivorship_summary": dict(contract.get("survivorship_summary") or {}),
        "baseline_aggregate": dict(agg),
        "mean_baseline_sharpe": _safe_float(agg.get("mean_baseline_sharpe")),
        "issues": list(contract.get("issues") or []),
        "sidecar_path": str(sidecar_path),
        "sidecar_sha256": sidecar_sha256,
    }


def _strategy_rows_from_contract(
    contract: Mapping[str, Any],
    *,
    sidecar_path: Path,
    sidecar_sha256: str,
) -> list:
    rows = []
    run_id = contract.get("run_id")
    producer_engine = contract.get("producer_engine")
    app_surface = contract.get("app_surface")
    run_status = contract.get("validation_status")
    for s in contract.get("strategies") or []:
        delta = dict(s.get("aggregate_baseline_delta") or {})
        rows.append({
            "run_id": run_id,
            "producer_engine": producer_engine,
            "app_surface": app_surface,
            "strategy_id": s.get("strategy_id"),
            "strategy_label": s.get("strategy_label"),
            # Locked 5C-1 §11: full strategy visibility. The strategy
            # row carries the run-level status PLUS the strategy-level
            # empirical_validation_status; survivorship-only filtering
            # is forbidden.
            "validation_status": run_status,
            "parametric_p_value": _safe_float(s.get("parametric_p_value")),
            "bh_q_value": _safe_float(s.get("bh_q_value")),
            "bonferroni_p_value": _safe_float(s.get("bonferroni_p_value")),
            "empirical_p_value": _safe_float(s.get("empirical_p_value")),
            "empirical_validation_status": s.get("empirical_validation_status"),
            "bootstrap_sharpe_ci_lower": _safe_float(
                s.get("bootstrap_sharpe_ci_lower")
            ),
            "bootstrap_sharpe_ci_upper": _safe_float(
                s.get("bootstrap_sharpe_ci_upper")
            ),
            "aggregate_metrics": {
                "trigger_days": s.get("trigger_days"),
                "wins": s.get("wins"),
                "losses": s.get("losses"),
                "win_rate": _safe_float(s.get("win_rate")),
                "std_dev": _safe_float(s.get("std_dev")),
                "sharpe": _safe_float(s.get("sharpe")),
                "t_statistic": _safe_float(s.get("t_statistic")),
                "avg_daily_capture": _safe_float(s.get("avg_daily_capture")),
                "total_capture": _safe_float(s.get("total_capture")),
            },
            "aggregate_baseline_delta": delta,
            "mean_sharpe_delta": _safe_float(delta.get("mean_sharpe_delta")),
            "mean_return_delta": _safe_float(delta.get("mean_return_delta")),
            "sidecar_path": str(sidecar_path),
            "sidecar_sha256": sidecar_sha256,
        })
    return rows


def _build_app_summary(runs: list, strategy_rows: list) -> dict:
    """Aggregate runs + strategy rows by ``producer_engine``.

    Per-engine counts include status counts, N/K/empirical totals,
    empirical_not_run count derived from strategy rows, and finite
    means of baseline Sharpe / Sharpe delta / return delta.
    """
    engines: dict = {}
    for r in runs:
        eng = r.get("producer_engine") or "unknown"
        bucket = engines.setdefault(eng, {
            "runs": 0,
            "status_counts": {},
            "total_n_strategies_tested": 0,
            "total_n_strategies_reported": 0,
            "total_n_strategies_survived_empirical": 0,
            "total_empirical_not_run": 0,
            "_baseline_sharpes": [],
            "_sharpe_deltas": [],
            "_return_deltas": [],
        })
        bucket["runs"] += 1
        s = r.get("validation_status") or "unknown"
        bucket["status_counts"][s] = bucket["status_counts"].get(s, 0) + 1
        bucket["total_n_strategies_tested"] += int(
            r.get("n_strategies_tested") or 0
        )
        bucket["total_n_strategies_reported"] += int(
            r.get("n_strategies_reported") or 0
        )
        bucket["total_n_strategies_survived_empirical"] += int(
            r.get("n_strategies_survived_empirical") or 0
        )
        mb = _safe_float(r.get("mean_baseline_sharpe"))
        if mb is not None:
            bucket["_baseline_sharpes"].append(mb)
    for sr in strategy_rows:
        eng = sr.get("producer_engine") or "unknown"
        bucket = engines.setdefault(eng, {
            "runs": 0,
            "status_counts": {},
            "total_n_strategies_tested": 0,
            "total_n_strategies_reported": 0,
            "total_n_strategies_survived_empirical": 0,
            "total_empirical_not_run": 0,
            "_baseline_sharpes": [],
            "_sharpe_deltas": [],
            "_return_deltas": [],
        })
        if sr.get("empirical_validation_status") == "empirical_not_run":
            bucket["total_empirical_not_run"] += 1
        sd = _safe_float(sr.get("mean_sharpe_delta"))
        rd = _safe_float(sr.get("mean_return_delta"))
        if sd is not None:
            bucket["_sharpe_deltas"].append(sd)
        if rd is not None:
            bucket["_return_deltas"].append(rd)
    out: dict = {}
    for eng, bucket in engines.items():
        out[eng] = {
            "runs": bucket["runs"],
            "status_counts": dict(bucket["status_counts"]),
            "total_n_strategies_tested": bucket["total_n_strategies_tested"],
            "total_n_strategies_reported": bucket["total_n_strategies_reported"],
            "total_n_strategies_survived_empirical": (
                bucket["total_n_strategies_survived_empirical"]
            ),
            "total_empirical_not_run": bucket["total_empirical_not_run"],
            "mean_baseline_sharpe": _mean_finite(bucket["_baseline_sharpes"]),
            "mean_sharpe_delta": _mean_finite(bucket["_sharpe_deltas"]),
            "mean_return_delta": _mean_finite(bucket["_return_deltas"]),
        }
    return out


def build_honest_validation_ledger(
    validation_root: Path,
    *,
    generated_at: Optional[str] = None,
    strict: bool = False,
) -> dict:
    """Build a ``validation_ledger_v1`` dict from durable sidecars.

    ``strict=False``: invalid / unreadable sidecars are recorded in
    ``rejected_sources`` and generation continues.

    ``strict=True``: invalid / unreadable sidecars raise ``ValueError``.

    An empty / missing ``validation_root`` returns a ledger with zero
    runs and the interactive-tier coverage note (Spymaster +
    Confluence are not durably persisted).
    """
    root = Path(validation_root)
    sidecars = discover_validation_sidecars(root)
    runs = []
    strategy_rows = []
    rejected_sources = []
    for sidecar_path in sidecars:
        try:
            contract = load_validation_sidecar(sidecar_path)
        except ValueError as exc:
            if strict:
                raise
            rejected_sources.append({
                "path": str(sidecar_path),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })
            continue
        try:
            sha = compute_validation_artifact_hash(sidecar_path)
        except Exception as exc:
            if strict:
                raise ValueError(
                    f"failed to hash validation sidecar {sidecar_path}: "
                    f"{type(exc).__name__}: {exc}"
                )
            rejected_sources.append({
                "path": str(sidecar_path),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })
            continue
        runs.append(_run_entry_from_contract(
            contract, sidecar_path=sidecar_path, sidecar_sha256=sha,
        ))
        strategy_rows.extend(_strategy_rows_from_contract(
            contract, sidecar_path=sidecar_path, sidecar_sha256=sha,
        ))

    status_summary: dict = {}
    for r in runs:
        s = r.get("validation_status") or "unknown"
        status_summary[s] = status_summary.get(s, 0) + 1
    app_summary = _build_app_summary(runs, strategy_rows)

    return {
        "ledger_version": _LEDGER_VERSION,
        "generated_at": generated_at or _now_utc_iso(),
        "validation_root": str(root),
        "sidecar_count": len(sidecars),
        "accepted_count": len(runs),
        "rejected_count": len(rejected_sources),
        "coverage_notes": [_INTERACTIVE_TIER_COVERAGE_NOTE],
        "status_summary": status_summary,
        "app_summary": app_summary,
        "runs": runs,
        "strategy_rows": strategy_rows,
        "rejected_sources": rejected_sources,
    }


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


_NON_VALID_STATUSES = (
    "failed", "partial", "unavailable", "oos_skipped", "in_sample_only",
)


def _md_cell(value) -> str:
    """Escape a single cell value for safe inclusion in a Markdown
    pipe table.

    - ``None`` becomes empty string.
    - Embedded newlines become ``<br>`` so a cell never breaks the
      row.
    - Pipes are escaped as ``\\|`` so cross-app strategy IDs that
      embed ``|`` (notably the Confluence
      ``CONFLUENCE({secondary}|{interval}|{members})`` format) do not
      corrupt the surrounding table layout.
    """
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
    text = text.replace("|", "\\|")
    return text


def _md_table(headers: list, rows: list) -> str:
    """Render an ASCII Markdown table with pipe-safe cell escaping."""
    if not headers:
        return ""
    body = ["| " + " | ".join(_md_cell(h) for h in headers) + " |"]
    body.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        body.append("| " + " | ".join(_md_cell(c) for c in row) + " |")
    return "\n".join(body)


def render_honest_validation_ledger_markdown(ledger: Mapping[str, Any]) -> str:
    """Render the ledger dict as a human-readable Markdown report."""
    lines = []
    lines.append("# Honest Validation Report Ledger")
    lines.append("")
    lines.append(f"- Ledger version: {ledger.get('ledger_version')}")
    lines.append(f"- Generated at: {ledger.get('generated_at')}")
    lines.append(f"- Validation root: {ledger.get('validation_root')}")
    lines.append(f"- Sidecars discovered: {ledger.get('sidecar_count', 0)}")
    lines.append(f"- Accepted: {ledger.get('accepted_count', 0)}")
    lines.append(f"- Rejected: {ledger.get('rejected_count', 0)}")
    lines.append("")

    lines.append("## Coverage Notes")
    lines.append("")
    for note in ledger.get("coverage_notes") or []:
        lines.append(f"- {note}")
    lines.append("")

    lines.append("## Status Summary")
    lines.append("")
    status_summary = ledger.get("status_summary") or {}
    if status_summary:
        rows = [
            [status, count] for status, count in sorted(status_summary.items())
        ]
        lines.append(_md_table(["Status", "Run count"], rows))
    else:
        lines.append("_No runs recorded._")
    lines.append("")

    lines.append("## App Summary")
    lines.append("")
    app_summary = ledger.get("app_summary") or {}
    if app_summary:
        headers = [
            "App", "Runs", "Statuses", "N tested", "K reported",
            "Empirically validated", "empirical_not_run",
            "Mean baseline Sharpe", "Mean Sharpe delta",
            "Mean return delta",
        ]
        rows = []
        for eng in sorted(app_summary.keys()):
            entry = app_summary[eng]
            sc = entry.get("status_counts") or {}
            sc_text = (
                ", ".join(f"{k}={v}" for k, v in sorted(sc.items()))
                if sc else "n/a"
            )
            rows.append([
                eng,
                entry.get("runs", 0),
                sc_text,
                entry.get("total_n_strategies_tested", 0),
                entry.get("total_n_strategies_reported", 0),
                entry.get("total_n_strategies_survived_empirical", 0),
                entry.get("total_empirical_not_run", 0),
                _format_float(entry.get("mean_baseline_sharpe"), digits=4),
                _format_float(entry.get("mean_sharpe_delta"), digits=4),
                _format_float(entry.get("mean_return_delta"), digits=4),
            ])
        lines.append(_md_table(headers, rows))
    else:
        lines.append("_No per-app entries._")
    lines.append("")

    lines.append("## Run Ledger")
    lines.append("")
    lines.append(
        "Multiple-comparisons primary control: BH (Benjamini-Hochberg). "
        "Supplementary disclosure: Bonferroni."
    )
    lines.append("")
    runs = ledger.get("runs") or []
    if runs:
        headers = [
            "run_id", "App", "Surface", "Status", "Folds",
            "N tested", "K reported", "Empirical survived",
            "BH alpha", "Supplementary",
            "Mean baseline Sharpe", "Sidecar SHA",
        ]
        rows = []
        for r in runs:
            sha = r.get("sidecar_sha256") or ""
            rows.append([
                r.get("run_id") or "",
                r.get("producer_engine") or "",
                r.get("app_surface") or "",
                r.get("validation_status") or "",
                r.get("walk_forward_n_folds"),
                r.get("n_strategies_tested", 0),
                r.get("n_strategies_reported", 0),
                r.get("n_strategies_survived_empirical", 0),
                _format_float(r.get("multiple_comparisons_control_alpha"), digits=4),
                r.get("multiple_comparisons_supplementary") or "",
                _format_float(r.get("mean_baseline_sharpe"), digits=4),
                sha[:12] if isinstance(sha, str) else "",
            ])
        lines.append(_md_table(headers, rows))
    else:
        lines.append("_No runs recorded._")
    lines.append("")

    lines.append("## Failed / Non-Valid Runs")
    lines.append("")
    bad_runs = [
        r for r in runs
        if (r.get("validation_status") or "") in _NON_VALID_STATUSES
    ]
    if bad_runs:
        for r in bad_runs:
            lines.append(
                f"- `{r.get('run_id')}` ({r.get('producer_engine')}/"
                f"{r.get('app_surface')}): "
                f"status={r.get('validation_status')}"
            )
            for issue in r.get("issues") or []:
                lines.append(f"  - {issue}")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Strategy Detail")
    lines.append("")
    lines.append(
        "All strategies from every accepted contract are listed. BH "
        "non-survivors and `empirical_not_run` strategies are NOT "
        "filtered out (locked 5C-1 Section 11 full visibility)."
    )
    lines.append("")
    strategy_rows = ledger.get("strategy_rows") or []
    if strategy_rows:
        headers = [
            "strategy_id", "App", "run_id", "BH q", "Bonferroni p",
            "Empirical status", "Empirical p",
            "Mean Sharpe delta", "Mean return delta",
        ]
        rows = []
        for s in strategy_rows:
            rows.append([
                s.get("strategy_id") or "",
                s.get("producer_engine") or "",
                s.get("run_id") or "",
                _format_float(s.get("bh_q_value"), digits=4),
                _format_float(s.get("bonferroni_p_value"), digits=4),
                s.get("empirical_validation_status") or "",
                _format_float(s.get("empirical_p_value"), digits=4),
                _format_float(s.get("mean_sharpe_delta"), digits=4),
                _format_float(s.get("mean_return_delta"), digits=4),
            ])
        lines.append(_md_table(headers, rows))
    else:
        lines.append("_No strategy rows recorded._")
    lines.append("")

    rejected = ledger.get("rejected_sources") or []
    if rejected:
        lines.append("## Rejected Sources")
        lines.append("")
        for entry in rejected:
            lines.append(
                f"- `{entry.get('path')}`: "
                f"{entry.get('error_type')}: {entry.get('error_message')}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def write_honest_validation_ledger(
    ledger: Mapping[str, Any],
    *,
    output_json: Path,
    output_markdown: Path,
):
    """Write the ledger as JSON + Markdown. Returns ``(json_path, md_path)``.

    Parent directories are created if missing. JSON is pretty-printed
    with ``sort_keys=True`` so re-runs produce diff-stable output.
    """
    output_json = Path(output_json)
    output_markdown = Path(output_markdown)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2, sort_keys=True, default=str)
    md_text = render_honest_validation_ledger_markdown(ledger)
    with open(output_markdown, "w", encoding="utf-8") as fh:
        fh.write(md_text)
    return output_json, output_markdown


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="honest_validation_ledger",
        description=(
            "Build the Phase 5C-3 Honest Validation Report Ledger from "
            "durable validation_contract_v1 sidecars."
        ),
    )
    parser.add_argument(
        "--validation-root",
        default=str(_DEFAULT_VALIDATION_ROOT),
        help=(
            "directory under which validation.json sidecars live "
            f"(default: {_DEFAULT_VALIDATION_ROOT})"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help=(
            "directory to write honest_validation_ledger.{json,md} "
            f"(default: {_DEFAULT_OUTPUT_DIR})"
        ),
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="explicit JSON output path (overrides --output-dir)",
    )
    parser.add_argument(
        "--output-markdown",
        default=None,
        help="explicit Markdown output path (overrides --output-dir)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail on any unreadable / invalid sidecar (default: record + continue)",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    validation_root = Path(args.validation_root)
    out_json = (
        Path(args.output_json)
        if args.output_json
        else Path(args.output_dir) / "honest_validation_ledger.json"
    )
    out_md = (
        Path(args.output_markdown)
        if args.output_markdown
        else Path(args.output_dir) / "honest_validation_ledger.md"
    )
    ledger = build_honest_validation_ledger(
        validation_root, strict=bool(args.strict),
    )
    json_path, md_path = write_honest_validation_ledger(
        ledger, output_json=out_json, output_markdown=out_md,
    )
    print(
        "[5C-3] honest validation ledger: "
        f"accepted={ledger.get('accepted_count', 0)} "
        f"rejected={ledger.get('rejected_count', 0)} "
        f"json={json_path} md={md_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
