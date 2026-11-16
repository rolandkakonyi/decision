# Request and response schemas

CLI batches and inference MCP use the same common request envelope. Each
provider may narrow supported question types and limits. MCP advertises the
constraints for its configured provider.

## Request

[Download the request schema](https://raw.githubusercontent.com/rolandkakonyi/decision/main/request.schema.json).

```json
--8<-- "request.schema.json"
```

## Response

Successful results preserve question IDs and include normalized answers,
provider token usage, and client-measured `duration_ms` for the whole batch.
Unknown token counts stay null; zero stays zero. Invalid or incomplete results
fail the batch. A dry run previews the request without response statistics.

[Download the response schema](https://raw.githubusercontent.com/rolandkakonyi/decision/main/response.schema.json).

```json
--8<-- "response.schema.json"
```
