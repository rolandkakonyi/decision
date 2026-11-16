# Built-in providers and configuration

Choose a wire API with `--provider` or `DECISION_PROVIDER`. The built-in names
are `typesafe`, `openai`, `gemini`, and `cloudflare`; installed executable
providers use their registered names. All four built-ins support Noul, Choice,
and Score over text and PNG/JPEG images. Box is available only with TypeSafe
and Jev 1.14. Provider-specific limits are described below.

## TypeSafe



Set `TYPESAFE_API_KEY` or `TYPESAFEAI_API_KEY` for the default TypeSafe host.
TypeSafe is the default provider. All providers share the common request
envelope and normalized answer format; their own limits can narrow that
envelope. Answers are validated before any batch is returned, and redirects
are not followed.

The base URL honors `--base-url`, `DECISION_BASE_URL`, then
`TYPESAFE_BASE_URL`. Model selection honors `--model`, `DECISION_MODEL`, then a
request model; the execution fallback is `TYPESAFE_DEFAULT_MODEL`, then
`jev-latest`. Image and Box requests opt into TypeSafe's experimental image
support. See [images and bounding boxes](images.md) for the limits.

## OpenAI Decisions

With `OPENAI_API_KEY` exported into the command's environment:

```bash
decision noul 'Does the message request a refund?' --state 'Please refund my order.' \
  --provider openai --model gpt-6-luna

decision choice 'Which breed is the dog in images.dog?' \
  --image dog=dog.jpg --provider openai --model gpt-6-luna \
  --option french_bulldog='French bulldog' --option english_bulldog='English bulldog'
```

Copy [openai-dog-questions.json](https://github.com/rolandkakonyi/decision/blob/main/skills/decision/references/openai-dog-questions.json)
for a mixed Noul, Choice, and Score batch:

```bash
decision ask --questions-file openai-dog-questions.json \
  --image dog=dog.jpg --provider openai --model gpt-6-luna --json
```

Or copy [openai-dog-request.json](https://github.com/rolandkakonyi/decision/blob/main/skills/decision/references/openai-dog-request.json)
beside your `dog.jpg`:

```bash
decision run openai-dog-request.json --provider openai --model gpt-6-luna --json
```

The adapter calls `POST /v1/decisions` directly over HTTPS. It maps
Noul to `predicate`, named Choice criteria to `choices`, and ordered Score
criteria to `levels`. Answers retain their question IDs and the usual
`noul`/`choice`/`score` types. Objects and arrays of objects become `input_json`
evidence; other state values become text. Images are sent once as shared
base64 data URLs, paired with their reference names. Structured criterion
descriptions are serialized as JSON text; Noul true/false criteria are included
in its instructions.

OpenAI currently supports `gpt-6-luna`, 1–400 questions, 2–255 choices, and
2–10 score levels. **Box is unsupported**: a batch containing Box fails before
any provider call. Refusals, duplicate/missing IDs, or invalid answers fail the
whole batch without returning partial judgments. No automatic retries or
provider fallback are used. See the [official guide](https://developers.openai.com/api/docs/guides/decisions)
and [API reference](https://developers.openai.com/api/reference/resources/decisions/methods/create).

The OpenAI base URL defaults to `https://api.openai.com/v1`, overridable via
`--base-url`, `DECISION_BASE_URL`, or `OPENAI_BASE_URL`, in that order. The default
model is `gpt-6-luna` when no model is selected. `.env.local` is not loaded
automatically. Export credentials into the environment before launching the command.

## Gemini Decisions

Export `GEMINI_API_KEY` into the command's environment, then select Gemini:

```bash
decision noul 'Does this request a refund?' --state 'Please refund my order.' \
  --provider gemini
decision choice 'Which breed is the dog in images.dog?' \
  --image dog=dog.jpg --provider gemini \
  --option french_bulldog='French bulldog' --option english_bulldog='English bulldog'
```

Noul, Choice, and Score use the shared question format and normalized answers.
For mixed batches, copy
[gemini-dog-questions.json](https://github.com/rolandkakonyi/decision/blob/main/skills/decision/references/gemini-dog-questions.json)
and pass it to `decision ask --questions-file gemini-dog-questions.json --image dog=dog.jpg --provider gemini --json`.
Alternatively, copy
[gemini-dog-request.json](https://github.com/rolandkakonyi/decision/blob/main/skills/decision/references/gemini-dog-request.json)
beside your `dog.jpg` and run `decision run gemini-dog-request.json --provider gemini --json`.

The adapter sends one POST to `/v1beta/decisions` with the default model
`gemini-decision-1`. It serializes JSON state as text, labels each named image,
and sends its bytes once as an inline base64 content block. Common Noul criteria
`true`/`false` become `true_description`/`false_description`. Structured criterion
descriptions become JSON text; absent Choice descriptions become empty strings.
Token counts map from Gemini's `total_input_tokens`/`total_output_tokens` into
the common `usage` fields. A zero output count remains zero.

Local checks enforce up to 1000 questions; IDs of 1–64 ASCII letters, digits,
underscores, or hyphens; 2–255 Choice options; and 2–255 nonempty Score levels.
Option IDs allow 1–128 characters with internal ASCII spaces, without edge
spaces, control characters, or non-ASCII spaces. The harness accepts PNG/JPEG
images up to 16 MiB each and 32 MiB combined; Gemini enforces its context and
media limits. Box is rejected before network access. Audio, video, and PDFs
are not accepted by the harness. Invalid, incomplete, or safety-blocked responses
fail the entire batch without partial judgments, redirects, or retries. See the
[official Decisions guide](https://aistudio.google.com/docs/decisions-api).

The base URL defaults to `https://generativelanguage.googleapis.com/v1beta`,
overridable via `--base-url`, `DECISION_BASE_URL`, or `GEMINI_BASE_URL`, in that
order; the adapter appends `/decisions`. `--model` and `DECISION_MODEL` override
the default model. At the official HTTPS host, `GEMINI_API_KEY` takes precedence
over `GOOGLE_API_KEY`; `DECISION_API_KEY` takes precedence over both. An explicit
`--api-key-env` / `DECISION_API_KEY_ENV` selects only that variable. Custom hosts
require a selected credential variable or `DECISION_API_KEY`.

## Cloudflare Clef

Use a Cloudflare Workers AI API token and your account's
32-character ID. Export `CLOUDFLARE_API_TOKEN` (or `CLOUDFLARE_AUTH_TOKEN`) and
`CLOUDFLARE_ACCOUNT_ID`, then run:

```bash
decision choice 'Which breed is the dog in images.dog?' \
  --image dog=dog.jpg --provider cloudflare --model clef-flash \
  --option french_bulldog='French bulldog' --option english_bulldog='English bulldog'
```

`clef` is the default; `clef-flash`, `@cf/cloudflare/clef`, and
`@cf/cloudflare/clef-flash` are also accepted. Noul, Choice, and Score share the
same request format and response stats as the other providers. For a mixed
batch, copy [cloudflare-dog-questions.json](https://github.com/rolandkakonyi/decision/blob/main/skills/decision/references/cloudflare-dog-questions.json):

```bash
decision ask --questions-file cloudflare-dog-questions.json \
  --image dog=dog.jpg --provider cloudflare --model clef-flash --json
```

Or copy [cloudflare-dog-request.json](https://github.com/rolandkakonyi/decision/blob/main/skills/decision/references/cloudflare-dog-request.json)
beside `dog.jpg` and run `decision run cloudflare-dog-request.json --provider cloudflare --json`.
Both send one request with one image for all three questions.

The adapter calls `/client/v4/accounts/ACCOUNT_ID/ai/run/@cf/cloudflare/MODEL`
and validates the Workers AI response envelope before returning answers.
Limits: 1–64 questions; IDs of 1–100 letters, digits, underscores, dots, or
hyphens; 2–255 choices; 2–10 score levels. **Box is unsupported** and fails
before sending a request. Up to 4 PNG/JPEG images are allowed, 4 MiB each and
8 MiB combined; the encoded request body must fit within 13 MiB. Cloudflare
enforces image decoding and the 16-megapixel limit. Images are sent in asset
order, with `state._decision_image_references` mapping reference names to
1-based positions. That field is reserved for image requests; scalar state
is preserved under `context`. See the [official Clef docs](https://developers.cloudflare.com/workers-ai/models/clef/)
and [Clef Flash docs](https://developers.cloudflare.com/workers-ai/models/clef-flash/).

`--account-id` overrides `DECISION_ACCOUNT_ID`, then `CLOUDFLARE_ACCOUNT_ID`.
The base URL defaults to `https://api.cloudflare.com/client/v4`, overridable
via `--base-url`, `DECISION_BASE_URL`, or `CLOUDFLARE_BASE_URL`. At the default
root, the adapter adds the account's `/accounts/ACCOUNT_ID/ai/run` path.
A custom base URL must be a **Workers AI run root**, such as an account's
`.../ai/run` or an AI Gateway's `.../workers-ai`; the adapter appends
`/@cf/cloudflare/MODEL`. Custom hosts require `--api-key-env KEY_VARIABLE`
(or `DECISION_API_KEY_ENV`) or `DECISION_API_KEY`. Account IDs and credentials
are server configuration in MCP and cannot be overridden by tool callers.
Dry runs require neither an account ID nor credentials. Cloudflare's
[REST setup guide](https://developers.cloudflare.com/workers-ai/get-started/rest-api/)
recommends its Workers AI token template; custom tokens require Workers AI
Read and Edit permissions.

## Other compatible hosts

Select the matching wire API and override routing and credentials:

```bash
export DECISION_PROVIDER=openai
export DECISION_BASE_URL=https://provider.example/api
export DECISION_API_KEY_ENV=OTHER_PROVIDER_KEY
export DECISION_MODEL=provider-model
decision run request.json
```

`--provider`, `--base-url`, `--api-key-env`, and `--model` override those settings. The key
selector is the name of an environment variable holding the credential, never
the credential itself. `DECISION_API_KEY` can hold an explicitly selected key
directly. Default provider keys are used only on the matching provider's official
HTTPS host. Other hosts require an explicit key selector or `DECISION_API_KEY`.
For `typesafe`, the base URL also honors `TYPESAFE_BASE_URL`; the execution model
defaults to `TYPESAFE_DEFAULT_MODEL`, then `jev-latest`. Compatible hosts must implement the selected Decisions
or System One API; a chat-completions endpoint alone does not suffice.

