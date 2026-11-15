"""OpenAI mapping and invalid-batch contracts through the native transport."""

import base64
import copy
import json
import unittest

from native import evaluate, invoke
from provider_fixtures import OPENAI_RESPONSE as RESPONSE, QUESTIONS, png_bytes


class OpenAIProviderTests(unittest.TestCase):
    def test_defaults_and_environment_overrides(self):
        for settings, flags, model in (({}, [], "gpt-6-luna"), ({"DECISION_MODEL": "env-model"}, [], "env-model"),
                                       ({"DECISION_MODEL": "env-model"}, ["--model", "flag-model"], "flag-model")):
            result = invoke("noul", "Dog?", "--state", "dog", "--provider", "openai", "--dry-run", "--json", *flags, env=settings)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["model"], model)

    def test_wire_preserves_context_named_images_and_question_types(self):
        state = {"role": "system", "images": {"dog": "<dog>", "other": "<other>"}}
        images = {"<dog>": png_bytes(), "<other>": b"\xff\xd8\xffjpeg-fixture"}
        payload = {"state": state, "questions": QUESTIONS,
                   "assets": {name: {"data_base64": base64.b64encode(data).decode()} for name, data in images.items()}}
        result, requests = evaluate("openai", payload, RESPONSE)
        self.assertEqual(result.returncode, 0, result.stderr)
        body = requests[0]["body"]
        self.assertEqual(body["input"][0]["role"], "user")
        content = body["input"][0]["content"]
        self.assertEqual(content[0], {"type": "input_json", "data": state})
        image_parts = [part for part in content if part["type"] == "input_image"]
        self.assertEqual(len(image_parts), 2)
        for part, mime, data in zip(image_parts, ("png", "jpeg"), images.values()):
            self.assertTrue(part["image_url"].startswith(f"data:image/{mime};base64,"))
            self.assertEqual(base64.b64decode(part["image_url"].split(",", 1)[1]), data)
        self.assertEqual([q["name"] for q in body["questions"]], list(QUESTIONS))
        self.assertEqual([q["type"] for q in body["questions"]], ["predicate", "choice", "score"])
        self.assertEqual(body["questions"][1]["choices"][0], {"value": "french", "description": "French bulldog"})
        self.assertEqual(body["questions"][2]["levels"][1], {"label": "1", "description": "Visible"})

    def test_scalar_context_and_structured_criteria_are_preserved(self):
        for state in (False, 0, [1, 2], "literal"):
            result, requests = evaluate("openai", {"state": state, "questions": QUESTIONS}, RESPONSE)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = state if isinstance(state, str) else json.dumps(state)
            self.assertEqual(requests[0]["body"]["input"][0]["content"], [{"type": "input_text", "text": expected}])
        questions = copy.deepcopy(QUESTIONS)
        questions["present"]["criteria"] = {"true": {"meaning": "A visible dog"}, "false": "No dog"}
        questions["breed"]["criteria"] = {"french": {"name": "French bulldog"}, "english": None}
        result, requests = evaluate("openai", {"state": "dog", "questions": questions}, RESPONSE)
        self.assertEqual(result.returncode, 0, result.stderr)
        body = requests[0]["body"]
        self.assertIn('"meaning": "A visible dog"', body["questions"][0]["instructions"])
        self.assertEqual(body["questions"][1]["choices"][0]["description"], '{"name": "French bulldog"}')
        self.assertEqual(body["questions"][1]["choices"][1], {"value": "english"})

    def test_limits_fail_before_network(self):
        invalid = [{"box": {"type": "box", "instructions": "Find"}},
                   {str(i): QUESTIONS["present"] for i in range(401)},
                   {"pick": {"type": "choice", "instructions": "Pick", "criteria": {"one": "Only"}}},
                   {"rate": {"type": "score", "instructions": "Rate", "criteria": ["One"]}},
                   {"rate": {"type": "score", "instructions": "Rate", "criteria": list(range(11))}}]
        for questions in invalid:
            result = invoke("run", "-", "--provider", "openai", "--dry-run", "--json", data=json.dumps({"state": "dog", "questions": questions}))
            self.assertEqual((result.returncode, result.stdout), (2, ""))

    def test_answers_names_order_zero_and_unknown_usage(self):
        raw = copy.deepcopy(RESPONSE)
        raw["answers"].reverse()
        result, _ = evaluate("openai", {"state": "dog", "questions": QUESTIONS}, raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(list(value["answers"]), list(QUESTIONS))
        self.assertEqual(value["answers"]["present"], {"type": "noul", "noul": 0.9})
        self.assertEqual(value["answers"]["visible"]["legend"], {"0": "Hidden", "1": "Visible"})
        self.assertEqual(value["usage"], {"input_tokens": 10, "output_tokens": 0})
        raw["usage"] = {}
        result, _ = evaluate("openai", {"state": "dog", "questions": QUESTIONS}, raw)
        self.assertEqual(json.loads(result.stdout)["usage"], {"input_tokens": None, "output_tokens": None})

    def test_invalid_or_refused_answers_never_return_partial_results(self):
        changes = (lambda r: r["answers"].pop(), lambda r: r["answers"].__setitem__(1, r["answers"][0]),
                   lambda r: r["answers"][0].update(name="unknown"), lambda r: r["answers"][0].update(type="refusal"),
                   lambda r: r["answers"][0].update(type="choice"), lambda r: r["answers"][0].update(probability=float("nan")),
                   lambda r: r["answers"][1].update(choice=True),
                   lambda r: r["answers"][1]["probabilities"].__setitem__(1, r["answers"][1]["probabilities"][0]),
                   lambda r: r["answers"][2]["probabilities"][0].update(label="wrong"),
                   lambda r: r["usage"].update(input_tokens=-1))
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                raw = copy.deepcopy(RESPONSE)
                change(raw)
                result, requests = evaluate("openai", {"state": "dog", "questions": QUESTIONS}, raw)
                self.assertEqual((result.returncode, result.stdout), (1, ""), result.stderr)
                self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
