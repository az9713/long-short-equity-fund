# ADR 001: OpenRouter (free Gemini) over the Anthropic API

**Status:** Accepted

## Context

Layer 3 needs an LLM endpoint to run four analyzers per ticker (filing, earnings, risk, insider) and a per-sector synthesizer. A full nightly run on the top 20 longs + top 20 shorts is ~160 calls.

The original design intent (taken from the source video the system reverse-engineers) was to call the Anthropic API directly with Claude 3.5 Sonnet. The user has a Claude *subscription* (claude.ai monthly plan) but no Anthropic API credits — those are billed separately. Using Sonnet via the API at default rates would cost ~$2–5 per nightly run.

The user is running this as a personal research project. Recurring API charges are prohibitive in that context.

## Decision

Use OpenRouter as the LLM gateway. Default model: `google/gemini-2.0-flash-exp:free`. Real cost: $0.

## Alternatives considered

### Option A: Anthropic API directly with Sonnet
- Pros: Best-in-class quality. Native prompt caching (90% off cached input tokens after the first call).
- Cons: $2–5 per nightly run. ~$60–150/month for a daily cycle. Not viable for a personal user without API credits.

### Option B: OpenAI API with GPT-4
- Pros: Comparable quality to Sonnet. SDK is well supported.
- Cons: Same cost issue as Option A, slightly cheaper per token but similar order of magnitude.

### Option C: OpenRouter free tier (chosen)
- Pros: $0 real cost. OpenAI-SDK-compatible — drop-in via `base_url`. Lets us swap to paid models later by changing one config line.
- Cons: 15 RPM rate limit (manageable with a 4.5s module-level sleep). Free model (Gemini 2.0 Flash exp) is weaker than Sonnet/GPT-4 on reasoning. No prompt caching support on the free tier.

### Option D: Run a local model (Ollama)
- Pros: Truly $0 with no rate limit. Private.
- Cons: Local inference at the quality needed (Llama 3.1 70B, Qwen 2.5 72B) requires a beefy machine (32+ GB VRAM). User's hardware is a laptop.

## Rationale

Free is the dominant criterion. The system's value is in the *integration* of quant scoring + LLM analysis + portfolio construction + risk + execution, not in the absolute quality of a single LLM call. A weaker model still produces useful structured analyses; the quant composite remains the primary signal.

Using OpenRouter with the OpenAI SDK means the code is provider-agnostic. Swapping to Sonnet later is two-line config change:

```yaml
ai:
  model: anthropic/claude-3.5-sonnet
  base_url: https://openrouter.ai/api/v1   # or https://api.anthropic.com/v1
```

No code changes required.

## Trade-offs

- **Quality reduction.** Gemini 2.0 Flash misses nuance that Sonnet would catch. Acceptable because LLM output is supplementary (30% weight) to quant composite (70%).
- **No prompt caching.** Gemini free tier doesn't support the prompt-cache mechanism that would otherwise reduce cost on cached system prompts. Irrelevant since the cost is already $0.
- **Rate limit.** 15 RPM means a full nightly run of 160 calls takes ~12 minutes. Acceptable for an end-of-day batch.

## Consequences

- The cost-tracker (`analysis/cost_tracker.py`) still runs and logs token counts, but the displayed dollar figure is "reference cost using paid-tier rates" — actual cost is $0.
- The user can experiment freely without incurring charges.
- Should the user obtain Anthropic API credits, swapping in Sonnet is trivial. The architecture supports any OpenAI-SDK-compatible endpoint.
