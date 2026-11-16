# Images and bounding boxes

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

See the [worked examples](https://github.com/rolandkakonyi/decision/blob/main/skills/decision/references/examples.md) for all four
question types, multiple images, previews, `ask`, `run`, and MCP. The generated
[dog request](https://github.com/rolandkakonyi/decision/blob/main/skills/decision/references/dog-request.json) asks all four question
types over one image; copy it beside your own `dog.jpg` before running it.

`--image [NAME=]PATH` can be repeated. Images are added under `state.images`,
keeping existing JSON object fields. Scalar state is retained as `state.context`;
no text state is required when images are supplied. Unnamed images are named
by flag position: `image`, `image_2`, and so on. For example,
`--image dog=dog.jpg --image photo.png` names the second image `image_2`.
Duplicate names fail explicitly.

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
Images are sent once per batch: TypeSafe uses multipart uploads; OpenAI and
Cloudflare use base64 data URLs; Gemini uses inline base64 content blocks.

For MCP, replace path descriptors with `{"data_base64": "...", "format": "png"}`.
MCP accepts base64 bytes only, never paths on the server's filesystem. Format is
optional and inferred; png, jpg, and jpeg are accepted. Base64 uses the standard
alphabet without whitespace or a data: prefix.

In TypeSafe mode, the CLI automatically sends `TypeSafe-Danger-Zone-Images: true`
for image or Box requests. TypeSafe currently marks these features experimental; Box requires
Jev 1.14. The CLI checks PNG/JPEG signatures and 16 MiB per file / 32 MiB combined.
The provider validates decoding, dimensions (currently 280-1568 pixels per side),
and expanded-request limits. Images are not resized. See the
[official image docs](https://docs.typesafe.ai/images) and
[examples/images.json](https://github.com/rolandkakonyi/decision/blob/main/examples/images.json).


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

Or copy [dog-mixed-request.json](https://github.com/rolandkakonyi/decision/blob/main/skills/decision/references/dog-mixed-request.json)
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

