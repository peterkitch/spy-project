import importlib
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

ENGINE_MODULES = [
    "spymaster",
    "onepass",
    "impactsearch",
    "stackbuilder",
    "confluence",
    "trafficflow",
]


def test_core_engines_import():
    failures = {}
    for name in ENGINE_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            failures[name] = repr(exc)
    assert not failures, failures
