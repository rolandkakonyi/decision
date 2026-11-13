# External provider commands

Decision runs trusted provider executables as separate processes. A provider can
use any language; its author supplies packaging, dependency pins and optional
setup. Users install the runtime that command needs. The native host and its
exports need no Python or Go installation.

## Install explicitly

A package contains `decision-provider.json` and its implementation files. Install
from a local directory, its manifest, a checksummed HTTPS tar.gz archive, or an
explicit Git ref:

```bash
decision providers install /path/to/provider-package
decision providers install /path/to/provider-package/decision-provider.json
decision providers install https://example.test/provider.tar.gz --sha256 SHA256_HEX
decision providers install 'git+https://example.test/provider.git#REF'
decision providers list
decision providers remove my-provider
```

Installation executes the package's optional setup command. Treat packages like
other executables you authorize to run as your user. Metadata is checked before
setup; an unsuccessful setup leaves the previous registration active. Archive
installations require a SHA-256 digest and reject traversal and links. Git refs
resolve to recorded exact commits. Updating requires another explicit install;
evaluation never fetches, updates, installs or runs setup. Removing a provider
unregisters it without deleting files used by running inference servers.

Select a registered provider using `--provider my-provider` or `DECISION_PROVIDER`.
The host validates common requests and raw results, chooses credentials and
measures elapsed time. `--disable-provider my-provider` fails a selection before
launching it, without falling back to another provider. Only the selected
provider is discovered and launched. Dry runs can call its credential-free
`describe` and `prepare` operations, but never evaluate or run setup.

## Personal and project scope

`--plugin-home DIRECTORY` selects the personal registration/artifact root.
`--project-config FILE` explicitly opts into a project registration file; the host
does not search the working directory or load project configuration implicitly.
Project registrations override personal names, while built-in names remain
reserved. Install, list, remove and inference support these launch flags:

```bash
decision providers install /path/to/provider-package --project-config ./providers.json
decision providers list --project-config ./providers.json
decision run request.json --provider my-provider --project-config ./providers.json
decision mcp --provider my-provider --project-config ./providers.json
```

## Inference and management MCP

A normal `decision mcp --provider my-provider` exposes only `evaluate`. Routing,
credential selection, timeout and scope are launch configuration; tool callers
cannot redirect them. The server resolves its provider once. Install/update/remove
operations do not change an existing inference server; restart it to select the
new registration.

An explicitly configured `decision mcp --management` exposes only
`provider_install`, `provider_list`, and `provider_remove`, without inference:

- `provider_install` takes `source` and optional `sha256`.
- `provider_list` takes an empty object and returns registered names/versions.
- `provider_remove` takes `name` and unregisters that provider.

Set plugin home and project scope in server launch flags. Keep management setup
an explicit user operation; do not install a package or enable management tools
as a side effect of answering a judgment query. Codex-plugin export's default MCP
declaration starts inference mode, not this management server.

## Package manifest and protocol

For example, an author-provided Node.js command can use:

```json
{
  "protocol_version": 1,
  "name": "my-provider",
  "version": "1.0.0",
  "command": ["node", "provider.mjs"],
  "setup": ["node", "setup.mjs"],
  "default_base_url": "https://api.example.test",
  "default_model": "provider-model",
  "credential_env_vars": ["MY_PROVIDER_KEY"],
  "schema_constraints": {}
}
```

`setup`, `default_model`, `credential_env_vars` and `schema_constraints` are
optional. Commands are argv arrays, never shell strings, and run with the
installed package root as their working directory. An absolute executable path
is supported. Setup runs only during explicit install. No runtime or dependency
manager is provisioned automatically.

Each operation launches one command and exchanges a bounded, newline-terminated
JSON frame. The methods are `describe`, `prepare`, and `evaluate`, with
`protocol_version: 1`. Describe returns the manifest name/version; prepare
normalizes the common request while preserving state, questions and assets.
Only evaluate receives connection settings and the selected credential, through
stdin rather than argv. Assets use base64 descriptors across this boundary.
Results use the common model/answers/usage shape and are validated atomically;
the host supplies authoritative `duration_ms`. Duplicate keys and nonfinite
numbers are rejected. Timeout and MCP cancellation stop the local process group;
they cannot promise cancellation at the remote provider.
