# Contributing

Open an issue for bugs or feature proposals. Include the decision version,
provider, operating system, a minimal request, and the expected and actual
behavior. Use synthetic data and remove credentials and private context.
Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

For a code change, fork the repository, create a branch, and open a pull request
explaining the problem, resulting behavior, and relevant validation. Discuss
large changes in an issue before implementing them. Contributions are licensed
under the project's [MIT license](LICENSE).

## Development setup

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/),
then run from your checkout:

```bash
uv sync --locked
uv run --locked decision skill --output skills/decision --check
uv run --locked python -m unittest discover -s tests -v
```

Tests use local provider fixtures and require no API keys or paid inference.
The native Codex plugin test runs when the Codex CLI is installed; CI has a
dedicated job for it. Do not add live provider calls to the test suite.

Keep CLI and MCP validation consistent, preserve environment-based provider
configuration, and keep credentials and provider response bodies out of errors.
See [AGENTS.md](AGENTS.md) for module boundaries and focused checks.

The companion skill is generated. Change `src/decision_cli/skill.py` or the
command/schema definitions, then regenerate it:

```bash
uv run --locked decision skill --output skills/decision
```

For packaging changes, also run `uv run --locked python scripts/verify_wheel.py`.
For standalone changes, use the build and compiled transport checks in
[docs/standalone.md](docs/standalone.md). Never commit environment files,
credentials, build outputs, or executables.

## Releases

The version in `pyproject.toml` is the release version. Maintainers keep
`src/decision_cli/cli.py`'s `VERSION`, `uv.lock`, and the generated skill in sync
with it. After successful CI on `main`,
an unreleased package version is published as a GitHub release tagged `vVERSION`
with a wheel and source distribution. Once that version is released, subsequent
pushes do not publish again until the version changes. Release tags are not
moved or reused for another version of the code.
