#!/usr/bin/env python3
"""Build decision as a single executable for the current OS and architecture."""

import argparse
import hashlib
from importlib.metadata import distribution
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import sysconfig

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def license_notices():
    sections = [("decision — MIT", Path(__file__).resolve().parents[1].joinpath("LICENSE").read_text(encoding="utf-8"))]
    python_licenses = (Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
                       Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "Resources/LICENSE")
    python_license = next((path for path in python_licenses if path.is_file()), None)
    if python_license is None:
        raise RuntimeError("CPython license not found; build with a Python installation that includes its license")
    sections.append(("CPython", python_license.read_text(encoding="utf-8")))
    pending = [("decision-harness", frozenset())]
    visited = set()
    packages = {}
    while pending:
        name, extras = pending.pop()
        name = canonicalize_name(name)
        if (name, extras) in visited:
            continue
        visited.add((name, extras))
        package = distribution(name)
        packages[name] = package
        for value in package.requires or []:
            requirement = Requirement(value)
            if requirement.marker is None or any(requirement.marker.evaluate({"extra": extra}) for extra in {"", *extras}):
                pending.append((requirement.name, frozenset(requirement.extras)))
    packages["pyinstaller"] = distribution("pyinstaller")
    for name, package in sorted(packages.items()):
        files = [p for p in package.files or []
                 if Path(str(p)).name.lower().startswith(("license", "copying", "notice", "copyright"))]
        if not files:
            raise RuntimeError(f"No license notices found for bundled package {name}")
        for path in sorted(files):
            sections.append((f"{name} {package.version} — {path}", package.locate_file(path).read_text(encoding="utf-8")))
    return "\n\n".join(f"{'=' * 72}\n{name}\n{'=' * 72}\n\n{text.rstrip()}" for name, text in sections) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory receiving the executable and SHA256SUMS")
    parser.add_argument("--cache-dir", type=Path, help="Reuse PyInstaller's processed-library cache; output analysis still starts fresh")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "src/decision_cli/cli.py"
    notices = license_notices()
    with tempfile.TemporaryDirectory(prefix="decision-build-") as temp:
        notice_path = Path(temp) / "THIRD_PARTY_NOTICES.txt"
        notice_path.write_text(notices, encoding="utf-8")
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
                   "--name", "decision", "--paths", str(source.parents[1]), "--collect-submodules", "decision_cli", "--distpath", str(output), "--workpath", str(Path(temp) / "work"),
                   "--specpath", temp, "--collect-submodules", "typesafe_sdk", "--collect-submodules", "openai",
                   "--copy-metadata", "typesafe-sdk", "--copy-metadata", "mcp", "--copy-metadata", "openai",
                   "--add-data", f"{notice_path}:.", str(source)]
        cache = args.cache_dir.resolve() if args.cache_dir else Path(temp) / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        if args.cache_dir is None:
            command.append("--clean")
        environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(cache))
        subprocess.run(command, check=True, stdout=sys.stderr, env=environment)
    binary = output / ("decision.exe" if os.name == "nt" else "decision")
    from PyInstaller.archive.readers import CArchiveReader

    if CArchiveReader(str(binary)).extract("THIRD_PARTY_NOTICES.txt") != notices.encode("utf-8"):
        raise RuntimeError("Standalone executable is missing its license notices")
    (output / "THIRD_PARTY_NOTICES.txt").write_text(notices, encoding="utf-8")
    names = (binary.name, "THIRD_PARTY_NOTICES.txt")
    (output / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}\n" for name in names))
    print(binary)


if __name__ == "__main__":
    main()
