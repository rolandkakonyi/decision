"""Deterministic CLI/MCP plugin exports and safe regeneration."""

from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision_cli import cli
from decision_cli.plugin import CATALOG, MANIFEST, MCP, PACKAGE, export_plugin, plugin_files
from decision_cli.skill import skill_files


class PluginTests(unittest.TestCase):
    def files(self, integration="mcp", command="decision", env_vars=()):
        return plugin_files(skill_files(cli.parser(), cli.VERSION, cli.request_schema()),
                            cli.VERSION, integration, command, env_vars)

    def test_modes_bundle_complete_skill_and_declare_only_selected_interface(self):
        skill = skill_files(cli.parser(), cli.VERSION, cli.request_schema())
        for mode in ("cli", "mcp"):
            files = self.files(mode)
            manifest = json.loads(files[MANIFEST])
            self.assertEqual(manifest["skills"], "./skills/decision")
            self.assertEqual(manifest["requires_local_executor"], True)
            self.assertEqual(set(p.relative_to(PACKAGE / "skills/decision").as_posix()
                                 for p in files if p.is_relative_to(PACKAGE / "skills/decision")), set(skill))
            self.assertIn(f"This plugin uses {mode.upper()} integration.", files[PACKAGE / "skills/decision/SKILL.md"])
            if mode == "mcp":
                self.assertEqual(manifest["mcpServers"], "./.mcp.json")
                server = json.loads(files[MCP])["mcpServers"]["decision"]
                self.assertEqual((server["command"], server["args"]), ("decision", ["mcp"]))
                self.assertIn("CLOUDFLARE_ACCOUNT_ID", server["env_vars"])
                self.assertTrue({"GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_BASE_URL"} <= set(server["env_vars"]))
            else:
                self.assertNotIn("mcpServers", manifest)
                self.assertNotIn(MCP, files)

    def test_export_ignores_environment_and_never_reads_or_embeds_credentials(self):
        expected = self.files()
        with patch.dict(os.environ, {"DECISION_PROVIDER": "invalid-provider", "DECISION_BASE_URL": "invalid-url",
                                     "DECISION_MODEL": "private-model", "DECISION_API_KEY_ENV": "PRIVATE_KEY",
                                     "PRIVATE_KEY": "never-export-this-value", "CLOUDFLARE_ACCOUNT_ID": "private-account",
                                     "GEMINI_API_KEY": "never-export-gemini-key"}), \
                patch.object(cli, "connection", side_effect=AssertionError("no credentials")), \
                patch.object(cli, "evaluate_request", side_effect=AssertionError("no inference")):
            self.assertEqual(self.files(), expected)
            with tempfile.TemporaryDirectory() as directory:
                self.assertEqual(cli.main(["plugin", "--output", directory]), 0)
                self.assertEqual(cli.main(["plugin", "--output", directory, "--check"]), 0)
                output = "".join(p.read_text() for p in Path(directory).rglob("*") if p.is_file())
                for value in ("invalid-provider", "invalid-url", "private-model", "never-export-this-value", "private-account", "never-export-gemini-key"):
                    self.assertNotIn(value, output)

    def test_cache_versions_track_mode_launch_configuration_and_skill_content(self):
        original = json.loads(self.files()[MANIFEST])["version"]
        variants = [self.files("cli"), self.files(command="/opt/decision"), self.files(env_vars=["CUSTOM_KEY"])]
        altered_skill = skill_files(cli.parser(), cli.VERSION, cli.request_schema())
        altered_skill["references/examples.md"] += "\nChanged example.\n"
        variants.append(plugin_files(altered_skill, cli.VERSION))
        for files in variants:
            self.assertNotEqual(json.loads(files[MANIFEST])["version"], original)
        self.assertEqual(self.files(env_vars=["CUSTOM_KEY", "CUSTOM_KEY"]), self.files(env_vars=["CUSTOM_KEY"]))

    def test_colliding_unmanaged_files_are_preserved(self):
        for path in (Path("README.md"), MANIFEST, CATALOG):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("User-owned content\n")
                with self.assertRaisesRegex(ValueError, "unmanaged"):
                    export_plugin(self.files(), root)
                self.assertEqual(target.read_text(), "User-owned content\n")
                self.assertEqual({p.relative_to(root) for p in root.rglob("*") if p.is_file()}, {path})

    def test_switching_modes_removes_stale_mcp_and_checks_detect_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export_plugin(self.files(), root)
            export_plugin(self.files(), root, check=True)
            export_plugin(self.files("cli"), root)
            self.assertFalse((root / MCP).exists())
            export_plugin(self.files("cli"), root, check=True)
            with self.assertRaisesRegex(ValueError, "stale"):
                export_plugin(self.files(), root, check=True)
            (root / MANIFEST).write_text("{}\n")
            with self.assertRaisesRegex(ValueError, "stale"):
                export_plugin(self.files("cli"), root, check=True)
            (root / "private.txt").write_text("User-owned data")
            with self.assertRaisesRegex(ValueError, "unmanaged"):
                export_plugin(self.files(), root)
            self.assertEqual((root / "private.txt").read_text(), "User-owned data")

    def test_symlink_output_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                export_plugin(self.files(), link)
            output = root / "output"
            output.mkdir()
            (output / "plugins").symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                export_plugin(self.files(), output)
            self.assertEqual(list(target.iterdir()), [])

    def test_invalid_mcp_configuration_is_rejected_before_export(self):
        for kwargs in ({"integration": "other"}, {"integration": "cli", "env_vars": ["CUSTOM_KEY"]},
                       {"integration": "cli", "command": "/opt/decision"}, {"command": ""}, {"command": "decision\nother"},
                       {"command": "./decision"}, {"command": "python -m decision_cli.cli"},
                       {"env_vars": ["KEY=secret"]}, {"env_vars": ["bad-name"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.files(**kwargs)
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["plugin", "--output", directory, "--env-var", "KEY=secret"]), 2)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
