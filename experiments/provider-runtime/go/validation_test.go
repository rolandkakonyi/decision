package main

import (
	"encoding/base64"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

const batch = `{"state":{"large":9007199254740993,"image":"<photo>"},"model":"fixture","questions":{"yes":{"type":"noul","instructions":"Is <photo> red?"},"choice":{"type":"choice","instructions":"Choose","criteria":{"z":"Z","a":"A"}},"score":{"type":"score","instructions":"Rate","criteria":["Low","High"]},"box":{"type":"box","instructions":"Locate <photo>"}}}`
const validResponse = `{"model":"fixture","answers":{"yes":{"type":"noul","noul":0.9},"choice":{"type":"choice","choice":"z","confidence":1,"probabilities":{"z":1,"a":0}},"score":{"type":"score","score":0,"confidence":1,"probabilities":{"0":1,"1":0},"legend":{"0":"Low","1":"High"}},"box":{"type":"box","box":{"top":0.1,"left":0.2,"right":0.8,"bottom":0.9},"confidence":null}},"usage":{"input_tokens":9007199254740993,"output_tokens":0}}`

func mustRequest(t *testing.T, s string) *request {
	t.Helper()
	r, e := parseRequest([]byte(s))
	if e != nil {
		t.Fatal(e)
	}
	return r
}
func mustDecode(t *testing.T, s string) any {
	t.Helper()
	v, e := decode([]byte(s))
	if e != nil {
		t.Fatal(e)
	}
	return v
}
func TestRawJSON(t *testing.T) {
	for _, s := range []string{`{"a":1,"a":2}`, `{"x":{"a":1,"a":2}}`, `[NaN]`, `[Infinity]`, `{} {}`, `[1e999]`, string([]byte{0xff})} {
		if _, e := decode([]byte(s)); e == nil {
			t.Fatalf("accepted %q", s)
		}
	}
	r := mustRequest(t, batch)
	if e := validateRequest(r, false, ""); e != nil {
		t.Fatal(e)
	}
	if obj(r.data["state"])["large"] != json.Number("9007199254740993") {
		t.Fatal("integer changed")
	}
	wire := string(encoded(r.wire()))
	if strings.Index(wire, `"z"`) > strings.Index(wire, `"a"`) {
		t.Fatal("choice order changed")
	}
}
func TestRequestValidation(t *testing.T) {
	for _, s := range []string{`{}`, `{"state":null,"questions":{"q":{"type":"noul","instructions":"Q"}}}`, `{"state":"x","questions":{}}`, `{"state":"x","questions":{"q":{"type":"noul","instructions":" "}}}`, `{"state":"x","questions":{"q":{"type":"choice","instructions":"Q","criteria":[]}}}`, `{"state":"x","questions":{"q":{"type":"score","instructions":"Q","criteria":{}}}}`, `{"state":"x","questions":{"q":{"type":"noul","instructions":"Q","criteria":{"yes":"Y"}}}}`} {
		r := mustRequest(t, s)
		if validateRequest(r, false, "") == nil {
			t.Fatalf("accepted %s", s)
		}
	}
	for _, s := range []string{`false`, `0`, `9007199254740993`, `[]`, `{}`} {
		r := mustRequest(t, `{"state":`+s+`,"questions":{"q":{"type":"noul","instructions":"Q"}}}`)
		if e := validateRequest(r, false, ""); e != nil {
			t.Fatalf("state %s: %v", s, e)
		}
	}
}
func TestResultValidation(t *testing.T) {
	qs := obj(mustRequest(t, batch).data["questions"])
	v := mustDecode(t, validResponse)
	r, e := validateResult(v, qs)
	if e != nil {
		t.Fatal(e)
	}
	if obj(r["usage"])["input_tokens"] != json.Number("9007199254740993") {
		t.Fatal("token precision changed")
	}
	obj(v)["usage"] = map[string]any{}
	r, e = validateResult(v, qs)
	if e != nil || obj(r["usage"])["input_tokens"] != nil {
		t.Fatal("unknown usage")
	}
	cases := []struct{ name, old, new string }{
		{"boolean answer", `"noul":0.9`, `"noul":true`}, {"string answer", `"noul":0.9`, `"noul":"0.9"`}, {"range", `"noul":0.9`, `"noul":1.01`}, {"wrong type", `"type":"noul"`, `"type":"box"`}, {"float usage", `"output_tokens":0`, `"output_tokens":0.0`}, {"negative usage", `"output_tokens":0`, `"output_tokens":-1`}, {"boolean usage", `"output_tokens":0`, `"output_tokens":true`}, {"missing answer", `"yes":`, `"other":`}, {"unoffered choice", `"choice":"z"`, `"choice":"other"`}, {"incomplete map", `"z":1,"a":0`, `"z":1`}, {"total", `"z":1,"a":0`, `"z":0.5,"a":0.4`}, {"negative probability", `"z":1,"a":0`, `"z":1,"a":-0.1`}, {"score range", `"score":0`, `"score":2`}, {"legend", `"1":"High"`, `"2":"High"`}, {"reversed box", `"left":0.2`, `"left":0.99`}, {"box confidence", `"confidence":null`, `"confidence":true`}, {"missing confidence", `,"confidence":null`, ``},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			v := mustDecode(t, strings.Replace(validResponse, c.old, c.new, 1))
			if _, e := validateResult(v, qs); e == nil {
				t.Fatal("accepted invalid result")
			}
		})
	}
	for _, probs := range []string{`"z":0.99,"a":0`, `"z":1,"a":0.01`} {
		v := mustDecode(t, strings.Replace(validResponse, `"z":1,"a":0`, probs, 1))
		if _, e := validateResult(v, qs); e != nil {
			t.Fatal("rounding tolerance rejected", e)
		}
	}
}
func TestAssets(t *testing.T) {
	png := []byte("\x89PNG\r\n\x1a\nfixture")
	r := mustRequest(t, batch)
	r.data["assets"] = map[string]any{"<photo>": map[string]any{"data_base64": base64.StdEncoding.EncodeToString(png)}}
	if e := validateRequest(r, false, ""); e != nil {
		t.Fatal(e)
	}
	expected := map[string]any{"type": "image", "format": "png", "data_base64": base64.StdEncoding.EncodeToString(png)}
	if !reflect.DeepEqual(obj(r.data["assets"])["<photo>"], expected) {
		t.Fatal("normalization")
	}
	for _, a := range []map[string]any{{"path": "/tmp/photo.png"}, {"data_base64": "bad"}, {"data_base64": base64.StdEncoding.EncodeToString(png), "format": "jpeg"}, {"data_base64": base64.StdEncoding.EncodeToString([]byte("not image"))}, {"path": "x", "data_base64": "y"}} {
		r := mustRequest(t, batch)
		r.data["assets"] = map[string]any{"<photo>": a}
		if validateRequest(r, false, "") == nil {
			t.Fatal("invalid asset accepted", a)
		}
	}
	r = mustRequest(t, batch)
	r.data["assets"] = map[string]any{"unused": expected}
	if validateRequest(r, false, "") == nil {
		t.Fatal("unused asset accepted")
	}
}
