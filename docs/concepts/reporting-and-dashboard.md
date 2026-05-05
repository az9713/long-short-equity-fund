# Reporting and dashboard

Layer 7. The Streamlit dashboard plus six reporting modules. Read-only over the data and state from Layers 1–6.

## What it is

Two packages: `reporting/` for the analysis modules, `dashboard/app.py` for the Streamlit UI. Plus the `run_dashboard.py` launcher.

## Module map

| Module | Output |
|--------|--------|
| `reporting/pnl_attribution.py` | Decompose P&L by sector, factor, and individual position |
| `reporting/win_loss.py` | Hit rate, average winner/loser, profit factor |
| `reporting/sector_performance.py` | Per-sector P&L and contribution |
| `reporting/turnover.py` | Period turnover stats |
| `reporting/tear_sheet.py` | Comprehensive monthly tear sheet (Sharpe, Sortino, max DD, beta, alpha) |
| `reporting/commentary.py` | Generate weekly investor letter via the AI client |
| `dashboard/app.py` | Streamlit UI |

## The dashboard

Launched via `python run_dashboard.py`. Streamlit serves at `http://localhost:8502` (configurable via `dashboard.port`).

Seven tabs, in order:

| Tab | Reads from | Shows |
|-----|------------|-------|
| I · Portfolio | `portfolio_positions`, `position_approvals` | Current positions, P&L, sector exposure, beta, pending approvals |
| II · Research | `output/scored_universe_latest.csv`, `analysis/cache` | Factor breakdowns per ticker, AI analyses, sector synthesis |
| III · Risk | `risk/risk_state.json`, `veto_log` | Circuit-breaker status, factor exposures, MCTR, correlation, recent vetoes |
| IV · Performance | `portfolio_history`, `fills` | Cumulative P&L, drawdown chart, win/loss stats, tear sheet |
| V · Execution | `orders`, `fills` | Open orders, recent fills, slippage statistics |
| VI · Letter | `reporting/commentary.py` output | Weekly investor letter (Markdown rendered) |
| VII · Backtest | `output/backtest_*.json` | Backtest equity curve, summary stats from prior `run_backtest.py` invocations |

The dashboard is read-only by design. It does not place trades, modify approvals, or run the pipeline. Use the CLI `run_*.py` scripts for those actions.

## Visual style

Custom CSS in `dashboard/app.py` provides:

- Dark background (`#000e17`) with indigo accent (`#6366f1`)
- Card-styled metric blocks with gradient backgrounds
- VIX badge color-coded by regime (green/yellow/red)
- Plotly charts with the same color palette

Streamlit's default chrome (header, footer, hamburger menu) is hidden via `display: none` in the global CSS.

## Tear sheet

`reporting/tear_sheet.py: generate_tear_sheet(start_date, end_date)` returns a dict and renders a Plotly figure with:

| Metric | Calculation |
|--------|-------------|
| Total return | `(equity[-1] / equity[0]) - 1` |
| Annualized return | `(1 + total_return) ** (252/n_days) - 1` |
| Annualized volatility | `daily_returns.std() * sqrt(252)` |
| Sharpe ratio | `(annualized_return - rf) / annualized_vol`, rf = 0.04 |
| Sortino ratio | Sharpe but with downside-only volatility |
| Max drawdown | `min(equity / equity.cummax() - 1)` |
| Win rate | `count(daily_returns > 0) / count(daily_returns)` |
| Beta vs SPY | OLS slope of fund returns on SPY returns |
| Alpha vs SPY (annualized) | OLS intercept × 252 |

The chart panel includes equity curve, drawdown, rolling Sharpe (60-day), and a return distribution histogram.

## Weekly investor letter

`reporting/commentary.py: generate_weekly_letter()` calls the AI client with:

- The week's P&L by sector
- The top 3 winners and top 3 losers
- New positions opened, positions closed
- Aggregated sector synthesis from Layer 3

It produces a Markdown letter with sections: *Performance recap*, *Notable positions*, *Sector view*, *Risk and outlook*. The output is shown verbatim in the Letter tab and saved to `output/letters/YYYYMMDD.md`.

If `OPENROUTER_API_KEY` is unset, the letter falls back to a deterministic template (no narrative — just the numbers).

## P&L attribution

`reporting/pnl_attribution.py: attribute_pnl(start_date, end_date)` decomposes the period's P&L into:

| Bucket | Definition |
|--------|------------|
| Long book | Sum of P&L across all positions where `side = LONG` |
| Short book | Sum of P&L across all positions where `side = SHORT` |
| By sector | Sum of P&L grouped by ticker's sector |
| By factor | Regression-style: estimated contribution of each factor exposure to portfolio return |
| By position (top 10) | Largest 10 P&L contributors, signed |

The factor decomposition uses the same rolling factor regression as the risk model — there's no separate model.

## Turnover

`reporting/turnover.py: compute_turnover(period)` returns:

```
turnover_pct = sum(|trade_usd|) / portfolio_value
             over the period
```

Tracked monthly. The fund's `portfolio.turnover_budget` (default 0.30, i.e. 30% per month) is a soft target — exceeding it does not block trades but is flagged in the dashboard.

## Common gotchas

**Tabs are empty.** Most tabs need at least one execution cycle to have data. Fresh DBs show "no data" placeholders. Run a full pipeline once to populate.

**Letter tab shows the deterministic template.** No `OPENROUTER_API_KEY` set, or the call failed. Check `output/letters/` — the most recent file shows whether AI text or the template ran.

**Tear sheet metrics are inf or NaN.** Insufficient history (< 5 days). Wait for more activity.

**Streamlit shows "ScriptRunner: Could not connect."** Port 8502 is already in use. Change `dashboard.port` in config and restart.

**Backtest tab is empty.** No `output/backtest_*.json` files yet. Run `python run_backtest.py --start 2022-01-01 --dev` to produce one.

## See also

- [Backtesting](backtesting.md) — what populates the Backtest tab
- [Configuration](../reference/configuration.md) — `dashboard.port`, `reporting.*`
