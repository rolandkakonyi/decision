#!/bin/sh
set -eu

decision_build_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
decision_build_root=$(CDPATH= cd -- "$decision_build_dir/../../.." && pwd)
decision_output=${1:-"$decision_build_root/.state/provider-prototype-go"}
mkdir -p "$decision_output"
decision_output=$(CDPATH= cd -- "$decision_output" && pwd)
cd "$decision_build_dir"

GOTOOLCHAIN=local CGO_ENABLED=0 go build -mod=readonly -buildvcs=false -trimpath \
  -ldflags='-s -w -buildid=' -o "$decision_output/decision" .
(cd "$decision_output"; go version -m decision) > "$decision_output/dependencies.txt"
mkdir -p "$decision_output/licenses"
cp -f "$decision_build_root/LICENSE" "$decision_output/licenses/decision.txt"
decision_go_root=$(go env GOROOT)
if test -f "$decision_go_root/LICENSE"; then
  cp -f "$decision_go_root/LICENSE" "$decision_output/licenses/go.txt"
else
  cp -f "$decision_go_root/../LICENSE" "$decision_output/licenses/go.txt"
fi
go list -mod=readonly -deps -f '{{with .Module}}{{if not .Main}}{{.Path}}{{end}}{{end}}' . |
  sort -u | while IFS= read -r decision_module; do
    test -n "$decision_module" || continue
    decision_module_dir=$(go list -mod=readonly -m -f '{{.Dir}}' "$decision_module")
    decision_license_name=$(printf '%s' "$decision_module" | tr '/' '_')
    decision_license_found=false
    for decision_license_file in LICENSE LICENSE.txt LICENSE.md COPYING; do
      if test -f "$decision_module_dir/$decision_license_file"; then
        cp -f "$decision_module_dir/$decision_license_file" "$decision_output/licenses/$decision_license_name.txt"
        decision_license_found=true
        break
      fi
    done
    if test "$decision_license_found" = false; then
      printf 'Missing license for %s\n' "$decision_module" >&2
      exit 1
    fi
    if test -f "$decision_module_dir/NOTICE"; then
      cp -f "$decision_module_dir/NOTICE" "$decision_output/licenses/$decision_license_name.NOTICE"
    fi
  done
