# Frozen Python provider host

This experimental executable implements the shared [provider-runtime contract](../CONTRACT.md).
Production commands and exports use their existing entrypoints.

Run from the repository root:

```bash
uv sync --locked --group build
uv run --locked python experiments/provider-runtime/python/host.py --version
uv run --locked python -m unittest discover -s experiments/provider-runtime/python -p 'test_*.py' -v
uv run --locked --group build python experiments/provider-runtime/python/build.py
DECISION_PROTOTYPE_EXECUTABLE="$PWD/.state/provider-prototype-python/decision" \
  uv run --locked python -m unittest discover -s experiments/provider-runtime/python -p test_runtime.py -v
uv run --locked python experiments/provider-runtime/acceptance.py \
  --binary "$PWD/.state/provider-prototype-python/decision" \
  --output .state/provider-prototype-python/acceptance.json
```

The output directory defaults to `.state/provider-prototype-python/`. The
single-file `decision` executable contains CPython, the TypeSafe SDK, the official
MCP SDK, their runtime dependencies, the reused common request/image/result
validators and the experimental host. It runs without a host Python installation.
External providers still require their declared executable/runtime; Git installation
requires `git` on the user's PATH. No interpreter or package manager is installed
for plugin authors. Setup is the author's explicit installation command.

`build.py` selects `typesafe-sdk==0.7.5`, `mcp==2.3.0`, and the locked transitive
`jsonschema==4.26.0` dependency as runtime roots. PyInstaller is pinned to
`6.22.3` in the repository build group. All other dependency versions come from
`uv.lock`; no root manifest changes are needed. The build explicitly excludes
OpenAI, the other provider adapters, production plugin/skill generation, and the
production entry-point registry. It fails if the bundled Python archive contains
OpenAI modules. The official MCP SDK imports HTTP server/authentication modules
even for STDIO, so this is not a manually minimized dependency graph.

Each build emits `DEPENDENCIES.txt` with resolved runtime roots/transitive
packages and the PyInstaller bootloader version, `BUNDLED_MODULES.txt` with the
actual Python archive module inventory, `THIRD_PARTY_NOTICES.txt`, and
`SHA256SUMS`. The notices include this project's MIT license, CPython's license,
and license/notice files from every selected runtime distribution and
PyInstaller. A copy is embedded in the executable and checked after building.
The distribution inventory describes selected distributions; the module inventory
shows what PyInstaller actually collected. Native extension modules and bundled
system libraries also reside in the single-file archive.

## Evaluation

CLI and inference MCP use the same async preparation/evaluation path. Common
request and image validation precedes provider evaluation. Image paths are resolved
relative to the CLI request file and normalized to base64 before crossing the
external process boundary. MCP accepts base64 only. Provider-specific schema
constraints come from the selected manifest and are enforced before preparation.

TypeSafe uses the official async SDK with no retry, redirect or environment proxy
inheritance. An explicit SDK response subclass validates raw JSON, including
duplicate keys and numeric types, before SDK normalization. Common result checks
run again on the normalized output. External providers pass through the same raw
and normalized result checks. The host owns credential selection and elapsed time;
plugin-supplied duration is discarded during normalization.

Each external `describe`, `prepare`, or `evaluate` starts a separate process. Input
and output frames are limited to 48 MiB. Standard output must contain exactly one
newline-terminated JSON response and then EOF. Standard error is discarded. The
child receives only `PATH`, `HOME`, `TMPDIR`, `TMP`, `TEMP`, `LANG`, `LC_ALL`, and
`SYSTEMROOT` when present. Evaluation receives the selected credential in stdin;
no ambient provider credentials are inherited. These are trusted executable
packages, with normal user filesystem access, rather than sandboxed code.
Timeout and inference MCP cancellation kill the POSIX process group and reap the
immediate child. Cancellation cannot promise remote request cancellation.

## Installation and scope

The default personal root is `~/.local/share/decision-prototype/providers`.
`--plugin-home` changes it. Its `registry.json` contains a `providers` object
mapping names to absolute artifact paths, manifest versions and optional exact Git
commits. `--project-config` explicitly selects another file with that same
`providers` object. Only registrations in that file override personal ones; no
working-directory search occurs. Install/remove writes the selected project scope
when supplied. Removing a missing registration in that scope is an input error;
it does not remove an inherited personal registration.

Install copies local sources, verifies SHA-256 before extracting an archive, or
checks out a Git ref and records its exact commit. Package metadata and source
symlinks are checked before setup. Archive traversal, symlinks, hardlinks and
special files are rejected. Compressed archives are capped at 128 MiB and declared
extracted file sizes at 256 MiB. Git and setup commands have a 30-second timeout;
archive HTTP operations use a 30-second socket timeout. All install command
output is suppressed. These limits are prototype policy, not manifest fields.

Validated source moves to a unique artifact path before setup so virtual environment
shebangs and other absolute paths remain valid. Setup may create its own dependency
files; it cannot change the provider manifest. A registration is atomically replaced
only after successful setup. Running inference hosts retain the selected artifact
and parsed manifest until restart. Reinstall and removal leave old files available.
Evaluation never fetches, installs, sets up or loads other providers.

## Limits and maintenance

Only the matched TypeSafe and external-provider slice is supported. There are no
production skill/plugin export commands, other built-ins, Windows support, release
integration or live-inference qualification. The artifact must be built and tested
on each target OS and architecture; the local build targets macOS arm64.

The SDK response hook overrides `_decode`, which is a private SDK extension point.
It is isolated in `host.py` and tied to the pinned SDK; upgrades require the raw
validation and transport regressions. Common helpers are imported directly from
this repository, so this experiment must be updated when their contracts change.
The dependency/module inventories make PyInstaller collection drift reviewable.

The store intentionally has no artifact garbage collector, registry locking,
signature verification or runtime sandbox. Old, failed-setup and superseded
artifacts can accumulate. Concurrent registry writes can overwrite registrations;
the prototype assumes one management writer per scope. Management installation
runs in a worker thread: cancelling its MCP request does not stop that installation,
which can finish until its command timeout. Inference subprocess cancellation is
handled independently and tested. These policies need a product decision before
production adoption.
