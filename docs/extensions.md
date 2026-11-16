# Provider plugins and Codex exports

Decision uses two different extension mechanisms. Choose by what you want to
connect.

## Add an inference backend: executable provider

A provider plugin implements an inference backend. It contains a
`decision-provider.json` manifest and a trusted command written in any
language. Decision exchanges versioned JSON messages with that command and
validates the returned answers.

Install the package with `decision providers install`, then select its name
with `--provider` or `DECISION_PROVIDER`. The native binary, including the
Homebrew installation, supports these providers. A plugin may need its own
runtime; its author supplies packaging and setup instructions.

- [Install, update, scope, and remove providers](installed-providers.md).
- [Write a package and implement the protocol](provider-plugins.md).

## Connect Codex: plugin export

A Codex plugin export packages Decision's generated Agent Skill and either
CLI instructions or an inference MCP declaration. Generate it with
`decision plugin`. It references a separately installed Decision executable;
it does not bundle that executable or install provider packages.

Generation is local and deterministic. It does not discover providers, run
setup, or perform inference. For custom MCP environment variables,
`--env-var NAME` forwards variable names, never their values.

- [Generate and install a Codex export](agent-integrations.md).
- [Complete export reference](plugin-reference.md).

## Choose an MCP server mode

An [inference server](mcp.md) exposes `evaluate` for one configured provider
and pins its installed artifact until restart. A
[management server](mcp-management.md) exposes only provider installation,
listing, and removal. The default Codex MCP export starts inference mode.
