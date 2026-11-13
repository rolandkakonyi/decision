package main

import (
	"context"
	"fmt"
	"math"
	"os"
	"os/signal"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

type options struct {
	provider, model, base, keyEnv, home, project string
	timeout                                      float64
	disabled                                     []string
	dry, management                              bool
}

func defaults() options {
	home, _ := os.UserHomeDir()
	p := os.Getenv("DECISION_PROVIDER")
	if p == "" {
		p = "typesafe"
	}
	return options{provider: p, model: os.Getenv("DECISION_MODEL"), base: os.Getenv("DECISION_BASE_URL"), keyEnv: os.Getenv("DECISION_API_KEY_ENV"), home: filepath.Join(home, ".local", "share", "decision-prototype-go"), timeout: 30}
}
func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
func parseOptions(args []string) (options, []string, string, error) {
	o := defaults()
	pos := []string{}
	checksum := ""
	for i := 0; i < len(args); i++ {
		a := args[i]
		if !strings.HasPrefix(a, "--") {
			pos = append(pos, a)
			continue
		}
		name, value, eq := strings.Cut(a, "=")
		switch name {
		case "--json":
			continue
		case "--dry-run":
			o.dry = true
			continue
		case "--management":
			o.management = true
			continue
		}
		if !eq {
			i++
			if i >= len(args) {
				return o, nil, "", invalidInput
			}
			value = args[i]
		}
		switch name {
		case "--provider":
			o.provider = value
		case "--model":
			o.model = value
		case "--base-url":
			o.base = value
		case "--api-key-env":
			o.keyEnv = value
		case "--timeout":
			f, e := strconv.ParseFloat(value, 64)
			if e != nil {
				return o, nil, "", invalidInput
			}
			o.timeout = f
		case "--disable-provider":
			o.disabled = append(o.disabled, value)
		case "--plugin-home":
			o.home = value
		case "--project-config":
			o.project = value
		case "--sha256":
			checksum = value
		default:
			return o, nil, "", invalidInput
		}
	}
	if math.IsNaN(o.timeout) || math.IsInf(o.timeout, 0) || o.timeout <= 0 || o.timeout > float64(math.MaxInt64)/float64(time.Second) {
		return o, nil, "", invalidInput
	}
	if o.keyEnv != "" && !envName.MatchString(o.keyEnv) {
		return o, nil, "", invalidInput
	}
	var e error
	o.home, e = filepath.Abs(o.home)
	if e != nil {
		return o, nil, "", invalidInput
	}
	if o.project != "" {
		o.project, e = filepath.Abs(o.project)
		if e != nil {
			return o, nil, "", invalidInput
		}
	}
	return o, pos, checksum, nil
}
func help() {
	fmt.Println("decision prototype 1\nUsage: decision run REQUEST.json --json [provider options]\n       decision mcp [provider options] [--management]\n       decision providers install SOURCE [--sha256 HEX] [--plugin-home DIRECTORY] [--project-config FILE]\n       decision providers list|remove [NAME] [--plugin-home DIRECTORY] [--project-config FILE]\nProvider options: --provider NAME --model MODEL --base-url URL --api-key-env NAME --timeout SECONDS --disable-provider NAME --plugin-home DIRECTORY --project-config FILE\nRun options: --dry-run --json")
}
func runMain(ctx context.Context, args []string) error {
	if len(args) == 1 && args[0] == "--version" {
		fmt.Println("decision prototype 1")
		return nil
	}
	if len(args) == 0 {
		return invalidInput
	}
	for _, a := range args {
		if a == "--help" || a == "-h" {
			help()
			return nil
		}
	}
	command := args[0]
	o, pos, checksum, e := parseOptions(args[1:])
	if e != nil {
		return e
	}
	if command == "providers" {
		if len(pos) < 1 {
			return invalidInput
		}
		operation := pos[0]
		var result any
		ctx, cancel := context.WithTimeout(ctx, time.Duration(o.timeout*float64(time.Second)))
		defer cancel()
		switch operation {
		case "install":
			if len(pos) != 2 {
				return invalidInput
			}
			result, e = install(ctx, o, pos[1], checksum)
		case "list":
			if len(pos) != 1 {
				return invalidInput
			}
			result, e = listProviders(o)
		case "remove":
			if len(pos) != 2 {
				return invalidInput
			}
			result, e = removeProvider(o, pos[1])
		default:
			return invalidInput
		}
		if e == nil {
			fmt.Println(string(encoded(result)))
		}
		return e
	}
	if command != "run" && command != "mcp" {
		return invalidInput
	}
	if command == "mcp" && len(pos) != 0 || command == "run" && len(pos) != 1 || command == "run" && o.management {
		return invalidInput
	}
	if command == "mcp" && o.management {
		return serve(ctx, o, nil)
	}
	resolveCtx, cancel := context.WithTimeout(ctx, time.Duration(o.timeout*float64(time.Second)))
	p, e := resolve(resolveCtx, o)
	cancel()
	if e != nil {
		return e
	}
	o = configure(o, p)
	if checkURL(o.base) != nil {
		return invalidInput
	}
	if command == "mcp" {
		return serve(ctx, o, p)
	}
	var b []byte
	directory, _ := os.Getwd()
	if pos[0] == "-" {
		b, e = readBounded(os.Stdin, frameLimit)
	} else {
		f, err := os.Open(pos[0])
		if err != nil {
			return invalidInput
		}
		b, e = readBounded(f, frameLimit)
		f.Close()
		abs, _ := filepath.Abs(pos[0])
		directory = filepath.Dir(abs)
	}
	if e != nil {
		return invalidInput
	}
	r, e := parseRequest(b)
	if e != nil {
		return e
	}
	result, e := evaluate(ctx, o, p, r, true, directory)
	if e == nil {
		fmt.Println(string(encoded(result)))
	}
	return e
}
func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	e := runMain(ctx, os.Args[1:])
	if e != nil {
		exit := 1
		if e == invalidInput || strings.HasPrefix(e.Error(), "Selected provider is disabled") {
			exit = 2
		}
		message := e.Error()
		if e != invalidInput && e != invalidResult && e != providerFailure && !strings.HasPrefix(message, "Selected provider is disabled") {
			message = "Provider operation failed; no answers returned"
		}
		fmt.Fprintln(os.Stderr, "decision: "+message)
		os.Exit(exit)
	}
}

func operationContext(ctx context.Context, o options) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, time.Duration(o.timeout*float64(time.Second)))
}
