# Environment variables

Every key consumed by the codebase from `.env` (or the process environment).

`.env` is loaded at module-import time by `utils.py` via `python-dotenv`. The first import — typically from `run_*.py` — picks up the file. Subsequent edits require restarting the process.

## Required

### `SEC_USER_AGENT_EMAIL`
Type: string (any valid email format)
Used by: `data/sec_data.py`, `data/institutional.py`
Purpose: SEC EDGAR's `User-Agent` policy requires a real email. Unidentified scrapers are throttled or blocked.
Default if missing: `user@example.com` — likely throttled. Set this before relying on Layer 1 SEC fetches.

### `SEC_USER_AGENT_NAME`
Type: string
Default if missing: `LS_Equity_Research`
Used by: same modules. Combined with the email into the `User-Agent` header. Any descriptive string works.

## Recommended

### `OPENROUTER_API_KEY`
Type: string (begins with `sk-or-v1-`)
Used by: `analysis/ai_client.py`
Purpose: Authenticates calls to OpenRouter for Layer 3 AI analysis.
Get one: [openrouter.ai](https://openrouter.ai) → Keys.
Without it: Layer 3 skips all analyzer calls; combined score = quant composite. Pipeline still runs.

### `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`
Type: string
Used by: `execution/broker.py`
Purpose: Authenticate Alpaca paper-trading API for Layer 6.
Get them: [alpaca.markets](https://alpaca.markets) → Paper Trading → API Keys.
Without them: broker runs in `SIMULATED` mode (synthetic fills, no real orders). Layer 6 still runs end-to-end.

## Optional

### `FORCE_DEV`
Type: `"1"` or unset
Used by: `data/universe.py: get_universe`, `run_data.py`
Purpose: Force dev mode for the current process, regardless of `config.yaml: dev_mode`. Set automatically by `run_data.py --dev`. Useful for running one-off dev-mode commands without flipping the config file.

### `ALPACA_LIVE_CONFIRMED`
Type: literal string `"YES_I_UNDERSTAND_THE_RISKS"`
Used by: `execution/broker.py: _is_live_mode`
Purpose: The second of two switches required for live trading. The first is `config.yaml: execution.mode: live`. Both must be set; missing or misspelled values default to paper. The verbose string is intentional friction. See [ADR-003](../architecture/adr/003-paper-trading-only.md).

## Optional paid data sources

These are checked but not required. Default code paths use free fallbacks.

### `FMP_API_KEY`
Type: string
Used by: `data/transcripts.py`
Purpose: Fetches earnings call transcripts from Financial Modeling Prep. Without it, `transcripts` table stays sparse and the earnings analyzer falls back to calendar metadata.
Get one: [financialmodelingprep.com](https://financialmodelingprep.com).

### `POLYGON_API_KEY`
Type: string
Used by: `data/providers.py` (when integrated)
Purpose: Higher-fidelity exchange-level prices than yfinance.
Get one: [polygon.io](https://polygon.io).

### `FRED_API_KEY`
Type: string
Used by: `risk/tail_risk.py`
Purpose: Fetches credit-spread series from FRED. Without it, `credit_spread` and `cs_zscore` in `check_tail_risk()` return `None`; the VIX check still works.
Get one: [fred.stlouisfed.org](https://fred.stlouisfed.org) → API.

## .env.example template

The shipped `.env.example` mirrors all of the above:

```env
SEC_USER_AGENT_EMAIL=your_email@example.com
SEC_USER_AGENT_NAME=LS_Equity_Research

OPENROUTER_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=

# Optional — paid data sources
# FMP_API_KEY=
# POLYGON_API_KEY=
# FRED_API_KEY=
```

Copy to `.env` (`cp .env.example .env`) and fill in the keys you have.

## See also

- [Configuration](configuration.md) — `config.yaml` fields (separate from `.env`)
- [Prerequisites](../getting-started/prerequisites.md) — where to get each key
