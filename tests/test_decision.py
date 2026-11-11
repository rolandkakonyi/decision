"""Decision CLI contracts; local fixtures only, no inference."""

from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "src/decision_cli/cli.py"
loader = importlib.machinery.SourceFileLoader("decision", str(HELPER))
spec = importlib.util.spec_from_loader(loader.name, loader)
decision = importlib.util.module_from_spec(spec)
loader.exec_module(decision)


class DecisionTests(unittest.TestCase):
    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = decision.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_needs_no_credentials_and_preserves_literal_context(self):
        with patch.dict(os.environ, {}, clear=True):
            code, out, err = self.invoke("noul", "Is this literal?", "--state", "`$(no)`\nquotes'", "--dry-run", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "`$(no)`\nquotes'")
        self.assertEqual(err, "")

    def test_choice_score_and_batch(self):
        code, out, _ = self.invoke("choice", "Which?", "--state", "x", "--option", "one=First", "--option", "two=Second", "--dry-run", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["questions"]["answer"]["criteria"], {"one": "First", "two": "Second"})
        code, out, _ = self.invoke("score", "How blocked?", "--state", "x", "--level", "Free", "--level", "Blocked", "--dry-run", "--json")
        self.assertEqual(json.loads(out)["questions"]["answer"]["criteria"], ["Free", "Blocked"])
        self.assertEqual(code, 0)
        with patch("sys.stdin", io.StringIO((ROOT / "examples/request.json").read_text())):
            code, out, _ = self.invoke("run", "-", "--model", "fixture", "--dry-run", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["model"], "fixture")
        self.assertEqual(len(json.loads(out)["questions"]), 3)

    def test_invalid_input_is_not_an_answer(self):
        for extra in (("--timeout", "nan"), ("--timeout", "0"), ("--base-url", "http://remote.example")):
            code, out, _ = self.invoke("noul", "Question?", "--state", "x", *extra, "--dry-run", "--json")
            self.assertEqual((code, out), (2, ""))
        with patch("sys.stdin", io.StringIO('{"state": "private context"')):
            code, out, err = self.invoke("run", "-", "--dry-run", "--json")
        self.assertEqual((code, out), (2, ""))
        self.assertNotIn("private context", err)
        with patch("sys.stdin", io.StringIO("{}")):
            code, out, err = self.invoke("ask", "--state-file", "-", "--questions-file", "-", "--dry-run", "--json")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("Only one input", err)

    def test_custom_host_never_inherits_typesafe_credentials(self):
        with patch.dict(os.environ, {"TYPESAFEAI_API_KEY": "fixture-typesafe"}, clear=True):
            for url in ("https://other.example/v1", "https://api.typesafe.ai.evil.example", "https://api.typesafe.ai:444"):
                args = decision.parser().parse_args(["noul", "Question?", "--state", "x", "--base-url", url])
                self.assertIsNone(decision.connection(args))
            args.base_url = "https://api.typesafe.ai"
            self.assertEqual(decision.connection(args), "fixture-typesafe")
            args.api_key_env = "UNSET_PROVIDER_KEY"
            self.assertIsNone(decision.connection(args))

    def test_environment_selects_provider_and_flags_override_it(self):
        settings = {"DECISION_BASE_URL": "https://other.example/api", "DECISION_API_KEY_ENV": "OTHER_KEY",
                    "OTHER_KEY": "other-fixture", "FLAG_KEY": "flag-fixture", "DECISION_MODEL": "other-model"}
        with patch.dict(os.environ, settings, clear=True):
            args = decision.parser().parse_args(["noul", "Question?", "--state", "x"])
            self.assertEqual(args.base_url, settings["DECISION_BASE_URL"])
            self.assertEqual(args.model, "other-model")
            self.assertEqual(decision.connection(args), "other-fixture")
            args = decision.parser().parse_args(["noul", "Question?", "--state", "x", "--api-key-env", "FLAG_KEY",
                                                "--base-url", "https://flag.example", "--model", "flag-model"])
            self.assertEqual(decision.connection(args), "flag-fixture")
            self.assertEqual(args.base_url, "https://flag.example")
            self.assertEqual(args.model, "flag-model")

    @unittest.skipUnless(importlib.util.find_spec("typesafe_sdk"), "requires typesafe-sdk==0.7.5")
    def test_real_sdk_transport_and_error_redaction(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), body))
                failed = body["state"] == "fail"
                data = ({"error": "fixture-provider-key private context"} if failed else {
                    "model": "fixture", "usage": {"input_tokens": 1, "output_tokens": 1},
                    "answers": {"answer": {"type": "noul", "noul": 0.75}},
                })
                self.send_response(401 if failed else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = dict(os.environ, DECISION_API_KEY="fixture-provider-key", TYPESAFEAI_API_KEY="never-forward")
            for state, expected in (("hello", 0), ("fail", 1)):
                result = subprocess.run([sys.executable, str(HELPER), "noul", "Is it a greeting?", "--state", state, "--json",
                                         "--base-url", f"http://127.0.0.1:{server.server_port}/custom", "--model", "fixture"],
                                        env=env, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected == 0:
                    self.assertEqual(json.loads(result.stdout)["answers"]["answer"]["noul"], 0.75)
                else:
                    self.assertEqual(result.stdout, "")
                    self.assertNotIn("fixture-provider-key", result.stderr)
                    self.assertNotIn("private context", result.stderr)
            self.assertEqual(len(requests), 2)  # No retries, including on errors.
            self.assertTrue(all(path == "/custom/v1/systemone" for path, _, _ in requests), requests)
            self.assertTrue(all(auth == "Bearer fixture-provider-key" for _, auth, _ in requests))
            self.assertEqual(requests[0][2]["model"], "fixture")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
