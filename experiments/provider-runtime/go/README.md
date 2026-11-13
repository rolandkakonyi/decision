# Native Go provider host experiment

This is the native Go implementation of [the common prototype contract](../CONTRACT.md).
Its only built-in transport is TypeSafe System One. It supports the four common
question types, PNG/JPEG assets, external provider commands, explicit installation,
and separate inference and management STDIO MCP servers. Production entry points
and package manifests are independent of this nested module.

Build from the repository root with Go 1.27.1:

```sh
experiments/provider-runtime/go/build.sh
.state/provider-prototype-go/decision --version
```

The build script sets `GOTOOLCHAIN=local` and `CGO_ENABLED=0`, uses the locked module
graph with `-mod=readonly`, and removes filesystem paths, VCS state, and the build
ID. It writes a host-native executable, its linked dependency inventory, and full
license notices under `.state/provider-prototype-go/`. A different output
directory can be the script's first argument. The underlying build command is:

```sh
cd experiments/provider-runtime/go
GOTOOLCHAIN=local CGO_ENABLED=0 go build -mod=readonly -buildvcs=false -trimpath \
  -ldflags='-s -w -buildid=' -o ../../../.state/provider-prototype-go/decision .
```

The executable embeds its request and output schemas. The schema files are
characterizations of `decision_cli.cli.request_schema()` and `response_schema()`
with TypeSafe SDK 0.7.5; they are not generated from a Go approximation.

Run local checks from the nested module:

```sh
cd experiments/provider-runtime/go
go test -race -timeout=60s ./...
go vet ./...
```

Tests use loopback HTTP and child test executables, without live providers or
credentials. The shared acceptance tests exercise the same binary through
CLI and MCP and cover local, archive, and Git installation.

## Runtime and installation

Built-in CLI and STDIO MCP need no Python, Go installation, shared Go runtime, or
provider SDK. HTTPS uses the operating system trust roots. Git-source installation
requires `git`; local and archive installations do not. A plugin's declared
command/setup runtime must already exist. The host never provisions runtimes.

The default plugin home is `~/.local/share/decision-prototype-go`. Each install
creates a unique artifact directory, runs setup there, and then atomically
switches a registration. Removal unregisters but retains artifacts, so running
inference sessions keep their selected version. Old artifact garbage collection
and simultaneous cross-process registry writers are outside this experiment.

Personal registrations live in `PLUGIN_HOME/registry.json`. A project file is
read only when supplied with `--project-config FILE`; it has the same format:

```json
{"fixture":{"path":"/absolute/installed/artifact","version":"1.0.0"}}
```

Git registrations additionally record the resolved `commit`. Installation with
`--project-config` writes that file; its entries override personal registrations.
Built-in provider names are reserved. Registration persistence is implementation
specific and is not an interchange format with the Python prototype.

Subprocesses inherit only `PATH`, `HOME`, `TMPDIR`, `LANG`, `LC_ALL`, and
`SYSTEMROOT` when present. Evaluation credentials travel in the JSON stdin frame
only; describe, prepare, and setup receive none. These are trusted commands,
executed without a shell or sandbox. Plugin stdout is capped protocol data;
stderr is discarded. The host kills and reaps its child on cancellation/timeout
and signals its POSIX process group to stop descendants. Local cancellation does
not promise cancellation of remote provider work. JSON frames are capped at
64 MiB, accommodating the full 32 MiB image batch after base64 encoding.

## Dependencies and maintenance

`go.mod` and `go.sum` pin the complete build graph. The official
[modelcontextprotocol/go-sdk](https://github.com/modelcontextprotocol/go-sdk)
v1.8.0 is the only direct module dependency. Its maintained v1 API provides MCP
framing, initialization, cancellation, and tool dispatch. Raw tool handlers retain
the exact characterized schemas and leave common input/output validation with
the host. Manifest `schema_constraints` are added to the advertised MCP input
schema; plugins remain responsible for provider-specific restrictions in prepare.
The host independently validates the common envelope and all common raw results.

The linked nonstandard modules are:

- `github.com/modelcontextprotocol/go-sdk` v1.8.0: MIT/Apache-2.0 transition;
  the copied upstream license explains which contributions retain MIT terms.
- `github.com/google/jsonschema-go` v0.4.3: MIT.
- `github.com/segmentio/encoding` v0.5.4 and `github.com/segmentio/asm` v1.1.3: MIT.
- `github.com/yosida95/uritemplate/v3` v3.0.2: BSD-3-Clause.
- `golang.org/x/oauth2` v0.35.0, `golang.org/x/sync` v0.20.0,
  `golang.org/x/sys` v0.41.0, and `golang.org/x/time` v0.15.0: BSD-3-Clause.

Go's bundled runtime/standard library use the Go BSD license. The build script
copies each linked module's upstream license (and NOTICE when present), plus
Decision and Go licenses. Distribute these notices alongside the executable.
Test-only module graph entries are not all linked into the distribution;
`dependencies.txt` records the actual binary's module metadata.

The Go host manually tracks TypeSafe's HTTP shape and the Python common contracts.
That creates maintenance work whenever those contracts change; the static schemas
also need deliberate refresh. The Go SDK brings transitive JSON, OAuth, and
rate-limiting code even though this host exposes only STDIO. The prototype buffers
JSON/image batches in memory and does not implement streaming assets. It is a
bounded POSIX prototype: only the host OS/architecture build is qualified, and it
does not include other built-ins, production command shortcuts, generators, or
release/distribution automation.
