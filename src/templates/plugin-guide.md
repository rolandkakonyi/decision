# Codex plugin export

Export either CLI or MCP integration, then install it explicitly on a local
Codex host. Both modes contain the complete generated decision skill, schemas,
and examples. Install the native decision executable separately for your operating
system and architecture. The generated plugin does not bundle dependencies,
credentials, or a binary. Neither the native host nor the export requires an interpreter.

## Choose the integration

```bash
decision plugin --output /private/decision-plugin-cli --integration cli
decision plugin --output /private/decision-plugin-mcp --integration mcp
```

CLI integration directs the skill to the installed `decision` command and
registers no MCP server. It uses `--json` when processing answers in code.
MCP integration (the default) also declares a STDIO `decision mcp` server and
directs the skill to its `evaluate` tool. It launches the installed executable;
the server is not embedded in the plugin files. Neither mode modifies Codex
settings, installs anything, reads credentials, or contacts a provider during export.

For a standalone binary or desktop host where PATH differs, use an absolute
MCP executable path. Spaces in that path are supported; do not supply a shell
command or arguments:

```bash
decision plugin --output /private/decision-plugin-mcp --integration mcp \
  --mcp-command /absolute/path/to/decision --env-var MY_PROVIDER_KEY
```

`--env-var NAME` is repeatable and applies only to MCP. It forwards additional
variable names for custom provider keys; values are never copied into files.
For example, set both `DECISION_API_KEY_ENV=MY_PROVIDER_KEY` and
`MY_PROVIDER_KEY` in Codex's environment. The standard `DECISION_*` settings,
TypeSafe/OpenAI/Cloudflare/Gemini token names, provider base URLs, and Cloudflare
account-ID variables are already included. Exports ignore the environment's
current values, so generation stays reproducible and credential-free.

## Install explicitly

The output is a local marketplace root, with the package at `plugins/decision/`,
its manifest at `plugins/decision/.codex-plugin/plugin.json`, and the catalog
at `.agents/plugins/marketplace.json`. From the generated output directory:

```bash
codex plugin marketplace add "$PWD"
codex plugin add decision@decision-local
```

Start a new Codex session. Keep the generated directory available as the local
marketplace source. Each export uses the plugin ID `decision@decision-local`;
select one output on each host. If changing the source directory, remove the old
marketplace with `codex plugin marketplace remove decision-local` before adding
the new one.

Configure `DECISION_PROVIDER` (`typesafe`, `openai`, `cloudflare`, `gemini`, or an installed provider command),
`DECISION_MODEL`, `DECISION_BASE_URL`, and `DECISION_API_KEY_ENV` in Codex's
process environment. Matching official provider hosts accept their standard
credential variables too. Cloudflare requires `CLOUDFLARE_ACCOUNT_ID` at its
default API root. Restart Codex after changing its environment. Env files are
not loaded automatically. Refer to the bundled skill for provider limits and
credential routing.

Codex-plugin export and runtime-provider installation are separate operations.
For an external provider, explicitly install its package with
`decision providers install SOURCE` on the same host before launching inference.
The package author supplies its command and optional setup; the user supplies
any required runtime. Export never fetches or executes those packages. The
generated MCP declaration starts inference mode with only `evaluate`; it does
not enable management tools. Read the skill's `references/providers.md` for
provider scope, updates, and an explicitly configured management server.

## Refresh and verify

Regenerate with the same output and options after updating decision. Switching
from MCP to CLI removes the generated MCP declaration. Content changes,
including integration and launch settings, change the package version so Codex
can distinguish cached exports. Unmanaged files and symlinks are rejected.

```bash
decision plugin --output /private/decision-plugin-cli --integration cli --check
codex plugin marketplace upgrade decision-local
codex plugin add decision@decision-local
```

For MCP checks, repeat the original `--integration mcp`, `--mcp-command`, and
`--env-var` options. Checks do not write files or access a provider. Installing
or refreshing a plugin does not install or update the executable; confirm with
`decision --version`. Edit the generator rather than the generated files.

See the [official plugin packaging guide](https://developers.openai.com/plugins/build/plugins)
for the Codex compatibility manifest and local marketplace format.
