# Backtesting

A standalone walk-forward simulator. Not a layer — runs separately via `run_backtest.py` and writes its own output files. See [ADR-002](../architecture/adr/002-backtesting-as-utility-not-layer.md) for why.

## What it is

A single 728-line script `run_backtest.py` that loops month-by-month over a historical window, building a price matrix from `daily_prices`, computing momentum (or full composite) signals from past data, applying simple long-short weights, and accumulating equity.

## Bias caveats — read first

JARVIS backtesting has known biases. They are not fixed; they are **acknowledged**. Use results as directional sanity checks, never as predictions.

| Bias | Source | Effect |
|------|--------|--------|
| Look-ahead in fundamentals | yfinance returns *current* fundamentals, not point-in-time | Value/quality factors look at today's balance sheet for trades dated 3 years ago |
| Survivorship bias | Universe is *today's* S&P 500 | Bankrupt/delisted names don't appear; their losses are missed |
| No transaction costs by default | Costs added only with `--with-costs` | Returns overstated by ~10 bps per round-trip |
| Signal stale within month | Signals computed at month-start, held all month | Real-time updates not modeled |

Mitigations:

- The default `run_backtest.py` mode uses **only momentum** (12-1 month price return) — the one factor whose history is point-in-time correct.
- `--full-score` mode runs the full 8-factor composite. **Use only with the look-ahead caveat in mind.** A printed warning makes this explicit at runtime.
- `--with-costs` adds a 10 bps round-trip cost estimate per rebalance.

## How walk-forward works

```
for month_start in [start, start+1mo, start+2mo, ..., end]:
    signal_date = month_start
    holding_period = month_start → month_end

    # Compute signal using data available at month_start
    if mode == "momentum_only":
        scores = momentum(prices ending at signal_date - 1d)
    else:  # full_score
        scores = composite_score()  # uses CURRENT data — biased

    # Build portfolio
    longs = top_N_by_score(scores)
    shorts = bottom_N_by_score(scores)

    # Hold for the month
    long_return  = avg(price[month_end] / price[month_start] - 1) for longs
    short_return = avg(1 - price[month_end] / price[month_start]) for shorts
    monthly_return = (long_return + short_return) / 2

    # Optional: subtract costs
    if with_costs:
        monthly_return -= 0.001  # 10bps round trip

    equity[month_end] = equity[prev_month_end] * (1 + monthly_return)
```

Output: monthly equity curve, total return, annualized return, Sharpe, max drawdown.

## CLI

```bash
python run_backtest.py [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--start YYYY-MM-DD` | `2021-01-01` | Backtest start date |
| `--end YYYY-MM-DD` | today | Backtest end date |
| `--dev` | off | Use dev_tickers (10 names) instead of full universe |
| `--full-score` | off | Use 8-factor composite instead of momentum-only (biased; warning printed) |
| `--with-costs` | off | Subtract 10 bps per rebalance |
| `--num-longs N` | 20 | Number of long names per period |
| `--num-shorts N` | 20 | Number of short names per period |

## Output

Two files per run:

- `output/backtest_<start>_<end>_<mode>.json` — full results, including the equity curve, monthly returns, summary stats.
- `output/backtest_latest.json` — copy of the most recent run, used by the dashboard's Backtest tab.

JSON schema:

```json
{
  "params": {"start": "...", "end": "...", "mode": "...", ...},
  "equity_curve": [{"date": "...", "value": 1.0}, ...],
  "monthly_returns": [...],
  "summary": {
    "total_return": 0.142,
    "annualized_return": 0.034,
    "annualized_vol": 0.087,
    "sharpe": 0.39,
    "max_drawdown": -0.061,
    "n_months": 36
  }
}
```

## Performance

A 4-year backtest on the dev (10-ticker) universe runs in 5–10 seconds. On the full S&P 500 it takes 1–3 minutes (price matrix construction is the dominant cost). The script holds the full price matrix in memory; expect ~500 MB for full-universe 5-year runs.

## Common gotchas

**Backtest dies with "price matrix empty."** No `daily_prices` rows for the requested date range. Run `python run_data.py` first; if running on a fresh database, you may need a long-history price pull (yfinance default is 5 years — set `--period max` in `data/market_data.py` if needed).

**Sharpe is implausibly high (>2).** Check whether `--full-score` is on. Without point-in-time fundamentals, look-ahead bias inflates results dramatically. Re-run in default momentum-only mode.

**Equity curve flat.** Signal isn't differentiating positions enough. Check that `--num-longs` and `--num-shorts` aren't both > universe-size / 2 (in dev mode with 10 tickers and 5 each, the entire universe is in the portfolio at all times).

**Results don't match dashboard.** The Backtest tab reads `output/backtest_latest.json`. If you ran with `--start 2010` last week and re-ran with `--start 2022` today, the latest file has the new run — the dashboard updates accordingly. Check the `params` block in the JSON.

## What this is not

- **Not a Monte Carlo simulator.** Single deterministic walk per call.
- **Not a portfolio optimizer backtest.** Uses equal-weight, not MVO. The MVO covariance estimation is too slow to run point-in-time at every monthly rebalance.
- **Not point-in-time correct.** See bias caveats. To do this properly requires CRSP / Compustat point-in-time data, which is out of scope.

## See also

- [ADR-002](../architecture/adr/002-backtesting-as-utility-not-layer.md) — why this is a utility, not a layer
- [Run a backtest](../guides/run-a-backtest.md) — task-oriented walkthrough
