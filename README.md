![decision: typed judgments via CLI and MCP on text and images](docs/assets/decision-banner.png)

# decision

[![GitHub Actions CI](https://github.com/rolandkakonyi/decision/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/rolandkakonyi/decision/actions/workflows/ci.yml)

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
decision choice 'Which breed is the dog in images.dog?' \
  --image dog=dog.jpg \
  --option french_bulldog='French bulldog' \
  --option english_bulldog='English bulldog' \
  --model jev-1.14.0

decision noul 'Is the dog in images.dog a French bulldog?' \
  --image dog=dog.jpg --model jev-1.14.0

decision score 'How clearly is the face of the dog in images.dog visible?' \
  --image dog=dog.jpg \
  --level 'The face is not visible' \
  --level 'The face is partly visible or obscured' \
  --level 'The entire face is clearly visible' \
  --model jev-1.14.0

decision box 'Locate the dog in images.dog' \
  --image dog=dog.jpg --model jev-1.14.0
```

The key before `=` in each `--option` is returned in the answer; the text after
it describes that option. Add `--json` for machine-readable output. Choice
always selects an offered option; add `--option other='Another breed or no identifiable dog'`
if that outcome is allowed.

See the [worked examples](skills/decision/references/examples.md) for all four
question types, multiple images, previews, `ask`, `run`, and MCP. The generated
[dog request](skills/decision/references/dog-request.json) asks all four question
types over one image; copy it beside your own `dog.jpg` before running it.

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

## Mix Noul, Box, and Choice in one request

Every named question can have a different type. Save the following as
`dog-mixed-questions.json`:

```json
{
  "dog_present": {
    "type": "noul",
    "instructions": "Is a dog visible in images.dog?"
  },
  "dog_location": {
    "type": "box",
    "instructions": "Locate the dog in images.dog"
  },
  "breed": {
    "type": "choice",
    "instructions": "Which breed is the dog in images.dog?",
    "criteria": {
      "french_bulldog": "French bulldog",
      "english_bulldog": "English bulldog"
    }
  }
}
```

With the CLI, supply the questions and image separately:

```bash
decision ask --questions-file dog-mixed-questions.json \
  --image dog=dog.jpg --model jev-1.14.0
```

Or copy [dog-mixed-request.json](skills/decision/references/dog-mixed-request.json)
beside your `dog.jpg` and use the complete request file:

```bash
decision run dog-mixed-request.json --json
```

Both send **one provider request**, upload the image once, and retain the answer
IDs `dog_present`, `dog_location`, and `breed`. CLI output is human-readable by
default; add `--json` to either command for structured answers. Add `--dry-run`
to preview without inference. Image paths inside request JSON are relative to
that JSON file; `--image` paths are relative to your working directory.

For MCP, call `evaluate` once with this input. Replace the placeholder with
standard base64 of your PNG/JPEG bytes, without whitespace or a `data:` prefix:

```json
{
  "model": "jev-1.14.0",
  "state": {"images": {"dog": "<dog-photo>"}},
  "assets": {
    "<dog-photo>": {"data_base64": "REPLACE_WITH_BASE64_IMAGE_BYTES"}
  },
  "questions": {
    "dog_present": {
      "type": "noul",
      "instructions": "Is a dog visible in images.dog?"
    },
    "dog_location": {
      "type": "box",
      "instructions": "Locate the dog in images.dog"
    },
    "breed": {
      "type": "choice",
      "instructions": "Which breed is the dog in images.dog?",
      "criteria": {
        "french_bulldog": "French bulldog",
        "english_bulldog": "English bulldog"
      }
    }
  }
}
```

MCP returns `structuredContent.answers` keyed by the same three IDs and accepts
base64 assets only. All questions are independent; use `dog_present` to decide
whether to use the location and breed answers after receiving the batch. Box
returns a location even when the dog is absent, and Choice selects an offered
breed. Add an `other` criterion when another breed or no dog is a valid outcome.

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
uv run --locked python scripts/verify_wheel.py
uv run --locked --group build python scripts/build_standalone.py --output .state/dist
DECISION_TEST_EXECUTABLE="$PWD/.state/dist/decision" \
  uv run --locked python -m unittest discover -s tests -p test_decision_mcp.py -v
```

Tests use local fixtures without inference. Do not commit environment files,
credentials, build directories, or generated executables. The clean-wheel check
installs outside the checkout and tests CLI, skill generation, images, and MCP.
See [AGENTS.md](AGENTS.md) for the module map, contracts, and narrow checks.

CI runs the source suite on Linux with Python 3.11/3.14 and macOS with 3.14,
plus separate wheel and native Linux/macOS standalone jobs. uv/Python and
PyInstaller library caches reduce repeated downloads and processing; build
outputs are always regenerated. Other platforms require native builds and
testing. Dependencies are recorded in `uv.lock`. Work goes directly to `main`;
tags are created manually when needed, with no automatic release workflow.
