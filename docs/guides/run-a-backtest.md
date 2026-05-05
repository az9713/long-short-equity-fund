# Run a backtest

Walk-forward simulation of the long-short signal against historical prices. Read [the backtesting concepts page](../concepts/backtesting.md) first — the bias caveats are not optional context.

## Prerequisites

- `daily_prices` populated for the date range you want to test. Run `python run_data.py` first; for ranges before the default 5-year history, you may need to edit `data/market_data.py: update_prices` to fetch a longer period.
- For `--full-score` mode, `fundamentals_quarterly` and `fundamentals_annual` must be populated. **Use only with bias caveats accepted.**

## Steps

### 1. Run the default momentum-only backtest

```bash
python run_backtest.py --start 2021-01-01 --end 2024-12-31
```

This uses the **momentum factor only** (12-1 month price return), which has clean point-in-time history. Default cohort sizes are 20 long / 20 short; default rebalance cadence is monthly.

In dev mode (10 tickers), use:

```bash
python run_backtest.py --dev --start 2022-01-01
```

Cohort sizes auto-shrink to fit the universe.

### 2. Add transaction costs

```bash
python run_backtest.py --start 2021-01-01 --with-costs
```

Subtracts 10 bps per round-trip rebalance. A more realistic estimate.

### 3. Adjust cohort size

```bash
python run_backtest.py --num-longs 10 --num-shorts 10 --start 2021-01-01
```

Smaller cohorts → more concentrated portfolio → higher per-name impact, more noise in the equity curve.

### 4. Run the full 8-factor composite (biased)

```bash
python run_backtest.py --full-score --start 2023-01-01 --dev
```

The script prints a bias warning at startup. The composite uses *current* fundamentals, so any reported alpha includes look-ahead. Use only to compare *relative* performance of factor weightings.

## Verification

Output ends with a summary block:

```
============================================
  Backtest Results
============================================
  Period:          2021-01-01 → 2024-12-31
  Mode:            momentum_only (with-costs)
  Cohort:          20 longs / 20 shorts
  Months:          48
  Total return:    +14.2%
  Annualized:       +3.4%
  Annualized vol:   8.7%
  Sharpe:           0.39
  Max drawdown:    -6.1%

  Output: output/backtest_2021-01-01_2024-12-31_mom.json
```

The JSON contains the equity curve and monthly returns for charting.

### View in dashboard

```bash
python run_dashboard.py
```

Tab VII (Backtest) reads `output/backtest_latest.json` (a copy of the most recent run) and renders the equity curve plus summary stats. Re-run a backtest, refresh the tab, and it shows the new run.

## Tuning

| To change | Edit |
|-----------|------|
| Rebalance frequency | `run_backtest.py: REBALANCE_FREQ` (currently `"M"` for monthly) |
| Risk-free rate for Sharpe | `run_backtest.py: RISK_FREE_RATE` (currently 0.04) |
| Cost model | `run_backtest.py: COST_BPS_ROUNDTRIP` (currently 10) |
| Universe | `--dev` for 10 tickers, otherwise full S&P 500 |
| Signal | Default momentum-only; `--full-score` for composite |

## Common gotchas (this task)

**Backtest runs but Sharpe is `nan`.** Fewer than 2 months of data, or std-deviation of monthly returns is 0. Extend the date range or pick a noisier universe.

**Annualized return is positive but Sharpe is negative.** The risk-free rate (4%) exceeds your annualized return. Either you're underperforming cash or your dataset is short.

**`--full-score` runs but throws warnings about missing fundamentals.** yfinance failed to populate quarterly fundamentals for some tickers. Affected tickers default to score 50 (sector median), which softens the signal. Either re-run `python run_data.py` to refresh, or accept the noisier result.

**The dashboard's Backtest tab shows yesterday's run.** It reads `output/backtest_latest.json`. Confirm by inspecting the file's `params` block — if `start_date` matches your last invocation, it's current. If not, re-run.

## See also

- [Backtesting](../concepts/backtesting.md) — what the script does and its biases
- [ADR-002](../architecture/adr/002-backtesting-as-utility-not-layer.md) — why this is a utility, not a pipeline layer
