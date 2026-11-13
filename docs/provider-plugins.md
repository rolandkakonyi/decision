# Provider API

Python packages register provider objects in the `decision.providers.v1`
entry-point group. The supported import surface is `decision_cli.providers`;
other modules are implementation details. The API version is independent of
the package release version. Providers must declare `api_version = 1`; breaking
interface revisions use a new entry-point group and API version.

Install providers into the same Python environment as `decision`. With a uv
tool installation, use `uv tool install /path/to/decision --with /path/to/provider`.
Standalone executables support built-ins only. Packages are trusted code with
the process's permissions and environment; the interface is not a sandbox.

## Registration

A provider package's `pyproject.toml` advertises an object, not a factory:

```toml
[project.entry-points."decision.providers.v1"]
acme = "acme_decision:provider"
```

Names use lowercase letters, digits, underscores, or hyphens, starting with a
letter. `typesafe`, `openai`, `cloudflare`, and `gemini` are reserved. Only the
selected entry point is loaded. Duplicate registrations for a selected name,
an unsupported API version, or a mismatched `ProviderInfo.name` fail locally.
An unselected broken extension does not affect built-ins or exports.

Repeat `--disable-provider NAME` on query commands or `decision mcp` to exclude
providers for that invocation. The check precedes entry-point discovery/import
and applies to explicit and environment-based selection, including built-ins.
Selecting a disabled name is an input error with no provider fallback.
Exclusions do not modify installed packages. MCP exclusions are fixed at startup;
individual tool calls cannot change them.

## Interface

The `Provider` protocol has four synchronous methods:

```python
from decision_cli.providers import (
    ConnectionSettings, PreparedRequest, ProviderInfo, ProviderError,
)

class AcmeProvider:
    api_version = 1
    info = ProviderInfo(
        name="acme",
        default_base_url="https://api.acme.example/v1",
        default_model="judge-1",
        base_url_env="ACME_BASE_URL",
        credential_env_vars=("ACME_API_KEY",),
    )

    def prepare(self, request, images) -> PreparedRequest:
        if images or any(q["type"] != "noul" for q in request["questions"].values()):
            raise ValueError("Acme accepts text Noul questions only")
        return PreparedRequest(request=request, data=request)

    def validate_connection(self, prepared, settings: ConnectionSettings) -> None:
        pass

    def evaluate(self, prepared, settings: ConnectionSettings, api_key: str) -> dict:
        import httpx2

        with httpx2.Client(timeout=settings.timeout, follow_redirects=False) as client:
            response = client.post(settings.base_url.rstrip("/") + "/judge",
                                   json=prepared.data,
                                   headers={"Authorization": "Bearer " + api_key})
        if not 200 <= response.status_code < 300:
            raise ProviderError("Acme request failed; no answers returned")
        return response.json()  # This example endpoint returns the common result shape.

    def schema_constraints(self) -> dict:
        return {"properties": {
            "questions": {"additionalProperties": {"properties": {"type": {"const": "noul"}}}},
            "assets": {"maxProperties": 0},
        }}

provider = AcmeProvider()
```

The package supplies its build configuration and transport dependencies. The
example uses the HTTP library already used by the built-in REST providers;
providers may use their own SDK instead. There is no core HTTP transport API.

- `prepare(request, images)` receives validated common JSON with provider/model
  defaults applied, and an ordered mapping of image references to PNG/JPEG bytes.
  `assets` descriptors and local paths are removed. Preserve state and questions;
  the returned common request may normalize the model. `PreparedRequest.data`
  is opaque provider-owned input: bytes, an SDK argument object, or image data.
  Check supported types, question/model limits, image limits, and encoded body
  size locally. This method runs for dry runs and before execution timing.
- `validate_connection(prepared, settings)` checks execution-only configuration,
  such as account syntax or a required account ID. `ConnectionSettings` contains
  the validated base URL, positive timeout, and optional account ID. This method
  runs before timing/network access and is skipped for dry runs. Neither local
  method may contact a provider or read credentials.
- `evaluate(prepared, settings, api_key)` performs one evaluation request for the
  whole batch. Image uploads may precede it, as with TypeSafe. Disable automatic
  retries and redirects, including SDK defaults. Validate provider-specific
  envelopes, duplicate answers, refusals, and usage before normalizing. Return
  common JSON containing `model`, `answers`, and `usage`; never return partial
  batches. Core validates IDs, types, probabilities, score bounds, and token
  counts before response-model normalization, and owns `duration_ms`. Unknown
  token counts are null; zero stays zero. Core timing includes execution and
  result handling, excluding local preparation.
- `schema_constraints()` returns a credential-free JSON Schema fragment that
  narrows the common request through `allOf`. It cannot widen the common envelope
  or grant MCP callers routing/settings authority. Keep the fragment consistent
  with runtime checks in `prepare`; core does not execute it as a validator.

`ValueError` from local preparation/configuration checks is an input error.
`ProviderError` signals an execution failure. Their messages must be locally
written and safe to show; never include response bodies, request content, keys,
or exception strings from SDKs. Unexpected exceptions are sanitized by core.
Providers must keep stdout silent for MCP and suppress SDK diagnostics that
could expose credentials or response bodies. Provider instances serve concurrent
MCP calls; keep per-request state in `PreparedRequest`, not instance attributes.

## Core-owned settings

Base URL precedence is CLI flag, `DECISION_BASE_URL`, `ProviderInfo.base_url_env`,
then `default_base_url`. Model precedence is CLI flag/`DECISION_MODEL`, request
model, then `default_model`; MCP per-call model overrides the server default.
`default_model=None` leaves model selection to the provider/SDK.

An explicit `--api-key-env` or `DECISION_API_KEY_ENV` is exclusive. Without one,
core tries `DECISION_API_KEY`, then `credential_env_vars` only on the exact
default base URL's HTTPS host at port 443/default. Custom hosts require a
generic or explicitly selected key. External fallbacks cannot use `DECISION_*`
or built-in credential names. Core rejects embedded URL credentials, query or
fragment, and non-HTTPS URLs except HTTP loopback.

MCP fixes the selected provider, model default, base URL, timeout, account ID,
and credential selector at startup. Calls can override only the model; key
values are resolved from the fixed selector for each evaluation. Skill/Codex
plugin exports do not discover third-party providers or forward their declared
environment variables automatically. Use the export command's existing
`--env-var NAME` option for custom MCP variables.

## Validation

Use local HTTP fixtures to check one batch per request, no redirects/retries,
preflight failures without network access, safe errors, normalized zero/unknown
usage, invalid/missing answers, and MCP model overrides/settings rejection.
The repository's [separate fixture package](../tests/fixtures/provider) and
[transport tests](../tests/test_provider_plugin_mcp.py) exercise real discovery
and a clean installed wheel outside the checkout. Built-ins implement the same
protocol and retain their native transport fixture coverage.
