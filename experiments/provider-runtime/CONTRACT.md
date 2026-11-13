# Provider runtime prototype comparison

This experiment compares a frozen Python host and a native Go host on the same
provider workflow. It is not a replacement release. Production commands, generated
skills, dependencies and releases retain their existing behavior.

## Product constraints

Provider integrations only. Plugins are trusted executable commands in any
language, without an in-process language ABI. Authors own packaging, dependency
pins and optional setup commands; users provide language runtimes. Discovery is
selected-provider only. A disabled provider never launches. Common validation,
credential selection, safe diagnostics and elapsed time remain host-owned.
CLI and inference MCP share evaluation. Default MCP exposes only `evaluate`,
using one configured provider. Management MCP is explicitly enabled and exposes
`provider_install`, `provider_list`, `provider_remove`, without inference.
Running inference servers pin the resolved plugin artifact until restart.

## Matched scope

Implement TypeSafe built-in Noul/Choice/Score/Box and PNG/JPEG assets against the
existing local HTTP fixture, and the external-provider protocol below. Preserve
the common result shape including null versus zero usage and `duration_ms`.
Both hosts enforce current request/result ranges, probability tolerance, answer
IDs/types, image references/limits, MCP base64-only inputs, HTTPS except loopback,
no retries/redirects and safe errors. SDK-derived coercion must not weaken raw
result checks. Preserve arbitrary JSON state, including integers above 2**53.
Default provider is typesafe; environment provider/model/base URL/key overrides
and `--disable-provider NAME` work. MCP tool arguments cannot change provider,
endpoint or credential selector. No live inference or real credentials.

Other built-ins, generated skill/Codex plugin exports, Homebrew publication,
Windows and a complete Go replacement are outside this matched slice. Do not
compare a four-provider production Python bundle to a one-provider Go host as if
it were a matched product-size result. Build the Python experimental entrypoint
with only dependencies used by this slice; report what is actually bundled.

## CLI surface

Both experimental executables support:

```
decision --version
# Exactly: decision prototype 1

decision run REQUEST.json --json [--provider NAME] [--model MODEL]
  [--base-url URL] [--api-key-env NAME] [--timeout SECONDS]
  [--disable-provider NAME] [--dry-run] [--plugin-home DIRECTORY]
  [--project-config FILE]
decision mcp [same connection/provider flags] [--management]
decision providers install SOURCE [--plugin-home DIRECTORY]
  [--project-config FILE] [--sha256 HEX]
decision providers list [--plugin-home DIRECTORY] [--project-config FILE]
decision providers remove NAME [--plugin-home DIRECTORY] [--project-config FILE]
```

`run -` reads JSON from stdin. Success/preview is JSON; input/usage failures exit
2, execution failures exit 1, sanitized diagnostics on stderr. `--version` and
help do not discover plugins or require credentials. A dry-run returns the
normalized common request, never credentials; no evaluation or setup occurs.
Provider options are subcommand options, matching the current CLI convention.
Default plugin root is user-scoped; tests always pass isolated roots. Project
configuration is loaded only through explicit `--project-config FILE`, never by
walking the working tree. Its selected registrations override personal ones;
built-in names remain reserved. No interpreter is auto-provisioned.

## Plugin package and installation

A package root contains `decision-provider.json`:

```json
{
  "protocol_version": 1,
  "name": "fixture",
  "version": "1.0.0",
  "command": ["python3", "provider.py"],
  "setup": ["python3", "setup.py"],
  "default_base_url": "https://api.example.test",
  "default_model": "fixture-1",
  "credential_env_vars": ["FIXTURE_API_KEY"],
  "schema_constraints": {}
}
```

`setup`, `default_model`, `credential_env_vars`, and `schema_constraints` are
optional. Command/setup are argv arrays, not shell strings, executed with cwd
at the immutable installed package root. Relative script paths are relative to
that root; an absolute executable/command is supported for local registration.
Validate metadata before setup. Setup is allowed only during explicit install;
reinstalling a package/version is an explicit update. A failed setup must not
replace the active registration. Never place credential values in manifests,
registrations or setup logs. Host-generated status/errors do not echo scripts.

Sources: local package directory or manifest, HTTPS tar.gz archive with required
SHA-256, and `git+https://URL#REF`. For offline fixture tests only, also accept
`git+file:///absolute/repo#REF` and loopback HTTP archives with SHA-256. Git
resolves refs to an exact recorded commit; evaluation never fetches or updates.
Extraction rejects paths outside the package root and symlinks. Installation
stages before activating a versioned artifact; old artifacts remain available
to already-running servers. Minimal personal/project registration format is
implementation-owned, not a cross-language persisted-state ABI. Both hosts must
load the same package manifest, not each other's registry files.

`install` JSON: `{"name":"fixture","version":"1.0.0","restart_required":true}`.
`list` JSON: array of objects with at least `name`, `version`.
`remove` JSON: `{"name":"fixture","removed":true}`. Removal unregisters;
it must not delete files needed by a pinned inference session.
Management tools use identical results: `provider_install` accepts `source`
and optional `sha256`; `provider_list` accepts `{}`; `provider_remove` accepts
`name`. Plugin home/project scope come only from server launch flags.

## External provider wire protocol

One child process per operation. Input and output are one bounded UTF-8 JSON
line. Both sides reject duplicate object keys and non-finite numbers. Permit
at least 48 MiB frames, to accommodate 32 MiB assets encoded as base64. Credentials
never appear in argv. Use a minimal documented inherited environment sufficient
for declared runtimes; pass the selected evaluation credential only in stdin.
Capture plugin stdout as protocol; suppress/cap stderr and unexpected exceptions.
Use a process group on POSIX and terminate/reap it on timeout or MCP cancellation.
Stopping locally does not promise remote request cancellation.

Requests:

```json
{"protocol_version":1,"method":"describe"}
{"protocol_version":1,"method":"prepare","request":{"state":"hello","questions":{"q":{"type":"noul","instructions":"Is hello present?"}}}}
{"protocol_version":1,"method":"evaluate","request":{"state":"hello","questions":{"q":{"type":"noul","instructions":"Is hello present?"}}},"settings":{"base_url":"http://127.0.0.1:1234","timeout":2,"account_id":null},"credential":"fixture-only-key"}
```

`request.assets` crossing the boundary contains named base64 descriptors, never
paths. Prepare is credential-free, returns the normalized common request and
preserves state/questions/assets; the host need not persist opaque plugin data.
Evaluation receives that normalized request and returns the common provider
result without authoritative duration. Host validates the raw result and adds
timing. Settings and credential appear only in evaluation.

Responses:

```json
{"protocol_version":1,"ok":true,"result":{}}
{"protocol_version":1,"ok":false,"error":{"code":"invalid_request"}}
```

Describe result includes at least `name` and `version` matching the selected
manifest. Prepare result is the normalized request. Evaluate result contains
`model`, `answers`, `usage`, matching the current common contract. Error codes
`invalid_request`, `provider_error` map to local safe diagnostics; never render
arbitrary error strings or raw child output.

## Comparison evidence

Coordinator owns common black-box tests/fixture programs and alternating timing
measurements. Language siblings own their implementation directories and local
tests/build scripts. Both receive the exact same fixtures and protocol. Disclose
all unsupported behavior rather than silently relaxing assertions.

Measure executable bytes, compressed distribution bytes, --version startup,
credential-free dry-run startup, MCP initialize+tools/list, TypeSafe loopback
batch/image evaluation and external-plugin evaluation. Record warmups separately,
at least five alternating Python/Go runs, host/tool versions, exact artifact
hashes and canonical output hashes for each run. For evaluation, canonicalize
elapsed duration and integral answer-number formatting; never ignore missing
answers, changed types, or probability/usage differences. MCP readiness hashes the
negotiated protocol and full tool names/input/output schemas; retain informational
server metadata and descriptions in raw output. Keep first-launch observations
separate: these are not controlled
cold-cache experiments. Record memory where comparable and label parent-only
versus process-tree accounting. Performance claims require behavioral parity;
partial prototype gaps remain explicit in the decision recommendation.
