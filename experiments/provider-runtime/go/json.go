package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"
)

const frameLimit = 64 << 20

var invalidInput = errors.New("Invalid request or JSON input")
var invalidResult = errors.New("Provider returned invalid decision data; no answers returned")
var providerFailure = errors.New("Provider operation failed; no answers returned")

// Token decoding rejects duplicates before any library can overwrite them, and
// keeps numbers lexical until the field's numeric contract is checked.
func decode(data []byte) (any, error) {
	if len(data) > frameLimit || !utf8.Valid(data) {
		return nil, invalidInput
	}
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	v, e := readValue(d)
	if e != nil {
		return nil, invalidInput
	}
	if _, e = d.Token(); e != io.EOF {
		return nil, invalidInput
	}
	return v, nil
}
func readValue(d *json.Decoder) (any, error) {
	t, e := d.Token()
	if e != nil {
		return nil, e
	}
	switch t {
	case json.Delim('{'):
		m := map[string]any{}
		for d.More() {
			k, e := d.Token()
			if e != nil {
				return nil, e
			}
			s, ok := k.(string)
			if !ok {
				return nil, invalidInput
			}
			if _, ok = m[s]; ok {
				return nil, invalidInput
			}
			v, e := readValue(d)
			if e != nil {
				return nil, e
			}
			m[s] = v
		}
		_, e = d.Token()
		return m, e
	case json.Delim('['):
		a := []any{}
		for d.More() {
			v, e := readValue(d)
			if e != nil {
				return nil, e
			}
			a = append(a, v)
		}
		_, e = d.Token()
		return a, e
	}
	if n, ok := t.(json.Number); ok && strings.ContainsAny(string(n), ".eE") {
		f, e := strconv.ParseFloat(string(n), 64)
		if e != nil || math.IsInf(f, 0) || math.IsNaN(f) {
			return nil, invalidInput
		}
	}
	return t, nil
}
func obj(v any) map[string]any { m, _ := v.(map[string]any); return m }
func str(v any) string         { s, _ := v.(string); return s }
func encoded(v any) []byte     { b, _ := json.Marshal(v); return b }
func fields(m map[string]any, allowed ...string) bool {
	for k := range m {
		found := false
		for _, a := range allowed {
			if k == a {
				found = true
			}
		}
		if !found {
			return false
		}
	}
	return true
}

type request struct {
	data map[string]any
	raw  map[string]json.RawMessage
}

func parseRequest(b []byte) (*request, error) {
	v, e := decode(b)
	if e != nil || obj(v) == nil {
		return nil, invalidInput
	}
	r := &request{data: obj(v)}
	_ = json.Unmarshal(b, &r.raw)
	return r, nil
}
func (r *request) wire() map[string]any {
	m := map[string]any{}
	for k, v := range r.data {
		m[k] = v
	}
	// Preserve insertion order of choices and arbitrary state, including integers.
	for _, k := range []string{"state", "questions"} {
		if b, ok := r.raw[k]; ok {
			m[k] = b
		}
	}
	return m
}
