#!/usr/bin/env python3
"""Build only the experimental TypeSafe/external-provider host with PyInstaller."""
import argparse
import hashlib
from importlib.metadata import distribution
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[3]


def notices():
    sections = [("decision — MIT", (ROOT / "LICENSE").read_text())]
    candidates = [Path(sysconfig.get_path("stdlib")) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "Resources/LICENSE"]
    license_path = next(path for path in candidates if path.is_file())
    sections.append(("CPython", license_path.read_text()))
    # Runtime roots, pinned in the repository lock. Do not traverse decision-harness:
    # that metadata includes providers absent from this experimental entrypoint.
    pending = [(name, frozenset()) for name in ("typesafe-sdk", "mcp", "jsonschema")]
    visited, packages = set(), {}
    while pending:
        name, extras = pending.pop()
        name = canonicalize_name(name)
        if (name, extras) in visited:
            continue
        visited.add((name, extras))
        package = distribution(name)
        packages[name] = package
        for entry in package.requires or []:
            requirement = Requirement(entry)
            if requirement.marker is None or any(requirement.marker.evaluate({"extra": extra}) for extra in {"", *extras}):
                pending.append((requirement.name, frozenset(requirement.extras)))
    packages["pyinstaller"] = distribution("pyinstaller")
    for name, package in sorted(packages.items()):
        files = [path for path in package.files or [] if Path(str(path)).name.lower().startswith(("license", "copying", "notice", "copyright"))]
        if not files:
            raise RuntimeError(f"Missing license for {name}")
        for path in sorted(files):
            sections.append((f"{name} {package.version} — {path}", package.locate_file(path).read_text()))
    inventory = "\n".join(f"{name}=={package.version}" for name, package in sorted(packages.items())) + "\n"
    return "\n\n".join(f"{'=' * 72}\n{name}\n{'=' * 72}\n\n{value.rstrip()}" for name, value in sections) + "\n", inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".state/provider-prototype-python")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    text, inventory = notices()
    with tempfile.TemporaryDirectory(prefix="decision-python-prototype-") as temp:
        notice_path = Path(temp) / "THIRD_PARTY_NOTICES.txt"
        notice_path.write_text(text)
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--name", "decision",
                   "--paths", str(ROOT / "src"), "--paths", str(Path(__file__).parent),
                   "--distpath", str(output), "--workpath", str(Path(temp) / "work"), "--specpath", temp,
                   "--exclude-module", "openai", "--exclude-module", "decision_cli.registry",
                   "--exclude-module", "decision_cli.openai_provider", "--exclude-module", "decision_cli.cloudflare_provider",
                   "--exclude-module", "decision_cli.skill", "--exclude-module", "decision_cli.plugin",
                   "--collect-submodules", "typesafe_sdk", "--copy-metadata", "typesafe-sdk", "--copy-metadata", "mcp",
                   "--collect-data", "jsonschema_specifications", "--add-data", f"{notice_path}:.",
                   str(Path(__file__).with_name("host.py"))]
        subprocess.run(command, check=True, env=dict(os.environ, PYINSTALLER_CONFIG_DIR=str(Path(temp) / "cache")))
    binary = output / "decision"
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(binary))
    if archive.extract("THIRD_PARTY_NOTICES.txt") != text.encode():
        raise RuntimeError("Missing bundled license notices")
    # Persist the actual PYZ module inventory for a reviewer to audit exclusions.
    modules = sorted(archive.open_embedded_archive("PYZ.pyz").toc)
    if any(name == "openai" or name.startswith("openai.") for name in modules):
        raise RuntimeError("Unexpected OpenAI dependency in matched build")
    (output / "BUNDLED_MODULES.txt").write_text("\n".join(modules) + "\n")
    (output / "THIRD_PARTY_NOTICES.txt").write_text(text)
    (output / "DEPENDENCIES.txt").write_text(inventory)
    (output / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}\n" for name in ("decision", "THIRD_PARTY_NOTICES.txt", "DEPENDENCIES.txt", "BUNDLED_MODULES.txt")))
    print(binary)


if __name__ == "__main__":
    main()
