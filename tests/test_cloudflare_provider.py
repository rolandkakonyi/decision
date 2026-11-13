"""Clef routing, media limits, and strict Workers AI response envelopes."""

import base64
import copy
import json
import os
import unittest
from unittest.mock import patch

from decision_cli import cli
from decision_cli import cloudflare_provider as cf
from decision_cli.errors import ProviderError
from test_images import png_bytes
from test_openai_provider import QUESTIONS

RESPONSE = {
    "success": True, "errors": [], "messages": [],
    "result": {
        "model": "clef", "usage": {"input_tokens": 10, "output_tokens": 0},
        "answers": {
            "present": {"type": "noul", "noul": 0.9},
            "breed": {"type": "choice", "choice": "french", "confidence": 0.9,
                      "probabilities": {"french": 0.9, "english": 0.1}},
            "visible": {"type": "score", "score": 1, "confidence": 0.9,
                        "legend": {"0": "Hidden", "1": "Visible"}, "probabilities": {"0": 0.1, "1": 0.9}},
        },
    },
}


class CloudflareProviderTests(unittest.TestCase):
    def args(self, *flags):
        return cli.parser().parse_args(["noul", "Dog?", "--state", "dog", "--provider", "cloudflare", *flags])

    def test_official_credentials_do_not_cross_hosts_or_providers(self):
        with patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "cf-fixture", "CLOUDFLARE_AUTH_TOKEN": "legacy-fixture",
                                     "OPENAI_API_KEY": "openai-fixture", "TYPESAFE_API_KEY": "typesafe-fixture"}, clear=True):
            args = self.args()
            self.assertEqual(cli.connection(args), "cf-fixture")
            self.assertEqual(args.base_url, cf.API_ROOT)
            self.assertEqual(cli.make_request(args)["model"], "clef")
            del os.environ["CLOUDFLARE_API_TOKEN"]
            self.assertEqual(cli.connection(args), "legacy-fixture")
            for url in ("https://custom.example/run", "https://api.cloudflare.com.evil.example/run",
                        "https://api.cloudflare.com:444/run", "https://api.openai.com/v1"):
                args.base_url = url
                self.assertIsNone(cli.connection(args))
            args = self.args("--api-key-env", "UNSET_SELECTOR")
            self.assertIsNone(cli.connection(args))
            for provider in ("typesafe", "openai"):
                args = self.args("--provider", provider, "--base-url", cf.API_ROOT)
                self.assertIsNone(cli.connection(args))

    def test_environment_flag_and_custom_run_root_precedence(self):
        settings = {"DECISION_PROVIDER": "cloudflare", "CLOUDFLARE_BASE_URL": "https://gateway.example/workers-ai",
                    "DECISION_API_KEY_ENV": "CUSTOM_KEY", "CUSTOM_KEY": "custom-fixture",
                    "CLOUDFLARE_ACCOUNT_ID": "a" * 32, "DECISION_ACCOUNT_ID": "b" * 32}
        with patch.dict(os.environ, settings, clear=True):
            args = cli.parser().parse_args(["mcp"])
            self.assertEqual(cli.connection(args), "custom-fixture")
            self.assertEqual(args.account_id, "b" * 32)
            self.assertEqual(cf.endpoint(args, "@cf/cloudflare/clef-flash"), "https://gateway.example/workers-ai/@cf/cloudflare/clef-flash")
            os.environ["DECISION_BASE_URL"] = cf.API_ROOT
            args = self.args("--account-id", "c" * 32)
            cli.connection(args)
            self.assertEqual(cf.endpoint(args, "clef"), cf.API_ROOT + "/accounts/" + "c" * 32 + "/ai/run/@cf/cloudflare/clef")
            args = self.args("--base-url", "https://flag.example/run/", "--api-key-env", "CLOUDFLARE_ACCOUNT_ID")
            self.assertEqual(cli.connection(args), "a" * 32)
            self.assertEqual(cf.endpoint(args, "clef"), "https://flag.example/run/@cf/cloudflare/clef")

    def test_account_and_model_validation_without_network(self):
        with patch.dict(os.environ, {}, clear=True):
            args = self.args()
            cli.connection(args)
            self.assertEqual(cli.make_request(args)["model"], "clef")  # previews need no account/key
            with self.assertRaisesRegex(ValueError, "CLOUDFLARE_ACCOUNT_ID"):
                cf.endpoint(args, "clef")
            args.account_id = "not/an/account"
            with self.assertRaisesRegex(ValueError, "32 hexadecimal"):
                cf.endpoint(args, "clef")
            args.account_id = None
            args.base_url = "http://127.0.0.1:1234/run"
            self.assertEqual(cf.endpoint(args, "clef-flash"), args.base_url + "/@cf/cloudflare/clef-flash")
            for model in ("jev-1.14.0", "clef-omni", "../../other"):
                with self.subTest(model=model), self.assertRaises(ValueError):
                    args = self.args("--model", model)
                    cli.prepare_batch(cli.make_request(args), args, {})

    def test_question_limits_and_ids_before_network(self):
        payload = {"state": "dog", "questions": QUESTIONS}
        cf.validate_request(payload)
        cf.validate_request({**payload, "questions": {str(i): QUESTIONS["present"] for i in range(64)}})
        bad_questions = [
            {str(i): QUESTIONS["present"] for i in range(65)},
            {"has space": QUESTIONS["present"]}, {"x" * 101: QUESTIONS["present"]},
            {"newline\n": QUESTIONS["present"]}, {"location": {"type": "box"}},
            {"breed": {**QUESTIONS["breed"], "criteria": {"only": None}}},
            {"breed": {**QUESTIONS["breed"], "criteria": {str(i): None for i in range(256)}}},
            {"visible": {**QUESTIONS["visible"], "criteria": ["one"]}},
            {"visible": {**QUESTIONS["visible"], "criteria": list(range(11))}},
        ]
        for questions in bad_questions:
            with self.subTest(question=next(iter(questions))), self.assertRaises(ValueError):
                cf.validate_request({**payload, "questions": questions})

    def test_named_images_once_preserve_context_without_mutation(self):
        state = {"images": {"dog": "<dog>", "other": "<other>"}, "message": "Compare"}
        original = copy.deepcopy(state)
        images = {"<dog>": png_bytes(), "<other>": b"\xff\xd8\xffjpeg-fixture"}
        body = json.loads(cf.prepare_body({"state": state, "questions": QUESTIONS, "model": "@cf/cloudflare/clef-flash"}, images))
        self.assertEqual(body["model"], "clef-flash")
        self.assertEqual(body["questions"], QUESTIONS)
        self.assertEqual(state, original)
        self.assertEqual(body["state"][cf.IMAGE_REFERENCES], [
            {"reference": "<dog>", "image_position_1_based": 1}, {"reference": "<other>", "image_position_1_based": 2}])
        for encoded, expected in zip(body["images"], images.values()):
            self.assertEqual(base64.b64decode(encoded.split(",", 1)[1]), expected)
        self.assertTrue(body["images"][1].startswith("data:image/jpeg;base64,"))
        for state in (False, 0, "text", [1, 2]):
            self.assertEqual(cf.build_request({"state": state, "questions": QUESTIONS}, {})["state"], state)
            self.assertEqual(cf.build_request({"state": state, "questions": QUESTIONS}, images)["state"]["context"], state)
        with self.assertRaisesRegex(ValueError, "reserve state"):
            cf.build_request({"state": {cf.IMAGE_REFERENCES: "user context"}, "questions": QUESTIONS}, images)

    def test_media_and_encoded_request_limits(self):
        payload = {"state": "dog", "questions": QUESTIONS}
        for images, message in (({str(i): png_bytes() for i in range(5)}, "4 images"),
                                ({"big": b"x" * (cf.MAX_IMAGE_BYTES + 1)}, "4 MiB"),
                                ({str(i): b"x" * (3 * 1024 * 1024) for i in range(3)}, "8 MiB")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                cf.prepare_body(payload, images)
        large = png_bytes() + b"x" * (cf.MAX_IMAGE_BYTES - len(png_bytes()))
        cf.prepare_body(payload, {"one": large, "two": large})  # exact per-image and combined limits
        with self.assertRaisesRegex(ValueError, "13 MiB"):
            cf.prepare_body({**payload, "state": "x" * cf.MAX_REQUEST_BYTES}, {})

    def test_normalized_stats_preserve_zero_and_unknown(self):
        response = copy.deepcopy(RESPONSE)
        result = cf.normalize_response(response, QUESTIONS)
        self.assertEqual(result, RESPONSE["result"])
        response["result"]["usage"] = {}
        self.assertEqual(cf.normalize_response(response, QUESTIONS)["usage"], {"input_tokens": None, "output_tokens": None})
        del response["result"]["usage"]
        self.assertEqual(cf.normalize_response(response, QUESTIONS)["usage"], {"input_tokens": None, "output_tokens": None})

    def test_malformed_responses_fail_whole_batch(self):
        changes = (
            lambda r: r.update(success=False), lambda r: r.update(success=1),
            lambda r: r.update(errors=[{"message": "private context"}]),
            lambda r: r["result"].update(model=""), lambda r: r["result"]["answers"].pop("present"),
            lambda r: r["result"]["answers"]["present"].update(type="box"),
            lambda r: r["result"]["answers"]["present"].update(noul=float("nan")),
            lambda r: r["result"]["answers"]["present"].update(noul=2),
            lambda r: r["result"]["answers"]["breed"].update(choice="unknown"),
            lambda r: r["result"]["answers"]["breed"]["probabilities"].pop("english"),
            lambda r: r["result"]["answers"]["visible"].update(score=-1),
            lambda r: r["result"]["answers"]["visible"]["legend"].pop("1"),
            lambda r: r["result"]["usage"].update(input_tokens=-1),
        )
        for index, change in enumerate(changes):
            raw = copy.deepcopy(RESPONSE)
            change(raw)
            with self.subTest(case=index), self.assertRaises(ProviderError):
                cf.normalize_response(raw, QUESTIONS)
        with self.assertRaises(ValueError):
            json.loads('{"success":true,"success":false}', object_pairs_hook=cf.unique_object)


if __name__ == "__main__":
    unittest.main()
