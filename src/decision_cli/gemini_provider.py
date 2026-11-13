"""Adapt common judgments to the Gemini Developer Decisions REST API."""

import base64
import json
import re
import unicodedata

from decision_cli.errors import ProviderError
from decision_cli.images import image_format
from decision_cli.results import number, validate_result

DEFAULT_MODEL = "gemini-decision-1"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
QUESTION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def description(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)


def validate_request(payload):
    questions = payload["questions"]
    if len(questions) > 1000:
        raise ValueError("Gemini Decisions supports at most 1000 questions per request")
    for name, question in questions.items():
        if not re.fullmatch(QUESTION_ID_PATTERN, name):
            raise ValueError("Gemini question IDs must be 1 to 64 ASCII letters, digits, underscores, or hyphens")
        kind = question["type"]
        if kind == "box":
            raise ValueError("Gemini Decisions does not support Box; use the typesafe provider for Box requests")
        if kind == "choice":
            if not 2 <= len(question["criteria"]) <= 255:
                raise ValueError("Gemini Decisions Choice requires 2 to 255 options")
            for key in question["criteria"]:
                if (not 1 <= len(key) <= 128 or len(key.encode("utf-8")) > 512 or key != key.strip()
                        or any(unicodedata.category(char) == "Cc" or (char.isspace() and char != " ") for char in key)):
                    raise ValueError("Gemini option IDs require 1 to 128 characters without edge spaces, control characters, or non-ASCII spaces")
        if kind == "score":
            if not 2 <= len(question["criteria"]) <= 255:
                raise ValueError("Gemini Decisions Score requires 2 to 255 levels")
            if any(not description(value).strip() for value in question["criteria"]):
                raise ValueError("Gemini score levels cannot be empty")


def build_request(payload, images):
    """Serialize JSON state as text and label each shared inline image once."""
    content = [{"type": "text", "text": description(payload["state"])}]
    for reference, data in images.items():
        content.extend([
            {"type": "text", "text": "Image reference (JSON string): " + json.dumps(reference)},
            {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mime_type": "image/" + image_format(data)},
        ])
    questions = {}
    for name, question in payload["questions"].items():
        converted = {"type": question["type"], "instructions": question["instructions"]}
        criteria = question.get("criteria")
        if question["type"] == "noul" and criteria is not None:
            converted["criteria"] = {key + "_description": description(value) if value is not None else ""
                                     for key, value in criteria.items()}
        elif question["type"] == "choice":
            converted["criteria"] = {key: description(value) if value is not None else "" for key, value in criteria.items()}
        elif question["type"] == "score":
            converted["criteria"] = [description(value) for value in criteria]
        questions[name] = converted
    return {"model": payload.get("model", DEFAULT_MODEL), "input": content, "questions": questions}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property")
        result[key] = value
    return result


def normalize_response(raw, questions, model=DEFAULT_MODEL):
    try:
        if "error" in raw:
            raise ValueError("Error response")
        answers = raw["answers"]
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ValueError("Missing or unexpected answers")
        normalized = {}
        for name, question in questions.items():
            answer = answers[name]
            kind = question["type"]
            if answer["type"] != kind:
                raise ValueError("Wrong answer type")
            number(answer["confidence"])
            if kind == "noul":
                normalized[name] = {"type": kind, "noul": answer["noul"]}
            elif kind == "choice":
                normalized[name] = {key: answer[key] for key in ("type", "choice", "confidence", "probabilities")}
            elif kind == "score":
                normalized[name] = {key: answer[key] for key in ("type", "score", "confidence", "probabilities", "legend")}
                if answer["legend"] != {str(i): description(value) for i, value in enumerate(question["criteria"])}:
                    raise ValueError("Mismatched score legend")
        usage = raw.get("usage")
        if usage is None:
            usage = {}
        result = {"model": model, "answers": normalized,
                  "usage": {"input_tokens": usage.get("total_input_tokens"), "output_tokens": usage.get("total_output_tokens")}}
        validate_result(result, questions)
        from typesafe_sdk import SystemOneResponse

        return SystemOneResponse.model_validate_json(json.dumps(result, allow_nan=False)).model_dump(mode="json")
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProviderError("Gemini returned invalid decision data; no answers returned") from None


def evaluate(payload, images, args, key):
    import httpx2

    body = json.dumps(build_request(payload, images), ensure_ascii=False, allow_nan=False).encode("utf-8")
    with httpx2.Client(timeout=args.timeout, follow_redirects=False) as client:
        response = client.post(args.base_url.rstrip("/") + "/decisions", content=body,
                               headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    if not 200 <= response.status_code < 300:
        raise ProviderError(f"Gemini request failed (HTTP {response.status_code}); no answers returned")
    try:
        raw = json.loads(response.content, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError):
        raise ProviderError("Gemini returned invalid JSON; no answers returned") from None
    return normalize_response(raw, payload["questions"], payload["model"])
