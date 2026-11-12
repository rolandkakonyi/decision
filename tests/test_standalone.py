"""Check notices in the actual compiled distribution, without inference."""

import hashlib
import importlib.util
import os
from pathlib import Path
import unittest


@unittest.skipUnless(os.getenv("DECISION_TEST_EXECUTABLE") and importlib.util.find_spec("PyInstaller"),
                     "requires a standalone build and the build dependency group")
class StandaloneNoticeTests(unittest.TestCase):
    def test_embedded_notices_cover_runtime_dependencies_and_match_sidecar(self):
        from PyInstaller.archive.readers import CArchiveReader

        binary = Path(os.environ["DECISION_TEST_EXECUTABLE"])
        sidecar = binary.with_name("THIRD_PARTY_NOTICES.txt")
        notices = sidecar.read_bytes()
        self.assertEqual(CArchiveReader(str(binary)).extract("THIRD_PARTY_NOTICES.txt"), notices)
        text = notices.decode("utf-8")
        for section in ("decision — MIT", "CPython", "pyinstaller ", "anyio ", "sniffio ", "openai ", "typesafe-sdk "):
            self.assertIn(section, text)
        root = Path(__file__).resolve().parents[1]
        self.assertIn((root / "LICENSE").read_text(), text)
        for line in binary.with_name("SHA256SUMS").read_text().splitlines():
            expected, name = line.split("  ")
            self.assertEqual(hashlib.sha256(binary.with_name(name).read_bytes()).hexdigest(), expected)
