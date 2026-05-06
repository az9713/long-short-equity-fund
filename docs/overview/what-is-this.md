# What is JARVIS?

JARVIS is a single-machine long-short equity hedge-fund research and paper-trading system. It scores a stock universe across eight quantitative factors, layers AI-driven qualitative analysis on top, builds a market-neutral portfolio, monitors risk against hard limits, and executes orders through Alpaca paper trading.

## The problem it solves

A real long-short fund needs eight or nine integrated capabilities — universe management, price and fundamentals data, factor scoring, qualitative research, optimization, risk control, execution, attribution, and reporting. Most retail tools cover one or two. JARVIS bundles all of them into a stack that can be run end-to-end on a laptop using free data sources and a free OpenRouter model tier.

The system is for research and paper-money simulation. It is not a production trading platform.

## How it works — the mental model

Think of JARVIS as an assembly line with seven stations. Each station reads from a SQLite database, does its work, and writes back. The next station picks up where the last left off.

```
                                  ┌─────────────────┐
                                  │  fund.db        │
                                  │  (SQLite)       │
                                  └────────┬────────┘
                                           │
   ┌────────┐  ┌────────┐  ┌────────┐  ┌──┴───┐  ┌────────┐  ┌────────┐  ┌────────┐
   │ Layer 1│→│ Layer 2 │→│ Layer 3 │→│ L4   │→│ Layer 5│→│ Layer 6│→│ Layer 7│
   │  Data  │  │ Scoring │  │   AI    │  │ Port │  │  Risk  │  │  Exec  │  │ Report │
   └────────┘  └─────────┘  └─────────┘  └──────┘  └────────┘  └────────┘  └────────┘
        │            │            │           │          │            │           │
        ▼            ▼            ▼           ▼          ▼            ▼           ▼
   prices,      factor       LLM          target     veto,        Alpaca     Streamlit
   filings,     scores,      JSON         weights    halt,        orders,    dashboard,
   fundamentals composite,   analyses,    + trades   stress,      fills,     letter
                signal       sector       (queued    breakers     slippage
                LONG/SHORT   reports      for                                
                             combined     approval)
                             score
```

Each layer has its own entry-point script (`run_data.py`, `run_scoring.py`, etc.) that you can run independently. There is also a Streamlit dashboard (`run_dashboard.py`) and a backtesting utility (`run_backtest.py`) outside the layered flow.

## The seven layers

| Layer | Module | Entry point | What it produces |
|-------|--------|-------------|------------------|
| 1 — Data | `data/` | `run_data.py` | Universe, prices, fundamentals, SEC filings, insider data, estimates, earnings calendar in `fund.db` |
| 2 — Scoring | `factors/` | `run_scoring.py` | 8 factor scores per ticker, sector-relative composite, LONG/SHORT signal in `output/scored_universe_latest.csv` |
| 3 — AI analysis | `analysis/` | `run_analysis.py` | LLM analyzer outputs (filing, risk, insider, earnings), sector synthesis, combined score |
| 4 — Portfolio | `portfolio/` | `run_portfolio.py` | Target weights via MVO or conviction-tilt; trade list in `position_approvals` |
| 5 — Risk | `risk/` | `run_risk_check.py` | Pre-trade veto results, circuit-breaker dashboard, stress test scenarios, halt lock |
| 6 — Execution | `execution/` | `run_execution.py` | Alpaca paper-trading orders, fills, slippage statistics |
| 7 — Reporting | `reporting/`, `dashboard/` | `run_dashboard.py` | Tear sheet, P&L attribution, weekly letter, Streamlit UI |

Plus one standalone utility: `run_backtest.py` for walk-forward simulation against historical data.

## A typical end-to-end flow

The intended nightly cycle, in order:

1. **Refresh data.** `python run_data.py` pulls today's prices, fundamentals, and any new filings. Run nightly after market close.
2. **Score the universe.** `python run_scoring.py` recomputes all eight factors, ranks within each GICS sector, and labels each ticker `LONG`, `SHORT`, or `NEUTRAL`. Output is a CSV.
3. **Run AI analysis** *(optional)*. `python run_analysis.py` uses OpenRouter to generate qualitative analyses for the top 20 longs and top 20 shorts, then computes a combined score that blends quant and AI views.
4. **Construct the portfolio.** `python run_portfolio.py --whatif` previews the rebalance. `--rebalance` queues the trades as `PENDING` for approval.
5. **Risk-check.** `python run_risk_check.py` confirms no circuit breakers are tripped and all factor exposures are within limits. Approve trades by promoting them to `APPROVED` in the database.
6. **Execute.** `python run_execution.py --execute` sends approved trades to Alpaca paper trading as limit orders.
7. **Report.** `python run_dashboard.py` opens the Streamlit UI at `http://localhost:8502` for review.

You can run any single step independently — each script reads from and writes to the same SQLite database, so state persists across runs.

## What this is NOT

- **Not a live trading system.** Execution is locked to Alpaca paper trading by default. Live mode requires an explicit environment variable that the user must set deliberately.
- **Not a backtest of a real strategy.** Backtests use *current* fundamentals and a *current* S&P 500 universe, so they have look-ahead and survivorship bias. They are directional only.
- **Not a black box.** Every factor score, every veto reason, every trade is logged to SQLite. Inspect the database directly when something is unclear.
- **Not multi-user.** State is local SQLite. There is no auth, no sharing, no cloud sync.
- **Not real-time.** The system is designed for an end-of-day rebalance cadence, not intraday signals.
