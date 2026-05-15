"""Phase 6I-55 amendment-1 doc-guard test.

Pins that the Phase 6I-55 evidence doc keeps the
amendment-1 verified-upstream-chain citations. No code
under test; this is a forward-looking guard against
future doc rewrites that might drop the upstream-chain
section.

Single test, no fixtures, no production access.
"""
from __future__ import annotations

from pathlib import Path


_DOC = (
    Path(__file__).resolve().parent.parent
    / "md_library"
    / "shared"
    / "2026-05-15_PHASE_6I55_STACKBUILDER_PILOT_BATCH_RETRY.md"
)

_EVIDENCE_JSON = (
    Path(__file__).resolve().parent.parent
    / "md_library"
    / "shared"
    / "2026-05-15_PHASE_6I55_STACKBUILDER_PILOT_BATCH_RETRY_EVIDENCE.json"
)


_REQUIRED_PHRASES = (
    # Verified upstream chain stages.
    "OnePass / signal_library",
    "ImpactSearch",
    "StackBuilder",
    "Confluence",
    # Code-path citations.
    "onepass.py:1154",
    "save_signal_library",
    "impactsearch.py:1525",
    "load_signal_library",
    "impactsearch.py:2491",
    "export_results_to_excel",
    "stackbuilder.py:583",
    "try_load_rank_from_impact_xlsx",
    "stackbuilder.py:889",
    "phase1_preflight",
    "stackbuilder.py:1487",
    "phase3_build_stacks",
    "--prefer-impact-xlsx",
    # ImpactSearch output convention.
    "output/impactsearch",
    "<TICKER>_analysis.xlsx",
    # Phase 6I-55a planner taxonomy (amendment-1 preferred
    # path).
    "ready_for_stackbuilder_with_impact_xlsx",
    "needs_impactsearch_run",
    "manual_review",
    # Concrete on-disk state recorded in amendment-1.
    "2026-01-09",
    # NOT recommended marker for option D.
    "NOT recommended",
)


def test_phase_6i_55_evidence_doc_carries_upstream_chain_citations():
    """The Phase 6I-55 evidence doc must keep every
    amendment-1 upstream-chain citation. Future doc
    rewrites that drop any of these phrases fail loudly
    here so the citation set is regenerated /
    cross-referenced."""
    assert _DOC.exists(), (
        f"Phase 6I-55 evidence doc missing at {_DOC}"
    )
    body = _DOC.read_text(encoding="utf-8")
    missing = [
        phrase for phrase in _REQUIRED_PHRASES
        if phrase not in body
    ]
    assert not missing, (
        "Phase 6I-55 evidence doc is missing required "
        f"amendment-1 phrases: {missing!r}"
    )


def test_phase_6i_55_evidence_json_carries_amendment_1_blocks():
    """The consolidated evidence JSON must carry the
    amendment-1 blocks (verified-upstream-chain,
    concrete state, revised options, no-production-
    activity)."""
    import json
    assert _EVIDENCE_JSON.exists()
    payload = json.loads(
        _EVIDENCE_JSON.read_text(encoding="utf-8"),
    )
    for key in (
        "amendment_1_verified_upstream_chain",
        "amendment_1_concrete_upstream_state",
        "amendment_1_revised_options_summary",
        "amendment_1_no_production_activity",
    ):
        assert key in payload, (
            f"Phase 6I-55 evidence JSON missing "
            f"amendment-1 block: {key!r}"
        )
    # Drill in: the upstream chain must cite each module
    # by name + line number.
    chain = payload[
        "amendment_1_verified_upstream_chain"
    ]
    citations = chain["code_path_citations"]
    cited_modules = " ".join(
        c["file_line"] for c in citations
    )
    for needle in (
        "onepass.py:1154",
        "impactsearch.py:1525",
        "impactsearch.py:2491",
        "stackbuilder.py:583",
        "stackbuilder.py:889",
        "stackbuilder.py:1487",
        "stackbuilder.py:3361",
    ):
        assert needle in cited_modules, (
            f"Phase 6I-55 evidence JSON upstream-chain "
            f"citations missing {needle!r}"
        )


def test_phase_6i_55_evidence_json_amendment_1_zero_production_flags():
    """Amendment-1 is docs-only; every production-
    activity flag must be False."""
    import json
    payload = json.loads(
        _EVIDENCE_JSON.read_text(encoding="utf-8"),
    )
    flags = payload["amendment_1_no_production_activity"]
    assert flags["documentation_only"] is True
    for negative_flag in (
        "no_yfinance",
        "no_stackbuilder_invocation",
        "no_onepass_invocation",
        "no_impactsearch_invocation",
        "no_source_refresh",
        "no_promotion",
        "no_confluence_patch_writer",
        "no_pipeline_runner",
    ):
        assert flags[negative_flag] is True, (
            f"amendment-1 flag {negative_flag} != True"
        )


# ---------------------------------------------------------------------------
# Phase 6I-55 amendment-2 path-existence guard.
#
# Codex found that the doc + JSON referenced raw
# stdout/stderr .txt files (RUN_SPY_STDOUT.txt /
# RUN_SPY_STDERR.txt) that are git-ignored and not in the
# checkout. Amendment-2 removed the stale path references
# and added a stdout_stderr_capture="embedded_only"
# marker. This guard asserts every path-shaped reference
# in the doc + JSON points to a file that actually exists
# on disk, unless explicitly marked embedded_only.
# ---------------------------------------------------------------------------


_REPO_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MD_LIBRARY_PREFIX = (
    "md_library/shared/2026-05-15_PHASE_6I55"
)


def _walk_evidence_paths_in_doc(doc_body: str) -> list[str]:
    """Scan the doc body for path-shaped references that
    point at Phase 6I-55 evidence files under
    md_library/shared/. Returns the de-duplicated list of
    referenced paths."""
    import re
    pattern = re.compile(
        r"(?:project/)?(md_library/shared/2026-05-15_"
        r"PHASE_6I55_[A-Za-z0-9_]+\.(?:json|md|txt|jsonl))"
    )
    seen: set[str] = set()
    out: list[str] = []
    for match in pattern.finditer(doc_body):
        path = match.group(1)
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def _walk_evidence_paths_in_json(payload: object) -> list[str]:
    """Recurse the parsed JSON looking for string values
    that look like md_library/shared/<phase> paths."""
    import re
    pattern = re.compile(
        r"^(?:project/)?(md_library/shared/2026-05-15_"
        r"PHASE_6I55_[A-Za-z0-9_]+\.(?:json|md|txt|jsonl))$"
    )
    seen: set[str] = set()

    def _visit(node: object) -> None:
        if isinstance(node, dict):
            for v in node.values():
                _visit(v)
        elif isinstance(node, list):
            for v in node:
                _visit(v)
        elif isinstance(node, str):
            m = pattern.match(node.strip())
            if m:
                seen.add(m.group(1))
    _visit(payload)
    return sorted(seen)


def test_doc_and_json_referenced_paths_all_exist_or_are_embedded_only():
    """Walk every path-shaped reference in the Phase 6I-55
    evidence doc + JSON. Every referenced path must
    either exist on disk OR be a known embedded-only
    `*_RUN_SPY_STDOUT.txt` / `*_RUN_SPY_STDERR.txt` file
    (those are git-ignored by `.gitignore *.txt`; their
    content is preserved in the JSON's stdout_tail /
    stderr_tail with a stdout_stderr_capture marker of
    ``embedded_only``)."""
    import json
    assert _DOC.exists()
    assert _EVIDENCE_JSON.exists()
    doc_body = _DOC.read_text(encoding="utf-8")
    payload = json.loads(
        _EVIDENCE_JSON.read_text(encoding="utf-8"),
    )

    referenced = set(
        _walk_evidence_paths_in_doc(doc_body)
    )
    referenced.update(
        _walk_evidence_paths_in_json(payload),
    )

    embedded_only_allowed = {
        "md_library/shared/"
        "2026-05-15_PHASE_6I55_RUN_SPY_STDOUT.txt",
        "md_library/shared/"
        "2026-05-15_PHASE_6I55_RUN_SPY_STDERR.txt",
    }

    missing_required: list[str] = []
    for rel_path in referenced:
        if rel_path in embedded_only_allowed:
            # These are documented as embedded-only;
            # they're git-ignored and not expected on
            # disk in the checkout.
            continue
        full = _REPO_PROJECT_ROOT / rel_path
        if not full.exists():
            missing_required.append(rel_path)
    assert not missing_required, (
        "Phase 6I-55 doc/JSON references paths that do "
        "not exist on disk and are not declared as "
        f"embedded_only: {sorted(missing_required)}"
    )


def test_per_ticker_results_marked_embedded_only():
    """After amendment-2, every per-ticker execution-log
    row must carry the stdout_stderr_capture ==
    'embedded_only' marker (or include stdout_path /
    stderr_path values that actually exist; we don't
    expect that variant going forward, but the test
    accepts either)."""
    import json
    payload = json.loads(
        _EVIDENCE_JSON.read_text(encoding="utf-8"),
    )
    rows = (
        payload.get("execution_log", {})
        .get("per_ticker_results", [])
    )
    assert rows, "execution_log.per_ticker_results empty"
    for row in rows:
        if "stdout_path" in row or "stderr_path" in row:
            # Legacy shape: both paths must point at
            # files that actually exist.
            for key in ("stdout_path", "stderr_path"):
                rel = row.get(key)
                if rel:
                    full = _REPO_PROJECT_ROOT / rel
                    assert full.exists(), (
                        f"row {row.get('ticker')!r} "
                        f"references {key}={rel!r} but "
                        "the file does not exist"
                    )
        else:
            # Amendment-2 shape: embedded-only marker.
            assert (
                row.get("stdout_stderr_capture")
                == "embedded_only"
            ), (
                f"row {row.get('ticker')!r} is missing "
                "the amendment-2 "
                "stdout_stderr_capture='embedded_only' "
                "marker AND has no stdout_path / "
                "stderr_path"
            )
            assert (
                "stdout_tail" in row
                and "stderr_tail" in row
            ), (
                f"row {row.get('ticker')!r} is embedded-"
                "only but missing stdout_tail / "
                "stderr_tail"
            )


def test_policy_gap_matches_amendment_1_framing():
    """Amendment-2 reconciled policy_gap.options_for_
    resolution + recommended_resolution to the amendment-1
    framing. The OLD pre-amendment-1 wording must NOT
    appear; the NEW preferred-path wording MUST appear."""
    import json
    payload = json.loads(
        _EVIDENCE_JSON.read_text(encoding="utf-8"),
    )
    policy_gap = payload["policy_gap"]
    options = " | ".join(
        policy_gap["options_for_resolution"],
    )
    recommended = policy_gap["recommended_resolution"]
    # Amendment-1 framing must be present.
    assert (
        "PREFERRED: Phase 6I-55a" in options
    ), (
        "policy_gap.options_for_resolution must lead "
        "with the Phase 6I-55a preferred path"
    )
    assert (
        "Phase 6I-55a" in recommended
    ), (
        "policy_gap.recommended_resolution must "
        "reference Phase 6I-55a"
    )
    # Option D regression-guard wording.
    assert "NOT RECOMMENDED" in options
    assert "TEF" in options
    # Manual-override wording for option A.
    assert "MANUAL OVERRIDE ONLY" in options
    # Pre-amendment recommendation pattern (the old
    # "operator picks an option (A/B/C/D)" framing) must
    # NOT survive.
    assert (
        "Operator decision required" not in recommended
    ), (
        "policy_gap.recommended_resolution still carries "
        "the pre-amendment 'Operator decision required' "
        "wording; amendment-2 should have replaced it "
        "with the Phase 6I-55a-led recommendation"
    )
