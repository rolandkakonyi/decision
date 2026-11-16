# Hierarchical classification

This native Rust example compares three ways to classify the same products
through the installed `decision` CLI:

- **Flat:** one Choice over all 24 leaf categories.
- **Greedy:** one Choice over four groups, then one over the selected group's
  six leaves. An incorrect group selection cannot be recovered.
- **Two branch:** rank the four groups, classify within each of the two most
  probable groups, then choose between their selected leaves. This uses four
  sequential requests and can recover from an incorrect top-ranked group.
  It cannot recover a leaf excluded by both groups or either local selection.

Code owns the taxonomy and traversal. The model only makes typed judgments.
Each request sees the product description, the same four group summaries, and
the current choices, never the case ID or expected label. Leaf IDs and
descriptions, including their parent ID, are identical wherever that leaf is
offered. Group summaries appear once in the shared state rather than being
repeated in every leaf description. Root requests see group summaries rather
than all leaf descriptions; reducing the candidate
context is part of the hierarchical strategy being measured. The two-branch
strategy reranks the surviving leaves together rather than comparing
probabilities obtained from different choice sets.

## Run

Build with the repository toolchain and locked dependencies. No new runtime
or dependencies are required:

```bash
cargo build --locked
cargo build --locked --example hierarchical
target/debug/examples/hierarchical --help
```

On Windows the executables have an `.exe` suffix. Configure credentials using
the normal [provider settings](../../README.md#provider-settings). Provider and
model must be explicit; the example forwards them to every `decision run`.
Base URL, credential selectors, and other supported environment settings retain
their normal behavior. A report contains the dataset and full requests/results;
handle custom data accordingly. Credentials are never added to the report.

The included dataset has 12 synthetic, manually labeled descriptions over a
two-level taxonomy. It includes distinctions such as commuter versus hiking
backpacks and laundry bags versus luggage organizers. All cases have exactly
one expected leaf; this is a closed-set example, without an abstention policy.

Live inference requires an authorized provider, dataset, and budget. For example,
with Gemini access configured and these 504 requests authorized:

```bash
mkdir -p .state
target/debug/examples/hierarchical \
  --decision target/debug/decision \
  --provider gemini --model gemini-decision-1 \
  --warmups 1 --rounds 5 --max-calls 504 \
  --context 'Same laptop, sequential requests, idle workload, Wi-Fi' \
  > .state/hierarchical.json
```

One full round uses 84 requests: 12 cases times 1 + 2 + 4 requests. Warmups
count against `--max-calls`. The example never retries or substitutes providers.
An inference error or exhausted attempt budget stops the experiment, exits
nonzero, and writes an incomplete report with the evidence collected so far.
Incomplete reports have no comparison summary. Keep separate attempts separate.

Use `--data path.json` for another dataset with the same `groups` and `cases`
structure as [data.json](data.json). Each group needs at least two leaves,
category IDs must be globally unique, and expected labels must name leaves.
The selected provider's Choice limits also apply to the flat candidate set.

## Read the comparison

```bash
jq '{complete, provider_attempts, summary: [.summary[] | del(.paired_with_flat)]}' \
  .state/hierarchical.json
```

Every case runs under every strategy on the same host, with the same explicit
provider/model and product text. Strategy order rotates across cases and
rounds. One or more warmup rounds run first; at least five measured rounds
follow. Warmup results remain in `runs` but are excluded from `summary`.

The summary reports accuracy, request count, input/output token totals, summed
provider duration, and wall time per complete classification. Wall time includes
CLI startup, local validation, provider calls, and traversal; provider duration
is the CLI's narrower measurement. Median and p95 use nearest-rank percentiles.
Unknown token usage stays `null`; zero stays zero. `usage_including_warmups`
includes all attempts with reported usage. Pricing is not inferred from token
counts. To estimate spend, use current provider prices and include warmups;
failed requests can have unreported usage. The first response establishes
`effective_model`; a later change stops the experiment, including when an alias
such as `jev-latest` resolves to a different version mid-run.

`paired_with_flat` records correctness and wall-time differences for the same
case and round, plus whether the predicted label matches. Inspect per-case
regressions and improvements before interpreting an average. Each run retains
requests, responses, expected/predicted labels, and an exact SHA-256 hash of the
JSON-encoded prediction. Metadata records dataset/source hashes, executable
version, platform, context notes, and run settings. Repeated predictions can
differ; output drift is evidence, not something to discard.

The 12 synthetic cases demonstrate the workflow, not a representative product
benchmark. Five repetitions measure variability, not 60 independent products.
Use a separate, representative held-out dataset before tuning or generalizing.
Hierarchy can reduce candidate context while increasing total calls and latency;
compare quality and total resource use against flat classification. A latency
reduction with different predictions is not a behavior-preserving speedup.

## Local verification

```bash
cargo test --locked --example hierarchical
```

The tests use deterministic local answers without credentials or network calls.
They check traversal, recovery from a wrong root, matched scheduling, usage
accounting, and preservation of failed attempts. They do not measure model quality.
