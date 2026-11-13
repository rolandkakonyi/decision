package main

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"syscall"
	"time"
)

type manifest struct {
	Protocol    int            `json:"protocol_version"`
	Name        string         `json:"name"`
	Version     string         `json:"version"`
	Command     []string       `json:"command"`
	Setup       []string       `json:"setup,omitempty"`
	BaseURL     string         `json:"default_base_url"`
	Model       string         `json:"default_model,omitempty"`
	Credentials []string       `json:"credential_env_vars,omitempty"`
	Constraints map[string]any `json:"schema_constraints,omitempty"`
}
type registration struct {
	Path    string `json:"path"`
	Version string `json:"version"`
	Commit  string `json:"commit,omitempty"`
}
type registry map[string]registration
type plugin struct {
	manifest
	root string
}

var safeName = regexp.MustCompile(`^[a-z][a-z0-9_-]*$`)
var envName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

func reserved(s string) bool {
	return s == "typesafe" || s == "openai" || s == "cloudflare" || s == "gemini"
}
func readBounded(r io.Reader, limit int64) ([]byte, error) {
	b, e := io.ReadAll(io.LimitReader(r, limit+1))
	if e != nil || int64(len(b)) > limit {
		return nil, invalidInput
	}
	return b, nil
}
func loadManifest(root string) (manifest, error) {
	var m manifest
	b, e := os.ReadFile(filepath.Join(root, "decision-provider.json"))
	if e != nil {
		return m, invalidInput
	}
	v, e := decode(b)
	if e != nil || obj(v) == nil {
		return m, invalidInput
	}
	if e = json.Unmarshal(b, &m); e != nil {
		return m, invalidInput
	}
	if m.Protocol != 1 || !safeName.MatchString(m.Name) || reserved(m.Name) || strings.TrimSpace(m.Version) == "" || !validArgv(m.Command) || m.Setup != nil && !validArgv(m.Setup) || checkURL(m.BaseURL) != nil {
		return m, invalidInput
	}
	for _, n := range m.Credentials {
		if !envName.MatchString(n) {
			return m, invalidInput
		}
	}
	return m, nil
}
func validArgv(a []string) bool {
	if len(a) == 0 || strings.TrimSpace(a[0]) == "" {
		return false
	}
	for _, s := range a {
		if strings.ContainsRune(s, 0) {
			return false
		}
	}
	return true
}
func registryPath(o options) string {
	if o.project != "" {
		return o.project
	}
	return filepath.Join(o.home, "registry.json")
}
func readRegistry(path string) (registry, error) {
	b, e := os.ReadFile(path)
	if os.IsNotExist(e) {
		return registry{}, nil
	}
	if e != nil {
		return nil, invalidInput
	}
	v, e := decode(b)
	if e != nil || obj(v) == nil {
		return nil, invalidInput
	}
	var r registry
	if json.Unmarshal(b, &r) != nil {
		return nil, invalidInput
	}
	return r, nil
}
func registrations(o options) (registry, error) {
	r, e := readRegistry(filepath.Join(o.home, "registry.json"))
	if e != nil {
		return nil, e
	}
	if o.project != "" {
		p, e := readRegistry(o.project)
		if e != nil {
			return nil, e
		}
		for k, v := range p {
			r[k] = v
		}
	}
	return r, nil
}
func writeRegistry(path string, r registry) error {
	if e := os.MkdirAll(filepath.Dir(path), 0700); e != nil {
		return invalidInput
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".registry-")
	if e != nil {
		return invalidInput
	}
	defer os.Remove(f.Name())
	if _, e = f.Write(encoded(r)); e == nil {
		e = f.Close()
	} else {
		f.Close()
	}
	if e != nil {
		return invalidInput
	}
	if os.Rename(f.Name(), path) != nil {
		return invalidInput
	}
	return nil
}
func copyTree(src, dst string) error {
	return filepath.WalkDir(src, func(p string, d os.DirEntry, e error) error {
		if e != nil {
			return invalidInput
		}
		rel, e := filepath.Rel(src, p)
		if e != nil {
			return invalidInput
		}
		if d.Type()&os.ModeSymlink != 0 {
			return invalidInput
		}
		if d.IsDir() {
			return os.MkdirAll(filepath.Join(dst, rel), 0700)
		}
		info, e := d.Info()
		if e != nil || !info.Mode().IsRegular() {
			return invalidInput
		}
		f, e := os.Open(p)
		if e != nil {
			return invalidInput
		}
		defer f.Close()
		b, e := readBounded(f, 128<<20)
		if e != nil {
			return e
		}
		return os.WriteFile(filepath.Join(dst, rel), b, info.Mode().Perm()&0777)
	})
}
func extractArchive(b []byte, dst string) error {
	z, e := gzip.NewReader(bytes.NewReader(b))
	if e != nil {
		return invalidInput
	}
	defer z.Close()
	t := tar.NewReader(z)
	total := int64(0)
	for {
		h, e := t.Next()
		if e == io.EOF {
			break
		}
		if e != nil {
			return invalidInput
		}
		p := filepath.Clean(h.Name)
		if filepath.IsAbs(p) || p == ".." || strings.HasPrefix(p, ".."+string(filepath.Separator)) {
			return invalidInput
		}
		target := filepath.Join(dst, p)
		switch h.Typeflag {
		case tar.TypeDir:
			if os.MkdirAll(target, 0700) != nil {
				return invalidInput
			}
		case tar.TypeReg:
			total += h.Size
			if h.Size < 0 || total > 256<<20 {
				return invalidInput
			}
			if os.MkdirAll(filepath.Dir(target), 0700) != nil {
				return invalidInput
			}
			f, e := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, os.FileMode(h.Mode)&0777)
			if e != nil {
				return invalidInput
			}
			_, e = io.Copy(f, t)
			f.Close()
			if e != nil {
				return invalidInput
			}
		default:
			return invalidInput
		}
	}
	return nil
}
func findPackage(stage string) (string, error) {
	if _, e := os.Stat(filepath.Join(stage, "decision-provider.json")); e == nil {
		return stage, nil
	}
	entries, e := os.ReadDir(stage)
	if e == nil && len(entries) == 1 && entries[0].IsDir() {
		p := filepath.Join(stage, entries[0].Name())
		if _, e := os.Stat(filepath.Join(p, "decision-provider.json")); e == nil {
			return p, nil
		}
	}
	return "", invalidInput
}
func minimalEnv() []string {
	r := []string{}
	for _, n := range []string{"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"} {
		if v, ok := os.LookupEnv(n); ok {
			r = append(r, n+"="+v)
		}
	}
	return r
}

type cappedBuffer struct {
	buffer bytes.Buffer
	limit  int
}

func (b *cappedBuffer) Write(p []byte) (int, error) {
	if b.buffer.Len()+len(p) > b.limit {
		return 0, providerFailure
	}
	return b.buffer.Write(p)
}
func execute(ctx context.Context, root string, argv []string, input []byte, limit int) ([]byte, error) {
	c := exec.CommandContext(ctx, argv[0], argv[1:]...)
	c.Dir = root
	c.Env = minimalEnv()
	c.Stdin = bytes.NewReader(input)
	b := &cappedBuffer{limit: limit}
	c.Stdout = b
	c.Stderr = io.Discard
	c.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	c.Cancel = func() error {
		if c.Process != nil {
			return syscall.Kill(-c.Process.Pid, syscall.SIGKILL)
		}
		return nil
	}
	c.WaitDelay = time.Second
	e := c.Run()
	if c.Process != nil {
		_ = syscall.Kill(-c.Process.Pid, syscall.SIGKILL)
	}
	if e != nil {
		return nil, providerFailure
	}
	return b.buffer.Bytes(), nil
}
func install(ctx context.Context, o options, source, checksum string) (any, error) {
	artifacts := filepath.Join(o.home, "artifacts")
	if os.MkdirAll(artifacts, 0700) != nil {
		return nil, invalidInput
	}
	stage, e := os.MkdirTemp(artifacts, "stage-")
	if e != nil {
		return nil, invalidInput
	}
	defer os.RemoveAll(stage)
	commit := ""
	if strings.HasPrefix(source, "git+") {
		u, e := url.Parse(strings.TrimPrefix(source, "git+"))
		if e != nil || u.Fragment == "" || u.User != nil || (u.Scheme != "https" && !(u.Scheme == "file" && u.Host == "" && filepath.IsAbs(u.Path))) {
			return nil, invalidInput
		}
		ref := u.Fragment
		if strings.HasPrefix(ref, "-") {
			return nil, invalidInput
		}
		u.Fragment = ""
		repo := filepath.Join(stage, "repo")
		if _, e = execute(ctx, stage, []string{"git", "-c", "http.followRedirects=false", "clone", "--quiet", "--no-checkout", "--", u.String(), repo}, nil, 1<<20); e != nil {
			return nil, e
		}
		if _, e = execute(ctx, repo, []string{"git", "checkout", "--quiet", "--detach", ref}, nil, 1<<20); e != nil {
			return nil, e
		}
		b, e := execute(ctx, repo, []string{"git", "rev-parse", "HEAD"}, nil, 1024)
		if e != nil {
			return nil, e
		}
		commit = strings.TrimSpace(string(b))
		os.RemoveAll(filepath.Join(repo, ".git"))
		stage = repo
	} else if strings.HasPrefix(source, "https://") || strings.HasPrefix(source, "http://") {
		if checkURL(source) != nil || len(checksum) != 64 {
			return nil, invalidInput
		}
		want, e := hex.DecodeString(checksum)
		if e != nil {
			return nil, invalidInput
		}
		req, e := http.NewRequestWithContext(ctx, "GET", source, nil)
		if e != nil {
			return nil, invalidInput
		}
		resp, e := httpClient.Do(req)
		if e != nil {
			return nil, providerFailure
		}
		defer resp.Body.Close()
		if resp.StatusCode != 200 {
			return nil, providerFailure
		}
		b, e := readBounded(resp.Body, 128<<20)
		if e != nil {
			return nil, e
		}
		sum := sha256.Sum256(b)
		if !bytes.Equal(want, sum[:]) {
			return nil, invalidInput
		}
		if e = extractArchive(b, stage); e != nil {
			return nil, e
		}
	} else {
		source, e = filepath.Abs(source)
		if e != nil {
			return nil, invalidInput
		}
		info, e := os.Lstat(source)
		if e != nil || info.Mode()&os.ModeSymlink != 0 {
			return nil, invalidInput
		}
		if !info.IsDir() {
			if filepath.Base(source) != "decision-provider.json" {
				return nil, invalidInput
			}
			source = filepath.Dir(source)
		}
		if e = copyTree(source, stage); e != nil {
			return nil, e
		}
	}
	if e = filepath.WalkDir(stage, func(_ string, entry os.DirEntry, err error) error {
		if err != nil || entry.Type()&os.ModeSymlink != 0 {
			return invalidInput
		}
		return nil
	}); e != nil {
		return nil, invalidInput
	}
	root, e := findPackage(stage)
	if e != nil {
		return nil, e
	}
	m, e := loadManifest(root)
	if e != nil {
		return nil, e
	}
	// Artifacts have unique identities even for explicit reinstalls of one version.
	final, e := os.MkdirTemp(artifacts, m.Name+"-")
	if e != nil {
		return nil, invalidInput
	}
	os.Remove(final)
	if e = os.Rename(root, final); e != nil {
		return nil, invalidInput
	}
	activated := false
	defer func() {
		if !activated {
			os.RemoveAll(final)
		}
	}()
	if m.Setup != nil {
		if _, e = execute(ctx, final, m.Setup, nil, 1<<20); e != nil {
			return nil, providerFailure
		}
	}
	// Setup is trusted executable code, but cannot silently change the manifest.
	after, e := loadManifest(final)
	if e != nil || !reflect.DeepEqual(m, after) {
		return nil, invalidInput
	}
	r, e := readRegistry(registryPath(o))
	if e != nil {
		return nil, e
	}
	r[m.Name] = registration{Path: final, Version: m.Version, Commit: commit}
	if e = writeRegistry(registryPath(o), r); e != nil {
		return nil, e
	}
	activated = true
	return map[string]any{"name": m.Name, "version": m.Version, "restart_required": true}, nil
}
func listProviders(o options) (any, error) {
	r, e := registrations(o)
	if e != nil {
		return nil, e
	}
	a := []any{}
	for _, name := range sortedKeys(r) {
		a = append(a, map[string]any{"name": name, "version": r[name].Version})
	}
	return a, nil
}
func removeProvider(o options, name string) (any, error) {
	if !safeName.MatchString(name) || reserved(name) {
		return nil, invalidInput
	}
	r, e := readRegistry(registryPath(o))
	if e != nil {
		return nil, e
	}
	if _, ok := r[name]; !ok {
		return nil, invalidInput
	}
	delete(r, name)
	if e = writeRegistry(registryPath(o), r); e != nil {
		return nil, e
	}
	return map[string]any{"name": name, "removed": true}, nil
}
func (p *plugin) call(ctx context.Context, method string, req *request, settings map[string]any, key string) (*request, any, error) {
	v := map[string]any{"protocol_version": 1, "method": method}
	if req != nil {
		v["request"] = req.wire()
	}
	if method == "evaluate" {
		v["settings"] = settings
		v["credential"] = key
	}
	b, e := execute(ctx, p.root, p.Command, append(encoded(v), '\n'), frameLimit)
	if e != nil {
		return nil, nil, e
	}
	b = bytes.TrimSuffix(b, []byte("\n"))
	b = bytes.TrimSuffix(b, []byte("\r"))
	if bytes.ContainsAny(b, "\r\n") {
		return nil, nil, providerFailure
	}
	decoded, e := decode(b)
	m := obj(decoded)
	if e != nil || m == nil || m["protocol_version"] != json.Number("1") {
		return nil, nil, providerFailure
	}
	if m["ok"] != true {
		if obj(m["error"])["code"] == "invalid_request" {
			return nil, nil, invalidInput
		}
		return nil, nil, providerFailure
	}
	if method == "prepare" {
		raw := map[string]json.RawMessage{}
		json.Unmarshal(b, &raw)
		r, e := parseRequest(raw["result"])
		return r, nil, e
	}
	return nil, m["result"], nil
}
func resolve(ctx context.Context, o options) (*plugin, error) {
	for _, name := range o.disabled {
		if name == o.provider {
			return nil, errors.New("Selected provider is disabled")
		}
	}
	if o.provider == "typesafe" {
		return nil, nil
	}
	if !safeName.MatchString(o.provider) || reserved(o.provider) {
		return nil, invalidInput
	}
	r, e := registrations(o)
	if e != nil {
		return nil, e
	}
	reg, ok := r[o.provider]
	if !ok {
		return nil, invalidInput
	}
	m, e := loadManifest(reg.Path)
	if e != nil || m.Name != o.provider || m.Version != reg.Version {
		return nil, invalidInput
	}
	p := &plugin{m, reg.Path}
	_, v, e := p.call(ctx, "describe", nil, nil, "")
	if e != nil {
		return nil, e
	}
	if obj(v)["name"] != m.Name || obj(v)["version"] != m.Version {
		return nil, providerFailure
	}
	return p, nil
}
