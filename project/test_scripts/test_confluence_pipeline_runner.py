"""Phase 6D-4 tests for confluence_pipeline_runner.

Pins the operator-facing chain runner contract:

  - single-ticker dry-run writes nothing
  - single-ticker write=True chains all three builders against
    temp roots and a final readiness pass
  - per-stage issue codes propagate into the rollup without
    being flattened
  - stale source data produces a stale readiness verdict (not a
    false success)
  - missing StackBuilder run prevents downstream stages from
    pretending success
  - missing MTF / Confluence stages surface their own clean
    issue codes
  - multi-ticker runner isolates per-ticker failures
  - CLI defaults to dry-run
  - CLI ``--write`` flag honors the explicit ``--artifact-root``
    and confines writes to the supplied temp tree
  - no yfinance / live engine imports in the runner module
"""
from __future__ import annotations

import ast
import io
import json
import pickle
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import confluence_mtf_artifact_builder as cmab  # noqa: E402
import confluence_pipeline_readiness as cpr  # noqa: E402
import confluence_pipeline_runner as runner  # noqa: E402
import research_artifacts as ra  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _layout(tmp_path: Path) -> dict[str, Path]:
    cache_dir = tmp_path / "cache"
    artifact_root = tmp_path / "artifacts"
    stack_dir = tmp_path / "stackbuilder"
    sig_dir = tmp_path / "siglib"
    for d in (cache_dir, artifact_root, stack_dir, sig_dir):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "cache_dir": cache_dir,
        "artifact_root": artifact_root,
        "stackbuilder_root": stack_dir,
        "signal_library_dir": sig_dir,
    }


def _write_target_cache(
    cache_dir: Path, ticker: str, *,
    last_date: str = "2026-05-08",
    n: int = 30,
    final_pair: str = "Buy 3,2",
) -> Path:
    """Write a minimal Spymaster-cache PKL so the downstream
    builders can resolve a target close series. The shape uses
    preprocessed_data + active_pairs to match the readers."""
    import pandas as pd
    cache_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.bdate_range(end=last_date, periods=n)
    df = pd.DataFrame(
        {"Close": [100.0 + i for i in range(n)]},
        index=dates,
    )
    active_pairs = ["Buy 3,2"] * (n - 1) + [final_pair]
    payload = {
        "preprocessed_data": df,
        "active_pairs": active_pairs,
    }
    safe = ticker.replace("^", "_")
    path = cache_dir / f"{safe}_precomputed_results.pkl"
    with path.open("wb") as fh:
        pickle.dump(payload, fh)
    return path


def _write_member_caches(cache_dir: Path, members: list[str]) -> None:
    for m in members:
        _write_target_cache(cache_dir, m, n=20)


def _write_combo_leaderboard(
    stack_root: Path, target: str, *,
    seed_name: str = "seedTC__AAA-D_BBB-D",
    members_str: str = "['AAA[D]', 'BBB[D]']",
) -> Path:
    """Write a minimal combo_leaderboard.xlsx covering K=1..12
    with the same member set on every row."""
    import pandas as pd
    safe = target.replace("^", "_")
    run_dir = stack_root / safe / seed_name
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = [{
        "K": k,
        "Trigger Days": 100 + k,
        "Total Capture (%)": 10.0 + k,
        "Sharpe Ratio": 0.1 + k * 0.01,
        "p-Value": 0.05,
        "Members": members_str,
    } for k in range(1, 13)]
    df = pd.DataFrame(rows, columns=[
        "K", "Trigger Days", "Total Capture (%)",
        "Sharpe Ratio", "p-Value", "Members",
    ])
    out = run_dir / "combo_leaderboard.xlsx"
    df.to_excel(out, index=False)
    return out


def _write_full_phase_6d1_inputs(
    dirs: dict[str, Path], target: str, *,
    last_date: str = "2026-05-08",
    members: tuple[str, ...] = ("AAA", "BBB"),
) -> None:
    """Lay down the inputs the Phase 6D-1 builder needs:
    target cache, member caches, and a StackBuilder
    combo_leaderboard.xlsx."""
    _write_target_cache(
        dirs["cache_dir"], target, last_date=last_date,
    )
    _write_member_caches(dirs["cache_dir"], list(members))
    members_str = (
        "[" + ", ".join(f"'{m}[D]'" for m in members) + "]"
    )
    _write_combo_leaderboard(
        dirs["stackbuilder_root"], target,
        members_str=members_str,
    )


# ---------------------------------------------------------------------------
# 1. Static-import sanity
# ---------------------------------------------------------------------------


def test_runner_module_has_no_forbidden_imports():
    """Runner is offline / read-only by design. It must NOT
    import yfinance or any live engine module."""
    tree = ast.parse(
        Path(runner.__file__).read_text(encoding="utf-8"),
    )
    forbidden = {
        "yfinance", "trafficflow", "spymaster", "impactsearch",
        "onepass", "confluence", "cross_ticker_confluence",
        "dash", "daily_signal_board",
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.append(node.module)
    bad = [m for m in found if m.split(".")[0] in forbidden]
    assert not bad, (
        "forbidden import in confluence_pipeline_runner: "
        + repr(bad)
    )


# ---------------------------------------------------------------------------
# 2. Dry-run writes nothing
# ---------------------------------------------------------------------------


def test_single_ticker_dry_run_writes_nothing(tmp_path: Path):
    """write=False must not leave any artifact on disk even when
    the inputs (StackBuilder + member caches) would let the
    builders produce content."""
    dirs = _layout(tmp_path)
    _write_full_phase_6d1_inputs(dirs, "SPY")
    res = runner.run_confluence_pipeline_for_ticker(
        "SPY", write=False, **dirs,
    )
    # All four stages recorded.
    assert [s.stage for s in res.stages] == [
        runner.STAGE_ID_6D1, runner.STAGE_ID_6D2,
        runner.STAGE_ID_6D3, runner.STAGE_ID_READINESS,
    ]
    # Stage 6D-1 reports K=1..12 attempted; built_k can be
    # populated in-memory even with write=False.
    s1 = res.stage(runner.STAGE_ID_6D1)
    assert s1 is not None
    assert set(s1.attempted_k) == set(range(1, 13))
    # No artifacts persisted - artifact_root walk turns up no
    # research_day JSONs.
    files = list(dirs["artifact_root"].rglob("*.research_day.json"))
    assert files == [], (
        f"write=False must not persist artifacts; found {files}"
    )
    assert res.write is False
    assert res.artifact_paths == ()


# ---------------------------------------------------------------------------
# 3. write=True chains all three builders end-to-end
# ---------------------------------------------------------------------------


def test_single_ticker_write_true_chains_three_builders(
    tmp_path: Path,
):
    """write=True must produce artifacts under each engine
    subdir and a single Confluence artifact at the canonical
    location. The readiness verdict reflects the freshly
    written tree."""
    dirs = _layout(tmp_path)
    _write_full_phase_6d1_inputs(dirs, "SPY")
    # Multi-timeframe libraries so the readiness presence-only
    # stage stays happy.
    for interval in ("1wk", "1mo"):
        (dirs["signal_library_dir"]
         / f"SPY_stable_v1_0_0_{interval}.pkl").write_bytes(b"x")
    res = runner.run_confluence_pipeline_for_ticker(
        "SPY", write=True,
        current_as_of_date="2026-05-08",
        **dirs,
    )
    # Stage 6D-1 wrote 12 daily K artifacts.
    s1 = res.stage(runner.STAGE_ID_6D1)
    assert s1 is not None
    assert set(s1.built_k) == set(range(1, 13))
    assert len(s1.artifact_paths) == 12
    # Stage 6D-2 wrote 12 MTF K artifacts.
    s2 = res.stage(runner.STAGE_ID_6D2)
    assert s2 is not None
    assert set(s2.built_k) == set(range(1, 13))
    assert len(s2.artifact_paths) == 12
    # Stage 6D-3 wrote a single Confluence artifact.
    s3 = res.stage(runner.STAGE_ID_6D3)
    assert s3 is not None
    assert s3.built is True
    assert len(s3.artifact_paths) == 1
    # Confluence artifact landed at the canonical confluence path.
    conf_files = list(
        (dirs["artifact_root"] / "confluence")
        .rglob("*.research_day.json"),
    )
    assert len(conf_files) == 1
    # Readiness verdict: ineligible because the catalogue
    # health report stage carries no report on the temp tree,
    # but missing_confluence_day_artifact is GONE.
    assert res.readiness is not None
    assert (
        cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT
        not in res.readiness.issue_codes
    )
    assert (
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE
        not in res.readiness.issue_codes
    )
    assert res.write is True


def test_pipeline_preserves_input_last_date_end_to_end(
    tmp_path: Path,
):
    """Phase 6F-4 contract: through the chain, MTF K and
    Confluence must end on the SAME trading day as the
    Phase 6D-1 daily K artifact. Phase 6D-1 owns its own
    persist_skip_bars=1 trim (T-1 of the input cache); the
    MTF bridge must not apply ANOTHER trim on top. Before
    the fix this test would fail with MTF / Confluence one
    day behind daily K, and the readiness verdict would
    carry ``stale_confluence_day_artifact``.

    Pipeline-style fixture: a target cache ending on
    ``2026-05-11`` (Mon) + StackBuilder + multi-timeframe
    libs. ``current_as_of_date=2026-05-08``. Phase 6D-1
    trims the cache to daily K last_date=2026-05-08. After
    the fix the rest of the chain holds that date.
    """
    dirs = _layout(tmp_path)
    _write_full_phase_6d1_inputs(
        dirs, "SPY", last_date="2026-05-11",
    )
    for interval in ("1wk", "1mo"):
        (dirs["signal_library_dir"]
         / f"SPY_stable_v1_0_0_{interval}.pkl").write_bytes(b"x")

    res = runner.run_confluence_pipeline_for_ticker(
        "SPY", write=True,
        current_as_of_date="2026-05-08",
        **dirs,
    )
    assert res.write is True
    s1 = res.stage(runner.STAGE_ID_6D1)
    s2 = res.stage(runner.STAGE_ID_6D2)
    s3 = res.stage(runner.STAGE_ID_6D3)
    assert s1 and len(s1.artifact_paths) == 12
    assert s2 and len(s2.artifact_paths) == 12
    assert s3 and len(s3.artifact_paths) == 1

    def _last_date(path: Path) -> str:
        art = ra.read_research_day_artifact(Path(path))
        assert art is not None
        assert art.daily
        return str(art.daily[-1].get("date"))

    # Step 1: read what Phase 6D-1 actually produced. That
    # is the canonical date the rest of the chain must
    # preserve. Phase 6D-1's own T-1 trim is owned by that
    # stage and out of scope for this test.
    daily_last_dates = {
        _last_date(p) for p in s1.artifact_paths
    }
    assert len(daily_last_dates) == 1, (
        "expected all daily K artifacts to end on the same "
        f"date; got {sorted(daily_last_dates)}"
    )
    expected_last = daily_last_dates.pop()
    # And that date must be >= the cutoff so the readiness
    # gate can pass after the fix.
    assert expected_last >= "2026-05-08", (
        "daily K last_date drifted past the readiness "
        f"cutoff; got {expected_last!r}"
    )

    # Step 2: MTF K and Confluence must inherit the daily K
    # last_date (Phase 6F-4 contract).
    for p in s2.artifact_paths:
        assert _last_date(p) == expected_last, (
            f"MTF K {Path(p).name} ended on "
            f"{_last_date(p)!r}; expected {expected_last!r} "
            "(daily K last_date). Phase 6F-4 default must "
            "not double-trim."
        )
    for p in s3.artifact_paths:
        assert _last_date(p) == expected_last, (
            f"Confluence {Path(p).name} ended on "
            f"{_last_date(p)!r}; expected {expected_last!r}"
        )

    # Step 3: readiness no longer carries the stale-
    # Confluence blocker.
    assert res.readiness is not None
    assert (
        cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT
        not in res.readiness.issue_codes
    ), (
        "stale_confluence_day_artifact must clear after the "
        "Phase 6F-4 fix; readiness reported: "
        f"{sorted(res.readiness.issue_codes)}"
    )


# ---------------------------------------------------------------------------
# 4. Stage issue codes roll up
# ---------------------------------------------------------------------------


def test_stage_issue_codes_propagate_to_rollup(tmp_path: Path):
    """Without a StackBuilder run, 6D-1 emits
    no_stackbuilder_run, 6D-2 emits no_daily_k_artifacts, 6D-3
    emits no_mtf_trafficflow_artifacts, and readiness lists the
    full set of missing stages. The runner's issue_codes
    rollup must contain each stage's primary code without
    flattening."""
    dirs = _layout(tmp_path)
    res = runner.run_confluence_pipeline_for_ticker(
        "SPY", write=False, **dirs,
    )
    rollup = set(res.issue_codes)
    for code in (
        "no_stackbuilder_run",
        "no_daily_k_artifacts",
        "no_mtf_trafficflow_artifacts",
        cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT,
        cpr.ISSUE_MISSING_MULTITIMEFRAME_TRAFFICFLOW_BRIDGE,
        cpr.ISSUE_MISSING_SIGNAL_ENGINE_CACHE,
    ):
        assert code in rollup, (
            f"rollup missing {code!r}; got {sorted(rollup)}"
        )
    # The blocked_reason is the highest-priority readiness code
    # (here: missing_confluence_day_artifact).
    assert (
        res.ranking_blocked_reason
        == cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT
    )
    assert res.leader_eligible is False


# ---------------------------------------------------------------------------
# 5. Stale source produces stale readiness
# ---------------------------------------------------------------------------


def test_stale_source_produces_stale_readiness(tmp_path: Path):
    """An ancient StackBuilder leaderboard + ancient member
    caches produce ancient Confluence artifacts. The readiness
    verdict (under today's default cutoff) must report
    stale_confluence_day_artifact - never a false success."""
    dirs = _layout(tmp_path)
    _write_full_phase_6d1_inputs(
        dirs, "SPY", last_date="2024-01-31",
        members=("AAA", "BBB"),
    )
    _write_target_cache(
        dirs["cache_dir"], "AAA",
        last_date="2024-01-31", n=20,
    )
    _write_target_cache(
        dirs["cache_dir"], "BBB",
        last_date="2024-01-31", n=20,
    )
    res = runner.run_confluence_pipeline_for_ticker(
        "SPY", write=True,
        # Don't pass current_as_of_date - default fallback
        # picks today's previous weekday.
        **dirs,
    )
    assert res.readiness is not None
    assert (
        cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT
        in res.readiness.issue_codes
    )
    assert res.leader_eligible is False
    assert (
        res.ranking_blocked_reason
        == cpr.ISSUE_STALE_CONFLUENCE_DAY_ARTIFACT
    )


# ---------------------------------------------------------------------------
# 6. Missing StackBuilder run -> downstream stages stay honest
# ---------------------------------------------------------------------------


def test_missing_stackbuilder_run_prevents_downstream_success(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    # Target cache exists, but no StackBuilder seed run.
    _write_target_cache(dirs["cache_dir"], "SPY")
    res = runner.run_confluence_pipeline_for_ticker(
        "SPY", write=True, **dirs,
    )
    s1 = res.stage(runner.STAGE_ID_6D1)
    s2 = res.stage(runner.STAGE_ID_6D2)
    s3 = res.stage(runner.STAGE_ID_6D3)
    assert s1.issue_codes == ("no_stackbuilder_run",)
    assert "no_daily_k_artifacts" in s2.issue_codes
    assert "no_mtf_trafficflow_artifacts" in s3.issue_codes
    # No artifact ever written.
    files = list(
        dirs["artifact_root"].rglob("*.research_day.json"),
    )
    assert files == []
    # Readiness reports the confluence stage as absent.
    assert (
        cpr.ISSUE_MISSING_CONFLUENCE_DAY_ARTIFACT
        in res.readiness.issue_codes
    )
    assert res.leader_eligible is False


# ---------------------------------------------------------------------------
# 7. Multi-ticker runner isolates failures
# ---------------------------------------------------------------------------


def test_multi_ticker_runner_isolates_per_ticker_failures(
    tmp_path: Path,
):
    dirs = _layout(tmp_path)
    # SPY: full inputs. NOPE: no inputs at all.
    _write_full_phase_6d1_inputs(dirs, "SPY")
    for interval in ("1wk", "1mo"):
        (dirs["signal_library_dir"]
         / f"SPY_stable_v1_0_0_{interval}.pkl").write_bytes(b"x")
    results = runner.run_confluence_pipeline_for_tickers(
        ["SPY", "NOPE"], write=True,
        current_as_of_date="2026-05-08",
        **dirs,
    )
    by_ticker = {r.ticker: r for r in results}
    # SPY produced 12 + 12 + 1 = 25 artifact paths.
    assert len(by_ticker["SPY"].artifact_paths) == 25
    # NOPE produced none.
    assert by_ticker["NOPE"].artifact_paths == ()
    assert "no_stackbuilder_run" in by_ticker["NOPE"].issue_codes
    # The two results are independent (NOPE issues do NOT
    # appear in SPY's rollup).
    assert (
        "no_stackbuilder_run" not in by_ticker["SPY"].issue_codes
    )


# ---------------------------------------------------------------------------
# 8. CLI defaults to dry-run
# ---------------------------------------------------------------------------


def test_cli_defaults_to_dry_run(
    monkeypatch, tmp_path: Path, capsys,
):
    """Without ``--write``, the CLI must not persist anything
    under the supplied artifact_root."""
    dirs = _layout(tmp_path)
    _write_full_phase_6d1_inputs(dirs, "SPY")
    argv = [
        "--ticker", "SPY",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
    ]
    rc = runner.main(argv)
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert isinstance(payload, list) and len(payload) == 1
    entry = payload[0]
    assert entry["ticker"] == "SPY"
    assert entry["write"] is False
    # No artifact written on disk.
    files = list(
        dirs["artifact_root"].rglob("*.research_day.json"),
    )
    assert files == []


# ---------------------------------------------------------------------------
# 9. CLI write flag writes only under explicit artifact_root
# ---------------------------------------------------------------------------


def test_cli_write_flag_writes_only_to_explicit_temp_root(
    tmp_path: Path, capsys,
):
    dirs = _layout(tmp_path)
    _write_full_phase_6d1_inputs(dirs, "SPY")
    for interval in ("1wk", "1mo"):
        (dirs["signal_library_dir"]
         / f"SPY_stable_v1_0_0_{interval}.pkl").write_bytes(b"x")
    argv = [
        "--ticker", "SPY",
        "--write",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
        "--current-as-of-date", "2026-05-08",
    ]
    rc = runner.main(argv)
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload[0]["write"] is True
    # 25 artifacts under the explicit temp artifact_root.
    files = list(
        dirs["artifact_root"].rglob("*.research_day.json"),
    )
    assert len(files) == 25, [p.name for p in files]
    # Confluence artifact present.
    conf_files = list(
        (dirs["artifact_root"] / "confluence")
        .rglob("*.research_day.json"),
    )
    assert len(conf_files) == 1


# ---------------------------------------------------------------------------
# 10. CLI exit codes
# ---------------------------------------------------------------------------


def test_cli_missing_ticker_args_returns_nonzero(capsys):
    """argparse returns 2 for invalid args. The runner exposes
    that via main() rather than allowing SystemExit through."""
    rc = runner.main([])
    assert rc == 2


def test_cli_mutually_exclusive_ticker_and_tickers(capsys):
    rc = runner.main(["--ticker", "SPY", "--tickers", "AAPL,SPY"])
    assert rc == 2


def test_cli_blank_ticker_returns_2_without_system_exit(capsys):
    """PR #199 audit fix: ``main(["--ticker", "   "])`` parsed
    cleanly through argparse but the post-parse empty-ticker
    check used ``parser.error()`` which raises SystemExit. The
    public ``main(argv=None) -> int`` contract requires a real
    return value; this test pins that the audit fix holds."""
    rc = None
    try:
        rc = runner.main(["--ticker", "   "])
    except SystemExit as exc:
        pytest.fail(
            "main() raised SystemExit on a blank --ticker; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2
    err = capsys.readouterr().err
    assert "ticker" in err.lower()


def test_cli_empty_tickers_returns_2_without_system_exit(capsys):
    """Same contract for ``--tickers``: ``,,,`` (all-empty list)
    must return 2 from main() without raising SystemExit."""
    rc = None
    try:
        rc = runner.main(["--tickers", ",,,"])
    except SystemExit as exc:
        pytest.fail(
            "main() raised SystemExit on an empty --tickers list; "
            f"contract requires return 2 (got SystemExit({exc.code}))"
        )
    assert rc == 2
    err = capsys.readouterr().err
    assert "ticker" in err.lower()


def test_cli_dry_run_flag_is_synonym_for_default(
    tmp_path: Path, capsys,
):
    dirs = _layout(tmp_path)
    _write_full_phase_6d1_inputs(dirs, "SPY")
    argv = [
        "--ticker", "SPY",
        "--dry-run",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
    ]
    rc = runner.main(argv)
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload[0]["write"] is False
    assert (
        list(dirs["artifact_root"].rglob("*.research_day.json"))
        == []
    )


def test_cli_write_and_dry_run_are_mutually_exclusive(capsys):
    rc = runner.main([
        "--ticker", "SPY", "--write", "--dry-run",
    ])
    assert rc == 2


# ---------------------------------------------------------------------------
# 11. CLI multi-ticker
# ---------------------------------------------------------------------------


def test_cli_multi_ticker_comma_list_runs_each(
    tmp_path: Path, capsys,
):
    dirs = _layout(tmp_path)
    _write_full_phase_6d1_inputs(dirs, "SPY")
    argv = [
        "--tickers", "SPY,NOPE",
        "--cache-dir", str(dirs["cache_dir"]),
        "--artifact-root", str(dirs["artifact_root"]),
        "--stackbuilder-root", str(dirs["stackbuilder_root"]),
        "--signal-library-dir", str(dirs["signal_library_dir"]),
    ]
    rc = runner.main(argv)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["ticker"] for r in payload] == ["SPY", "NOPE"]


# ---------------------------------------------------------------------------
# 12. JSON serialization shape
# ---------------------------------------------------------------------------


def test_result_to_json_dict_is_serializable(tmp_path: Path):
    dirs = _layout(tmp_path)
    _write_full_phase_6d1_inputs(dirs, "SPY")
    res = runner.run_confluence_pipeline_for_ticker(
        "SPY", write=False, **dirs,
    )
    d = res.to_json_dict()
    # Round-trips through json.dumps without complaint.
    s = json.dumps(d)
    assert "SPY" in s
    # All stages present in serialized form.
    stage_ids = {entry["stage"] for entry in d["stages"]}
    assert stage_ids == {
        runner.STAGE_ID_6D1, runner.STAGE_ID_6D2,
        runner.STAGE_ID_6D3, runner.STAGE_ID_READINESS,
    }
    # Paths serialize as strings.
    for s_entry in d["stages"]:
        for p in s_entry["artifact_paths"]:
            assert isinstance(p, str)
    # readiness is either None or a dict.
    assert d["readiness"] is None or isinstance(d["readiness"], dict)
