# Codex plugin and Agent Skill

These exports connect agents to a separately installed Decision executable.
They do not install executable provider packages. See
[providers and Codex exports](extensions.md) to choose the extension you need.

## Generate a Codex plugin

Choose how Codex invokes decision:

```bash
decision plugin --output "$PWD/.state/decision-cli" --integration cli
decision plugin --output "$PWD/.state/decision-mcp" --integration mcp
```

Both exports include the generated skill, examples, and schemas. **CLI** mode
uses the installed `decision` command and declares no MCP server. **MCP** mode
(the default) also declares a STDIO server running `decision mcp` and directs
the skill to its `evaluate` tool. The executable must be installed separately
on each host; a plugin export does not bundle a binary or dependencies.

The output is a local marketplace containing `plugins/decision/`. To install
the MCP export explicitly:

```bash
codex plugin marketplace add "$PWD/.state/decision-mcp"
codex plugin add decision@decision-local
```

Start a new Codex session. Set provider/model/base URL/key-selector variables in
Codex's environment; standard credential and Cloudflare account-ID variable
names are forwarded to the MCP process. Values are never written to the export.
For a custom key or a native binary outside PATH:

```bash
decision plugin --output "$PWD/.state/decision-mcp" --integration mcp \
  --mcp-command /absolute/path/to/decision --env-var MY_PROVIDER_KEY
```

Set `DECISION_API_KEY_ENV=MY_PROVIDER_KEY` and `MY_PROVIDER_KEY` in Codex's
environment. `--env-var` is repeatable and applies only to MCP. The generator
ignores current environment values and does not load `.env` files, contact a
provider, register the marketplace, or change Codex settings.

Repeat the original export options with `--check` to verify freshness. Switching
the same output from MCP to CLI removes the old MCP declaration. Regenerate,
run `codex plugin marketplace upgrade decision-local`, then
`codex plugin add decision@decision-local` to refresh, and start a new session.
Both modes use the ID `decision@decision-local`; select one output per host.
The package version changes when generated content or launch settings change.
Exports refuse unmanaged files and symlinks. See the generated
[plugin guide](plugin-reference.md) for the complete workflow.


## Generated skill

```bash
decision skill                         # Markdown to stdout, no API call
decision skill --output skills/decision
decision skill --output skills/decision --check
```

The generator owns `skills/decision/SKILL.md` and its example, input schema,
and command reference. The schema and command reference come from the running
tool. Edit `src/skill.rs`, `src/templates/`, and the CLI definitions, then regenerate.
CI rejects a stale generated export. No key or provider access is required.

Installing the skill does not install the executable. The export is portable:
copy it into a compatible agent's skill directory, and install `decision` on
that host separately.

