package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"strings"
)

func nonempty(v any) bool { s, ok := v.(string); return ok && strings.TrimSpace(s) != "" }
func validateRequest(r *request, paths bool, directory string) error {
	p := r.data
	if p["state"] == nil || !fields(p, "state", "questions", "model", "assets") {
		return invalidInput
	}
	if v, ok := p["model"]; ok && !nonempty(v) {
		return invalidInput
	}
	qs := obj(p["questions"])
	if len(qs) == 0 {
		return invalidInput
	}
	for name, v := range qs {
		q := obj(v)
		if strings.TrimSpace(name) == "" || !fields(q, "type", "instructions", "criteria") || !nonempty(q["instructions"]) {
			return invalidInput
		}
		c := q["criteria"]
		switch q["type"] {
		case "noul":
			if c != nil && (obj(c) == nil || !fields(obj(c), "true", "false")) {
				return invalidInput
			}
		case "choice":
			if len(obj(c)) == 0 {
				return invalidInput
			}
			for k := range obj(c) {
				if strings.TrimSpace(k) == "" {
					return invalidInput
				}
			}
		case "score":
			a, ok := c.([]any)
			if !ok || len(a) == 0 {
				return invalidInput
			}
		case "box":
		default:
			return invalidInput
		}
	}
	assets, has := p["assets"]
	if !has {
		return nil
	}
	a := obj(assets)
	if len(a) == 0 {
		return invalidInput
	}
	total := 0
	var refs []string
	collectStrings(p["state"], &refs)
	for _, v := range qs {
		q := obj(v)
		collectStrings(q["instructions"], &refs)
		collectStrings(q["criteria"], &refs)
	}
	for name, v := range a {
		m := obj(v)
		if strings.TrimSpace(name) == "" || len(name) > 1024 || m == nil || !fields(m, "path", "data_base64", "format", "type") {
			return invalidInput
		}
		if t, ok := m["type"]; ok && t != "image" {
			return invalidInput
		}
		if f, ok := m["format"]; ok && f != "png" && f != "jpg" && f != "jpeg" {
			return invalidInput
		}
		_, path := m["path"]
		_, data := m["data_base64"]
		if path == data || path && !paths {
			return invalidInput
		}
		found := false
		for _, s := range refs {
			if strings.Contains(s, name) {
				found = true
				break
			}
		}
		if !found {
			return invalidInput
		}
		var raw []byte
		var e error
		if path {
			if !nonempty(m["path"]) {
				return invalidInput
			}
			p := str(m["path"])
			if strings.HasPrefix(p, "~/") {
				home, _ := os.UserHomeDir()
				p = filepath.Join(home, p[2:])
			}
			if !filepath.IsAbs(p) {
				p = filepath.Join(directory, p)
			}
			f, e := os.Open(p)
			if e != nil {
				return invalidInput
			}
			raw, e = readBounded(f, 16<<20)
			f.Close()
			if e != nil {
				return invalidInput
			}
		} else {
			if !nonempty(m["data_base64"]) || len(str(m["data_base64"])) > 4*((16*1024*1024+2)/3) {
				return invalidInput
			}
			s := str(m["data_base64"])
			if strings.ContainsAny(s, "\r\n") {
				return invalidInput
			}
			raw, e = base64.StdEncoding.Strict().DecodeString(s)
			if e != nil {
				return invalidInput
			}
		}
		if len(raw) > 16<<20 {
			return invalidInput
		}
		format := ""
		if bytes.HasPrefix(raw, []byte("\x89PNG\r\n\x1a\n")) {
			format = "png"
		} else if bytes.HasPrefix(raw, []byte{255, 216, 255}) {
			format = "jpeg"
		} else {
			return invalidInput
		}
		if f, ok := m["format"]; ok && strings.ReplaceAll(str(f), "jpg", "jpeg") != format {
			return invalidInput
		}
		total += len(raw)
		if total > 32<<20 {
			return invalidInput
		}
		// The executable boundary never receives local paths.
		a[name] = map[string]any{"type": "image", "format": format, "data_base64": base64.StdEncoding.EncodeToString(raw)}
	}
	return nil
}
func collectStrings(v any, out *[]string) {
	switch x := v.(type) {
	case string:
		*out = append(*out, x)
	case map[string]any:
		for _, v := range x {
			collectStrings(v, out)
		}
	case []any:
		for _, v := range x {
			collectStrings(v, out)
		}
	}
}
func num(v any, max float64) (float64, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	f, e := n.Float64()
	return f, e == nil && !math.IsNaN(f) && !math.IsInf(f, 0) && f >= 0 && f <= max
}
func sameKeys(a, b map[string]any) bool {
	if len(a) != len(b) {
		return false
	}
	for k := range a {
		if _, ok := b[k]; !ok {
			return false
		}
	}
	return true
}
func validateResult(v any, qs map[string]any) (map[string]any, error) {
	r := obj(v)
	if !nonempty(r["model"]) {
		return nil, invalidResult
	}
	as := obj(r["answers"])
	if as == nil || !sameKeys(as, qs) {
		return nil, invalidResult
	}
	normalized := map[string]any{}
	for id, v := range qs {
		q := obj(v)
		a := obj(as[id])
		if a == nil || a["type"] != q["type"] {
			return nil, invalidResult
		}
		out := map[string]any{"type": a["type"]}
		switch q["type"] {
		case "noul":
			if _, ok := num(a["noul"], 1); !ok {
				return nil, invalidResult
			}
			out["noul"] = a["noul"]
		case "choice", "score":
			if _, ok := num(a["confidence"], 1); !ok {
				return nil, invalidResult
			}
			out["confidence"] = a["confidence"]
			expected := obj(q["criteria"])
			if q["type"] == "score" {
				expected = map[string]any{}
				for i := range q["criteria"].([]any) {
					expected[big.NewInt(int64(i)).String()] = nil
				}
			}
			ps := obj(a["probabilities"])
			if ps == nil || !sameKeys(ps, expected) {
				return nil, invalidResult
			}
			sum := 0.0
			comp := 0.0
			for _, v := range ps {
				f, ok := num(v, 1)
				if !ok {
					return nil, invalidResult
				}
				y := f - comp
				t := sum + y
				comp = (t - sum) - y
				sum = t
			}
			if math.Abs(sum-1) > 0.02 {
				return nil, invalidResult
			}
			out["probabilities"] = ps
			if q["type"] == "choice" {
				s, ok := a["choice"].(string)
				if !ok {
					return nil, invalidResult
				}
				if _, ok = expected[s]; !ok {
					return nil, invalidResult
				}
				out["choice"] = s
			} else {
				if _, ok := num(a["score"], float64(len(expected)-1)); !ok {
					return nil, invalidResult
				}
				lg := obj(a["legend"])
				if lg == nil || !sameKeys(lg, expected) {
					return nil, invalidResult
				}
				for _, v := range lg {
					switch v.(type) {
					case string, map[string]any, []any:
					default:
						return nil, invalidResult
					}
				}
				out["score"] = a["score"]
				out["legend"] = lg
			}
		case "box":
			b := obj(a["box"])
			edges := map[string]float64{}
			for _, k := range []string{"left", "top", "right", "bottom"} {
				f, ok := num(b[k], 1)
				if !ok {
					return nil, invalidResult
				}
				edges[k] = f
			}
			if edges["left"] > edges["right"] || edges["top"] > edges["bottom"] {
				return nil, invalidResult
			}
			if _, exists := a["confidence"]; !exists {
				return nil, invalidResult
			}
			if a["confidence"] != nil {
				if _, ok := num(a["confidence"], 1); !ok {
					return nil, invalidResult
				}
			}
			out["confidence"] = a["confidence"]
			out["box"] = map[string]any{"left": b["left"], "top": b["top"], "right": b["right"], "bottom": b["bottom"]}
		}
		normalized[id] = out
	}
	u := obj(r["usage"])
	if u == nil {
		return nil, invalidResult
	}
	usage := map[string]any{}
	for _, k := range []string{"input_tokens", "output_tokens"} {
		v := u[k]
		if v != nil {
			n, ok := v.(json.Number)
			if !ok {
				return nil, invalidResult
			}
			i, ok := new(big.Int).SetString(string(n), 10)
			if !ok || i.Sign() < 0 {
				return nil, invalidResult
			}
		}
		usage[k] = v
	}
	return map[string]any{"model": r["model"], "answers": normalized, "usage": usage}, nil
}
