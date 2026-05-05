# Prerequisites

What you need before running JARVIS for the first time.

## Software

### Python 3.11 or newer
Verify: `python --version` (must print `3.11.x` or higher)
Install: [python.org/downloads](https://www.python.org/downloads/) or your OS package manager

### pip
Verify: `pip --version`
Install: ships with Python; if missing, run `python -m ensurepip --upgrade`

### git *(optional, for clone-based install)*
Verify: `git --version`

## API keys

JARVIS works with three external services. Two are required for the full pipeline; one is optional.

| Key | Where to get | Required for | Cost |
|-----|-------------|--------------|------|
| `SEC_USER_AGENT_EMAIL` | Any working email — no signup | Layer 1 SEC filings, Form 4 insider data | Free |
| `OPENROUTER_API_KEY` | [openrouter.ai](https://openrouter.ai) → Keys | Layer 3 AI analysis | $0 with the configured free model |
| `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` | [alpaca.markets](https://alpaca.markets) → Paper Trading → API Keys | Layer 6 paper-trade execution | Free |

The SEC `User-Agent` header is a courtesy identifier — SEC EDGAR rate-limits unidentified scrapers. Any real email works; no account is created.

If you skip the OpenRouter key, Layer 3 falls back to a 100% quant combined score. The system still runs end-to-end.

If you skip the Alpaca keys, Layer 6 runs in `SIMULATED` mode — orders are logged but not sent. The system still runs end-to-end.

## Optional paid data keys

These are **not** required. The default code paths use free fallbacks.

| Key | Provider | What it improves |
|-----|----------|------------------|
| `FMP_API_KEY` | [Financial Modeling Prep](https://financialmodelingprep.com) | Earnings call transcripts (currently scraped from free sources) |
| `POLYGON_API_KEY` | [Polygon.io](https://polygon.io) | Higher-fidelity exchange-level prices (yfinance is the default) |
| `FRED_API_KEY` | [FRED](https://fred.stlouisfed.org) | Yield curve, credit-spread data for the tail-risk monitor |

Add these to `.env` only if you need them. The default `.env.example` has them commented out.

## Hardware and disk

- **Disk:** ~200 MB for fundamentals/prices on the dev (10-ticker) universe; ~2–3 GB for the full S&P 500.
- **RAM:** 2 GB is sufficient for dev mode; 8 GB recommended for full-universe scoring and stress tests.
- **Network:** Each layer hits external APIs. A full nightly cycle on the S&P 500 takes 15–30 minutes depending on rate limits.

## Operating system

The codebase runs on macOS, Linux, and Windows. There are no native binaries. All paths are constructed via `pathlib.Path`, so Windows backslashes are handled transparently.

## Next

Continue to [quickstart](quickstart.md).
