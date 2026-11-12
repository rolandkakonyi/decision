"""All providers enforce request-aware answer and probability contracts."""

import copy
import unittest

from decision_cli.errors import ProviderError
from decision_cli.results import validate_result
from test_cloudflare_provider import RESPONSE
from test_openai_provider import QUESTIONS


class ResultValidationTests(unittest.TestCase):
    def test_valid_results_preserve_zero_and_unknown_usage(self):
        result = copy.deepcopy(RESPONSE["result"])
        result["usage"] = {"input_tokens": None, "output_tokens": 0}
        self.assertIs(validate_result(result, QUESTIONS), result)

    def test_invalid_answers_reject_the_entire_batch(self):
        changes = (
            lambda r: r["answers"]["present"].update(noul=2),
            lambda r: r["answers"]["present"].update(noul=float("nan")),
            lambda r: r["answers"]["breed"].update(choice="not-offered"),
            lambda r: r["answers"]["breed"].update(confidence=2),
            lambda r: r["answers"]["breed"].update(probabilities={"other": 1}),
            lambda r: r["answers"]["breed"].update(probabilities={"french": -1, "english": 2}),
            lambda r: r["answers"]["visible"].update(score=2),
            lambda r: r["answers"]["visible"].update(legend={"0": "Hidden"}),
            lambda r: r["answers"].pop("present"),
            lambda r: r["usage"].update(input_tokens=-1),
        )
        for index, change in enumerate(changes):
            result = copy.deepcopy(RESPONSE["result"])
            change(result)
            with self.subTest(case=index), self.assertRaises(ProviderError):
                validate_result(result, QUESTIONS)

    def test_probability_totals_for_both_provider_adapters(self):
        from decision_cli.cloudflare_provider import normalize_response as cloudflare
        from decision_cli.openai_provider import normalize_response as openai
        from test_openai_provider import RESPONSE as OPENAI_RESPONSE

        for total in (0, 0.5, 1.8):
            for provider in ("openai", "cloudflare"):
                for kind in ("choice", "score"):
                    with self.subTest(total=total, provider=provider, kind=kind), self.assertRaises(ProviderError):
                        if provider == "openai":
                            raw = copy.deepcopy(OPENAI_RESPONSE)
                            answer = next(a for a in raw["answers"] if a["type"] == kind)
                            for row in answer["probabilities"]:
                                row["probability"] = total / 2
                            openai(raw, QUESTIONS)
                        else:
                            raw = copy.deepcopy(RESPONSE)
                            answer = next(a for a in raw["result"]["answers"].values() if a["type"] == kind)
                            answer["probabilities"] = {key: total / 2 for key in answer["probabilities"]}
                            cloudflare(raw, QUESTIONS)

    def test_rounded_large_distributions_remain_valid(self):
        criteria = {str(i): str(i) for i in range(255)}
        result = {"model": "fixture", "usage": {}, "answers": {"pick": {
            "type": "choice", "choice": "0", "confidence": 0.1,
            "probabilities": {key: round(1 / 255, 4) for key in criteria},
        }}}
        validate_result(result, {"pick": {"type": "choice", "criteria": criteria}})

    def test_box_edges_are_finite_ordered_fractions(self):
        questions = {"location": {"type": "box"}}
        result = {"model": "fixture", "answers": {"location": {"type": "box", "confidence": None,
                   "box": {"left": 0, "top": 0, "right": 1, "bottom": 1}}}}
        validate_result(result, questions)
        for field, value in (("left", 2), ("right", -1), ("top", float("inf"))):
            invalid = copy.deepcopy(result)
            invalid["answers"]["location"]["box"][field] = value
            with self.subTest(field=field), self.assertRaises(ProviderError):
                validate_result(invalid, questions)
