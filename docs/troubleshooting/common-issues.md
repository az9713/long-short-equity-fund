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

**Cause:** No candidates with `signal in (LONG, SHORT)` in `output/scored_universe_latest.csv`. All signals are HOLD.

**Fix:** Inspect the CSV:

```bash
sqlite3 -separator ',' :memory: \
  "select count(*), signal from read_csv('output/scored_universe_latest.csv') group by signal;"
```

(Or just open the CSV.) If everything is HOLD, the composite scores are too clustered around 50. Causes:

- Dev mode with sparse sectors → many factors fall back to score 50.
- Universe is too small for sector-relative ranking to differentiate.

Switch to full universe or relax thresholds in `factors/composite.py: _assign_signal`.

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
