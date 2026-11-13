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
