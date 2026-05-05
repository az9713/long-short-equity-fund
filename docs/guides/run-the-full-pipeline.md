# Run the full pipeline

End-to-end nightly cycle: data refresh → scoring → AI → portfolio → risk → execution → review. About 30 minutes on the full S&P 500, 5 minutes in dev mode.

## Prerequisites

- Quickstart completed once (database initialized, dependencies installed).
- `.env` filled with at minimum `SEC_USER_AGENT_EMAIL`. `OPENROUTER_API_KEY` and Alpaca keys recommended; system degrades gracefully without them.
- `dev_mode: true` in `config.yaml` for first runs; flip to `false` when ready for full universe.

## Steps

### 1. Refresh data

```bash
python run_data.py
```

For dev mode, add `--dev`. To skip the slowest steps on first runs:

```bash
python run_data.py --dev --no-filings --no-13f
```

Verify: the script ends with a summary printing nonzero `Tickers updated` and `Price bars stored`.

### 2. Score the universe

```bash
python run_scoring.py
```

Verify: `output/scored_universe_latest.csv` exists and has rows. Spot-check one ticker:

```bash
python run_scoring.py --ticker AAPL
```

This prints the per-subfactor breakdown for AAPL.

### 3. Run AI analysis (optional but recommended)

```bash
python run_analysis.py --estimate-cost
```

Prints token estimate. The configured Gemini free model costs $0; if you've changed the model, this is your sanity check.

Then run for real:

```bash
python run_analysis.py
```

Top 20 longs + top 20 shorts × 4 analyzers = ~160 LLM calls. Expect 12 minutes the first run; cache hits speed subsequent runs to seconds.

If you want only one ticker:

```bash
python run_analysis.py --ticker AAPL
```

Verify: combined scores in `output/scored_universe_latest.csv` (the `combined_score` column populates after this step).

### 4. Preview the rebalance

```bash
python run_portfolio.py --whatif
```

Prints proposed weights, trade list, estimated turnover, and any warnings. Nothing is committed.

Read the output. If it looks reasonable, commit:

```bash
python run_portfolio.py --rebalance
```

This inserts trades as `PENDING` in `position_approvals`. Check current state any time:

```bash
python run_portfolio.py --current
```

### 5. Risk-check

```bash
python run_risk_check.py
```

Look for `Circuit Breakers: ALL GREEN`. If any breaker is triggered, stop and investigate before proceeding to execution. See [handle a circuit breaker](handle-a-circuit-breaker.md).

For a deeper view, run all six stress scenarios:

```bash
python run_risk_check.py --stress
```

### 6. Approve trades

This is a manual human-in-the-loop step. Promote `PENDING` rows to `APPROVED` in the database. The simplest way:

```bash
sqlite3 data/fund.db "UPDATE position_approvals SET status='APPROVED' WHERE status='PENDING';"
```

To approve only specific tickers:

```bash
sqlite3 data/fund.db "UPDATE position_approvals SET status='APPROVED' WHERE ticker IN ('AAPL','MSFT');"
```

### 7. Execute

Always dry-run first:

```bash
python run_execution.py --dry-run
```

This prints the orders that *would* be placed: ticker, side, shares, limit price, estimated slippage, ADV percent. No Alpaca calls. Confirm the numbers match what you expected from `--whatif`.

Then execute:

```bash
python run_execution.py --execute
```

Each approved trade goes through the eight-check pre-trade veto, then (if it passes) becomes an Alpaca limit order at ±10 bps. Watch the output for any veto reasons.

After execution, check the order book:

```bash
python run_execution.py --status
```

Limit orders may sit unfilled until market activity reaches the limit price; use `gtc` time-in-force (default) to leave them through end of day.

### 8. Review

Launch the dashboard:

```bash
python run_dashboard.py
```

Open `http://localhost:8502`:

- Tab I (Portfolio): new positions, P&L, beta
- Tab III (Risk): factor exposures, breakers
- Tab V (Execution): orders, slippage stats
- Tab VI (Letter): regenerate the weekly letter via the dashboard button (or run `python -c "from reporting.commentary import generate_weekly_letter; generate_weekly_letter()"`)

## Verification

A successful nightly run produces all of:

- New rows in `daily_prices` for today
- A fresh `output/scored_universe_latest.csv` with today's date in the equivalent dated copy
- Updated `position_approvals` (rows now `APPROVED` or `EXECUTED`)
- New rows in `orders` and `fills`
- `risk/risk_state.json` updated with today's `portfolio_value`

## Troubleshooting this guide

**Step 4 says "no candidates."** Layer 2 didn't produce any LONG/SHORT signals (composite outside the [30, 70] band). In dev mode this is common with 10 tickers — switch to a larger universe or relax thresholds in `factors/composite.py`.

**Step 5 shows nonzero P&L on day 0.** Expected — the breakers compare today's portfolio value to the *previous* state file value. On a freshly populated DB, `risk_state.json` doesn't exist yet, so day 0 P&L looks like 0.0. Day 1 onwards it's accurate.

**Step 7 fills slow or not at all.** Limit orders at ±10 bps don't always fill, especially in low-volume names. Use `--cancel-all` to clear the book and re-run, or widen the limit offset in `run_execution.py: _run_execute`.

## See also

- [Run a backtest](run-a-backtest.md) — separate workflow, not part of nightly
- [Handle a circuit breaker](handle-a-circuit-breaker.md) — what to do if step 5 trips a breaker
