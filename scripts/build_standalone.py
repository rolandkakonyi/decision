#!/usr/bin/env python3
"""Build decision as a single executable for the current OS and architecture."""

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory receiving the executable and SHA256SUMS")
    parser.add_argument("--cache-dir", type=Path, help="Reuse PyInstaller's processed-library cache; output analysis still starts fresh")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "src/decision_cli/cli.py"
    with tempfile.TemporaryDirectory(prefix="decision-build-") as temp:
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
                   "--name", "decision", "--paths", str(source.parents[1]), "--collect-submodules", "decision_cli", "--distpath", str(output), "--workpath", str(Path(temp) / "work"),
                   "--specpath", temp, "--collect-submodules", "typesafe_sdk", "--collect-submodules", "openai",
                   "--copy-metadata", "typesafe-sdk", "--copy-metadata", "mcp", "--copy-metadata", "openai", str(source)]
        cache = args.cache_dir.resolve() if args.cache_dir else Path(temp) / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        if args.cache_dir is None:
            command.append("--clean")
        environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(cache))
        subprocess.run(command, check=True, stdout=sys.stderr, env=environment)
    binary = output / ("decision.exe" if os.name == "nt" else "decision")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  {binary.name}\n")
    print(binary)


if __name__ == "__main__":
    main()
