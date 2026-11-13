# Questions and answers

Decision asks typed questions over shared context. Use the CLI for terminal
workflows or the MCP server's `evaluate` tool for agents. Both use the same
request validation and normalized answers.

## One state, independent questions

`state` is the context shared by every question in a batch. It can be text or
another non-null JSON value. Images are named assets referenced from the state
or questions. `questions` maps your IDs to typed questions; answers retain those
IDs. Questions are independent, so application code decides how to combine
their answers after the batch returns.

## Noul: a yes probability

Ask a yes/no question and receive the probability of yes, from 0 to 1.
Readable output shows a percentage. Optional `true` and `false` criteria
describe what each outcome means.

```bash
decision noul 'Does this request a refund?' \
  --state 'Please refund my order.' --dry-run
```

Supported by all four built-in providers.

## Choice: a named option

Define a set of named outcomes. The answer selects an offered key and includes
probabilities for all options. The description after `=` explains the option;
the name before it is the returned key. Include `other` when none of your
specific labels may fit.

```bash
decision choice 'What is the message about?' --state 'Where is my order?' \
  --option order='An existing order' \
  --option other='Anything else' --dry-run
```

Supported by all four built-in providers. Their option limits differ.

## Score: an ordered rating

Define levels in order, starting at 0. The response includes level probabilities,
an expected score that can lie between levels, and confidence. Use concrete
descriptions to make your rubric clear.

```bash
decision score 'How urgent is the request?' --state 'Please reply today.' \
  --level 'Can wait' --level 'Needs attention soon' \
  --level 'Needs immediate attention' --dry-run
```

Supported by all four built-in providers. Their level limits differ.

## Box: a location in an image

Ask for a bounding box. Left, top, right, and bottom are fractions of the image
dimensions. Box requires TypeSafe with Jev 1.14 and is experimental.

```bash
decision box 'Locate the dog in images.dog' \
  --image dog=dog.jpg --provider typesafe --model jev-1.14.0
```

This command needs your image and provider credentials. Box returns a location
even when the target is absent. Pair it with a presence Noul, then decide
whether to use the coordinates. OpenAI, Gemini, and Cloudflare reject Box
before network access.

[Image inputs and mixed batches →](images.md)

## Complete answers and batch stats

Decision validates answer IDs, types, offered choices, score bounds,
probability maps, and token counts before returning results. An invalid or
incomplete answer fails the whole batch. Choice and Score probability maps
must include every offered option and sum to 1 within 0.02 to allow rounding.

Human-readable output is the default, including in pipes. Use `--json` for
scripts. MCP returns structured results. Every successful query preserves
provider token usage and includes `duration_ms` for the batch. Unreported token
counts stay null; zero stays zero. A dry run has no inference or response stats.

See [CLI and output](cli.md), [provider limits and defaults](providers.md),
and the [request schema](schema.md).
