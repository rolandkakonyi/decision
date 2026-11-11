# Decision repository

This private repository owns the decision CLI, embedded STDIO MCP server,
standalone build, tests, and companion-skill generator. Preserve environment-based
provider and credential-variable overrides. Never commit secrets, environments,
or binaries. Use direct commits to `main`; create tags manually only when asked.
CI verifies commits and does not publish releases or create tags.

## Module and contract map

| Area | Owner | Contract |
| --- | --- | --- |
| Commands, request validation, provider routing, MCP | `src/decision_cli/cli.py` | CLI and MCP share validation and evaluation. One batch sends one provider request; answer IDs and types must match. No automatic retries. Credentials and provider bodies stay out of diagnostics. MCP tool callers cannot redirect the provider or credential selector. |
| Images and assets | `src/decision_cli/images.py` | CLI accepts paths and base64; MCP accepts base64 only. Preserve reference checks, byte limits, PNG/JPEG checks, and image opt-in. |
| Terminal output | `src/decision_cli/output.py` | Human-readable query output by default; `--json` forces JSON. Every successful result preserves provider token usage and adds client-measured `duration_ms` for the batch. Unknown counts stay null, zero counts stay zero. Diagnostics use stderr. MCP stdout is protocol only. |
| Skill export | `src/decision_cli/skill.py` | Generated command/schema references reflect the running tool. Generation needs no credentials or provider access. |
| Wheel verification | `scripts/verify_wheel.py` | Install the built wheel in a fresh venv outside the checkout; test installed CLI, skill, image uploads, and MCP against a local provider fixture. |
| Standalone build | `scripts/build_standalone.py` | Build for the host OS/architecture, bundle SDKs, produce a checksum. Optional cache reuses processed libraries; analysis and spec files start fresh. |
| Tests | `tests/test_decision.py`, `tests/test_images.py`, `tests/test_decision_mcp.py`, `tests/test_examples.py` | Request/output contracts; image validation; CLI/MCP provider fixture; documented command examples. No paid inference. |

## Validation

Start with `uv sync --locked`. Use the narrow check for the area being changed:

| Change | Check |
| --- | --- |
| Requests, flags, provider settings, output | `uv run --locked python -m unittest discover -s tests -p test_decision.py -v` |
| Image validation | `uv run --locked python -m unittest discover -s tests -p test_images.py -v` |
| Provider transport or MCP | `uv run --locked python -m unittest discover -s tests -p test_decision_mcp.py -v` |
| Skill examples | `uv run --locked python -m unittest discover -s tests -p test_examples.py -v` |
| Packaging, dependencies, entry points | `uv run --locked python scripts/verify_wheel.py` |
| Standalone/runtime loading | Build and run the compiled transport tests below. |

Before finishing, run the generated-skill check and full source suite:

```bash
uv run --locked decision skill --output skills/decision --check
uv run --locked python -m unittest discover -s tests -v
```

For packaging/runtime changes and before distributing a standalone executable:

```bash
uv sync --locked --group build
uv run --locked --group build python scripts/build_standalone.py --output .state/dist
.state/dist/decision skill --output skills/decision --check
DECISION_TEST_EXECUTABLE="$PWD/.state/dist/decision" \
  uv run --locked python -m unittest discover -s tests -p test_decision_mcp.py -v
```

CI runs source checks on Python 3.11/3.14 and macOS, a clean Linux wheel install,
and native Linux/macOS standalone checks. See `.github/workflows/ci.yml` and
`docs/standalone.md`. Build on each target OS/architecture. Test without live
inference unless the user authorized the provider, data, and experimentation budget.

## Generated skill

The generated `skills/decision/` directory is an export, not an edit location.
Change `src/decision_cli/skill.py` and the command/schema definitions, then run
`uv run --locked decision skill --output skills/decision` and the checks above.

Generation requires no provider credentials or API access. The agent-skills repo
imports this generated skill at a reviewed commit and checks file hashes. Keep
the export portable: use the installed `decision` command and relative references,
never assume this checkout or a sibling skill exists. Do not put tool source or
machine-specific paths into the generated skill.
