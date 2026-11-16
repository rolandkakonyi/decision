![decision: typed judgments via CLI and MCP on text and images](docs/assets/decision-banner.png)

# Decision

[![GitHub Actions CI](https://github.com/rolandkakonyi/decision/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/rolandkakonyi/decision/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/rolandkakonyi/decision)](https://github.com/rolandkakonyi/decision/releases/latest)

Ask typed questions about text, JSON, and images from your terminal or AI agent.
Decision connects to TypeSafe Jev, OpenAI Decisions, Gemini Decisions, and
Cloudflare Clef through one request format and a common answer format.
Route support messages, score urgency, or locate objects using constrained
answers that scripts can validate.

- **Noul (yes/no probability):** estimate the probability that a statement is true.
- **Choice:** select from named options, with probabilities for each.
- **Score:** rate evidence against ordered levels.
- **Box:** locate a region in an image (TypeSafe only).

Batch questions over shared evidence in one provider request. Use readable CLI
output, JSON for scripts, or the embedded STDIO MCP server. Extend the available
providers with executable plugins written in any language.

[Documentation](https://rolandkakonyi.github.io/decision/) ·
[Quickstart](docs/quickstart.md) ·
[Provider protocol](docs/provider-plugins.md)

## Install

```bash
brew install rolandkakonyi/tap/decision
```

Homebrew supports macOS ARM64 and Linux x86_64. Native archives for those
platforms and Windows x86_64 are available from
[GitHub releases](https://github.com/rolandkakonyi/decision/releases). The
executable needs no Python or uv runtime. See the
[installation guide](docs/quickstart.md) for archives, upgrades, and source builds.

## Try it without credentials

```bash
decision noul 'Does this request a refund?' --state 'Please refund my order.' --dry-run
```

Dry runs validate locally without inference. For live queries, configure your
[provider's credentials](docs/providers.md) and omit `--dry-run`. Inference is
billed by the provider. Human-readable output is the default; use `--json` for
scripts. Mixed questions share one evaluation request and keep their IDs.

## Explore

- [Question types and answers](docs/concepts.md), [CLI and batches](docs/cli.md),
  and [image inputs](docs/images.md).
- [Inference MCP](docs/mcp.md) for judgments and
  [management MCP](docs/mcp-management.md) for explicit provider installation.
- [Install executable providers](docs/installed-providers.md) written in any
  language, or [write a provider](docs/provider-plugins.md).
- [Export a Codex plugin or Agent Skill](docs/agent-integrations.md) for an
  installed Decision executable. [Provider plugins and Codex exports](docs/extensions.md)
  serve different purposes.

## Development and license

Rust owns the executable and provider transports. Python and uv support tests,
packaging, and the documentation site. See [CONTRIBUTING.md](CONTRIBUTING.md)
for setup and validation, [native builds](docs/standalone.md) for distribution,
and [SECURITY.md](SECURITY.md) for private vulnerability reporting.

Decision is [MIT licensed](LICENSE). Provider accounts and inference usage are
subject to each provider's separate terms and pricing.
