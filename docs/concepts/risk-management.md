# Risk management

Layer 5. Owns the pre-trade veto, circuit breakers, factor risk model, correlation monitor, tail-risk monitor, and stress tests. Every trade passes through this layer before execution.

## What it is

A package of eight modules under `risk/` plus the `run_risk_check.py` entry point.

## Module map

| Module | Role |
|--------|------|
| `risk/pre_trade.py` | Eight-check veto applied to every trade before submission |
| `risk/circuit_breakers.py` | Daily, weekly, drawdown loss limits + halt lock |
| `risk/factor_risk_model.py` | Cross-sectional Barra-style factor regression for risk decomposition |
| `risk/factor_monitor.py` | Watch portfolio-level factor exposures vs. limits |
| `risk/correlation_monitor.py` | Per-book correlation, effective-bet count |
| `risk/tail_risk.py` | VIX regime + credit-spread monitor |
| `risk/stress_test.py` | Six counterfactual scenarios applied to current weights |
| `risk/risk_state.py` | Persists risk state to `risk/risk_state.json` |

## The pre-trade veto

`risk/pre_trade.py: pre_trade_veto(ticker, side, shares, price, portfolio_value)` returns `(approved: bool, reason: str)`.

Eight checks, in order:

| # | Check | Triggers when | Source |
|---|-------|---------------|--------|
| 1 | Halt lock | `risk/halt.lock` exists | Always |
| 2 | Earnings blackout | Earnings ≤5 days away | `data/earnings_calendar.py` |
| 3 | Liquidity | Trade > 5% of ADV | `data/market_data.get_adv` |
| 4 | Position size | Trade > 1.5× `max_position_pct` | `config.portfolio.max_position_pct` |
| 5 | Sector exposure | Post-trade sector weight > `max_sector_pct` | `config.portfolio.max_sector_pct` |
| 6 | Gross exposure | Post-trade gross > `gross_limit` | `config.portfolio.gross_limit` |
| 7 | Net beta | Post-trade abs(net beta) > 1.5× `max_beta` | `portfolio/beta.py` |
| 8 | Correlation | Max pairwise corr with existing book > `correlation_veto` | `risk/correlation_monitor.py` |

**Closing trades skip checks 2–8.** Only the halt lock applies — you can always close a position. Detection logic is in `_is_closing_trade`.

Every veto is logged to the `veto_log` table with timestamp, ticker, side, shares, price, and reason.

## Circuit breakers

`risk/circuit_breakers.py: check_circuit_breakers(portfolio_value)` returns a list of triggered breakers.

| Level | Threshold | Source field | Action |
|-------|-----------|--------------|--------|
| Daily warning | P&L < `daily_loss_limit` (default −1.5%) | `risk.daily_loss_limit` | Reduce new positions; log alert |
| Daily kill | P&L < `daily_halt_limit` (default −2.5%) | `risk.daily_halt_limit` | Write `halt.lock`; abort execution |
| Weekly warning | Week P&L < `weekly_loss_limit` (default −4%) | `risk.weekly_loss_limit` | Reduce new positions; log alert |
| Drawdown kill | Drawdown from peak < `drawdown_limit` (default −8%) | `risk.drawdown_limit` | Write `halt.lock`; abort execution |
| Single position | Single name > `single_position_nav_limit` (default 3%) | `risk.single_position_nav_limit` | Force trim alert |
| Correlation alert | Avg book correlation > `correlation_alert` (default 0.60) | `risk.correlation_alert` | Log alert; no halt |

When the kill switch fires, two things happen:

1. `risk/halt.lock` is written with the reason and timestamp.
2. Execution layer's `--execute` mode aborts on its next call (it checks `_check_circuit_breakers` first).

Cleared with `python run_risk_check.py --clear-halt`. The script removes the lock file; persistent state (peak, daily P&L) is recomputed from `portfolio_history` on the next run.

## Factor risk model

`risk/factor_risk_model.py` runs a cross-sectional regression of stock returns on factor exposures over a rolling 60-day window:

```
r_i = α + Σ β_i,k · F_k + ε_i
```

Where `F_k` are the eight factors from Layer 2 (treated as returns proxies, not raw scores) and `β_i,k` is stock i's exposure to factor k.

The output is a covariance matrix decomposing portfolio variance into:

- **Factor risk** — variance from factor exposures (the part explainable by the model).
- **Specific risk** — variance from stock-specific noise (residuals).

Both are surfaced in the risk dashboard. Factor risk should be the *minority* of total risk in a well-diversified book; if it's >70%, you're concentrated on a few factors.

### MCTR

Marginal Contribution to Risk: how much portfolio variance would change if you added 1% to a position's weight.

```
MCTR_i = (Σ · w)_i / σ_p
```

JARVIS flags positions where `|MCTR / weight|` exceeds 1.5× the median — a position contributing more risk than its weight implies. Surfaced in the dashboard's Risk tab.

## Correlation monitor

`risk/correlation_monitor.py: check_correlations(positions)` returns:

- `avg_long_correlation` — mean pairwise correlation across all long pairs
- `avg_short_correlation` — same for shorts
- `effective_bets` — `1 / sum(w_i^2 * (1 + (n-1) * avg_corr))` — a diversification proxy

If `avg_long_correlation > correlation_alert` (default 0.60), an alert fires. If a single pair exceeds `correlation_veto` (default 0.80), the pre-trade veto blocks any new trade that would increase exposure to that pair.

## Tail-risk monitor

`risk/tail_risk.py: check_tail_risk()` reads VIX (via yfinance `^VIX`) and credit spreads (FRED if `FRED_API_KEY` set, else None) and returns:

```python
{
  "vix": 18.4,
  "vix_regime": "LOW",       # LOW < 25, ELEVATED 25-33, HIGH > 33
  "credit_spread": 1.2,      # or None
  "cs_zscore": 0.3,          # or None
  "action": "OK",            # OK | REDUCE | HALT
  "message": "...",
}
```

Actions:
- `OK`: VIX < `vix_reduce_threshold` (default 25)
- `REDUCE`: 25 ≤ VIX < `vix_halt_threshold` (default 33). Halve all new position sizes.
- `HALT`: VIX ≥ 33. Write halt lock.

Credit-spread Z-score adds a separate signal: `cs_zscore > 2.0` triggers a `REDUCE` regardless of VIX.

## Stress tests

`risk/stress_test.py: run_stress_tests(weights)` runs six scenarios and returns a list of result dicts.

| Scenario | Type | Source |
|----------|------|--------|
| 2008 GFC (Sep 2008) | Historical | Replay actual price moves from Sep–Nov 2008 |
| 2020 COVID (Mar 2020) | Historical | Replay Feb–Mar 2020 |
| 2022 inflation/rate hikes | Historical | Replay full year 2022 |
| Beta-1 shock | Synthetic | Apply −10% shock proportional to each name's beta |
| Liquidity stress | Synthetic | Widen all spreads to 100 bps; assume 30% slippage on the shorts |
| Factor crowding unwind | Synthetic | Reverse momentum + value factor returns by 2σ |

Per scenario:

```
{
  "scenario_name": "2008 GFC",
  "total_pnl_pct": -0.083,
  "long_pnl_pct": -0.21,
  "short_pnl_pct": +0.13,
  "worst_contributors": [{"ticker": "...", "contribution": -0.012}, ...],
}
```

Run with `python run_risk_check.py --stress`.

## Risk dashboard

`run_risk_check.py` (no flags) prints a snapshot:

```
RISK DASHBOARD - YYYY-MM-DD
============================================
Circuit Breakers:  ALL GREEN
  Daily P&L:      +0.3% (limit: -1.5%)
  Weekly P&L:     -0.8% (limit: -4.0%)
  Drawdown:       -1.2% (limit: -8.0%)

Tail Risk:
  VIX: 18.4 (LOW regime)
  Credit Spread: 1.2

Factor Monitor:
  No factor alerts

Correlation:
  Long book avg correlation:  0.32
  Short book avg correlation: 0.28
  Effective bets: 18.4

Risk Decomposition:
  Factor risk:   42.1%
  Specific risk: 57.9%
```

The dashboard is the operator's source of truth before promoting `PENDING` trades to `APPROVED`.

## Common gotchas

**`halt.lock` exists but I don't know why.** Open the file — the reason is written on the first line with timestamp.

**Circuit breakers always show 0 daily P&L.** The breakers compare today's `portfolio_value` to the previous one in `risk_state.json`. On a fresh database with no history, both are equal. Run `python run_risk_check.py` daily so the state file accumulates.

**Stress test results show "no positions."** You haven't executed any trades yet (Layer 6) so `portfolio_positions` is empty. Run the stress test after at least one execution, or pass `weights` directly via the Python API for a what-if.

**Factor risk is 100%.** With very few positions (1–2 names), there's no specific-risk diversification possible. Expand the book or accept the concentration.

## See also

- [Execution](execution.md) — what happens after the pre-trade veto returns approved
- [Configuration](../reference/configuration.md) — every `risk.*` field
- [Database schema](../reference/database-schema.md) — `veto_log`
