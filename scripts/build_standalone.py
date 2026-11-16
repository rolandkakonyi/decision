#!/usr/bin/env python3
"""Build and package the host-native Cargo executable with license notices."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
LEGAL_PREFIXES = ("license", "licence", "copying", "notice", "copyright")


def run(*args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True, encoding="utf-8")


def package_version(root=ROOT):
    return tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"]


def toolchain():
    expected = tomllib.loads((ROOT / "rust-toolchain.toml").read_text(encoding="utf-8"))["toolchain"]["channel"]
    version = run("rustc", "--version").strip()
    if version.split()[1] != expected:
        raise RuntimeError(f"Build requires Rust {expected}")
    host = next(line.removeprefix("host: ") for line in run("rustc", "-vV").splitlines() if line.startswith("host: "))
    return version, host


def resolved_packages(metadata):
    """Include the target's complete resolved graph, including build dependencies."""
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    pending = [metadata["resolve"]["root"]]
    selected = set()
    while pending:
        identity = pending.pop()
        if identity in selected:
            continue
        selected.add(identity)
        pending.extend(dependency["pkg"] for dependency in nodes[identity]["deps"])
    return sorted((package for package in metadata["packages"] if package["id"] in selected),
                  key=lambda package: (package["name"], package["version"]))


def license_notices(metadata, rust_version, sysroot, source_root=ROOT):
    sections = [("decision — MIT", (source_root / "LICENSE").read_text(encoding="utf-8"))]
    rust_docs = Path(sysroot) / "share/doc/rust"
    copyright_file = rust_docs / "COPYRIGHT-library.html"
    licenses = sorted((rust_docs / "licenses").glob("*.txt"))
    if not copyright_file.is_file() or not licenses:
        raise RuntimeError("The Rust toolchain is missing its standard-library copyright or license files")
    sections.append((f"Rust standard library — {rust_version}", copyright_file.read_text(encoding="utf-8")))
    for path in licenses:
        sections.append((f"Rust distribution license text — {path.name}", path.read_text(encoding="utf-8")))
    inventory = []
    for package in resolved_packages(metadata):
        if package["source"] is None:
            continue
        directory = Path(package["manifest_path"]).parent
        notices = {path for path in directory.rglob("*")
                   if path.is_file() and path.name.lower().startswith(LEGAL_PREFIXES)}
        if package.get("license_file"):
            notices.add(directory / package["license_file"])
        if not notices:
            raise RuntimeError(f"No license notices found for {package['name']} {package['version']}")
        names = []
        for path in sorted(notices):
            relative = path.relative_to(directory).as_posix()
            names.append(relative)
            sections.append((f"{package['name']} {package['version']} — {relative}",
                             path.read_text(encoding="utf-8")))
        inventory.append({"name": package["name"], "version": package["version"],
                          "license": package["license"], "notices": names})
    introduction = ("License inventory for the native distribution.\n"
                    "Cargo entries cover the complete resolved build graph for this target, including build tools.\n"
                    "Rust standard-library attribution and accompanying upstream license texts are included below.\n\n")
    text = introduction + "\n\n".join(f"{'=' * 72}\n{name}\n{'=' * 72}\n\n{content.rstrip()}"
                                      for name, content in sections) + "\n"
    return text, inventory


def checksums(directory, names):
    return "".join(f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
                   for name in sorted(names))


def archive_distribution(output, name, files, windows):
    archive = output / (name + (".zip" if windows else ".tar.gz"))
    if windows:
        with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=9) as zipped:
            for filename in sorted(files):
                entry = ZipInfo(name + "/" + filename, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = (0o100755 if filename == "decision.exe" else 0o100644) << 16
                entry.compress_type = ZIP_DEFLATED
                zipped.writestr(entry, (output / filename).read_bytes(), compresslevel=9)
    else:
        with archive.open("wb") as stream, gzip.GzipFile(filename="", fileobj=stream, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for filename in sorted(files):
                    source = output / filename
                    entry = tar.gettarinfo(str(source), arcname=name + "/" + filename)
                    entry.uid = entry.gid = entry.mtime = 0
                    entry.uname = entry.gname = ""
                    entry.mode = 0o755 if filename == "decision" else 0o644
                    entry.pax_headers = {}
                    with source.open("rb") as contents:
                        tar.addfile(entry, contents)
    return archive


def build(output, prebuilt=None, manifest_path=ROOT / "Cargo.toml"):
    rust_version, target = toolchain()
    manifest_path = manifest_path.resolve()
    source_root = manifest_path.parent
    if prebuilt is None:
        subprocess.run(["cargo", "build", "--locked", "--release", "--manifest-path", str(manifest_path)], cwd=ROOT, check=True, stdout=sys.stderr)
    metadata = json.loads(run("cargo", "metadata", "--locked", "--manifest-path", str(manifest_path), "--format-version", "1", "--filter-platform", target))
    executable = "decision.exe" if "windows" in target else "decision"
    source = prebuilt.resolve() if prebuilt else Path(metadata["target_directory"]) / "release" / executable
    version = package_version(source_root)
    environment = {name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR") if name in os.environ}
    actual_version = subprocess.check_output([str(source), "--version"], text=True, encoding="utf-8", env=environment).strip()
    if actual_version != "decision " + version:
        raise RuntimeError("Executable version does not match its Cargo manifest")
    if source.resolve() != (output / executable).resolve():
        shutil.copyfile(source, output / executable)
    (output / executable).chmod(0o755)
    (output / "LICENSE").write_bytes((source_root / "LICENSE").read_bytes())
    notices, dependencies = license_notices(metadata, rust_version, run("rustc", "--print", "sysroot").strip(), source_root)
    (output / "THIRD_PARTY_NOTICES.txt").write_text(notices, encoding="utf-8", newline="\n")
    name = f"decision-{version}-{target}"
    manifest = {"format_version": 1, "name": "decision", "version": version, "target": target,
                "rustc": rust_version, "executable": executable,
                "archive": name + (".zip" if "windows" in target else ".tar.gz"),
                "dependency_scope": "resolved target build graph, including build tools", "dependencies": dependencies}
    (output / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8", newline="\n")
    files = [executable, "LICENSE", "THIRD_PARTY_NOTICES.txt", "build-manifest.json"]
    (output / "SHA256SUMS").write_text(checksums(output, files), encoding="utf-8", newline="\n")
    return archive_distribution(output, name, [*files, "SHA256SUMS"], "windows" in target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--executable", type=Path, help="Package an already-built native executable matching the Cargo manifest")
    parser.add_argument("--manifest-path", type=Path, default=ROOT / "Cargo.toml")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    print(build(output, args.executable, args.manifest_path))


if __name__ == "__main__":
    main()
