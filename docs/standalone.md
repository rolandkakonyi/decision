# Standalone decision

The same program implements terminal commands and `decision mcp`. Build it with
PyInstaller's one-file mode to bundle Python, the pinned TypeSafe, OpenAI, and MCP SDKs,
and native dependencies. End users need no Python, uv, or package download.
The executable still needs access to its configured provider and credentials.
It supports the bundled TypeSafe, OpenAI, Cloudflare, and Gemini providers.
Third-party [provider extensions](provider-plugins.md) require a Python
installation; installing a package beside the executable does not extend its
frozen runtime.

## Build

Requires `uv` and Python 3.11+ on the build host. The project pins `typesafe-sdk==0.7.5`, `mcp==2.3.0`, and
`openai==3.49.0`, and `pyinstaller==6.22.3`, with transitive versions in `uv.lock`. The helper
analyzes imports and produces `decision` (or
`decision.exe` on Windows), `THIRD_PARTY_NOTICES.txt`, and `SHA256SUMS` in the chosen output directory.
It overwrites the same-named executable on subsequent builds. No credentials
are embedded; credentials are resolved from the user's environment at runtime.
License notices for the application, Python runtime, PyInstaller, and recursive
runtime dependencies are embedded in the executable and supplied as a readable
sidecar. Missing package notices fail the build. Distribute the notices file
and matching checksums with the executable.

From the repository root:

```bash
uv run --locked --group build python scripts/build_standalone.py --output .state/decision-dist
.state/decision-dist/decision --version
.state/decision-dist/decision run examples/request.json --dry-run
.state/decision-dist/decision mcp
```

The last command waits for an MCP client on stdin. Register that executable with
`codex mcp add decision -- /absolute/path/to/decision mcp`.

The same executable generates skills and Codex plugins without Python:

```bash
/absolute/path/to/decision plugin --output /private/decision-plugin --integration mcp \
  --mcp-command /absolute/path/to/decision
/absolute/path/to/decision plugin --output /private/decision-cli-plugin --integration cli
```

MCP exports reference that executable; they do not copy it into the plugin.
CLI exports expect `decision` on PATH. Install the generated local marketplace
explicitly as described in [the plugin guide](../skills/decision/references/plugin.md).

Repeated builds can reuse PyInstaller's processed-library cache:

```bash
uv run --locked --group build python scripts/build_standalone.py \
  --output .state/decision-dist --cache-dir .state/pyinstaller-cache
```

Each build still creates fresh analysis, work, and spec files. Without
`--cache-dir`, the helper uses a fresh temporary cache and `--clean`.
CI caches uv downloads and managed Python installations separately by job,
platform, Python version, and dependency files. It also caches processed native
libraries by OS, architecture, Python version, dependencies, and build helper.
Executables and analysis results are rebuilt rather than restored from cache.

## Verify the compiled artifact

Use the same local-provider and MCP integration tests as the source version:

```bash
DECISION_TEST_EXECUTABLE="$PWD/.state/decision-dist/decision" \
  uv run --locked python -m unittest discover -s tests -p 'test_*mcp.py' -v
```

These exercise TypeSafe, OpenAI, Cloudflare, and Gemini transports, initialization, tool discovery, input/output schemas, batches of
typed questions in one provider call, PNG multipart uploads for TypeSafe and inline images for OpenAI through CLI and MCP,
human/JSON output with token usage and duration, custom provider credentials, and recovery from errors without
inference. They also verify both plugin exports and a real MCP exchange launched
from the generated configuration. The test runner needs Python and
the SDKs; the tested executable does not. Check a copied executable from an
unrelated directory without Python or uv on PATH as a startup smoke test.

## Platform and release constraints

- PyInstaller builds for the host OS and architecture, not a universal binary.
  Build and test Linux x86_64/arm64, macOS arm64/x86_64, and Windows separately
  for whichever targets will be supported. CI builds and tests the native
  `ubuntu-latest` and `macos-latest` runner targets with Python 3.14. Other
  architectures and Windows require their own native verification.
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
