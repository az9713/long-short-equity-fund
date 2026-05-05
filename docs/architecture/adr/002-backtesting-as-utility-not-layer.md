# ADR 002: Backtesting as a standalone utility, not a pipeline layer

**Status:** Accepted

## Context

Backtesting was originally proposed as Layer 8 in the system, with its own entry point `run_backtest.py` invoked sequentially after the dashboard. The reasoning was symmetry — every other capability has a layer, so backtesting should too.

Reflection during planning surfaced two issues:

1. **Frequency.** Backtests are run sparingly — to validate a new factor, evaluate a weight change, or convince oneself the system has any signal at all. Running a backtest on every nightly cycle wastes minutes for no benefit.
2. **Bias.** JARVIS backtesting has known look-ahead and survivorship biases (yfinance fundamentals are current, universe is today's S&P 500). Putting it in the production cycle would risk users treating biased numbers as a real performance metric, when they should be directional sanity checks only.

## Decision

Make `run_backtest.py` a **standalone utility**. It is not invoked by any nightly script. It writes to `output/backtest_*.json` and is consumed only by the Backtest tab in the dashboard.

The backtest is invoked manually:

```bash
python run_backtest.py --start 2021-01-01 --dev
```

It is not part of `run_data.py → run_scoring.py → run_analysis.py → run_portfolio.py → run_risk_check.py → run_execution.py → run_dashboard.py`.

## Alternatives considered

### Option A: Layer 8 in the nightly cycle
- Pros: Symmetric with the seven layers. One-click "run everything."
- Cons: Wastes time and storage every night. Encourages misuse — daily numbers from a biased backtest will be over-trusted.

### Option B: Standalone utility (chosen)
- Pros: Run when needed, ignore otherwise. Caveats and warnings can be loud at the CLI level. Easier to iterate on without affecting the main pipeline.
- Cons: Slightly less discoverable. Users have to know the script exists.

### Option C: Cron-scheduled weekly run
- Pros: Out of the daily cycle but still automated.
- Cons: Same misuse risk as Option A. Cron is not consistent with the project's "single user, single machine, run scripts manually" philosophy.

## Rationale

Backtesting is fundamentally a research artifact, not a production output. The layered architecture serves the *production* nightly cycle (decide what to trade tomorrow). Backtesting belongs to a different mode of operation (evaluate a hypothesis about strategy parameters) that doesn't share state or cadence with production.

The user is also better-served by friction here. Re-running a backtest manually forces a moment of "do I actually need this number, and what bias is in it?" — which is healthier than auto-generating it.

## Trade-offs

- **Discoverability.** Users may not realize the backtester exists. Mitigated by referencing it from the dashboard, the CLI reference, and the onboarding docs.
- **No automatic regression detection.** A subtle change to scoring that breaks historical backtests won't be caught by the nightly cycle. Mitigated by recommending users run a backtest after any significant scoring change.

## Consequences

- The dashboard's Backtest tab is empty until the user runs `run_backtest.py` at least once. The tab's "no data" placeholder explicitly tells the user how to populate it.
- Backtest output goes to `output/backtest_*.json`, parallel to but not part of the production data files.
- Bias caveats are printed at every invocation and documented in [the backtesting concept page](../../concepts/backtesting.md). Users cannot ignore them.
