"""Cloudflare Workers AI Clef transport and System One envelope validation."""

import base64
import json
import re

from decision_cli.errors import ProviderError
from decision_cli.images import image_format

API_ROOT = "https://api.cloudflare.com/client/v4"
DEFAULT_MODEL = "clef"
MODELS = ("clef", "clef-flash")
QUESTION_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,100}$"
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 13 * 1024 * 1024
IMAGE_REFERENCES = "_decision_image_references"


def model_name(value):
    name = value.strip().removeprefix("@cf/cloudflare/")
    if name not in MODELS:
        raise ValueError("Cloudflare supports clef or clef-flash (short names or @cf/cloudflare/ IDs)")
    return name


def validate_request(payload):
    questions = payload["questions"]
    if len(questions) > 64:
        raise ValueError("Cloudflare Clef supports at most 64 questions per request")
    for name, question in questions.items():
        if not re.fullmatch(QUESTION_ID_PATTERN, name):
            raise ValueError("Cloudflare question IDs must be 1 to 100 letters, digits, underscores, dots, or hyphens")
        kind = question["type"]
        if kind == "box":
            raise ValueError("Cloudflare Clef does not support Box; use the typesafe provider for Box requests")
        if kind == "choice" and not 2 <= len(question["criteria"]) <= 255:
            raise ValueError("Cloudflare Clef Choice requires 2 to 255 options")
        if kind == "score" and not 2 <= len(question["criteria"]) <= 10:
            raise ValueError("Cloudflare Clef Score requires 2 to 10 levels")
    if len(payload.get("assets", {})) > MAX_IMAGES:
        raise ValueError("Cloudflare Clef supports at most 4 images")


def endpoint(args, model):
    root = args.base_url.rstrip("/")
    account = args.account_id
    if account and not re.fullmatch(r"[A-Fa-f0-9]{32}", account):
        raise ValueError("Cloudflare account ID must contain 32 hexadecimal characters")
    if root == API_ROOT:
        if not account:
            raise ValueError("Set CLOUDFLARE_ACCOUNT_ID, DECISION_ACCOUNT_ID, or --account-id for Cloudflare, or supply a Workers AI run-root --base-url")
        root += f"/accounts/{account}/ai/run"
    return root + "/@cf/cloudflare/" + model_name(model)


def build_request(payload, images):
    if len(images) > MAX_IMAGES:
        raise ValueError("Cloudflare Clef supports at most 4 images")
    if any(len(data) > MAX_IMAGE_BYTES for data in images.values()):
        raise ValueError("Cloudflare Clef images must be at most 4 MiB each")
    if sum(map(len, images.values())) > MAX_TOTAL_BYTES:
        raise ValueError("Cloudflare Clef images together must be at most 8 MiB")
    result = {"model": model_name(payload.get("model", DEFAULT_MODEL)),
              "state": payload["state"], "questions": payload["questions"]}
    if images:
        state = dict(payload["state"]) if isinstance(payload["state"], dict) else {"context": payload["state"]}
        if IMAGE_REFERENCES in state:
            raise ValueError("Cloudflare image requests reserve state." + IMAGE_REFERENCES)
        state[IMAGE_REFERENCES] = [
            {"reference": name, "image_position_1_based": index}
            for index, name in enumerate(images, 1)
        ]
        result["state"] = state
        result["images"] = [f"data:image/{image_format(data)};base64," + base64.b64encode(data).decode("ascii")
                            for data in images.values()]
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property")
        result[key] = value
    return result


def prepare_body(payload, images):
    body = json.dumps(build_request(payload, images), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("Cloudflare Clef request body exceeds 13 MiB")
    return body


def number(value, maximum=1):
    if type(value) not in (int, float) or not 0 <= value <= maximum:
        raise ValueError("Invalid numeric result")
    return value


def normalize_response(raw, questions):
    try:
        if raw["success"] is not True or raw.get("errors", []) != []:
            raise ValueError("Unsuccessful envelope")
        result = raw["result"]
        if not isinstance(result["model"], str) or not result["model"].strip():
            raise ValueError("Missing model")
        answers = result["answers"]
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ValueError("Missing or unexpected answers")
        normalized = {}
        for name, question in questions.items():
            answer = answers[name]
            kind = question["type"]
            if answer["type"] != kind:
                raise ValueError("Wrong answer type")
            if kind == "noul":
                normalized[name] = {"type": kind, "noul": number(answer["noul"])}
                continue
            if kind == "choice":
                if answer["choice"] not in question["criteria"] or set(answer["probabilities"]) != set(question["criteria"]):
                    raise ValueError("Unexpected choices")
                normalized[name] = {"type": kind, "choice": answer["choice"]}
            elif kind == "score":
                indices = {str(index) for index in range(len(question["criteria"]))}
                if set(answer["legend"]) != indices or set(answer["probabilities"]) != indices:
                    raise ValueError("Unexpected score levels")
                normalized[name] = {"type": kind, "score": number(answer["score"], len(indices) - 1), "legend": answer["legend"]}
            normalized[name].update(confidence=number(answer["confidence"]),
                                    probabilities={key: number(value) for key, value in answer["probabilities"].items()})
        usage = result.get("usage")
        if usage is None:
            usage = {}
        counts = {key: usage.get(key) for key in ("input_tokens", "output_tokens")}
        if any(value is not None and (type(value) is not int or value < 0) for value in counts.values()):
            raise ValueError("Invalid token counts")
        from typesafe_sdk import SystemOneResponse

        result = {"model": result["model"], "answers": normalized, "usage": counts}
        return SystemOneResponse.model_validate_json(json.dumps(result, allow_nan=False)).model_dump(mode="json")
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProviderError("Cloudflare returned an unsuccessful or invalid decision response; no answers returned") from None


def evaluate(payload, body, args, key):
    import httpx2

    url = endpoint(args, payload["model"])
    with httpx2.Client(timeout=args.timeout, follow_redirects=False) as client:
        response = client.post(url, content=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    if not 200 <= response.status_code < 300:
        raise ProviderError(f"Cloudflare request failed (HTTP {response.status_code}); no answers returned")
    try:
        raw = json.loads(response.content, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError):
        raise ProviderError("Cloudflare returned invalid JSON; no answers returned") from None
    return normalize_response(raw, payload["questions"])
