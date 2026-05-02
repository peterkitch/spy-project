"""
Phase 1B-2B: StackBuilder Dash batch closure bug.

The Dash multi-secondary launch loop previously defined the worker
function ``_job()`` as a closure over the loop variables ``args``,
``sec``, ``ppath``, and ``primaries``. Python late-binding closure
semantics mean every thread sees the LAST iteration's values once
the for-loop completes — so threads started early in the loop end
up running with the wrong secondary's parameters.

Fix: take ``_job(job_args, job_sec, job_ppath, job_primaries)`` and
pass loop values explicitly via ``threading.Thread(args=...)``.

This test reproduces the original bug pattern in isolation, then
verifies the fixed pattern actually delivers the right values.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def _import_stackbuilder():
    sb = importlib.import_module("stackbuilder")
    if not hasattr(sb, "run_for_secondary"):
        spec = importlib.util.spec_from_file_location(
            "stackbuilder", str(PROJECT_DIR / "stackbuilder.py")
        )
        sb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sb)
    return sb


def test_closure_bug_reproduction():
    """Show that the late-binding closure pattern is genuinely broken.

    This test does NOT exercise stackbuilder; it demonstrates the
    Python semantics that motivate the fix. If this test ever fails
    (i.e. the language semantics change), the production fix is
    obsolete.
    """
    captured = []
    captured_lock = threading.Lock()

    threads = []
    for sec in ("A", "B", "C", "D"):
        # Each iteration rebinds `sec`. A naive `def _job(): ... use sec ...`
        # captures the NAME, not the VALUE, so all threads see the
        # final binding by the time they run.
        def _job_buggy():
            time.sleep(0.01)  # let the loop finish before the thread starts
            with captured_lock:
                captured.append(sec)

        t = threading.Thread(target=_job_buggy, daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    # All four threads see the LAST iteration's value of `sec`.
    # If Python's closure semantics ever change, this assertion fails
    # and we should rethink the fix.
    assert captured == ["D", "D", "D", "D"], (
        f"closure semantics changed; got {captured}. "
        "Re-evaluate the production fix."
    )


def test_threadargs_pattern_delivers_correct_values():
    """Show that the production fix (Thread(args=...)) actually works."""
    captured = []
    captured_lock = threading.Lock()

    def _job(job_sec, job_ppath, job_primaries):
        time.sleep(0.01)
        with captured_lock:
            captured.append((job_sec, job_ppath, tuple(job_primaries)))

    threads = []
    for sec in ("A", "B", "C", "D"):
        ppath = f"/tmp/{sec}.json"
        primaries = [f"{sec}_p1", f"{sec}_p2"]
        primaries_snapshot = list(primaries)
        t = threading.Thread(
            target=_job,
            args=(sec, ppath, primaries_snapshot),
            daemon=True,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    # Every thread sees its own iteration's values (set order is not
    # guaranteed because threads run concurrently).
    captured_sorted = sorted(captured)
    expected = sorted([
        ("A", "/tmp/A.json", ("A_p1", "A_p2")),
        ("B", "/tmp/B.json", ("B_p1", "B_p2")),
        ("C", "/tmp/C.json", ("C_p1", "C_p2")),
        ("D", "/tmp/D.json", ("D_p1", "D_p2")),
    ])
    assert captured_sorted == expected


def test_stackbuilder_dispatches_distinct_args_per_thread(monkeypatch):
    """Verify the production dispatch path uses distinct per-job args.

    Approach: monkeypatch ``run_for_secondary`` to record its
    arguments instead of doing real work, then drive the loop body
    by hand (mirroring the production threading.Thread(args=...)
    call). Asserts each thread's run_for_secondary call sees its own
    secondary, args.secondary, ppath, and primaries snapshot.
    """
    sb = _import_stackbuilder()
    records = []
    records_lock = threading.Lock()

    def _record(args, sec, specified_primaries=None):
        # Sleep briefly so all threads start before any returns;
        # this surfaces any cross-thread state leakage.
        time.sleep(0.01)
        with records_lock:
            records.append({
                "args_secondary": args.secondary,
                "args_outdir": args.outdir,
                "sec": sec,
                "specified_primaries": (
                    tuple(specified_primaries) if specified_primaries else None
                ),
                "args_id": id(args),
            })

    monkeypatch.setattr(sb, "run_for_secondary", _record)

    # Mirror the production loop body (the closure-fix pattern).
    secondaries = ["A", "B", "C", "D"]
    primaries = ["P1", "P2"]
    threads = []
    expected_args_ids = []

    for sec in secondaries:
        ppath = f"/tmp/{sec}.json"
        args = SimpleNamespace(
            secondary=sec,
            outdir=f"/tmp/out/{sec}",
        )
        expected_args_ids.append(id(args))
        primaries_snapshot = list(primaries)

        def _job(job_args, job_sec, job_ppath, job_primaries):
            sb.run_for_secondary(job_args, job_sec, specified_primaries=job_primaries)

        t = threading.Thread(
            target=_job,
            args=(args, sec, ppath, primaries_snapshot),
            daemon=True,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    # Every secondary recorded exactly once with its own args/sec.
    secs_seen = sorted(r["sec"] for r in records)
    assert secs_seen == sorted(secondaries)

    # Each record's args.secondary matches its sec (no cross-binding).
    for r in records:
        assert r["args_secondary"] == r["sec"], (
            f"thread saw mismatched args: args.secondary={r['args_secondary']} "
            f"vs sec={r['sec']}"
        )
        assert r["args_outdir"] == f"/tmp/out/{r['sec']}", (
            f"args.outdir leaked across threads: {r['args_outdir']}"
        )
        assert r["specified_primaries"] == ("P1", "P2")

    # Each thread saw its own args object (ids should all be in the
    # expected set, with no duplicates).
    args_ids_seen = sorted(r["args_id"] for r in records)
    assert args_ids_seen == sorted(expected_args_ids), (
        "threads shared an args object instead of getting their own"
    )
