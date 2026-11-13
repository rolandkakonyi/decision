"""Native CLI contracts exercised through local fixtures, without inference."""

import json
from pathlib import Path
import unittest

from native import ROOT, evaluate, invoke, response_server


def noul_response(body):
    return {"model": body.get("model", "fixture"), "answers": {"answer": {"type": "noul", "noul": 0.75}},
            "usage": {"input_tokens": 0, "output_tokens": None}}


class DecisionTests(unittest.TestCase):
    def test_dry_run_preserves_literal_context_without_credentials(self):
        context = "`$(no)`\nquotes'"
        result = invoke("noul", "Is this literal?", "--state", context, "--dry-run", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], context)
        self.assertEqual(result.stderr, "")

    def test_choice_score_batch_and_json_flag_positions(self):
        for argv, expected in ((["choice", "Which?", "--option", "one=First", "--option", "two=Second"], {"one": "First", "two": "Second"}),
                               (["score", "How blocked?", "--level", "Free", "--level", "Blocked"], ["Free", "Blocked"])):
            for flags in (["--json", *argv], [*argv, "--json"]):
                result = invoke(*flags, "--state", "x", "--dry-run")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["questions"]["answer"]["criteria"], expected)
        result = invoke("run", "-", "--model", "fixture", "--dry-run", "--json", data=(ROOT / "examples/request.json").read_text())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["model"], "fixture")
        self.assertEqual(len(json.loads(result.stdout)["questions"]), 3)

    def test_invalid_input_never_returns_an_answer_or_selected_context(self):
        for flags in (("--timeout", "nan"), ("--timeout", "0"), ("--timeout", "inf"), ("--base-url", "http://remote.example")):
            result = invoke("noul", "Question?", "--state", "x", *flags, "--dry-run", "--json")
            self.assertEqual((result.returncode, result.stdout), (2, ""), result.stderr)
        result = invoke("run", "-", "--dry-run", "--json", data='{"state":"private context"')
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertNotIn("private context", result.stderr)
        result = invoke("ask", "--state-file", "-", "--questions-file", "-", "--dry-run", "--json", data="{}")
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("Only one input", result.stderr)

    def test_custom_host_does_not_receive_implicit_provider_keys(self):
        with response_server(noul_response) as (url, requests):
            for settings in ({"TYPESAFE_API_KEY": "do-not-forward"},
                             {"TYPESAFEAI_API_KEY": "do-not-forward"},
                             {"DECISION_API_KEY": "generic-fixture", "DECISION_API_KEY_ENV": "UNSET_SELECTOR"}):
                result = invoke("noul", "Question?", "--state", "x", "--json", "--base-url", url, env=settings)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
            self.assertEqual(requests, [])

    def test_environment_provider_model_and_flags(self):
        result = invoke("noul", "Question?", "--state", "x", "--dry-run", "--json", env={"DECISION_PROVIDER": "gemini"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["model"], "gemini-decision-1")
        result = invoke("noul", "Question?", "--state", "x", "--provider", "cloudflare", "--model", "clef-flash", "--dry-run", "--json",
                        env={"DECISION_PROVIDER": "gemini", "DECISION_MODEL": "other"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["model"], "clef-flash")

    def test_sdk_model_fallback_and_cli_mcp_model_precedence(self):
        payload = {"state": "x", "questions": {"answer": {"type": "noul", "instructions": "Question?"}}}
        for settings, flags, model in (({}, [], "jev-latest"), ({"TYPESAFE_DEFAULT_MODEL": "sdk-model"}, [], "sdk-model"),
                                       ({"TYPESAFE_DEFAULT_MODEL": "sdk-model", "DECISION_MODEL": "env-model"}, [], "env-model"),
                                       ({"DECISION_MODEL": "env-model"}, ["--model", "flag-model"], "flag-model")):
            result, requests = evaluate("typesafe", payload, noul_response, flags=flags, settings=settings)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(requests[0]["body"]["model"], model)
            self.assertEqual(json.loads(result.stdout)["model"], model)

    def test_duration_includes_provider_work_and_errors_are_redacted(self):
        with response_server(noul_response, delay=0.04) as (url, requests):
            result = invoke("noul", "Question?", "--state", "hello", "--json", "--base-url", url,
                            env={"DECISION_API_KEY": "fixture-provider-key"})
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertGreaterEqual(value["duration_ms"], 30)
            self.assertEqual(value["usage"], {"input_tokens": 0, "output_tokens": None})
            self.assertEqual(len(requests), 1)
        result, requests = evaluate("typesafe", {"state": "private context", "questions": {"answer": {"type": "noul", "instructions": "Question?"}}},
                                    {"error": "fixture-provider-key private context"}, status=401)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertNotIn("fixture-provider-key", result.stderr)
        self.assertNotIn("private context", result.stderr)
        self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
