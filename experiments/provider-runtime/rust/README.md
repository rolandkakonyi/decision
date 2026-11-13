# Native Rust provider host experiment

This isolated executable implements the [matched provider contract](../CONTRACT.md)
with native Rust validation, TypeSafe HTTP and multipart mapping, and the official
[rmcp](https://github.com/modelcontextprotocol/rust-sdk) MCP server on Tokio.
External providers are trusted executable commands; their declared runtimes remain
user-supplied. Built-in evaluation and MCP require neither Python nor Go.

## Build and run

From the repository root:

```sh
uv sync --locked
experiments/provider-runtime/rust/build.sh
.state/provider-prototype-rust/decision --version
uv run --locked python experiments/provider-runtime/acceptance.py \
  --binary "$PWD/.state/provider-prototype-rust/decision" \
  --output .state/provider-prototype-rust/acceptance.json
```

The build script runs the nested, locked Cargo release build, copies the native
executable to `.state/provider-prototype-rust/decision`, and collects project and
transitive dependency licenses, a target-specific dependency inventory and native
linkage beside it. Python/uv are build/verification tools, not built-in runtime
requirements. No global Rust tools, settings or targets are installed.

The equivalent compilation command is:

```sh
CARGO_TARGET_DIR="$PWD/.state/provider-prototype-rust/target" \
  cargo build --release --locked \
  --manifest-path experiments/provider-runtime/rust/Cargo.toml
```

The nested `Cargo.lock` fixes dependency versions. `Cargo.toml` declares Rust 1.88
as its compatibility floor; this floor is not itself qualified. Build with the
installed Rust 1.94.1 toolchain, native linker and C/C++ compiler. AWS-LC used its
CC builder on this host; other targets/configurations can require CMake. Release
builds use thin LTO, one codegen unit and symbol stripping. This is a native
executable, without self-extraction or an interpreter launcher.

Reqwest has default features disabled: only JSON, multipart and rustls TLS are
enabled. Redirects and retries are disabled explicitly. Rustls uses AWS-LC,
whose C/assembly implementation is compiled and linked into the executable;
there is no runtime OpenSSL dependency. Certificate trust uses the platform
verifier and native system trust libraries. Consequently this is not a fully
static binary. `dependencies.json` records the exact host-target dependency graph,
including build/proc-macro crates; `linkage.txt` records the dynamic libraries.
`THIRD_PARTY_NOTICES.txt` contains available license/notice texts from that graph,
including bundled AWS-LC sources. The project MIT license is copied separately.

macOS ARM64 is the qualified platform. Linux and macOS are intended POSIX targets,
but Linux, macOS x86-64, cross-compilation and Homebrew bottles have not been
qualified. The process-group and signal implementation is POSIX-specific; Windows
needs a separate process-tree lifecycle implementation. Native compilation and
platform trust reduce portability relative to a pure standard-library Go build;
Rust itself does not require an installed Rust runtime for distribution.

## Runtime behavior

The CLI surfaces, input/output schemas and external protocol match the common
contract. The schema JSON files are static snapshots of the common schema and
must be synchronized when that contract changes. TypeSafe is implemented directly
over HTTP; other production built-ins and production exports are outside this
experiment.

CLI `--model` overrides `DECISION_MODEL`; this CLI default overwrites a model in
the request, matching production CLI behavior. MCP preserves a supplied request
model and uses the launch default only when absent. If TypeSafe still has no
model, its execution fallback is `TYPESAFE_DEFAULT_MODEL`, then `jev-latest`.
That SDK fallback is not added to credential-free preview output.

JSON preserves object insertion order and arbitrary integer precision. A strict
pass rejects duplicate keys before deserialization can overwrite them; floating
nonfinite values and malformed UTF-8 are rejected. Result validation checks raw
answer/usage types before constructing normalized output. MCP uses SDK model
messages, negotiation, dispatch and cancellation; a bounded Tokio line adapter
checks strict JSON before SDK deserialization. Invalid JSON frames close the
connection. Its explicit 64 MiB limit accommodates two base64-encoded 16 MiB assets.
Image batches are buffered and cloned, so maximum-asset calls use substantially
more memory than their binary image sizes.

Default MCP exposes only `evaluate` for the launch-configured provider; explicit
`--management` exposes the three installer tools without inference. A server pins
its selected manifest and immutable artifact path at startup. Install/update stages
and validates a package before optional setup, and atomically replaces only the
selected personal or explicitly supplied project registry. Failed setup leaves
prior registration intact. Removal unregisters without deleting old artifacts.
Registry files are implementation-specific; artifact garbage collection and
cross-process registry locking are absent. Management filesystem/archive operations
are synchronous on the async thread; cancellation is observed at async boundaries.

Plugin/setup/Git commands inherit only `PATH`, `HOME`, `TMPDIR`, `LANG`, `LC_ALL`
and `SYSTEMROOT` when present. The selected credential goes only in evaluation
stdin. No ambient provider credentials or unrelated variables reach subprocesses;
stdout is bounded protocol and stderr is discarded. POSIX process groups are
killed on timeout, inference cancellation, signal shutdown and completion. Direct
children are explicitly waited on; cancellation schedules a tracked waiter that
is drained before CLI exit. Descendants receive the group kill and are reaped by
their parent or operating system. This is trusted software, not a sandbox, and
local cancellation does not establish remote provider cancellation.

## Checks

```sh
CARGO_TARGET_DIR="$PWD/.state/provider-prototype-rust/target" \
  cargo test --locked --manifest-path experiments/provider-runtime/rust/Cargo.toml
CARGO_TARGET_DIR="$PWD/.state/provider-prototype-rust/target" \
  cargo clippy --locked --all-targets \
  --manifest-path experiments/provider-runtime/rust/Cargo.toml -- -D warnings
cargo fmt --manifest-path experiments/provider-runtime/rust/Cargo.toml --check
uv run --locked decision skill --output skills/decision --check
uv run --locked python -m unittest discover -s tests -v
```

Unit tests cover strict JSON, preserved numeric/order semantics, raw result types,
probability keys/totals, image boundaries and endpoint policy. The unchanged shared
acceptance harness additionally exercises native TypeSafe/MCP, external plugins,
maximum asset frames, setup rollback, source pinning and process-group cancellation.
All inference validation uses local fixtures. Comparative timing belongs to the
common collector and is not run by this build or its checks.
