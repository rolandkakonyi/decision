"""Real Clef HTTP CLI/MCP requests against a local Workers AI fixture."""

import asyncio
import base64
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from test_cloudflare_provider import RESPONSE
from test_images import png_bytes
from test_openai_provider import QUESTIONS
from transport import command, working_directory, isolate


class CloudflareTransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), body))
                state = body["state"]
                mode = state.get("fixture_mode") if isinstance(state, dict) else None
                response = copy.deepcopy(RESPONSE)
                response["result"]["model"] = body["model"]
                response["result"]["answers"] = {
                    name: {**RESPONSE["result"]["answers"][next(k for k, a in RESPONSE["result"]["answers"].items() if a["type"] == q["type"])]}
                    for name, q in body["questions"].items()
                }
                if mode == "failure":
                    response.update(success=False, errors=[{"message": "cf-fixture-secret private context"}])
                elif mode == "missing":
                    response["result"]["answers"].pop(next(iter(body["questions"])))
                elif mode == "invalid":
                    response["result"]["answers"]["present"]["noul"] = "cf-fixture-secret private context"
                elif mode == "type":
                    response["result"]["answers"]["present"]["type"] = "box"
                status = {"http": 401, "rate": 429, "redirect": 302}.get(mode, 200)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if mode == "redirect":
                    self.send_header("Location", "/must-not-follow")
                self.end_headers()
                encoded = json.dumps(response).encode()
                if mode == "json":
                    encoded = b"cf-fixture-secret private context"
                elif mode == "duplicate":
                    encoded = encoded.replace(b'"success": true', b'"success": true, "success": true')
                self.wfile.write(encoded)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.environment = isolate(dict(DECISION_PROVIDER="cloudflare",
                                       DECISION_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/custom/run",
                                       DECISION_API_KEY_ENV="CF_FIXTURE_KEY", CF_FIXTURE_KEY="cf-fixture-secret",
                                       CLOUDFLARE_API_TOKEN="do-not-forward", OPENAI_API_KEY="do-not-forward",
                                       DECISION_MODEL="clef", DECISION_ACCOUNT_ID="", CLOUDFLARE_ACCOUNT_ID=""))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def run_cli(self, args, payload=None):
        return subprocess.run([*command(), *args], input=json.dumps(payload) if payload else None,
                              env=self.environment, cwd=working_directory(), text=True, capture_output=True, timeout=30)

    def assert_result(self, result):
        self.assertEqual(result["answers"], RESPONSE["result"]["answers"])
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 0})
        self.assertGreaterEqual(result["duration_ms"], 0)

    def assert_wire(self, model="clef"):
        path, auth, body = self.requests[-1]
        self.assertEqual(path, "/custom/run/@cf/cloudflare/" + model)
        self.assertEqual(auth, "Bearer cf-fixture-secret")
        self.assertEqual(body["model"], model)
        self.assertEqual(body["questions"], QUESTIONS)
        self.assertNotIn("assets", body)
        self.assertNotIn("duration_ms", body)
        return body

    def test_cli_mixed_ask_run_images_and_readable_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "dog.png"
            photo.write_bytes(png_bytes())
            questions = Path(directory) / "questions.json"
            questions.write_text(json.dumps(QUESTIONS))
            payload = {"state": {"caption": "dog"}, "questions": QUESTIONS}
            invocations = [(["run", "-", "--image", f"dog={photo}", "--json"], payload),
                           (["ask", "--questions-file", str(questions), "--image", f"dog={photo}",
                             "--model", "@cf/cloudflare/clef-flash", "--json"], None)]
            for args, data in invocations:
                before = len(self.requests)
                result = self.run_cli(args, data)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_result(json.loads(result.stdout))
                self.assertEqual(len(self.requests), before + 1)
                body = self.assert_wire("clef-flash" if args[0] == "ask" else "clef")
                self.assertEqual(body["state"]["images"]["dog"], "<decision-image:dog>")
                self.assertEqual(body["state"]["_decision_image_references"], [
                    {"reference": "<decision-image:dog>", "image_position_1_based": 1}])
                self.assertEqual(len(body["images"]), 1)
                self.assertEqual(base64.b64decode(body["images"][0].split(",", 1)[1]), png_bytes())
            human = self.run_cli(["noul", "Dog?", "--image", str(photo)])
            self.assertEqual(human.returncode, 0, human.stderr)
            self.assertIn("Yes probability: 90.0%", human.stdout)
            self.assertIn("Usage: 10 input tokens, 0 output tokens", human.stdout)
            self.assertIn("Duration:", human.stdout)

    def test_preflight_and_provider_failures_are_atomic_redacted_and_not_retried(self):
        box = {"state": "dog", "questions": {**QUESTIONS, "location": {"type": "box", "instructions": "Locate dog"}}}
        result = self.run_cli(["run", "-", "--json"], box)
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("does not support Box", result.stderr)
        self.assertEqual(self.requests, [])
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "oversize.png"
            photo.write_bytes(png_bytes() + b"x" * (4 * 1024 * 1024))
            for flags in ([], ["--dry-run"]):
                result = self.run_cli(["noul", "Dog?", "--image", str(photo), "--json", *flags])
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("4 MiB", result.stderr)
                self.assertEqual(self.requests, [])
        for mode in ("http", "rate", "redirect", "failure", "missing", "invalid", "type", "json", "duplicate"):
            with self.subTest(mode=mode):
                before = len(self.requests)
                result = self.run_cli(["run", "-", "--json"], {"state": {"fixture_mode": mode}, "questions": QUESTIONS})
                self.assertEqual((result.returncode, result.stdout), (1, ""), result.stderr)
                self.assertNotIn("cf-fixture-secret", result.stderr)
                self.assertNotIn("private context", result.stderr)
                self.assertEqual(len(self.requests), before + 1)

    def test_mcp_capabilities_model_override_server_settings_and_recovery(self):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from jsonschema import validate

        async def check():
            cmd = command()
            parameters = StdioServerParameters(command=cmd[0], args=[*cmd[1:], "mcp"],
                                               env=self.environment, cwd=working_directory())
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=30) as session:
                    await session.initialize()
                    tool = (await session.list_tools()).tools[0]
                    questions_schema = tool.input_schema["allOf"][0]["properties"]["questions"]
                    variants = questions_schema["additionalProperties"]["oneOf"]
                    self.assertEqual({v["properties"]["type"]["const"] for v in variants}, {"noul", "choice", "score"})
                    self.assertEqual(questions_schema["maxProperties"], 64)
                    self.assertEqual(tool.input_schema["allOf"][0]["properties"]["assets"]["maxProperties"], 4)
                    payload = {"model": "@cf/cloudflare/clef-flash", "state": {"photo": "<photo>"}, "questions": QUESTIONS,
                               "assets": {"<photo>": {"data_base64": base64.b64encode(png_bytes()).decode()}}}
                    validate(payload, tool.input_schema)
                    result = await session.call_tool("evaluate", payload)
                    self.assertFalse(result.is_error, result.content)
                    self.assert_result(result.structured_content)
                    validate(result.structured_content, tool.output_schema)
                    self.assertEqual(json.loads(result.content[0].text), result.structured_content)
                    self.assertEqual(len(self.requests), 1)
                    self.assert_wire("clef-flash")
                    for extra in ({"provider": "typesafe"}, {"base_url": "https://other.example"},
                                  {"account_id": "a" * 32}, {"api_key_env": "OTHER_KEY"}):
                        self.assertTrue((await session.call_tool("evaluate", {**payload, **extra})).is_error)
                        self.assertEqual(len(self.requests), 1)
                    for invalid in ({**payload, "assets": {"<photo>": {"path": "/local/image.png"}}},
                                    {**payload, "questions": {"has space": QUESTIONS["present"]}},
                                    {**payload, "questions": {str(i): QUESTIONS["present"] for i in range(65)}}):
                        self.assertTrue((await session.call_tool("evaluate", invalid)).is_error)
                        self.assertEqual(len(self.requests), 1)
                    for mode in ("http", "failure", "missing", "invalid"):
                        result = await session.call_tool("evaluate", {"state": {"fixture_mode": mode}, "questions": QUESTIONS})
                        self.assertTrue(result.is_error)
                        self.assertIsNone(result.structured_content)
                        self.assertNotIn("cf-fixture-secret", result.content[0].text)
                    result = await session.call_tool("evaluate", {"state": "dog", "questions": QUESTIONS})
                    self.assertFalse(result.is_error)
                    self.assert_result(result.structured_content)
                    self.assert_wire()
                    self.assertEqual(len(self.requests), 6)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
