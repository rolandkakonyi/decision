"""Real Gemini CLI and MCP exchanges against an isolated local HTTP fixture."""

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

from test_gemini_provider import RESPONSE
from test_images import png_bytes
from test_openai_provider import QUESTIONS
from transport import command, working_directory, isolate


class GeminiTransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("x-goog-api-key"), body))
                state = body["input"][0]["text"]
                mode = json.loads(state).get("fixture_mode") if state.startswith("{") else None
                response = copy.deepcopy(RESPONSE)
                response["answers"] = {
                    name: copy.deepcopy(next(a for a in RESPONSE["answers"].values() if a["type"] == q["type"]))
                    for name, q in body["questions"].items()
                }
                if mode == "missing":
                    response["answers"].pop(next(iter(body["questions"])))
                elif mode == "invalid":
                    response["answers"]["present"]["noul"] = "gemini-fixture-secret private context"
                elif mode == "type":
                    response["answers"]["present"]["type"] = "refusal"
                elif mode == "error":
                    response["error"] = {"message": "gemini-fixture-secret private context"}
                code = {"http": 401, "safety": 400, "rate": 429, "server": 503, "redirect": 302}.get(mode, 200)
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                if mode == "redirect":
                    self.send_header("Location", "/must-not-follow")
                self.end_headers()
                encoded = json.dumps(response).encode()
                if mode == "json":
                    encoded = b"gemini-fixture-secret private context"
                elif mode == "duplicate":
                    encoded = encoded.replace(b'"noul": 0.9', b'"noul": 0.9, "noul": 0.9')
                self.wfile.write(encoded)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.environment = isolate(dict(os.environ, DECISION_PROVIDER="gemini", DECISION_MODEL="gemini-decision-1",
                                       DECISION_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/v1beta",
                                       DECISION_API_KEY_ENV="GEMINI_FIXTURE_KEY", GEMINI_FIXTURE_KEY="gemini-fixture-secret",
                                       GEMINI_API_KEY="do-not-forward", GOOGLE_API_KEY="do-not-forward"))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def run_cli(self, args, payload=None):
        return subprocess.run([*command(), *args], input=json.dumps(payload) if payload else None,
                              env=self.environment, cwd=working_directory(), text=True, capture_output=True, timeout=30)

    def assert_result(self, result):
        expected = copy.deepcopy(RESPONSE["answers"])
        del expected["present"]["confidence"]
        self.assertEqual(result["answers"], expected)
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 0})
        self.assertGreaterEqual(result["duration_ms"], 0)

    def assert_wire(self, model="gemini-decision-1"):
        path, auth, body = self.requests[-1]
        self.assertEqual(path, "/v1beta/decisions")
        self.assertEqual(auth, "gemini-fixture-secret")
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
            invocations = [(["run", "-", "--image", f"dog={photo}", "--json"], {"state": "dog", "questions": QUESTIONS}),
                           (["ask", "--questions-file", str(questions), "--image", f"dog={photo}", "--json"], None)]
            for args, payload in invocations:
                before = len(self.requests)
                result = self.run_cli(args, payload)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_result(json.loads(result.stdout))
                self.assertEqual(len(self.requests), before + 1)
                body = self.assert_wire()
                self.assertEqual(json.loads(body["input"][0]["text"])["images"]["dog"], "<decision-image:dog>")
                self.assertIn("<decision-image:dog>", body["input"][1]["text"])
                self.assertEqual(base64.b64decode(body["input"][2]["data"]), png_bytes())
                self.assertEqual(len(body["input"]), 3)
            result = self.run_cli(["noul", "Dog?", "--image", str(photo)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Yes probability: 90.0%", result.stdout)
            self.assertIn("Usage: 10 input tokens, 0 output tokens", result.stdout)

    def test_invalid_input_and_provider_failures_are_atomic_redacted_without_retries(self):
        for questions in ({"location": {"type": "box", "instructions": "Locate dog"}},
                          {"has.dot": QUESTIONS["present"]},
                          {"score": {"type": "score", "instructions": "Rate", "criteria": ["", "Two"]}}):
            result = self.run_cli(["run", "-", "--json"], {"state": "dog", "questions": questions})
            self.assertEqual((result.returncode, result.stdout), (2, ""), result.stderr)
            self.assertEqual(self.requests, [])
        for mode in ("http", "safety", "rate", "server", "redirect", "missing", "invalid", "type", "error", "json", "duplicate"):
            with self.subTest(mode=mode):
                before = len(self.requests)
                result = self.run_cli(["run", "-", "--json"], {"state": {"fixture_mode": mode}, "questions": QUESTIONS})
                self.assertEqual((result.returncode, result.stdout), (1, ""), result.stderr)
                self.assertNotIn("gemini-fixture-secret", result.stderr)
                self.assertNotIn("private context", result.stderr)
                self.assertEqual(len(self.requests), before + 1)

    def test_mcp_schema_settings_isolation_and_recovery(self):
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
                    schema = tool.input_schema["properties"]["questions"]
                    variants = schema["additionalProperties"]["oneOf"]
                    self.assertEqual({v["properties"]["type"]["const"] for v in variants}, {"noul", "choice", "score"})
                    self.assertEqual(schema["maxProperties"], 1000)
                    score = next(v for v in variants if v["properties"]["type"]["const"] == "score")
                    self.assertEqual(score["properties"]["criteria"]["maxItems"], 255)
                    payload = {"state": {"photo": "<photo>"}, "questions": QUESTIONS, "model": "custom-model",
                               "assets": {"<photo>": {"data_base64": base64.b64encode(png_bytes()).decode()}}}
                    validate(payload, tool.input_schema)
                    result = await session.call_tool("evaluate", payload)
                    self.assertFalse(result.is_error, result.content)
                    self.assert_result(result.structured_content)
                    self.assertEqual(result.structured_content["model"], "custom-model")
                    validate(result.structured_content, tool.output_schema)
                    self.assertEqual(json.loads(result.content[0].text), result.structured_content)
                    self.assert_wire("custom-model")
                    for extra in ({"provider": "typesafe"}, {"base_url": "https://other.example"}, {"api_key_env": "OTHER_KEY"}):
                        self.assertTrue((await session.call_tool("evaluate", {**payload, **extra})).is_error)
                        self.assertEqual(len(self.requests), 1)
                    for invalid in ({**payload, "assets": {"<photo>": {"path": "/local/image.png"}}},
                                    {**payload, "questions": {"has.dot": QUESTIONS["present"]}},
                                    {**payload, "questions": {str(i): QUESTIONS["present"] for i in range(1001)}}):
                        self.assertTrue((await session.call_tool("evaluate", invalid)).is_error)
                        self.assertEqual(len(self.requests), 1)
                    for mode in ("http", "error", "missing", "invalid"):
                        result = await session.call_tool("evaluate", {"state": {"fixture_mode": mode}, "questions": QUESTIONS})
                        self.assertTrue(result.is_error)
                        self.assertIsNone(result.structured_content)
                        self.assertNotIn("gemini-fixture-secret", result.content[0].text)
                    result = await session.call_tool("evaluate", {"state": "dog", "questions": QUESTIONS})
                    self.assertFalse(result.is_error, result.content)
                    self.assert_result(result.structured_content)
                    self.assert_wire()
                    self.assertEqual(len(self.requests), 6)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
