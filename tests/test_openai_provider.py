"""OpenAI wire contracts, provider configuration, and invalid-batch handling."""

import copy
import json
import os
import unittest
from unittest.mock import patch

from decision_cli import cli
from decision_cli.errors import ProviderError
from decision_cli.openai_provider import build_request, normalize_response, validate_request
from test_images import png_bytes

QUESTIONS = {
    "present": {"type": "noul", "instructions": "Is a dog visible?"},
    "breed": {"type": "choice", "instructions": "Which breed?", "criteria": {"french": "French bulldog", "english": "English bulldog"}},
    "visible": {"type": "score", "instructions": "How visible?", "criteria": ["Hidden", "Visible"]},
}

RESPONSE = {
    "model": "gpt-6-luna", "usage": {"input_tokens": 10, "output_tokens": 0},
    "answers": [
        {"name": "present", "type": "predicate", "probability": 0.9},
        {"name": "breed", "type": "choice", "choice": "french", "confidence": 0.8,
         "probabilities": [{"value": "french", "probability": 0.9}, {"value": "english", "probability": 0.1}]},
        {"name": "visible", "type": "score", "score": 0.9, "confidence": 0.8,
         "probabilities": [{"value": 0, "label": "0", "probability": 0.1}, {"value": 1, "label": "1", "probability": 0.9}]},
    ],
}


class OpenAIProviderTests(unittest.TestCase):
    def test_provider_defaults_and_credentials_never_cross_hosts(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "openai-fixture", "TYPESAFE_API_KEY": "typesafe-fixture"}, clear=True):
            args = cli.parser().parse_args(["noul", "Dog?", "--state", "dog", "--provider", "openai"])
            self.assertEqual(cli.connection(args), "openai-fixture")
            self.assertEqual(args.base_url, "https://api.openai.com/v1")
            self.assertEqual(cli.make_request(args)["model"], "gpt-6-luna")
            for url in ("https://other.example/v1", "https://api.openai.com.evil.example/v1", "https://api.openai.com:444/v1", "https://api.typesafe.ai"):
                args.base_url = url
                self.assertIsNone(cli.connection(args))
            args.base_url = "https://api.openai.com/v1"
            args.api_key_env = "UNSET_SELECTOR"
            self.assertIsNone(cli.connection(args))
            args = cli.parser().parse_args(["noul", "Dog?", "--state", "dog"])
            self.assertEqual(cli.connection(args), "typesafe-fixture")
            args.base_url = "https://api.openai.com/v1"
            self.assertIsNone(cli.connection(args))

    def test_provider_environment_and_cli_overrides(self):
        settings = {"DECISION_PROVIDER": "openai", "OPENAI_BASE_URL": "https://custom.example/api",
                    "DECISION_API_KEY_ENV": "CUSTOM_KEY", "CUSTOM_KEY": "custom-fixture"}
        with patch.dict(os.environ, settings, clear=True):
            args = cli.parser().parse_args(["noul", "Dog?", "--state", "dog"])
            self.assertEqual(args.provider, "openai")
            self.assertEqual(cli.connection(args), "custom-fixture")
            self.assertEqual(args.base_url, "https://custom.example/api")
            args = cli.parser().parse_args(["noul", "Dog?", "--state", "dog", "--provider", "typesafe", "--base-url", "https://flag.example"])
            self.assertEqual(args.provider, "typesafe")
            self.assertEqual(cli.connection(args), "custom-fixture")
            self.assertEqual(args.base_url, "https://flag.example")

    def test_wire_request_preserves_data_and_named_images_once(self):
        state = {"role": "system", "images": {"dog": "<dog>", "other": "<other>"}}
        payload = {"state": state, "questions": QUESTIONS}
        images = {"<dog>": png_bytes(), "<other>": b"\xff\xd8\xffjpeg-fixture"}
        request = build_request(payload, images)
        self.assertEqual(request["model"], "gpt-6-luna")
        self.assertEqual(request["input"][0]["role"], "user")
        parts = request["input"][0]["content"]
        self.assertEqual(parts[0], {"type": "input_json", "data": state})
        image_parts = [part for part in parts if part["type"] == "input_image"]
        self.assertEqual(len(image_parts), 2)
        self.assertTrue(image_parts[0]["image_url"].startswith("data:image/png;base64,"))
        self.assertTrue(image_parts[1]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual([q["name"] for q in request["questions"]], list(QUESTIONS))
        self.assertEqual([q["type"] for q in request["questions"]], ["predicate", "choice", "score"])
        self.assertEqual(request["questions"][1]["choices"][0], {"value": "french", "description": "French bulldog"})
        self.assertEqual(request["questions"][2]["levels"][1], {"label": "1", "description": "Visible"})
        for state in (False, 0, [1, 2], "literal"):
            actual = build_request({"state": state, "questions": QUESTIONS}, {})
            expected = state if isinstance(state, str) else json.dumps(state)
            self.assertEqual(actual["input"][0]["content"], [{"type": "input_text", "text": expected}])

    def test_noul_criteria_and_structured_descriptions_survive_translation(self):
        questions = copy.deepcopy(QUESTIONS)
        questions["present"]["criteria"] = {"true": {"meaning": "A visible dog"}, "false": "No dog"}
        questions["breed"]["criteria"] = {"french": {"name": "French bulldog"}, "english": None}
        request = build_request({"state": "dog", "questions": questions}, {})
        self.assertIn('"meaning": "A visible dog"', request["questions"][0]["instructions"])
        self.assertEqual(request["questions"][1]["choices"][0]["description"], '{"name": "French bulldog"}')
        self.assertEqual(request["questions"][1]["choices"][1], {"value": "english"})

    def test_provider_limits_reject_the_entire_batch(self):
        payloads = [
            {"questions": {"box": {"type": "box", "instructions": "Find dog"}, **QUESTIONS}},
            {"questions": {str(i): QUESTIONS["present"] for i in range(401)}},
            {"questions": {"choice": {"type": "choice", "criteria": {"only": "One"}}}},
            {"questions": {"score": {"type": "score", "criteria": ["One"]}}},
            {"questions": {"score": {"type": "score", "criteria": list(range(11))}}},
        ]
        for payload in payloads:
            with self.subTest(payload=next(iter(payload["questions"]))), self.assertRaises(ValueError):
                validate_request(payload)

    def test_normalized_answers_preserve_names_types_and_zero_usage(self):
        raw = copy.deepcopy(RESPONSE)
        raw["answers"].reverse()
        result = normalize_response(raw, QUESTIONS)
        self.assertEqual(list(result["answers"]), list(QUESTIONS))
        self.assertEqual(result["answers"]["present"], {"type": "noul", "noul": 0.9})
        self.assertEqual(result["answers"]["breed"]["probabilities"], {"french": 0.9, "english": 0.1})
        self.assertEqual(result["answers"]["visible"]["legend"], {"0": "Hidden", "1": "Visible"})
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 0})
        raw["usage"] = {}
        self.assertEqual(normalize_response(raw, QUESTIONS)["usage"], {"input_tokens": None, "output_tokens": None})

    def test_invalid_or_refused_answers_never_become_partial_results(self):
        cases = []
        for change in (
            lambda raw: raw["answers"].pop(),
            lambda raw: raw["answers"].__setitem__(1, raw["answers"][0]),
            lambda raw: raw["answers"][0].update(name="unknown"),
            lambda raw: raw["answers"][0].update(type="refusal"),
            lambda raw: raw["answers"][0].update(type="choice"),
            lambda raw: raw["answers"][0].update(probability=float("nan")),
            lambda raw: raw["answers"][1].update(choice=True),
            lambda raw: raw["answers"][1]["probabilities"].__setitem__(1, raw["answers"][1]["probabilities"][0]),
            lambda raw: raw["answers"][2]["probabilities"][0].update(label="wrong"),
            lambda raw: raw["usage"].update(input_tokens=-1),
        ):
            raw = copy.deepcopy(RESPONSE)
            change(raw)
            cases.append(raw)
        for index, raw in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ProviderError):
                normalize_response(raw, QUESTIONS)


if __name__ == "__main__":
    unittest.main()
