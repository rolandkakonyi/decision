# Executable provider protocol

Decision runs trusted provider programs in any language. A package contains a
`decision-provider.json` manifest and the program/resources it needs. Authors
own dependencies and packaging; users supply any declared runtime. Programs
have normal user permissions. The interface is not a sandbox.

## Package and installation

```json
{
  "protocol_version": 1,
  "name": "acme",
  "version": "1.0.0",
  "command": ["node", "provider.js"],
  "setup": ["npm", "ci"],
  "default_base_url": "https://api.acme.example/v1",
  "default_model": "judge-1",
  "credential_env_vars": ["ACME_API_KEY"],
  "schema_constraints": {}
}
```

`setup` and `default_model` may be omitted. Command arrays are executed directly,
without a shell, from the installed package directory. Use a self-contained
binary to avoid an interpreter requirement. Names start with a lowercase
letter and contain lowercase letters, digits, underscores, or hyphens.
`typesafe`, `openai`, `cloudflare`, and `gemini` are reserved.

```bash
decision providers install ./acme
decision providers install ./acme/decision-provider.json
decision providers install 'git+https://github.com/example/acme.git#v1.0.0'
decision providers install https://example.com/acme-1.0.0.tar.gz --sha256 HEX_DIGEST
decision providers list
decision providers remove acme
```

HTTPS archives require the expected SHA-256 digest and contain the manifest at
the root or in a single enclosing directory. Git sources require an explicit
revision; the resolved commit is recorded. HTTPS redirects are disabled. Local
Git/file and HTTP loopback fixtures are also supported. Git must be installed
when using Git sources.

Install stages an independent artifact, runs declared setup, validates its
manifest, then activates the registration. Reinstalling a name updates it;
failed setup leaves the current registration intact. Setup never runs during
evaluation. Removal deactivates a registration; artifacts are retained so a
running MCP server can continue using its pinned version.

`--plugin-home DIRECTORY` selects personal storage. `--project-config FILE`
explicitly activates an additional registration file, with project entries
taking precedence. Install/remove with that flag affects the project file.
There is no implicit project discovery or dependency installation.

## Messages

Each operation starts a new provider process. It reads one UTF-8 JSON line on
stdin, writes one UTF-8 JSON response line to stdout, then exits successfully.
Diagnostics belong on stderr and must exclude secrets and private context.
Duplicate JSON keys, extra output, nonzero exits, and malformed frames fail.
The frame limit is 64 MiB.

Decision first verifies the package identity:

```json
{"protocol_version":1,"method":"describe"}
```

```json
{"protocol_version":1,"ok":true,"result":{"name":"acme","version":"1.0.0"}}
```

`prepare` receives a validated common request with model defaults applied.
Assets contain normalized PNG/JPEG `data_base64` descriptors, never local paths.
It must preserve state, questions, and assets; it may normalize the model.
Validate supported question types, models, image counts, and body limits here,
without inference or credentials. This operation also runs for dry runs.

```json
{"protocol_version":1,"method":"prepare","request":{"state":"Hello","questions":{"greeting":{"type":"noul","instructions":"Is this a greeting?"}},"model":"judge-1"}}
```

Return the common request as `result`. Evaluation adds fixed connection
settings and the resolved credential:

```json
{"protocol_version":1,"method":"evaluate","request":{"state":"Hello","questions":{"greeting":{"type":"noul","instructions":"Is this a greeting?"}},"model":"judge-1"},"settings":{"base_url":"https://api.acme.example/v1","timeout":30.0,"account_id":null},"credential":"RESOLVED_AT_RUNTIME"}
```

Send one provider evaluation per batch, with no retries or redirects. Validate
provider-specific envelopes, refusals, and duplicate answers, then return:

```json
{"protocol_version":1,"ok":true,"result":{"model":"judge-1","answers":{"greeting":{"type":"noul","noul":0.9}},"usage":{"input_tokens":0,"output_tokens":null}}}
```

Decision validates every answer against the original questions and owns
`duration_ms`. IDs/types must match; choices must be offered; probability maps
must be complete and total 1 within 0.02. Counts are nonnegative integers or
null; zero remains zero. Invalid data fails the whole batch.

For errors, return `ok:false` and `error.code` of `invalid_request` or
`provider_error`. The host uses its own safe diagnostic and ignores error
messages from the provider. It applies the timeout and terminates the provider
process tree on timeout/cancellation. Programs inherit a restricted runtime
environment; the selected credential travels through stdin, not command args.
Do not assume unrelated environment variables are available.

## Configuration and MCP

Base URL precedence is flag, `DECISION_BASE_URL`, then manifest default. Model
precedence is flag, `DECISION_MODEL`, request model, then manifest default.
MCP per-call model overrides its startup default.

An explicit `--api-key-env` or `DECISION_API_KEY_ENV` is exclusive. Otherwise,
Decision uses `DECISION_API_KEY`, then manifest credential names only on the
exact default HTTPS host/port. Custom hosts require an explicit selector or
generic key. Built-in credential names and `DECISION_*` names cannot be
registered as external fallback names. URLs reject embedded credentials,
queries, fragments, and non-HTTPS transport except HTTP loopback.

`schema_constraints` narrows the common MCP schema through `allOf`; it cannot
grant routing or credential authority. Implement matching checks in `prepare`:
the schema fragment is advertising, not an executable host validator.

An inference server selects one provider and installed artifact until restart:

```bash
decision mcp --provider acme
```

Explicit management mode exposes `provider_install`, `provider_list`, and
`provider_remove` instead of inference:

```bash
decision mcp --management
```

Repeat `--disable-provider NAME` to exclude a built-in or installed provider
for one CLI invocation or server lifetime. Disabled selection fails before
launching the program. Other installed providers are not loaded.

Skill and Codex plugin exports never launch providers. Add custom variable
names with `decision plugin --env-var NAME`; values are never exported.
See [the executable fixture](../tests/plugin_fixture.py) and
[conformance tests](../tests/test_provider_plugin_mcp.py) for working examples,
including install/update, CLI/MCP, images, timeout, and cancellation.
