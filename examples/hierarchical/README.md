# Hierarchical classification

This native Rust example compares three ways to classify the same products
through the installed `decision` CLI:

- **Flat:** one Choice over all leaves when there are at most 255. Larger
  candidate sets use fixed SHA-256 ID order, split into chunks of at most 255;
  one batch evaluates all chunks, then a second request reranks their winners.
  Chunks are independent of semantic taxonomy groups. Singleton chunks need
  no model question. A correct leaf can be lost in its chunk.
- **Greedy:** one Choice over four groups, then one over the selected group's
  six leaves. An incorrect group selection cannot be recovered.
- **Two branch:** rank the four groups, classify within each of the two most
  probable groups in one batch, then choose between their selected leaves.
  This uses three sequential requests and can recover from an incorrect top-ranked group.
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
with Gemini access configured and these 432 requests authorized:

```bash
mkdir -p .state
target/debug/examples/hierarchical \
  --decision target/debug/decision \
  --provider gemini --model gemini-decision-1 \
  --warmups 1 --rounds 5 --max-calls 432 \
  --context 'Same laptop, sequential requests, idle workload, Wi-Fi' \
  > .state/hierarchical.json
```

One full toy-data round uses 72 requests: 12 cases times 1 + 2 + 3 requests. Warmups
count against `--max-calls`. The example never retries or substitutes providers.
An inference error or exhausted attempt budget stops the experiment, exits
nonzero, and writes an incomplete report with the evidence collected so far.
Incomplete reports have no comparison summary. Keep separate attempts separate.

Use `--data path.json` for another dataset with the same `groups` and `cases`
structure as [data.json](data.json). Each group needs at least two leaves,
category IDs must be globally unique, and expected labels must name leaves.
Groups must have 2 to 255 leaves and there must be 2 to 255 groups. Both flat
and two-branch strategies batch independent questions, matching the CLI's
supported contract rather than making separate round trips for each question.

## Larger taxonomy and harder cases

[shopify.json](shopify.json) contains 1,024 leaf categories sampled from a pinned
[Shopify product taxonomy](https://github.com/Shopify/product-taxonomy), grouped
under eight original departments. Category IDs and full paths are unchanged;
group descriptions list the department's immediate subcategories. The data is
MIT-licensed; see [Shopify's license](SHOPIFY-LICENSE).

[shopify-cases.json](shopify-cases.json) defines 16 manually labeled synthetic
products, two per department. The descriptions use aliases, technical details,
accessory exclusions, and cross-department distinctions. These are authored
cases, not merchant listings or independently adjudicated benchmark labels.
The expected label identifies the most specific applicable offered leaf.
For example, a window-mounted cat bed maps to Cat Window Beds & Perches rather
than general Cat Perches & Shelves. Backpacking stoves and shoe bags follow
Shopify's Home & Garden placement; microphone boom arms follow Electronics.

The offline preparation script prioritizes expected leaves, then their nearby
taxonomy neighbors, then deterministically ordered distractors. It balances
sampling across departments. This intentionally creates a challenging sample
rather than estimating production category frequencies. Sampling uses labels
to keep targets in the candidate set; labels never enter an inference request.

`--sizes` produces strictly increasing nested subsets from the supplied data.
Every size retains all target leaves and at least two children per group. It
fills remaining slots round-robin from the dataset's existing child order, so
adding distractors does not change cases, labels, or category descriptions.
The full sampled department sizes range from 31 to 142 leaves, within the
per-question Choice limit. A 1,024-category flat run uses five chunk questions
in one batch plus one winner question; one-shot flat is unavailable at that size.

For an authorized Gemini experiment:

```bash
target/debug/examples/hierarchical \
  --decision target/debug/decision \
  --provider gemini --model gemini-decision-1 \
  --data examples/hierarchical/shopify.json --sizes 128,255,1024 \
  --warmups 1 --rounds 5 --max-calls 1824 --max-input-tokens 40000000 \
  --context 'Same laptop, sequential requests, idle workload, Wi-Fi' \
  > .state/hierarchical-shopify.json
```

All sizes share one attempt and reported-input-token budget. The token limit
stops before the next call after reported usage reaches the threshold; one
request can overshoot. Unknown usage stops a token-limited experiment. For
money limits, reserve the cost of one maximum-size model request and any
earlier or unreported usage. Neither token counts nor this flag replace the
provider's billing records. There are 1,824 requests including warmups for
this matrix; fewer calls can be sent if an error or budget limit stops it.

Multi-size reports contain `experiments`, each with its own selected dataset,
summary, hashes, and `flat_mode` (`one_shot` or `chunked_batch`). The top-level
attempt and token totals include every size and warmup. Independent questions
are batched in all sizes, so compare sizes within this experiment using the
same recorded harness hash.

```bash
jq '{complete, provider_attempts, input_tokens_including_warmups,
     experiments: [.experiments[] | {metadata, summary: [.summary[] | del(.paired_with_flat)]}]}' \
  .state/hierarchical-shopify.json
```

To reproduce the snapshot, download the pinned `SOURCE` in
[prepare_shopify.py](prepare_shopify.py) and run this optional Python data-preparation
tool with uv. It verifies the source SHA-256 and performs no inference:

```bash
uv run --locked python examples/hierarchical/prepare_shopify.py \
  --categories /path/to/categories.txt --output /tmp/shopify.json
```

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

The synthetic cases demonstrate the workflow, not a representative product
benchmark. Five repetitions measure variability, not five times as many independent products.
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
