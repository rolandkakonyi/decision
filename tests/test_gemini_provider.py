"""Gemini request translation, credential routing, and atomic response validation."""

import base64
import copy
import json
import os
import unittest
from unittest.mock import patch

from decision_cli import cli, gemini_provider as gemini
from decision_cli.errors import ProviderError
from test_images import png_bytes
from test_openai_provider import QUESTIONS

RESPONSE = {
    "answers": {
        "present": {"type": "noul", "noul": 0.9, "confidence": 0.9},
        "breed": {"type": "choice", "choice": "french", "confidence": 0.9,
                  "probabilities": {"french": 0.9, "english": 0.1}},
        "visible": {"type": "score", "score": 0.9, "confidence": 0.9,
                    "legend": {"0": "Hidden", "1": "Visible"}, "probabilities": {"0": 0.1, "1": 0.9}},
    },
    "usage": {"total_input_tokens": 10, "total_output_tokens": 0, "total_tokens": 10,
              "input_tokens_by_modality": [{"modality": "text", "tokens": 10}]},
}


class GeminiProviderTests(unittest.TestCase):
    def args(self, *extra):
        return cli.parser().parse_args(["noul", "Dog?", "--state", "dog", "--provider", "gemini", *extra])

    def test_defaults_key_precedence_and_host_isolation(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-fixture", "GOOGLE_API_KEY": "google-fixture",
                                     "OPENAI_API_KEY": "unrelated-fixture"}, clear=True):
            args = self.args()
            self.assertEqual(cli.connection(args), "gemini-fixture")
            self.assertEqual(args.base_url, gemini.API_ROOT)
            self.assertEqual(cli.make_request(args)["model"], gemini.DEFAULT_MODEL)
            del os.environ["GEMINI_API_KEY"]
            self.assertEqual(cli.connection(args), "google-fixture")
            os.environ["DECISION_API_KEY"] = "explicit-fixture"
            self.assertEqual(cli.connection(args), "explicit-fixture")
            del os.environ["DECISION_API_KEY"]
            for url in ("https://other.example/api", "https://generativelanguage.googleapis.com.evil.example",
                        "https://generativelanguage.googleapis.com:444/v1beta", "http://127.0.0.1:9999"):
                args.base_url = url
                self.assertIsNone(cli.connection(args))
            args.base_url = gemini.API_ROOT
            args.api_key_env = "MISSING_KEY"
            self.assertIsNone(cli.connection(args))
            args = cli.parser().parse_args(["noul", "Dog?", "--state", "dog"])
            args.base_url = gemini.API_ROOT
            self.assertIsNone(cli.connection(args))

    def test_environment_and_flag_overrides(self):
        settings = {"DECISION_PROVIDER": "gemini", "GEMINI_BASE_URL": "https://custom.example/api",
                    "DECISION_API_KEY_ENV": "CUSTOM_KEY", "CUSTOM_KEY": "fixture", "DECISION_MODEL": "custom-model"}
        with patch.dict(os.environ, settings, clear=True):
            args = cli.parser().parse_args(["noul", "Dog?", "--state", "dog"])
            self.assertEqual(args.provider, "gemini")
            self.assertEqual(cli.connection(args), "fixture")
            self.assertEqual(args.base_url, settings["GEMINI_BASE_URL"])
            self.assertEqual(cli.make_request(args)["model"], "custom-model")
            os.environ["DECISION_BASE_URL"] = "https://generic.example/api"
            args = self.args()
            cli.connection(args)
            self.assertEqual(args.base_url, os.environ["DECISION_BASE_URL"])
            args = self.args("--base-url", "https://flag.example", "--model", "flag-model")
            cli.connection(args)
            self.assertEqual(args.base_url, "https://flag.example")
            self.assertEqual(cli.make_request(args)["model"], "flag-model")

    def test_translation_preserves_context_criteria_and_named_images_once(self):
        questions = copy.deepcopy(QUESTIONS)
        questions["present"]["criteria"] = {"true": {"meaning": "dog visible"}, "false": "No dog"}
        questions["breed"]["criteria"] = {"french": None, "english": {"breed": "English bulldog"}}
        payload = {"state": {"images": {"dog": "<dog>", "other": "<other>"}}, "questions": questions}
        original = copy.deepcopy(payload)
        images = {"<dog>": png_bytes(), "<other>": b"\xff\xd8\xffjpeg-fixture"}
        request = gemini.build_request(payload, images)
        self.assertEqual(payload, original)
        self.assertEqual(request["model"], gemini.DEFAULT_MODEL)
        self.assertEqual(json.loads(request["input"][0]["text"]), payload["state"])
        self.assertEqual(request["questions"]["present"]["criteria"],
                         {"true_description": '{"meaning": "dog visible"}', "false_description": "No dog"})
        self.assertEqual(request["questions"]["breed"]["criteria"], {"french": "", "english": '{"breed": "English bulldog"}'})
        for i, (reference, data) in enumerate(images.items()):
            self.assertIn(json.dumps(reference), request["input"][1 + i * 2]["text"])
            self.assertEqual(base64.b64decode(request["input"][2 + i * 2]["data"]), data)
        self.assertEqual([p["mime_type"] for p in request["input"] if p["type"] == "image"], ["image/png", "image/jpeg"])
        for state in (False, 0, [1, 2], "literal"):
            content = gemini.build_request({**payload, "state": state}, {})["input"]
            self.assertEqual(content, [{"type": "text", "text": state if isinstance(state, str) else json.dumps(state)}])

    def test_provider_limits_before_network(self):
        payload = {"state": "dog", "questions": QUESTIONS}
        gemini.validate_request({**payload, "questions": {str(i): QUESTIONS["present"] for i in range(1000)}})
        gemini.validate_request({**payload, "questions": {"levels": {"type": "score", "criteria": [str(i) for i in range(255)]}}})
        gemini.validate_request({**payload, "questions": {"options": {"type": "choice", "criteria": {"é" * 128: "", "two words": None}}}})
        bad = [
            {str(i): QUESTIONS["present"] for i in range(1001)},
            *({name: QUESTIONS["present"]} for name in ("has.dot", "has space", "x" * 65, "newline\n", "é")),
            {"box": {"type": "box"}},
            {"choice": {"type": "choice", "criteria": {"only": None}}},
            {"choice": {"type": "choice", "criteria": {str(i): None for i in range(256)}}},
            *({"choice": {"type": "choice", "criteria": {key: "", "valid": ""}}}
              for key in ("a" * 129, " edge", "edge ", "a\tb", "a\x00b", "a\u00a0b")),
            *({"score": {"type": "score", "criteria": levels}} for levels in (["one"], ["x"] * 256, ["", "two"], [" ", "two"])),
        ]
        for index, questions in enumerate(bad):
            with self.subTest(case=index), self.assertRaises(ValueError):
                gemini.validate_request({**payload, "questions": questions})

    def test_normalization_preserves_common_answers_model_and_zero_unknown_usage(self):
        raw = copy.deepcopy(RESPONSE)
        result = gemini.normalize_response(raw, QUESTIONS, "custom-model")
        self.assertEqual(result["model"], "custom-model")
        self.assertEqual(result["answers"]["present"], {"type": "noul", "noul": 0.9})
        self.assertEqual(result["answers"]["breed"], RESPONSE["answers"]["breed"])
        self.assertEqual(result["answers"]["visible"], RESPONSE["answers"]["visible"])
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 0})
        for usage in ({}, None):
            raw["usage"] = usage
            self.assertEqual(gemini.normalize_response(raw, QUESTIONS)["usage"], {"input_tokens": None, "output_tokens": None})
        del raw["usage"]
        self.assertEqual(gemini.normalize_response(raw, QUESTIONS)["usage"], {"input_tokens": None, "output_tokens": None})

    def test_malformed_response_fails_entire_batch(self):
        changes = (
            lambda r: r.update(error={"message": "private context"}),
            lambda r: r["answers"].pop("present"),
            lambda r: r["answers"].update(extra=r["answers"]["present"]),
            lambda r: r["answers"]["present"].update(type="refusal"),
            lambda r: r["answers"]["present"].update(noul=float("nan")),
            lambda r: r["answers"]["present"].update(noul="0.9"),
            lambda r: r["answers"]["present"].update(noul=True),
            lambda r: r["answers"]["present"].update(noul=2),
            lambda r: r["answers"]["present"].update(confidence=-1),
            lambda r: r["answers"]["breed"].update(choice="unknown"),
            lambda r: r["answers"]["breed"]["probabilities"].pop("english"),
            lambda r: r["answers"]["breed"]["probabilities"].update(english=0.9),
            lambda r: r["answers"]["visible"].update(score=2),
            lambda r: r["answers"]["visible"]["legend"].update({"1": "Wrong level"}),
            lambda r: r["answers"]["visible"]["probabilities"].update({"1": -0.1}),
            lambda r: r["usage"].update(total_input_tokens=-1),
            lambda r: r["usage"].update(total_output_tokens=True),
            lambda r: r.update(usage=[]),
        )
        for i, change in enumerate(changes):
            raw = copy.deepcopy(RESPONSE)
            change(raw)
            with self.subTest(case=i), self.assertRaises(ProviderError):
                gemini.normalize_response(raw, QUESTIONS)
        with self.assertRaises(ValueError):
            json.loads('{"answers":{},"answers":{}}', object_pairs_hook=gemini.unique_object)


if __name__ == "__main__":
    unittest.main()
