"""Ask typed questions through TypeSafe, OpenAI/Gemini Decisions, or Cloudflare Clef."""

import argparse
import asyncio
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

from decision_cli.errors import ProviderError

VERSION = "0.9.0"


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
    plugin = commands.add_parser("plugin", help="Export a Codex plugin and local marketplace; no API access")
    plugin.add_argument("--output", type=Path, required=True, help="Directory receiving the generated marketplace and plugins/decision package")
    plugin.add_argument("--integration", choices=("cli", "mcp"), default="mcp", help="Use installed CLI commands or bundle a STDIO MCP declaration (default: mcp)")
    plugin.add_argument("--mcp-command", default="decision", help="MCP executable name or absolute path, without arguments (default: decision on PATH)")
    plugin.add_argument("--env-var", action="append", default=[], metavar="NAME", help="Additional environment variable name to forward to MCP; repeat for custom provider keys")
    plugin.add_argument("--check", action="store_true", help="Verify the generated plugin matches this tool and integration options")
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
        sub.add_argument("--provider", default=os.getenv("DECISION_PROVIDER", "typesafe"), metavar="NAME",
                         help="Built-in typesafe, openai, cloudflare, gemini, or installed provider plugin (default: DECISION_PROVIDER or typesafe)")
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


def selected_provider(args):
    from decision_cli.registry import load_provider

    provider = getattr(args, "_provider", None)
    if provider is None or provider.info.name != args.provider:
        provider = args._provider = load_provider(args.provider)
    return provider


def prepare_request(payload, args, *, allow_paths=False):
    payload = dict(validate_request(payload, allow_paths=allow_paths))
    if args.model and "model" not in payload:
        payload["model"] = args.model
    default = selected_provider(args).info.default_model
    if default is not None:
        payload.setdefault("model", default)
    return payload


def connection(args):
    info = selected_provider(args).info
    if not args.base_url:
        args.base_url = (os.getenv(info.base_url_env) if info.base_url_env else None) or info.default_base_url
    url = urlsplit(args.base_url)
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Base URL must have a host and no credentials, query, or fragment")
    local = url.hostname in {"localhost", "127.0.0.1", "::1"}
    if url.scheme != "https" and not (url.scheme == "http" and local):
        raise ValueError("Base URL requires HTTPS (HTTP is allowed for loopback only)")
    official = url.scheme == "https" and url.port in (None, 443) and url.hostname == urlsplit(info.default_base_url).hostname
    names = [args.api_key_env] if args.api_key_env else ["DECISION_API_KEY"]
    if official and not args.api_key_env:
        names += info.credential_env_vars
    return next((os.environ[n].strip() for n in names if os.environ.get(n, "").strip()), None)


def validate_connection(args):
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    return connection(args)


def prepare_batch(payload, args, images):
    from decision_cli.providers import PreparedRequest

    request = deepcopy({key: value for key, value in payload.items() if key != "assets"})
    try:
        prepared = selected_provider(args).prepare(request, images)
        if not isinstance(prepared, PreparedRequest):
            raise TypeError("Invalid prepared request")
        validate_request(prepared.request)
        if (prepared.request["state"] != payload["state"] or prepared.request["questions"] != payload["questions"]
                or "assets" in prepared.request):
            raise ProviderError("Provider preparation must preserve state and questions and omit assets")
        return prepared
    except (ValueError, ProviderError):
        raise
    except Exception:
        raise ProviderError("Provider request preparation failed; no answers returned") from None


def evaluate_request(payload, args, *, allow_paths=False, image_data=None):
    """Shared CLI/MCP operation: one request for all questions sharing state."""
    payload = prepare_request(payload, args, allow_paths=allow_paths)
    from decision_cli.images import load_images
    from decision_cli.providers import ConnectionSettings
    from decision_cli.results import validate_result

    images = load_images(payload, allow_paths) if image_data is None else image_data
    prepared = prepare_batch(payload, args, images)
    key = validate_connection(args)
    if not key:
        raise ValueError("No provider key: set DECISION_API_KEY or select it with DECISION_API_KEY_ENV / --api-key-env; matching official provider hosts also accept their provider-specific credential variables")
    settings = ConnectionSettings(args.base_url, args.timeout, args.account_id)
    provider = selected_provider(args)
    try:
        provider.validate_connection(prepared, settings)
    except (ValueError, ProviderError):
        raise
    except Exception:
        raise ProviderError("Provider connection validation failed; no answers returned") from None
    try:
        started = time.perf_counter()
        result = provider.evaluate(prepared, settings, key)
        # Validate raw values before SDK normalization can coerce invalid answers.
        validate_result(result, payload["questions"])
        from typesafe_sdk import SystemOneResponse

        result = SystemOneResponse.model_validate_json(json.dumps(result, allow_nan=False)).model_dump(mode="json")
        validate_result(result, payload["questions"])
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result
    except ProviderError:
        raise
    except Exception as error:
        status = getattr(error, "status_code", None)
        suffix = f" (HTTP {status})" if isinstance(status, int) else ""
        raise ProviderError(f"{type(error).__name__}{suffix}; request failed, no answer returned") from None


def request_schema(provider="typesafe"):
    """Providers can restrict, but cannot replace, the common MCP envelope."""
    from decision_cli.registry import load_provider
    from decision_cli.schema import request_schema as common_schema

    schema = common_schema()
    selected = load_provider(provider) if isinstance(provider, str) else provider
    try:
        constraints = selected.schema_constraints()
        if not isinstance(constraints, dict):
            raise TypeError("Invalid schema constraints")
        json.dumps(constraints, allow_nan=False)
    except Exception:
        raise ProviderError("Provider schema generation failed") from None
    if constraints:
        schema["allOf"] = [constraints]
    return schema


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

    # Resolve once: installed plugins and routing are fixed for this server.
    schema = request_schema(selected_provider(args))

    async def list_tools(_context, _params):
        return types.ListToolsResult(tools=[types.Tool(
            name="evaluate", description="Ask Jev or a System One compatible model multiple typed questions about shared context in one request. Returns answers keyed by question ID, probabilities, model, token usage, and duration_ms for the batch. Does not take actions based on judgments.",
            inputSchema=schema, outputSchema=response_schema(),
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
        if args.command == "plugin":
            from decision_cli.plugin import export_plugin, plugin_files
            from decision_cli.skill import skill_files

            files = plugin_files(skill_files(cli, VERSION, request_schema()), VERSION,
                                 args.integration, args.mcp_command, args.env_var)
            export_plugin(files, args.output, args.check)
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
            prepared = prepare_batch(payload, args, images)
            preview = dict(prepared.request)
            if "assets" in payload:
                preview["assets"] = payload["assets"]
            print(json.dumps(preview, indent=2, allow_nan=False) if args.json else render_preview(preview, images))
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
