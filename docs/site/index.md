---
title: Typed judgments. One CLI.
hide:
  - toc
---

<div class="decision-hero" markdown>

<div class="hero-copy" markdown>

<p class="eyebrow">TEXT + IMAGES → TYPED ANSWERS</p>

# Typed judgments.<br>One CLI.

Ask for a probability, a choice, a score, or a location.
Get validated answers from your selected AI provider — in the terminal or through MCP.

[Get started →](quickstart.md){ .md-button .md-button--primary }
[Explore the examples](examples.md){ .hero-secondary }

<p class="hero-meta">Open source · MIT licensed · CLI + MCP</p>

</div>

<div class="type-diagram" aria-label="Four question types, one shared interface">
<a class="type-node" href="concepts/#noul-a-yes-probability"><span class="type-glyph" aria-hidden="true">○</span><span><strong>Noul</strong><small>A yes probability</small></span><code>0 … 1</code></a>
<a class="type-node" href="concepts/#choice-a-named-option"><span class="type-glyph" aria-hidden="true">⑂</span><span><strong>Choice</strong><small>A named option</small></span><code>A / B / C</code></a>
<a class="type-node" href="concepts/#score-an-ordered-rating"><span class="type-glyph" aria-hidden="true">▥</span><span><strong>Score</strong><small>An ordered rating</small></span><code>0 … n</code></a>
<a class="type-node" href="concepts/#box-a-location-in-an-image"><span class="type-glyph" aria-hidden="true">⌗</span><span><strong>Box</strong><small>A location · TypeSafe only</small></span><code>x₁ y₁ x₂ y₂</code></a>
<div class="diagram-result"><span aria-hidden="true">✓</span> One validated batch</div>
</div>

</div>

<div class="install-strip" markdown>

<div markdown>
<p class="eyebrow">01 / INSTALL</p>

Homebrew · macOS ARM64 and Linux x86_64
</div>

```bash
brew install rolandkakonyi/tap/decision
```

</div>

## Try it without a key

Validate a real request locally. A dry run checks the input without contacting a provider or running inference.

```bash
decision noul 'Is this a greeting?' --state 'Hello' --dry-run
```

For live judgments, [configure your provider](providers.md) and omit `--dry-run`.
Inference is billed by the provider. Prefer Python? The [installation guide](quickstart.md) covers uv and provider extensions.

## Choose your interface

<div class="path-grid" markdown>

<a class="path-card" href="cli/"><span class="eyebrow">TERMINAL</span><strong>Ask from the CLI →</strong><span>One question or a mixed batch. Readable output for people, JSON for scripts.</span></a>

<a class="path-card" href="mcp/"><span class="eyebrow">AGENTS</span><strong>Connect through MCP →</strong><span>The same validation and answer types, exposed as one evaluate tool.</span></a>

<a class="path-card" href="provider-plugins/"><span class="eyebrow">PYTHON</span><strong>Bring a provider →</strong><span>Add a trusted provider package through the versioned Python interface.</span></a>

</div>

## Four built-in providers

All four support Noul, Choice, and Score over text and PNG/JPEG images.
**Box is available only with TypeSafe and requires Jev 1.14.** Each provider has its own model and request limits.

<div class="provider-strip" markdown>

[**TypeSafe**<br>Jev · default provider](providers.md#provider-settings)

[**OpenAI**<br>Decisions API](providers.md#openai-decisions)

[**Gemini**<br>Decisions API](providers.md#gemini-decisions)

[**Cloudflare**<br>Clef + Clef Flash](providers.md#cloudflare-clef)

</div>

Select with `--provider NAME` or `DECISION_PROVIDER`. Homebrew includes all four;
third-party extensions require a [Python installation](quickstart.md).

## A shared contract, from input to answer

Give a batch one **state** and named **questions**. Each question has a type and instructions;
Choice and Score also define their allowed outcomes. Answers keep your question IDs.

```json
{
  "state": "Please refund my order. It arrived damaged.",
  "questions": {
    "refund": {
      "type": "noul",
      "instructions": "Does the message request a refund?"
    },
    "topic": {
      "type": "choice",
      "instructions": "What is the main topic?",
      "criteria": {
        "order": "An existing order",
        "other": "Anything else"
      }
    }
  }
}
```

Save as `request.json`, then preview it:

```bash
decision run request.json --dry-run
```

One evaluation batch sends one provider request. Results are validated as a whole;
invalid or incomplete answers fail the batch. Requests are not automatically retried.
Successful queries include provider token usage and client-measured duration.

[Understand the question types →](concepts.md){ .md-button }

<div class="identity-banner" markdown>

![Decision: four question types join into one CLI interface](assets/decision-banner.png){ loading=lazy }

</div>
