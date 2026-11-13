"""Verify native archive contents, notices, checksums, and packaging policy."""

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_standalone import archive_distribution, checksums, package_version, resolved_packages
from release_version import release_checksums, unreleased_tag
from verify_distribution import unpack, verify_payload


class NativePackagingTests(unittest.TestCase):
    def payload(self, root, windows=False):
        root.mkdir(parents=True, exist_ok=True)
        executable = "decision.exe" if windows else "decision"
        (root / executable).write_bytes(b"MZfixture" if windows else b"\x7fELFfixture")
        (root / "LICENSE").write_bytes((ROOT / "LICENSE").read_bytes())
        (root / "THIRD_PARTY_NOTICES.txt").write_text("fixture notices\n", encoding="utf-8")
        (root / "build-manifest.json").write_text(json.dumps({
            "format_version": 1, "name": "decision", "version": package_version(),
            "target": "x86_64-pc-windows-msvc" if windows else "x86_64-unknown-linux-gnu",
            "executable": executable,
        }), encoding="utf-8")
        names = [executable, "LICENSE", "THIRD_PARTY_NOTICES.txt", "build-manifest.json"]
        (root / "SHA256SUMS").write_text(checksums(root, names), encoding="utf-8", newline="\n")
        return [*names, "SHA256SUMS"]

    def test_native_tar_and_zip_are_deterministic_and_self_contained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for windows in (False, True):
                output = root / str(windows)
                files = self.payload(output, windows)
                archive = archive_distribution(output, "decision-fixture", files, windows)
                first = archive.read_bytes()
                archive_distribution(output, "decision-fixture", files, windows)
                self.assertEqual(archive.read_bytes(), first)
                package = unpack(archive, root / ("unpacked-" + str(windows)))
                self.assertEqual(verify_payload(package)["executable"], "decision.exe" if windows else "decision")
                self.assertEqual({path.name for path in package.iterdir()}, set(files))

    def test_modified_executable_or_missing_notices_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.payload(root)
            (root / "decision").write_bytes(b"\x7fELFmodified")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                verify_payload(root)
            self.payload(root)
            (root / "THIRD_PARTY_NOTICES.txt").unlink()
            with self.assertRaisesRegex(RuntimeError, "file set"):
                verify_payload(root)

    def test_archive_paths_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, entry in enumerate((tarfile.TarInfo("../escape"), tarfile.TarInfo("decision/link"))):
                archive = root / f"{index}.tar.gz"
                if index:
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = "/outside"
                with tarfile.open(archive, "w:gz") as tar:
                    tar.addfile(entry, io.BytesIO())
                with self.assertRaises(RuntimeError):
                    unpack(archive, root / f"unpack-{index}")

    def test_license_inventory_uses_only_reachable_target_packages(self):
        metadata = {"packages": [{"id": "root", "name": "decision", "version": "1"},
                                  {"id": "used", "name": "used", "version": "1"},
                                  {"id": "other-target", "name": "other-target", "version": "1"}],
                    "resolve": {"root": "root", "nodes": [{"id": "root", "deps": [{"pkg": "used"}]},
                                                           {"id": "used", "deps": []},
                                                           {"id": "other-target", "deps": []}]}}
        self.assertEqual([package["id"] for package in resolved_packages(metadata)], ["root", "used"])

    def test_existing_tags_are_never_reused(self):
        tag = "v" + package_version()
        with patch("release_version.subprocess.check_output", return_value=json.dumps([{"ref": "refs/tags/" + tag}])):
            self.assertIsNone(unreleased_tag("fixture/repository"))
        with patch("release_version.subprocess.check_output", return_value=json.dumps([{"ref": "refs/tags/" + tag + "-other"}])):
            self.assertEqual(unreleased_tag("fixture/repository"), tag)
        with patch("release_version.subprocess.check_output", side_effect=RuntimeError("API unavailable")):
            with self.assertRaises(RuntimeError):
                unreleased_tag("fixture/repository")

    def test_release_checksums_require_all_three_platform_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            version = package_version()
            names = [f"decision-{version}-x86_64-unknown-linux-gnu.tar.gz",
                     f"decision-{version}-aarch64-apple-darwin.tar.gz",
                     f"decision-{version}-x86_64-pc-windows-msvc.zip"]
            for name in names[:2]:
                (root / name).write_bytes(b"fixture")
            with self.assertRaises(RuntimeError):
                release_checksums(root)
            (root / names[2]).write_bytes(b"fixture")
            release_checksums(root)
            self.assertEqual((root / "SHA256SUMS").read_text(), checksums(root, names))


@unittest.skipUnless(os.getenv("DECISION_TEST_DISTRIBUTION"), "requires an extracted native distribution")
class NativeDistributionTests(unittest.TestCase):
    def test_packaged_native_executable_has_complete_notices_and_checksums(self):
        package = Path(os.environ["DECISION_TEST_DISTRIBUTION"])
        manifest = verify_payload(package)
        self.assertEqual(Path(os.environ["DECISION_TEST_EXECUTABLE"]).resolve(), (package / manifest["executable"]).resolve())
        text = (package / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
        for section in ("decision — MIT", "Rust standard library", "reqwest ", "rmcp ", "serde_json "):
            self.assertIn(section, text)
        self.assertIn((ROOT / "LICENSE").read_text(encoding="utf-8"), text)
        self.assertTrue(manifest["rustc"].startswith("rustc 1.94.1 "))
        self.assertIn("reqwest", {dependency["name"] for dependency in manifest["dependencies"]})
        for line in (package / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((package / name).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
