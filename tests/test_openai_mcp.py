"""Real OpenAI SDK CLI/MCP transport against a local Decisions API fixture."""

import asyncio
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from test_images import png_bytes
from test_openai_provider import QUESTIONS
from transport import command, working_directory, isolate


@unittest.skipUnless(importlib.util.find_spec("openai") and importlib.util.find_spec("mcp"), "requires pinned SDKs")
class OpenAITransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), body))
                state = body["input"][0]["content"][0].get("data", {})
                mode = state.get("fixture_mode") if isinstance(state, dict) else None
                answers = []
                for q in body["questions"]:
                    answer = {"type": q["type"], "name": q["name"]}
                    if q["type"] == "predicate":
                        answer["probability"] = 0.9
                    elif q["type"] == "choice":
                        answer.update(choice=q["choices"][0]["value"], confidence=0.8,
                                      probabilities=[{"value": c["value"], "probability": float(i == 0)}
                                                     for i, c in enumerate(q["choices"])])
                    elif q["type"] == "score":
                        answer.update(score=len(q["levels"]) - 1, confidence=0.8,
                                      probabilities=[{"label": level["label"], "value": i,
                                                      "probability": float(i == len(q["levels"]) - 1)}
                                                     for i, level in enumerate(q["levels"])])
                    answers.append(answer)
                if mode == "refusal":
                    answers[0] = {"name": answers[0]["name"], "type": "refusal"}
                elif mode == "invalid":
                    answers[0]["probability"] = "openai-fixture-secret private context"
                elif mode == "missing":
                    answers.pop()
                elif mode == "duplicate":
                    answers[-1] = answers[0]
                result = {"model": body["model"], "answers": answers,
                          "usage": {"input_tokens": 10, "output_tokens": 0, "total_tokens": 10,
                                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                                    "output_tokens_details": {"reasoning_tokens": 0}}}
                if mode == "http":
                    result = {"error": {"message": "openai-fixture-secret private context", "type": "invalid_api_key"}}
                self.send_response(401 if mode == "http" else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.environment = isolate(dict(os.environ, DECISION_PROVIDER="openai",
                                       DECISION_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/custom/v1",
                                       DECISION_API_KEY_ENV="OPENAI_FIXTURE_KEY", OPENAI_FIXTURE_KEY="openai-fixture-secret",
                                       OPENAI_API_KEY="do-not-forward", TYPESAFEAI_API_KEY="do-not-forward",
                                       DECISION_MODEL="fixture-default"))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def run_cli(self, args, payload=None):
        return subprocess.run([*command(), *args], input=json.dumps(payload) if payload else None,
                              env=self.environment, cwd=working_directory(), text=True, capture_output=True, timeout=30)

    def assert_result(self, response):
        self.assertEqual(set(response["answers"]), set(QUESTIONS))
        self.assertEqual(response["answers"]["present"]["noul"], 0.9)
        self.assertEqual(response["answers"]["breed"]["choice"], "french")
        self.assertEqual(response["answers"]["visible"]["score"], 1)
        self.assertEqual(response["answers"]["visible"]["legend"], {"0": "Hidden", "1": "Visible"})
        self.assertEqual(response["usage"], {"input_tokens": 10, "output_tokens": 0})
        self.assertGreaterEqual(response["duration_ms"], 0)

    def assert_wire(self, index):
        path, auth, body = self.requests[index]
        self.assertEqual(path, "/custom/v1/decisions")
        self.assertEqual(auth, "Bearer openai-fixture-secret")
        self.assertEqual([q["type"] for q in body["questions"]], ["predicate", "choice", "score"])
        self.assertEqual([q["name"] for q in body["questions"]], list(QUESTIONS))
        self.assertNotIn("assets", body)
        self.assertNotIn("duration_ms", body)
        return body

    def test_cli_ask_and_run_share_one_image_and_report_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "dog.png"
            photo.write_bytes(png_bytes())
            questions = Path(directory) / "questions.json"
            questions.write_text(json.dumps(QUESTIONS))
            payload = {"state": {"caption": "dog"}, "questions": QUESTIONS}
            invocations = [(["run", "-", "--image", f"dog={photo}", "--json"], payload),
                           (["ask", "--questions-file", str(questions), "--image", f"dog={photo}", "--json"], None)]
            for args, data in invocations:
                before = len(self.requests)
                result = self.run_cli(args, data)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_result(json.loads(result.stdout))
                self.assertEqual(len(self.requests), before + 1)
                body = self.assert_wire(-1)
                parts = body["input"][0]["content"]
                self.assertEqual(parts[0]["data"]["images"]["dog"], "<decision-image:dog>")
                images = [p for p in parts if p["type"] == "input_image"]
                self.assertEqual(len(images), 1)
                self.assertEqual(base64.b64decode(images[0]["image_url"].split(",", 1)[1]), png_bytes())
            human = self.run_cli(["noul", "Is this a dog?", "--image", str(photo)])
            self.assertEqual(human.returncode, 0, human.stderr)
            self.assertIn("Yes probability: 90.0%", human.stdout)
            self.assertIn("Usage: 10 input tokens, 0 output tokens", human.stdout)
            self.assertIn("Duration:", human.stdout)

    def test_cli_rejects_box_and_discards_failed_or_refused_batches(self):
        box = {"state": "dog", "questions": {**QUESTIONS, "location": {"type": "box", "instructions": "Locate dog"}}}
        result = self.run_cli(["run", "-", "--json"], box)
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("does not support Box", result.stderr)
        self.assertEqual(self.requests, [])
        for mode in ("http", "refusal", "missing", "duplicate", "invalid"):
            with self.subTest(mode=mode):
                before = len(self.requests)
                result = self.run_cli(["run", "-", "--json"], {"state": {"fixture_mode": mode}, "questions": QUESTIONS})
                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertNotIn("openai-fixture-secret", result.stderr)
                self.assertNotIn("private context", result.stderr)
                self.assertEqual(len(self.requests), before + 1)

    def test_mcp_schema_images_model_override_and_error_recovery(self):
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
                    variants = tool.input_schema["properties"]["questions"]["additionalProperties"]["oneOf"]
                    self.assertEqual({v["properties"]["type"]["const"] for v in variants}, {"noul", "choice", "score"})
                    self.assertEqual(tool.input_schema["properties"]["questions"]["maxProperties"], 400)
                    payload = {"model": "call-override", "state": {"photo": "<photo>"}, "questions": QUESTIONS,
                               "assets": {"<photo>": {"data_base64": base64.b64encode(png_bytes()).decode()}}}
                    validate(payload, tool.input_schema)
                    result = await session.call_tool("evaluate", payload)
                    self.assertFalse(result.is_error)
                    self.assert_result(result.structured_content)
                    validate(result.structured_content, tool.output_schema)
                    self.assertEqual(json.loads(result.content[0].text), result.structured_content)
                    self.assertEqual(len(self.requests), 1)
                    self.assertEqual(self.assert_wire(-1)["model"], "call-override")
                    for extra in ({"provider": "typesafe"}, {"base_url": "https://other.example"}):
                        result = await session.call_tool("evaluate", {**payload, **extra})
                        self.assertTrue(result.is_error)
                        self.assertEqual(len(self.requests), 1)
                    invalid = {**payload, "assets": {"<photo>": {"path": "/local/image.png"}}}
                    self.assertTrue((await session.call_tool("evaluate", invalid)).is_error)
                    self.assertEqual(len(self.requests), 1)
                    for mode in ("http", "refusal", "missing", "duplicate"):
                        result = await session.call_tool("evaluate", {"state": {"fixture_mode": mode}, "questions": QUESTIONS})
                        self.assertTrue(result.is_error)
                        self.assertIsNone(result.structured_content)
                        self.assertNotIn("openai-fixture-secret", result.content[0].text)
                        self.assertNotIn("private context", result.content[0].text)
                    result = await session.call_tool("evaluate", {"state": "dog", "questions": QUESTIONS})
                    self.assertFalse(result.is_error)
                    self.assert_result(result.structured_content)
                    self.assertEqual(self.requests[-1][2]["model"], "fixture-default")
                    self.assertEqual(len(self.requests), 6)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
