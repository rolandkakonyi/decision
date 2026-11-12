"""Generate a Codex plugin and local marketplace from the companion skill."""

import hashlib
import json
from pathlib import Path
import re

MARKETPLACE = "decision-local"
PACKAGE = Path("plugins/decision")
MANIFEST = PACKAGE / ".codex-plugin/plugin.json"
MCP = PACKAGE / ".mcp.json"
CATALOG = Path(".agents/plugins/marketplace.json")
ENV_VARS = (
    "DECISION_PROVIDER", "DECISION_BASE_URL", "DECISION_API_KEY_ENV", "DECISION_API_KEY",
    "DECISION_MODEL", "DECISION_ACCOUNT_ID",
    "TYPESAFE_BASE_URL", "TYPESAFE_API_KEY", "TYPESAFEAI_API_KEY", "TYPESAFE_DEFAULT_MODEL",
    "OPENAI_BASE_URL", "OPENAI_API_KEY",
    "CLOUDFLARE_BASE_URL", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_AUTH_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
)
INTEGRATIONS = {
    "cli": """This plugin uses CLI integration. Use the installed `decision` command for
queries, with `--json` when processing answers in code. This package registers
no MCP server. The MCP sections below describe the separate, optional interface;
do not register a server as part of a query.
""",
    "mcp": """This plugin uses MCP integration. Prefer this plugin's `decision` MCP server
and its `evaluate` tool for queries. It accepts the same named questions and
base64 assets described below. CLI examples explain the shared request format;
do not launch a duplicate MCP server or substitute CLI inference after a tool
failure. The installed `decision` executable starts the server.
""",
}

GUIDE = r"""# Codex plugin export

Export either CLI or MCP integration, then install it explicitly on a local
Codex host. Both modes contain the complete generated decision skill, schemas,
and examples. Install decision separately using `uv tool install /path/to/decision`
or a native standalone executable. The generated plugin does not bundle Python,
dependencies, credentials, or a binary.

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
TypeSafe/OpenAI/Cloudflare token names, provider base URLs, and Cloudflare
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

Configure `DECISION_PROVIDER` (`typesafe`, `openai`, or `cloudflare`),
`DECISION_MODEL`, `DECISION_BASE_URL`, and `DECISION_API_KEY_ENV` in Codex's
process environment. Matching official provider hosts accept their standard
credential variables too. Cloudflare requires `CLOUDFLARE_ACCOUNT_ID` at its
default API root. Restart Codex after changing its environment. Env files are
not loaded automatically. Refer to the bundled skill for provider limits and
credential routing.

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
"""


def encoded(value):
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def plugin_files(skill, version, integration="mcp", command="decision", env_vars=()):
    if integration not in INTEGRATIONS:
        raise ValueError("Plugin integration must be cli or mcp")
    if integration == "cli" and (command != "decision" or env_vars):
        raise ValueError("--mcp-command and --env-var apply only to MCP integration")
    if not isinstance(command, str) or not command.strip() or any(c in command for c in "\r\n\0"):
        raise ValueError("MCP command must be an executable name or absolute path, without line breaks")
    if not Path(command).is_absolute() and (Path(command).name != command or any(c.isspace() for c in command)):
        raise ValueError("Use an MCP executable name or absolute path, not a relative path or shell command")
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in env_vars):
        raise ValueError("--env-var requires an environment variable name, never NAME=value")
    files = {PACKAGE / "skills/decision" / name: content for name, content in skill.items()}
    entrypoint = PACKAGE / "skills/decision/SKILL.md"
    files[entrypoint] = files[entrypoint].replace(
        "# Decision\n", "# Decision\n\n## Plugin integration\n\n" + INTEGRATIONS[integration], 1)
    if integration == "cli":
        files[entrypoint] = files[entrypoint].replace(
            "Prefer this tool when available for structured code-mode composition.",
            "This CLI plugin uses command invocations for structured code-mode composition.")
    manifest = {
        "name": "decision", "description": "Typed judgments with Jev, OpenAI Decisions, or Cloudflare Clef.",
        "author": {"name": "Roland Kakonyi"}, "repository": "https://github.com/rolandkakonyi/decision",
        "skills": "./skills/decision", "requires_local_executor": True,
        "interface": {"displayName": "Decision", "shortDescription": "Typed judgments over text and images"},
    }
    if integration == "mcp":
        manifest["mcpServers"] = "./.mcp.json"
        files[MCP] = encoded({"mcpServers": {"decision": {
            "command": command, "args": ["mcp"], "env_vars": sorted(set(ENV_VARS) | set(env_vars)),
        }}})
    # Changing integration, launch configuration, or skill content changes the cache version.
    digest = hashlib.sha256(encoded(manifest).encode())
    for name, content in sorted(files.items()):
        digest.update(name.as_posix().encode() + b"\0" + content.encode())
    manifest["version"] = version + "+" + digest.hexdigest()[:16]
    files[MANIFEST] = encoded(manifest)
    files[CATALOG] = encoded({
        "name": MARKETPLACE, "interface": {"displayName": "Decision Local"},
        "plugins": [{"name": "decision", "source": {"source": "local", "path": "./plugins/decision"},
                     "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}],
    })
    files[Path("README.md")] = f"<!-- Generated by decision {version}; integration: {integration}. -->\n\n" + GUIDE
    return files


def export_plugin(files, output, check=False):
    root = Path(output).expanduser().absolute()
    if root.is_symlink():
        raise ValueError("Plugin output cannot use symlinks")
    root = root.resolve()
    existing = set()
    if root.exists():
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Plugin output cannot contain symlinks")
            if path.is_file():
                existing.add(path.relative_to(root))
    if check:
        if existing != set(files) or any((root / name).read_text() != content for name, content in files.items()):
            raise ValueError("Generated plugin is stale; regenerate with the same integration options")
        return
    stale_mcp = {MCP} if MCP in existing and MANIFEST in existing and CATALOG in existing else set()
    if existing - set(files) - stale_mcp:
        raise ValueError("Plugin output contains unmanaged files; choose an empty directory or a previous generated export")
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for name in stale_mcp - set(files):
        (root / name).unlink()
