"""Shared native-process isolation for CLI and MCP fixtures."""

import os
from native import command, environment


def working_directory():
    return os.getenv("DECISION_TEST_CWD")


def isolate(environment):
    from native import environment as clean_environment
    return clean_environment(environment)
