# Decision repository

Decision owns a native Rust CLI, embedded STDIO MCP server, four built-in
provider transports, executable provider plugins, and companion-skill/Codex
plugin exports. Preserve environment-based provider/model/credential overrides.
Never commit secrets, environments, or binaries. Use feature branches and draft
PRs. CI releases an unreleased Cargo version only after checks pass on `main`;
do not move or reuse version tags.

## Module contracts

- `src/cli.rs`, `src/main.rs`: command parsing, input construction, shared
  dispatch. Human output is default; `--json` works before/after commands.
- `src/provider.rs`, `src/providers/`: provider configuration, credential host
  scope, TypeSafe/OpenAI/Clef/Gemini transports. One evaluation per batch; no
  retries/redirects. Provider bodies and credentials stay out of diagnostics.
- `src/validation.rs`, `src/json.rs`: common request/result validation, strict
  JSON, PNG/JPEG/reference/byte limits. CLI permits paths; MCP permits base64
  only. Reject invalid answers for the whole batch. Preserve zero versus
  unknown usage and the 0.02 probability-total tolerance.
- `src/mcp.rs`: one configured provider and pinned artifact per inference
  server. Callers cannot redirect provider/base URL/account/key selector.
  Explicit management mode exposes only provider install/list/remove.
- `src/store.rs`, `src/process.rs`: trusted executable provider installation,
  versioned JSON protocol, staged activation, explicit project config, bounded
  output, timeout/cancellation and process-tree cleanup. Setup only runs during
  explicit installation/update. Disabled providers never launch.
- `src/output.rs`: readable answers, safe terminal strings, batch usage and
  client-measured duration. Diagnostics use stderr; MCP stdout is protocol only.
- `src/skill.rs`, `src/plugin.rs`, `src/templates/`: deterministic exports.
  Generation requires no credentials/provider discovery/network/installation.
  Forward variable names, never values. CLI plugins declare no MCP server;
  mode switching removes owned stale config. Preserve unmanaged files and reject
  symlinks. Content changes alter cache version.
- `scripts/build_standalone.py`: native archives, dependency/license notices,
  checksums. Build on every supported OS/architecture; never commit artifacts.
- `tests/`: native black-box CLI/MCP/provider/export/installation fixtures.
  Python is test tooling only. No paid inference in tests.

## Validation

Use the repository Rust toolchain and locked dependencies:

```bash
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo build --locked
uv sync --locked
target/debug/decision skill --output skills/decision --check
uv run --locked python -m unittest discover -s tests -v
```

On Windows, the executable is `target/debug/decision.exe`. Set
`DECISION_TEST_EXECUTABLE` to test another build. Use a narrow unittest filename
or Rust test filter while iterating; run the full checks before finishing.
Tests must exercise behavior, preserve assertions, and use only local fixtures.
Live inference requires separate provider/data/budget authorization.

Packaging changes also require the archive verification in
[docs/standalone.md](docs/standalone.md). CI runs native macOS/Linux/Windows and
a dedicated real Codex plugin installation job. Local success does not establish
another operating system works; inspect the corresponding CI result.

## Generated skill

`skills/decision/` is an export. Change `src/skill.rs`, templates, or the
command/schema definitions, then regenerate:

```bash
target/debug/decision skill --output skills/decision
```

Keep the export portable: installed `decision` command, relative references,
no checkout paths or tool source. Do not edit generated files directly.
