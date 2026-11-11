"""Ask typed questions through TypeSafe, OpenAI Decisions, or Cloudflare Clef."""

import argparse
import asyncio
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

from decision_cli.errors import ProviderError

VERSION = "0.7.0"


def read_json(path):
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    return json.loads(text)


def parser():
    cli = argparse.ArgumentParser(prog="decision", description=__doc__)
    cli.add_argument("--version", action="version", version=f"decision {VERSION}")
    cli.add_argument("--json", action="store_true", help="Print evaluation or request preview as JSON")
    commands = cli.add_subparsers(dest="command", required=True)
    skill = commands.add_parser("skill", help="Print or export the companion Agent Skill; no API access")
    skill.add_argument("--output", type=Path, help="Write SKILL.md and references into this directory")
    skill.add_argument("--check", action="store_true", help="Verify an exported skill matches this tool")
    for name in ("noul", "choice", "score", "box", "ask", "run", "mcp"):
        sub = commands.add_parser(name)
        if name in {"noul", "choice", "score", "box"}:
            sub.add_argument("question")
        if name == "run":
            sub.add_argument("request_file", help="JSON object with shared state and named questions; - reads stdin")
        if name not in {"run", "mcp"}:
            state = sub.add_mutually_exclusive_group()
            state.add_argument("--state", help="Literal text context")
            state.add_argument("--state-file", help="JSON state file; - reads stdin")
        if name == "ask":
            sub.add_argument("--questions-file", required=True, help="JSON object of named questions; - reads stdin")
        if name == "choice":
            sub.add_argument("--option", action="append", required=True, help="NAME=description; repeat")
        if name == "score":
            sub.add_argument("--level", action="append", required=True, help="Ordered level; repeat")
        sub.add_argument("--model", default=os.getenv("DECISION_MODEL"))
        sub.add_argument("--provider", choices=("typesafe", "openai", "cloudflare"), default=os.getenv("DECISION_PROVIDER", "typesafe"),
                         help="Wire API provider (default: DECISION_PROVIDER or typesafe)")
        sub.add_argument("--base-url", default=os.getenv("DECISION_BASE_URL"),
                         help="API base URL; defaults to the selected provider's URL or provider-specific environment setting")
        sub.add_argument("--api-key-env", default=os.getenv("DECISION_API_KEY_ENV"),
                         help="Environment variable holding the provider key (default: DECISION_API_KEY_ENV)")
        sub.add_argument("--account-id", default=os.getenv("DECISION_ACCOUNT_ID") or os.getenv("CLOUDFLARE_ACCOUNT_ID"),
                         help="Cloudflare account ID (default: DECISION_ACCOUNT_ID or CLOUDFLARE_ACCOUNT_ID)")
        sub.add_argument("--timeout", type=float, default=30)
        if name != "mcp":
            sub.add_argument("--image", action="append", default=[], metavar="[NAME=]PATH",
                             help="Attach a PNG/JPEG under state.images; repeat for multiple images")
            sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                             help="Print evaluation or request preview as JSON")
            sub.add_argument("--dry-run", action="store_true", help="Preview the validated request without contacting a provider")
    return cli


def validate_request(payload, *, allow_paths=False):
    if not isinstance(payload, dict) or "state" not in payload or payload["state"] is None:
        raise ValueError("Request must be an object containing state and questions")
    if set(payload) - {"state", "questions", "model", "assets"}:
        raise ValueError("Only state, questions, model, and assets are supported")
    if "model" in payload and (not isinstance(payload["model"], str) or not payload["model"].strip()):
        raise ValueError("model must be a nonempty string")
    questions = payload.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise ValueError("questions must be a nonempty object")
    for name, q in questions.items():
        if (not isinstance(name, str) or not name.strip() or not isinstance(q, dict)
                or not isinstance(q.get("type"), str) or q["type"] not in {"noul", "choice", "score", "box"}):
            raise ValueError("Each named question must have type noul, choice, score, or box")
        if set(q) - {"type", "instructions", "criteria"}:
            raise ValueError("Question fields must be type, instructions, and criteria")
        if not isinstance(q.get("instructions"), str) or not q["instructions"].strip():
            raise ValueError("Each question needs nonempty instructions")
        criteria = q.get("criteria")
        if q["type"] == "choice" and (not isinstance(criteria, dict) or not criteria):
            raise ValueError("Choice criteria must be a nonempty object")
        if q["type"] == "choice" and any(not isinstance(k, str) or not k.strip() for k in criteria):
            raise ValueError("Choice names must be nonempty strings")
        if q["type"] == "score" and (not isinstance(criteria, list) or not criteria):
            raise ValueError("Score criteria must be a nonempty ordered list")
        if q["type"] == "noul" and criteria is not None and (not isinstance(criteria, dict) or set(criteria) - {"true", "false"}):
            raise ValueError("Noul criteria must be an object with true/false keys")
    try:
        json.dumps(payload, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError("Request must contain finite JSON values") from None
    from decision_cli.images import validate_assets

    validate_assets(payload, allow_paths)
    return payload


def make_request(args):
    if args.command == "run":
        payload = read_json(args.request_file)
    elif args.command == "ask":
        if args.state_file == "-" and args.questions_file == "-":
            raise ValueError("Only one input can read stdin")
        state = read_json(args.state_file) if args.state_file else args.state
        payload = {"state": state, "questions": read_json(args.questions_file)}
    else:
        if not args.question.strip():
            raise ValueError("Question cannot be empty")
        state = read_json(args.state_file) if args.state_file else args.state
        q = {"type": args.command, "instructions": args.question}
        if args.command == "choice":
            options = {}
            for option in args.option:
                key, sep, description = option.partition("=")
                if not sep or not key.strip() or key in options:
                    raise ValueError("Each option needs a unique nonempty NAME=description")
                options[key] = description or None
            q["criteria"] = options
        if args.command == "score":
            if any(not level.strip() for level in args.level):
                raise ValueError("Score levels cannot be empty")
            q["criteria"] = args.level
        payload = {"state": state, "questions": {"answer": q}}
    if args.model:
        if not isinstance(payload, dict):
            raise ValueError("Request must be an object")
        payload["model"] = args.model
    if not isinstance(payload, dict):
        raise ValueError("Request must be an object")
    if args.command != "run" and payload.get("state") is None and not args.image:
        raise ValueError("Supply --state, --state-file, or --image")
    from decision_cli.images import attach_cli_images

    directory = Path(args.request_file).expanduser().resolve().parent if args.command == "run" and args.request_file != "-" else Path.cwd()
    payload = attach_cli_images(payload, args.image, directory)
    return prepare_request(payload, args, allow_paths=True)


def prepare_request(payload, args, *, allow_paths=False):
    payload = dict(validate_request(payload, allow_paths=allow_paths))
    if args.model and "model" not in payload:
        payload["model"] = args.model
    if args.provider == "openai":
        from decision_cli.openai_provider import DEFAULT_MODEL, validate_request as validate_openai

        payload.setdefault("model", DEFAULT_MODEL)
        validate_openai(payload)
    elif args.provider == "cloudflare":
        from decision_cli.cloudflare_provider import DEFAULT_MODEL, model_name, validate_request as validate_cloudflare

        payload["model"] = model_name(payload.get("model", DEFAULT_MODEL))
        validate_cloudflare(payload)
    return payload


def connection(args):
    if args.provider not in {"typesafe", "openai", "cloudflare"}:
        raise ValueError("Provider must be typesafe, openai, or cloudflare")
    if not args.base_url:
        if args.provider == "openai":
            args.base_url = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        elif args.provider == "cloudflare":
            args.base_url = os.getenv("CLOUDFLARE_BASE_URL") or "https://api.cloudflare.com/client/v4"
        else:
            args.base_url = os.getenv("TYPESAFE_BASE_URL") or "https://api.typesafe.ai"
    url = urlsplit(args.base_url)
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Base URL must have a host and no credentials, query, or fragment")
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if url.scheme != "https" and not (url.scheme == "http" and local):
        raise ValueError("Base URL requires HTTPS (HTTP is allowed for loopback only)")
    official = url.scheme == "https" and url.port in (None, 443)
    names = [args.api_key_env] if args.api_key_env else ["DECISION_API_KEY"]
    if official and not args.api_key_env:
        if args.provider == "typesafe" and url.hostname == "api.typesafe.ai":
            names += ["TYPESAFE_API_KEY", "TYPESAFEAI_API_KEY"]
        elif args.provider == "openai" and url.hostname == "api.openai.com":
            names += ["OPENAI_API_KEY"]
        elif args.provider == "cloudflare" and url.hostname == "api.cloudflare.com":
            names += ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_AUTH_TOKEN"]
    key = next((os.environ[n].strip() for n in names if os.environ.get(n, "").strip()), None)
    return key


def validate_connection(args):
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    return connection(args)


def evaluate_request(payload, args, *, allow_paths=False, image_data=None):
    """Shared CLI/MCP operation: one request for all questions sharing state."""
    payload = prepare_request(payload, args, allow_paths=allow_paths)
    from decision_cli.images import load_images

    image_data = load_images(payload, allow_paths) if image_data is None else image_data
    payload.pop("assets", None)
    key = validate_connection(args)
    if not key:
        raise ValueError("No provider key: set DECISION_API_KEY or select it with DECISION_API_KEY_ENV / --api-key-env; matching official provider hosts also accept TYPESAFE_API_KEY / TYPESAFEAI_API_KEY, OPENAI_API_KEY, or CLOUDFLARE_API_TOKEN / CLOUDFLARE_AUTH_TOKEN")
    if args.provider == "cloudflare":
        from decision_cli.cloudflare_provider import endpoint, prepare_body

        # Configuration and media-limit failures are input errors, before timing/network access.
        endpoint(args, payload["model"])
        cloudflare_body = prepare_body(payload, image_data)
    logging.getLogger("typesafe_sdk").setLevel(logging.CRITICAL)
    logging.getLogger("openai").setLevel(logging.CRITICAL)
    try:
        started = time.perf_counter()
        if args.provider == "openai":
            from decision_cli.openai_provider import evaluate

            result = evaluate(payload, image_data, args, key)
        elif args.provider == "cloudflare":
            from decision_cli.cloudflare_provider import evaluate

            result = evaluate(payload, cloudflare_body, args, key)
        else:
            from typesafe_sdk import Image, RetryPolicy, TypeSafeClient

            options = {}
            if image_data:
                options["assets"] = {name: Image(data) for name, data in image_data.items()}
            if image_data or any(q["type"] == "box" for q in payload["questions"].values()):
                options["extra_headers"] = {"TypeSafe-Danger-Zone-Images": "true"}
            with TypeSafeClient(api_key=key, base_url=args.base_url, timeout=args.timeout,
                                retry=RetryPolicy(max_retries=0), compression=False) as client:
                response = client.system_one(**payload, **options)
            result = response.model_dump(mode="json")
        if set(result.get("answers", {})) != set(payload["questions"]):
            raise ProviderError("Provider returned missing or unexpected question IDs; no answer returned")
        if any(result["answers"][name]["type"] != question["type"] for name, question in payload["questions"].items()):
            raise ProviderError("Provider returned unexpected answer types; no answer returned")
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result
    except ProviderError:
        raise
    except Exception as error:
        status = getattr(error, "status_code", None)
        suffix = f" (HTTP {status})" if isinstance(status, int) else ""
        raise ProviderError(f"{type(error).__name__}{suffix}; request failed, no answer returned") from None


def request_schema(provider="typesafe"):
    """MCP input matches the batch JSON accepted by the CLI."""
    variants = []
    for kind in ("noul", "choice", "score", "box"):
        if provider in {"openai", "cloudflare"} and kind == "box":
            continue
        fields = {"type": {"const": kind}, "instructions": {"type": "string", "minLength": 1}}
        required = ["type", "instructions"]
        if kind == "choice":
            fields["criteria"] = {"type": "object", "minProperties": 1, "propertyNames": {"minLength": 1}}
            if provider in {"openai", "cloudflare"}:
                fields["criteria"].update(minProperties=2, maxProperties=255)
            required.append("criteria")
        elif kind == "score":
            fields["criteria"] = {"type": "array", "minItems": 1}
            if provider in {"openai", "cloudflare"}:
                fields["criteria"].update(minItems=2, maxItems=10)
            required.append("criteria")
        elif kind == "noul":
            fields["criteria"] = {"type": ["object", "null"], "properties": {"true": {}, "false": {}}, "additionalProperties": False}
        else:
            fields["criteria"] = {}
        variants.append({"type": "object", "properties": fields, "required": required, "additionalProperties": False})
    from decision_cli.images import assets_schema

    assets = assets_schema()
    model = {"type": "string", "minLength": 1, "description": "Optional model override; defaults to server configuration."}
    if provider == "cloudflare":
        from decision_cli.cloudflare_provider import MODELS, MAX_IMAGE_BYTES, MAX_IMAGES

        assets["maxProperties"] = MAX_IMAGES
        assets["additionalProperties"]["properties"]["data_base64"]["maxLength"] = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
        assets["description"] += " Clef: at most 4 images, 4 MiB each and 8 MiB combined."
        model["enum"] = [*MODELS, *("@cf/cloudflare/" + name for name in MODELS)]
    return {
        "type": "object", "required": ["state", "questions"], "additionalProperties": False,
        "properties": {
            "state": {"description": "Shared context for every independent question. JSON value, excluding null.",
                      "type": ["object", "array", "string", "number", "boolean"]},
            "questions": {"type": "object", "minProperties": 1, "propertyNames": {"minLength": 1},
                          **({"maxProperties": 400} if provider == "openai" else {}),
                          **({"maxProperties": 64, "propertyNames": {"pattern": r"^[A-Za-z0-9_.-]{1,100}$"}} if provider == "cloudflare" else {}),
                          "description": "Named questions evaluated together in ONE provider request.",
                          "additionalProperties": {"oneOf": variants}},
            "model": model,
            "assets": assets,
        },
    }


def response_schema():
    """Provider response plus client-measured elapsed time for the whole batch."""
    from typesafe_sdk import SystemOneResponse

    schema = SystemOneResponse.model_json_schema()
    schema["title"] = "DecisionResponse"
    schema["properties"]["duration_ms"] = {
        "type": "number", "minimum": 0,
        "description": "Client-measured elapsed milliseconds for SDK setup, image upload, and provider response handling for the entire batch.",
    }
    schema.setdefault("required", []).append("duration_ms")
    return schema


async def serve_mcp(args):
    """Expose the same operation through the official MCP SDK over stdio."""
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    async def list_tools(_context, _params):
        return types.ListToolsResult(tools=[types.Tool(
            name="evaluate", description="Ask Jev or a System One compatible model multiple typed questions about shared context in one request. Returns answers keyed by question ID, probabilities, model, token usage, and duration_ms for the batch. Does not take actions based on judgments.",
            inputSchema=request_schema(args.provider), outputSchema=response_schema(),
            annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True),
        )])

    async def call_tool(_context, params):
        try:
            if params.name != "evaluate":
                raise ValueError("Unknown tool; use evaluate")
            payload = validate_request(params.arguments or {})
            # Do not block the MCP event loop: ping/cancellation and independent calls can progress.
            result = await asyncio.to_thread(evaluate_request, payload, args)
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))],
                                        structuredContent=result)
        except (ValueError, ProviderError) as error:
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text=str(error))])

    server = Server("decision", version=VERSION, on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command == "skill":
            from decision_cli.skill import export_skill, skill_files

            export_skill(skill_files(cli, VERSION, request_schema()), args.output, args.check)
            return 0
        validate_connection(args)
        if args.command == "mcp":
            asyncio.run(serve_mcp(args))
            return 0
        payload = make_request(args)
        from decision_cli.images import load_images
        from decision_cli.output import render_preview, render_result

        images = load_images(payload, allow_paths=True)
        if args.dry_run:
            if args.provider == "cloudflare":
                from decision_cli.cloudflare_provider import prepare_body

                prepare_body(payload, images)
            print(json.dumps(payload, indent=2, allow_nan=False) if args.json else render_preview(payload, images))
            return 0
        result = evaluate_request(payload, args, allow_paths=True, image_data=images)
        print(json.dumps(result, allow_nan=False) if args.json else render_result(result))
        return 0
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError) as error:
        # JSON parse errors can contain user input; print only their category.
        message = "Invalid JSON input" if isinstance(error, json.JSONDecodeError) else str(error)
        print(f"decision: {message}", file=sys.stderr)
        return 2
    except (ProviderError, ImportError) as error:
        # Provider errors can echo context or secrets; never dump their bodies.
        message = str(error) if isinstance(error, ProviderError) else "Missing dependency; run through uv or use the standalone executable"
        print(f"decision: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
