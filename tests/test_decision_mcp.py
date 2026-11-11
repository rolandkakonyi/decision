"""Exercise CLI and MCP batches through a local System One fixture."""

import asyncio
import base64
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from test_images import png_bytes

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src/decision_cli/cli.py"
REQUEST = json.loads((ROOT / "examples/request.json").read_text())
HAS_SDKS = bool(importlib.util.find_spec("mcp") and importlib.util.find_spec("typesafe_sdk"))


def command():
    binary = os.getenv("DECISION_TEST_EXECUTABLE")
    return [binary] if binary else [sys.executable, str(SCRIPT)]


def working_directory():
    return os.getenv("DECISION_TEST_CWD")


@unittest.skipUnless(HAS_SDKS, "requires pinned MCP and TypeSafe SDKs")
class DecisionTransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.uploads = []
        requests = self.requests
        uploads = self.uploads

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                content_type = self.headers["Content-Type"]
                if content_type.startswith("multipart/form-data"):
                    message = BytesParser(policy=default).parsebytes(("Content-Type: " + content_type + "\r\n\r\n").encode() + raw)
                    parts = {p.get_param("name", header="content-disposition"): p for p in message.iter_parts()}
                    body = json.loads(parts.pop("request").get_payload(decode=True))
                    uploads.append(({name: part.get_payload(decode=True) for name, part in parts.items()},
                                    self.headers.get("TypeSafe-Danger-Zone-Images")))
                else:
                    body = json.loads(raw)
                requests.append((self.path, self.headers.get("Authorization"), body))
                failure = body["state"] == "fail"
                answers = {}
                for name, question in body["questions"].items():
                    if question["type"] == "noul":
                        answers[name] = {"type": "noul", "noul": 0.9}
                    elif question["type"] == "choice":
                        names = list(question["criteria"])
                        answers[name] = {"type": "choice", "choice": names[0], "confidence": 1,
                                         "probabilities": {n: float(n == names[0]) for n in names}}
                    elif question["type"] == "score":
                        levels = question["criteria"]
                        answers[name] = {"type": "score", "score": 0, "confidence": 1,
                                         "legend": {str(i): level for i, level in enumerate(levels)},
                                         "probabilities": {str(i): float(i == 0) for i in range(len(levels))}}
                    elif question["type"] == "box":
                        answers[name] = {"type": "box", "box": {"left": 0.1, "top": 0.2, "right": 0.8, "bottom": 0.9}, "confidence": 0.8}
                if body["state"] == "missing":
                    answers.pop(next(iter(answers)))
                if body["state"] == "wrong_type":
                    answers["team"] = {"type": "noul", "noul": 0.5}
                result = {"model": body["model"], "usage": {"input_tokens": 10, "output_tokens": 3}, "answers": answers}
                if failure:
                    result = {"error": "provider-secret private context"}
                self.send_response(401 if failure else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.environment = dict(os.environ, DECISION_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/custom",
                                DECISION_API_KEY_ENV="FIXTURE_KEY", FIXTURE_KEY="provider-secret",
                                DECISION_MODEL="fixture-default", TYPESAFEAI_API_KEY="do-not-forward")
        mode = os.getenv("DECISION_TEST_MODE", "standalone" if os.getenv("DECISION_TEST_EXECUTABLE") else "source")
        if mode not in {"source", "wheel", "standalone"}:
            raise ValueError("DECISION_TEST_MODE must be source, wheel, or standalone")
        if mode == "standalone":
            self.environment.update(PATH="/nonexistent", PYTHONHOME="/nonexistent", PYTHONPATH="/nonexistent")
        elif mode == "wheel":
            self.environment.pop("PYTHONHOME", None)
            self.environment.pop("PYTHONPATH", None)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def assert_batch(self, result):
        self.assertEqual(set(result["answers"]), set(REQUEST["questions"]))
        self.assertEqual(result["answers"]["team"]["choice"], "billing")
        self.assertEqual(result["answers"]["refund"]["noul"], 0.9)
        self.assertEqual(result["answers"]["blocking"]["score"], 0)

    def test_cli_ask_and_run_send_one_request_for_all_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            questions = Path(directory) / "questions.json"
            questions.write_text(json.dumps(REQUEST["questions"]))
            invocations = [(["run", "-"], json.dumps(REQUEST)),
                           (["ask", "--state", "shared context", "--questions-file", str(questions)], None)]
            for args, data in invocations:
                before = len(self.requests)
                result = subprocess.run([*command(), *args, "--json"], input=data, env=self.environment,
                                        capture_output=True, text=True, timeout=30, cwd=working_directory())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_batch(json.loads(result.stdout))
                self.assertEqual(len(self.requests), before + 1)
                path, auth, payload = self.requests[-1]
                self.assertEqual(path, "/custom/v1/systemone")
                self.assertEqual(auth, "Bearer provider-secret")
                self.assertEqual(payload["questions"], REQUEST["questions"])

    def test_mcp_handshake_discovery_batches_and_recoverable_errors(self):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from jsonschema import validate

        async def check():
            cmd = command()
            parameters = StdioServerParameters(command=cmd[0], args=[*cmd[1:], "mcp"], env=self.environment, cwd=working_directory())
            with tempfile.TemporaryFile(mode="w+") as diagnostics:
                async with stdio_client(parameters, errlog=diagnostics) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=20) as session:
                        initialization = await session.initialize()
                        self.assertEqual(initialization.server_info.name, "decision")
                        tools = (await session.list_tools()).tools
                        self.assertEqual([t.name for t in tools], ["evaluate"])
                        tool = tools[0]
                        validate(REQUEST, tool.input_schema)
                        self.assertTrue(tool.annotations.read_only_hint)
                        result = await session.call_tool("evaluate", {**REQUEST, "model": "call-override"})
                        self.assertFalse(result.is_error)
                        self.assert_batch(result.structured_content)
                        validate(result.structured_content, tool.output_schema)
                        self.assertEqual(len(self.requests), 1)
                        self.assertEqual(self.requests[0][2]["model"], "call-override")
                        self.assertEqual(self.requests[0][1], "Bearer provider-secret")
                        bad = await session.call_tool("evaluate", {"state": "private context", "questions": {}})
                        self.assertTrue(bad.is_error)
                        self.assertNotIn("private context", bad.content[0].text)
                        self.assertEqual(len(self.requests), 1)
                        redirected = await session.call_tool("evaluate", {**REQUEST, "base_url": "https://unselected.example"})
                        self.assertTrue(redirected.is_error)
                        self.assertEqual(len(self.requests), 1)
                        malformed = await session.call_tool("evaluate", {
                            "state": "private context", "questions": {"bad": {"type": [], "instructions": "x"}}})
                        self.assertTrue(malformed.is_error)
                        self.assertEqual(len(self.requests), 1)
                        for state in ("fail", "missing", "wrong_type"):
                            result = await session.call_tool("evaluate", {**REQUEST, "state": state})
                            self.assertTrue(result.is_error)
                            self.assertIsNone(result.structured_content)
                            self.assertNotIn("provider-secret", result.content[0].text)
                            self.assertNotIn("private context", result.content[0].text)
                        # Failure doesn't poison the stdio session.
                        result = await session.call_tool("evaluate", REQUEST)
                        self.assertFalse(result.is_error)
                        self.assert_batch(result.structured_content)
                        self.assertEqual(len(self.requests), 5)
                        self.assertEqual(self.requests[-1][2]["model"], "fixture-default")
                diagnostics.seek(0)
                self.assertNotIn("provider-secret", diagnostics.read())

        asyncio.run(check())

    def test_cli_image_uploads_once_for_mixed_questions_and_human_box_output(self):
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "photo.png"
            photo.write_bytes(png_bytes())
            payload = {**REQUEST, "questions": {**REQUEST["questions"], "location": {"type": "box", "instructions": "Locate the square in images.photo"}}}
            result = subprocess.run([*command(), "run", "-", "--image", f"photo={photo}", "--model", "jev-1.14.0", "--json"],
                                    input=json.dumps(payload), env=self.environment, text=True, capture_output=True, timeout=30, cwd=working_directory())
            self.assertEqual(result.returncode, 0, result.stderr)
            answers = json.loads(result.stdout)["answers"]
            self.assertEqual(set(answers), set(payload["questions"]))
            self.assertEqual(len(self.requests), 1)
            self.assertEqual(len(self.uploads[0][0]), 1)
            self.assertEqual(next(iter(self.uploads[0][0].values())), png_bytes())
            self.assertEqual(self.uploads[0][1], "true")
            body = self.requests[0][2]
            reference = body["state"]["images"]["photo"]
            self.assertIn(reference, body["assets"])
            self.assertIn(body["assets"][reference]["part"], self.uploads[0][0])
            human = subprocess.run([*command(), "box", "Find the square", "--image", str(photo)],
                                   env=self.environment, text=True, capture_output=True, timeout=30, cwd=working_directory())
            self.assertEqual(human.returncode, 0, human.stderr)
            self.assertIn("Box: left=0.1, top=0.2", human.stdout)

    def test_mcp_base64_images_are_uploaded_and_paths_are_rejected(self):
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from jsonschema import validate

        async def check():
            cmd = command()
            parameters = StdioServerParameters(command=cmd[0], args=[*cmd[1:], "mcp"], env=self.environment, cwd=working_directory())
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=20) as session:
                    await session.initialize()
                    tool = (await session.list_tools()).tools[0]
                    payload = {"state": {"photo": "<photo>"}, "model": "jev-1.14.0",
                               "questions": {"red": {"type": "noul", "instructions": "Is the photo red?"},
                                             "location": {"type": "box", "instructions": "Locate the square"}},
                               "assets": {"<photo>": {"data_base64": base64.b64encode(png_bytes()).decode(), "format": "png"}}}
                    validate(payload, tool.input_schema)
                    result = await session.call_tool("evaluate", payload)
                    self.assertFalse(result.is_error)
                    self.assertEqual(set(result.structured_content["answers"]), {"red", "location"})
                    self.assertEqual(len(self.requests), 1)
                    self.assertEqual(next(iter(self.uploads[0][0].values())), png_bytes())
                    self.assertEqual(self.uploads[0][1], "true")
                    invalid = {**payload, "assets": {"<photo>": {"path": "/local/image.png"}}}
                    result = await session.call_tool("evaluate", invalid)
                    self.assertTrue(result.is_error)
                    self.assertIn("data_base64", result.content[0].text)
                    self.assertEqual(len(self.requests), 1)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
