#!/usr/bin/env python3
"""Select an untagged Cargo version for main-only CI release publication."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import quote

from build_standalone import package_version


def unreleased_tag(repository):
    tag = "v" + package_version()
    references = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repository}/git/matching-refs/tags/{quote(tag, safe='')}"],
        text=True, encoding="utf-8"))
    if any(reference["ref"] == "refs/tags/" + tag for reference in references):
        return None
    return tag


def release_checksums(directory):
    files = sorted([*directory.glob("*.tar.gz"), *directory.glob("*.zip")])
    version = package_version()
    expected = {f"decision-{version}-x86_64-unknown-linux-gnu.tar.gz",
                f"decision-{version}-aarch64-apple-darwin.tar.gz",
                f"decision-{version}-x86_64-pc-windows-msvc.zip"}
    if {path.name for path in files} != expected:
        raise RuntimeError("Release requires the three expected native platform archives")
    (directory / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in files),
        encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checksums", type=Path)
    args = parser.parse_args()
    if args.checksums:
        release_checksums(args.checksums)
        return
    tag = unreleased_tag(os.environ["GITHUB_REPOSITORY"])
    if tag:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8", newline="\n") as output:
            output.write("tag=" + tag + "\n")
    else:
        print("Cargo version already has a tag; no release will be published")


if __name__ == "__main__":
    main()
