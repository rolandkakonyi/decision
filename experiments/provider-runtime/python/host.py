#!/usr/bin/env python3
"""Experimental frozen Python host; production entrypoints are untouched."""
import argparse
import asyncio
import base64
from copy import deepcopy
import json
import logging
import math
import os
from pathlib import Path
import sys
import time

# Source execution and the frozen build use the same experimental entrypoint.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from decision_cli.cli import validate_request, response_schema
from decision_cli.errors import ProviderError
from decision_cli.images import attach_cli_images, image_format, load_images
from decision_cli.results import validate_result
from decision_cli.schema import request_schema
from store import Store, check_url
from wire import loads, operation


def parser():
    cli = argparse.ArgumentParser(prog="decision")
    cli.add_argument("--version", action="version", version="decision prototype 1")
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("run", "mcp"):
        sub = commands.add_parser(name)
        if name == "run":
            sub.add_argument("request_file")
            sub.add_argument("--json", action="store_true")
            sub.add_argument("--dry-run", action="store_true")
        else:
            sub.add_argument("--management", action="store_true")
        sub.add_argument("--provider", default=os.getenv("DECISION_PROVIDER", "typesafe"))
        sub.add_argument("--model", default=os.getenv("DECISION_MODEL"))
        sub.add_argument("--base-url", default=os.getenv("DECISION_BASE_URL"))
        sub.add_argument("--api-key-env", default=os.getenv("DECISION_API_KEY_ENV"))
        sub.add_argument("--timeout", type=float, default=30)
        sub.add_argument("--disable-provider", action="append", default=[])
        scope(sub)
    providers = commands.add_parser("providers").add_subparsers(dest="action", required=True)
    for name in ("install", "list", "remove"):
        sub = providers.add_parser(name)
        if name == "install":
            sub.add_argument("source")
            sub.add_argument("--sha256")
        if name == "remove":
            sub.add_argument("name")
        scope(sub)
    return cli


def scope(sub):
    sub.add_argument("--plugin-home", default=str(Path.home() / ".local/share/decision-prototype/providers"))
    sub.add_argument("--project-config")


class Host:
    def __init__(self, args):
        self.args = args
        self.store = Store(args.plugin_home, args.project_config)
        self.package = None
        self.manifest = None
        self.schema = request_schema()

    async def initialize(self):
        args = self.args
        if args.provider in args.disable_provider:
            raise ValueError("Selected provider is disabled")
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("Timeout must be finite and positive")
        if args.provider == "typesafe":
            self.manifest = {"default_base_url": "https://api.typesafe.ai", "credential_env_vars": ["TYPESAFE_API_KEY", "TYPESAFEAI_API_KEY"]}
            args.base_url = args.base_url or os.getenv("TYPESAFE_BASE_URL") or self.manifest["default_base_url"]
        else:
            self.package, self.manifest = self.store.resolve(args.provider)
            described = await operation(self.package, self.manifest, "describe", args.timeout)
            if (not isinstance(described, dict) or described.get("name") != args.provider
                    or described.get("version") != self.manifest["version"]):
                raise ProviderError("Provider description does not match installed manifest")
            constraints = self.manifest.get("schema_constraints", {})
            if constraints:
                self.schema["allOf"] = [constraints]
            args.base_url = args.base_url or self.manifest["default_base_url"]
        check_url(args.base_url)

    def credential(self):
        args = self.args
        current = check_url(args.base_url)
        official = check_url(self.manifest["default_base_url"])
        names = [args.api_key_env] if args.api_key_env else ["DECISION_API_KEY"]
        if not args.api_key_env and current.scheme == "https" and current.port in (None, 443) and current.hostname == official.hostname:
            names += self.manifest.get("credential_env_vars", [])
        key = next((os.environ[name].strip() for name in names if os.environ.get(name, "").strip()), None)
        if not key:
            raise ValueError("No provider key; set DECISION_API_KEY or --api-key-env")
        return key

    async def prepare(self, payload, allow_paths=False):
        payload = deepcopy(validate_request(payload, allow_paths=allow_paths))
        if self.args.model:
            payload.setdefault("model", self.args.model)
        if self.manifest.get("default_model"):
            payload.setdefault("model", self.manifest["default_model"])
        images = load_images(payload, allow_paths)
        if "assets" in payload:
            payload["assets"] = {name: {"type": "image", "format": image_format(data), "data_base64": base64.b64encode(data).decode()} for name, data in images.items()}
        if self.package:
            from jsonschema import Draft202012Validator
            if not Draft202012Validator(self.manifest.get("schema_constraints", {})).is_valid(payload):
                raise ValueError("Request violates provider constraints")
            prepared = await operation(self.package, self.manifest, "prepare", self.args.timeout, request=deepcopy(payload))
            try:
                validate_request(prepared)
                # Serialized comparisons distinguish true/1 and preserve arbitrary JSON integers.
                for field in ("state", "questions", "assets"):
                    if json.dumps(prepared.get(field), sort_keys=True) != json.dumps(payload.get(field), sort_keys=True):
                        raise ValueError("Provider preparation changed request")
            except Exception:
                raise ProviderError("Provider preparation changed or invalidated request") from None
            payload = prepared
        return payload, images

    async def evaluate(self, payload, allow_paths=False):
        payload, images = await self.prepare(payload, allow_paths)
        credential = self.credential()
        started = time.perf_counter()
        try:
            if self.package:
                result = await operation(self.package, self.manifest, "evaluate", self.args.timeout,
                                         request=payload, settings={"base_url": self.args.base_url, "timeout": self.args.timeout, "account_id": None}, credential=credential)
            else:
                result = await self.typesafe(payload, images, credential)
            validate_result(result, payload["questions"])
            from typesafe_sdk import SystemOneResponse
            result = SystemOneResponse.model_validate_json(json.dumps(result, allow_nan=False)).model_dump(mode="json")
            validate_result(result, payload["questions"])
            result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            return result
        except (ValueError, ProviderError):
            raise ProviderError("Provider returned invalid data or execution failed") from None
        except Exception:
            raise ProviderError("Provider execution failed; no answers returned") from None

    async def typesafe(self, payload, images, credential):
        import httpx2
        from typesafe_sdk import AsyncTypeSafeClient, Image, RetryPolicy, SystemOneResponse
        questions = payload["questions"]
        class RawCheckedResponse(SystemOneResponse):
            @classmethod
            def _decode(cls, response):
                validate_result(loads(response.content), questions)
                return super()._decode(response)
        logging.getLogger("typesafe_sdk").setLevel(logging.CRITICAL)
        options = {"response_model": RawCheckedResponse}
        if images:
            options["assets"] = {name: Image(data) for name, data in images.items()}
        if images or any(q["type"] == "box" for q in questions.values()):
            options["extra_headers"] = {"TypeSafe-Danger-Zone-Images": "true"}
        async with AsyncTypeSafeClient(api_key=credential, base_url=self.args.base_url,
                                      timeout=self.args.timeout, retry=RetryPolicy(max_retries=0), compression=False,
                                      http_client=httpx2.AsyncClient(timeout=self.args.timeout, follow_redirects=False, trust_env=False)) as client:
            result = await asyncio.wait_for(client.system_one(**{k: v for k, v in payload.items() if k != "assets"}, **options), self.args.timeout)
        return result.model_dump(mode="json")


async def serve(args):
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    host = Host(args)
    if not args.management:
        await host.initialize()
    management = {
        "provider_install": {"type": "object", "properties": {"source": {"type": "string"}, "sha256": {"type": "string"}}, "required": ["source"], "additionalProperties": False},
        "provider_list": {"type": "object", "properties": {}, "additionalProperties": False},
        "provider_remove": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
    }
    async def listing(_context, _params):
        if args.management:
            tools = [types.Tool(name=name, description=name.replace("_", " "), inputSchema=schema) for name, schema in management.items()]
        else:
            tools = [types.Tool(name="evaluate", description="Evaluate one typed batch using the configured provider", inputSchema=host.schema, outputSchema=response_schema())]
        return types.ListToolsResult(tools=tools)
    async def call(_context, params):
        try:
            arguments = params.arguments or {}
            if args.management:
                from jsonschema import Draft202012Validator
                if params.name not in management or not Draft202012Validator(management[params.name]).is_valid(arguments):
                    raise ValueError("Invalid management request")
                if params.name == "provider_install":
                    result = await asyncio.to_thread(host.store.install, arguments["source"], arguments.get("sha256"))
                elif params.name == "provider_remove":
                    result = host.store.remove(arguments["name"])
                else:
                    result = host.store.listing()
            else:
                if params.name != "evaluate":
                    raise ValueError("Unknown inference tool")
                result = await host.evaluate(arguments)
            # List results retain their CLI array shape in JSON text content.
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, allow_nan=False))], structuredContent=result if isinstance(result, dict) else None)
        except (ValueError, ProviderError):
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text="Invalid request or provider operation failed")])
        except Exception:
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text="Provider operation failed")])
    server = Server("decision-prototype-python", version="1", on_list_tools=listing, on_call_tool=call)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


async def execute(args):
    if args.command == "mcp":
        await serve(args)
        return
    host = Host(args)
    await host.initialize()
    if args.request_file == "-":
        payload = loads(sys.stdin.buffer.read())
        directory = Path.cwd()
    else:
        path = Path(args.request_file).expanduser().resolve()
        payload = loads(path.read_bytes())
        directory = path.parent
    validate_request(payload, allow_paths=True)
    if args.model:
        payload["model"] = args.model
    payload = attach_cli_images(payload, [], directory)
    if args.dry_run:
        result, _ = await host.prepare(payload, True)
    else:
        result = await host.evaluate(payload, True)
    print(json.dumps(result, allow_nan=False))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "providers":
            store = Store(args.plugin_home, args.project_config)
            if args.action == "install":
                result = store.install(args.source, args.sha256)
            elif args.action == "remove":
                result = store.remove(args.name)
            else:
                result = store.listing()
            print(json.dumps(result, allow_nan=False))
        else:
            asyncio.run(execute(args))
        return 0
    except ValueError:
        print("Invalid request or provider configuration", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Operation cancelled", file=sys.stderr)
        return 1
    except Exception:
        print("Provider operation failed; no answers returned", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
