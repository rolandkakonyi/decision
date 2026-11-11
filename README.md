# decision

One command for typed judgments from Jev or compatible System One providers.
The same executable supports terminal commands, a STDIO MCP server, and
generation of its own companion Agent Skill.

```bash
uv sync --locked
uv run decision run examples/request.json --dry-run
uv run decision mcp
uv run decision skill --output skills/decision
```

Install on PATH with `uv tool install /path/to/decision`, or build a standalone
executable using [the build guide](docs/standalone.md). For a remote install,
use existing GitHub authentication and pin a reviewed commit:

```bash
uv tool install 'git+https://github.com/rolandkakonyi/decision.git@<commit-sha>'
```

This repository is private. Installations and updates require access to it.
The Python distribution is named `decision-harness`; the installed command is
`decision`. Uninstall it with `uv tool uninstall decision-harness`.
For development, `uv tool install --editable .` from the checkout keeps source
edits live. After pulling version 0.4.0, reinstall a non-editable local install
with `uv tool install --force .`.

## Ask several questions together

```bash
decision run request.json
decision run request.json --json
decision ask --state-file state.json --questions-file questions.json
decision noul 'Does this request a refund?' --state 'Please refund my order.'
```

A request has one `state` and a `questions` object keyed by question IDs.
Each question specifies `type` (`noul`, `choice`, `score`, or `box`),
`instructions`, and any `criteria`. Mixed questions sharing state are sent in
one provider request, and answers retain their IDs. See
[examples/request.json](examples/request.json). `--dry-run` validates without
inference. Human-readable output is the default, including when piped. Add
`--json` before or after a query command to force JSON for scripts. The default
changed in 0.4.0, so existing JSON consumers must add that flag. MCP responses
stay structured. Diagnostics go to stderr; requests are not automatically retried.

## Images and Box

```bash
decision noul 'Is a person visible?' --image camera=door.png --model jev-1.14.0
decision box 'Locate the package in images.camera' --image camera=door.png --model jev-1.14.0
decision run request.json --image camera=door.png --image badge=badge.jpg --json
```

`--image [NAME=]PATH` can be repeated. Images are added under `state.images`,
keeping existing JSON object fields. Scalar state is retained as `state.context`;
no text state is required when images are supplied. Unnamed files receive the
names `image`, `image_2`, etc. Duplicate names fail explicitly.

Batch JSON also accepts an `assets` map. Use unique references in state,
question instructions, or criteria:

```json
{
  "model": "jev-1.14.0",
  "state": {"photo": "<photo>"},
  "assets": {"<photo>": {"path": "photo.png"}},
  "questions": {
    "present": {"type": "noul", "instructions": "Is there a package in photo?"},
    "location": {"type": "box", "instructions": "Locate the package in photo"}
  }
}
```

Paths in request JSON files are relative to that file; flag paths and paths in
stdin are relative to the working directory. Every asset must be referenced.
The SDK uploads images once in multipart form, shared by the batch.

For MCP, replace path descriptors with `{"data_base64": "...", "format": "png"}`.
MCP accepts base64 bytes only, never paths on the server's filesystem. Format is
optional and inferred; png, jpg, and jpeg are accepted. Base64 uses the standard
alphabet without whitespace or a data: prefix.

The CLI automatically sends `TypeSafe-Danger-Zone-Images: true` for image or
Box requests. TypeSafe currently marks these features experimental; Box requires
Jev 1.14. The CLI checks PNG/JPEG signatures and 16 MiB per file / 32 MiB combined.
The provider validates decoding, dimensions (currently 280-1568 pixels per side),
and expanded-request limits. Images are not resized. See the
[official image docs](https://docs.typesafe.ai/images) and
[examples/images.json](examples/images.json).

## Provider settings

Set `TYPESAFE_API_KEY` or `TYPESAFEAI_API_KEY` for the default TypeSafe host.
Switch compatible providers using environment variables:

```bash
export DECISION_BASE_URL=https://provider.example/api
export DECISION_API_KEY_ENV=OTHER_PROVIDER_KEY
export DECISION_MODEL=provider-model
decision run request.json
```

`--base-url`, `--api-key-env`, and `--model` override those settings. The key
selector is the name of an environment variable holding the credential, never
the credential itself. `DECISION_API_KEY` can hold an explicitly selected key
directly. TypeSafe credentials are not automatically sent to other hosts.
Providers must implement the TypeSafe System One API and SDK authentication;
an OpenAI-compatible chat endpoint alone does not suffice.

## MCP in the same command

```bash
codex mcp add decision -- /absolute/path/to/decision mcp
```

The `evaluate` tool accepts `{state, questions, model?, assets?}` and returns structured
answers, model, and usage with an output schema. Forward the selected credential
variable and its selector in Codex's MCP environment settings. Provider routing,
key selection, and timeout are fixed server configuration; tool callers can
override only the model. Stdout is reserved for protocol messages in MCP mode.

## Generated skill

```bash
decision skill                         # Markdown to stdout, no API call
decision skill --output skills/decision
decision skill --output skills/decision --check
```

The generator owns `skills/decision/SKILL.md` and its example, input schema,
and command reference. The schema and command reference come from the running
tool. Edit `src/decision_cli/skill.py` and the CLI definitions, then regenerate.
CI rejects a stale generated export. No key or provider access is required.

`agent-skills` consumes a generated snapshot pinned to a commit in this repo.
After committing and pushing a reviewed change here, refresh its snapshot with:

```bash
python scripts/sync-decision-skill.py --source /path/to/decision
python scripts/sync-decision-skill.py --check
```

Run those commands in the `agent-skills` checkout. Its import records the source
commit and file hashes, retains marketplace/cloud discovery, and contains no
tool implementation. Installing the skill does not install the executable.

## Development

```bash
uv sync --locked --group build
uv run --locked decision skill --output skills/decision --check
uv run --locked python -m unittest discover -s tests -v
uv run --locked --group build python scripts/build_standalone.py --output .state/dist
DECISION_TEST_EXECUTABLE="$PWD/.state/dist/decision" \
  uv run --locked python -m unittest discover -s tests -p test_decision_mcp.py -v
```

Tests use local fixtures without inference. Do not commit environment files,
credentials, build directories, or generated executables. Only Linux x86_64
standalone builds have been verified; other platforms require native builds
and testing. Dependencies are recorded in `uv.lock`.
