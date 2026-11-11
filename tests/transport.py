"""Shared process isolation for source, clean wheel, and standalone fixtures."""

import os
from pathlib import Path
import sys


def command():
    binary = os.getenv("DECISION_TEST_EXECUTABLE")
    script = Path(__file__).resolve().parents[1] / "src/decision_cli/cli.py"
    return [binary] if binary else [sys.executable, str(script)]


def working_directory():
    return os.getenv("DECISION_TEST_CWD")


def isolate(environment):
    mode = os.getenv("DECISION_TEST_MODE", "standalone" if os.getenv("DECISION_TEST_EXECUTABLE") else "source")
    if mode not in {"source", "wheel", "standalone"}:
        raise ValueError("DECISION_TEST_MODE must be source, wheel, or standalone")
    if mode == "standalone":
        environment.update(PATH="/nonexistent", PYTHONHOME="/nonexistent", PYTHONPATH="/nonexistent")
    elif mode == "wheel":
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
    return environment
