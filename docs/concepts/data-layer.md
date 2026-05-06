# The data layer

Layer 1. Owns every external data fetch and the SQLite schema. Every other layer reads from `data/fund.db`.

## What it is

A package of nine modules under `data/` plus the `run_data.py` entry point. Each module is responsible for one data domain and writes to one or more SQLite tables.

## Module map

| Module | Responsibility | Tables written |
|--------|----------------|----------------|
| `data/universe.py` | S&P 500 membership from Wikipedia, sector tags from GICS | `universe` |
| `data/market_data.py` | Daily OHLCV prices via yfinance | `daily_prices` |
| `data/fundamentals.py` | Income statement, balance sheet, cash flow from yfinance | `fundamentals_quarterly`, `fundamentals_annual` |
| `data/sec_data.py` | 10-K/10-Q/8-K filings + Form 4 insider transactions from SEC EDGAR | `sec_filings`, `insider_transactions` |
| `data/institutional.py` | 13F holdings (free fallback: SEC EDGAR direct) | `institutional_holdings` |
| `data/short_interest.py` | Short interest reports from FINRA | `short_interest` |
| `data/estimates.py` | Analyst estimates and revisions (yfinance) | `analyst_estimates` |
| `data/earnings_calendar.py` | Upcoming earnings dates per ticker | `earnings_calendar` |
| `data/transcripts.py` | Earnings call transcripts (placeholder — FMP if key set) | `earnings_transcripts` |
| `data/providers.py` | Shared HTTP client, retries, throttling | — |

## How a refresh works

`run_data.py` calls each module in sequence. Each module:

1. Reads the universe table to get the ticker list.
2. Loops over tickers (often with a small thread pool for I/O parallelism).
3. Hits the source API.
4. Upserts rows into its tables.

There is no streaming or queue. The script blocks until the previous step finishes before starting the next.

## The eight steps

```
[1/8] Universe        → wikipedia.org/wiki/List_of_S&P_500
[2/8] Market prices   → yfinance
[3/8] Fundamentals    → yfinance
[4/8] SEC data        → data.sec.gov (EDGAR)
[5/8] Institutional   → data.sec.gov (13F)
[6/8] Short interest  → finra.org
[7/8] Estimates       → yfinance
[8/8] Earnings cal    → yfinance
```

`--no-filings` skips step 4 (the slowest). `--no-13f` skips step 5.

## Sector normalization

yfinance returns sector strings like `"Technology"` and `"Financial Services"`. GICS sectors as used by Wikipedia and the sector ETFs (XLK, XLF, etc.) are `"Information Technology"` and `"Financials"`. The translation table is `_YF_TO_GICS` in `factors/base.py`. All downstream layers use the GICS names.

## Rate limits and retries

| Source | Limit | Behavior |
|--------|-------|----------|
| yfinance | None published; ~0.5–1 req/sec recommended | Built-in jitter; `tenacity` retries on transient errors |
| SEC EDGAR | 10 req/sec, declining `User-Agent` blocks unidentified scrapers | `User-Agent` set from `SEC_USER_AGENT_*` env vars; sleep + retry |
| FINRA | None published | Single request per refresh; cached for the day |
| OpenRouter (Layer 3, separate) | 15 req/min for the free Gemini model | 4.5-second module-level rate-limit |

## Data quality assumptions

- yfinance fundamentals are *current*, not point-in-time. Backtests using the value/quality factors have look-ahead bias. See [backtesting](backtesting.md).
- yfinance occasionally drops a ticker for a refresh; `market_data.update_prices` catches the exception and continues.
- SEC EDGAR sometimes returns malformed XBRL. The filing analyzer (Layer 3) is defensive: missing fields produce `None`, not crashes.
- Form 4 insider data is several days lagged. JARVIS does not attempt to capture intra-day insider activity.
- SEC's submissions API returns `primary_doc` like `xslF345X06/form4.xml` for Form 4 — that path serves the stylesheet-rendered HTML view, not raw XML. JARVIS strips the directory prefix to fetch the raw XML at the filing root, with `index.json` enumeration as a fallback. See [changelog #2](../changelog.md#2-form-4-primary_doc-returned-the-stylesheet-not-the-xml).
- 13F XML often includes `xsi:schemaLocation` without an `xmlns:xsi` declaration. JARVIS's parser strips namespaced attributes and uses `lxml` recovery mode. See [changelog #3](../changelog.md#3-13f-xml-parser-tripped-on-undeclared-xsischemalocation).

## Dev mode behavior

When `dev_mode: true` (config) or `--dev` (CLI), `data/universe.py` returns the ten hard-coded `dev_tickers` instead of the full S&P 500. Every downstream module then operates on those ten only. A full dev refresh runs in under a minute even with all eight steps enabled.

## Common gotchas

**The `daily_prices` table is empty after a refresh.** yfinance returned no data for those tickers. Check that the tickers exist on Yahoo (sometimes BRK.B → BRK-B, etc.). The universe loader normalizes Wikipedia tickers (replacing `.` with `-`) but custom tickers passed via `--ticker` flags are not normalized.

**SEC fetches return 403.** Your `SEC_USER_AGENT_EMAIL` is the placeholder default `user@example.com`. Set a real one in `.env`.

**13F holdings are sparse for small caps.** Expected — funds only file 13F if AUM > $100M. Small-cap names will show 0–2 holders.

**Earnings calendar has stale dates.** yfinance occasionally returns past earnings as "next." The earnings-blackout pre-trade check tolerates this — if "days to earnings" is negative, the check ignores it.

## See also

- [Configuration](../reference/configuration.md) — `dev_mode`, `dev_tickers`, `universe.benchmark_tickers`
- [Database schema](../reference/database-schema.md) — full DDL for every table written here
- [Run the full pipeline](../guides/run-the-full-pipeline.md) — when to run each layer
