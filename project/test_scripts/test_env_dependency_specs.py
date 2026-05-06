"""
Phase 5B Item 5 static guard: pin lockstep between
project/environment.yml and project/requirements.txt + agreement
with the pinned spyproject2 audit runtime.

Prevents the form of drift documented in CLAUDE.md Section 1
(env files declared aspirational pins for newer NumPy/pandas that
diverged from the actual passing audit runtime). After this guard
lands, any future drift between the two env files or between an
env-file pin and the live runtime will fail loudly here.

Parsing is line-based and stdlib-only (no PyYAML dependency) so
this test runs cleanly under either spyproject2 or spyproject2_basic.

ASCII-only assertion messages per CLAUDE.md cp1252 discipline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Set


PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_YML_PATH = PROJECT_DIR / "environment.yml"
REQ_TXT_PATH = PROJECT_DIR / "requirements.txt"


# ---------------------------------------------------------------------------
# Allowlists for documented one-way entries
# ---------------------------------------------------------------------------
#
# Packages that intentionally appear in environment.yml ONLY and not in
# requirements.txt. These are conda-only platform/runtime packages that
# have no clean pip equivalent contract (Python itself, the conda pip
# bootstrap, setuptools, wheel). The pip-only requirements.txt path
# does not pin Python or the bootstrap toolchain.
ENV_YML_ONLY_ALLOWLIST: Set[str] = {
    "python",
    "pip",
    "setuptools",
    "wheel",
}

# Packages that intentionally appear in requirements.txt ONLY. After the
# Phase 5B Item 5 lockstep pass there are none; the set is left empty so
# future one-way pip-only adds are an explicit, reviewed decision.
REQUIREMENTS_TXT_ONLY_ALLOWLIST: Set[str] = set()


# ---------------------------------------------------------------------------
# Parsers (no PyYAML)
# ---------------------------------------------------------------------------


def _strip_comment(line: str) -> str:
    if "#" in line:
        return line.split("#", 1)[0]
    return line


def _strip_env_marker(entry: str) -> str:
    if ";" in entry:
        return entry.split(";", 1)[0].strip()
    return entry


def parse_environment_yml(path: Path) -> Dict[str, str]:
    """Return {package_name_lower: version_string} for every strictly
    pinned entry in ``environment.yml`` (both the conda section and
    the nested pip section).

    Strict pin formats accepted:
      - conda style ``name=X.Y.Z`` (single ``=``, no comparison ops)
      - pip style ``name==X.Y.Z`` (double ``==``)

    Loose constraints (``>=``, ``<=``, ``~=`` etc.) and unpinned
    entries are skipped: they are not part of the lockstep contract.
    """
    pins: Dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = _strip_comment(raw).rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("- "):
            continue
        entry = stripped[2:].strip()
        if not entry or entry == "pip:":
            continue
        entry = _strip_env_marker(entry)
        if not entry:
            continue
        if "==" in entry:
            name, ver = entry.split("==", 1)
            pins[name.strip().lower()] = ver.strip()
            continue
        if ">=" in entry or "<=" in entry or "~=" in entry or ">" in entry or "<" in entry:
            # Loose constraint; not part of the strict-pin contract.
            continue
        if "=" in entry:
            name, ver = entry.split("=", 1)
            pins[name.strip().lower()] = ver.strip()
            continue
        # Unpinned (e.g., ``- openssl``); skip.
    return pins


def parse_requirements_txt(path: Path) -> Dict[str, str]:
    """Return {package_name_lower: version_string} for every strictly
    pinned entry in ``requirements.txt``.

    Only ``name==X.Y.Z`` is treated as a strict pin. Anything else is
    skipped.
    """
    pins: Dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for raw in text.splitlines():
        line = _strip_comment(raw).rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        entry = _strip_env_marker(stripped)
        if not entry:
            continue
        if "==" in entry:
            name, ver = entry.split("==", 1)
            pins[name.strip().lower()] = ver.strip()
    return pins


# ---------------------------------------------------------------------------
# A. Shared-pin lockstep (with allowlist)
# ---------------------------------------------------------------------------


def test_environment_yml_and_requirements_txt_shared_pins_match():
    env_pins = parse_environment_yml(ENV_YML_PATH)
    req_pins = parse_requirements_txt(REQ_TXT_PATH)
    shared = set(env_pins) & set(req_pins)
    shared -= ENV_YML_ONLY_ALLOWLIST
    shared -= REQUIREMENTS_TXT_ONLY_ALLOWLIST

    mismatches = []
    for pkg in sorted(shared):
        if env_pins[pkg] != req_pins[pkg]:
            mismatches.append(
                "  " + pkg
                + ": env.yml=" + env_pins[pkg]
                + " req.txt=" + req_pins[pkg]
            )
    assert not mismatches, (
        "Shared-package pin drift between environment.yml and "
        "requirements.txt:\n" + "\n".join(mismatches)
    )

    # Orphan check: every strictly pinned package must appear in both
    # files unless explicitly allowlisted.
    env_only = set(env_pins) - set(req_pins) - ENV_YML_ONLY_ALLOWLIST
    req_only = set(req_pins) - set(env_pins) - REQUIREMENTS_TXT_ONLY_ALLOWLIST
    assert not env_only, (
        "Packages strictly pinned in environment.yml but missing from "
        "requirements.txt (and not in ENV_YML_ONLY_ALLOWLIST): "
        + str(sorted(env_only))
    )
    assert not req_only, (
        "Packages strictly pinned in requirements.txt but missing from "
        "environment.yml (and not in REQUIREMENTS_TXT_ONLY_ALLOWLIST): "
        + str(sorted(req_only))
    )


# ---------------------------------------------------------------------------
# B. Critical runtime pins agree with the executing Python runtime
# ---------------------------------------------------------------------------


def test_critical_runtime_pins_match_executing_runtime():
    """The verified spyproject2 audit runtime is the source of truth
    for these four packages. environment.yml MUST match it exactly.
    Drift here means a contributor recreating the env from the file
    will not get the audit-grade stack.
    """
    env_pins = parse_environment_yml(ENV_YML_PATH)
    expected = {
        "numpy": "1.26.4",
        "pandas": "2.2.1",
        "scipy": "1.13.1",
        "pytest": "8.3.5",
    }

    import numpy
    import pandas
    import scipy
    import pytest as _pytest

    runtime_versions = {
        "numpy": numpy.__version__,
        "pandas": pandas.__version__,
        "scipy": scipy.__version__,
        "pytest": _pytest.__version__,
    }

    failures = []
    for pkg, want in expected.items():
        env_pin = env_pins.get(pkg)
        if env_pin != want:
            failures.append(
                "  " + pkg
                + ": environment.yml pin=" + str(env_pin)
                + " expected=" + want
            )
        runtime_ver = runtime_versions[pkg]
        if runtime_ver != want:
            failures.append(
                "  " + pkg
                + ": runtime version=" + runtime_ver
                + " expected=" + want
            )
    assert not failures, (
        "Critical runtime pin drift (env file vs. expected vs. "
        "executing runtime):\n" + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# C. pandas-market-calendars must be declared in both files
# ---------------------------------------------------------------------------


def test_pandas_market_calendars_declared():
    env_text = ENV_YML_PATH.read_text(encoding="utf-8")
    req_text = REQ_TXT_PATH.read_text(encoding="utf-8")
    target = "pandas-market-calendars==5.1.1"
    assert target in env_text, (
        "Expected '" + target + "' in environment.yml; this is a "
        "direct import at project/spymaster.py:91 and must not be "
        "left to ambient transitive resolution."
    )
    assert target in req_text, (
        "Expected '" + target + "' in requirements.txt; the pip-only "
        "install path must include it."
    )


# ---------------------------------------------------------------------------
# D. xlsxwriter must not appear in either file
# ---------------------------------------------------------------------------


def test_xlsxwriter_absent():
    """Installing xlsxwriter changes pandas's default Excel writer.
    The codebase explicitly drives openpyxl; xlsxwriter is removed
    to avoid silent behavior shifts in pandas .to_excel calls.
    """
    env_text = ENV_YML_PATH.read_text(encoding="utf-8")
    req_text = REQ_TXT_PATH.read_text(encoding="utf-8")
    assert "xlsxwriter" not in env_text.lower(), (
        "xlsxwriter must not be declared in environment.yml; "
        "it shifts pandas Excel writer defaults."
    )
    assert "xlsxwriter" not in req_text.lower(), (
        "xlsxwriter must not be declared in requirements.txt; "
        "it shifts pandas Excel writer defaults."
    )


# ---------------------------------------------------------------------------
# E. pyarrow optionality is intentional
# ---------------------------------------------------------------------------


def test_pyarrow_optional_absence_does_not_fail():
    """Documents that pyarrow is intentionally NOT pinned in either
    env file at this phase. Parquet/V3 manifest support is deferred;
    the absence is a deliberate choice and this test will fail-fast
    with a clear message if a future contributor adds pyarrow without
    updating the deferred-work entry.
    """
    env_text = ENV_YML_PATH.read_text(encoding="utf-8").lower()
    req_text = REQ_TXT_PATH.read_text(encoding="utf-8").lower()
    if "pyarrow" in env_text or "pyarrow" in req_text:
        # Re-deferral of the Parquet decision must update the
        # deferred-work memory entry and the Phase 5 ledger.
        raise AssertionError(
            "pyarrow appeared in an env file but the Parquet/V3 "
            "manifest decision is currently deferred. Update "
            "md_library/shared/2026-05-05_PHASE_5A_CLEANUP_LEDGER "
            "and the deferred-work entry before re-introducing it."
        )
    # Otherwise the deliberate absence is correct; pass-through.


# ---------------------------------------------------------------------------
# F. jinja2 pin agreement
# ---------------------------------------------------------------------------


def test_jinja2_pin_matches_runtime():
    env_pins = parse_environment_yml(ENV_YML_PATH)
    req_pins = parse_requirements_txt(REQ_TXT_PATH)
    expected = "3.1.6"
    assert env_pins.get("jinja2") == expected, (
        "environment.yml jinja2 pin must be " + expected
        + " (matches the verified spyproject2 runtime); got="
        + str(env_pins.get("jinja2"))
    )
    assert req_pins.get("jinja2") == expected, (
        "requirements.txt jinja2 pin must be " + expected
        + "; got=" + str(req_pins.get("jinja2"))
    )


# ---------------------------------------------------------------------------
# G. typing-extensions held at 4.14.0 (Selenium 4.35.0 constraint)
# ---------------------------------------------------------------------------


def test_typing_extensions_pin_held_at_4_14_0():
    """Selenium 4.35.0 declares ``typing-extensions~=4.14.0`` and
    rejects 4.15.0. The verified runtime currently has 4.15.0
    installed in spyproject2 — that is a known mismatch flagged in
    the Phase 5 deferred-work entries; the env files MUST hold the
    Selenium-compatible 4.14.0 pin so a fresh install resolves
    cleanly.
    """
    env_pins = parse_environment_yml(ENV_YML_PATH)
    req_pins = parse_requirements_txt(REQ_TXT_PATH)
    expected = "4.14.0"
    assert env_pins.get("typing-extensions") == expected, (
        "environment.yml typing-extensions pin must be " + expected
        + " (Selenium 4.35.0 rejects 4.15.0); got="
        + str(env_pins.get("typing-extensions"))
    )
    assert req_pins.get("typing-extensions") == expected, (
        "requirements.txt typing-extensions pin must be " + expected
        + "; got=" + str(req_pins.get("typing-extensions"))
    )
    env_text = ENV_YML_PATH.read_text(encoding="utf-8")
    req_text = REQ_TXT_PATH.read_text(encoding="utf-8")
    assert "typing-extensions==4.15.0" not in env_text, (
        "typing-extensions must not be bumped to 4.15.0 in "
        "environment.yml; Selenium 4.35.0 rejects it."
    )
    assert "typing-extensions==4.15.0" not in req_text, (
        "typing-extensions must not be bumped to 4.15.0 in "
        "requirements.txt; Selenium 4.35.0 rejects it."
    )
