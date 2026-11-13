#!/usr/bin/env python3
"""Run native black-box fixtures, with explicit module exclusions for CI jobs."""

import argparse
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "tests")
    parser.add_argument("--exclude", action="append", default=[], metavar="TEST_FILE.py")
    args = parser.parse_args()
    directory = args.directory.resolve()
    paths = sorted(directory.glob("test_*.py"))
    unknown = set(args.exclude) - {path.name for path in paths}
    if unknown:
        parser.error("Unknown excluded test module: " + ", ".join(sorted(unknown)))
    suite = unittest.TestSuite()
    for path in paths:
        if path.name in args.exclude:
            print(f"Excluding {path.name}", flush=True)
        else:
            suite.addTests(unittest.defaultTestLoader.discover(str(directory), pattern=path.name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.testsRun and result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
