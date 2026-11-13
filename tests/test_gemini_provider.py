"""Gemini native mapping, limits, and whole-batch result validation."""

import base64
import copy
import json
import unittest

from native import evaluate, invoke
from provider_fixtures import GEMINI_RESPONSE as RESPONSE, QUESTIONS, png_bytes


class GeminiProviderTests(unittest.TestCase):
    def test_model_and_environment_defaults(self):
        for env, flags, model in (({}, [], "gemini-decision-1"), ({"DECISION_MODEL": "custom"}, [], "custom"),
                                  ({"DECISION_MODEL": "custom"}, ["--model", "flag"], "flag")):
            result = invoke("noul", "Dog?", "--state", "dog", "--provider", "gemini", "--dry-run", "--json", *flags, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["model"], model)

    def test_context_criteria_and_shared_images_survive_translation(self):
        questions = copy.deepcopy(QUESTIONS)
        questions["present"]["criteria"] = {"true": {"meaning": "dog visible"}, "false": "No dog"}
        questions["breed"]["criteria"] = {"french": None, "english": {"breed": "English bulldog"}}
        images = {"<dog>": png_bytes(), "<other>": b"\xff\xd8\xffjpeg-fixture"}
        payload = {"state": {"images": {"dog": "<dog>", "other": "<other>"}}, "questions": questions,
                   "assets": {name: {"data_base64": base64.b64encode(data).decode()} for name, data in images.items()}}
        raw = copy.deepcopy(RESPONSE)
        result, requests = evaluate("gemini", payload, raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        body = requests[0]["body"]
        self.assertEqual(json.loads(body["input"][0]["text"]), payload["state"])
        self.assertEqual(body["questions"]["present"]["criteria"], {"true_description": '{"meaning": "dog visible"}', "false_description": "No dog"})
        self.assertEqual(body["questions"]["breed"]["criteria"], {"french": "", "english": '{"breed": "English bulldog"}'})
        for index, (reference, data) in enumerate(images.items()):
            self.assertIn(json.dumps(reference), body["input"][1 + index * 2]["text"])
            self.assertEqual(base64.b64decode(body["input"][2 + index * 2]["data"]), data)
        self.assertEqual([p["mime_type"] for p in body["input"] if p["type"] == "image"], ["image/png", "image/jpeg"])
        for state in (False, 0, [1, 2], "literal"):
            result, requests = evaluate("gemini", {"state": state, "questions": QUESTIONS}, RESPONSE)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(requests[0]["body"]["input"], [{"type": "text", "text": state if isinstance(state, str) else json.dumps(state)}])

    def test_provider_limits_and_unicode_option_ids(self):
        bad = [{str(i): QUESTIONS["present"] for i in range(1001)},
               *({name: QUESTIONS["present"]} for name in ("has.dot", "has space", "x" * 65, "newline\n", "é")),
               {"box": {"type": "box"}}, {"choice": {"type": "choice", "criteria": {"only": None}}},
               {"choice": {"type": "choice", "criteria": {str(i): None for i in range(256)}}},
               *({"choice": {"type": "choice", "criteria": {key: "", "valid": ""}}} for key in ("a" * 129, " edge", "edge ", "a\tb", "a\x00b", "a\u00a0b")),
               *({"score": {"type": "score", "criteria": levels}} for levels in (["one"], ["x"] * 256, ["", "two"], [" ", "two"]))]
        for index, questions in enumerate(bad):
            with self.subTest(case=index):
                questions = {name: {"instructions": "Question?", **q} for name, q in questions.items()}
                result = invoke("run", "-", "--provider", "gemini", "--dry-run", "--json", data=json.dumps({"state": "dog", "questions": questions}))
                self.assertEqual((result.returncode, result.stdout), (2, ""))
        for questions in ({str(i): QUESTIONS["present"] for i in range(1000)},
                          {"levels": {"type": "score", "instructions": "Rate", "criteria": [str(i) for i in range(255)]}},
                          {"pick": {"type": "choice", "instructions": "Pick", "criteria": {"é" * 128: "", "two words": None}}}):
            result = invoke("run", "-", "--provider", "gemini", "--dry-run", "--json", data=json.dumps({"state": "dog", "questions": questions}))
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_normalized_answers_preserve_zero_and_unknown_counts(self):
        for usage, expected in ((RESPONSE["usage"], {"input_tokens": 10, "output_tokens": 0}), ({}, {"input_tokens": None, "output_tokens": None}),
                                (None, {"input_tokens": None, "output_tokens": None})):
            raw = copy.deepcopy(RESPONSE)
            raw["usage"] = usage
            result, _ = evaluate("gemini", {"state": "dog", "questions": QUESTIONS}, raw)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["usage"], expected)
            self.assertEqual(value["answers"]["present"], {"type": "noul", "noul": 0.9})
            self.assertEqual(value["answers"]["visible"], RESPONSE["answers"]["visible"])

    def test_invalid_results_and_duplicate_properties_fail_the_batch(self):
        changes = (lambda r: r.update(error={"message": "private context"}), lambda r: r["answers"].pop("present"),
                   lambda r: r["answers"].update(extra=r["answers"]["present"]), lambda r: r["answers"]["present"].update(type="refusal"),
                   *(lambda r, v=v: r["answers"]["present"].update(noul=v) for v in (float("nan"), "0.9", True, 2)),
                   lambda r: r["answers"]["present"].update(confidence=-1), lambda r: r["answers"]["breed"].update(choice="unknown"),
                   lambda r: r["answers"]["breed"]["probabilities"].pop("english"), lambda r: r["answers"]["breed"]["probabilities"].update(english=0.9),
                   lambda r: r["answers"]["visible"].update(score=2), lambda r: r["answers"]["visible"]["legend"].update({"1": "Wrong"}),
                   lambda r: r["answers"]["visible"]["probabilities"].update({"1": -0.1}), lambda r: r["usage"].update(total_input_tokens=-1),
                   lambda r: r["usage"].update(total_output_tokens=True), lambda r: r.update(usage=[]))
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                raw = copy.deepcopy(RESPONSE)
                change(raw)
                result, _ = evaluate("gemini", {"state": "dog", "questions": QUESTIONS}, raw)
                self.assertEqual((result.returncode, result.stdout), (1, ""), result.stderr)
        raw = json.dumps(RESPONSE).replace('"noul": 0.9', '"noul": 0.9, "noul": 0.9').encode()
        result, _ = evaluate("gemini", {"state": "dog", "questions": QUESTIONS}, raw)
        self.assertEqual((result.returncode, result.stdout), (1, ""))


if __name__ == "__main__":
    unittest.main()
