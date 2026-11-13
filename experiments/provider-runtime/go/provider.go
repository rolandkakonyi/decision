package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"net/url"
	"os"
	"reflect"
	"strings"
	"time"
)

var httpClient = &http.Client{CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}

func checkURL(s string) error {
	u, e := url.Parse(s)
	if e != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return invalidInput
	}
	local := u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1" || u.Hostname() == "::1"
	if u.Scheme != "https" && !(u.Scheme == "http" && local) {
		return invalidInput
	}
	return nil
}
func configure(o options, p *plugin) options {
	if o.base == "" {
		if p == nil {
			o.base = os.Getenv("TYPESAFE_BASE_URL")
			if o.base == "" {
				o.base = "https://api.typesafe.ai"
			}
		} else {
			o.base = p.BaseURL
		}
	}
	return o
}
func credential(o options, p *plugin) string {
	names := []string{"DECISION_API_KEY"}
	if o.keyEnv != "" {
		names = []string{o.keyEnv}
	} else {
		official := "https://api.typesafe.ai"
		extra := []string{"TYPESAFE_API_KEY", "TYPESAFEAI_API_KEY"}
		if p != nil {
			official = p.BaseURL
			extra = p.Credentials
		}
		u, _ := url.Parse(o.base)
		v, _ := url.Parse(official)
		if u.Scheme == "https" && (u.Port() == "" || u.Port() == "443") && u.Hostname() == v.Hostname() {
			names = append(names, extra...)
		}
	}
	for _, n := range names {
		if v := strings.TrimSpace(os.Getenv(n)); v != "" {
			return v
		}
	}
	return ""
}
func evaluate(ctx context.Context, o options, p *plugin, r *request, paths bool, directory string) (any, error) {
	if e := validateRequest(r, paths, directory); e != nil {
		return nil, e
	}
	_, hasModel := r.data["model"]
	if o.model != "" && !hasModel {
		r.data["model"] = o.model
	} else if _, ok := r.data["model"]; !ok && p != nil && p.Model != "" {
		r.data["model"] = p.Model
	}
	ctx, cancel := context.WithTimeout(ctx, time.Duration(o.timeout*float64(time.Second)))
	defer cancel()
	if p != nil {
		prepared, _, e := p.call(ctx, "prepare", r, nil, "")
		if e != nil {
			return nil, e
		}
		if e = validateRequest(prepared, false, ""); e != nil {
			return nil, providerFailure
		}
		if !reflect.DeepEqual(r.data["state"], prepared.data["state"]) || !reflect.DeepEqual(r.data["questions"], prepared.data["questions"]) || !reflect.DeepEqual(r.data["assets"], prepared.data["assets"]) {
			return nil, providerFailure
		}
		r = prepared
	}
	if o.dry {
		return r.wire(), nil
	}
	key := credential(o, p)
	if key == "" {
		return nil, invalidInput
	}
	started := time.Now()
	var raw any
	var e error
	if p == nil {
		raw, e = typesafe(ctx, o, r, key)
	} else {
		_, raw, e = p.call(ctx, "evaluate", r, map[string]any{"base_url": o.base, "timeout": o.timeout, "account_id": nil}, key)
	}
	if e != nil {
		return nil, e
	}
	result, e := validateResult(raw, obj(r.data["questions"]))
	if e != nil {
		return nil, e
	}
	result["duration_ms"] = float64(time.Since(started).Microseconds()) / 1000
	return result, nil
}
func typesafe(ctx context.Context, o options, r *request, key string) (any, error) {
	body := r.wire()
	delete(body, "assets")
	if _, ok := body["model"]; !ok {
		model := strings.TrimSpace(os.Getenv("TYPESAFE_DEFAULT_MODEL"))
		if model == "" {
			model = "jev-latest"
		}
		body["model"] = model
	}
	contentType := "application/json"
	payload := encoded(body)
	a := obj(r.data["assets"])
	if len(a) > 0 {
		var buffer bytes.Buffer
		w := multipart.NewWriter(&buffer)
		entries := map[string]any{}
		names := sortedKeys(a)
		for i, name := range names {
			entries[name] = map[string]any{"type": "image", "part": fmt.Sprintf("asset-%d", i)}
		}
		body["assets"] = entries
		h := textproto.MIMEHeader{}
		h.Set("Content-Disposition", `form-data; name="request"`)
		h.Set("Content-Type", "application/json")
		part, e := w.CreatePart(h)
		if e != nil {
			return nil, providerFailure
		}
		part.Write(encoded(body))
		for i, name := range names {
			m := obj(a[name])
			raw, _ := base64.StdEncoding.DecodeString(str(m["data_base64"]))
			partName := fmt.Sprintf("asset-%d", i)
			h := textproto.MIMEHeader{}
			h.Set("Content-Disposition", fmt.Sprintf(`form-data; name="%s"; filename="%s"`, partName, partName))
			h.Set("Content-Type", "image/"+str(m["format"]))
			part, e = w.CreatePart(h)
			if e != nil {
				return nil, providerFailure
			}
			part.Write(raw)
		}
		w.Close()
		payload = buffer.Bytes()
		contentType = w.FormDataContentType()
	}
	req, e := http.NewRequestWithContext(ctx, "POST", strings.TrimRight(o.base, "/")+"/v1/systemone", bytes.NewReader(payload))
	if e != nil {
		return nil, invalidInput
	}
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", contentType)
	req.Header.Set("Accept", "application/json")
	danger := len(a) > 0
	for _, v := range obj(r.data["questions"]) {
		if obj(v)["type"] == "box" {
			danger = true
		}
	}
	if danger {
		req.Header.Set("TypeSafe-Danger-Zone-Images", "true")
	}
	resp, e := httpClient.Do(req)
	if e != nil {
		return nil, providerFailure
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, providerFailure
	}
	b, e := readBounded(resp.Body, frameLimit)
	if e != nil {
		return nil, providerFailure
	}
	v, e := decode(b)
	if e != nil {
		return nil, invalidResult
	}
	return v, nil
}
