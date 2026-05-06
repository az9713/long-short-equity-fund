# Portfolio construction

Layer 4. Reads the scored universe, runs Mean-Variance Optimization (MVO) or conviction-tilt, applies hard constraints, and queues trades as `PENDING` for approval.

## What it is

A package of nine modules under `portfolio/` plus the `run_portfolio.py` entry point.

## Module map

| Module | Role |
|--------|------|
| `portfolio/optimizer.py` | Conviction-tilt optimizer (ranking-based) |
| `portfolio/mvo_optimizer.py` | Mean-variance optimizer (scipy SLSQP) |
| `portfolio/state.py` | SQLite schema and CRUD for `portfolio_positions`, `portfolio_history`, `position_approvals` |
| `portfolio/rebalance.py` | Diff target weights vs. current positions → trade list |
| `portfolio/rebalance_schedule.py` | Heuristics for whether a rebalance is warranted today |
| `portfolio/transaction_cost.py` | Estimate cost in bps from spread + market impact |
| `portfolio/beta.py` | Per-ticker and portfolio beta against benchmarks |
| `portfolio/factor_exposure.py` | Decompose portfolio across factor scores for monitoring |

## The two optimizers

### MVO — Mean-Variance Optimization

Minimize `−w·μ + λ·w·Σ·w` subject to:

| Constraint | Default | Field |
|------------|---------|-------|
| Per-position absolute weight | ≤5% | `portfolio.max_position_pct` |
| Per-sector net weight | ≤25% | `portfolio.max_sector_pct` |
| Gross exposure | ≤150% | `portfolio.gross_limit` |
| Net beta | ≤±0.15 | `portfolio.max_beta` |
| Long count | = 20 | `portfolio.num_longs` |
| Short count | = 20 | `portfolio.num_shorts` |

Where:
- `μ` is the expected-return vector, derived from the combined score (or quant composite if AI is absent).
- `Σ` is the 120-day return covariance matrix from `daily_prices`. Tickers with fewer than 60 aligned return rows are excluded.
- `λ` is `portfolio.mvo_risk_aversion` (default 1.0).

Implementation: `scipy.optimize.minimize` with method `SLSQP`. The starting point is the conviction-tilt weights (so the optimizer has a feasible warm start).

If the optimizer fails to converge (or no feasible region exists — possible in dev mode with only 10 tickers), JARVIS falls back to conviction-tilt.

### Conviction-tilt

A simpler ranking-based optimizer:

1. Sort longs by combined score descending; sort shorts ascending.
2. Allocate equal-weight 5% to the top N (long) and bottom N (short), where N = `num_longs`/`num_shorts`.
3. Halve any position whose ticker has earnings within 5 days.
4. Project onto sector / gross / beta constraints by scaling.

Faster, more stable, but ignores covariance — does not optimize for diversification benefit.

**Cap-aware sizing.** When too few candidates exist to fill the gross target without breaching `max_position_pct`, the optimizer accepts a smaller book rather than silently uncapping positions. With 3 LONG candidates and a 5% cap, the long book is sized at 15% gross — not 75% with 25%-sized positions. The fallback target gross is `min(_GROSS_TARGET, max_pos × n_candidates)`. See [changelog #8](../changelog.md#8-conviction-optimizer-silently-breached-the-per-position-cap).

Choose between them in `config.yaml`:

```yaml
portfolio:
  optimize_method: mvo  # mvo | conviction
```

Override per-run with `--optimize-method`.

## Rebalance flow

```
scored_universe.csv  →  combined_score (re-run)  →  MVO/conviction
                                                          │
                                                          ▼
                                                  target_weights dict
                                                          │
                          current portfolio_positions  →  diff
                                                          │
                                                          ▼
                                                   trade list (DataFrame)
                                                          │
                          ┌───────────────────────────────┤
                          ▼                               ▼
                   --whatif: print                --rebalance: insert
                   no DB write                    PENDING rows in
                                                  position_approvals
```

The trade list has one row per change-in-weight ticker:

| Column | Description |
|--------|-------------|
| `ticker` | GICS ticker |
| `side` | `LONG` or `SHORT` |
| `current_shares` | Today's position |
| `target_shares` | Target position |
| `trade_shares` | Difference |
| `trade_usd` | `trade_shares * close` |
| `estimated_cost_bps` | From `transaction_cost.estimate_cost_bps` |

## Turnover budget

`portfolio.turnover_budget` (default 30%) caps how much of the portfolio can churn in a single rebalance — measured as `sum(|trade_usd|) / portfolio_value`. Trades exceeding the budget are trimmed by smallest-delta-weight first.

The budget is **only applied when there are existing positions to churn from.** On the first rebalance from an empty book — by definition 100% turnover — the budget is skipped (otherwise no first-time portfolio could ever be built). See [changelog #9](../changelog.md#9-turnover-budget-killed-every-initial-build-rebalance).

## Approval flow

After `--rebalance`, trades sit as `PENDING` rows in the `position_approvals` table. The execution layer (Layer 6) only acts on rows in the `APPROVED` state.

Promotion from `PENDING` to `APPROVED` is a manual step. Options:

- SQL update directly:
  ```sql
  UPDATE position_approvals SET status = 'APPROVED' WHERE ticker = 'AAPL';
  ```
- A small approval script (not currently included in the codebase — write your own as needed).
- Approve all at once via the dashboard's Execution tab *(if implemented in your build)*.

This human-in-the-loop step is intentional. JARVIS does not auto-execute the optimizer's output.

## Beta and sector neutrality

Beta is computed in `portfolio/beta.py` as the OLS slope of a stock's daily returns against its sector ETF (or SPY for unmappable sectors) over a 60-day window. Sector ETF mapping:

| GICS sector | ETF |
|-------------|-----|
| Information Technology | XLK |
| Financials | XLF |
| Energy | XLE |
| Industrials | XLI |
| Health Care | XLV |
| Communication Services | XLC |
| Consumer Discretionary | XLY |
| Consumer Staples | XLP |
| Materials | XLB |
| Real Estate | XLRE |
| Utilities | XLU |

The MVO net-beta constraint is enforced as `|sum(w_i * beta_i)| <= max_beta`. The conviction optimizer post-projects weights to satisfy the same.

Sector neutrality is enforced as `|sum(w_i for i in sector)| <= max_sector_pct` per sector. Both optimizers respect this.

## Transaction cost model

`portfolio/transaction_cost.py: estimate_cost_bps(ticker, trade_usd, side)` returns:

```
spread_cost_bps + impact_cost_bps
```

Where:
- `spread_cost_bps` = `execution.slippage_spread_bps` (default 5)
- `impact_cost_bps` = `execution.market_impact_coeff * sqrt(trade_usd / adv_usd) * 10_000`

The MVO objective subtracts `cost_as_return(trade_usd)` from expected return, so high-impact trades are de-prioritized.

## Earnings blackout halving

Both optimizers halve the target weight for any ticker with earnings within 5 calendar days. This dampens position size around binary-event volatility without forcing a full close.

## Common gotchas

**MVO falls back to conviction every run.** Likely an infeasible region in dev mode (too few tickers). Switch `optimize_method: conviction` in config to silence the fallback log lines.

**Trade list is empty.** Either there are no candidates in the scored CSV with `signal in (LONG, SHORT)`, or the diff between target and current is below the rebalance threshold. Check `rebalance_schedule.py` for the threshold (currently 1% per position).

**Net exposure is non-zero in MVO output.** The MVO net constraint uses `net_limit` (default 0.10, i.e. 10%), not 0. The fund is approximately market-neutral, not strictly zero-net.

**Sector counts don't match `num_longs`/`num_shorts`.** Those are *intent* values used by the conviction optimizer. MVO can land on different counts within the constraints. Adjust `num_longs`/`num_shorts` if you want stricter cardinality.

## See also

- [Risk management](risk-management.md) — what veto and halt checks the trade list passes through next
- [Configuration](../reference/configuration.md) — every `portfolio.*` field
- [Database schema](../reference/database-schema.md) — `portfolio_positions`, `position_approvals`
