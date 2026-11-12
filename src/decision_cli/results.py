"""Request-aware checks shared by normalized provider responses."""

import math

from decision_cli.errors import ProviderError

PROBABILITY_TOLERANCE = 0.02


def number(value, maximum=1):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("Invalid numeric answer")


def validate_result(result, questions):
    try:
        if not isinstance(result["model"], str) or not result["model"].strip():
            raise ValueError("Missing model")
        answers = result["answers"]
        if set(answers) != set(questions):
            raise ValueError("Mismatched answer IDs")
        for name, question in questions.items():
            answer = answers[name]
            kind = question["type"]
            if answer["type"] != kind:
                raise ValueError("Mismatched answer type")
            if kind == "noul":
                number(answer["noul"])
            elif kind in {"choice", "score"}:
                number(answer["confidence"])
                expected = set(question["criteria"]) if kind == "choice" else {str(i) for i in range(len(question["criteria"]))}
                probabilities = answer["probabilities"]
                if not isinstance(probabilities, dict) or set(probabilities) != expected:
                    raise ValueError("Mismatched probability keys")
                for value in probabilities.values():
                    number(value)
                # Up to 255 independently rounded probabilities can accumulate error.
                if not math.isclose(math.fsum(probabilities.values()), 1, rel_tol=0, abs_tol=PROBABILITY_TOLERANCE):
                    raise ValueError("Invalid probability total")
                if kind == "choice":
                    if answer["choice"] not in expected:
                        raise ValueError("Unoffered choice")
                else:
                    number(answer["score"], len(expected) - 1)
                    if set(answer["legend"]) != expected:
                        raise ValueError("Mismatched score legend")
            elif kind == "box":
                box = answer["box"]
                for edge in ("left", "top", "right", "bottom"):
                    number(box[edge])
                if box["left"] > box["right"] or box["top"] > box["bottom"]:
                    raise ValueError("Reversed box edges")
                if answer.get("confidence") is not None:
                    number(answer["confidence"])
        for key in ("input_tokens", "output_tokens"):
            value = result.get("usage", {}).get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Invalid token count")
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise ProviderError("Provider returned invalid decision data; no answers returned") from None
    return result
