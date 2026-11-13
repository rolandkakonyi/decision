"""Run the same black-box provider-runtime contract against either executable."""

import argparse
import asyncio
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

from fixtures import MCP, PNG, REQUEST, archive, environment, image_request, package, provider_server, structured
from plugin_fixture import answers_for

BINARY = None


def running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="decision-prototype-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.home = self.root / "plugins"
        self.env = environment()
        self.marker = self.root / "launches.jsonl"
        self.source = package(self.root / "source", marker=self.marker)

    def cli(self, *args, data=None, env=None, success=True):
        result = subprocess.run([BINARY, *map(str, args)], input=data, capture_output=True, text=True,
                                env=env or self.env, timeout=35)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("fixture-only-key", result.stderr)
        self.assertNotIn("provider-private-body", result.stderr)
        return result

    def install(self, source=None, *extra):
        result = self.cli("providers", "install", source or self.source, "--plugin-home", self.home, *extra)
        value = json.loads(result.stdout)
        self.assertEqual(value["name"], "fixture")
        self.assertTrue(value["restart_required"])
        return value

    def run_payload(self, payload, provider="typesafe", *extra, env=None, success=True):
        return self.cli("run", "-", "--json", "--provider", provider, "--plugin-home", self.home,
                        *extra, data=json.dumps(payload), env=env, success=success)

    def mcp_command(self, provider="typesafe", *extra):
        return [BINARY, "mcp", "--provider", provider, "--plugin-home", str(self.home), *map(str, extra)]

    def assert_result(self, result, request=REQUEST):
        self.assertEqual(result["answers"], answers_for(request))
        self.assertEqual(result["usage"], {"input_tokens": 0, "output_tokens": None})
        self.assertIs(type(result["usage"]["input_tokens"]), int)
        self.assertIsNot(type(result["duration_ms"]), bool)
        self.assertTrue(math.isfinite(result["duration_ms"]))
        self.assertGreaterEqual(result["duration_ms"], 0)
        self.assertEqual(result["model"], request["model"])

    def records(self):
        return [json.loads(line) for line in self.marker.read_text().splitlines()] if self.marker.exists() else []

    def test_version_without_credentials_or_runtime_path(self):
        result = self.cli("--version", env={"PATH": "/nonexistent", "HOME": str(self.root)})
        self.assertEqual(result.stdout, "decision prototype 1\n")

    def test_typesafe_cli_batch_and_integer_state(self):
        with provider_server() as (url, requests):
            result = json.loads(self.run_payload(REQUEST, "typesafe", "--base-url", url + "/custom").stdout)
        self.assert_result(result)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["path"], "/custom/v1/systemone")
        self.assertEqual(requests[0]["auth"], "Bearer fixture-only-key")
        self.assertEqual(requests[0]["body"]["state"]["large"], 9007199254740993)
        self.assertNotIn("duration_ms", requests[0]["body"])

    def test_typesafe_cli_png_box_multipart(self):
        payload = image_request()
        with provider_server() as (url, requests):
            result = json.loads(self.run_payload(payload, "typesafe", "--base-url", url).stdout)
        self.assert_result(result, payload)
        self.assertEqual(len(requests), 1)
        self.assertEqual(list(requests[0]["uploads"].values()), [PNG])
        self.assertEqual(requests[0]["opt_in"], "true")
        part = next(iter(requests[0]["uploads"]))
        self.assertEqual(requests[0]["body"]["assets"], {"<photo>": {"type": "image", "part": part}})

    def test_cli_path_image_and_credential_free_preview(self):
        payload = image_request()
        image = self.root / "photo.png"
        image.write_bytes(PNG)
        payload["assets"]["<photo>"] = {"path": str(image), "format": "png"}
        env = {key: value for key, value in self.env.items() if key != "FIXTURE_KEY"}
        with provider_server() as (url, requests):
            preview = json.loads(self.run_payload(payload, "typesafe", "--base-url", url, "--dry-run", env=env).stdout)
            self.assertEqual(preview["state"], payload["state"])
            self.assertEqual(preview["questions"], payload["questions"])
            self.assertEqual(requests, [])
            result = json.loads(self.run_payload(payload, "typesafe", "--base-url", url).stdout)
        self.assert_result(result, payload)
        self.assertEqual(list(requests[0]["uploads"].values()), [PNG])

    def test_typesafe_atomic_errors_and_no_redirect_retry(self):
        with provider_server() as (url, requests):
            for mode in ("missing_answer", "negative_usage", "provider_error", "redirect"):
                with self.subTest(mode=mode):
                    before = len(requests)
                    payload = {**REQUEST, "state": {"fixture_mode": mode}}
                    result = self.run_payload(payload, "typesafe", "--base-url", url, success=False)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(len(requests), before + 1)

    def test_local_install_setup_and_plugin_evaluation(self):
        self.install()
        markers = list(self.home.rglob("setup-ran"))
        self.assertEqual(len(markers), 1)
        modified = markers[0].stat().st_mtime_ns
        result = json.loads(self.run_payload(REQUEST, "fixture").stdout)
        self.assert_result(result)
        self.assertEqual(markers[0].stat().st_mtime_ns, modified)
        records = self.records()
        self.assertIn("evaluate", [item.get("method") for item in records])
        self.assertTrue(all(item["integer"] == 9007199254740993 for item in records if item.get("method") in {"prepare", "evaluate"}))

    def test_disabled_provider_never_launches(self):
        self.install()
        self.marker.unlink(missing_ok=True)
        result = self.run_payload(REQUEST, "fixture", "--disable-provider", "fixture", success=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.records(), [])

    def test_external_raw_result_validation_and_safe_errors(self):
        self.install()
        for mode in ("missing_answer", "wrong_type", "negative_usage", "float_usage", "bool_answer",
                     "leak_error", "malformed_output", "duplicate_output", "mutated_prepare"):
            with self.subTest(mode=mode):
                result = self.run_payload({**REQUEST, "state": {"fixture_mode": mode}}, "fixture", success=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")

    def test_external_dry_run_has_no_evaluation_or_setup(self):
        self.install()
        self.marker.unlink(missing_ok=True)
        env = {key: value for key, value in self.env.items() if key != "FIXTURE_KEY"}
        preview = json.loads(self.run_payload(REQUEST, "fixture", "--dry-run", env=env).stdout)
        self.assertEqual(preview["state"], REQUEST["state"])
        self.assertNotIn("evaluate", [item["method"] for item in self.records()])

    def test_failed_setup_preserves_active_registration(self):
        self.install()
        broken = package(self.root / "broken", "2.0.0", setup_fails=True)
        result = self.cli("providers", "install", broken, "--plugin-home", self.home, success=False)
        self.assertNotEqual(result.returncode, 0)
        entries = json.loads(self.cli("providers", "list", "--plugin-home", self.home).stdout)
        self.assertEqual(next(item for item in entries if item["name"] == "fixture")["version"], "1.0.0")
        self.assert_result(json.loads(self.run_payload(REQUEST, "fixture").stdout))

    def test_git_install_pinned_source(self):
        for args in (["init", "--initial-branch=main"], ["add", "."],
                     ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-m", "Fixture package"]):
            subprocess.run(["git", *args], cwd=self.source, capture_output=True, check=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.source, text=True).strip()
        self.install("git+" + self.source.as_uri() + "#" + revision)
        self.assert_result(json.loads(self.run_payload(REQUEST, "fixture").stdout))

    def test_archive_install_verifies_hash(self):
        digest = archive(self.source, self.root / "fixture.tar.gz")
        with provider_server(self.root) as (url, _):
            result = self.cli("providers", "install", url + "/fixture.tar.gz", "--sha256", "0" * 64,
                              "--plugin-home", self.home, success=False)
            self.assertNotEqual(result.returncode, 0)
            self.install(url + "/fixture.tar.gz", "--sha256", digest)
        self.assert_result(json.loads(self.run_payload(REQUEST, "fixture").stdout))

    def test_project_configuration_is_explicit(self):
        project = self.root / "project" / "providers.json"
        self.install(None, "--project-config", project)
        global_entries = json.loads(self.cli("providers", "list", "--plugin-home", self.home).stdout)
        self.assertNotIn("fixture", [item["name"] for item in global_entries])
        project_entries = json.loads(self.cli("providers", "list", "--plugin-home", self.home,
                                              "--project-config", project).stdout)
        self.assertIn("fixture", [item["name"] for item in project_entries])
        result = self.run_payload(REQUEST, "fixture", "--project-config", project)
        self.assert_result(json.loads(result.stdout))

    def test_typesafe_mcp_schema_images_and_routing_rejection(self):
        from decision_cli.cli import request_schema, response_schema
        with provider_server() as (url, requests):
            with MCP(self.mcp_command("typesafe", "--base-url", url)) as client:
                tools = client.request("tools/list", {})["tools"]
                self.assertEqual([item["name"] for item in tools], ["evaluate"])
                self.assertEqual(tools[0]["inputSchema"], request_schema())
                self.assertEqual(tools[0]["outputSchema"], response_schema())
                result = client.call("evaluate", image_request())
                self.assertFalse(result.get("isError", False))
                self.assert_result(structured(result))
                self.assertEqual(list(requests[-1]["uploads"].values()), [PNG])
                before = len(requests)
                for invalid in ({**REQUEST, "provider": "fixture"},
                                {**REQUEST, "assets": {"<photo>": {"path": "/local/photo.png"}}}):
                    result = client.call("evaluate", invalid)
                    self.assertTrue(result.get("isError"))
                    self.assertEqual(len(requests), before)

    def test_mcp_and_plugin_accept_maximum_asset_frame(self):
        import base64
        self.install()
        image = PNG + b"\0" * (16 * 1024 * 1024 - len(PNG))
        encoded = base64.b64encode(image).decode()
        payload = deepcopy(REQUEST)
        payload["state"]["images"] = ["<photo>", "<second-photo>"]
        payload["assets"] = {name: {"data_base64": encoded, "format": "png"}
                             for name in ("<photo>", "<second-photo>")}
        with MCP(self.mcp_command("fixture", "--timeout", "20")) as client:
            result = client.call("evaluate", payload)
            self.assertFalse(result.get("isError", False))
            self.assert_result(structured(result), payload)

    def test_management_mcp_install_list_remove_and_new_inference(self):
        with MCP(self.mcp_command("typesafe", "--management")) as manager:
            tools = manager.request("tools/list", {})["tools"]
            self.assertEqual({item["name"] for item in tools}, {"provider_install", "provider_list", "provider_remove"})
            result = manager.call("provider_install", {"source": str(self.source)})
            self.assertFalse(result.get("isError", False))
            self.assertEqual(structured(result)["name"], "fixture")
            entries = structured(manager.call("provider_list", {}))
            self.assertIn("fixture", [item["name"] for item in entries])
            with MCP(self.mcp_command("fixture")) as inference:
                result = inference.call("evaluate", REQUEST)
                self.assertFalse(result.get("isError", False))
                self.assert_result(structured(result))
            result = manager.call("provider_remove", {"name": "fixture"})
            self.assertTrue(structured(result)["removed"])

    def test_official_mcp_client_accepts_management_results(self):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        async def check():
            command = self.mcp_command("typesafe", "--management")
            params = StdioServerParameters(command=command[0], args=command[1:], env=self.env)
            with tempfile.TemporaryFile(mode="w+") as diagnostics:
                async with stdio_client(params, errlog=diagnostics) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=20) as session:
                        await session.initialize()
                        tools = (await session.list_tools()).tools
                        self.assertEqual({tool.name for tool in tools}, {"provider_install", "provider_list", "provider_remove"})
                        for name, args in (("provider_install", {"source": str(self.source)}),
                                           ("provider_list", {}), ("provider_remove", {"name": "fixture"})):
                            result = await session.call_tool(name, args)
                            self.assertFalse(result.is_error)
                            self.assertTrue(result.content or result.structured_content)
        asyncio.run(check())

    def test_running_mcp_keeps_pinned_artifact_after_update_and_remove(self):
        self.install()
        payload = {**REQUEST, "state": {"fixture_mode": "version"}}
        with MCP(self.mcp_command("fixture")) as old:
            self.assertEqual(structured(old.call("evaluate", payload))["answers"]["red"]["noul"], 0.1)
            new_source = package(self.root / "updated", "2.0.0")
            self.install(new_source)
            self.assertEqual(structured(old.call("evaluate", payload))["answers"]["red"]["noul"], 0.1)
            with MCP(self.mcp_command("fixture")) as new:
                self.assertEqual(structured(new.call("evaluate", payload))["answers"]["red"]["noul"], 0.2)
            self.cli("providers", "remove", "fixture", "--plugin-home", self.home)
            self.assertEqual(structured(old.call("evaluate", payload))["answers"]["red"]["noul"], 0.1)

    def assert_processes_stop(self, pids):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(running(pid) for pid in pids):
            time.sleep(0.05)
        remaining = [pid for pid in pids if running(pid)]
        for pid in remaining:
            os.kill(pid, signal.SIGKILL)
        self.assertEqual(remaining, [], "plugin processes remained alive")

    def test_cli_timeout_reaps_plugin_process_group(self):
        self.install()
        result = self.run_payload({**REQUEST, "state": {"fixture_mode": "hang"}}, "fixture", "--timeout", "1", success=False)
        self.assertEqual(result.returncode, 1)
        records = self.records()
        pids = [item["pid"] for item in records if item.get("method") == "evaluate"]
        pids += [item["child_pid"] for item in records if "child_pid" in item]
        self.assertGreaterEqual(len(pids), 2)
        self.assert_processes_stop(pids)

    def test_mcp_cancellation_reaps_plugin_process_group(self):
        self.install()
        with MCP(self.mcp_command("fixture", "--timeout", "20")) as client:
            identifier = client.start("tools/call", {"name": "evaluate", "arguments": {**REQUEST, "state": {"fixture_mode": "hang"}}})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not any("child_pid" in item for item in self.records()):
                time.sleep(0.05)
            records = self.records()
            pids = [item["pid"] for item in records if item.get("method") == "evaluate"]
            pids += [item["child_pid"] for item in records if "child_pid" in item]
            self.assertGreaterEqual(len(pids), 2)
            client.notify("notifications/cancelled", {"requestId": identifier, "reason": "fixture cancellation"})
            self.assert_processes_stop(pids)
            client.request("ping", {})


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes[test.id().split(".")[-1]] = {"status": "passed"}

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcomes[str(test)] = {"status": "failed", "detail": self._exc_info_to_string(err, test)}

    def addError(self, test, err):
        super().addError(test, err)
        self.outcomes[str(test)] = {"status": "error", "detail": self._exc_info_to_string(err, test)}


def main():
    global BINARY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--test", help="Run one named Acceptance method")
    args = parser.parse_args()
    BINARY = str(Path(args.binary).resolve())
    suite = unittest.TestSuite([Acceptance(args.test)]) if args.test else unittest.defaultTestLoader.loadTestsFromTestCase(Acceptance)
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    report = {"binary": BINARY, "tests_run": result.testsRun, "passed": result.wasSuccessful(),
              "failures": len(result.failures), "errors": len(result.errors), "outcomes": result.outcomes,
              "failure_details": [{"test": str(test), "detail": detail} for test, detail in result.failures + result.errors]}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
