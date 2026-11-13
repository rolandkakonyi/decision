"""Collect locked host-target dependencies, license texts and native linkage."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    manifest = Path(__file__).with_name("Cargo.toml")
    host = next(line.removeprefix("host: ") for line in command("rustc", "-vV").splitlines() if line.startswith("host: "))
    metadata = json.loads(command("cargo", "metadata", "--locked", "--format-version", "1", "--filter-platform", host,
                                  "--manifest-path", str(manifest)))
    nodes = {node["id"]: node for node in metadata["resolve"]["nodes"]}
    packages = {package["id"]: package for package in metadata["packages"]}
    selected = set()

    def walk(identifier):
        if identifier in selected:
            return
        selected.add(identifier)
        for dependency in nodes[identifier]["dependencies"]:
            walk(dependency)

    walk(metadata["resolve"]["root"])
    inventory = []
    notices = ["Decision provider-runtime Rust prototype dependency notices\n",
               "Includes the locked host-target dependency graph, including build tools and proc macros.\n"
               "Not every listed crate contributes runtime code. Full available license and notice texts follow.\n"]
    license_name = re.compile(r"^(licen[cs]e|copying|copyright|notice|authors)([-._].*)?$", re.IGNORECASE)
    for package in sorted((packages[key] for key in selected if packages[key]["source"]), key=lambda p: (p["name"], p["version"])):
        source = Path(package["manifest_path"]).parent
        paths = {path for path in source.rglob("*") if path.is_file() and license_name.fullmatch(path.name)}
        if package.get("license_file"):
            paths.add(source / package["license_file"])
        item = {key: package.get(key) for key in ("name", "version", "license", "repository")}
        item["license_texts"] = [str(path.relative_to(source)) for path in sorted(paths)]
        if not paths:
            raise RuntimeError(f"No license text in package {package['name']} {package['version']}")
        inventory.append(item)
        notices.append(f"\n{'=' * 72}\n{package['name']} {package['version']}\nSPDX: {package.get('license')}\nRepository: {package.get('repository')}\n")
        for path in sorted(paths):
            notices.append(f"\n--- {path.relative_to(source)} ---\n")
            notices.append(path.read_text(errors="replace"))
            notices.append("\n")
    output = args.output.resolve()
    shutil.copyfile(root / "LICENSE", output / "LICENSE")
    (output / "THIRD_PARTY_NOTICES.txt").write_text("".join(notices))
    binary = output / "decision"
    linkage_command = ["otool", "-L", str(binary)] if platform.system() == "Darwin" else ["ldd", str(binary)]
    linkage = command(*linkage_command).replace(str(binary), "decision")
    (output / "linkage.txt").write_text(linkage + "\n")
    report = {"target": host, "rustc": command("rustc", "--version"), "cargo": command("cargo", "--version"),
              "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "binary_bytes": binary.stat().st_size, "native_linkage": linkage,
              "dependency_scope": "Locked host-target graph including build and proc-macro dependencies",
              "packages": inventory}
    (output / "dependencies.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(inventory)} locked host-target dependencies; license texts and native linkage collected")


if __name__ == "__main__":
    main()
