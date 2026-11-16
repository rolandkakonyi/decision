#!/usr/bin/env python3
"""Verify a native archive and run its executable outside the source checkout."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tarfile
import tempfile
from zipfile import ZipFile

from build_standalone import ROOT, package_version

SYSTEM_NAMES = ("PATH", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT", "WINDIR",
                "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")


def unpack(archive, destination):
    roots = set()

    def target(name):
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name or not path.parts:
            raise RuntimeError("Archive contains an invalid path")
        roots.add(path.parts[0])
        return destination.joinpath(*path.parts)

    if archive.suffix == ".zip":
        with ZipFile(archive) as zipped:
            for entry in zipped.infolist():
                path = target(entry.filename)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise RuntimeError("Archive contains a symlink")
                if entry.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(zipped.read(entry))
                    path.chmod(0o755 if path.name in {"decision", "decision.exe"} else 0o644)
    else:
        with tarfile.open(archive, "r:gz") as tar:
            for entry in tar:
                path = target(entry.name)
                if entry.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                elif entry.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(tar.extractfile(entry).read())
                    path.chmod(entry.mode & 0o777)
                else:
                    raise RuntimeError("Archive contains a non-regular file")
    if len(roots) != 1:
        raise RuntimeError("Archive must contain one package directory")
    package = destination / roots.pop()
    if not package.is_dir():
        raise RuntimeError("Archive package directory is missing")
    return package


def verify_payload(package, source_root=ROOT):
    manifest = json.loads((package / "build-manifest.json").read_text(encoding="utf-8"))
    executable = manifest["executable"]
    if executable not in {"decision", "decision.exe"} or manifest["format_version"] != 1 or manifest["name"] != "decision":
        raise RuntimeError("Invalid native distribution manifest")
    if manifest["version"] != package_version(source_root):
        raise RuntimeError("Distribution version differs from Cargo.toml")
    expected = {executable, "LICENSE", "THIRD_PARTY_NOTICES.txt", "build-manifest.json"}
    actual = {path.relative_to(package).as_posix() for path in package.rglob("*") if path.is_file()}
    if actual != expected | {"SHA256SUMS"}:
        raise RuntimeError("Distribution file set is incomplete or contains unexpected files")
    checksums = {}
    for line in (package / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        if name in checksums or name not in expected:
            raise RuntimeError("Distribution checksum names are invalid")
        checksums[name] = digest
        if hashlib.sha256((package / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("Distribution checksum mismatch")
    if set(checksums) != expected:
        raise RuntimeError("Distribution checksums are incomplete")
    if (package / "LICENSE").read_bytes() != (source_root / "LICENSE").read_bytes():
        raise RuntimeError("Distribution license differs from the project license")
    magic = (package / executable).read_bytes()[:4]
    if not (magic == b"\x7fELF" or magic[:2] == b"MZ" or magic in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}):
        raise RuntimeError("Distribution executable is not a native ELF, Mach-O, or PE binary")
    return manifest


def run(command, *, cwd, environment):
    return subprocess.run(command, cwd=cwd, env=environment, text=True, encoding="utf-8", check=True, capture_output=True)


def verify_runtime(package, manifest, workspace, source_root=ROOT):
    binary = package / manifest["executable"]
    home = workspace / "home"
    home.mkdir()
    environment = {name: os.environ[name] for name in SYSTEM_NAMES if name in os.environ}
    environment.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / "Local"), APPDATA=str(home / "Roaming"))
    empty_path = workspace / "empty-path"
    empty_path.mkdir()
    isolated = {**environment, "PATH": str(empty_path), "PYTHONHOME": str(empty_path), "PYTHONPATH": str(empty_path)}
    version = run([str(binary), "--version"], cwd=workspace, environment=isolated).stdout.strip()
    if version != "decision " + manifest["version"]:
        raise RuntimeError("Packaged executable version does not match its manifest")
    preview = run([str(binary), "--json", "run", str(source_root / "examples/request.json"), "--dry-run"],
                  cwd=workspace, environment=isolated)
    if not {"state", "questions"} <= json.loads(preview.stdout).keys():
        raise RuntimeError("Packaged executable did not return a valid request preview")
    exported = workspace / "skill"
    run([str(binary), "skill", "--output", str(exported)], cwd=workspace, environment=isolated)
    expected = {path.relative_to(source_root / "skills/decision"): path.read_bytes()
                for path in (source_root / "skills/decision").rglob("*") if path.is_file()}
    actual = {path.relative_to(exported): path.read_bytes() for path in exported.rglob("*") if path.is_file()}
    if actual != expected:
        raise RuntimeError("Packaged executable skill differs from the committed generated skill")
    for integration in ("cli", "mcp"):
        run([str(binary), "plugin", "--output", str(workspace / integration), "--integration", integration],
            cwd=workspace, environment=isolated)
    tests = {**environment, "DECISION_TEST_EXECUTABLE": str(binary), "DECISION_TEST_CWD": str(workspace),
             "DECISION_TEST_DISTRIBUTION": str(package)}
    subprocess.run([sys.executable, str(ROOT / "scripts/run_native_tests.py"), "--directory", str(source_root / "tests"), "--exclude", "test_codex_plugin.py"],
                   cwd=workspace, env=tests, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--archive", type=Path)
    sources.add_argument("--distribution", type=Path)
    parser.add_argument("--source-root", type=Path, default=ROOT, help="Source checkout whose version, exports, and fixtures match the archive")
    args = parser.parse_args()
    archive = args.archive
    if args.distribution:
        manifest = json.loads((args.distribution / "build-manifest.json").read_text(encoding="utf-8"))
        archive = args.distribution / manifest["archive"]
    archive = archive.resolve()
    with tempfile.TemporaryDirectory(prefix="decision-native-") as directory:
        workspace = Path(directory)
        package = unpack(archive, workspace / "package")
        manifest = verify_payload(package, args.source_root.resolve())
        verify_runtime(package, manifest, workspace, args.source_root.resolve())
    print("Native archive, runtime isolation, exports, provider fixtures, and MCP verified")


if __name__ == "__main__":
    main()
