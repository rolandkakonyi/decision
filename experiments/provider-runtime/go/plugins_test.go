package main

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestPluginProcess(t *testing.T) {
	args := os.Args
	mode := ""
	for i, a := range args {
		if a == "--fixture" && i+1 < len(args) {
			mode = args[i+1]
		}
	}
	if mode == "" {
		return
	}
	if mode == "setup" {
		os.WriteFile("setup-ran", []byte("ready"), 0600)
		os.Exit(0)
	}
	if mode == "setup-fail" {
		os.Exit(7)
	}
	b, e := readBounded(os.Stdin, frameLimit)
	if e != nil {
		os.Exit(9)
	}
	message, e := decode(bytes.TrimSpace(b))
	if e != nil {
		os.Exit(9)
	}
	m := obj(message)
	var result any
	switch m["method"] {
	case "describe":
		data, _ := os.ReadFile("decision-provider.json")
		var manifest manifest
		json.Unmarshal(data, &manifest)
		result = map[string]any{"name": manifest.Name, "version": manifest.Version}
	case "prepare":
		result = m["request"]
	case "evaluate":
		if os.Getenv("UNRELATED_SECRET") != "" || os.Getenv("FIXTURE_KEY") != "" || m["credential"] != "fixture-key" {
			os.Exit(8)
		}
		result, _ = decode([]byte(validResponse))
	default:
		os.Exit(9)
	}
	os.Stdout.Write(append(encoded(map[string]any{"protocol_version": 1, "ok": true, "result": result}), '\n'))
	os.Exit(0)
}
func fixturePackage(t *testing.T, dir, version, setup string) string {
	t.Helper()
	os.MkdirAll(dir, 0700)
	exe, e := os.Executable()
	if e != nil {
		t.Fatal(e)
	}
	m := manifest{Protocol: 1, Name: "fixture", Version: version, Command: []string{exe, "-test.run=TestPluginProcess", "--", "--fixture", "provider"}, Setup: []string{exe, "-test.run=TestPluginProcess", "--", "--fixture", setup}, BaseURL: "https://api.example.test", Model: "fixture"}
	if e := os.WriteFile(filepath.Join(dir, "decision-provider.json"), encoded(m), 0600); e != nil {
		t.Fatal(e)
	}
	return dir
}
func TestInstallSetupRollbackAndPinnedArtifact(t *testing.T) {
	o := defaults()
	o.home = filepath.Join(t.TempDir(), "home")
	o.provider = "fixture"
	o.model = ""
	o.keyEnv = "FIXTURE_KEY"
	t.Setenv("FIXTURE_KEY", "fixture-key")
	t.Setenv("UNRELATED_SECRET", "do-not-forward")
	source := fixturePackage(t, filepath.Join(t.TempDir(), "source"), "1.0.0", "setup")
	ctx := context.Background()
	if _, e := install(ctx, o, source, ""); e != nil {
		t.Fatal(e)
	}
	p, e := resolve(ctx, o)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = os.Stat(filepath.Join(p.root, "setup-ran")); e != nil {
		t.Fatal("setup not run")
	}
	if _, e = evaluate(ctx, configure(o, p), p, mustRequest(t, batch), false, ""); e != nil {
		t.Fatal(e)
	}
	fixturePackage(t, source, "2.0.0", "setup-fail")
	if _, e = install(ctx, o, source, ""); e == nil {
		t.Fatal("failed setup accepted")
	}
	p2, e := resolve(ctx, o)
	if e != nil || p2.Version != "1.0.0" || p.root != p2.root {
		t.Fatal("registration lost", e)
	}
	fixturePackage(t, source, "2.0.0", "setup")
	if _, e = install(ctx, o, source, ""); e != nil {
		t.Fatal(e)
	}
	p2, e = resolve(ctx, o)
	if e != nil || p2.Version != "2.0.0" || p.root == p2.root {
		t.Fatal("not versioned", e)
	}
	if _, e = removeProvider(o, "fixture"); e != nil {
		t.Fatal(e)
	}
	if _, e = evaluate(ctx, configure(o, p), p, mustRequest(t, batch), false, ""); e != nil {
		t.Fatal("pinned artifact removed", e)
	}
}
func TestProjectExplicitAndDisableBeforeLaunch(t *testing.T) {
	o := defaults()
	o.home = filepath.Join(t.TempDir(), "home")
	o.project = filepath.Join(t.TempDir(), "project.json")
	source := fixturePackage(t, filepath.Join(t.TempDir(), "source"), "1.0.0", "setup")
	if _, e := install(context.Background(), o, source, ""); e != nil {
		t.Fatal(e)
	}
	without := o
	without.project = ""
	v, e := listProviders(without)
	if e != nil || len(v.([]any)) != 0 {
		t.Fatal("project implicitly loaded")
	}
	o.provider = "fixture"
	o.disabled = []string{"fixture"}
	r, _ := readRegistry(o.project)
	reg := r["fixture"]
	os.Remove(filepath.Join(reg.Path, "decision-provider.json"))
	if _, e = resolve(context.Background(), o); e == nil || !strings.Contains(e.Error(), "disabled") {
		t.Fatal("disabled plugin loaded", e)
	}
}
func TestUnsafePackageAndArchive(t *testing.T) {
	source := fixturePackage(t, filepath.Join(t.TempDir(), "source"), "1.0.0", "setup")
	if e := os.Symlink("/tmp", filepath.Join(source, "link")); e != nil {
		t.Fatal(e)
	}
	if e := copyTree(source, t.TempDir()); e == nil {
		t.Fatal("copied symlink")
	}
	for _, h := range []*tar.Header{{Name: "../escape", Mode: 0600, Size: 1, Typeflag: tar.TypeReg}, {Name: "link", Typeflag: tar.TypeSymlink, Linkname: "/tmp"}, {Name: "link", Typeflag: tar.TypeLink, Linkname: "file"}} {
		var b bytes.Buffer
		g := gzip.NewWriter(&b)
		w := tar.NewWriter(g)
		w.WriteHeader(h)
		if h.Size > 0 {
			w.Write([]byte("x"))
		}
		w.Close()
		g.Close()
		if extractArchive(b.Bytes(), t.TempDir()) == nil {
			t.Fatal("unsafe archive accepted", h.Name)
		}
	}
}
func TestExecuteTimeoutAndOutputLimit(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	start := time.Now()
	if _, e := execute(ctx, t.TempDir(), []string{"sh", "-c", "sleep 60 & wait"}, nil, 1024); e == nil {
		t.Fatal("timeout ignored")
	}
	if time.Since(start) > 2*time.Second {
		t.Fatal("timeout did not stop group")
	}
	if _, e := execute(context.Background(), t.TempDir(), []string{"sh", "-c", "printf 123456789"}, nil, 4); e == nil {
		t.Fatal("output limit ignored")
	}
}
