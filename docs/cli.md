# CLI and batches

## Questions and batches

Ask a single question with its matching command:

```bash
decision choice 'Which team should handle this message?' \
  --state 'I was charged twice.' \
  --option billing='Payments and refunds' \
  --option technical='Bugs and integrations'

decision score 'How urgent is this issue?' \
  --state 'The service is unavailable for all customers.' \
  --level 'Can wait' --level 'Needs attention soon' --level 'Needs immediate attention'
```

Choice returns one of the offered option keys. Include an `other` option when
none of the named options may apply. Score levels run from lowest to highest,
with a numeric score from 0 to the number of levels minus one. Scores may be
fractional; `legend` maps zero-based level keys to their descriptions.

For several questions about the same evidence, save a request as `request.json`:

```json
{
  "state": {"message": "I was charged twice. Please refund the extra payment."},
  "questions": {
    "refund": {
      "type": "noul",
      "instructions": "Does the customer request a refund?"
    },
    "team": {
      "type": "choice",
      "instructions": "Which team handles this message?",
      "criteria": {
        "billing": "Payments and refunds",
        "technical": "Bugs and integrations"
      }
    },
    "blocking": {
      "type": "score",
      "instructions": "How much does the problem block the customer?",
      "criteria": [
        "Can continue normally",
        "Can continue with inconvenience",
        "Cannot continue"
      ]
    }
  }
}
```

```bash
decision run request.json --json
```

Questions share `state` and retain their IDs in the answers. They are evaluated
independently in one provider request; use the returned answers to make any
conditional decisions. Add `--dry-run` to validate and preview the request.
Use `decision run -` to read a request from stdin, or keep evidence and questions
in separate files with `decision ask --state-file state.json --questions-file questions.json`.

Decision validates the entire response before returning a batch. Missing or
invalid answers fail the batch without partial results. Choice and Score
probability maps must include every offered option and total 1 within 0.02.
Requests are not automatically retried, redirected, or sent to a fallback provider.
`--timeout SECONDS` limits query execution (default: 30 seconds); expiration
reports a provider failure without answers.


## Response stats

Every successful query includes input/output token counts and elapsed request
time, for single questions and mixed batches. Human-readable output ends with:

```text
Usage: 120 input tokens, 12 output tokens
Duration: 243.180 ms
```

JSON output and MCP `structuredContent` include these fields alongside the
model and answers:

```json
{
  "usage": {"input_tokens": 120, "output_tokens": 12},
  "duration_ms": 243.18
}
```

Stats cover the whole batch. `duration_ms` is measured with a monotonic clock
around provider execution, image upload, and response handling. Process startup
and local file validation are excluded. Token counts come from the provider;
unreported counts are `null` in JSON/MCP and shown as unavailable in readable
output. Zero counts remain zero. Dry runs preview a request without inference
and have no response stats.

