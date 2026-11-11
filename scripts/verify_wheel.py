#!/usr/bin/env python3
"""Build and test a clean wheel installation outside the source checkout."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=ROOT, env=None, capture=False):
    return subprocess.run(args, cwd=cwd, env=env, text=True, check=True, capture_output=capture)


def main():
    with tempfile.TemporaryDirectory(prefix="decision-wheel-") as directory:
        temp = Path(directory)
        wheel_dir, environment, workspace = temp / "wheels", temp / "venv", temp / "workspace"
        workspace.mkdir()
        run("uv", "build", "--wheel", "--out-dir", str(wheel_dir))
        wheels = list(wheel_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Expected exactly one built wheel")
        requirements = temp / "requirements.txt"
        run("uv", "export", "--locked", "--no-dev", "--no-emit-project", "--output-file", str(requirements), capture=True)
        run("uv", "venv", "--python", sys.executable, str(environment))
        bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
        python = bin_dir / ("python.exe" if os.name == "nt" else "python")
        executable = bin_dir / ("decision.exe" if os.name == "nt" else "decision")
        run("uv", "pip", "install", "--python", str(python), "--require-hashes", "-r", str(requirements))
        run("uv", "pip", "install", "--python", str(python), "--no-deps", str(wheels[0]))
        clean = dict(os.environ)
        clean.pop("PYTHONPATH", None)
        clean.pop("PYTHONHOME", None)
        installed = run(str(python), "-I", "-c", "import decision_cli; print(decision_cli.__file__)",
                        cwd=workspace, env=clean, capture=True).stdout.strip()
        if not Path(installed).resolve().is_relative_to(environment.resolve()):
            raise RuntimeError("Wheel import resolved outside the clean installation")
        expected = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        version = run(str(executable), "--version", cwd=workspace, env=clean, capture=True).stdout.strip()
        if version != f"decision {expected}":
            raise RuntimeError("Installed command version does not match the project")
        run(str(executable), "run", str(ROOT / "examples/request.json"), "--dry-run", "--json", cwd=workspace, env=clean)
        exported = workspace / "skill"
        run(str(executable), "skill", "--output", str(exported), cwd=workspace, env=clean)
        expected_files = {p.relative_to(ROOT / "skills/decision"): p.read_bytes()
                          for p in (ROOT / "skills/decision").rglob("*") if p.is_file()}
        actual_files = {p.relative_to(exported): p.read_bytes() for p in exported.rglob("*") if p.is_file()}
        if actual_files != expected_files:
            raise RuntimeError("Installed wheel skill differs from the committed generated skill")
        tests = dict(clean, DECISION_TEST_EXECUTABLE=str(executable), DECISION_TEST_MODE="wheel",
                     DECISION_TEST_CWD=str(workspace))
        run(sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-p", "test_*mcp.py", "-v",
            cwd=workspace, env=tests)
    print("Clean wheel CLI, generated skill, image uploads, and MCP transport verified")


if __name__ == "__main__":
    main()
