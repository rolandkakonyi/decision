package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

//go:embed request.schema.json
var inputSchema []byte

//go:embed response.schema.json
var outputSchema []byte

func serve(ctx context.Context, o options, p *plugin) error {
	s := mcp.NewServer(&mcp.Implementation{Name: "decision-prototype-go", Version: "1"}, nil)
	add := func(name, description string, schema any, out any, fn func(context.Context, map[string]any) (any, error)) {
		s.AddTool(&mcp.Tool{Name: name, Description: description, InputSchema: schema, OutputSchema: out}, func(ctx context.Context, r *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			v, e := decode(r.Params.Arguments)
			if e != nil || obj(v) == nil {
				e = invalidInput
			}
			var result any
			if e == nil {
				result, e = fn(ctx, obj(v))
			}
			if e != nil {
				return &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: e.Error()}}}, nil
			}
			reply := &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: string(encoded(result))}}}
			if obj(result) != nil {
				reply.StructuredContent = result
			}
			return reply, nil
		})
	}
	if o.management {
		add("provider_install", "Install a trusted provider package; setup may execute.", json.RawMessage(`{"type":"object","required":["source"],"additionalProperties":false,"properties":{"source":{"type":"string"},"sha256":{"type":"string"}}}`), nil, func(ctx context.Context, a map[string]any) (any, error) {
			if !fields(a, "source", "sha256") || !nonempty(a["source"]) {
				return nil, invalidInput
			}
			if checksum, exists := a["sha256"]; exists {
				if _, ok := checksum.(string); !ok {
					return nil, invalidInput
				}
			}
			ctx, cancel := operationContext(ctx, o)
			defer cancel()
			return install(ctx, o, str(a["source"]), str(a["sha256"]))
		})
		add("provider_list", "List registered provider versions.", json.RawMessage(`{"type":"object","additionalProperties":false}`), nil, func(_ context.Context, a map[string]any) (any, error) {
			if len(a) != 0 {
				return nil, invalidInput
			}
			return listProviders(o)
		})
		add("provider_remove", "Unregister a provider; pinned sessions retain their artifact.", json.RawMessage(`{"type":"object","required":["name"],"additionalProperties":false,"properties":{"name":{"type":"string"}}}`), nil, func(_ context.Context, a map[string]any) (any, error) {
			if !fields(a, "name") || !nonempty(a["name"]) {
				return nil, invalidInput
			}
			return removeProvider(o, str(a["name"]))
		})
	} else {
		schema := json.RawMessage(inputSchema)
		if p != nil && len(p.Constraints) > 0 {
			var m map[string]any
			json.Unmarshal(inputSchema, &m)
			m["allOf"] = []any{p.Constraints}
			schema = encoded(m)
		}
		// Raw handler keeps the published schema exact and avoids SDK coercion.
		s.AddTool(&mcp.Tool{Name: "evaluate", Description: "Ask typed questions about shared context in one provider request.", InputSchema: schema, OutputSchema: json.RawMessage(outputSchema), Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true, OpenWorldHint: boolPtr(true), DestructiveHint: boolPtr(false)}}, func(ctx context.Context, call *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			r, e := parseRequest(call.Params.Arguments)
			var result any
			if e == nil {
				result, e = evaluate(ctx, o, p, r, false, "")
			}
			if e != nil {
				return &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: e.Error()}}}, nil
			}
			return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: string(encoded(result))}}, StructuredContent: result}, nil
		})
	}
	if e := s.Run(ctx, &mcp.StdioTransport{MaxLineLength: frameLimit}); e != nil {
		return providerFailure
	}
	return nil
}
func boolPtr(b bool) *bool { return &b }
