# Common issues

The most frequent failures and their fixes. Ordered by frequency.

## SEC fetches return 403

**Cause:** `SEC_USER_AGENT_EMAIL` is unset or still the placeholder `user@example.com`. SEC EDGAR requires a real email and throttles or blocks requests with placeholder strings.

**Fix:**
```bash
# Edit .env
SEC_USER_AGENT_EMAIL=your_real@email.com
SEC_USER_AGENT_NAME=Your_Name_Or_Project
```

**If that doesn't work:** EDGAR may have temporarily rate-limited your IP. Wait 15 minutes and retry.

---

## yfinance returns no data for many tickers

**Cause:** Yahoo Finance rate-limited or blocked the request. yfinance is a free unofficial wrapper; reliability is not guaranteed.

**Fix:** Wait 5–10 minutes and re-run. The script is idempotent — it upserts, doesn't duplicate.

```bash
python run_data.py --dev
```

**If that doesn't work:** Set `POLYGON_API_KEY` in `.env` and uncomment the line in `.env.example`. The data layer will route to Polygon when the key is present (where the integration is wired in `data/providers.py`).

---

## `OPENROUTER_API_KEY not set` warning

**Cause:** No key in `.env`. AI analysis is skipped.

**Fix:**
1. Get a free key at [openrouter.ai](https://openrouter.ai).
2. Add to `.env`:
   ```
   OPENROUTER_API_KEY=sk-or-v1-...
   ```
3. Re-run `python run_analysis.py`.

**If that doesn't work:** The combined score gracefully falls back to the quant composite. The pipeline still completes. No fix needed if you don't want LLM analysis.

---

## Circuit breaker fires immediately on a fresh database

**Cause:** Stale `risk/risk_state.json` with old `peak_value` or `portfolio_value` that doesn't match current state.

**Fix:**
```bash
rm risk/risk_state.json
python run_risk_check.py
```

The file regenerates from `portfolio_history`. If the table is empty, defaults are used (peak = portfolio = $100,000), and no breaker fires.

**If that doesn't work:** The halt lock may also be stale:
```bash
python run_risk_check.py --clear-halt
```

---

## Streamlit shows "ScriptRunner: Could not connect"

**Cause:** Port 8502 is already in use by another process (often a previous `run_dashboard.py` that wasn't fully killed).

**Fix:** Find and kill the lingering process:

```bash
# macOS / Linux
lsof -i :8502
kill -9 <PID>
```

```powershell
# Windows
netstat -ano | findstr :8502
taskkill /F /PID <PID>
```

Or change the port in `config.yaml: dashboard.port` and re-run.

---

## MVO falls back to conviction-tilt every run

**Cause:** Infeasible region. The constraints (gross ≤ 1.5, net beta ≤ 0.15, sector ≤ 0.25) cannot all be satisfied with the available candidates. Common in dev mode (10 tickers).

**Fix:** Switch to conviction explicitly to silence the warning:

```yaml
portfolio:
  optimize_method: conviction
```

Or relax constraints (raise `gross_limit`, `max_beta`, etc.).

**If that doesn't work:** Switch to the full universe (`dev_mode: false`). MVO is much more likely to find a feasible region with 503 candidates.

---

## Trade list is empty after `--whatif`

**Cause:** No candidates with `signal in (LONG, SHORT)` in `output/scored_universe_latest.csv`. All signals are NEUTRAL.

**Fix:** Inspect the CSV:

```bash
sqlite3 -separator ',' :memory: \
  "select count(*), signal from read_csv('output/scored_universe_latest.csv') group by signal;"
```

(Or just open the CSV.) If everything is NEUTRAL, the composite scores are too clustered around 50. Causes:

- **Dev mode with singleton sectors.** `MIN_GROUP_SIZE = 3` in `factors/base.py` makes any sector with <3 tickers fall back to score 50. With one ticker per sector, every factor scores 50 and the composite collapses to 50. The default `dev_tickers` is calibrated to give ≥3 tickers in IT, Health Care, and Financials — keep that property if you edit it. See [changelog #4](../changelog.md#4-dev_tickers-had-one-ticker-per-most-sectors--every-score-collapsed-to-50).
- **Universe smaller than 5 tickers.** Signal assignment is skipped entirely below 5 tickers — everything stays NEUTRAL.

Switch to full universe (`dev_mode: false`) or rebuild `dev_tickers` with viable cohorts.

---

## `submit_order failed: fractional orders must be DAY orders`

**Cause:** `execution.time_in_force = "gtc"` (the default) — but Alpaca rejects GTC for fractional shares. JARVIS routinely sizes positions in fractional shares, so this rejected every paper order. Resolved as of [changelog #12](../changelog.md#12-alpaca-rejected-every-paper-order-fractional-orders-must-be-day-orders) — TIF auto-overrides to DAY when shares are fractional.

**Fix:** No action needed if you're on a current commit. If you see this on an older commit, either:

- Pull the fix in `execution/executor.py`, or
- Set `execution.time_in_force: "day"` in `config.yaml` (works but DAY orders cancel at market close).

---

## Layer 3 LLM calls return 404

**Cause:** The configured `ai.model` was retired by OpenRouter. Free models rotate.

**Fix:** Query the live model list and pick a current free model:

```bash
curl -s "https://openrouter.ai/api/v1/models" | python -c "
import json, sys
for m in json.load(sys.stdin)['data']:
    if ':free' in m['id']: print(m['id'])
"
```

Pick one that responds without 429s (small open-weight models tend to be less congested), and update `config.yaml`:

```yaml
ai:
  model: "openai/gpt-oss-20b:free"   # or another current free slug
```

The architecture is provider-agnostic — see [ADR 001](../architecture/adr/001-openrouter-over-anthropic-api.md).

---

## `Insider transactions: 0` after data refresh, even with SEC email set

**Cause:** Two separate Form 4 bugs surfaced together: a `TypeError` in the SEC fetcher and a URL-construction mismatch where `primary_doc` pointed at the stylesheet HTML render rather than the raw XML. Both fixed — see [changelog #1, #2](../changelog.md#1-sec-_sec_get-clobbered-caller-headers-typeerror).

**Fix:** Pull current `data/sec_data.py`. Verify with:

```bash
python -c "
import sqlite3
c = sqlite3.connect('data/fund.db')
print(c.execute('SELECT COUNT(*) FROM insider_transactions').fetchone()[0])
"
```

Should be in the hundreds for a 10-ticker dev refresh, thousands for full S&P 500.

---

## Stress test prints "No results (empty portfolio)" pre-execution

**Cause:** `--stress` used to require live positions in `portfolio_positions`. Pre-execution, that table is empty.

**Fix:** As of [changelog #10](../changelog.md#10-run_risk_checkpy---stress-printed-no-results-empty-portfolio-pre-execution), `--stress` falls back to a hypothetical book built from the latest scored signals at the per-position cap. As long as `output/scored_universe_latest.csv` has LONG or SHORT rows, the six scenarios print.

If you still see "no positions and no scored signals", run `python run_scoring.py` first — that produces the CSV the fallback reads.

---

## Limit orders never fill

**Cause:** Limit price is too tight (default ±10 bps from last close). In a fast market, the price can move beyond the limit before the order fills.

**Fix:**
```bash
python run_execution.py --cancel-all
```

Then either:

1. Widen the limit offset. Edit `run_execution.py: _run_execute`:
   ```python
   limit_price = round(close * 1.002, 2)   # 20 bps instead of 10
   ```
2. Wait until end-of-day; `gtc` orders sit until expired or filled.

---

## Backtest results are implausibly good (Sharpe > 2)

**Cause:** Used `--full-score` mode. yfinance fundamentals are *current*, not point-in-time, so the value/quality factors have look-ahead bias.

**Fix:** Run in default momentum-only mode:

```bash
python run_backtest.py --start 2021-01-01 --end 2024-12-31
```

(Drop `--full-score`.) Momentum is computed from price history alone, which is point-in-time correct.

**If that doesn't work:** Check that `--num-longs` and `--num-shorts` aren't both > universe-size / 2. With dev mode (10 tickers) and 5/5 cohorts, the entire universe is in the portfolio, which makes the long-short return collapse to a benign average.

---

## `import alpaca` fails

**Cause:** `alpaca-py` is not installed, or you have the deprecated `alpaca_trade_api` package instead.

**Fix:**
```bash
pip uninstall alpaca_trade_api alpaca-trade-api
pip install -r requirements.txt
```

The codebase uses the modern `alpaca-py` SDK (top-level `alpaca` namespace), not the deprecated `alpaca_trade_api`.

---

## "halt.lock exists" but I want to trade

**Cause:** A previous circuit breaker fired and wrote the lock file. New (non-closing) trades are blocked.

**Fix:** First diagnose what fired (check `risk/halt.lock` contents), then if you want to override:

```bash
python run_risk_check.py --clear-halt
```

See [Handle a circuit breaker](../guides/handle-a-circuit-breaker.md) for the full procedure.

---

## Combined score same as quant composite

**Cause:** AI analyzers didn't produce output for the relevant tickers. Either no `OPENROUTER_API_KEY` set, or the source data tables are empty (no recent filings, no transcripts).

**Fix:**
1. Confirm the key is set: `python -c "import os; print(bool(os.getenv('OPENROUTER_API_KEY')))"`.
2. Confirm the source tables have data:
   ```sql
   SELECT count(*) FROM sec_filings WHERE ticker = 'AAPL';
   SELECT count(*) FROM insider_transactions WHERE ticker = 'AAPL';
   ```
3. Re-run `python run_data.py` (without `--no-filings`) if the tables are empty.

---

## "ai_cache hit" but I want fresh analysis

**Cause:** Layer 3 caches analyzer outputs for `ai.cache_ttl_days` (default 30 days) keyed by `(ticker, analyzer, prompt_hash)`. Re-runs hit the cache.

**Fix:** Clear the relevant rows:

```bash
sqlite3 data/fund.db "DELETE FROM analysis_cache WHERE ticker = 'AAPL';"
```

Or all entries:
```bash
sqlite3 data/fund.db "DELETE FROM analysis_cache;"
```

Then re-run `python run_analysis.py --ticker AAPL`.

---

## Dashboard tabs all empty

**Cause:** No data has flowed through the pipeline yet. The dashboard reads from `fund.db` and `output/` files; empty inputs produce empty tabs.

**Fix:** Run the full pipeline at least once:

```bash
python run_data.py --dev --no-filings --no-13f
python run_scoring.py
python run_dashboard.py
```

After this, Tab II (Research) populates. Other tabs need additional layers — see [run the full pipeline](../guides/run-the-full-pipeline.md).

## See also

- [Run the full pipeline](../guides/run-the-full-pipeline.md) — the canonical successful path
- [Handle a circuit breaker](../guides/handle-a-circuit-breaker.md) — risk-specific issues
