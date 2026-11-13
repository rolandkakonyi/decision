#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$repo"
export CARGO_TARGET_DIR="$repo/.state/provider-prototype-rust/target"
output="$repo/.state/provider-prototype-rust"
cargo build --release --locked --manifest-path experiments/provider-runtime/rust/Cargo.toml
cp "$CARGO_TARGET_DIR/release/decision" "$output/decision"
uv run --locked python experiments/provider-runtime/rust/notices.py --output "$output"
