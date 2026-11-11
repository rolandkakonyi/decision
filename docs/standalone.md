# Standalone decision

The same program implements terminal commands and `decision mcp`. Build it with
PyInstaller's one-file mode to bundle Python, the pinned TypeSafe and MCP SDKs,
and native dependencies. End users need no Python, uv, or package download.
The executable still needs access to its configured provider and credentials.

## Build

Requires `uv` and Python 3.11+ on the build host. The project pins `typesafe-sdk==0.7.5`, `mcp==2.3.0`, and
`pyinstaller==6.22.3`, with transitive versions in `uv.lock`. The helper
analyzes imports and produces `decision` (or
`decision.exe` on Windows) plus `SHA256SUMS` in the chosen output directory.
It overwrites the same-named executable on subsequent builds. No credentials
are embedded; credentials are resolved from the user's environment at runtime.

From the repository root:

```bash
uv run --locked --group build python scripts/build_standalone.py --output .state/decision-dist
.state/decision-dist/decision --version
.state/decision-dist/decision run examples/request.json --dry-run
.state/decision-dist/decision mcp
```

The last command waits for an MCP client on stdin. Register that executable with
`codex mcp add decision -- /absolute/path/to/decision mcp`.

## Verify the compiled artifact

Use the same local-provider and MCP integration tests as the source version:

```bash
DECISION_TEST_EXECUTABLE="$PWD/.state/decision-dist/decision" \
  uv run --locked python -m unittest discover -s tests -p test_decision_mcp.py -v
```

These exercise initialization, tool discovery, input/output schemas, batches of
Noul/Choice/Score questions in one provider call, custom provider credentials,
and recovery from errors without inference. The test runner needs Python and
the SDKs; the tested executable does not. Check a copied executable from an
unrelated directory without Python or uv on PATH as a startup smoke test.

## Platform and release constraints

- PyInstaller builds for the host OS and architecture, not a universal binary.
  Build and test Linux x86_64/arm64, macOS arm64/x86_64, and Windows separately
  for whichever targets will be supported. Only Linux x86_64 is verified here.
- Linux bundles use the build host's glibc baseline. Build official Linux
  releases on the oldest supported distro; a build on a newer distro may not
  run on an older one. This is not a fully static executable.
- One-file bundles extract runtime files to a temporary directory. The host
  must allow extraction and execution there; set `TMPDIR` when needed. Startup
  includes extraction time, so a long-lived MCP process amortizes that cost.
- Build in a clean environment; test the artifact, record its SHA-256 hash, and
  distribute the matching checksum. Dependencies are locked, but builds are not claimed to be
  bit-for-bit reproducible across build hosts.
- Public macOS distribution should be signed and notarized. Consider Windows
  code signing for public releases. No signing keys or release publishing are
  configured by this helper.

For now PyInstaller keeps one implementation and proves the single-executable
path. A zipapp/PEX still needs a Python runtime; uv script distribution needs
uv and first-run dependency access. A Go or Rust rewrite could produce smaller
native artifacts and easier cross-compilation, but would replace the existing
Python SDK integration and require another implementation to maintain.
