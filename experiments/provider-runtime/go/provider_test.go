package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestTypeSafeMultipartAndRawResults(t *testing.T) {
	calls := 0
	png := []byte("\x89PNG\r\n\x1a\nfixture")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Path != "/custom/v1/systemone" || r.Header.Get("Authorization") != "Bearer fixture-key" || r.Header.Get("TypeSafe-Danger-Zone-Images") != "true" {
			t.Error("request routing/headers")
		}
		typ, params, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if typ != "multipart/form-data" {
			t.Error("not multipart")
			return
		}
		reader := multipart.NewReader(r.Body, params["boundary"])
		part, e := reader.NextPart()
		if e != nil {
			t.Error(e)
			return
		}
		if part.FormName() != "request" || part.Header.Get("Content-Type") != "application/json" {
			t.Error("request part")
		}
		b, _ := io.ReadAll(part)
		body := obj(mustDecode(t, string(b)))
		if obj(body["state"])["large"] != json.Number("9007199254740993") {
			t.Error("integer precision")
		}
		if strings.Index(string(b), `"z"`) > strings.Index(string(b), `"a"`) {
			t.Error("choice order")
		}
		ref := obj(obj(body["assets"])["<photo>"])
		part, e = reader.NextPart()
		if e != nil {
			t.Error(e)
			return
		}
		b, _ = io.ReadAll(part)
		if part.FormName() != str(ref["part"]) || part.FileName() != str(ref["part"]) || part.Header.Get("Content-Type") != "image/png" || string(b) != string(png) {
			t.Error("image part")
		}
		if _, e = reader.NextPart(); e != io.EOF {
			t.Error("extra image upload")
		}
		io.WriteString(w, validResponse)
	}))
	defer server.Close()
	t.Setenv("FIXTURE_KEY", "fixture-key")
	o := defaults()
	o.base = server.URL + "/custom"
	o.keyEnv = "FIXTURE_KEY"
	o.model = ""
	r := mustRequest(t, batch)
	r.data["assets"] = map[string]any{"<photo>": map[string]any{"data_base64": base64.StdEncoding.EncodeToString(png)}}
	result, e := evaluate(context.Background(), o, nil, r, false, "")
	if e != nil {
		t.Fatal(e)
	}
	if calls != 1 || obj(result)["duration_ms"] == nil {
		t.Fatal("batch/timing")
	}
}
func TestTypeSafeNoRetryRedirectOrBodyLeak(t *testing.T) {
	for _, mode := range []string{"redirect", "failure", "duplicate", "coercion"} {
		t.Run(mode, func(t *testing.T) {
			calls := 0
			s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				switch mode {
				case "redirect":
					w.Header().Set("Location", "/target")
					w.WriteHeader(307)
				case "failure":
					w.WriteHeader(503)
					io.WriteString(w, "fixture-key private-context")
				case "duplicate":
					io.WriteString(w, `{"model":"fixture-key","model":"fixture","answers":{}}`)
				case "coercion":
					io.WriteString(w, strings.Replace(validResponse, `"noul":0.9`, `"noul":true`, 1))
				}
			}))
			defer s.Close()
			o := defaults()
			o.base = s.URL
			o.keyEnv = "FIXTURE_KEY"
			o.model = ""
			t.Setenv("FIXTURE_KEY", "fixture-key")
			_, e := evaluate(context.Background(), o, nil, mustRequest(t, batch), false, "")
			if e == nil || strings.Contains(e.Error(), "fixture-key") || strings.Contains(e.Error(), "private-context") || calls != 1 {
				t.Fatalf("failure not isolated: calls=%d err=%v", calls, e)
			}
		})
	}
}
func TestConnectionPolicy(t *testing.T) {
	for _, s := range []string{"http://example.com", "https://user:pass@example.com", "https://example.com?a=1", "https://example.com#x", "ftp://localhost"} {
		if checkURL(s) == nil {
			t.Fatal("unsafe URL", s)
		}
	}
	for _, s := range []string{"https://example.com/custom", "http://127.0.0.1:123", "http://[::1]:123"} {
		if checkURL(s) != nil {
			t.Fatal(s)
		}
	}
	t.Setenv("DECISION_API_KEY", "")
	t.Setenv("TYPESAFE_API_KEY", "typesafe-key")
	o := defaults()
	o.keyEnv = ""
	o.base = "http://127.0.0.1:123"
	if credential(o, nil) != "" {
		t.Fatal("official key crossed host boundary")
	}
	o.base = "https://api.typesafe.ai"
	if credential(o, nil) != "typesafe-key" {
		t.Fatal("official override")
	}
}
func TestTypeSafeCancellation(t *testing.T) {
	release := make(chan struct{})
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { <-release }))
	defer func() { close(release); s.Close() }()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	o := defaults()
	o.base = s.URL
	_, e := typesafe(ctx, o, mustRequest(t, batch), "fixture")
	if e == nil {
		t.Fatal("uncancelled request")
	}
}

func TestModelOverrideContract(t *testing.T) {
	o := defaults()
	o.model = "launch-default"
	o.dry = true
	for _, cli := range []bool{false, true} {
		result, e := evaluate(context.Background(), o, nil, mustRequest(t, batch), cli, "")
		if e != nil {
			t.Fatal(e)
		}
		want := "fixture"
		if cli {
			want = "launch-default"
		}
		if obj(result)["model"] != want {
			t.Fatalf("cli=%v model=%v", cli, obj(result)["model"])
		}
	}
	r := mustRequest(t, batch)
	delete(r.data, "model")
	result, e := evaluate(context.Background(), o, nil, r, false, "")
	if e != nil || obj(result)["model"] != "launch-default" {
		t.Fatal("missing MCP model did not use launch default", e)
	}
}

func TestTypeSafeSDKModelFallback(t *testing.T) {
	for _, test := range []struct{ name, sdkModel, decisionModel, cliModel, requestModel, want string }{
		{name: "default", want: "jev-latest"},
		{name: "sdk", sdkModel: " sdk-custom ", want: "sdk-custom"},
		{name: "blank sdk", sdkModel: " \t", want: "jev-latest"},
		{name: "decision", sdkModel: "sdk-custom", decisionModel: "decision-custom", want: "decision-custom"},
		{name: "cli", sdkModel: "sdk-custom", decisionModel: "decision-custom", cliModel: "cli-custom", want: "cli-custom"},
		{name: "cli over request", sdkModel: "sdk-custom", decisionModel: "decision-custom", cliModel: "cli-custom", requestModel: "request-custom", want: "cli-custom"},
		{name: "decision over request", sdkModel: "sdk-custom", decisionModel: "decision-custom", requestModel: "request-custom", want: "decision-custom"},
		{name: "request over sdk", sdkModel: "sdk-custom", requestModel: "request-custom", want: "request-custom"},
	} {
		t.Run(test.name, func(t *testing.T) {
			t.Setenv("TYPESAFE_DEFAULT_MODEL", test.sdkModel)
			t.Setenv("DECISION_MODEL", test.decisionModel)
			t.Setenv("FIXTURE_KEY", "fixture-key")
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				body, e := io.ReadAll(r.Body)
				if e != nil {
					t.Error(e)
					return
				}
				wire := obj(mustDecode(t, string(body)))
				if wire["model"] != test.want {
					t.Errorf("wire model = %v, want %s", wire["model"], test.want)
				}
				result := obj(mustDecode(t, validResponse))
				result["model"] = wire["model"]
				w.Write(encoded(result))
			}))
			defer server.Close()
			var args []string
			if test.cliModel != "" {
				args = []string{"--model", test.cliModel}
			}
			opts, pos, _, e := parseOptions(args)
			if e != nil || len(pos) != 0 {
				t.Fatal(e)
			}
			opts.base = server.URL
			opts.keyEnv = "FIXTURE_KEY"
			makeInput := func() *request {
				r := mustRequest(t, batch)
				delete(r.data, "model")
				if test.requestModel != "" {
					r.data["model"] = test.requestModel
				}
				return r
			}
			opts.dry = true
			preview, e := evaluate(context.Background(), opts, nil, makeInput(), true, "")
			if e != nil {
				t.Fatal(e)
			}
			if test.decisionModel == "" && test.cliModel == "" && test.requestModel == "" {
				if _, exists := obj(preview)["model"]; exists {
					t.Fatal("SDK fallback changed dry-run output")
				}
			}
			if calls != 0 {
				t.Fatal("dry-run sent a request")
			}
			opts.dry = false
			result, e := evaluate(context.Background(), opts, nil, makeInput(), true, "")
			if e != nil {
				t.Fatal(e)
			}
			if obj(result)["model"] != test.want || calls != 1 {
				t.Fatalf("result model = %v, calls = %d", obj(result)["model"], calls)
			}
		})
	}
}
