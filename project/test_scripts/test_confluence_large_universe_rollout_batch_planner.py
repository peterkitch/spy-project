"""Phase 6I-51 tests for the large-universe rollout
batch planner + board preview command manifest.

Pins:

  * Schema-version + batch taxonomy + authorization-class
    taxonomy + policy-basis tags are stable constants.
  * Each Phase 6I-50 ``recommended_next_action`` code maps
    to the correct Phase 6I-51 batch:
      - SPY already_board_ranked -> board_render_now.
      - daily-only / blocked_missing_inputs / manual_review
        -> blocked_or_manual_review.
      - write_partial_artifact -> partial writer candidate
        WITH ``--allow-partial-payload-plan`` AND
        ``requires_separate_operator_authorization=True``.
      - write_strict_artifact -> strict writer candidate
        (no ``--allow-partial-payload-plan``).
      - refresh_source_cache -> single per-ticker
        ``signal_engine_cache_refresher.py --ticker <T>``
        candidate (NOT a CSV / batch form).
      - rerun_stackbuilder -> StackBuilder candidate using
        ``--secondary <TICKER>`` (NOT ``--ticker``).
  * StackBuilder rerun candidates are
    ``blocked_by_policy_decision=True`` by default;
    ``--accept-proposed-stackbuilder-defaults`` flips this
    to ``False`` and tags ``policy_basis=
    proposed_defaults``.
  * Every generated command's ``command`` field starts
    with the pinned interpreter.
  * The module does NOT execute any candidate command --
    injection seam: no ``subprocess`` import at module
    top level (static guard).
  * ``--output`` and ``--emit-shell-script`` reject paths
    inside any production root.
  * Batch counts sum to the inspected count.
  * Unresolved policy questions are carried through from
    the input launch plan.
"""
from __future__ import annotations

import ast
import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_large_universe_rollout_batch_planner as rbp  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _row(
    *,
    ticker: str,
    action: str,
    artifact_status: str = "strict_full_60_cell",
    cache_status: str = "cache_ready",
    signal_library_status: str = "stable_ready",
    stackbuilder_status: str = "run_available",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "artifact_status": artifact_status,
        "cache_status": cache_status,
        "signal_library_status": signal_library_status,
        "stackbuilder_status": stackbuilder_status,
        "recommended_next_action": action,
    }


def _launch_plan(rows: list[dict[str, Any]]) -> dict[
    str, Any,
]:
    return {
        "schema_version": (
            "confluence_large_universe_launch_planner_v1"
        ),
        "universe_mode": "explicit_tickers",
        "target_tickers": [r["ticker"] for r in rows],
        "rows": rows,
        "counts": {
            "inspected_count": len(rows),
        },
        "stackbuilder_policy": {
            # All six launch-policy questions are now settled
            # at the launch planner; the unresolved list is
            # empty. The rollout planner's launch-authorization
            # gate is independent of this list and stays
            # fail-closed by default; settlement is not
            # authorization.
            "unresolved_policy_questions": [],
        },
    }


# ---------------------------------------------------------------------------
# 0. Schema + taxonomy stability
# ---------------------------------------------------------------------------


def test_schema_and_taxonomies_are_stable():
    assert (
        rbp.SCHEMA_VERSION
        == "confluence_large_universe_rollout_batch_planner_v1"
    )
    for batch in (
        rbp.BATCH_BOARD_RENDER_NOW,
        rbp.BATCH_PARTIAL_ARTIFACT_WRITE_CANDIDATES,
        rbp.BATCH_STRICT_ARTIFACT_WRITE_CANDIDATES,
        rbp.BATCH_SOURCE_REFRESH_CANDIDATES,
        rbp.BATCH_SIGNAL_LIBRARY_REBUILD_OR_PROMOTION_CANDIDATES,
        rbp.BATCH_STACKBUILDER_RERUN_CANDIDATES,
        rbp.BATCH_BLOCKED_OR_MANUAL_REVIEW,
    ):
        assert batch in rbp.ALL_BATCHES
    for auth in (
        rbp.AUTH_READ_ONLY,
        rbp.AUTH_SOURCE_CACHE_WRITE,
        rbp.AUTH_CONFLUENCE_ARTIFACT_WRITE,
        rbp.AUTH_SIGNAL_LIBRARY_PROMOTION_WRITE,
        rbp.AUTH_STACKBUILDER_WRITE,
        rbp.AUTH_MANUAL_REVIEW,
    ):
        assert auth in rbp.ALL_AUTH_CLASSES
    assert (
        rbp.PINNED_INTERPRETER
        == "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/"
        "envs/spyproject2/python.exe"
    )


# ---------------------------------------------------------------------------
# 1. SPY already_board_ranked -> board_render_now
# ---------------------------------------------------------------------------


def test_already_board_ranked_routes_to_board_render_now():
    plan = _launch_plan([
        _row(ticker="SPY", action="already_board_ranked"),
    ])
    rollout = rbp.build_rollout_batch_plan(plan)
    assert (
        rollout["batches"][
            rbp.BATCH_BOARD_RENDER_NOW
        ]["tickers"]
        == ["SPY"]
    )
    cmds = [
        c for c in rollout["command_manifest"]
        if c["ticker"] == "SPY"
    ]
    assert all(
        c["batch"] == rbp.BATCH_BOARD_RENDER_NOW
        for c in cmds
    )
    assert all(
        c["authorization_class"] == rbp.AUTH_READ_ONLY
        for c in cmds
    )
    assert all(
        c["requires_separate_operator_authorization"]
        is False for c in cmds
    )


# ---------------------------------------------------------------------------
# 2. daily-only / blocked_missing_inputs -> blocked_or_manual_review
# ---------------------------------------------------------------------------


def test_daily_only_blocked_routes_to_manual_review():
    plan = _launch_plan([
        _row(
            ticker="_GSPC",
            action="blocked_missing_inputs",
            artifact_status="daily_only",
            cache_status="cache_missing",
            signal_library_status="stable_missing",
            stackbuilder_status="run_missing",
        ),
    ])
    rollout = rbp.build_rollout_batch_plan(plan)
    assert (
        rollout["batches"][
            rbp.BATCH_BLOCKED_OR_MANUAL_REVIEW
        ]["tickers"]
        == ["_GSPC"]
    )
    cmds = [
        c for c in rollout["command_manifest"]
        if c["ticker"] == "_GSPC"
    ]
    assert len(cmds) == 1
    assert (
        cmds[0]["authorization_class"]
        == rbp.AUTH_MANUAL_REVIEW
    )
    assert cmds[0]["argv"] is None
    # The Phase 6I-50 row fields are carried through into
    # blocked_or_manual_review for the operator.
    blocked = rollout["blocked_or_manual_review"]
    assert blocked[0]["ticker"] == "_GSPC"
    assert blocked[0]["artifact_status"] == "daily_only"


# ---------------------------------------------------------------------------
# 3. write_partial_artifact -> partial writer candidate with
#    --allow-partial-payload-plan + separate-auth flag.
# ---------------------------------------------------------------------------


def test_partial_artifact_write_candidate_shape():
    plan = _launch_plan([
        _row(
            ticker="AAA",
            action="write_partial_artifact",
            artifact_status="artifact_missing",
        ),
    ])
    rollout = rbp.build_rollout_batch_plan(
        plan,
        invalid_members_json_path=(
            "some/dir/invalid_members.json"
        ),
    )
    cmds = [
        c for c in rollout["command_manifest"]
        if c["ticker"] == "AAA"
    ]
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd["batch"] == (
        rbp.BATCH_PARTIAL_ARTIFACT_WRITE_CANDIDATES
    )
    assert (
        cmd["authorization_class"]
        == rbp.AUTH_CONFLUENCE_ARTIFACT_WRITE
    )
    assert (
        cmd["requires_separate_operator_authorization"]
        is True
    )
    assert "--allow-partial-payload-plan" in cmd["argv"]
    assert "--write" in cmd["argv"]
    # @PATH form for the writer's --invalid-members-json
    # argument.
    assert (
        "@some/dir/invalid_members.json"
        in cmd["argv"]
    )
    # Per-writer ticker flag is --ticker (the writer's
    # actual CLI), not --secondary.
    assert "--ticker" in cmd["argv"]
    assert "AAA" in cmd["argv"]


# ---------------------------------------------------------------------------
# 4. write_strict_artifact -> strict writer candidate.
# ---------------------------------------------------------------------------


def test_strict_artifact_write_candidate_shape():
    plan = _launch_plan([
        _row(ticker="BBB", action="write_strict_artifact"),
    ])
    rollout = rbp.build_rollout_batch_plan(plan)
    cmds = [
        c for c in rollout["command_manifest"]
        if c["ticker"] == "BBB"
    ]
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd["batch"] == (
        rbp.BATCH_STRICT_ARTIFACT_WRITE_CANDIDATES
    )
    # No --allow-partial-payload-plan on strict path.
    assert (
        "--allow-partial-payload-plan"
        not in cmd["argv"]
    )
    assert "--write" in cmd["argv"]
    assert (
        cmd["authorization_class"]
        == rbp.AUTH_CONFLUENCE_ARTIFACT_WRITE
    )
    assert (
        cmd["requires_separate_operator_authorization"]
        is True
    )


# ---------------------------------------------------------------------------
# 5. refresh_source_cache -> per-ticker --ticker refresher
#    candidate (NOT CSV / batch form).
# ---------------------------------------------------------------------------


def test_source_refresh_is_per_ticker_not_csv():
    plan = _launch_plan([
        _row(
            ticker="CCC",
            action="refresh_source_cache",
            cache_status="cache_missing",
        ),
        _row(
            ticker="DDD",
            action="refresh_source_cache",
            cache_status="cache_missing",
        ),
    ])
    rollout = rbp.build_rollout_batch_plan(plan)
    refresh_cmds = [
        c for c in rollout["command_manifest"]
        if c["batch"] == rbp.BATCH_SOURCE_REFRESH_CANDIDATES
    ]
    # Per-ticker: one command per ticker.
    assert len(refresh_cmds) == 2
    for c in refresh_cmds:
        argv = c["argv"]
        assert "--ticker" in argv
        # The ticker appears as a separate argv token
        # (NOT a comma-separated string).
        tix_idx = argv.index("--ticker")
        assert argv[tix_idx + 1] in ("CCC", "DDD")
        # Defensive: no comma-separated multi-ticker
        # token leaked in (the Phase 6E-5 refresher does
        # not support a CSV form).
        assert "," not in argv[tix_idx + 1]
        assert (
            "signal_engine_cache_refresher.py" in argv
        )
        assert (
            c["authorization_class"]
            == rbp.AUTH_SOURCE_CACHE_WRITE
        )


# ---------------------------------------------------------------------------
# 6. rerun_stackbuilder uses --secondary <TICKER>, NOT
#    --ticker.
# ---------------------------------------------------------------------------


def test_stackbuilder_rerun_uses_secondary_not_ticker():
    plan = _launch_plan([
        _row(
            ticker="EEE", action="rerun_stackbuilder",
        ),
    ])
    rollout = rbp.build_rollout_batch_plan(
        plan, accept_proposed_stackbuilder_defaults=True,
    )
    sb_cmds = [
        c for c in rollout["command_manifest"]
        if c["batch"] == (
            rbp.BATCH_STACKBUILDER_RERUN_CANDIDATES
        )
    ]
    assert len(sb_cmds) == 1
    cmd = sb_cmds[0]
    argv = cmd["argv"]
    # Phase 6I-50 amendment-1: --secondary is the real
    # StackBuilder entry flag. --ticker would be wrong.
    assert "--secondary" in argv
    sec_idx = argv.index("--secondary")
    assert argv[sec_idx + 1] == "EEE"
    assert "--ticker" not in argv
    # Phase 6I-50 amendment-1: --combine-mode intersection
    # is now explicitly surfaced in the candidate.
    assert "--combine-mode" in argv
    cm_idx = argv.index("--combine-mode")
    assert argv[cm_idx + 1] == "intersection"
    # Proposed launch defaults pinned.
    for flag, val in (
        ("--top-n", "20"),
        ("--bottom-n", "20"),
        ("--max-k", "6"),
        ("--search", "beam"),
        ("--beam-width", "12"),
        ("--seed-by", "total_capture"),
        ("--min-trigger-days", "30"),
    ):
        assert flag in argv
        assert argv[argv.index(flag) + 1] == val
    # --both-modes is NOT auto-added (settled launch
    # policy is both_modes=False; the candidate command
    # therefore omits the --both-modes flag).
    assert "--both-modes" not in argv


# ---------------------------------------------------------------------------
# 7. StackBuilder rerun is blocked_by_policy_decision
#    unless --accept-proposed-stackbuilder-defaults set.
# ---------------------------------------------------------------------------


def test_stackbuilder_rerun_policy_gating():
    plan = _launch_plan([
        _row(
            ticker="FFF", action="rerun_stackbuilder",
        ),
    ])
    # Default: blocked.
    rollout_default = rbp.build_rollout_batch_plan(plan)
    sb_default = [
        c for c in rollout_default["command_manifest"]
        if c["batch"] == (
            rbp.BATCH_STACKBUILDER_RERUN_CANDIDATES
        )
    ][0]
    assert (
        sb_default["blocked_by_policy_decision"] is True
    )
    # After the five-question settlement PR the default-
    # blocked basis is launch_authorization_required
    # rather than unresolved_questions. The constant
    # POLICY_BASIS_UNRESOLVED_QUESTIONS is preserved for
    # backward symbol compat but is no longer the active
    # blocked basis.
    assert (
        sb_default["policy_basis"]
        == rbp.POLICY_BASIS_LAUNCH_AUTHORIZATION_REQUIRED
    )
    assert sb_default["operator_policy_required"] is True
    # With accept-proposed flag.
    rollout_accept = rbp.build_rollout_batch_plan(
        plan, accept_proposed_stackbuilder_defaults=True,
    )
    sb_accept = [
        c for c in rollout_accept["command_manifest"]
        if c["batch"] == (
            rbp.BATCH_STACKBUILDER_RERUN_CANDIDATES
        )
    ][0]
    assert (
        sb_accept["blocked_by_policy_decision"] is False
    )
    assert (
        sb_accept["policy_basis"]
        == rbp.POLICY_BASIS_PROPOSED_DEFAULTS
    )
    assert (
        sb_accept["operator_policy_required"] is False
    )


# ---------------------------------------------------------------------------
# 8. Every command string starts with the pinned
#    interpreter.
# ---------------------------------------------------------------------------


def test_every_command_string_uses_pinned_interpreter():
    plan = _launch_plan([
        _row(ticker="SPY", action="already_board_ranked"),
        _row(
            ticker="AAA",
            action="write_partial_artifact",
        ),
        _row(ticker="BBB", action="write_strict_artifact"),
        _row(
            ticker="CCC",
            action="refresh_source_cache",
        ),
        _row(
            ticker="DDD",
            action="rebuild_signal_libraries",
            signal_library_status="stable_missing",
        ),
        _row(
            ticker="EEE",
            action="promote_signal_libraries",
            signal_library_status="staged_possible",
        ),
        _row(
            ticker="FFF", action="rerun_stackbuilder",
        ),
    ])
    rollout = rbp.build_rollout_batch_plan(
        plan, accept_proposed_stackbuilder_defaults=True,
    )
    for cmd in rollout["command_manifest"]:
        if cmd["argv"] is None:
            # Manual / documentation-only records carry no
            # argv; their ``command`` is a # comment.
            assert cmd["command"].startswith(
                "# ",
            )
            continue
        assert cmd["argv"][0] == rbp.PINNED_INTERPRETER
        assert cmd["command"].startswith(
            rbp.PINNED_INTERPRETER,
        ) or cmd["command"].startswith(
            f'"{rbp.PINNED_INTERPRETER}"',
        )


# ---------------------------------------------------------------------------
# 9. Static guard: no subprocess (or other forbidden
#    top-level imports). No candidate command is ever
#    executed by this module.
# ---------------------------------------------------------------------------


_FORBIDDEN_TOP_LEVEL_IMPORTS = frozenset({
    "subprocess",
    "yfinance",
    "dash",
    "signal_engine_cache_refresher",
    "signal_library_stable_promotion_writer",
    "multiwindow_k_confluence_patch_writer",
    "confluence_pipeline_runner",
    "daily_board_automation_writer",
    "daily_board_automation_executor",
    "spymaster",
    "trafficflow",
    "stackbuilder",
    "onepass",
    "impactsearch",
    "confluence",
    "cross_ticker_confluence",
    "daily_signal_board",
})


def test_no_forbidden_top_level_imports():
    here = Path(__file__).resolve().parent.parent
    src = (
        here
        / "confluence_large_universe_rollout_batch_planner.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_level_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for n in node.names:
                top_level_names.add(
                    n.name.split(".")[0],
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level_names.add(
                    node.module.split(".")[0],
                )
    leaked = (
        top_level_names & _FORBIDDEN_TOP_LEVEL_IMPORTS
    )
    assert not leaked, (
        f"Forbidden top-level imports in rollout batch "
        f"planner: {sorted(leaked)}"
    )


# ---------------------------------------------------------------------------
# 10. --output / --emit-shell-script reject paths inside
#     any production root.
# ---------------------------------------------------------------------------


def test_output_path_guard_rejects_production_root_paths(
    tmp_path, capsys,
):
    # Build a minimal saved planner JSON so the CLI has a
    # valid input.
    planner_path = tmp_path / "planner.json"
    planner_path.write_text(
        json.dumps(_launch_plan([])),
        encoding="utf-8",
    )

    forbidden_outputs = [
        "cache/results/rollout.json",
        "cache\\status\\rollout.json",
        "output/research_artifacts/rollout.json",
        "output/stackbuilder/rollout.json",
        "signal_library/data/stable/rollout.json",
    ]
    for forbidden in forbidden_outputs:
        rc = rbp.main([
            "--planner-json", str(planner_path),
            "--output", forbidden,
        ])
        out = capsys.readouterr()
        assert rc == 2, (
            f"Expected rc=2 for production-root output "
            f"path {forbidden!r}; got rc={rc}"
        )
        assert (
            "output_path_inside_production_root"
            in out.err
        )

    # Same guard on --emit-shell-script.
    rc2 = rbp.main([
        "--planner-json", str(planner_path),
        "--emit-shell-script",
        "cache/results/rollout.sh",
    ])
    err2 = capsys.readouterr().err
    assert rc2 == 2
    assert (
        "shell_script_path_inside_production_root"
        in err2
    )


# ---------------------------------------------------------------------------
# 11. Batch counts are stable + sum to inspected count.
# ---------------------------------------------------------------------------


def test_batch_counts_sum_to_inspected_count():
    plan = _launch_plan([
        _row(ticker="SPY", action="already_board_ranked"),
        _row(
            ticker="AAA",
            action="write_partial_artifact",
        ),
        _row(ticker="BBB", action="write_strict_artifact"),
        _row(
            ticker="CCC",
            action="refresh_source_cache",
        ),
        _row(
            ticker="DDD",
            action="rebuild_signal_libraries",
            signal_library_status="stable_missing",
        ),
        _row(
            ticker="EEE",
            action="promote_signal_libraries",
            signal_library_status="staged_possible",
        ),
        _row(
            ticker="FFF", action="rerun_stackbuilder",
        ),
        _row(
            ticker="_GSPC",
            action="blocked_missing_inputs",
        ),
    ])
    rollout = rbp.build_rollout_batch_plan(plan)
    bs = rollout["batch_summary"]
    # 8 input rows -> 8 distributed across batches.
    total = sum(bs.values())
    assert total == 8
    assert bs[rbp.BATCH_BOARD_RENDER_NOW] == 1
    assert (
        bs[rbp.BATCH_PARTIAL_ARTIFACT_WRITE_CANDIDATES]
        == 1
    )
    assert (
        bs[rbp.BATCH_STRICT_ARTIFACT_WRITE_CANDIDATES]
        == 1
    )
    assert bs[rbp.BATCH_SOURCE_REFRESH_CANDIDATES] == 1
    # rebuild + promote collapse to one batch.
    assert (
        bs[
            rbp.BATCH_SIGNAL_LIBRARY_REBUILD_OR_PROMOTION_CANDIDATES
        ]
        == 2
    )
    assert (
        bs[rbp.BATCH_STACKBUILDER_RERUN_CANDIDATES] == 1
    )
    assert bs[rbp.BATCH_BLOCKED_OR_MANUAL_REVIEW] == 1
    assert (
        rollout["input_universe_summary"][
            "inspected_count"
        ]
        == 8
    )


# ---------------------------------------------------------------------------
# 12. Unresolved policy questions are carried through.
# ---------------------------------------------------------------------------


def test_unresolved_policy_questions_carry_through():
    plan = _launch_plan([
        _row(ticker="SPY", action="already_board_ranked"),
    ])
    rollout = rbp.build_rollout_batch_plan(plan)
    questions = rollout["unresolved_policy_questions"]
    assert isinstance(questions, list)
    # All six launch-policy questions are now settled at
    # the launch planner; the unresolved list carries
    # through verbatim from the input plan as an empty
    # list. Membership assertions are not applicable
    # because the list has no members; this test only
    # pins the verbatim-pass-through behavior at the
    # zero-question state.
    assert questions == []


# ---------------------------------------------------------------------------
# 12a. Fail-closed launch-authorization gate survives the unresolved-
#      policy count reaching zero. This is the load-bearing safety
#      property of the five-question settlement PR: settling policy
#      MUST NOT auto-authorize launch.
# ---------------------------------------------------------------------------


def test_gate_remains_default_blocked_at_zero_unresolved_questions():
    """Settling all six launch-policy questions empties the launch
    planner's unresolved-policy list. The rollout planner's launch-
    authorization gate is independent of that list and must remain
    fail-closed by default: blocked_by_policy_decision stays True on
    a fresh rollout plan unless the operator passes the explicit
    --accept-proposed-stackbuilder-defaults override. Candidate
    StackBuilder commands stay emitted as strings only, never
    executed."""
    # Establish the precondition the test is named for:
    # the launch planner reports zero unresolved questions.
    plan = _launch_plan([
        _row(ticker="FFF", action="rerun_stackbuilder"),
    ])
    assert plan["stackbuilder_policy"][
        "unresolved_policy_questions"
    ] == []
    # Default rollout: blocked.
    rollout_default = rbp.build_rollout_batch_plan(plan)
    sb_default = [
        c for c in rollout_default["command_manifest"]
        if c["batch"] == (
            rbp.BATCH_STACKBUILDER_RERUN_CANDIDATES
        )
    ][0]
    assert sb_default["blocked_by_policy_decision"] is True
    assert (
        sb_default["policy_basis"]
        == rbp.POLICY_BASIS_LAUNCH_AUTHORIZATION_REQUIRED
    )
    assert sb_default["operator_policy_required"] is True
    # Candidate command emitted as a string, not executed.
    assert isinstance(sb_default["command"], str)
    assert sb_default["command"].strip() != ""
    # Override still flips blocked off and retags the
    # basis as proposed_defaults.
    rollout_accept = rbp.build_rollout_batch_plan(
        plan, accept_proposed_stackbuilder_defaults=True,
    )
    sb_accept = [
        c for c in rollout_accept["command_manifest"]
        if c["batch"] == (
            rbp.BATCH_STACKBUILDER_RERUN_CANDIDATES
        )
    ][0]
    assert sb_accept["blocked_by_policy_decision"] is False
    assert (
        sb_accept["policy_basis"]
        == rbp.POLICY_BASIS_PROPOSED_DEFAULTS
    )
    assert sb_accept["operator_policy_required"] is False


def test_policy_basis_unresolved_questions_constant_still_defined():
    """POLICY_BASIS_UNRESOLVED_QUESTIONS is preserved as an exported
    symbol for backward compatibility (other consumers may have
    imported the name) even though it is no longer the active
    default-blocked basis."""
    assert hasattr(rbp, "POLICY_BASIS_UNRESOLVED_QUESTIONS")
    assert isinstance(
        rbp.POLICY_BASIS_UNRESOLVED_QUESTIONS, str,
    )
    assert (
        rbp.POLICY_BASIS_UNRESOLVED_QUESTIONS
        != rbp.POLICY_BASIS_LAUNCH_AUTHORIZATION_REQUIRED
    )


# ---------------------------------------------------------------------------
# 13. --planner-json CLI path round-trips a saved Phase
#     6I-50 evidence file (extra coverage; sanity-checks
#     the JSON-loader code path).
# ---------------------------------------------------------------------------


def test_cli_planner_json_round_trip(tmp_path, capsys):
    plan = _launch_plan([
        _row(ticker="SPY", action="already_board_ranked"),
        _row(
            ticker="_GSPC",
            action="blocked_missing_inputs",
            artifact_status="daily_only",
        ),
    ])
    planner_path = tmp_path / "phase_6i50_evidence.json"
    planner_path.write_text(
        json.dumps(plan), encoding="utf-8",
    )
    rc = rbp.main([
        "--planner-json", str(planner_path),
        "--accept-proposed-stackbuilder-defaults",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert (
        parsed["schema_version"]
        == rbp.SCHEMA_VERSION
    )
    assert (
        parsed["accept_proposed_stackbuilder_defaults"]
        is True
    )
    assert (
        parsed["batches"][rbp.BATCH_BOARD_RENDER_NOW][
            "tickers"
        ]
        == ["SPY"]
    )
    assert (
        parsed["batches"][
            rbp.BATCH_BLOCKED_OR_MANUAL_REVIEW
        ]["tickers"]
        == ["_GSPC"]
    )


# ---------------------------------------------------------------------------
# 14. --emit-shell-script writes a script with every
#     candidate command commented out.
# ---------------------------------------------------------------------------


def test_emit_shell_script_writes_commented_out_lines(
    tmp_path, capsys,
):
    plan = _launch_plan([
        _row(ticker="SPY", action="already_board_ranked"),
        _row(
            ticker="FFF", action="rerun_stackbuilder",
        ),
    ])
    planner_path = tmp_path / "plan.json"
    planner_path.write_text(
        json.dumps(plan), encoding="utf-8",
    )
    script_path = tmp_path / "rollout.sh"
    out_path = tmp_path / "rollout.json"
    rc = rbp.main([
        "--planner-json", str(planner_path),
        "--accept-proposed-stackbuilder-defaults",
        "--output", str(out_path),
        "--emit-shell-script", str(script_path),
    ])
    assert rc == 0
    body = script_path.read_text(encoding="utf-8")
    # Every command body line starts with "# ".
    lines = [
        ln for ln in body.splitlines()
        if rbp.PINNED_INTERPRETER in ln
    ]
    assert lines, (
        "expected at least one pinned-interpreter line "
        "in the shell-script body"
    )
    for ln in lines:
        assert ln.startswith("# "), (
            f"shell-script line not commented out: "
            f"{ln!r}"
        )


# ---------------------------------------------------------------------------
# Phase 6I-51 amendment-1 tests: corrected CLI shapes against
# the real argparse surfaces.
#
#   * confluence_static_board_renderer.py uses
#     --from-tickers (NOT --tickers) and does not expose
#     --signal-library-dir / --stackbuilder-root directly
#     (overlay-* flags carry those).
#   * confluence_website_export_package.py accepts
#     --tickers + --top-n + --artifact-root + --cache-dir
#     only; --signal-library-dir / --stackbuilder-root do
#     not exist there.
#   * signal_library_stable_promotion_writer.py requires
#     --tickers (CSV) + --staged-dir + --production-stable-
#     dir + --intervals + --write. Does NOT accept --ticker
#     or --signal-library-dir.
# ---------------------------------------------------------------------------


def test_static_board_render_uses_from_tickers_not_tickers():
    """The static-board-render candidate must use
    --from-tickers (the real CLI flag), NOT --tickers
    (which the original Phase 6I-51 commit used)."""
    plan = _launch_plan([
        _row(ticker="SPY", action="already_board_ranked"),
    ])
    rollout = rbp.build_rollout_batch_plan(
        plan,
        artifact_root="output/research_artifacts",
        cache_dir="cache/results",
        signal_library_dir="signal_library/data/stable",
        stackbuilder_root="output/stackbuilder",
    )
    cmds = [
        c for c in rollout["command_manifest"]
        if c["command_label"] == "static_board_render"
    ]
    assert len(cmds) == 1
    argv = cmds[0]["argv"]
    assert (
        "confluence_static_board_renderer.py" in argv
    )
    assert "--from-tickers" in argv
    # Hard regression guard: --tickers must NOT appear.
    assert "--tickers" not in argv
    # Overlay flags carry the signal-library / stackbuilder
    # roots (the renderer does NOT accept them directly).
    assert "--with-local-overlays" in argv
    assert "--overlay-signal-library-dir" in argv
    assert "--overlay-stackbuilder-root" in argv
    # --signal-library-dir / --stackbuilder-root must NOT
    # appear as direct flags (the renderer doesn't expose
    # them).
    assert "--signal-library-dir" not in argv
    assert "--stackbuilder-root" not in argv


def test_static_board_render_argv_parses_against_real_cli():
    """Sanity-check that the generated static-board-render
    argv is accepted by the real renderer's argparse
    surface. We deferred-import the renderer here (the
    rollout planner itself never imports it)."""
    import confluence_static_board_renderer as csbr

    plan = _launch_plan([
        _row(ticker="SPY", action="already_board_ranked"),
    ])
    rollout = rbp.build_rollout_batch_plan(
        plan,
        artifact_root="output/research_artifacts",
        cache_dir="cache/results",
        signal_library_dir="signal_library/data/stable",
        stackbuilder_root="output/stackbuilder",
    )
    cmd = next(
        c for c in rollout["command_manifest"]
        if c["command_label"] == "static_board_render"
    )
    # Drop the leading interpreter + script name; argparse
    # consumes only the flags that follow.
    parser = csbr._build_arg_parser()
    # SystemExit signals argparse rc != 0 (e.g. unknown
    # flag); a clean parse returns a Namespace.
    parsed = parser.parse_args(cmd["argv"][2:])
    assert parsed.from_tickers == "SPY"
    assert parsed.with_local_overlays is True


def test_website_export_package_only_supported_flags():
    """The website-export-package candidate must NOT
    include --signal-library-dir or --stackbuilder-root
    (those flags don't exist on that CLI)."""
    plan = _launch_plan([
        _row(ticker="SPY", action="already_board_ranked"),
    ])
    rollout = rbp.build_rollout_batch_plan(
        plan,
        artifact_root="output/research_artifacts",
        cache_dir="cache/results",
        signal_library_dir="signal_library/data/stable",
        stackbuilder_root="output/stackbuilder",
    )
    cmd = next(
        c for c in rollout["command_manifest"]
        if c["command_label"] == "website_export_package"
    )
    argv = cmd["argv"]
    assert "--tickers" in argv
    assert "--artifact-root" in argv
    assert "--cache-dir" in argv
    # Unsupported flags MUST be absent.
    assert "--signal-library-dir" not in argv
    assert "--stackbuilder-root" not in argv


def test_website_export_package_argv_parses_against_real_cli():
    """Sanity-check that the website-export-package argv
    parses against the real CLI."""
    import confluence_website_export_package as cwep

    plan = _launch_plan([
        _row(ticker="SPY", action="already_board_ranked"),
    ])
    rollout = rbp.build_rollout_batch_plan(
        plan,
        artifact_root="output/research_artifacts",
        cache_dir="cache/results",
        signal_library_dir="signal_library/data/stable",
        stackbuilder_root="output/stackbuilder",
    )
    cmd = next(
        c for c in rollout["command_manifest"]
        if c["command_label"] == "website_export_package"
    )
    parser = cwep._build_arg_parser()
    parsed = parser.parse_args(cmd["argv"][2:])
    assert parsed.tickers == "SPY"


def test_promotion_template_without_staged_dir_is_doc_only():
    """A ticker with signal_library_status=staged_possible
    BUT no --staged-dir-for-promotion supplied should
    emit a documentation-only template (argv=None) rather
    than an argv that would fail the writer's
    required=True argparse."""
    plan = _launch_plan([
        _row(
            ticker="AAA",
            action="promote_signal_libraries",
            signal_library_status="staged_possible",
        ),
    ])
    rollout = rbp.build_rollout_batch_plan(plan)
    cmd = next(
        c for c in rollout["command_manifest"]
        if c["batch"] == (
            rbp.BATCH_SIGNAL_LIBRARY_REBUILD_OR_PROMOTION_CANDIDATES
        )
    )
    assert cmd["argv"] is None
    assert (
        cmd["command_label"]
        == "signal_library_promotion_template"
    )
    assert "<STAGED_DIR>" in cmd["command"]
    # The template references the real writer flags.
    assert "--tickers AAA" in cmd["command"]
    assert "--staged-dir" in cmd["command"]
    assert "--production-stable-dir" in cmd["command"]
    assert "--intervals" in cmd["command"]
    # And does NOT reference the wrong --ticker /
    # --signal-library-dir flags.
    assert "--ticker " not in cmd["command"]
    # ``--signal-library-dir`` must not appear in the
    # template (the writer doesn't expose it; the
    # equivalent flag is ``--production-stable-dir``).
    assert "--signal-library-dir " not in cmd["command"]


def test_promotion_with_staged_dir_produces_executable_argv():
    """When --staged-dir-for-promotion is supplied, the
    candidate emits an executable argv with the correct
    flags."""
    plan = _launch_plan([
        _row(
            ticker="BBB",
            action="promote_signal_libraries",
            signal_library_status="staged_possible",
        ),
    ])
    rollout = rbp.build_rollout_batch_plan(
        plan,
        signal_library_dir=(
            "signal_library/data/stable"
        ),
        staged_dir_for_promotion=(
            "signal_library/data/staged_2026_05_15"
        ),
    )
    cmd = next(
        c for c in rollout["command_manifest"]
        if c["batch"] == (
            rbp.BATCH_SIGNAL_LIBRARY_REBUILD_OR_PROMOTION_CANDIDATES
        )
    )
    assert cmd["command_label"] == "signal_library_promotion"
    argv = cmd["argv"]
    assert argv is not None
    # --tickers (CSV form), --staged-dir, --production-stable-dir,
    # --intervals, --write.
    assert "--tickers" in argv
    tickers_idx = argv.index("--tickers")
    assert argv[tickers_idx + 1] == "BBB"
    assert "--staged-dir" in argv
    sd_idx = argv.index("--staged-dir")
    assert (
        argv[sd_idx + 1]
        == "signal_library/data/staged_2026_05_15"
    )
    assert "--production-stable-dir" in argv
    psd_idx = argv.index("--production-stable-dir")
    assert (
        argv[psd_idx + 1] == "signal_library/data/stable"
    )
    assert "--intervals" in argv
    iv_idx = argv.index("--intervals")
    assert argv[iv_idx + 1] == "1d,1wk,1mo,3mo,1y"
    assert "--write" in argv
    # And NO --ticker / --signal-library-dir.
    assert "--ticker" not in argv
    assert "--signal-library-dir" not in argv
    # Authorization tagging.
    assert (
        cmd["authorization_class"]
        == rbp.AUTH_SIGNAL_LIBRARY_PROMOTION_WRITE
    )
    assert (
        cmd["requires_separate_operator_authorization"]
        is True
    )


def test_promotion_argv_parses_against_real_cli():
    """Sanity-check that the executable promotion argv
    parses against the real writer's argparse surface."""
    import signal_library_stable_promotion_writer as slspw

    plan = _launch_plan([
        _row(
            ticker="CCC",
            action="promote_signal_libraries",
            signal_library_status="staged_possible",
        ),
    ])
    rollout = rbp.build_rollout_batch_plan(
        plan,
        signal_library_dir=(
            "signal_library/data/stable"
        ),
        staged_dir_for_promotion=(
            "signal_library/data/staged_2026_05_15"
        ),
    )
    cmd = next(
        c for c in rollout["command_manifest"]
        if c["command_label"] == "signal_library_promotion"
    )
    parser = slspw._build_arg_parser()
    parsed = parser.parse_args(cmd["argv"][2:])
    assert parsed.tickers == "CCC"
    assert (
        parsed.staged_dir
        == "signal_library/data/staged_2026_05_15"
    )
    assert (
        parsed.production_stable_dir
        == "signal_library/data/stable"
    )
    assert parsed.intervals == "1d,1wk,1mo,3mo,1y"
    assert parsed.write is True


def test_intervals_for_promotion_override():
    """--intervals-for-promotion should override the
    1d,1wk,1mo,3mo,1y default."""
    plan = _launch_plan([
        _row(
            ticker="DDD",
            action="promote_signal_libraries",
            signal_library_status="staged_possible",
        ),
    ])
    rollout = rbp.build_rollout_batch_plan(
        plan,
        staged_dir_for_promotion="staged_dir",
        intervals_for_promotion="1d,1wk",
    )
    cmd = next(
        c for c in rollout["command_manifest"]
        if c["command_label"] == "signal_library_promotion"
    )
    argv = cmd["argv"]
    iv_idx = argv.index("--intervals")
    assert argv[iv_idx + 1] == "1d,1wk"
