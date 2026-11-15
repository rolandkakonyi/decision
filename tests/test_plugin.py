"""Black-box deterministic native CLI/MCP exports and safe regeneration."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from native import invoke, response_server

PACKAGE = Path("plugins/decision")
MANIFEST = PACKAGE / ".codex-plugin/plugin.json"
MCP = PACKAGE / ".mcp.json"
CATALOG = Path(".agents/plugins/marketplace.json")


class PluginTests(unittest.TestCase):
    def args(self, output, integration="mcp", command="decision", env_vars=()):
        result = ["plugin", "--output", str(output), "--integration", integration]
        if command != "decision":
            result += ["--mcp-command", command]
        for name in env_vars:
            result += ["--env-var", name]
        return result

    def export(self, output, integration="mcp", command="decision", env_vars=(), check=False, env=None):
        result = invoke(*self.args(output, integration, command, env_vars), *(["--check"] if check else []), env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def files(self, integration="mcp", command="decision", env_vars=(), env=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.export(root, integration, command, env_vars, env=env)
            return {p.relative_to(root): p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}

    def cache_version(self, files):
        manifest = json.loads(files[MANIFEST])
        version = manifest.pop("version").split("+", 1)[0]
        digest = hashlib.sha256((json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode())
        for name, content in sorted(files.items(), key=lambda item: item[0].as_posix()):
            if name in {MANIFEST, CATALOG, Path("README.md")}:
                continue
            digest.update(name.as_posix().encode() + b"\0" + content.encode())
        return version + "+" + digest.hexdigest()[:16]

    def test_modes_bundle_complete_skill_and_declare_only_selected_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = invoke("skill", "--output", root)
            self.assertEqual(result.returncode, 0, result.stderr)
            skill = {p.relative_to(root) for p in root.rglob("*") if p.is_file()}
        for mode in ("cli", "mcp"):
            files = self.files(mode)
            manifest = json.loads(files[MANIFEST])
            self.assertEqual(manifest["skills"], "./skills/decision")
            self.assertIs(manifest["requires_local_executor"], True)
            self.assertEqual({p.relative_to(PACKAGE / "skills/decision") for p in files
                              if p.is_relative_to(PACKAGE / "skills/decision")}, skill)
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
        settings = {"DECISION_PROVIDER": "invalid-provider", "DECISION_BASE_URL": "invalid-url",
                    "DECISION_MODEL": "private-model", "DECISION_API_KEY_ENV": "PRIVATE_KEY",
                    "PRIVATE_KEY": "never-export-this-value", "CLOUDFLARE_ACCOUNT_ID": "private-account",
                    "GEMINI_API_KEY": "never-export-gemini-key"}
        self.assertEqual(self.files(env=settings), expected)
        with tempfile.TemporaryDirectory() as directory, response_server({}) as (url, requests):
            root = Path(directory)
            self.export(root, env=settings)
            self.export(root, check=True, env=settings)
            output = "".join(p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file())
            for value in settings.values():
                self.assertNotIn(value, output)
            self.export(root, env={"DECISION_BASE_URL": url, "DECISION_API_KEY": "fixture-key"})
            self.assertEqual(requests, [])

    def test_cache_versions_track_mode_launch_configuration_and_skill_content(self):
        original_files = self.files()
        original = json.loads(original_files[MANIFEST])["version"]
        self.assertEqual(original, self.cache_version(original_files))
        absolute = str(Path(tempfile.gettempdir()).resolve() / "decision with spaces")
        for files in (self.files("cli"), self.files(command=absolute), self.files(env_vars=["CUSTOM_KEY"])):
            self.assertNotEqual(json.loads(files[MANIFEST])["version"], original)
            self.assertEqual(json.loads(files[MANIFEST])["version"], self.cache_version(files))
        # The native unit test mutates the generator's skill input. This checks
        # the published version against actual export bytes without a test hook.
        altered = dict(original_files)
        altered[PACKAGE / "skills/decision/references/examples.md"] += "\nChanged example.\n"
        self.assertNotEqual(self.cache_version(altered), original)
        self.assertEqual(self.files(env_vars=["CUSTOM_KEY", "CUSTOM_KEY"]), self.files(env_vars=["CUSTOM_KEY"]))

    def test_colliding_unmanaged_files_are_preserved(self):
        for path in (Path("README.md"), MANIFEST, CATALOG):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("User-owned content\n", encoding="utf-8")
                result = invoke(*self.args(root))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("unmanaged", result.stderr)
                self.assertEqual(target.read_text(encoding="utf-8"), "User-owned content\n")
                self.assertEqual({p.relative_to(root) for p in root.rglob("*") if p.is_file()}, {path})

    def test_switching_modes_removes_stale_mcp_and_checks_detect_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.export(root)
            self.export(root, check=True)
            self.export(root, "cli")
            self.assertFalse((root / MCP).exists())
            self.export(root, "cli", check=True)
            result = invoke(*self.args(root), "--check")
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("stale", result.stderr)
            (root / MANIFEST).write_text("{}\n", encoding="utf-8")
            result = invoke(*self.args(root, "cli"), "--check")
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("stale", result.stderr)
            (root / "private.txt").write_text("User-owned data", encoding="utf-8")
            result = invoke(*self.args(root))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("unmanaged", result.stderr)
            self.assertEqual((root / "private.txt").read_text(encoding="utf-8"), "User-owned data")

    def test_symlink_output_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            result = invoke(*self.args(link))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("symlink", result.stderr)
            output = root / "output"
            output.mkdir()
            (output / "plugins").symlink_to(target, target_is_directory=True)
            result = invoke(*self.args(output))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("symlink", result.stderr)
            self.assertEqual(list(target.iterdir()), [])

    def test_invalid_mcp_configuration_is_rejected_before_export(self):
        absolute = str(Path(tempfile.gettempdir()).resolve() / "decision")
        for flags in (("--integration", "other"), ("--integration", "cli", "--env-var", "CUSTOM_KEY"),
                      ("--integration", "cli", "--mcp-command", absolute), ("--mcp-command", ""),
                      ("--mcp-command", "decision\nother"), ("--mcp-command", "./decision"),
                      ("--mcp-command", "python -m provider"), ("--env-var", "KEY=secret"),
                      ("--env-var", "bad-name")):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as directory:
                result = invoke("plugin", "--output", directory, *flags)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(list(Path(directory).iterdir()), [])
                self.assertNotIn("KEY=secret", result.stderr)


if __name__ == "__main__":
    unittest.main()
