#!/usr/bin/env python3
"""Build and test a clean wheel installation outside the source checkout."""

from email.parser import BytesParser
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
from zipfile import ZipFile

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
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        with ZipFile(wheels[0]) as wheel:
            metadata_path = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA"))
            metadata = BytesParser().parsebytes(wheel.read(metadata_path))
            if metadata["License-Expression"] != project["license"]:
                raise RuntimeError("Wheel license metadata does not match the project")
            if metadata.get_all("License-File") != project["license-files"]:
                raise RuntimeError("Wheel license file declarations do not match the project")
            metadata_dir = metadata_path.rsplit("/", 1)[0]
            for license_file in project["license-files"]:
                if wheel.read(f"{metadata_dir}/licenses/{license_file}") != (ROOT / license_file).read_bytes():
                    raise RuntimeError("Wheel license text does not match the project")
            if metadata.get_payload(decode=True).decode("utf-8").strip() != (ROOT / project["readme"]).read_text().strip():
                raise RuntimeError("Wheel description does not match the README")
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
        expected = project["version"]
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
