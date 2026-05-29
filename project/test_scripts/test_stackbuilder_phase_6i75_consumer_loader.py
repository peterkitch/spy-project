"""Phase 6I-75: StackBuilder consumer-only signal-library loader tests
(Slice 2b/4 amendment: strict manifest verification restored).

These tests originated around PR #290's Phase 6I-75 StackBuilder
runtime-boundary work. The Slice 2b/4 amendment unwinds only the
manifest-bypass portion of that contract to restore the Phase 3A
consumer-verifies contract (PR #140 / F11-F15); every other PR #290
goal is still pinned here.

The contract this file enforces:

  * StackBuilder must NOT import or call ``onepass.load_signal_library``
    or any other OnePass entry point from its signal-library load
    path (PR #290 OnePass-decoupling preserved).
  * Library loads route through ``fallback_load_signal_library`` (the
    Phase 3A-named consumer entry point that the B12 AST guard
    inspects), with ``load_signal_library_for_stackbuilder`` and
    ``load_lib_or_none`` as thin forward-compatibility aliases. The
    loader delegates pickle reading and manifest verification to
    ``provenance_manifest.load_verified_signal_library`` (the central
    Phase 3A loader); manifest / content_hash mismatches are
    rejected, legacy no-manifest libraries are accepted.
  * Loads must be memoized per-run on both success and failure, keyed
    by file identity (path + mtime_ns + size).
  * Diagnostics must use the ``[STACKBUILDER:*]`` prefix family,
    never ``[ONEPASS:*]`` and never ``Forcing rebuild``.
  * No network call, PKL write, or manifest write is permitted from
    the consumer load path.
"""

from __future__ import annotations

import ast
import importlib
import io
import json
import os
import pickle
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


import provenance_manifest as pm  # noqa: E402


@pytest.fixture
def sb():
    """Fresh-imported stackbuilder module with consumer-loader cache cleared."""
    module = importlib.import_module("stackbuilder")
    module._reset_consumer_loader_cache()
    yield module
    module._reset_consumer_loader_cache()


def _write_lib(
    path: Path,
    with_manifest: bool = True,
    mutate_after_attach: bool = False,
) -> dict:
    """Write a minimal-but-valid signal-library PKL at ``path``.

    Schema mirrors the OnePass on-disk shape that StackBuilder
    consumes: ``primary_signals`` is a 1D scalar-per-day sequence
    parallel to ``dates``.

    Slice 2b/4 amendment: when ``with_manifest=True`` the manifest is
    attached via ``provenance_manifest.attach_manifest`` so the
    ``content_hash`` reflects the real canonical payload. The Phase 3A
    central loader recomputes and compares this hash on every load,
    so a phony hash would (correctly) trip the manifest-rejection
    path. ``with_manifest=False`` writes a legacy library with no
    ``_manifest`` field; the central loader accepts these as
    ``legacy=True``. ``mutate_after_attach=True`` mirrors the F14
    fixture pattern (``test_provenance_manifest.py:445-447``): the
    payload is overwritten AFTER the manifest is attached, leaving the
    on-disk pickle with a ``_manifest.content_hash`` that no longer
    matches the on-disk ``primary_signals``. The loader must reject
    such tampered fixtures.
    """
    lib = {
        "primary_signals": [0, 1, -1, 0],
        "dates": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        "tags": ["BUY[D]"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    if with_manifest:
        ticker = path.stem.split("_")[0]
        pm.attach_manifest(
            lib,
            path,
            artifact_type="signal_library_daily",
            ticker=ticker,
            interval="1d",
            params={
                "engine_version": "1.0.0",
                "MAX_SMA_DAY": 114,
                "price_source": "Close",
                "interval": "1d",
            },
            engine_version="1.0.0",
        )
        if mutate_after_attach:
            # Tamper after manifest attach -> content_hash mismatch.
            lib["primary_signals"] = [1, 1, 1, 1]
    with open(path, "wb") as fh:
        pickle.dump(lib, fh)
    return lib


def _live_string_constants(source_path: Path) -> list:
    """Return all string constants in ``source_path`` that are NOT
    module/function/class docstrings or comments.

    Comments are dropped by ``ast.parse`` naturally. Docstrings are
    the first statement of a module/function/class body whose value
    is an ``ast.Constant`` string.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    docstring_node_ids: set = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0] if isinstance(body, list) else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstring_node_ids.add(id(first.value))
    live = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_node_ids:
                continue
            live.append(node.value)
    return live


# ---------------------------------------------------------------------------
# Static surface checks
# ---------------------------------------------------------------------------

def test_module_does_not_import_load_signal_library_from_onepass():
    """No ``ImportFrom(onepass, [load_signal_library])`` survives.

    Comments and docstrings that mention the removed import are
    fine — only live ``from onepass import load_signal_library``
    statements are forbidden.
    """
    tree = ast.parse((PROJECT_DIR / "stackbuilder.py").read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "onepass":
            for alias in node.names:
                if alias.name == "load_signal_library":
                    offenders.append(
                        f"line {node.lineno}: from onepass import {alias.name}"
                    )
    assert offenders == []


def test_module_symbol_load_signal_library_is_none(sb):
    """The module-level ``load_signal_library`` is bound to ``None``."""
    assert sb.load_signal_library is None


def test_no_onepass_or_rebuild_diagnostics_in_active_code():
    """No live string constant emits ``[ONEPASS:`` or ``Forcing rebuild``.

    Implementation: walk the AST and look at every ``ast.Constant``
    string that is NOT a docstring of its enclosing module / class /
    function. Comments are dropped by ``ast.parse`` already.
    """
    live = _live_string_constants(PROJECT_DIR / "stackbuilder.py")
    offenders = [s for s in live if "[ONEPASS:" in s or "Forcing rebuild" in s]
    assert offenders == [], f"Forbidden live string constants: {offenders!r}"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_consumer_loader_reads_pkl_directly(sb, tmp_path, monkeypatch):
    """``load_signal_library_for_stackbuilder`` returns the dict via
    direct ``pickle.load`` — no OnePass call required."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "ABC_stable_v1.pkl"
    expected = _write_lib(lib_path)

    loaded = sb.load_signal_library_for_stackbuilder("ABC")

    assert loaded is not None
    assert loaded["primary_signals"] == expected["primary_signals"]
    assert loaded["dates"] == expected["dates"]


def test_consumer_loader_accepts_lib_without_manifest(sb, tmp_path, monkeypatch):
    """Consumer mode loads readable libraries with no ``_manifest`` key.

    The Phase 3B-1 strict path rejected these; the Phase 6I-75 consumer
    path only cares about the data payload.
    """
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "ABC_stable_v1.pkl"
    _write_lib(lib_path, with_manifest=False)

    loaded = sb.load_signal_library_for_stackbuilder("ABC")

    assert loaded is not None
    assert "_manifest" not in loaded


# ---------------------------------------------------------------------------
# Memoization
# ---------------------------------------------------------------------------

def test_consumer_loader_memoizes_success(sb, tmp_path, monkeypatch):
    """A successful load is cached; subsequent calls do not re-pickle.load."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "MEM_stable_v1.pkl"
    _write_lib(lib_path)

    call_count = {"n": 0}
    real_load = pickle.load

    def counting_load(fh, *args, **kwargs):
        call_count["n"] += 1
        return real_load(fh, *args, **kwargs)

    monkeypatch.setattr(sb.pickle, "load", counting_load)

    a = sb.load_signal_library_for_stackbuilder("MEM")
    b = sb.load_signal_library_for_stackbuilder("MEM")
    c = sb.load_signal_library_for_stackbuilder("MEM")

    assert a is not None and b is not None and c is not None
    assert call_count["n"] == 1


def test_consumer_loader_memoizes_failure(sb, tmp_path, monkeypatch):
    """A missing library is cached as a failure; subsequent calls do not
    re-glob or re-warn."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))

    out = io.StringIO()
    with redirect_stdout(out):
        a = sb.load_signal_library_for_stackbuilder("MISSING")
        b = sb.load_signal_library_for_stackbuilder("MISSING")
        c = sb.load_signal_library_for_stackbuilder("MISSING")

    assert a is None and b is None and c is None
    # Warn-once: a single missing-library diagnostic for the ticker.
    missing_lines = [
        ln for ln in out.getvalue().splitlines()
        if "[STACKBUILDER:library_missing]" in ln
    ]
    assert len(missing_lines) == 1


def test_no_candidate_failure_is_cached(sb, tmp_path, monkeypatch):
    """Amendment 5: a no-candidates outcome is stored in the cache so
    repeated calls for the same missing ticker neither re-glob nor
    re-warn."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))

    glob_count = {"n": 0}
    real_lister = sb.list_signal_library_candidates

    def counting_lister(ticker):
        glob_count["n"] += 1
        return real_lister(ticker)

    monkeypatch.setattr(sb, "list_signal_library_candidates", counting_lister)

    out = io.StringIO()
    with redirect_stdout(out):
        for _ in range(5):
            assert sb.load_signal_library_for_stackbuilder("ABSENT") is None

    # The lister is called at most once per missing ticker per run.
    assert glob_count["n"] == 1
    # The diagnostic is also emitted at most once.
    missing_lines = [
        ln for ln in out.getvalue().splitlines()
        if "[STACKBUILDER:library_missing]" in ln
    ]
    assert len(missing_lines) == 1


def test_no_candidate_cache_key_includes_signal_lib_dir(sb, tmp_path, monkeypatch):
    """Switching ``SIGNAL_LIB_DIR_RUNTIME`` invalidates the no-candidate
    failure cache for the same ticker so the new directory is actually
    inspected."""
    dir_a = tmp_path / "lib_a"
    dir_b = tmp_path / "lib_b"
    dir_a.mkdir()
    dir_b.mkdir()

    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(dir_a))
    assert sb.load_signal_library_for_stackbuilder("MULTI") is None

    # Place a usable PKL under dir_b and switch the runtime dir there;
    # the new no-candidate key differs, so the loader must inspect
    # dir_b and find the PKL.
    _write_lib(dir_b / "MULTI_stable_v1.pkl")
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(dir_b))
    found = sb.load_signal_library_for_stackbuilder("MULTI")
    assert found is not None


def test_corrupt_pkl_returns_none_without_writes(sb, tmp_path, monkeypatch):
    """A truncated / corrupt PKL returns ``None`` with no rebuild
    trigger, no file write under the signal-library directory, and no
    network call."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    corrupt_path = tmp_path / "CORRUPT_stable_v1.pkl"
    # Truncated header — not a valid pickle stream.
    corrupt_path.write_bytes(b"\x80\x04\x95not-a-valid-pickle")

    before = sorted(p.name for p in tmp_path.iterdir())
    out = io.StringIO()
    with redirect_stdout(out):
        result = sb.load_signal_library_for_stackbuilder("CORRUPT")
    after = sorted(p.name for p in tmp_path.iterdir())

    assert result is None
    # No new file created. No file removed. The single corrupt PKL is
    # still on disk untouched.
    assert before == after
    # No "rebuild" / OnePass / network diagnostic surfaced.
    text = out.getvalue()
    assert "[STACKBUILDER:library_unreadable]" in text
    assert "[ONEPASS:" not in text
    assert "rebuild" not in text.lower()
    assert "yfinance" not in text.lower()


def test_readable_pkl_with_mismatched_manifest_is_rejected(sb, tmp_path, monkeypatch):
    """A readable PKL whose payload was tampered with after the
    manifest was attached must be rejected by the StackBuilder
    consumer loader.

    Slice 2b/4 amendment: inverts the prior Phase 6I-75 acceptance
    pin. The restored Phase 3A consumer-verifies contract (PR #140 /
    F11-F15) requires that every signal-library consumer detect a
    ``content_hash`` mismatch and refuse to return the corrupted
    library. ``stackbuilder.fallback_load_signal_library`` now joins
    onepass / impactsearch / impact_fastpath / confluence in
    enforcing that contract via
    ``provenance_manifest.load_verified_signal_library``."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "MISMATCH_stable_v1.pkl"
    _write_lib(lib_path, mutate_after_attach=True)

    out = io.StringIO()
    with redirect_stdout(out):
        loaded = sb.load_signal_library_for_stackbuilder("MISMATCH")

    assert loaded is None
    assert "[STACKBUILDER:library_manifest_failed]" in out.getvalue()


def test_memoization_key_includes_mtime_and_size(sb, tmp_path, monkeypatch):
    """Modifying the underlying PKL forces a re-read because mtime_ns
    and/or size change.

    Slice 2b/4 amendment: the second write attaches a fresh manifest
    (which also overwrites the sidecar JSON) so the central loader's
    Phase 3A verification accepts the new payload. The cache-
    invalidation invariant being tested is unchanged.
    """
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "MOD_stable_v1.pkl"
    _write_lib(lib_path)
    first = sb.load_signal_library_for_stackbuilder("MOD")
    assert first is not None

    # Rewrite with a different payload + fresh manifest + bump mtime.
    lib_v2 = {
        "primary_signals": [1, 0, 0, 0],
        "dates": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        "tags": ["SHORT[D]"],
    }
    pm.attach_manifest(
        lib_v2,
        lib_path,
        artifact_type="signal_library_daily",
        ticker="MOD",
        interval="1d",
        params={
            "engine_version": "1.0.0",
            "MAX_SMA_DAY": 114,
            "price_source": "Close",
            "interval": "1d",
        },
        engine_version="1.0.0",
    )
    with open(lib_path, "wb") as fh:
        pickle.dump(lib_v2, fh)
    # Advance mtime by 2 seconds so the cache key (mtime_ns) changes.
    st = os.stat(lib_path)
    os.utime(lib_path, (st.st_atime + 2, st.st_mtime + 2))

    second = sb.load_signal_library_for_stackbuilder("MOD")
    assert second is not None
    assert second["tags"] == ["SHORT[D]"]


# ---------------------------------------------------------------------------
# Diagnostics + safety
# ---------------------------------------------------------------------------

def test_diagnostic_uses_stackbuilder_prefix_not_onepass(sb, tmp_path, monkeypatch):
    """Missing-library diagnostics use the ``[STACKBUILDER:*]`` prefix
    family, never ``[ONEPASS:*]`` or the word ``rebuild``."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))

    out = io.StringIO()
    with redirect_stdout(out):
        result = sb.load_signal_library_for_stackbuilder("NOPE")

    assert result is None
    text = out.getvalue()
    assert "[STACKBUILDER:library_missing]" in text
    assert "[ONEPASS:" not in text
    assert "Forcing rebuild" not in text
    assert "rebuild" not in text.lower()


def test_invalid_payload_emits_invalid_diagnostic(sb, tmp_path, monkeypatch):
    """A readable PKL with the wrong shape emits ``library_invalid``."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "BAD_stable_v1.pkl"
    lib_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lib_path, "wb") as fh:
        pickle.dump({"not_a_lib": True}, fh)

    out = io.StringIO()
    with redirect_stdout(out):
        result = sb.load_signal_library_for_stackbuilder("BAD")

    assert result is None
    assert "[STACKBUILDER:library_invalid]" in out.getvalue()


def test_consumer_loader_does_not_write_to_signal_lib_dir(sb, tmp_path, monkeypatch):
    """A load attempt never creates, modifies, or removes files under
    the signal-library directory."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "RO_stable_v1.pkl"
    _write_lib(lib_path)

    before = {p.name: os.stat(p) for p in tmp_path.iterdir()}
    sb.load_signal_library_for_stackbuilder("RO")
    sb.load_signal_library_for_stackbuilder("MISSING_TICKER_XYZ")
    after = {p.name: os.stat(p) for p in tmp_path.iterdir()}

    assert set(before.keys()) == set(after.keys())
    for name, st_before in before.items():
        st_after = after[name]
        assert st_before.st_size == st_after.st_size
        assert st_before.st_mtime_ns == st_after.st_mtime_ns


def test_load_lib_or_none_routes_through_consumer_loader(sb, tmp_path, monkeypatch):
    """``load_lib_or_none`` no longer calls OnePass first; it calls the
    consumer loader directly."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "ROUTE_stable_v1.pkl"
    _write_lib(lib_path)

    consumer_calls = {"n": 0}
    real_loader = sb.load_signal_library_for_stackbuilder

    def spy_loader(ticker):
        consumer_calls["n"] += 1
        return real_loader(ticker)

    monkeypatch.setattr(sb, "load_signal_library_for_stackbuilder", spy_loader)

    out = sb.load_lib_or_none("ROUTE")

    assert out is not None
    assert consumer_calls["n"] == 1


def test_fallback_load_signal_library_is_phase3a_consumer_entry_point(sb, tmp_path, monkeypatch):
    """``fallback_load_signal_library`` is the Phase 3A-named consumer
    entry point that the B12 AST guard inspects, and it routes through
    ``provenance_manifest.load_verified_signal_library`` per the
    restored Phase 3A consumer-verifies contract.

    Slice 2b/4 amendment: PR #290 had made this name a thin alias for
    ``load_signal_library_for_stackbuilder`` that bypassed manifest
    verification. The amendment reverses the alias direction so the
    B12-named function carries the verification call statically, and
    confirms that both names continue to return the same library
    object for a valid input (so callers pinned on either name keep
    working).
    """
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "ALIAS_stable_v1.pkl"
    _write_lib(lib_path)

    via_fallback = sb.fallback_load_signal_library("ALIAS")
    via_forward = sb.load_signal_library_for_stackbuilder("ALIAS")
    assert via_fallback is not None
    assert via_forward is not None
    assert via_fallback["primary_signals"] == [0, 1, -1, 0]
    assert via_forward["primary_signals"] == [0, 1, -1, 0]
    assert via_fallback is via_forward


# ---------------------------------------------------------------------------
# Part 3: --skip-durable-validation gate
# ---------------------------------------------------------------------------

def test_skip_validation_summary_has_all_locked_keys(sb):
    """``_build_skipped_validation_summary`` returns a Mapping that
    satisfies the locked-10 schema gate."""
    summary = sb._build_skipped_validation_summary()
    # The validator raises if any locked key is missing.
    sb._validate_stackbuilder_validation_summary(summary)
    assert summary["validation_status"] == "skipped"
    assert summary["validation_artifact_path"] is None
    assert summary["validation_artifact_hash"] is None
    assert summary["n_strategies_tested"] is None
    assert summary["n_strategies_reported"] is None
    assert summary["skip_reason"] == "operator_flag"


def test_skip_validation_summary_custom_reason(sb):
    """Custom skip reasons propagate to the summary."""
    summary = sb._build_skipped_validation_summary(skip_reason="runner_request")
    assert summary["skip_reason"] == "runner_request"
    assert summary["validation_status"] == "skipped"


def test_skip_validation_completion_line_text(sb):
    """Completion line clearly says SKIPPED with the reason and never
    fabricates a sidecar path."""
    summary = sb._build_skipped_validation_summary()
    lines = sb._stackbuilder_validation_completion_lines(summary)
    assert any("SKIPPED" in ln and "operator_flag" in ln for ln in lines)
    assert all("Sidecar:" not in ln for ln in lines)


def test_parse_args_skip_durable_validation_default_off(sb):
    """``--skip-durable-validation`` defaults to False so the Phase 5C
    fail-closed contract is preserved without the explicit opt-in."""
    args = sb.parse_args(["--secondary", "SPY"])
    assert args.skip_durable_validation is False


def test_parse_args_skip_durable_validation_explicit(sb):
    """When the flag is set, ``args.skip_durable_validation`` is True."""
    args = sb.parse_args(["--secondary", "SPY", "--skip-durable-validation"])
    assert args.skip_durable_validation is True


def test_stable_cli_args_subset_includes_skip_flag(sb):
    """The manifest fingerprint subset records the skip-validation
    flag so re-runs produce the same fingerprint only when the flag
    matches."""
    args = sb.parse_args(["--secondary", "SPY", "--skip-durable-validation"])
    subset = sb._stable_cli_args_subset(args)
    assert subset["skip_durable_validation"] is True

    args2 = sb.parse_args(["--secondary", "SPY"])
    subset2 = sb._stable_cli_args_subset(args2)
    assert subset2["skip_durable_validation"] is False


# ---------------------------------------------------------------------------
# Part 3: runner-side --skip-durable-validation plumbing
# ---------------------------------------------------------------------------

def test_runner_parse_args_skip_durable_validation_default_off():
    """``stackbuilder_workbook_runner --skip-durable-validation`` is
    OFF by default."""
    runner = importlib.import_module("stackbuilder_workbook_runner")
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--primaries", "AROW",
    ])
    assert args.skip_durable_validation is False


def test_runner_parse_args_skip_durable_validation_explicit():
    """Explicit ``--skip-durable-validation`` sets the namespace flag."""
    runner = importlib.import_module("stackbuilder_workbook_runner")
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--primaries", "AROW",
        "--skip-durable-validation",
    ])
    assert args.skip_durable_validation is True


def test_runner_effective_config_includes_skip_flag():
    """``_effective_config`` carries the skip flag for dry-run JSON."""
    runner = importlib.import_module("stackbuilder_workbook_runner")
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--primaries", "AROW",
        "--skip-durable-validation",
    ])
    cfg = runner._effective_config(args)
    assert cfg["skip_durable_validation"] is True


def test_runner_engine_args_carry_skip_flag():
    """The SimpleNamespace handed to ``stackbuilder.run_for_secondary``
    carries ``skip_durable_validation``."""
    runner = importlib.import_module("stackbuilder_workbook_runner")
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--primaries", "AROW",
        "--skip-durable-validation",
    ])
    ns = runner.build_stackbuilder_args_namespace(
        args,
        secondary="SPY",
        primaries_resolution={"primary_source": "impact_xlsx"},
    )
    assert ns.skip_durable_validation is True


def test_runner_dry_run_plan_per_secondary_carries_skip_flag():
    """The dry-run plan ``per_secondary_plan`` entries expose the flag."""
    runner = importlib.import_module("stackbuilder_workbook_runner")
    args = runner.parse_args([
        "--secondaries", "SPY",
        "--primaries", "AROW",
        "--skip-durable-validation",
        "--duration-budget-minutes", "10",
        "--operator-budget-label", "phase_6i75_test",
    ])
    plan = runner.build_run_plan(
        args,
        secondaries_resolution={
            "status": "ok",
            "secondaries": ["SPY"],
        },
        primaries_resolution={
            "status": "ok",
            "primary_source": "impact_xlsx",
            "primary_count": 1,
            "primaries": ["AROW"],
        },
    )
    assert plan["effective_config"]["skip_durable_validation"] is True
    assert plan["per_secondary_plan"][0]["skip_durable_validation"] is True


# ---------------------------------------------------------------------------
# Amendment 3: hotpath benchmark harness schema + calibration coverage
# ---------------------------------------------------------------------------

_HOTPATH_REQUIRED_TOP_LEVEL_KEYS = (
    "per_combo_ms_current_measured",
    "current_sample_iterations",
    "current_extrapolated_to_102050_seconds",
    "observed_phase6i74_ms_per_combo",
    "per_combo_ms_legacy_synthetic_baseline",
    "per_combo_ms_target",
    "overhead_decomposition",
    "dominant_source",
    "dominant_source_share",
    "fast_helper_attempted",
    "per_combo_ms_fast_measured",
    "fast_sample_iterations",
    "fast_helper_wired",
    "reason_not_wired",
)


@pytest.fixture
def hotpath_result(tmp_path):
    """Run the benchmark once at a tiny iteration count and return the
    parsed JSON. Used by every Amendment 3 schema test."""
    bench = importlib.import_module(
        "test_scripts.bench_phase_6i75_hotpath_decomposition"
    )
    out_path = tmp_path / "phase_6i75_hotpath_decomposition.json"
    rc = bench.main([
        "--out", str(out_path),
        "--current-sample-iterations", "2",
        "--years", "1",
        "--k", "2",
        "--warmup-iterations", "1",
        "--max-wall-seconds", "900",
    ])
    assert rc == 0
    assert out_path.is_file()
    return json.loads(out_path.read_text(encoding="utf-8"))


def test_hotpath_harness_required_top_level_keys(hotpath_result):
    """All Phase 6I-75 spec keys are present at the top level."""
    missing = [k for k in _HOTPATH_REQUIRED_TOP_LEVEL_KEYS
               if k not in hotpath_result]
    assert missing == [], f"missing keys: {missing!r}"


def test_hotpath_harness_constants_match_spec(hotpath_result):
    """The three locked constants from the prompt are emitted verbatim."""
    assert hotpath_result["observed_phase6i74_ms_per_combo"] == 187.4
    assert hotpath_result["per_combo_ms_legacy_synthetic_baseline"] == 5.94
    assert hotpath_result["per_combo_ms_target"] == 18.0


def test_hotpath_harness_reason_not_wired_present_when_not_wired(hotpath_result):
    """When fast_helper_wired is False, reason_not_wired is a non-empty
    string explaining the data-driven decision."""
    assert hotpath_result["fast_helper_wired"] is False
    assert hotpath_result["fast_helper_attempted"] is False
    reason = hotpath_result["reason_not_wired"]
    assert isinstance(reason, str)
    assert reason.strip()
    assert hotpath_result["per_combo_ms_fast_measured"] is None
    assert hotpath_result["fast_sample_iterations"] is None


def test_hotpath_harness_dominant_source_populated(hotpath_result):
    """The decomposition picks a dominant source and reports its share.

    Phase 6I-76 note: the Phase 6I-75 harness times the canonical
    ``combine_consensus_signals`` reference in isolation as one of its
    components, but the StackBuilder phase3 production path now uses
    ``_combine_signals_fast`` instead. After 6I-76 the standalone
    canonical timing can therefore exceed the full
    ``_combined_metrics_signals`` per-combo median, so the
    dominant-source share is just required to be a positive number.
    """
    decomposition = hotpath_result["overhead_decomposition"]
    assert isinstance(decomposition, dict)
    assert decomposition, "overhead_decomposition is empty"
    assert hotpath_result["dominant_source"] in decomposition
    share = hotpath_result["dominant_source_share"]
    assert share is not None
    assert share > 0.0


def test_hotpath_harness_calibration_metadata_present(hotpath_result):
    """Calibrated-sampling metadata is recorded for future audits."""
    meta = hotpath_result["metadata"]
    assert meta["warmup_iterations"] >= 1
    assert meta["k_members"] >= 1
    assert meta["max_wall_seconds"] > 0
    assert meta["phase_6i74_combo_count"] == 102050
    assert "calibration_note" in meta
    assert "extrapolation_method" in meta
    assert isinstance(meta["halted_for_budget"], bool)
    assert hotpath_result["current_sample_iterations"] >= 1


def test_hotpath_harness_extrapolation_is_consistent(hotpath_result):
    """``current_extrapolated_to_102050_seconds`` matches the documented
    formula ``per_combo_ms * 102050 / 1000``."""
    expected = (
        hotpath_result["per_combo_ms_current_measured"] * 102050 / 1000.0
    )
    actual = hotpath_result["current_extrapolated_to_102050_seconds"]
    assert abs(actual - expected) < 1e-6


def test_hotpath_harness_json_no_local_paths(hotpath_result):
    """The verdict JSON must not embed local absolute paths, drive
    letters, usernames, or conda paths.

    Forbidden tokens are reconstructed from split string literals at
    test time so this source file does not itself contain the
    privacy-scan needles. (Without the split, the privacy scanner
    would always match the literal needles inside this test, even
    though the runtime check is correct.)
    """
    text = json.dumps(hotpath_result)
    forbidden = [
        "spo" + "rt",
        "NV" + "IDIA",
        "Mini" + "Conda",
        "App" + "Data",
        "spy" + "project2",
        "/Us" + "ers/",
        "C" + ":\\",
        "C" + ":/",
    ]
    leaks = [needle for needle in forbidden if needle in text]
    assert leaks == [], f"privacy leaks in verdict JSON: {leaks!r}"


def test_hotpath_harness_budget_ceiling_is_enforced(tmp_path):
    """A tiny ``--max-wall-seconds`` budget flips ``halted_for_budget``
    on without crashing the harness — the JSON is still emitted with
    the projection metadata so an operator can see WHY the run halted."""
    bench = importlib.import_module(
        "test_scripts.bench_phase_6i75_hotpath_decomposition"
    )
    out_path = tmp_path / "halted.json"
    rc = bench.main([
        "--out", str(out_path),
        "--current-sample-iterations", "2",
        "--years", "1",
        "--k", "2",
        "--warmup-iterations", "1",
        # 0.0001 seconds = effectively zero; projection of any
        # measurable per-combo cost over 102050 iterations will exceed.
        "--max-wall-seconds", "0.0001",
    ])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["halted_for_budget"] is True
    assert "projected" in payload["metadata"]["calibration_note"]


# ---------------------------------------------------------------------------
# Amendment 4: run_for_secondary skip-gate isolation
# ---------------------------------------------------------------------------


def _build_minimal_skip_args(
    *,
    secondary: str = "SPY",
    outdir: Path,
    skip: bool,
):
    """Construct the smallest plausible argparse.Namespace-like object
    that ``run_for_secondary`` can consume. Each engine-internal phase
    is stubbed out in the test body so this namespace only needs to
    expose the attributes the test path actually reads."""
    from types import SimpleNamespace
    return SimpleNamespace(
        secondary=secondary,
        secondaries=None,
        primaries="STUB",
        signal_lib_dir=None,
        impact_xlsx_dir=None,
        impact_xlsx_max_age_days=45,
        prefer_impact_xlsx=False,
        strict_manifests=False,
        outdir=str(outdir),
        output_format="xlsx",
        top_n=1,
        bottom_n=1,
        max_k=1,
        exhaustive_k=1,
        min_trigger_days=1,
        sharpe_eps=1e-6,
        seed_by="total_capture",
        optimize_by="total_capture",
        allow_decreasing=False,
        search="beam",
        beam_width=1,
        both_modes=False,
        k_patience=0,
        combine_mode="intersection",
        grace_days=None,
        threads="auto",
        jobs=1,
        verbose=False,
        no_progress=True,
        save_stats=False,
        fail_on_missing_cache=False,
        serve=False,
        port=8054,
        alpha=0.05,
        min_marginal_capture=0.0,
        progress_path=None,
        skip_durable_validation=skip,
    )


def _install_engine_stubs(sb_module, monkeypatch):
    """Stub out every side-effecting StackBuilder function that
    ``run_for_secondary`` would normally invoke so the test exercises
    only the validation gate. No I/O, no engine work, no canonical
    artifact writes.

    Returns the in-memory primaries / rank / leaderboard objects that
    the stubs hand back, in case the test wants to assert on them.
    """
    import pandas as _pd

    primaries = ["AROW", "AWR"]
    primaries_df = _pd.DataFrame({"Primary Ticker": primaries})
    idx = _pd.bdate_range("2024-01-02", periods=10)
    sec_df = _pd.DataFrame({"Close": _pd.Series(range(1, 11), index=idx)})
    sec_rets = sb_module.pct_returns(sec_df["Close"])
    vendor_secondary = "SPY"

    def _fake_phase1_preflight(args, secondary, specified_primaries=None):
        return primaries_df, sec_rets, vendor_secondary
    monkeypatch.setattr(sb_module, "phase1_preflight", _fake_phase1_preflight)

    rank_df = _pd.DataFrame({
        "Primary Ticker": primaries,
        "Total Capture (%)": [10.0, 5.0],
        "Sharpe Ratio": [0.5, 0.3],
        "Trigger Days": [100, 80],
        "Mode": ["D", "D"],
    })

    def _fake_phase2_rank_all(
        args, primaries_df_in, sec_rets_in, outdir,
        secondary=None, progress_path=None, *,
        grace_days=None, data_available_through=None,
    ):
        return rank_df, rank_df.copy(), rank_df.iloc[:0].copy()
    monkeypatch.setattr(sb_module, "phase2_rank_all", _fake_phase2_rank_all)

    # Non-empty leaderboard so the run_for_secondary summary path,
    # which formats ``best_sharpe``/``best_capture``/``best_trigger_days``
    # with f-string ``:.3f`` / ``:.2f``, has real numbers to print. The
    # actual values don't matter for the skip-gate assertions.
    leaderboard_df = _pd.DataFrame({
        "Sharpe Ratio": [0.5],
        "Total Capture (%)": [10.0],
        "Trigger Days": [100],
    })
    final_members: list = []

    def _fake_phase3_build_stacks(
        args, rank_direct, rank_inverse, sec_rets_in, outdir,
        progress_cb=None, *, grace_days=None,
        data_available_through=None, validation_collector=None,
    ):
        return leaderboard_df, final_members
    monkeypatch.setattr(sb_module, "phase3_build_stacks", _fake_phase3_build_stacks)

    # Make XLSX writes no-ops so the test doesn't depend on Excel
    # engines and can't accidentally create canonical artifact files.
    def _noop_write_table(df, basepath):
        return None
    monkeypatch.setattr(sb_module, "write_table", _noop_write_table)

    return {
        "primaries_df": primaries_df,
        "rank_df": rank_df,
        "leaderboard_df": leaderboard_df,
    }


@pytest.mark.parametrize("skip", [True, False])
def test_run_for_secondary_skip_gate_controls_validation_path(
    sb, tmp_path, monkeypatch, skip,
):
    """End-to-end skip-gate proof against ``run_for_secondary`` with
    phase1/2/3 + writers monkeypatched.

    When ``args.skip_durable_validation=True``:

      * ``_prepare_stackbuilder_durable_validation`` is NEVER called.
      * The manifest carries ``durable_validation_status='skipped'`` and
        ``durable_validation_skip_reason='operator_flag'``.
      * The locked-10 ``validation_status`` is ``'skipped'``.
      * No durable validation sidecar is written.

    When ``args.skip_durable_validation=False``: the spy IS called and
    the run records ``durable_validation_status='ran'`` with whatever
    summary the spy returns.
    """
    outdir = tmp_path / "out"
    outdir.mkdir()

    _install_engine_stubs(sb, monkeypatch)

    # Spy on the four validation surfaces named by the spec.
    spy_calls: dict = {
        "_prepare_stackbuilder_durable_validation": 0,
        "StackBuilderValidationAdapter_init": 0,
        "validate_strategy_set": 0,
        "write_validation_sidecar": 0,
    }

    def _spy_prepare(**kwargs):
        spy_calls["_prepare_stackbuilder_durable_validation"] += 1
        # Synthesize a minimal "ran" summary so the non-skip branch
        # can complete; ``_validate_stackbuilder_validation_summary``
        # only checks key presence.
        summary = {
            "validation_contract_version": "v1",
            "validation_status": "valid",
            "n_strategies_tested": 0,
            "n_strategies_reported": 0,
            "multiple_comparisons_control_method": "BH",
            "multiple_comparisons_control_alpha": 0.05,
            "walk_forward_n_folds": 0,
            "mean_baseline_sharpe": None,
            "validation_artifact_path": str(tmp_path / "sidecar.json"),
            "validation_artifact_hash": "0" * 64,
        }
        return ({}, summary, str(tmp_path / "sidecar.json"))
    monkeypatch.setattr(
        sb, "_prepare_stackbuilder_durable_validation", _spy_prepare,
    )

    # Spy on StackBuilderValidationAdapter constructor and
    # validate_strategy_set / write_validation_sidecar at the import
    # sites StackBuilder uses; the skip path must not touch them.
    import validation_engine as _ve_module

    class _SpyAdapter:
        def __init__(self, *a, **kw):
            spy_calls["StackBuilderValidationAdapter_init"] += 1

    # StackBuilder imports StackBuilderValidationAdapter at module
    # load time; patch the binding on stackbuilder itself.
    if hasattr(sb, "StackBuilderValidationAdapter"):
        monkeypatch.setattr(
            sb, "StackBuilderValidationAdapter", _SpyAdapter,
        )

    def _spy_validate_strategy_set(*a, **kw):
        spy_calls["validate_strategy_set"] += 1
        return None
    if hasattr(sb, "validate_strategy_set"):
        monkeypatch.setattr(
            sb, "validate_strategy_set", _spy_validate_strategy_set,
        )
    if hasattr(_ve_module, "validate_strategy_set"):
        monkeypatch.setattr(
            _ve_module, "validate_strategy_set", _spy_validate_strategy_set,
        )

    def _spy_write_sidecar(*a, **kw):
        spy_calls["write_validation_sidecar"] += 1
        return None
    if hasattr(sb, "write_validation_sidecar"):
        monkeypatch.setattr(
            sb, "write_validation_sidecar", _spy_write_sidecar,
        )
    if hasattr(_ve_module, "write_validation_sidecar"):
        monkeypatch.setattr(
            _ve_module, "write_validation_sidecar", _spy_write_sidecar,
        )

    args_ns = _build_minimal_skip_args(outdir=outdir, skip=skip)
    # Set the progress path under tmp_path so the engine doesn't try
    # to write to the canonical PROGRESS_ROOT location.
    args_ns.progress_path = str(tmp_path / "progress.json")

    final_outdir = sb.run_for_secondary(args_ns, "SPY")
    # Read the manifest the engine just wrote.
    manifest_path = Path(final_outdir) / "run_manifest.json"
    assert manifest_path.is_file(), (
        f"run_for_secondary did not produce a run_manifest at {manifest_path}"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if skip:
        # Spec gate — none of these surfaces should have been touched.
        assert spy_calls["_prepare_stackbuilder_durable_validation"] == 0
        assert spy_calls["StackBuilderValidationAdapter_init"] == 0
        assert spy_calls["validate_strategy_set"] == 0
        assert spy_calls["write_validation_sidecar"] == 0
        assert manifest["durable_validation_status"] == "skipped"
        assert manifest["durable_validation_skip_reason"] == "operator_flag"
        assert manifest["validation_status"] == "skipped"
        assert manifest["validation_artifact_path"] is None
        assert manifest["validation_artifact_hash"] is None
    else:
        # The non-skip path still routes through the spy (proof the
        # skip flag is the ONLY thing controlling the branch).
        assert spy_calls["_prepare_stackbuilder_durable_validation"] == 1
        assert manifest["durable_validation_status"] == "ran"
        assert manifest["durable_validation_skip_reason"] is None
        assert manifest["validation_status"] == "valid"
