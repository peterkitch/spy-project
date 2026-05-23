"""Phase 6I-75: StackBuilder consumer-only signal-library loader tests.

These tests pin the consumer-loader contract introduced in Phase 6I-75:

  * StackBuilder must NOT import or call ``onepass.load_signal_library``
    or any other OnePass entry point from its signal-library load path.
  * Library loads must go through ``load_signal_library_for_stackbuilder``
    (and the legacy aliases ``fallback_load_signal_library`` /
    ``load_lib_or_none``), all of which route through a direct
    ``pickle.load`` and accept readable libraries even when manifest
    metadata is stale or missing.
  * Loads must be memoized per-run on both success and failure, keyed by
    file identity (path + mtime_ns + size).
  * Diagnostics must use the ``[STACKBUILDER:*]`` prefix family — never
    ``[ONEPASS:*]`` and never ``Forcing rebuild``.
  * No network call, PKL write, or manifest write is permitted from the
    consumer load path.
"""

from __future__ import annotations

import ast
import importlib
import io
import os
import pickle
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


@pytest.fixture
def sb():
    """Fresh-imported stackbuilder module with consumer-loader cache cleared."""
    module = importlib.import_module("stackbuilder")
    module._reset_consumer_loader_cache()
    yield module
    module._reset_consumer_loader_cache()


def _write_lib(path: Path, with_manifest: bool = True) -> dict:
    """Write a minimal-but-valid signal-library PKL at ``path``.

    Schema mirrors the OnePass on-disk shape that StackBuilder
    consumes: ``primary_signals`` is a 1D scalar-per-day sequence
    parallel to ``dates``.
    """
    lib = {
        "primary_signals": [0, 1, -1, 0],
        "dates": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        "tags": ["BUY[D]"],
    }
    if with_manifest:
        lib["_manifest"] = {
            "content_hash": "deadbeef" * 8,
            "schema_version": 1,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
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


def test_memoization_key_includes_mtime_and_size(sb, tmp_path, monkeypatch):
    """Modifying the underlying PKL forces a re-read because mtime_ns
    and/or size change."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "MOD_stable_v1.pkl"
    _write_lib(lib_path)
    first = sb.load_signal_library_for_stackbuilder("MOD")
    assert first is not None

    # Rewrite with a different payload + bump mtime.
    lib_v2 = {
        "primary_signals": [1, 0, 0, 0],
        "dates": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
        "tags": ["SHORT[D]"],
    }
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


def test_fallback_load_signal_library_is_alias(sb, tmp_path, monkeypatch):
    """``fallback_load_signal_library`` aliases the consumer loader and
    no longer routes through provenance manifest verification."""
    monkeypatch.setattr(sb, "SIGNAL_LIB_DIR_RUNTIME", str(tmp_path))
    lib_path = tmp_path / "ALIAS_stable_v1.pkl"
    _write_lib(lib_path)

    loaded = sb.fallback_load_signal_library("ALIAS")
    assert loaded is not None
    assert loaded["primary_signals"] == [0, 1, -1, 0]


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
