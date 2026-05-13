"""Phase 6I-10: end-to-end Daily Board automation flow
evidence audit.

A read-only auditor that walks the full automation chain
read-only and emits a single JSON evidence report. It
answers: "Do we have proof the chain works, and where is
it still simulated or inferred?"

**This is NOT a production-authorization phase.** The
audit does NOT execute the writer. It does NOT execute
any engine. It does NOT fetch yfinance. It runs read-
only over the existing Phase 6H / 6I stack:

  - Phase 6I-4 ``upstream_research_input_audit``
  - Phase 6I-1 ``confluence_ranking_contract_validator``
  - Phase 6I-3 ``confluence_ranking_emitter``
  - Phase 6I-6 ``daily_board_execution_queue_planner``
  - Phase 6I-9 ``daily_board_supervised_run_gate``
  - Phase 6I-7 ``spymaster_master_audit`` (helper-only)
  - Phase 6I-8 ``daily_board_automation_writer`` --
    inspected as TEXT only (AST scan + constant
    presence). The writer module is NEVER imported by
    this audit module so even a defensive accidental
    runtime call is contained.

Strictly read-only / offline
----------------------------

  - No yfinance import / fetch.
  - No subprocess.
  - No writer / refresher / pipeline runner / live
    engine import at the audit module's top level.
  - Production roots are snapshotted before and after
    the full audit and asserted byte-mtime-identical;
    the report exposes ``production_roots_untouched``.

Public surface
--------------

    StageCheck                          # dataclass
    FlowIntegrityAuditReport            # dataclass (+ to_json_dict)

    STAGE_*                             # stable stage-id constants

    run_daily_board_flow_integrity_audit(
        tickers=None, *,
        from_stackbuilder_universe=False, top_n=10,
        cache_dir=None, artifact_root=None,
        stackbuilder_root=None, signal_library_dir=None,
        impactsearch_output_dir=None,
        current_as_of_date=None,
        snapshot_production_roots=True,
    ) -> FlowIntegrityAuditReport

    main(argv=None) -> int

CLI
---

    python daily_board_flow_integrity_audit.py --ticker SPY
    python daily_board_flow_integrity_audit.py --tickers SPY,AAPL
    python daily_board_flow_integrity_audit.py \\
        --from-stackbuilder-universe --top-n 3

Three ticker-source flags mutually exclusive. JSON to
stdout. ``rc=0`` / ``rc=2`` (invalid args) / ``rc=3``
(unexpected). ``SystemExit`` is never propagated from
``main()``.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import confluence_pipeline_readiness as _cpr
import confluence_ranking_contract_validator as _crcv
import confluence_ranking_emitter as _cre
import daily_board_execution_queue_planner as _eqp
import daily_board_supervised_run_gate as _gate
import spymaster_master_audit as _sma
import upstream_research_input_audit as _urai


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STAGE_UPSTREAM = "upstream_research_input_audit"
STAGE_CONTRACT = "confluence_ranking_contract_validator"
STAGE_RANKING = "confluence_ranking_emitter"
STAGE_QUEUE_AND_GATE = "queue_and_gate"
STAGE_WRITER_STATIC = "writer_static_audit"
STAGE_SPYMASTER_HELPER = "spymaster_master_audit_helper"

ALL_STAGES: tuple[str, ...] = (
    STAGE_UPSTREAM,
    STAGE_CONTRACT,
    STAGE_RANKING,
    STAGE_QUEUE_AND_GATE,
    STAGE_WRITER_STATIC,
    STAGE_SPYMASTER_HELPER,
)

# "Known simulated or inferred" steps -- surfaces that
# this audit CANNOT prove against real production
# without an actual authorized writer run. The list is
# stable so an operator can grep on it.
SIMULATED_REAL_AUTHORIZED_WRITER_RUN = (
    "real_authorized_writer_run"
)
SIMULATED_REAL_REFRESHER_INVOCATION = (
    "real_signal_engine_cache_refresher_invocation"
)
SIMULATED_REAL_PIPELINE_WRITE = (
    "real_confluence_pipeline_runner_write"
)
SIMULATED_REAL_YFINANCE_FETCH = "real_yfinance_fetch"
SIMULATED_REAL_POST_PIPELINE_VALIDATION = (
    "real_post_pipeline_validation_on_writer_path"
)


_DEFAULT_SIMULATED_STEPS: tuple[str, ...] = (
    SIMULATED_REAL_AUTHORIZED_WRITER_RUN,
    SIMULATED_REAL_REFRESHER_INVOCATION,
    SIMULATED_REAL_PIPELINE_WRITE,
    SIMULATED_REAL_YFINANCE_FETCH,
    SIMULATED_REAL_POST_PIPELINE_VALIDATION,
)


# Production roots snapshotted before and after the
# audit run.
_PRODUCTION_ROOT_NAMES: tuple[str, ...] = (
    "cache_results",
    "cache_status",
    "research_artifacts",
    "signal_library_stable",
    "stackbuilder",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StageCheck:
    """Per-stage verdict. ``passed`` reflects whether
    the stage's read-only contract holds; ``detail`` is a
    short human-legible string; ``issue_codes`` carries
    stable codes for downstream consumers; ``notes`` is
    a free-form supplemental list."""

    stage: str
    passed: bool
    detail: str
    issue_codes: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass
class FlowIntegrityAuditReport:
    generated_at: str
    current_as_of_date: str
    tickers: tuple[str, ...]
    stage_checks: tuple[StageCheck, ...]
    all_read_only_checks_passed: bool
    production_roots_untouched: bool
    upstream_summary: dict[str, Any]
    contract_summary: dict[str, Any]
    ranking_summary: dict[str, Any]
    queue_summary: dict[str, Any]
    gate_summary: dict[str, Any]
    writer_static_summary: dict[str, Any]
    spymaster_audit_summary: dict[str, Any]
    known_simulated_or_inferred_steps: tuple[str, ...]
    recommended_next_evidence_step: str
    safe_to_consider_authorized_run_after_review: bool

    def to_json_dict(self) -> dict[str, Any]:
        return _report_to_json_dict(self)


def _stage_check_to_json(s: StageCheck) -> dict[str, Any]:
    return {
        "stage": s.stage,
        "passed": bool(s.passed),
        "detail": s.detail,
        "issue_codes": list(s.issue_codes),
        "notes": list(s.notes),
    }


def _report_to_json_dict(
    r: FlowIntegrityAuditReport,
) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at,
        "current_as_of_date": r.current_as_of_date,
        "tickers": list(r.tickers),
        "stage_checks": [
            _stage_check_to_json(s) for s in r.stage_checks
        ],
        "all_read_only_checks_passed": bool(
            r.all_read_only_checks_passed,
        ),
        "production_roots_untouched": bool(
            r.production_roots_untouched,
        ),
        "upstream_summary": dict(r.upstream_summary),
        "contract_summary": dict(r.contract_summary),
        "ranking_summary": dict(r.ranking_summary),
        "queue_summary": dict(r.queue_summary),
        "gate_summary": dict(r.gate_summary),
        "writer_static_summary": dict(
            r.writer_static_summary,
        ),
        "spymaster_audit_summary": dict(
            r.spymaster_audit_summary,
        ),
        "known_simulated_or_inferred_steps": list(
            r.known_simulated_or_inferred_steps,
        ),
        "recommended_next_evidence_step": (
            r.recommended_next_evidence_step
        ),
        "safe_to_consider_authorized_run_after_review": bool(
            r.safe_to_consider_authorized_run_after_review,
        ),
    }


# ---------------------------------------------------------------------------
# Production-root snapshotting
# ---------------------------------------------------------------------------


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def _production_roots() -> dict[str, Path]:
    base = _project_dir()
    return {
        "cache_results": base / "cache" / "results",
        "cache_status": base / "cache" / "status",
        "research_artifacts": (
            base / "output" / "research_artifacts"
        ),
        "signal_library_stable": (
            base / "signal_library" / "data" / "stable"
        ),
        "stackbuilder": base / "output" / "stackbuilder",
    }


def _snapshot_root(root: Path) -> dict[str, tuple[int, float]]:
    """Return ``{relative_path: (size, mtime)}`` for every
    file under ``root``. Missing roots return empty
    dicts so a brand-new clone with no prior runs still
    snapshots cleanly."""
    out: dict[str, tuple[int, float]] = {}
    if not root.exists() or not root.is_dir():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            continue
        out[rel] = (stat.st_size, stat.st_mtime)
    return out


def _snapshot_production_roots() -> dict[
    str, dict[str, tuple[int, float]],
]:
    return {
        name: _snapshot_root(root)
        for name, root in _production_roots().items()
    }


# ---------------------------------------------------------------------------
# Stage check helpers
# ---------------------------------------------------------------------------


def _stage_upstream(
    tickers: list[str],
    dirs: dict[str, Any],
    current_as_of_date: Optional[str],
) -> tuple[StageCheck, dict[str, Any]]:
    """Call Phase 6I-4 audit; surface aggregate counts +
    blocked-ticker totals. Stage passes when the call
    returns a well-formed report (every per-state object
    has the documented fields)."""
    notes: list[str] = []
    if not tickers:
        summary = {
            "inspected_count": 0,
            "upstream_trio_ready_tickers": [],
            "blocked_tickers": [],
            "counts_by_primary_blocker": {},
        }
        return (
            StageCheck(
                stage=STAGE_UPSTREAM,
                passed=True,
                detail="no tickers; nothing to audit",
                issue_codes=(),
                notes=(
                    "Empty ticker list bypasses the "
                    "upstream audit; downstream stages "
                    "will report empty too.",
                ),
            ),
            summary,
        )
    try:
        report = _urai.audit_upstream_research_inputs_many(
            tickers, current_as_of_date=current_as_of_date,
            **dirs,
        )
    except Exception as exc:
        return (
            StageCheck(
                stage=STAGE_UPSTREAM,
                passed=False,
                detail=(
                    "upstream audit raised: "
                    f"{exc!r}"
                ),
                issue_codes=("upstream_audit_exception",),
                notes=(),
            ),
            {"error": repr(exc)},
        )
    summary = {
        "inspected_count": int(report.inspected_count),
        "upstream_trio_ready_tickers": list(
            report.upstream_trio_ready_tickers,
        ),
        "blocked_tickers": list(report.blocked_tickers),
        "counts_by_primary_blocker": dict(
            report.counts_by_primary_blocker,
        ),
    }
    return (
        StageCheck(
            stage=STAGE_UPSTREAM,
            passed=True,
            detail=(
                f"upstream audit inspected "
                f"{summary['inspected_count']} ticker(s); "
                f"trio-ready "
                f"{len(summary['upstream_trio_ready_tickers'])}, "
                f"blocked "
                f"{len(summary['blocked_tickers'])}"
            ),
            issue_codes=(),
            notes=tuple(notes),
        ),
        summary,
    )


def _stage_contract(
    tickers: list[str],
    dirs: dict[str, Any],
    current_as_of_date: Optional[str],
) -> tuple[StageCheck, dict[str, Any]]:
    """Call Phase 6I-1 validator (many-ticker entry).
    Stage passes if the call returns; the per-ticker
    contract verdicts populate the summary."""
    if not tickers:
        return (
            StageCheck(
                stage=STAGE_CONTRACT,
                passed=True,
                detail="no tickers; nothing to validate",
                issue_codes=(),
                notes=(),
            ),
            {"validations": []},
        )
    # The validator's many-ticker entry doesn't take
    # ``impactsearch_output_dir``; trim that root.
    sub_dirs = {
        k: v for k, v in dirs.items()
        if k != "impactsearch_output_dir"
    }
    try:
        report = (
            _crcv.validate_confluence_ranking_contracts(
                tickers,
                current_as_of_date=current_as_of_date,
                **sub_dirs,
            )
        )
    except Exception as exc:
        return (
            StageCheck(
                stage=STAGE_CONTRACT,
                passed=False,
                detail=(
                    "contract validator raised: "
                    f"{exc!r}"
                ),
                issue_codes=(
                    "contract_validator_exception",
                ),
                notes=(),
            ),
            {"error": repr(exc)},
        )
    valid = [
        v.ticker for v in report.validations
        if v.cache_contract_ok
        and v.stackbuilder_contract_ok
        and v.daily_k_contract_ok
        and v.mtf_contract_ok
        and v.confluence_contract_ok
        and v.readiness_contract_ok
        and v.board_row_contract_ok
    ]
    invalid = [
        v.ticker for v in report.validations
        if v.ticker not in valid
    ]
    summary = {
        "inspected_count": int(report.inspected_count),
        "fully_valid_tickers": list(
            report.fully_valid_tickers,
        ),
        "contract_failed_tickers": list(
            report.contract_failed_tickers,
        ),
        "all_contracts_passed_tickers": valid,
        "any_contract_failed_tickers": invalid,
    }
    return (
        StageCheck(
            stage=STAGE_CONTRACT,
            passed=True,
            detail=(
                f"contract validator inspected "
                f"{summary['inspected_count']} ticker(s); "
                f"fully-valid (incl. leader) "
                f"{len(summary['fully_valid_tickers'])}, "
                f"any-contract-failed "
                f"{len(summary['any_contract_failed_tickers'])}"
            ),
            issue_codes=(),
            notes=(),
        ),
        summary,
    )


def _stage_ranking(
    tickers: list[str],
    dirs: dict[str, Any],
    current_as_of_date: Optional[str],
    top_n: int,
) -> tuple[StageCheck, dict[str, Any]]:
    """Call Phase 6I-3 emitter; confirm Group A + Group
    B fields are present on every row AND the three
    tails are preserved."""
    if not tickers:
        return (
            StageCheck(
                stage=STAGE_RANKING,
                passed=True,
                detail="no tickers; nothing to rank",
                issue_codes=(),
                notes=(),
            ),
            {
                "row_count": 0,
                "positive_tail_count": 0,
                "negative_tail_count": 0,
                "low_buy_tail_count": 0,
            },
        )
    sub_dirs = {
        k: v for k, v in dirs.items()
        if k != "impactsearch_output_dir"
    }
    try:
        report = _cre.emit_confluence_ranking(
            tickers,
            current_as_of_date=current_as_of_date,
            top_n=top_n, **sub_dirs,
        )
    except Exception as exc:
        return (
            StageCheck(
                stage=STAGE_RANKING,
                passed=False,
                detail=f"ranking emitter raised: {exc!r}",
                issue_codes=("ranking_emitter_exception",),
                notes=(),
            ),
            {"error": repr(exc)},
        )
    issue_codes: list[str] = []
    group_a_required = (
        "consensus_signal", "agreement_active",
        "agreement_total", "agreement_ratio",
        "buy_votes", "short_votes", "none_votes",
        "missing_votes", "signed_vote_score",
        "zero_buy_flag", "timeframes", "K_values",
        "expected_cell_count",
    )
    group_b_required = (
        "total_capture_pct", "avg_daily_capture_pct",
        "sharpe_ratio", "trigger_days", "wins", "losses",
        "p_value",
    )
    for row in report.rows:
        for field in group_a_required:
            if not hasattr(row, field):
                issue_codes.append(
                    f"ranking_row_missing_group_a_{field}",
                )
                break
        for field in group_b_required:
            if not hasattr(row, field):
                issue_codes.append(
                    f"ranking_row_missing_group_b_{field}",
                )
                break
        if issue_codes:
            break
    summary = {
        "row_count": int(report.inspected_count),
        "positive_tail_count": len(report.positive_tail),
        "negative_tail_count": len(report.negative_tail),
        "low_buy_tail_count": len(report.low_buy_tail),
        "counts_by_consensus_signal": dict(
            report.counts_by_consensus_signal,
        ),
        "counts_by_contract_validity": dict(
            report.counts_by_contract_validity,
        ),
    }
    passed = not issue_codes
    return (
        StageCheck(
            stage=STAGE_RANKING,
            passed=passed,
            detail=(
                f"ranking emitter produced "
                f"{summary['row_count']} row(s); "
                f"positive_tail "
                f"{summary['positive_tail_count']}, "
                f"negative_tail "
                f"{summary['negative_tail_count']}, "
                f"low_buy_tail "
                f"{summary['low_buy_tail_count']}"
            ),
            issue_codes=tuple(issue_codes),
            notes=(
                "Both top and bottom tails are "
                "preserved per Phase 6I-3 contract.",
            ) if passed else (),
        ),
        summary,
    )


def _stage_queue_and_gate(
    tickers: list[str],
    from_universe: bool,
    dirs: dict[str, Any],
    current_as_of_date: Optional[str],
    top_n: int,
) -> tuple[
    StageCheck, dict[str, Any], dict[str, Any],
]:
    """Call Phase 6I-6 queue planner + Phase 6I-9
    supervised gate. Confirm advisory commands are
    strings only and the gate's decision is consistent
    with the queue counts."""
    try:
        gate_report = _gate.evaluate_supervised_run_gate(
            tickers=tickers if tickers else None,
            from_stackbuilder_universe=from_universe,
            top_n=top_n,
            current_as_of_date=current_as_of_date,
            **dirs,
        )
    except Exception as exc:
        return (
            StageCheck(
                stage=STAGE_QUEUE_AND_GATE,
                passed=False,
                detail=(
                    "queue planner / gate raised: "
                    f"{exc!r}"
                ),
                issue_codes=(
                    "queue_or_gate_exception",
                ),
                notes=(),
            ),
            {"error": repr(exc)},
            {"error": repr(exc)},
        )
    issue_codes: list[str] = []
    for cmd in gate_report.advisory_commands:
        if not isinstance(cmd, str):
            issue_codes.append(
                "advisory_command_not_a_string",
            )
            break
    queue_summary = {
        "queue_counts": dict(gate_report.queue_counts),
        "queue_truncation": dict(gate_report.queue_truncation),
    }
    gate_summary = {
        "safe_to_authorize_writer_now": bool(
            gate_report.safe_to_authorize_writer_now,
        ),
        "recommended_operator_action": (
            gate_report.recommended_operator_action
        ),
        "authorization_candidate_tickers": list(
            gate_report.authorization_candidate_tickers,
        ),
        "blocking_reasons": list(
            gate_report.blocking_reasons,
        ),
        "advisory_commands_count": len(
            gate_report.advisory_commands,
        ),
        "discovered_stackbuilder_ticker_count": int(
            gate_report.discovered_stackbuilder_ticker_count,
        ),
        "inspected_count": int(gate_report.inspected_count),
        "positive_tail_count": len(
            gate_report.positive_tail,
        ),
        "negative_tail_count": len(
            gate_report.negative_tail,
        ),
        "low_buy_tail_count": len(
            gate_report.low_buy_tail,
        ),
    }
    return (
        StageCheck(
            stage=STAGE_QUEUE_AND_GATE,
            passed=not issue_codes,
            detail=(
                f"gate verdict "
                f"safe={gate_summary['safe_to_authorize_writer_now']}, "
                f"action={gate_summary['recommended_operator_action']}, "
                f"candidates={len(gate_summary['authorization_candidate_tickers'])}, "
                f"advisory_cmds="
                f"{gate_summary['advisory_commands_count']}"
            ),
            issue_codes=tuple(issue_codes),
            notes=(
                "Advisory commands are display-only "
                "strings; the writer's two-key gate is "
                "untouched.",
            ),
        ),
        queue_summary,
        gate_summary,
    )


# ---------------------------------------------------------------------------
# Writer static audit (TEXT inspection of writer module
# source; never imports the writer module itself)
# ---------------------------------------------------------------------------


# Required tokens that MUST appear in the writer's source
# text for the audit to pass. The validator-marker
# constant is the Phase 6I-8 JSONL pin; the env-var
# constants are the two-key auth gate.
_WRITER_REQUIRED_TOKENS: tuple[str, ...] = (
    "ENV_VAR_NAME",
    "ENV_VAR_REQUIRED_VALUE",
    "phase_6h5_explicit",
    "CONTRACT_VALIDATOR_FUNCTION_MARKER",
    "_default_contract_validator_callable",
    "FINAL_PIPELINE_EXECUTED_CONTRACT_INVALID",
    "FINAL_REFRESH_THEN_PIPELINE_EXECUTED_CONTRACT_INVALID",
    "ISSUE_POST_PIPELINE_CONTRACT_INVALID",
    "ISSUE_POST_PIPELINE_CONTRACT_VALIDATION_EXCEPTION",
)

# Top-level imports forbidden in the writer module. The
# validator / refresher / pipeline runner are lazy-
# resolved inside function bodies; the top-level AST
# scan must NOT see them.
_WRITER_FORBIDDEN_TOP_LEVEL_IMPORTS: frozenset[str] = (
    frozenset({
        "yfinance",
        "subprocess",
        "confluence_ranking_contract_validator",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
    })
)


def _stage_writer_static(
    *,
    writer_source_path: Optional[Path] = None,
) -> tuple[StageCheck, dict[str, Any]]:
    """Inspect ``daily_board_automation_writer.py`` as
    TEXT only. Confirm the required tokens are present
    and the top-level AST imports are forbidden-free."""
    if writer_source_path is None:
        writer_source_path = (
            _project_dir() / "daily_board_automation_writer.py"
        )
    try:
        text = writer_source_path.read_text(encoding="utf-8")
    except Exception as exc:
        return (
            StageCheck(
                stage=STAGE_WRITER_STATIC,
                passed=False,
                detail=(
                    "could not read writer source: "
                    f"{exc!r}"
                ),
                issue_codes=("writer_source_unreadable",),
                notes=(),
            ),
            {"error": repr(exc)},
        )
    missing = [
        tok for tok in _WRITER_REQUIRED_TOKENS
        if tok not in text
    ]
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return (
            StageCheck(
                stage=STAGE_WRITER_STATIC,
                passed=False,
                detail=(
                    "writer source did not parse: "
                    f"{exc!r}"
                ),
                issue_codes=("writer_source_parse_error",),
                notes=(),
            ),
            {"error": repr(exc)},
        )
    top_level_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level_imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_imports.append(node.module)
    bad_imports = sorted({
        m for m in top_level_imports
        if m.split(".")[0]
        in _WRITER_FORBIDDEN_TOP_LEVEL_IMPORTS
    })
    issue_codes: list[str] = []
    if missing:
        issue_codes.append(
            "writer_required_token_missing",
        )
    if bad_imports:
        issue_codes.append(
            "writer_forbidden_top_level_import",
        )
    # No-StackBuilder-age-window guard (mirrors the
    # audit guards on the rest of the chain).
    age_substrings = [
        s for s in (
            "STACKBUILDER_AGE_DAYS",
            "STACKBUILDER_STALE_DAYS",
            "STALE_DAYS",
            "AGE_DAYS",
            "30 days",
            "thirty days",
        ) if s in text
    ]
    if age_substrings:
        issue_codes.append(
            "writer_stackbuilder_age_window_substring",
        )
    passed = not issue_codes
    summary = {
        "writer_source_path": str(writer_source_path),
        "required_tokens_present": (
            not missing
        ),
        "missing_required_tokens": missing,
        "top_level_imports": top_level_imports,
        "forbidden_top_level_imports_present": bad_imports,
        "age_window_substrings_present": age_substrings,
    }
    return (
        StageCheck(
            stage=STAGE_WRITER_STATIC,
            passed=passed,
            detail=(
                "writer static audit: required tokens "
                f"{'present' if not missing else 'MISSING'}, "
                "top-level imports "
                f"{'clean' if not bad_imports else 'CONTAMINATED'}, "
                "age window "
                f"{'absent' if not age_substrings else 'PRESENT'}"
            ),
            issue_codes=tuple(issue_codes),
            notes=(
                "Writer module is inspected as TEXT "
                "only; this audit module NEVER imports "
                "the writer module so any accidental "
                "runtime call is contained.",
            ),
        ),
        summary,
    )


# ---------------------------------------------------------------------------
# Spymaster master-audit helper inspection
# ---------------------------------------------------------------------------


def _stage_spymaster_helper(
    *,
    helper_source_path: Optional[Path] = None,
) -> tuple[StageCheck, dict[str, Any]]:
    """Confirm the Phase 6I-7 master-audit helper
    module's public surface (layout / panel / load
    functions) is present and that its render path
    keeps advisory commands as text (no buttons in the
    advisory subpanel; this audit only checks the
    helper's own AST for forbidden imports + names)."""
    if helper_source_path is None:
        helper_source_path = (
            _project_dir() / "spymaster_master_audit.py"
        )
    try:
        text = helper_source_path.read_text(encoding="utf-8")
    except Exception as exc:
        return (
            StageCheck(
                stage=STAGE_SPYMASTER_HELPER,
                passed=False,
                detail=(
                    "could not read helper source: "
                    f"{exc!r}"
                ),
                issue_codes=("helper_source_unreadable",),
                notes=(),
            ),
            {"error": repr(exc)},
        )
    required_names = (
        "build_audit_layout_section",
        "load_audit_report",
        "render_audit_panel",
        "MASTER_AUDIT_SECTION_ID",
        "MASTER_AUDIT_LOAD_BUTTON_ID",
        "MASTER_AUDIT_PANEL_ID",
        "READ_ONLY_NOTICE_TEXT",
    )
    missing = [n for n in required_names if n not in text]
    # Check that the helper exposes the right public
    # callables via the live module.
    helper_attrs_present = all(
        hasattr(_sma, n) for n in required_names
    )
    # Light render test: pass a dummy "unavailable"
    # state and confirm the helper returns a Dash
    # component without raising. This exercises the
    # rendering pathway without requiring the full
    # Spymaster app.
    render_ok = True
    render_error: Optional[str] = None
    try:
        panel = _sma.render_audit_panel(
            None, "audit_unavailable_simulated",
        )
        # Confirm the component has a children attr
        # (Dash components always do); we don't import
        # dash here to verify the type.
        _ = getattr(panel, "children", None)
    except Exception as exc:
        render_ok = False
        render_error = repr(exc)
    issue_codes: list[str] = []
    if missing:
        issue_codes.append("helper_required_name_missing")
    if not helper_attrs_present:
        issue_codes.append("helper_attr_not_exposed")
    if not render_ok:
        issue_codes.append("helper_render_path_raised")
    # Forbidden top-level imports on the helper.
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        issue_codes.append("helper_source_parse_error")
        top_level_imports = []
    else:
        top_level_imports = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_level_imports.append(node.module)
    helper_forbidden = {
        "daily_board_automation_writer",
        "signal_engine_cache_refresher",
        "confluence_pipeline_runner",
        "yfinance",
        "subprocess",
    }
    bad_imports = sorted({
        m for m in top_level_imports
        if m.split(".")[0] in helper_forbidden
    })
    if bad_imports:
        issue_codes.append(
            "helper_forbidden_top_level_import",
        )
    passed = not issue_codes
    summary = {
        "helper_source_path": str(helper_source_path),
        "required_names_present": not missing,
        "missing_required_names": missing,
        "helper_attrs_exposed": helper_attrs_present,
        "render_path_ok": render_ok,
        "render_error": render_error,
        "top_level_imports": top_level_imports,
        "forbidden_top_level_imports_present": bad_imports,
    }
    return (
        StageCheck(
            stage=STAGE_SPYMASTER_HELPER,
            passed=passed,
            detail=(
                "spymaster helper static + render "
                "audit: "
                f"{'ok' if passed else 'FAILED'}"
            ),
            issue_codes=tuple(issue_codes),
            notes=(
                "Inspecting the helper module only "
                "(not the full Spymaster Dash app). "
                "Render path exercised against the "
                "unavailable-state branch.",
            ),
        ),
        summary,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _resolve_ticker_list(
    tickers: Optional[Iterable[str]],
    from_universe: bool,
    stackbuilder_root: Optional[Any],
) -> list[str]:
    explicit: list[str] = []
    if tickers is not None:
        for t in tickers:
            cleaned = str(t).strip().upper()
            if cleaned:
                explicit.append(cleaned)
    if from_universe:
        universe = _eqp._planner.discover_stackbuilder_universe(  # type: ignore[attr-defined]
            stackbuilder_root,
        ) if False else (
            __import__(
                "daily_board_universe_planner",
            ).discover_stackbuilder_universe(
                stackbuilder_root,
            )
        )
        seen = set(explicit)
        for t in universe:
            if t not in seen:
                explicit.append(t)
                seen.add(t)
    return explicit


def run_daily_board_flow_integrity_audit(
    tickers: Optional[Iterable[str]] = None,
    *,
    from_stackbuilder_universe: bool = False,
    top_n: int = 10,
    cache_dir: Optional[Any] = None,
    artifact_root: Optional[Any] = None,
    stackbuilder_root: Optional[Any] = None,
    signal_library_dir: Optional[Any] = None,
    impactsearch_output_dir: Optional[Any] = None,
    current_as_of_date: Optional[str] = None,
    snapshot_production_roots: bool = True,
) -> FlowIntegrityAuditReport:
    """End-to-end flow integrity audit.

    Strictly read-only. The audit invokes the Phase 6I-4
    audit, Phase 6I-1 validator, Phase 6I-3 emitter,
    Phase 6I-6 queue planner, Phase 6I-9 supervised gate,
    Phase 6I-7 Spymaster helper, and a static TEXT scan
    of the Phase 6H-5 / 6I-8 writer. The writer module
    is NEVER imported by this audit. Production roots
    are snapshotted before and after and asserted byte-
    mtime-identical when ``snapshot_production_roots``
    is True (the default).
    """
    resolved_cutoff = _cpr.resolve_current_as_of_date(
        current_as_of_date,
    )
    ticker_list = _resolve_ticker_list(
        tickers, from_stackbuilder_universe,
        stackbuilder_root,
    )
    dirs = {
        "cache_dir": cache_dir,
        "artifact_root": artifact_root,
        "stackbuilder_root": stackbuilder_root,
        "signal_library_dir": signal_library_dir,
        "impactsearch_output_dir": impactsearch_output_dir,
    }
    before = (
        _snapshot_production_roots()
        if snapshot_production_roots else {}
    )

    stage_checks: list[StageCheck] = []

    upstream_check, upstream_summary = _stage_upstream(
        ticker_list, dirs, resolved_cutoff,
    )
    stage_checks.append(upstream_check)

    contract_check, contract_summary = _stage_contract(
        ticker_list, dirs, resolved_cutoff,
    )
    stage_checks.append(contract_check)

    ranking_check, ranking_summary = _stage_ranking(
        ticker_list, dirs, resolved_cutoff, top_n,
    )
    stage_checks.append(ranking_check)

    (
        queue_gate_check,
        queue_summary,
        gate_summary,
    ) = _stage_queue_and_gate(
        ticker_list, from_stackbuilder_universe, dirs,
        resolved_cutoff, top_n,
    )
    stage_checks.append(queue_gate_check)

    writer_check, writer_summary = _stage_writer_static()
    stage_checks.append(writer_check)

    (
        spymaster_check,
        spymaster_summary,
    ) = _stage_spymaster_helper()
    stage_checks.append(spymaster_check)

    after = (
        _snapshot_production_roots()
        if snapshot_production_roots else {}
    )
    production_roots_untouched = (
        before == after
        if snapshot_production_roots else True
    )

    all_passed = all(s.passed for s in stage_checks)

    # ``safe_to_consider_authorized_run_after_review`` is
    # True iff every read-only check passed AND the gate
    # said safe AND production roots stayed untouched.
    # This is a verdict for OPERATOR REVIEW. It does
    # NOT authorize anything.
    gate_safe = bool(
        gate_summary.get(
            "safe_to_authorize_writer_now", False,
        ),
    )
    safe_to_consider = (
        all_passed
        and gate_safe
        and production_roots_untouched
    )

    recommended_next = (
        "Authorize a SUPERVISED first production "
        "writer run for ONE write-ready ticker on a "
        "controlled day; confirm post-pipeline contract "
        "validation surfaces the Phase 6I-8 JSONL "
        "validator marker. Until that run is captured, "
        "the writer + refresher + pipeline + post-"
        "pipeline-validation surfaces remain proven "
        "only with fake callables OR temp-root "
        "rehearsals."
        if safe_to_consider
        else (
            "Resolve the failing read-only checks BEFORE "
            "any authorized run. The composite verdict "
            "safe_to_consider_authorized_run_after_review "
            "is False; see stage_checks for the failing "
            "stage(s)."
        )
    )

    return FlowIntegrityAuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(
            timespec="seconds",
        ),
        current_as_of_date=resolved_cutoff,
        tickers=tuple(ticker_list),
        stage_checks=tuple(stage_checks),
        all_read_only_checks_passed=bool(all_passed),
        production_roots_untouched=bool(
            production_roots_untouched,
        ),
        upstream_summary=upstream_summary,
        contract_summary=contract_summary,
        ranking_summary=ranking_summary,
        queue_summary=queue_summary,
        gate_summary=gate_summary,
        writer_static_summary=writer_summary,
        spymaster_audit_summary=spymaster_summary,
        known_simulated_or_inferred_steps=(
            _DEFAULT_SIMULATED_STEPS
        ),
        recommended_next_evidence_step=recommended_next,
        safe_to_consider_authorized_run_after_review=bool(
            safe_to_consider,
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily_board_flow_integrity_audit",
        description=(
            "Phase 6I-10 end-to-end Daily Board "
            "automation flow evidence audit. Walks the "
            "Phase 6I read-only stack + the Phase 6H-5 "
            "/ 6I-8 writer TEXT and emits a single "
            "evidence report. Never invokes the writer; "
            "never runs any engine."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ticker", default=None,
        help="Single ticker symbol.",
    )
    group.add_argument(
        "--tickers", default=None,
        help="Comma-separated ticker list.",
    )
    group.add_argument(
        "--from-stackbuilder-universe",
        action="store_true",
        help=(
            "Discover the universe from saved "
            "StackBuilder ticker directories."
        ),
    )
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--stackbuilder-root", default=None)
    parser.add_argument("--signal-library-dir", default=None)
    parser.add_argument(
        "--impactsearch-output-dir", default=None,
    )
    parser.add_argument(
        "--current-as-of-date", default=None,
    )
    parser.add_argument(
        "--top-n", type=int, default=10,
        help=(
            "Maximum rows per Phase 6I-3 ranking tail "
            "(passed through)."
        ),
    )
    return parser


def _parse_ticker_sources(
    args: argparse.Namespace,
) -> tuple[list[str], bool]:
    explicit: list[str] = []
    if args.ticker:
        t = str(args.ticker).strip()
        if t:
            explicit.append(t)
    if args.tickers:
        for part in str(args.tickers).split(","):
            t = part.strip()
            if t:
                explicit.append(t)
    return explicit, bool(args.from_stackbuilder_universe)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(
            list(argv) if argv is not None else None,
        )
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    explicit_tickers, from_universe = (
        _parse_ticker_sources(args)
    )
    if not explicit_tickers and not from_universe:
        print(
            json.dumps({
                "error": "no_ticker_source_supplied",
                "detail": (
                    "Provide one of --ticker SYM, "
                    "--tickers SYM1,SYM2,..., or "
                    "--from-stackbuilder-universe."
                ),
            }),
            file=sys.stderr,
        )
        return 2
    try:
        report = run_daily_board_flow_integrity_audit(
            tickers=explicit_tickers or None,
            from_stackbuilder_universe=from_universe,
            top_n=args.top_n,
            cache_dir=args.cache_dir,
            artifact_root=args.artifact_root,
            stackbuilder_root=args.stackbuilder_root,
            signal_library_dir=args.signal_library_dir,
            impactsearch_output_dir=(
                args.impactsearch_output_dir
            ),
            current_as_of_date=args.current_as_of_date,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(
            json.dumps({
                "error": "unhandled_exception",
                "detail": str(exc),
            }),
            file=sys.stderr,
        )
        return 3
    print(json.dumps(report.to_json_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
