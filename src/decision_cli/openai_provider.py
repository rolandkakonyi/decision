"""Adapt the common question format to OpenAI's native Decisions API."""

import base64
import json
import math

from decision_cli.errors import ProviderError
from decision_cli.images import image_format

DEFAULT_MODEL = "gpt-6-luna"


def validate_request(payload):
    questions = payload["questions"]
    if len(questions) > 400:
        raise ValueError("OpenAI Decisions supports at most 400 questions per request")
    for question in questions.values():
        kind = question["type"]
        if kind == "box":
            raise ValueError("OpenAI Decisions does not support Box; use the typesafe provider for Box requests")
        if kind == "choice" and not 2 <= len(question["criteria"]) <= 255:
            raise ValueError("OpenAI Decisions Choice requires 2 to 255 options")
        if kind == "score" and not 2 <= len(question["criteria"]) <= 10:
            raise ValueError("OpenAI Decisions Score requires 2 to 10 levels")


def description(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)


def build_request(payload, images):
    """Keep JSON evidence as data and attach each named image once."""
    state = payload["state"]
    if isinstance(state, dict) or (isinstance(state, list) and all(isinstance(item, dict) for item in state)):
        content = [{"type": "input_json", "data": state}]
    else:
        content = [{"type": "input_text", "text": description(state)}]
    for reference, data in images.items():
        content.extend([
            {"type": "input_text", "text": "Image reference (JSON string): " + json.dumps(reference)},
            {"type": "input_image", "image_url": f"data:image/{image_format(data)};base64," + base64.b64encode(data).decode("ascii")},
        ])
    questions = []
    for name, question in payload["questions"].items():
        kind = question["type"]
        instructions = question["instructions"]
        if kind == "noul" and question.get("criteria") is not None:
            instructions += "\nInterpret true and false using these criteria (JSON): " + description(question["criteria"])
        converted = {"name": name, "type": "predicate" if kind == "noul" else kind, "instructions": instructions}
        if kind == "choice":
            converted["choices"] = [
                {"value": key, **({"description": description(value)} if value is not None else {})}
                for key, value in question["criteria"].items()
            ]
        elif kind == "score":
            converted["levels"] = [{"label": str(index), "description": description(value)}
                                   for index, value in enumerate(question["criteria"])]
        questions.append(converted)
    return {"model": payload.get("model", DEFAULT_MODEL), "input": [{"role": "user", "content": content}],
            "questions": questions}


def number(value, minimum=0, maximum=1):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("Invalid numeric result")
    return float(value)


def probabilities(rows, expected, *, score=False):
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("Incomplete probabilities")
    result = {}
    for row in rows:
        key = row["value"]
        if (score and (type(key) is not int or row["label"] != str(key))) or (not score and not isinstance(key, str)):
            raise ValueError("Invalid probability key")
        if key not in expected or key in result:
            raise ValueError("Unexpected or duplicate probability key")
        result[key] = number(row["probability"])
    return result


def normalize_response(raw, questions):
    """Refuse partial, duplicate, mismatched, refused, or malformed batches."""
    try:
        rows = raw["answers"]
        if not isinstance(rows, list) or len(rows) != len(questions):
            raise ValueError("Incomplete answers")
        answers = {}
        for row in rows:
            name = row["name"]
            if not isinstance(name, str) or name not in questions or name in answers:
                raise ValueError("Unexpected or duplicate answer ID")
            if row["type"] == "refusal":
                raise ProviderError("OpenAI refused a question; no answers returned")
            question = questions[name]
            kind = question["type"]
            if row["type"] != ("predicate" if kind == "noul" else kind):
                raise ValueError("Unexpected answer type")
            answer = {"type": kind}
            if kind == "noul":
                answer["noul"] = number(row["probability"])
            elif kind == "choice":
                choice = row["choice"]
                if not isinstance(choice, str) or choice not in question["criteria"]:
                    raise ValueError("Unexpected choice")
                answer.update(choice=choice, confidence=number(row["confidence"]),
                              probabilities=probabilities(row["probabilities"], question["criteria"]))
            elif kind == "score":
                legend = {index: description(value) for index, value in enumerate(question["criteria"])}
                answer.update(score=number(row["score"], maximum=len(legend) - 1),
                              confidence=number(row["confidence"]), legend=legend,
                              probabilities=probabilities(row["probabilities"], legend, score=True))
            answers[name] = answer
        usage = raw.get("usage") or {}
        counts = {key: usage.get(key) for key in ("input_tokens", "output_tokens")}
        if any(value is not None and (type(value) is not int or value < 0) for value in counts.values()):
            raise ValueError("Invalid token counts")
        if not isinstance(raw["model"], str) or not raw["model"].strip():
            raise ValueError("Missing model")
        from typesafe_sdk import SystemOneResponse

        result = {"model": raw["model"], "usage": counts, "answers": {name: answers[name] for name in questions}}
        # JSON validation also normalizes integer score keys to the common wire format.
        return SystemOneResponse.model_validate_json(json.dumps(result, allow_nan=False)).model_dump(mode="json")
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProviderError("OpenAI returned invalid decision data; no answers returned") from None


def evaluate(payload, images, args, key):
    from openai import OpenAI

    request = build_request(payload, images)
    with OpenAI(api_key=key, base_url=args.base_url, timeout=args.timeout, max_retries=0) as client:
        response = client.decisions.create(**request)
    # A malformed SDK object must not emit serialization warnings containing provider data.
    return normalize_response(response.model_dump(mode="json", warnings=False), payload["questions"])
