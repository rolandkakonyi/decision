# Worked examples

Use your own PNG or JPEG image named `dog.jpg`. Image limits and provider
settings are described in SKILL.md. These commands use Jev 1.14 explicitly.
Output is human-readable by default; append `--json` for structured output.
Every successful query includes input/output token counts in `usage` and
elapsed request time in `duration_ms`, for the whole batch.

## Choice: select a breed

```bash
decision choice 'Which breed is the dog in images.dog?' \
  --image dog=dog.jpg \
  --option french_bulldog='French bulldog' \
  --option english_bulldog='English bulldog' \
  --model jev-1.14.0
```

`--image dog=dog.jpg` makes the picture available as `images.dog`. Each
`--option NAME=DESCRIPTION` defines a returned key and its description.
The selected key is `french_bulldog` or `english_bulldog`, with probabilities
for both. A Choice always selects one of its options; if other breeds should
be possible, add `--option other='Another breed or no identifiable dog'`.

## Noul: ask a yes/no question

```bash
decision noul 'Is the dog in images.dog a French bulldog?' \
  --image dog=dog.jpg --model jev-1.14.0
```

Returns the probability of yes, from 0 to 1, shown as a percentage in human
output. This is one judgment rather than a choice between breed labels.

## Score: evaluate ordered levels

```bash
decision score 'How clearly is the face of the dog in images.dog visible?' \
  --image dog=dog.jpg \
  --level 'The face is not visible' \
  --level 'The face is partly visible or obscured' \
  --level 'The entire face is clearly visible' \
  --model jev-1.14.0
```

Levels are ordered from 0 in the order supplied. This rubric ranges from 0 to
2; the expected score can fall between levels. The response includes the level
probabilities and confidence.

## Box: locate the dog

```bash
decision box 'Locate the dog in images.dog' \
  --image dog=dog.jpg --model jev-1.14.0
```

Returns left, top, right, and bottom as fractions of image dimensions, plus
confidence. Box requires Jev 1.14 and returns a location even when the target
is absent; pair it with a presence Noul when absence matters.

## Mixed Noul, Box, and Choice: CLI

Save the following as `dog-mixed-questions.json`, or copy the file from
`references/dog-mixed-questions.json`:

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

Ask all three questions about the same image with one provider request:

```bash
decision ask --questions-file dog-mixed-questions.json \
  --image dog=dog.jpg --model jev-1.14.0
```

For `run`, copy `references/dog-mixed-request.json` beside your `dog.jpg`,
naming the copy `dog-mixed-request.json`. It wraps those same questions with
the shared state, model, and image asset:

```bash
decision run dog-mixed-request.json --json
```

Both commands upload the image once and return answers keyed by `dog_present`,
`dog_location`, and `breed`. Output is human-readable by default; add `--json`
to either command for structured answers, or `--dry-run` to preview without
inference. Questions are independent: use the presence probability to decide
whether to use the location or breed answer after receiving the batch. A Box
still returns coordinates when no dog is present, and Choice always selects
one of its offered breeds. Add an `other` criterion when needed.

## Run: all four types in one request

Copy `references/dog-request.json` beside your `dog.jpg`, naming the copy
`dog-request.json`, then run:

```bash
decision run dog-request.json --json
```

This asks presence, breed, face visibility, and location together. The image is
uploaded once, with answers keyed by `dog_present`, `breed`, `face_visibility`,
and `dog_location`. Questions are independent; use application code to decide
when to use the breed or location answer. Image paths inside request JSON are
relative to that JSON file.

## Ask: reuse a questions file with another image

Copy `references/dog-questions.json` to `dog-questions.json` and run:

```bash
decision ask --questions-file dog-questions.json \
  --image dog=dog.jpg --model jev-1.14.0 --json
```

The questions file contains only the named questions object. You can change
the image path without editing the questions. Add `--state` or `--state-file`
for extra shared context; image-only state works too.

## Compare two images

```bash
decision noul 'Do images.before and images.after show the same dog?' \
  --image before=before.jpg --image after=after.jpg --model jev-1.14.0
```

Repeat `--image NAME=PATH` for distinct images, and refer to each by its name.

## Preview and JSON output

```bash
decision run dog-request.json --dry-run
decision run dog-request.json --dry-run --json
```

Previews validate the selected files and request without calling a provider.
Add `--json` to any query command for JSON answers instead of human output.
For a text-only example:

```bash
decision noul 'Does the message request a refund?' \
  --state 'Please refund my order.'
```

## Mixed Noul, Box, and Choice: MCP

Call the `evaluate` tool once with the following input. Replace
`REPLACE_WITH_BASE64_IMAGE_BYTES` with the standard base64 encoding of your
PNG/JPEG file's bytes, without whitespace or a `data:` prefix. The image
format is inferred from the bytes.

```json
{
  "model": "jev-1.14.0",
  "state": {
    "images": {
      "dog": "<dog-photo>"
    }
  },
  "assets": {
    "<dog-photo>": {
      "data_base64": "REPLACE_WITH_BASE64_IMAGE_BYTES"
    }
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

This uses the same three named questions as the CLI example. MCP accepts
base64 assets, not local file paths, and returns structured answers regardless
of the CLI output preference. Read `structuredContent.answers.dog_present`,
`structuredContent.answers.dog_location`, and `structuredContent.answers.breed`.
Read token counts from `structuredContent.usage` and elapsed milliseconds from
`structuredContent.duration_ms`.
