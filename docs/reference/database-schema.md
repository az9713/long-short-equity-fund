# Database schema

Every SQLite table in `data/fund.db`. All tables are created lazily — the first module that needs a table calls `CREATE TABLE IF NOT EXISTS` before its first read or write. Open the database directly with any SQLite client (`sqlite3 data/fund.db`).

The single database holds *everything*: universe, prices, fundamentals, filings, positions, fills, AI cache, risk state. There is no separate file per layer.

## Universe and reference

### `universe`
Created by `data/universe.py`. Source: Wikipedia (S&P 500 list).

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT PRIMARY KEY | GICS ticker (Wikipedia format normalized: `.` → `-`) |
| `company` | TEXT | Company name |
| `sector` | TEXT | GICS sector |
| `sub_industry` | TEXT | GICS sub-industry |
| `is_benchmark` | INTEGER | 1 if a benchmark ETF (SPY, XLK, ...) |
| `last_updated` | TEXT | ISO timestamp |

### `cik_map`
Created by `data/sec_data.py`. Maps tickers to SEC Central Index Keys.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT PRIMARY KEY | |
| `cik` | TEXT | Zero-padded 10-digit CIK |
| `last_updated` | TEXT | |

### `cusip_ticker_map`
Created by `data/institutional.py`. CUSIP → ticker for parsing 13F filings.

| Column | Type | Description |
|--------|------|-------------|
| `cusip` | TEXT PRIMARY KEY | |
| `ticker` | TEXT | |
| `last_updated` | TEXT | |

## Market data

### `daily_prices`
Created by `data/market_data.py`. Source: yfinance.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT | |
| `date` | TEXT | ISO date |
| `open`, `high`, `low`, `close`, `volume` | REAL | OHLCV |
| `adj_close` | REAL | Split- and dividend-adjusted close |
| Composite key | (ticker, date) | |

## Fundamentals

### `fundamentals`
Created by `data/fundamentals.py`. Source: yfinance. Combined quarterly + annual.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT | |
| `period` | TEXT | `quarterly` or `annual` |
| `as_of` | TEXT | Period end date |
| `metric` | TEXT | E.g., `revenue`, `net_income`, `total_debt`, `gross_margin` |
| `value` | REAL | |
| Composite key | (ticker, period, as_of, metric) | |

> **Caveat:** yfinance returns *current* values, not point-in-time. Backtests using these have look-ahead bias.

### `analyst_estimates`
Created by `data/estimates.py`. Source: yfinance.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT | |
| `as_of` | TEXT | |
| `eps_estimate` | REAL | |
| `eps_actual` | REAL | |
| `revisions_up`, `revisions_down` | INTEGER | Count over last 90 days |

### `earnings_calendar`
Created by `data/earnings_calendar.py`. Source: yfinance.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT PRIMARY KEY | |
| `next_earnings_date` | TEXT | ISO date |
| `last_updated` | TEXT | |

### `transcripts`
Created by `data/transcripts.py`. Source: FMP if `FMP_API_KEY` set.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT | |
| `fiscal_quarter` | TEXT | E.g. `2025Q1` |
| `content` | TEXT | Full transcript |
| `fetched_date` | TEXT | |
| Composite key | (ticker, fiscal_quarter) | |

## SEC

### `sec_filings`
Created by `data/sec_data.py`. Source: SEC EDGAR.

| Column | Type | Description |
|--------|------|-------------|
| `accession_number` | TEXT PRIMARY KEY | EDGAR accession |
| `ticker` | TEXT | |
| `cik` | TEXT | |
| `form` | TEXT | `10-K`, `10-Q`, `8-K`, etc. |
| `filing_date` | TEXT | |
| `url` | TEXT | EDGAR URL |
| `text` | TEXT | Extracted body text |

### `insider_transactions`
Created by `data/sec_data.py`. Source: SEC EDGAR Form 4.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT | |
| `filing_date` | TEXT | |
| `insider_name` | TEXT | |
| `insider_title` | TEXT | |
| `transaction_type` | TEXT | `BUY` / `SELL` / `OPTION_EXERCISE` |
| `shares` | REAL | |
| `price` | REAL | |
| Composite key | (ticker, filing_date, insider_name, transaction_type) | |

### `institutional_holdings`
Created by `data/institutional.py`. Source: SEC 13F.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT | |
| `holder` | TEXT | Filer name |
| `as_of` | TEXT | Quarter-end date |
| `shares` | REAL | |
| `value_usd` | REAL | |
| Composite key | (ticker, holder, as_of) | |

## Short interest

### `short_interest`
Created by `data/short_interest.py`. Source: FINRA.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT | |
| `as_of` | TEXT | Settlement date |
| `short_interest` | REAL | |
| `days_to_cover` | REAL | |
| Composite key | (ticker, as_of) | |

## Factor outputs

### `factor_returns`
Created by `factors/crowding.py`. Daily long-top-quintile minus short-bottom-quintile returns per factor.

| Column | Type | Description |
|--------|------|-------------|
| `date` | TEXT | |
| `factor` | TEXT | E.g., `momentum`, `value` |
| `return` | REAL | |
| Composite key | (date, factor) | |

## Portfolio

### `portfolio_positions`
Created by `portfolio/state.py`. Current holdings.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT PRIMARY KEY | |
| `side` | TEXT | `LONG` / `SHORT` |
| `shares` | REAL | |
| `entry_price` | REAL | |
| `entry_date` | TEXT | |
| `current_price` | REAL | Updated by `update_current_prices` |
| `unrealized_pnl` | REAL | |
| `sector` | TEXT | |
| `factor_scores` | TEXT | JSON snapshot of scores at entry |

### `portfolio_history`
Created by `portfolio/state.py`. Append-only trade log.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `date` | TEXT | |
| `ticker` | TEXT | |
| `action` | TEXT | `OPEN` / `CLOSE` / `ADJUST` |
| `shares` | REAL | |
| `price` | REAL | |
| `reason` | TEXT | |

### `position_approvals`
Created by `portfolio/state.py`. Trade approval queue.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT PRIMARY KEY | |
| `side` | TEXT | |
| `status` | TEXT | `PENDING` / `APPROVED` / `REJECTED` / `EXECUTED` |
| `approved_at` | TEXT | |
| `rejected_reason` | TEXT | |

## Execution

### `open_orders`
Created by `execution/order_manager.py`.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `alpaca_order_id` | TEXT | Returned by Alpaca on submit |
| `ticker` | TEXT | |
| `side` | TEXT | `BUY` / `SELL` / `SELL_SHORT` / `BUY_TO_COVER` |
| `shares` | REAL | |
| `limit_price` | REAL | |
| `status` | TEXT | `accepted` / `filled` / `canceled` / `rejected` |
| `created_at` | TEXT | |

### `order_log`
Created by `execution/slippage.py` and `execution/executor.py`. Per-fill record.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `timestamp` | TEXT | |
| `ticker` | TEXT | |
| `side` | TEXT | |
| `shares` | REAL | |
| `limit_price` | REAL | |
| `fill_price` | REAL | |
| `slippage_bps` | REAL | Signed; positive = worse fill |
| `cost_usd` | REAL | `slippage_bps × trade_usd / 10_000` |
| `status` | TEXT | `FILLED` / `SIMULATED` / `REJECTED` |

### `short_availability`
Created by `execution/short_check.py`. Cache of Alpaca borrow availability.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT PRIMARY KEY | |
| `easy_to_borrow` | INTEGER | 0 or 1 |
| `last_checked` | TEXT | |

## Risk

### `veto_log`
Created by `risk/pre_trade.py`. One row per blocked trade.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `timestamp` | TEXT | |
| `ticker` | TEXT | |
| `side` | TEXT | |
| `shares` | REAL | |
| `price` | REAL | |
| `reason` | TEXT | E.g., `"Earnings in 3 days (blackout)"` |

### `credit_spread_history`
Created by `risk/tail_risk.py`. FRED credit-spread series.

| Column | Type | Description |
|--------|------|-------------|
| `date` | TEXT PRIMARY KEY | |
| `value` | REAL | |

## AI

### `analysis_cache`
Created by `analysis/cache.py`. Memoizes analyzer outputs.

| Column | Type | Description |
|--------|------|-------------|
| `analyzer` | TEXT | `filing` / `earnings` / `risk` / `insider` / `sector` |
| `ticker` | TEXT | |
| `artifact_id` | TEXT | Stable hash of the source artifact (e.g. filing accession or transcript hash) |
| `result` | TEXT | JSON string |
| `cached_at` | DATE | |
| Composite key | (analyzer, ticker, artifact_id) | |

### `ai_cost_log`
Created by `analysis/cost_tracker.py`. Per-call token usage.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | |
| `timestamp` | TEXT | |
| `analyzer` | TEXT | |
| `ticker` | TEXT | |
| `input_tokens` | INTEGER | |
| `output_tokens` | INTEGER | |
| `model` | TEXT | |

## Reporting

### `daily_attribution`
Created by `reporting/pnl_attribution.py`. Rolling P&L decomposition.

| Column | Type | Description |
|--------|------|-------------|
| `date` | TEXT | |
| `ticker` | TEXT | |
| `pnl_usd` | REAL | |
| `sector` | TEXT | |
| `signal` | TEXT | `LONG` / `SHORT` |
| Composite key | (date, ticker) | |

## State files (not SQLite)

A few pieces of state live as files outside `fund.db`:

| Path | Format | Purpose |
|------|--------|---------|
| `risk/risk_state.json` | JSON | Daily snapshot used by circuit breakers (`portfolio_value`, `peak_value`, `weekly_pnl`, `risk_decomposition`, `mctr`) |
| `risk/halt.lock` | JSON | Presence blocks all execution. Reason + timestamp inside. |
| `output/scored_universe_latest.csv` | CSV | Output of Layer 2; consumed by Layers 3, 4, 5 |
| `output/backtest/{equity_curve,monthly_returns,rebalance_log}.csv` + `summary.txt` | CSV / text | Most recent backtest result. Each `run_backtest.py` invocation overwrites this directory. |
| `output/reports/<TICKER>_<DATE>.md` | Markdown | Per-ticker AI report |
| `output/letters/<DATE>.md` | Markdown | Weekly investor letter |

## Inspecting the database

```bash
# List all tables
sqlite3 data/fund.db ".tables"

# Show a table's schema
sqlite3 data/fund.db ".schema portfolio_positions"

# Quick row counts
sqlite3 data/fund.db "
SELECT 'daily_prices', COUNT(*) FROM daily_prices
UNION ALL SELECT 'sec_filings', COUNT(*) FROM sec_filings
UNION ALL SELECT 'portfolio_positions', COUNT(*) FROM portfolio_positions
UNION ALL SELECT 'order_log', COUNT(*) FROM order_log;
"
```

## See also

- [Data layer](../concepts/data-layer.md) — what populates these tables
- [Configuration](configuration.md) — runtime config (separate from schema)
