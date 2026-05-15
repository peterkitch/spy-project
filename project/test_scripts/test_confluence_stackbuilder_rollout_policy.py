"""Phase 6I-52 tests for the locked StackBuilder rollout
policy + first seed-universe manifest.

Pins:

  * Schema-version + policy name + policy version are
    stable constants.
  * The six locked policy decisions are pinned EXACTLY
    (both_modes=False, combine_mode='intersection',
    seed_by=optimize_by='total_capture', member-universe
    size 12, rerun cadence 'manual_supervised',
    invalid-member rotation
    'partial_effective_members_with_warning').
  * Every generated StackBuilder command uses
    ``--secondary <TICKER>`` (NOT ``--ticker``), includes
    ``--combine-mode intersection`` + ``--seed-by
    total_capture`` + ``--optimize-by total_capture``,
    and does NOT include ``--both-modes``.
  * Every command's ``command`` field starts with the
    pinned interpreter.
  * The seed universe is deduped + uppercased + stripped
    (the committed tuple intentionally includes a
    duplicate to pin the normalizer behaviour).
  * The manifest count equals the deduped ticker count.
  * Each command record carries the locked taxonomy:
    ``authorization_class='stackbuilder_write'``,
    ``requires_separate_operator_authorization=True``,
    ``policy_basis='phase_6i_52_locked_policy'``,
    ``blocked_by_policy_decision=False``.
  * The generated argv parses cleanly against the real
    ``stackbuilder.parse_args`` argparse surface.
  * Static guard: no forbidden top-level imports (no
    subprocess / yfinance / dash / writer modules / engine
    modules). The module does NOT execute any candidate
    command.
  * ``--output`` rejects paths inside any production root.
"""
from __future__ import annotations

import ast
import io
import json
import sys
from pathlib import Path
from typing import Any


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


import confluence_stackbuilder_rollout_policy as srp  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Schema-version + policy-name + policy-version stability
# ---------------------------------------------------------------------------


def test_schema_and_policy_constants_are_stable():
    assert (
        srp.SCHEMA_VERSION
        == "confluence_stackbuilder_rollout_policy_v1"
    )
    assert srp.POLICY_NAME == "phase_6i_52_locked_policy"
    assert srp.POLICY_VERSION == "v1"
    assert srp.POLICY_BASIS == srp.POLICY_NAME
    assert srp.PINNED_INTERPRETER == (
        "C:/Users/sport/AppData/Local/NVIDIA/MiniConda/"
        "envs/spyproject2/python.exe"
    )


# ---------------------------------------------------------------------------
# 2. The six locked policy decisions are pinned EXACTLY.
# ---------------------------------------------------------------------------


def test_six_locked_policy_decisions_are_pinned_exactly():
    assert srp.POLICY_BOTH_MODES is False
    assert srp.POLICY_COMBINE_MODE == "intersection"
    assert srp.POLICY_SEED_BY == "total_capture"
    assert srp.POLICY_OPTIMIZE_BY == "total_capture"
    assert srp.POLICY_MEMBER_UNIVERSE_SIZE == 12
    assert srp.POLICY_RERUN_CADENCE == "manual_supervised"
    assert (
        srp.POLICY_INVALID_MEMBER_ROTATION
        == "partial_effective_members_with_warning"
    )
    # The mapping form carries each decision's rationale.
    decisions = srp.LOCKED_POLICY_DECISIONS
    for key in (
        "both_modes",
        "combine_mode",
        "seed_by",
        "optimize_by",
        "member_universe_size",
        "rerun_cadence",
        "invalid_member_rotation",
    ):
        assert key in decisions
        assert "value" in decisions[key]
        assert "rationale" in decisions[key]
        assert isinstance(
            decisions[key]["rationale"], str,
        )
        assert decisions[key]["rationale"], (
            f"{key} rationale is empty"
        )


# ---------------------------------------------------------------------------
# 3. Every command uses --secondary (NOT --ticker) +
#    locked policy flags.
# ---------------------------------------------------------------------------


def test_every_command_uses_secondary_and_locked_flags():
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    for cmd in manifest["command_manifest"]:
        argv = cmd["argv"]
        # --secondary, NOT --ticker.
        assert "--secondary" in argv
        assert "--ticker" not in argv
        sec_idx = argv.index("--secondary")
        # The ticker right after --secondary matches the
        # row's ticker.
        assert argv[sec_idx + 1] == cmd["ticker"]
        # --combine-mode intersection.
        assert "--combine-mode" in argv
        cm_idx = argv.index("--combine-mode")
        assert argv[cm_idx + 1] == "intersection"
        # --seed-by total_capture.
        assert "--seed-by" in argv
        sb_idx = argv.index("--seed-by")
        assert argv[sb_idx + 1] == "total_capture"
        # --optimize-by total_capture.
        assert "--optimize-by" in argv
        ob_idx = argv.index("--optimize-by")
        assert argv[ob_idx + 1] == "total_capture"
        # Other pinned launch defaults.
        for flag, val in (
            ("--top-n", "20"),
            ("--bottom-n", "20"),
            ("--max-k", "6"),
            ("--search", "beam"),
            ("--beam-width", "12"),
            ("--min-trigger-days", "30"),
        ):
            assert flag in argv
            assert (
                argv[argv.index(flag) + 1] == val
            )


def test_no_command_includes_both_modes():
    """The first-rollout policy pins both_modes=False; the
    flag absence matches the store_true default. This test
    is a hard regression guard."""
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    for cmd in manifest["command_manifest"]:
        assert "--both-modes" not in cmd["argv"]


# ---------------------------------------------------------------------------
# 4. Every command's display string starts with the pinned
#    interpreter.
# ---------------------------------------------------------------------------


def test_every_command_starts_with_pinned_interpreter():
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    for cmd in manifest["command_manifest"]:
        assert cmd["argv"][0] == srp.PINNED_INTERPRETER
        assert cmd["command"].startswith(
            srp.PINNED_INTERPRETER,
        )


# ---------------------------------------------------------------------------
# 5. Seed universe is deduped + uppercased + stripped.
# ---------------------------------------------------------------------------


def test_seed_universe_dedupes_and_normalizes():
    """The committed FIRST_ROLLOUT_PILOT_UNIVERSE_V1
    intentionally includes a duplicate JPM entry to pin
    that the normalizer dedupes. Caller-supplied tickers
    with whitespace / mixed case must also normalize."""
    # Default universe: the committed tuple has 26 entries
    # (one duplicate JPM); the normalizer dedupes to 25.
    assert (
        len(srp.FIRST_ROLLOUT_PILOT_UNIVERSE_V1) == 26
    )
    assert (
        srp.FIRST_ROLLOUT_PILOT_UNIVERSE_V1.count("JPM")
        == 2
    )
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    tickers = manifest["seed_universe_tickers"]
    assert len(tickers) == 25
    assert tickers.count("JPM") == 1
    # Caller-supplied path: whitespace + lowercase get
    # normalized; duplicates collapse.
    manifest2 = (
        srp.build_stackbuilder_rollout_policy_manifest(
            tickers=[
                "  aapl  ", "AAPL", "msft",
                " googl ", "GOOGL", "",
            ],
        )
    )
    assert manifest2["seed_universe_tickers"] == [
        "AAPL", "MSFT", "GOOGL",
    ]
    assert manifest2["seed_universe_count"] == 3


# ---------------------------------------------------------------------------
# 6. Manifest count equals deduped ticker count.
# ---------------------------------------------------------------------------


def test_manifest_count_matches_ticker_count():
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    assert (
        len(manifest["command_manifest"])
        == manifest["seed_universe_count"]
    )
    assert manifest["seed_universe_count"] == 25
    # And every manifest row's ticker is in the seed list.
    tickers = set(manifest["seed_universe_tickers"])
    for cmd in manifest["command_manifest"]:
        assert cmd["ticker"] in tickers


# ---------------------------------------------------------------------------
# 7. Each command record carries the locked taxonomy.
# ---------------------------------------------------------------------------


def test_each_command_carries_locked_taxonomy():
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    for cmd in manifest["command_manifest"]:
        assert (
            cmd["authorization_class"]
            == "stackbuilder_write"
        )
        assert (
            cmd["requires_separate_operator_authorization"]
            is True
        )
        assert (
            cmd["policy_basis"]
            == "phase_6i_52_locked_policy"
        )
        assert (
            cmd["blocked_by_policy_decision"] is False
        )
        assert (
            cmd["command_label"]
            == "stackbuilder_first_rollout_run"
        )


# ---------------------------------------------------------------------------
# 8. Generated argv parses against the real stackbuilder
#    argparse surface (catches future CLI drift).
# ---------------------------------------------------------------------------


def test_generated_argv_parses_against_real_stackbuilder_cli():
    """Deferred-import stackbuilder + run parse_args on
    the generated argv (minus the leading interpreter +
    script-name). This catches future stackbuilder.py CLI
    drift the moment it lands."""
    import stackbuilder
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    for cmd in manifest["command_manifest"]:
        argv = cmd["argv"]
        # argv = [interpreter, script.py, --flag, val, ...]
        # parse_args wants only the flags.
        parsed = stackbuilder.parse_args(argv[2:])
        assert parsed.secondary == cmd["ticker"]
        assert parsed.combine_mode == "intersection"
        assert parsed.seed_by == "total_capture"
        assert parsed.optimize_by == "total_capture"
        assert parsed.both_modes is False
        assert parsed.top_n == 20
        assert parsed.bottom_n == 20
        assert parsed.max_k == 6
        assert parsed.search == "beam"
        assert parsed.beam_width == 12
        assert parsed.min_trigger_days == 30


# ---------------------------------------------------------------------------
# 9. Static guard: no forbidden top-level imports.
#    The module must not execute any candidate command.
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
        / "confluence_stackbuilder_rollout_policy.py"
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
        f"Forbidden top-level imports in rollout policy: "
        f"{sorted(leaked)}"
    )


# ---------------------------------------------------------------------------
# 10. --output rejects paths inside any production root.
# ---------------------------------------------------------------------------


def test_output_path_guard_rejects_production_root_paths(
    capsys,
):
    forbidden_outputs = [
        "cache/results/policy.json",
        "cache\\status\\policy.json",
        "output/research_artifacts/policy.json",
        "output/stackbuilder/policy.json",
        "signal_library/data/stable/policy.json",
    ]
    for forbidden in forbidden_outputs:
        rc = srp.main(["--output", forbidden])
        err = capsys.readouterr().err
        assert rc == 2
        assert "output_path_inside_production_root" in err


# ---------------------------------------------------------------------------
# 11. --tickers CLI override + --signal-library-dir
#     threading.
# ---------------------------------------------------------------------------


def test_cli_tickers_override_and_signal_lib_dir(
    tmp_path, capsys,
):
    out = tmp_path / "policy.json"
    rc = srp.main([
        "--tickers", "spy,aapl,aapl",  # dedup test
        "--signal-library-dir",
        "signal_library/data/stable",
        "--output", str(out),
    ])
    assert rc == 0
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["seed_universe_tickers"] == [
        "SPY", "AAPL",
    ]
    assert parsed["seed_universe_count"] == 2
    assert len(parsed["command_manifest"]) == 2
    for cmd in parsed["command_manifest"]:
        assert (
            "--signal-lib-dir" in cmd["argv"]
        )
        sl_idx = cmd["argv"].index("--signal-lib-dir")
        assert (
            cmd["argv"][sl_idx + 1]
            == "signal_library/data/stable"
        )


# ---------------------------------------------------------------------------
# 12. SPY appears in the seed universe (continuity with
#     the Phase 6I-49 pilot).
# ---------------------------------------------------------------------------


def test_seed_universe_includes_spy_for_continuity():
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    assert "SPY" in manifest["seed_universe_tickers"]
    # And appears as the first ticker (matching the
    # committed tuple's deliberate ordering).
    assert (
        manifest["seed_universe_tickers"][0] == "SPY"
    )


# ---------------------------------------------------------------------------
# 13. unresolved_or_deferred_policy_items carries through
#     so downstream consumers (Phase 6I-53) can audit them.
# ---------------------------------------------------------------------------


def test_unresolved_or_deferred_policy_items_present():
    manifest = (
        srp.build_stackbuilder_rollout_policy_manifest()
    )
    items = manifest["unresolved_or_deferred_policy_items"]
    assert isinstance(items, list)
    # Five-ish deliberately-deferred items (per-ticker
    # member sizing, automated cadence, auto-substitution,
    # combine_mode union eval, seed_by sharpe eval,
    # second-rollout universe size).
    assert len(items) >= 5
    assert any(
        i.startswith("per_ticker_member_universe_sizing")
        for i in items
    )
    assert any(
        i.startswith("automated_rerun_cadence")
        for i in items
    )
