# Management MCP server

Use management mode when an agent explicitly needs to install, list, or remove
provider packages. It is separate from [inference MCP](mcp.md): a management
server exposes no `evaluate` tool.

## Configure explicitly

```bash
codex mcp add decision-providers -- /absolute/path/to/decision mcp --management
```

Set the personal provider root with `--plugin-home DIRECTORY`, or opt into a
team registration file with `--project-config FILE`. These are server launch
settings; the working directory does not activate project configuration.

## Tools

- `provider_install` accepts `source` and optional `sha256`. Supported sources
  are local directories/manifests, explicitly pinned Git sources, and
  checksum-verified HTTPS tar.gz archives.
- `provider_list` accepts an empty object and returns registered names and
  versions.
- `provider_remove` accepts `name` and deactivates its registration.

Packages are trusted programs with normal user permissions. Installation may
run the package's declared setup command. Supply any runtime it requires;
Decision does not provision a language runtime automatically. Installation and
updates must be explicit user operations, separate from answering a judgment.

An unsuccessful setup preserves the previous active registration. Updating or
removing a provider does not redirect a running inference server: it keeps its
pinned artifact until restart. Removing a registration retains installed
artifacts needed by those servers.

See [provider installation](installed-providers.md) for scopes and command-line
examples, and the [provider protocol](provider-plugins.md) for package details.
