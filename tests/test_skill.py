"""Skill generation stays deterministic, credential-free, and current."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from decision_cli import cli
from decision_cli.skill import EXAMPLE, skill_files

ROOT = Path(__file__).resolve().parents[1]


class SkillTests(unittest.TestCase):
    def test_committed_export_matches_running_cli(self):
        self.assertEqual(cli.main(["skill", "--output", str(ROOT / "skills/decision"), "--check"]), 0)
        self.assertEqual(EXAMPLE, json.loads((ROOT / "examples/request.json").read_text()))
        self.assertEqual(cli.VERSION, tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"])

    def test_generation_ignores_provider_settings_and_credentials(self):
        expected = skill_files(cli.parser(), cli.VERSION, cli.request_schema())
        with patch.dict(os.environ, {"DECISION_BASE_URL": "invalid-url",
                                    "DECISION_API_KEY_ENV": "PRIVATE_VAR", "PRIVATE_VAR": "do-not-export-credential"}), \
                patch.object(cli, "evaluate_request", side_effect=AssertionError("no provider calls")):
            actual = skill_files(cli.parser(), cli.VERSION, cli.request_schema())
            self.assertEqual(expected, actual)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main(["skill"]), 0)
            self.assertEqual(output.getvalue(), expected["SKILL.md"])
            self.assertNotIn("do-not-export-credential", output.getvalue())
            self.assertNotIn("invalid-url", output.getvalue())

    def test_export_check_and_stale_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(cli.main(["skill", "--output", directory]), 0)
            self.assertEqual(cli.main(["skill", "--output", directory, "--check"]), 0)
            root = Path(directory)
            self.assertEqual(json.loads((root / "references/request-schema.json").read_text()), cli.request_schema())
            (root / "SKILL.md").write_text("stale\n")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["skill", "--output", directory, "--check"]), 2)
                self.assertEqual(cli.main(["skill", "--check"]), 2)


if __name__ == "__main__":
    unittest.main()
