# Inference MCP server

An inference server exposes only `evaluate` for one configured provider. An
installed executable provider is resolved once at startup, and its artifact
is pinned until the server restarts. Provider install, update, and removal
operations do not change an existing inference server.

## Register with a client



```bash
codex mcp add decision -- /absolute/path/to/decision mcp
codex mcp add decision-openai -- /absolute/path/to/decision mcp --provider openai
codex mcp add decision-cloudflare -- /absolute/path/to/decision mcp --provider cloudflare
codex mcp add decision-gemini -- /absolute/path/to/decision mcp --provider gemini
```

The `evaluate` tool accepts `{state, questions, model?, assets?}` and returns structured
answers, model, usage, and `duration_ms` with an output schema. Forward the selected credential
variable and its selector in Codex's MCP environment settings. Provider routing,
account ID, key selection, and timeout are fixed server configuration; tool callers can
override only the model. Stdout is reserved for protocol messages in MCP mode.
OpenAI, Cloudflare, and Gemini servers advertise only Noul, Choice, and Score and their provider limits.
Forward `OPENAI_API_KEY` for the official OpenAI host, or the selected credential
variable and `DECISION_API_KEY_ENV` for another host.
For Cloudflare, forward `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.
For Gemini, forward `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) for the official host.

## Installed providers and scopes

Select an installed provider and, when needed, opt into a project registration
file explicitly:

```bash
decision mcp --provider acme --project-config ./providers.json
```

The working directory never activates a provider configuration. Use
`--plugin-home DIRECTORY` for a different personal root. Repeat
`--disable-provider NAME` to exclude a provider for the server's lifetime;
disabled providers never launch. These settings cannot be changed by tool calls.

## Administration is a separate mode

Use [management MCP](mcp-management.md) for explicit provider installation,
listing, and removal. It exposes those tools instead of inference. The default
[Codex plugin export](agent-integrations.md) starts inference mode and does
not install providers.

