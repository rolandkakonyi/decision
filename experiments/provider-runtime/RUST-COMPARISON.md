# Rust and Go native provider hosts

Rust is a viable host for Decision. It passes the same 21 acceptance checks as
Go and has smaller artifacts and a lower idle RSS snapshot in this experiment.
Most latency ranges overlap, so the results do not establish a general speed
advantage for either native host. Homebrew and support for multiple operating
systems do not disqualify Rust.

## Matched behavior and evidence

The [Rust implementation](rust/README.md) uses the official rmcp SDK on Tokio,
Reqwest/rustls, and native Rust request/result validation and TypeSafe mapping.
The [Go implementation](go/README.md) uses the official Go MCP SDK and standard
library HTTP/validation code. Both implement the [same contract](CONTRACT.md),
including CLI/MCP installation, Git/archive/local sources, explicit project scope,
author-owned setup, pinned running-server versions, image limits, raw response
checks and process-group cancellation. Built-in operation requires no installed
language runtime. External plugins use their author-declared command/runtime.

Both built artifacts pass [all 21 shared checks](evidence/native-go-rust/acceptance.json),
including two 16 MiB image assets, official Python MCP client management calls,
setup rollback, disabled-provider no-launch, safe diagnostics and model override
precedence. Rust's six local unit tests, Clippy, formatting and build passed. Go's
race tests, vet and build passed. The generated-skill check and production source
suite passed, with one expected standalone-only skip among 80 source tests. The
production implementation is unchanged; no live provider API was called.

The original [Python/Go measurements](COMPARISON.md) remain tied to their original
artifacts. Their Go artifact lacked the SDK-specific `TYPESAFE_DEFAULT_MODEL`
fallback. The native pair uses the corrected Go implementation and adds a shared
regression for this behavior. CLI flags / `DECISION_MODEL` override request-file
models; explicit MCP request models override server defaults. SDK-only fallback
is applied during execution, leaving credential-free preview unchanged.

## Measurements

This is a fresh Go/Rust batch on the same macOS 27.2 ARM64, 10-CPU host, with
Go 1.27.1 and Rust 1.94.1. Each scenario has two warmups per host followed by
seven strictly alternating Go/Rust pairs. Build and test processes were idle
while measuring; unrelated desktop load was not controlled. No cold-cache claim
is made. The [complete evidence](evidence/native-go-rust/results.json) retains
artifact hashes, individual measurements, output hashes, ranges, warmups and
gate outcomes. All nine scenarios have matching canonical output hashes.

Observed median wall times, Go followed by Rust:

- CLI `--version`: **17.78 ms / 14.69 ms**.
- Credential-free dry run: **18.32 ms / 15.41 ms**.
- MCP initialization and tool discovery: **22.61 ms / 19.21 ms**.
- CLI TypeSafe loopback evaluation: **20.71 ms / 17.61 ms** for text and
  **21.39 ms / 18.52 ms** for the small image/Box batch.
- CLI external-plugin evaluation: **151.83 ms / 144.83 ms**.
- Existing MCP server, TypeSafe loopback evaluation: **1.97 ms / 1.16 ms** for
  text and **1.85 ms / 1.32 ms** for the small image/Box batch.
- Existing MCP server, external-plugin evaluation: **144.09 ms / 138.96 ms**.

Only the small image/Box CLI scenario passes the stringent lower-Rust-latency
claim gate with non-overlapping ranges. The other eight ranges overlap, so their
lower Rust medians are observations, not supported improvement claims. These
are local fixture timings, not model-inference measurements. Do not combine
this batch with earlier Python/Go attempts or compare their medians as one
controlled three-host experiment.

The Go executable is **8.57 MB**, versus **5.87 MB** for Rust. Standardized gzip
archives containing the executable, project license and dependency notices are
**3.39 MB / 2.82 MB**. Sizes use decimal MB. At MCP readiness, median RSS is
**14.4 MiB / 8.2 MiB**, with one process each. This is an idle snapshot, not peak
or unique physical memory; shared pages may be counted. Maximum-image calls
buffer and clone data in both implementations and need separate peak-memory
qualification if that becomes a selection criterion.

## Homebrew and operating systems

Homebrew supports Cargo builds and already distributes Rust programs such as
[ripgrep](https://github.com/Homebrew/homebrew-core/blob/HEAD/Formula/r/ripgrep.rb).
Its Rust compiler dependency is build-only. A tap installing prebuilt Rust
executables does not need to install Rust for users. The existing binary-based
distribution approach can serve either native host.

Rust maintains [targets for macOS, Linux and Windows](https://doc.rust-lang.org/rustc/platform-support.html).
Target support and this project's qualification are separate. Both native
prototypes are qualified only on macOS ARM64; their current process-group code
is POSIX-specific. Linux still needs native build/runtime tests, and Windows
needs process-tree lifecycle code and verification in either implementation.
Homebrew itself is not the Windows distribution channel.

The selected Go build uses `CGO_ENABLED=0`. Rust's selected TLS provider, AWS-LC,
compiles C/assembly into the executable and requires native build tooling. This
host used its CC builder; other configurations can require CMake. Rust
[cross-compilation also needs target tooling such as a linker](https://rust-lang.github.io/rustup/cross-compilation.html).
That adds build/release setup compared with this Go configuration, but does not
make Rust unsuitable for native CI builds on each supported OS.

The Rust binary uses macOS Security/CoreFoundation, libiconv and libSystem;
it has no runtime OpenSSL dependency. The Go binary also uses macOS system
libraries. The Rust build emits target dependency and native-linkage inventories
and includes license texts for its graph, including AWS-LC. Its 162 dependency
entries include build/proc-macro crates; this count is not directly comparable
to Go module counts or linked runtime size.

## Maintenance and scope

Both native hosts own TypeSafe's wire mapping, common validation and static
schema synchronization. Rust adds Cargo/TLS build configuration; Go carries its
own manual contract mapping. The discovered SDK environment fallback is a
concrete example of behavior that needs characterization during either port.
The prototypes do not demonstrate a long-term maintenance winner.

Both retain superseded plugin artifacts and omit registry locking and garbage
collection. Rust management filesystem/archive work is synchronous on an async
thread, with cancellation observed at async boundaries. Inference process-group
cancellation is qualified separately. The remaining production providers,
command shortcuts, generated exports and release integration are outside this
matched slice. Choosing a host does not make these prototypes a complete port.

Build and reproduce using the [Rust guide](rust/README.md), [Go guide](go/README.md)
and language-neutral collector:

```sh
uv run --locked python experiments/provider-runtime/compare.py \
  --baseline-binary /path/to/final-go/decision --baseline-name Go \
  --candidate-binary /path/to/rust/decision --candidate-name Rust \
  --output .state/native-go-rust-new
```

Use a new output directory for each attempt. Canonicalization is the same as the
Python/Go collector: preserve answer/usage semantics and schemas, exclude elapsed
duration and normalize integral answer-number formatting. Canonical samples and
per-scenario manifests accompany the published evidence.
