# Install and quickstart

## Install

```bash
brew install rolandkakonyi/tap/decision
```

Homebrew supports macOS ARM64 and Linux x86_64. The executable includes all
four built-in providers and supports executable
provider plugins. It needs no Python or uv runtime. Upgrade with
`brew upgrade rolandkakonyi/tap/decision`.

Native archives for macOS ARM64, Linux x86_64, and Windows x86_64 are available
from [GitHub releases](https://github.com/rolandkakonyi/decision/releases).
Extract the archive, verify its SHA-256 checksum, and place `decision`
(`decision.exe` on Windows) on PATH. Keep the accompanying license notices.
See [the build guide](standalone.md) for platform constraints.

With Rust installed, build and install from a checkout:

```bash
cargo install --locked --path .
```

## Verify a release archive

Download the archive for your platform and `SHA256SUMS` from the same
[release](https://github.com/rolandkakonyi/decision/releases). Compare the
archive's SHA-256 value with its entry in `SHA256SUMS` before extracting it.

=== "macOS"

    ```bash
    shasum -a 256 decision-VERSION-aarch64-apple-darwin.tar.gz
    ```

=== "Linux"

    ```bash
    sha256sum decision-VERSION-x86_64-unknown-linux-gnu.tar.gz
    ```

=== "Windows PowerShell"

    ```powershell
    Get-FileHash .\decision-VERSION-x86_64-pc-windows-msvc.zip -Algorithm SHA256
    ```

Replace `VERSION` with the downloaded release version. Keep the extracted
license notices alongside your installation. Upgrade a Homebrew installation
with `brew upgrade rolandkakonyi/tap/decision`; for archives, download and verify
the new release.


## Quickstart

Validate a request without credentials or inference:

```bash
decision noul 'Does this request a refund?' --state 'Please refund my order.' --dry-run
```

TypeSafe is the default provider. For this walkthrough, export `OPENAI_API_KEY`
into your environment and select OpenAI for the current shell:

```bash
# Requires OPENAI_API_KEY in the environment.
export DECISION_PROVIDER=openai
decision noul 'Does this request a refund?' --state 'Please refund my order.'
```

No model flag is required: each built-in provider has a default model.
[Provider configuration](providers.md) lists credentials and defaults.
Decision does not load `.env` files automatically. Inference is billed by the
selected provider.
`--provider NAME` overrides the shell setting for a single command.

Human-readable output is the default, including when piped. Add `--json` before
or after a query command for structured output. Diagnostics go to stderr.
Use `decision --help` or `decision COMMAND --help` for available options.

Executable providers are available with the same native installation. Use
[`decision providers install`](installed-providers.md) to add a trusted
package; no Python package installation is required. Distinguish these
providers from [Codex plugin exports](extensions.md).

