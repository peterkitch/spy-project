"""
Post Phase 3 cleanup: pin the spymaster ``update_dynamic_strategy_display``
``annualized_return`` UnboundLocalError bug.

The pre-fix code referenced ``annualized_return`` in a tooltip f-string
hundreds of lines before its assignment, so any execution path that
reached the tooltip raised ``UnboundLocalError`` at runtime.

This test pins the bug class statically: within
``update_dynamic_strategy_display``, every Load of the local name
``annualized_return`` must be preceded (by line number) by at least one
Store of the same name in the same function body.

A static guard is used in preference to a synthetic Dash-callback
runtime fixture because the bug is purely a variable-ordering defect:
no input combination is required to expose it once the failing branch
is reached, and a static check pins the exact regression class without
recreating the full callback context.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

SPYMASTER_PATH = PROJECT_DIR / "spymaster.py"
FUNC_NAME = "update_dynamic_strategy_display"
LOCAL_NAME = "annualized_return"


def _find_function(tree: ast.AST, name: str) -> "ast.FunctionDef | None":
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _local_use_and_assign_lines(func: ast.FunctionDef, name: str) -> "tuple[list[int], list[int]]":
    """Return ``(use_lines, assign_lines)`` where each list contains the
    AST ``lineno`` of every Load / Store of ``name`` inside ``func``.

    Loads inside the local-name scope of nested functions / lambdas /
    comprehensions are skipped to keep the check honest about the
    enclosing function's bindings.
    """
    use_lines: "list[int]" = []
    assign_lines: "list[int]" = []
    nested_scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    for sub in ast.walk(func):
        if sub is func:
            continue
        if isinstance(sub, nested_scopes):
            # Don't descend into nested function definitions; their
            # bindings are independent of the outer scope.
            continue
        if isinstance(sub, ast.Name) and sub.id == name:
            if isinstance(sub.ctx, ast.Load):
                use_lines.append(sub.lineno)
            elif isinstance(sub.ctx, ast.Store):
                assign_lines.append(sub.lineno)
    return use_lines, assign_lines


def test_annualized_return_is_assigned_before_first_use():
    """``annualized_return`` must be Stored before any Load inside
    ``update_dynamic_strategy_display`` to prevent ``UnboundLocalError``.
    """
    text = SPYMASTER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(SPYMASTER_PATH))
    func = _find_function(tree, FUNC_NAME)
    assert func is not None, (
        f"Could not find function {FUNC_NAME!r} in {SPYMASTER_PATH}; "
        f"the regression guard cannot run if the function is renamed or removed."
    )

    use_lines, assign_lines = _local_use_and_assign_lines(func, LOCAL_NAME)
    if not use_lines:
        pytest.skip(
            f"{LOCAL_NAME!r} is no longer read inside {FUNC_NAME!r}; "
            f"the regression class no longer applies."
        )
    assert assign_lines, (
        f"{LOCAL_NAME!r} is read inside {FUNC_NAME!r} but never assigned "
        f"locally; this would raise NameError or UnboundLocalError."
    )
    first_use = min(use_lines)
    first_assign = min(assign_lines)
    assert first_assign <= first_use, (
        f"{LOCAL_NAME!r} is read at line {first_use} in {FUNC_NAME!r} but "
        f"first assigned at line {first_assign}, which is AFTER the read. "
        f"This is the Post Phase 3 UnboundLocalError bug class. The fix is "
        f"to move the assignment before the first read (e.g. immediately "
        f"after the ``years`` computation block)."
    )
