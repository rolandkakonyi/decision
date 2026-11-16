# CLI and batches

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
[examples/request.json](https://github.com/rolandkakonyi/decision/blob/main/examples/request.json). `--dry-run` validates without
inference. Human-readable output is the default, including when piped. Add
`--json` before or after a query command to force JSON for scripts. MCP responses
stay structured. Diagnostics go to stderr; requests are not automatically retried.


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

