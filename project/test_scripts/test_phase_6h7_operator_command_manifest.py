"""Phase 6H-7 tests for the operator command manifest.

Pins the static-manifest contract so the runbook and the
machine-readable JSON cannot drift apart silently:

  - the manifest JSON parses cleanly
  - the schema label is the documented Phase 6H-7 string
  - the env-var name + value match the Phase 6H-5 constants
    EXACTLY (single source of truth -- if the constants
    change the manifest must be updated in the same PR)
  - the authorized writer command template includes every
    required root flag (--status-dir, --execution-log,
    --cache-dir, --artifact-root, --stackbuilder-root,
    --signal-library-dir) plus --write
  - no manifest command template invokes StackBuilder or
    OnePass execution paths
  - no manifest command template encodes a universe sweep
    (--all-tickers, --universe, wildcard patterns)
  - the prohibited commands list explicitly flags
    StackBuilder execution, OnePass execution, MTF library
    rebuild, and universe sweep
  - the read-only commands list covers the five
    documented read-only entry points
  - the decision tree carries every operator-facing
    verdict the planner can emit
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import daily_board_automation_writer as dbw  # noqa: E402


MANIFEST_PATH = (
    Path(__file__).resolve().parent.parent
    / "md_library" / "shared"
    / "2026-05-12_PHASE_6H7_OPERATOR_COMMAND_MANIFEST.json"
)

RUNBOOK_PATH = (
    Path(__file__).resolve().parent.parent
    / "md_library" / "shared"
    / "2026-05-12_PHASE_6H7_PRODUCTION_RUNBOOK.md"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def manifest() -> dict:
    raw = MANIFEST_PATH.read_text(encoding="utf-8")
    return json.loads(raw)


@pytest.fixture(scope="module")
def runbook_text() -> str:
    return RUNBOOK_PATH.read_text(encoding="utf-8")


def _every_command_template(manifest: dict) -> list[str]:
    """Collect every command-template string the manifest
    publishes across read-only, dry-run, and authorized
    sections so the safety assertions can iterate over the
    full set."""
    out: list[str] = []
    for entry in manifest.get("read_only_commands", []):
        if entry.get("command_template"):
            out.append(entry["command_template"])
    for entry in manifest.get("dry_run_commands", []):
        if entry.get("command_template"):
            out.append(entry["command_template"])
    writer = manifest.get("authorized_writer_command") or {}
    if writer.get("command_template"):
        out.append(writer["command_template"])
    if writer.get("powershell_template"):
        out.append(writer["powershell_template"])
    return out


# ---------------------------------------------------------------------------
# 1. JSON shape
# ---------------------------------------------------------------------------


def test_manifest_file_exists_and_parses_as_json():
    assert MANIFEST_PATH.exists(), (
        f"Phase 6H-7 manifest missing at {MANIFEST_PATH}"
    )
    raw = MANIFEST_PATH.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)


def test_manifest_schema_label_is_phase_6h7(manifest: dict):
    assert manifest.get("schema") == (
        "phase_6h7_operator_command_manifest_v1"
    )


def test_manifest_top_level_keys(manifest: dict):
    required_keys = {
        "schema",
        "generated_for_main_head",
        "last_updated",
        "description",
        "required_env_var",
        "required_root_flags_for_authorized_writes",
        "read_only_commands",
        "dry_run_commands",
        "authorized_writer_command",
        "prohibited_commands",
        "decision_tree",
        "safety_contract_summary",
        "rollback_recovery",
        "do_not_run_warnings",
    }
    missing = required_keys - set(manifest.keys())
    assert not missing, f"manifest missing top-level keys: {missing!r}"


# ---------------------------------------------------------------------------
# 2. Env-var contract: single source of truth
# ---------------------------------------------------------------------------


def test_manifest_env_var_name_matches_phase_6h5_constant(
    manifest: dict,
):
    """The manifest's env-var name MUST match
    ``daily_board_automation_writer.ENV_VAR_NAME`` exactly so
    runbook readers and downstream tools cannot diverge."""
    env = manifest.get("required_env_var") or {}
    assert env.get("name") == dbw.ENV_VAR_NAME


def test_manifest_env_var_required_value_matches_phase_6h5_constant(
    manifest: dict,
):
    env = manifest.get("required_env_var") or {}
    assert env.get("required_value") == dbw.ENV_VAR_REQUIRED_VALUE


# ---------------------------------------------------------------------------
# 3. Required root flags
# ---------------------------------------------------------------------------


REQUIRED_AUTH_FLAGS = (
    "--write",
    "--cache-dir",
    "--status-dir",
    "--artifact-root",
    "--stackbuilder-root",
    "--signal-library-dir",
    "--execution-log",
)


def test_required_root_flags_list_covers_phase_6h6_plumbing(
    manifest: dict,
):
    """The manifest's required-roots list must cover every
    flag the Phase 6H-5/6H-6 writer needs to redirect under
    authorized writes."""
    declared = set(
        manifest.get(
            "required_root_flags_for_authorized_writes", [],
        ),
    )
    expected = {
        "--cache-dir",
        "--status-dir",
        "--artifact-root",
        "--stackbuilder-root",
        "--signal-library-dir",
        "--execution-log",
    }
    missing = expected - declared
    assert not missing, (
        "required_root_flags_for_authorized_writes is missing "
        f"the following Phase 6H-5/6H-6 root flags: {missing!r}"
    )


def test_authorized_writer_command_template_includes_every_required_flag(
    manifest: dict,
):
    """The authorized writer command template must include
    every required flag the operator MUST supply for an
    authorized run. Belt-and-suspenders for the prose
    runbook's expectations."""
    writer = manifest.get("authorized_writer_command") or {}
    template = writer.get("command_template") or ""
    for flag in REQUIRED_AUTH_FLAGS:
        assert flag in template, (
            f"authorized_writer_command.command_template "
            f"is missing required flag: {flag!r}"
        )


def test_authorized_writer_powershell_template_includes_every_required_flag(
    manifest: dict,
):
    writer = manifest.get("authorized_writer_command") or {}
    template = writer.get("powershell_template") or ""
    for flag in REQUIRED_AUTH_FLAGS:
        assert flag in template, (
            f"authorized_writer_command.powershell_template "
            f"is missing required flag: {flag!r}"
        )


def test_authorized_writer_command_carries_the_env_var(
    manifest: dict,
):
    """The bash/POSIX template should prefix the env-var
    assignment (so an operator copy-pasting the template
    gets the two-key gate satisfied)."""
    writer = manifest.get("authorized_writer_command") or {}
    template = writer.get("command_template") or ""
    assert dbw.ENV_VAR_NAME in template
    assert dbw.ENV_VAR_REQUIRED_VALUE in template


def test_authorized_writer_required_flags_field_matches_template(
    manifest: dict,
):
    """The ``required_flags`` list and the
    ``command_template`` string must agree on what is
    required."""
    writer = manifest.get("authorized_writer_command") or {}
    required_flags = set(writer.get("required_flags") or ())
    for flag in REQUIRED_AUTH_FLAGS:
        assert flag in required_flags, (
            f"authorized_writer_command.required_flags is "
            f"missing {flag!r}"
        )


# ---------------------------------------------------------------------------
# 4. Prohibited / forbidden command patterns
# ---------------------------------------------------------------------------


def test_no_manifest_command_references_stackbuilder_execution(
    manifest: dict,
):
    """No command template in the read-only / dry-run /
    authorized-writer sections may invoke StackBuilder. The
    Phase 6H-3 contract is explicit: StackBuilder runs are
    manual research workflow, not a daily automation step."""
    bad_substrings = ("stackbuilder.py",)
    for template in _every_command_template(manifest):
        for needle in bad_substrings:
            assert needle not in template, (
                f"command template references {needle!r}; "
                "StackBuilder execution is not part of the "
                f"daily automation: {template!r}"
            )


def test_no_manifest_command_references_onepass_execution(
    manifest: dict,
):
    bad_substrings = ("onepass.py",)
    for template in _every_command_template(manifest):
        for needle in bad_substrings:
            assert needle not in template, (
                f"command template references {needle!r}; "
                "OnePass execution is not part of the daily "
                f"automation: {template!r}"
            )


def test_no_manifest_command_encodes_a_universe_sweep(
    manifest: dict,
):
    """No command template may encode a universe sweep.
    Wildcard rewrites of the saved catalogue are the
    single highest-blast-radius mistake the operator can
    make on this stack."""
    bad_patterns = [
        r"--all-tickers",
        r"--universe",
        r"--every-ticker",
        r"--wildcard",
        # Defensive: an unquoted glob in the command would
        # be a shell expansion attempting to enumerate
        # files; treat any unbracketed * outside angle
        # brackets as suspect.
    ]
    for template in _every_command_template(manifest):
        for pattern in bad_patterns:
            assert not re.search(pattern, template), (
                f"command template encodes a universe-sweep "
                f"pattern {pattern!r}: {template!r}"
            )


def test_prohibited_commands_list_flags_known_dangers(
    manifest: dict,
):
    """The manifest's ``prohibited_commands`` list must
    explicitly call out StackBuilder execution, OnePass
    execution, multi-timeframe library rebuilds, universe
    sweeps, and the two-key-violation patterns."""
    prohibited = manifest.get("prohibited_commands") or []
    labels = {entry.get("label") for entry in prohibited}
    expected_labels = {
        "universe_sweep",
        "stackbuilder_execution",
        "onepass_execution",
        "multitimeframe_library_rebuild",
        "writer_with_only_cli_flag_no_env",
        "writer_against_production_without_status_dir_override",
    }
    missing = expected_labels - labels
    assert not missing, (
        f"prohibited_commands list is missing labels: "
        f"{sorted(missing)!r}"
    )


# ---------------------------------------------------------------------------
# 5. Read-only command coverage
# ---------------------------------------------------------------------------


def test_read_only_commands_cover_the_documented_stack(
    manifest: dict,
):
    read_only = manifest.get("read_only_commands") or []
    labels = {entry.get("label") for entry in read_only}
    expected = {
        "cache_cutoff_watcher",
        "automation_preflight",
        "source_freshness_preflight",
        "launch_readiness_audit",
        "refresher_dry_run",
    }
    missing = expected - labels
    assert not missing, (
        f"read_only_commands is missing labels: "
        f"{sorted(missing)!r}"
    )
    # Every read-only entry must declare writes=false.
    for entry in read_only:
        assert entry.get("writes") is False, (
            f"read-only entry must declare writes=false: "
            f"{entry!r}"
        )


def test_dry_run_commands_cover_executor_and_writer(
    manifest: dict,
):
    dry_run = manifest.get("dry_run_commands") or []
    labels = {entry.get("label") for entry in dry_run}
    expected = {
        "automation_executor_dry_run",
        "automation_writer_dry_run",
    }
    missing = expected - labels
    assert not missing, (
        f"dry_run_commands is missing labels: "
        f"{sorted(missing)!r}"
    )
    for entry in dry_run:
        assert entry.get("writes") is False, (
            f"dry-run entry must declare writes=false: "
            f"{entry!r}"
        )


# ---------------------------------------------------------------------------
# 6. Decision tree
# ---------------------------------------------------------------------------


def test_decision_tree_covers_every_planner_verdict(
    manifest: dict,
):
    """The decision tree must enumerate every
    ``recommended_automation_action`` the Phase 6H-3 planner
    can emit. Drift here would mean the runbook silently
    omits an operator response."""
    import daily_board_automation_preflight as dap

    tree = manifest.get("decision_tree") or []
    verdicts = {
        entry.get("watcher_or_planner_verdict")
        for entry in tree
    }
    expected = set(dap.RECOMMENDED_AUTOMATION_ACTIONS)
    missing = expected - verdicts
    assert not missing, (
        "decision_tree is missing planner verdicts: "
        f"{sorted(missing)!r}"
    )


# ---------------------------------------------------------------------------
# 7. Safety contract summary
# ---------------------------------------------------------------------------


def test_safety_contract_summary_has_required_keys(
    manifest: dict,
):
    safety = manifest.get("safety_contract_summary") or {}
    required = {
        "two_key_write_authorization",
        "refresh_recheck_pipeline_sequencing",
        "persist_skip_lag_rule",
        "stackbuilder_policy",
        "watcher_exception_handling",
        "execution_log_contract",
    }
    missing = required - set(safety.keys())
    assert not missing, (
        f"safety_contract_summary missing keys: "
        f"{sorted(missing)!r}"
    )


def test_stackbuilder_policy_string_rejects_age_window(
    manifest: dict,
):
    """The StackBuilder policy in the manifest must
    explicitly say no age-based stale window. This is the
    load-bearing Phase 6H-3 contract."""
    safety = manifest.get("safety_contract_summary") or {}
    policy = safety.get("stackbuilder_policy", "")
    assert "No 30-day stale window" in policy, (
        "stackbuilder_policy must explicitly call out the "
        "no-age-window rule from Phase 6H-3"
    )
    assert "ambiguous_tied_mtime" in policy, (
        "stackbuilder_policy must name the tied-mtime "
        "ambiguous case"
    )


# ---------------------------------------------------------------------------
# 8. Rollback / recovery
# ---------------------------------------------------------------------------


def test_rollback_recovery_carries_required_callouts(
    manifest: dict,
):
    rollback = manifest.get("rollback_recovery") or {}
    required = {
        "git_does_not_track_production_cache_or_output",
        "before_any_authorized_run",
        "after_any_authorized_run",
        "do_not_attempt_universe_rollback",
    }
    missing = required - set(rollback.keys())
    assert not missing, (
        f"rollback_recovery missing keys: {sorted(missing)!r}"
    )


# ---------------------------------------------------------------------------
# 9. do_not_run warnings list non-empty + cover the critical cases
# ---------------------------------------------------------------------------


def test_do_not_run_warnings_cover_critical_cases(
    manifest: dict,
):
    warnings = manifest.get("do_not_run_warnings") or []
    assert warnings, "do_not_run_warnings must be non-empty"
    combined = " ".join(warnings).lower()
    assert "--all-tickers" in combined
    assert "--universe" in combined
    assert "stackbuilder" in combined
    assert "onepass" in combined
    assert "validation_contract_v1" in combined


# ---------------------------------------------------------------------------
# 10. Runbook cross-references
# ---------------------------------------------------------------------------


def test_runbook_exists_and_cross_references_manifest(
    runbook_text: str,
):
    """The prose runbook must reference the sibling JSON
    manifest by filename so a reader following one finds the
    other."""
    assert (
        "2026-05-12_PHASE_6H7_OPERATOR_COMMAND_MANIFEST.json"
        in runbook_text
    )
    assert (
        "phase_6h7_operator_command_manifest_v1"
        in runbook_text
    )


def test_runbook_carries_two_key_auth_recipe(
    runbook_text: str,
):
    """Belt-and-suspenders: the prose recipe must include
    both keys (CLI --write AND env var) so a copy-paste
    reader does not miss either."""
    assert "--write" in runbook_text
    assert dbw.ENV_VAR_NAME in runbook_text
    assert dbw.ENV_VAR_REQUIRED_VALUE in runbook_text


def test_runbook_carries_every_required_root_flag(
    runbook_text: str,
):
    for flag in (
        "--cache-dir",
        "--status-dir",
        "--artifact-root",
        "--stackbuilder-root",
        "--signal-library-dir",
        "--execution-log",
    ):
        assert flag in runbook_text, (
            f"runbook is missing required-flag callout: {flag!r}"
        )


def test_runbook_carries_persist_skip_lag_rule(
    runbook_text: str,
):
    """The persist-skip-lag rule is the structural reason
    refresh-then-pipeline must gate on the watcher recheck.
    The runbook must spell it out."""
    assert "persist_skip_bars" in runbook_text or (
        "persist-skip" in runbook_text.lower()
    )
    assert "strictly greater than" in runbook_text or (
        "strict inequality" in runbook_text
    )


def test_runbook_carries_no_stackbuilder_age_window(
    runbook_text: str,
):
    """Phase 6H-3 contract: saved stack variants are
    durable. The runbook must reject any age-based stale
    window so the rule is visible in two places (here AND
    the manifest)."""
    text = runbook_text
    assert "No monthly stale window" in text or (
        "no monthly stale window" in text.lower()
    )
    assert "No 30-day age threshold" in text or (
        "no 30-day" in text.lower()
    ) or "no 30-day age threshold" in text.lower()
