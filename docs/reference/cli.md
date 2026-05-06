# CLI reference

Every entry-point script and every flag.

All scripts are run from the `ls_equity_fund/` directory:

```bash
python run_<script>.py [flags]
```

## run_data.py

Layer 1 — Refresh data from external sources into `fund.db`.

```bash
python run_data.py [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--no-filings` | off | Skip step 4 (SEC 10-K/10-Q/8-K filings). The slowest step. |
| `--no-13f` | off | Skip step 5 (institutional 13F filings). |
| `--dev` | off | Force dev mode (10 hard-coded tickers) regardless of config. |

**Side effects:** writes to `daily_prices`, `fundamentals`, `sec_filings`, `insider_transactions`, `institutional_holdings`, `short_interest`, `analyst_estimates`, `earnings_calendar`, `transcripts`, `universe`, `cik_map`, `cusip_ticker_map`.

**Exit codes:** 0 success, 1 if `universe` is empty after fetch.

---

## run_scoring.py

Layer 2 — Score the universe across eight factors.

```bash
python run_scoring.py [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--ticker SYMBOL` | none | Score a single ticker; print detailed per-factor breakdown |
| `--sector NAME` | none | Score only tickers in `NAME` (substring match against GICS sector) |

**Output:** `output/scored_universe_latest.csv` (and a dated copy `output/scored_universe_<YYYYMMDD>.csv`).

**Exit codes:** 0 success, 1 if no results produced.

---

## run_analysis.py

Layer 3 — Run AI analyzers on candidates.

```bash
python run_analysis.py [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--estimate-cost` | off | Print token estimate and exit; no LLM calls |
| `--ticker SYMBOL` | none | Run all four analyzers on a single ticker |
| `--sector NAME` | none | Run analyzers + sector synthesis for one sector |
| (none) | — | Full run: top 20 LONG + top 20 SHORT |

**Side effects:** writes to `analysis_cache`, `ai_cost_log`. Writes per-ticker reports to `output/reports/`.

**Exit codes:** 0 success, 1 if scored universe CSV missing.

**Without `OPENROUTER_API_KEY`:** all analyzer calls are skipped; combined score = quant composite.

---

## run_portfolio.py

Layer 4 — Construct the target portfolio.

```bash
python run_portfolio.py [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--current` | — | Show current positions, P&L, beta, pending approvals |
| `--whatif` | — | Print proposed rebalance without committing |
| `--rebalance` | — | Generate trades and queue them as `PENDING` in `position_approvals` |
| `--optimize-method {mvo,conviction}` | from config | Override `portfolio.optimize_method` for this run |

Exactly one of `--current`, `--whatif`, `--rebalance` must be provided.

**Side effects:** `--rebalance` writes to `position_approvals` (status `PENDING`). `--current` reads only.

**Exit codes:** 0 success, 1 if scored universe missing or no candidates.

---

## run_risk_check.py

Layer 5 — Risk dashboard, stress test, halt-lock control.

```bash
python run_risk_check.py [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| (none) | — | Full risk dashboard (circuit breakers, factor monitor, correlation, MCTR) |
| `--stress` | — | Run all 6 stress scenarios |
| `--tail-only` | — | Just VIX + credit-spread check |
| `--clear-halt` | — | Remove `risk/halt.lock` |

**Side effects:** updates `risk/risk_state.json`. `--clear-halt` removes `risk/halt.lock`.

**Exit codes:** 0 always.

---

## run_execution.py

Layer 6 — Place orders on Alpaca paper trading.

```bash
python run_execution.py [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run` | — | Print orders that would be placed; no Alpaca calls |
| `--execute` | — | Submit `APPROVED` trades to Alpaca |
| `--status` | — | Show open orders, slippage stats, recent fills |
| `--cancel-all` | — | Cancel every open order |

Exactly one flag is required.

**Side effects:** `--execute` writes to `open_orders`, `order_log` (slippage is a column on `order_log`, not a separate table). On fill: `portfolio_positions`, `portfolio_history`.

**SIGINT handling:** during `--execute`, Ctrl-C cancels all pending orders before exit.

**Exit codes:** 0 success.

---

## run_dashboard.py

Layer 7 — Launch the Streamlit dashboard.

```bash
python run_dashboard.py
```

No flags. Starts Streamlit on the port in `config.yaml: dashboard.port` (default 8502). Equivalent to:

```bash
streamlit run dashboard/app.py --server.port 8502
```

Stop with Ctrl-C.

---

## run_backtest.py

Standalone walk-forward backtest. Not a layer.

```bash
python run_backtest.py [flags]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start YYYY-MM-DD` | 3 years ago | Backtest start date |
| `--end YYYY-MM-DD` | yesterday | Backtest end date |
| `--method {mvo,conviction}` | `conviction` | Portfolio construction method |
| `--num-longs N` | 20 | Long cohort size |
| `--num-shorts N` | 20 | Short cohort size |
| `--rebalance-days N` | 21 | Rebalance frequency in trading days (21 ≈ monthly) |
| `--with-costs` | off | Subtract 10 bps per round-trip rebalance |
| `--dev` | off | Use `dev_tickers` (10 names) |
| `--output-dir PATH` | `output/backtest/` | Where to write CSV/TXT artifacts |
| `--full-score` | off | Use 8-factor composite (BIASED — see backtesting docs) |

**Output:** `output/backtest/equity_curve.csv`, `monthly_returns.csv`, `rebalance_log.csv`, `summary.txt`.

**Exit codes:** 0 success, 1 if no rebalance periods can complete (insufficient price history).

## See also

- [Configuration](configuration.md) — every config field
- [Environment variables](env-vars.md) — every `.env` key
