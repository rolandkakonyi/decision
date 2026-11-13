# Python and Go provider hosts

The matched prototypes support the same provider workflow and pass all 20 shared
acceptance tests. Go has substantially lower standalone startup cost and smaller
artifacts. Persistent external-plugin calls have equivalent latency in this
experiment. The evidence supports Go as a native CLI/Homebrew host; plugin
flexibility itself works with either language.

The [native Rust/Go comparison](RUST-COMPARISON.md) evaluates the third host
and records the corrected Go model-environment behavior. The timings below
remain snapshots of the original Python and Go artifacts.

These are TypeSafe/external-provider experiments, not complete replacement
implementations. The production CLI, other built-in providers, generated exports,
and release configuration retain their existing behavior.

## Comparable behavior

Both hosts implement Noul, Choice, Score and Box, PNG/JPEG inputs, common raw result
validation, environment overrides, and the same language-neutral process protocol.
The shared tests cover CLI and MCP evaluation, official MCP client management
installation, local/Git/archive sources, checksums, setup rollback, explicit
project configuration, disabled-provider no-launch, safe errors, and timeout/MCP
cancellation of plugin process groups. Two 16 MiB images traverse MCP and external
prepare/evaluate successfully. Running inference servers retain their selected
artifact after installation, update and removal until restart.

[Acceptance evidence](evidence/acceptance.json) records 20 passing tests per
standalone. The production generated-skill check passed; the source suite ran
80 tests with 79 passing and one standalone-only skip. Each language's build guide
also documents its local checks: [Python](python/README.md), [Go](go/README.md).

## Measurements

Measurements use macOS 27.2 ARM64 on the same 10-CPU host, CPython 3.13.16 and
Go 1.27.1. Each scenario has two warmups per variant, then seven strictly
alternating Python/Go pairs. Artifact hashes and canonical output hashes are
retained. No prototype builds or test suites ran during timing; other desktop
workloads were not controlled. First observations are separate and do not
establish cold-cache performance.

Median wall times, Python followed by Go:

- CLI `--version`: **537 ms / 18 ms**.
- Credential-free dry run: **561 ms / 21 ms**.
- MCP initialize and tool discovery: **1,108 ms / 19 ms**.
- CLI TypeSafe loopback evaluation: **769 ms / 24 ms** for text and
  **760 ms / 22 ms** for the small image/Box batch.
- CLI external-plugin evaluation: **957 ms / 137 ms**.
- Existing MCP server, TypeSafe loopback evaluation: **2.69 ms / 0.59 ms** for
  text and **3.34 ms / 1.15 ms** for the small image/Box batch.
- Existing MCP server, external-plugin evaluation: **85.10 ms / 85.13 ms**.
  The ranges overlap and there is no supported performance-improvement claim.

All nine scenarios have matching canonical outputs. Eight pass the evidence gate
for lower Go wall time, including non-overlapping sample ranges. The persistent
external-plugin scenario explicitly fails that gate. The fixture plugin uses the
same existing Python interpreter under both hosts; its process startup dominates
that scenario. These loopback measurements do not measure model inference or
predict end-to-end remote API latency.

The frozen Python executable is **21.58 MB**, versus **8.57 MB** for Go. Compressed
archives containing the executable, project license and dependency notices are
**21.44 MB / 3.39 MB**. Sizes use decimal MB. Both distributions include the same
matched feature slice; the Python build excludes the other production providers.

At MCP readiness, the median process-tree RSS snapshot sum is **80.5 MiB** for
Python across two processes and **14.4 MiB** for Go in one process. This is an idle
snapshot, not peak memory or unique physical memory; shared pages can be counted
more than once. Both hosts buffer image batches, so this does not establish
maximum-size request memory consumption.

[Startup/CLI evidence](evidence/startup/results.json) and
[persistent-MCP evidence](evidence/persistent-mcp/results.json) are separate batches;
their attempts are not pooled. Each contains individual values, warmups, artifact
hashes, output hashes, ranges and gate outcomes. Per-scenario manifests and
canonical samples sit beside them. Canonical hashes use compact sorted JSON,
exclude host-owned elapsed duration, and normalize integral answer-number
formatting; token-count types remain unchanged. MCP readiness compares negotiated
protocol and complete tool names/input/output schemas. Informational metadata
samples are retained separately. Per-trial raw outputs remain in the local batch.

## Maintenance tradeoffs

Python reuses existing request/image/result helpers and the TypeSafe/MCP SDKs.
Its experimental runtime consists of 555 lines across host, store and wire modules,
excluding reused helpers, dependencies, tests and build code. Strict pre-coercion
TypeSafe response validation currently overrides the SDK's private `_decode`
method. That hook is isolated, pinned and tested, but remains an upgrade risk.
PyInstaller requires native target builds and extracts the one-file runtime at
startup.

Go has 1,499 lines across the equivalent runtime modules, excluding schemas,
dependencies, tests and build code. It owns common validation and TypeSafe's HTTP
and multipart mapping directly, plus static copies of the common schemas.
Provider/schema changes therefore require deliberate synchronization. The official
Go MCP SDK supplies framing and cancellation. The built-in host requires neither
Python nor an installed Go runtime; this macOS build still uses system libraries.
These line counts describe ownership and reuse, not measured developer effort.

Both installers retain old artifacts and lack garbage collection and cross-process
registry locking. Python management installation runs in a worker thread;
cancelling that management call does not stop installation before its command
timeout. Inference cancellation is separately implemented and qualified in both.
Only macOS ARM64 artifacts are qualified here. Other built-ins, production command
shortcuts, skill/plugin exports and release/distribution integration still need a
migration before Go could replace the shipped program. No live provider API was
called.

## Reproduce

Use the [common contract](CONTRACT.md) and each language's build guide. On the
measured platform/toolchain, from the repository root:

```sh
uv sync --locked --group build --python 3.13.16
uv run --locked --group build python experiments/provider-runtime/python/build.py
experiments/provider-runtime/go/build.sh
uv run --locked python experiments/provider-runtime/acceptance.py \
  --binary .state/provider-prototype-python/decision
uv run --locked python experiments/provider-runtime/acceptance.py \
  --binary .state/provider-prototype-go/decision
uv run --locked python experiments/provider-runtime/compare.py \
  --baseline-binary .state/provider-prototype-python/decision --baseline-name Python \
  --candidate-binary .state/provider-prototype-go/decision --candidate-name Go \
  --output .state/provider-comparison-new
```

The output directory must not already exist, keeping measurement attempts separate.
The collector reports descriptive statistics by default. An optional `--evaluator`
module can gate claims from the exported manifests. The published measurements
were checked for balanced alternating runs, warmups, identical outputs, one host
and execution context, lower candidate medians, and non-overlapping ranges.
