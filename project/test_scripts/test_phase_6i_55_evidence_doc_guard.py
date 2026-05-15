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
