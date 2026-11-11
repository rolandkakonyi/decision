# Decision repository

This private repository owns the decision CLI, embedded STDIO MCP server,
standalone build, tests, and companion-skill generator. Keep provider calls and
validation shared between CLI and MCP. Preserve environment-based provider and
credential-variable overrides. Never commit secrets, environments, or binaries.

The generated `skills/decision/` directory is an export, not an edit location.
Change `src/decision_cli/skill.py` and the command/schema definitions, then run:

```bash
uv sync --locked --group build
uv run --locked decision skill --output skills/decision
uv run --locked decision skill --output skills/decision --check
uv run --locked python -m unittest discover -s tests -v
```

Generation requires no provider credentials or API access. The agent-skills repo
imports this generated skill at a reviewed commit and checks file hashes. Keep
the export portable: use the installed `decision` command and relative references,
never assume this checkout or a sibling skill exists. Do not put tool source or
machine-specific paths into the generated skill.

For a standalone executable, use `uv run --locked --group build python
scripts/build_standalone.py --output .state/dist`, then run the transport tests
with `DECISION_TEST_EXECUTABLE` pointing to that executable. Build on each target
OS and architecture. Test without live inference unless the user authorized
the provider, data, and experimentation budget.
