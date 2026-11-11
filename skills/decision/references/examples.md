# Worked examples

Use your own PNG or JPEG image named `dog.jpg`. Image limits and provider
settings are described in SKILL.md. These commands use Jev 1.14 explicitly.
Output is human-readable by default; append `--json` for structured output.

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

## MCP

The same batch can be sent to `evaluate`. Replace the path descriptor in
`dog-request.json` with `{"data_base64": "...", "format": "jpeg"}` using the
image's actual bytes. MCP accepts base64 assets, not local file paths, and
returns structured answers regardless of the CLI output preference.
