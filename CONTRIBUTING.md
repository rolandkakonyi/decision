# Contributing

Open an issue for bugs or feature proposals. Include the decision version,
provider, operating system, a minimal synthetic request, and expected/actual
behavior. Remove credentials and private context. Report vulnerabilities
privately as described in [SECURITY.md](SECURITY.md).

Fork the repository, create a branch, and open a pull request explaining the
problem, resulting behavior, and relevant validation. Discuss large changes
in an issue first. Contributions use the project's [MIT license](LICENSE).

## Development

Install Rust using the repository toolchain, Python 3.11+, and uv. Python is
used by the integration tests; users of the executable do not need it.

```bash
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo build --locked
uv sync --locked
target/debug/decision skill --output skills/decision --check
uv run --locked python -m unittest discover -s tests -v
```

On Windows, use `target/debug/decision.exe`. Tests use local fixtures without
API keys or paid inference. The real Codex plugin installation test requires
the Codex CLI; CI has a dedicated job for it.

Keep CLI and MCP validation consistent, preserve environment-based provider
configuration, and keep credentials and provider bodies out of diagnostics.
See [AGENTS.md](AGENTS.md) for module boundaries. Provider programs implement
the [executable protocol](docs/provider-plugins.md).

The companion skill is generated from `src/skill.rs`, `src/templates/`, and
command/schema definitions. Regenerate with:

```bash
target/debug/decision skill --output skills/decision
```

For packaging changes, run the native archive checks in
[docs/standalone.md](docs/standalone.md). Never commit credentials, environment
files, build outputs, or executables.

## Releases

`Cargo.toml` owns the release version. Keep `Cargo.lock` and the generated
skill in sync. After successful CI on `main`, an unreleased version is
published as a GitHub release tagged `vVERSION` with native archives, notices,
and checksums. Further pushes publish only when the version changes. Release
tags are never moved or reused.
