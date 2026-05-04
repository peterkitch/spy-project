"""
Post Phase 3 cleanup: pin the StackBuilder stale-XLSX rejection-message bug.

Pre-fix, when ``try_load_rank_from_impact_xlsx`` rejected a workbook as
stale (older than ``--impact-xlsx-max-age-days``, default 45 days),
``phase2_rank_all`` raised the generic message
``"No ImpactSearch Excel found ..."``, telling the user the file was
absent when it actually existed but was too old. The user then chased
a missing-file error for a file that was on disk.

Post-fix, the loader populates a structured ``rejection_out`` dict on
stale rejection, and the caller produces an actionable message that
names the file path, the actual age, the configured threshold, and
the three remediation paths (refresh, raise the threshold, disable).

This test seeds a fresh-looking but mtime-aged XLSX in a tmp_path and
exercises ``phase2_rank_all`` with ``prefer_impact_xlsx=True`` and no
caller-provided primaries (so the slow-path fallback does not fire).
It asserts that the resulting ``RuntimeError`` carries the new
diagnostic phrasing and not the old "No ImpactSearch Excel found"
phrasing.

No live data, no network. Synthetic XLSX, mtime tampered to the past.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def _seed_stale_xlsx(tmp_path: Path, secondary: str, *, age_days: float) -> Path:
    """Write a synthetic ImpactSearch XLSX matching the candidate filename
    convention and back-date its mtime so the staleness gate trips."""
    workbook = tmp_path / f"{secondary}_analysis.xlsx"
    df = pd.DataFrame([
        {
            "Primary Ticker": "AAA",
            "Avg Daily Capture (%)": 0.10,
            "Total Capture (%)": 1.0,
            "Sharpe Ratio": 1.5,
            "Win Ratio (%)": 60.0,
            "Std Dev (%)": 0.5,
            "Trigger Days": 8,
            "p-Value": 0.04,
        },
    ])
    df.to_excel(workbook, index=False, engine="openpyxl")
    past = time.time() - (age_days * 86400.0)
    os.utime(workbook, (past, past))
    return workbook


def test_stale_xlsx_rejection_message_is_specific(tmp_path):
    import stackbuilder

    secondary = "ZZZ"
    workbook = _seed_stale_xlsx(tmp_path, secondary, age_days=60.0)

    args = SimpleNamespace(
        secondary=secondary,
        prefer_impact_xlsx=True,
        impact_xlsx_dir=str(tmp_path),
        impact_xlsx_max_age_days=45,  # 60 > 45 -> stale
        strict_manifests=False,
        no_progress=True,
        bottom_n=0,
        signal_lib_dir="<unused>",
        threads="auto",
    )
    primaries_df = pd.DataFrame(columns=["Primary Ticker"])
    sec_rets = pd.Series(dtype=float)

    out = tmp_path / "out"
    out.mkdir()

    with pytest.raises(RuntimeError) as exc_info:
        stackbuilder.phase2_rank_all(
            args, primaries_df, sec_rets, outdir=str(out),
            secondary=secondary, progress_path=None,
        )

    msg = str(exc_info.value)

    # The new message must name the workbook, the staleness reason, and the
    # remediation knobs. We assert on individual phrases so a future
    # rewording stays caught while letting the wording evolve a little.
    assert "found" in msg, msg
    assert "rejected as stale" in msg, msg
    assert "age=" in msg, msg
    assert "max_age_days=45" in msg, msg
    assert workbook.name in msg, msg
    assert "--impact-xlsx-max-age-days" in msg, msg

    # Crucially: the old, misleading message must NOT appear, since the
    # workbook actually exists.
    assert "No ImpactSearch Excel found" not in msg, msg


def test_not_found_message_unchanged_when_dir_is_empty(tmp_path):
    """Sanity: when the directory is genuinely empty, the legacy
    'No ImpactSearch Excel found' message must still fire so we don't
    misreport a missing file as a stale one."""
    import stackbuilder

    args = SimpleNamespace(
        secondary="ZZZ",
        prefer_impact_xlsx=True,
        impact_xlsx_dir=str(tmp_path),  # empty dir
        impact_xlsx_max_age_days=45,
        strict_manifests=False,
        no_progress=True,
        bottom_n=0,
        signal_lib_dir="<unused>",
        threads="auto",
    )
    primaries_df = pd.DataFrame(columns=["Primary Ticker"])
    sec_rets = pd.Series(dtype=float)

    out = tmp_path / "out2"
    out.mkdir()

    with pytest.raises(RuntimeError) as exc_info:
        stackbuilder.phase2_rank_all(
            args, primaries_df, sec_rets, outdir=str(out),
            secondary="ZZZ", progress_path=None,
        )
    msg = str(exc_info.value)
    assert "No ImpactSearch Excel found" in msg, msg
    assert "rejected as stale" not in msg, msg
