# Native builds and distribution

Decision is a Rust executable with built-in TypeSafe, OpenAI Decisions,
Cloudflare Clef/Clef Flash, and Gemini Decisions transports. CLI, STDIO MCP,
exports, and executable provider plugins use the same binary. End users need
no Python, uv, or first-launch dependency download. Interpreted provider plugins
may require runtimes supplied by the user.

## Build and test

Install the repository Rust toolchain, then run:

```bash
cargo build --locked --release
cargo test --locked
```

The binary is `target/release/decision` (`decision.exe` on Windows). Native
builds target the host OS and architecture. The lockfile pins dependency
versions; builds are not claimed to be bit-for-bit reproducible across hosts.

Python and uv are used only for black-box testing and archive assembly:

```bash
uv sync --locked
uv run --locked python scripts/build_standalone.py --output .state/dist
```

The build helper produces the executable, application/dependency notices,
platform archive, and SHA-256 checksums. Distribute matching notices/checksums
with the executable. No credentials are embedded. Missing dependency license
material fails packaging.

## Verify the executable

Use the same local provider fixtures and MCP clients as development:

```bash
DECISION_TEST_EXECUTABLE="$PWD/target/release/decision" \
  uv run --locked python -m unittest discover -s tests -v
```

In PowerShell:

```powershell
$env:DECISION_TEST_EXECUTABLE = "$PWD/target/release/decision.exe"
uv run --locked python -m unittest discover -s tests -v
```

The fixtures exercise all providers, one evaluation per batch, PNG/JPEG
transport, question limits, answer validation, credential host scope,
model/environment precedence, human/JSON output, exports, and error recovery.
Runtime-provider checks cover local/Git/archive installation, setup failures,
CLI/MCP evaluation, explicit management mode, updates, pinned servers, timeout,
and cancellation. They use synthetic keys and no paid inference.

The dedicated Codex job installs the Codex CLI and runs
`test_codex_plugin.py` with `DECISION_TEST_CODEX=1`. Ordinary fixture runs skip
that external-client integration explicitly; enabling it requires the client
and treats a missing installation as a failure.

## Supported release targets

CI builds and tests macOS ARM64, Linux x86_64, and Windows x86_64 on native
GitHub Actions runners. Each job validates the compiled executable and archive;
Windows process cleanup is exercised on Windows. Other architectures need
separate native qualification.

Linux executables use the runner's glibc baseline and are not fully static.
Rustls supplies TLS without a system OpenSSL dependency. macOS/Windows archives
are unsigned: platform signing/notarization requires a separate release setup.

Successful `main` CI publishes a GitHub release for an unreleased Cargo version,
including native archives and checksums. Feature-branch CI never publishes.
Existing tags are immutable. Homebrew formula updates are managed separately
in the tap repository.

The binary also generates portable skills and local Codex marketplaces:

```bash
decision skill --output ./decision-skill
decision plugin --output ./decision-plugin --integration mcp \
  --mcp-command /absolute/path/to/decision
```

Exports reference an installed executable; they do not copy it. Register the
marketplace explicitly as described in the generated
[plugin guide](../skills/decision/references/plugin.md).
