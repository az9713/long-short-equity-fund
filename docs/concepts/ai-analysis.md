# AI analysis

Layer 3. Calls four LLM analyzers per ticker, optionally rolls them up to a sector synthesis, and produces a *combined score* that blends quant and AI views.

## What it is

A package of nine modules under `analysis/` plus the `run_analysis.py` entry point. The four "analyzer" modules each generate one structured JSON output per ticker by prompting an LLM through OpenRouter.

## Module map

| Module | Purpose | Output keys |
|--------|---------|-------------|
| `analysis/ai_client.py` | Wraps the OpenAI SDK to call OpenRouter; module-level rate limiter (4.5s between calls); JSON extraction; tenacity retries | — |
| `analysis/cache.py` | SQLite-backed cache of analyzer outputs keyed by `(ticker, analyzer, hash)` | — |
| `analysis/cost_tracker.py` | Counts input/output tokens per call; persists to JSON | — |
| `analysis/filing_analyzer.py` | Reads latest 10-K/10-Q from `sec_filings`, summarizes risk factors and forward-looking statements | `risks`, `forward_looking`, `accounting_changes`, `summary` |
| `analysis/earnings_analyzer.py` | Reads latest earnings transcript / call summary; flags margin guidance, capex changes, tone | `tone`, `guidance_change`, `key_themes`, `summary` |
| `analysis/risk_analyzer.py` | Synthesizes a risk score from filing analysis + recent stock action | `risk_score`, `risk_factors`, `summary` |
| `analysis/insider_analyzer.py` | Reads recent Form 4 transactions, flags clusters or unusual sizes | `pattern`, `notable_transactions`, `summary` |
| `analysis/sector_analysis.py` | Aggregates per-ticker analyses for a sector → outlook + top long/short ideas | `sector_outlook`, `top_long_idea`, `top_short_idea`, `summary` |
| `analysis/combined_score.py` | Blends quant composite (Layer 2) with AI scores into a final per-ticker number | — |
| `analysis/report_generator.py` | Writes per-ticker markdown reports to `output/reports/` | — |

## How the LLM is called

Every analyzer goes through `analysis/ai_client.py: chat_completion()`. That function:

1. Sleeps if the last call was less than 4.5 seconds ago (stay under 15 RPM rate limit).
2. Builds an OpenAI SDK client pointing at OpenRouter (`base_url: https://openrouter.ai/api/v1`).
3. Sends the prompt with the configured model (default `openai/gpt-oss-20b:free`).
4. On `RateLimitError`, `APIConnectionError`, or 5xx response, retries with exponential backoff (tenacity).
5. Returns the response text.
6. Each analyzer then runs `extract_json()` to pull a JSON object out of the response (handles raw JSON, fenced ```json blocks, and brace-extraction fallback).
7. Token counts are recorded in the cost tracker.

Because the configured model is on OpenRouter's free tier, real cost is $0. The cost tracker still runs so you can see token volume. Free models on OpenRouter rotate over time — if the default 404s, see [ADR-001](../architecture/adr/001-openrouter-over-anthropic-api.md) and [changelog #6](../changelog.md#6-default-openrouter-model-was-deprecated-404).

## The cache

`analysis/cache.py` writes every analyzer output to a SQLite table keyed by `(ticker, analyzer_name, prompt_hash)`. The TTL is `ai.cache_ttl_days` (default 30) in `config.yaml`. On subsequent runs:

- If the underlying data hasn't changed (filing date, transcript hash) → cache hit, no LLM call.
- If it has changed → cache miss, LLM call + cache write.

The cache substantially reduces per-run cost for sector synthesis and re-runs. Clear it by deleting the `ai_cache` table or the relevant rows.

## Combined score

`analysis/combined_score.py` produces a single number per ticker:

```
combined = 0.7 * quant_composite + 0.3 * ai_score
```

`ai_score` is computed from the four analyzer outputs by mapping their structured fields to a 0–100 scale (e.g., a "negative" earnings tone subtracts; a high insider buying cluster adds). The exact mapping is in `combined_score.py: _ai_score_for_ticker`.

If any analyzer returned `None` (no data, or the LLM call failed), the combined score falls back to the quant composite for that ticker — `has_ai_analysis = False` is set on the row.

## Cost behavior

| Setting | Value | Effect |
|---------|-------|--------|
| `ai.cost_ceiling_usd` (config) | 25.0 | Soft ceiling — cost tracker logs a warning when exceeded; does not abort the run |
| Module rate limit | 4.5s between calls | ~13 calls/min, well under the free tier's 15 RPM |
| Free model | `openai/gpt-oss-20b:free` | Real cost: $0 |

A full run on the top 20 longs + top 20 shorts × 4 analyzers = 160 calls. At 13/min, this takes ~12 minutes the first time. With cache hits, subsequent runs are seconds.

## Sector synthesis

After per-ticker analyzers finish, `analysis/sector_analysis.py` is called once per unique sector in the candidate set. It aggregates the per-ticker analyses and produces:

```json
{
  "sector_outlook": "neutral | bullish | bearish",
  "top_long_idea": "AAPL — strong momentum, no insider selling, Q4 guidance raise",
  "top_short_idea": "INTC — declining revisions, insider clusters, capex headwind",
  "summary": "..."
}
```

The summary appears in the dashboard's Research tab and feeds the weekly investor letter (Layer 7).

## Without an OpenRouter key

If `OPENROUTER_API_KEY` is unset:

- `run_analysis.py` prints a warning and skips all analyzer calls.
- `combined_score.py` returns the quant composite unchanged (combined = quant).
- `report_generator.py` writes per-ticker reports with quant breakdowns only.
- The Research tab in the dashboard shows quant scores only.

The pipeline still runs end-to-end. AI analysis is a value-add, not a hard dependency.

## Switching models

To use Claude or GPT-4 instead of Gemini, edit `config.yaml`:

```yaml
ai:
  provider: openrouter
  model: anthropic/claude-3.5-sonnet      # or openai/gpt-4-turbo
  base_url: https://openrouter.ai/api/v1   # unchanged
```

You can also point directly at the provider:

```yaml
ai:
  provider: openrouter   # name is informational; only base_url + key matter
  model: claude-3-5-sonnet-20241022
  base_url: https://api.anthropic.com/v1
```

In that case set the appropriate API key in `.env` (the SDK will look up `OPENROUTER_API_KEY` regardless of provider — adjust the env var name in `analysis/ai_client.py: _get_client()` if needed).

## Common gotchas

**Analyzer returns `None`.** The LLM either rate-limited (look for tenacity retry messages in the log), returned non-JSON, or the underlying data was missing (no recent filing, no transcript). Re-run; if persistent, inspect the cache table directly.

**Combined score is the same as quant composite.** No analyzers produced output for those tickers. Either no key set, or the data layer didn't populate the source tables (`sec_filings`, `earnings_transcripts`).

**Sector outlook is empty for some sectors.** Sectors with fewer than 2 candidates in the candidate set are skipped — there's nothing to synthesize.

**Token counts climb but cost stays $0.** Expected — the configured model is the free Gemini tier. The dollar figure shown in `_print_cost_summary` is a *reference* cost using paid-tier rates. Actual cost is $0.

## See also

- [ADR-001](../architecture/adr/001-openrouter-over-anthropic-api.md) — why OpenRouter
- [Configuration](../reference/configuration.md) — `ai.*` keys
- [Portfolio construction](portfolio-construction.md) — what consumes the combined score
