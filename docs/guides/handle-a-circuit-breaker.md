# Handle a circuit breaker

Circuit breakers fire when JARVIS detects loss limits being breached. This guide walks through diagnosing the trigger, deciding whether to override, and clearing the halt lock.

## Prerequisites

- A live system with at least one execution cycle of history.
- Familiarity with the [risk management concepts](../concepts/risk-management.md).

## Steps

### 1. Identify which breaker fired

The most common signal is one of:

- `run_execution.py --execute` aborts with `KILL_SWITCH ACTIVE — execution aborted`.
- `run_risk_check.py` shows `Circuit Breakers: N BREAKER(S) TRIGGERED`.
- The dashboard's Risk tab shows red/orange status badges.

Run:

```bash
python run_risk_check.py
```

Read the `TRIGGERED` block:

```
TRIGGERED:
  [DAILY_KILL] KILL_SWITCH: Daily P&L -2.7% < limit -2.5%
```

| Level shown | What happened |
|-------------|---------------|
| `DAILY_WARN` | Daily P&L < `daily_loss_limit` (default −1.5%). Reduce sizes; no halt. |
| `DAILY_KILL` | Daily P&L < `daily_halt_limit` (default −2.5%). Halt lock written. |
| `WEEKLY_WARN` | Weekly P&L < `weekly_loss_limit` (default −4%). Reduce sizes; no halt. |
| `DRAWDOWN_KILL` | Drawdown from peak < `drawdown_limit` (default −8%). Halt lock written. |
| `SINGLE_POSITION` | One name > `single_position_nav_limit` (default 3%). Trim alert. |

### 2. Check the halt lock file

```bash
cat risk/halt.lock
```

The file contains the reason and timestamp of the halt:

```json
{
  "timestamp": "2026-05-04T14:32:11Z",
  "reason": "DAILY_KILL: Daily P&L -2.7% < limit -2.5%",
  "portfolio_value": 97300.0
}
```

If the file exists, all execution paths refuse new (non-closing) trades.

### 3. Investigate the loss

Open the dashboard:

```bash
python run_dashboard.py
```

In Tab IV (Performance):
- Inspect today's biggest P&L contributors. Is the loss broad (book-wide drift) or concentrated (one or two names)?
- Compare to the SPY/QQQ benchmark. If the entire market is down 3%, your beta exposure may be the cause.

In Tab III (Risk):
- Check factor exposures. Did the book drift toward a factor that cratered (e.g., heavy momentum + a momentum reversal day)?
- Check correlation. If `effective_bets` dropped sharply, the book was more concentrated than you thought.

In Tab V (Execution):
- Look at recent fills. Anything fill far worse than expected (slippage > 100 bps)? That suggests a liquidity event in a specific name.

### 4. Decide what to do

Three options, ordered by aggressiveness:

**Option A — Wait it out.** Don't clear the halt. Existing positions stay open; only new (non-closing) trades are blocked. Re-evaluate tomorrow.

**Option B — Close down the book.** Closing trades skip pre-trade veto checks 2–8 (see [risk management](../concepts/risk-management.md#the-pre-trade-veto)), so even with the halt active, you can de-risk. To close everything:

```bash
sqlite3 data/fund.db "UPDATE position_approvals SET status='APPROVED' WHERE side IN ('CLOSE_LONG','CLOSE_SHORT');"
```

(Generate the closing trades first via a small script, or use the dashboard if you've added that affordance.)

**Option C — Override and resume.** If the loss was a known one-off (data error, single corp action) and you're confident the breaker was a false positive, clear the halt and resume.

### 5. Clear the halt lock

```bash
python run_risk_check.py --clear-halt
```

The script prints `Halt lock cleared.` and removes `risk/halt.lock`. Persistent state (`risk_state.json`) is recomputed on the next run from `portfolio_history`.

> **Warning:** Clearing a kill-switch halt does NOT reset the loss state. If P&L is still below the limit on the next risk check, the halt re-fires immediately. Wait until at least one of:
> - Daily P&L recovers above the daily kill threshold (typically requires next trading day rollover).
> - Drawdown recovers above the drawdown kill threshold (typically requires positive returns).

### 6. Resume execution

```bash
python run_execution.py --execute
```

The first thing this does is re-run the circuit-breaker check. If the halt re-fires, you cleared it prematurely.

## Verification

A successful clear-and-resume produces:

- `risk/halt.lock` removed
- `python run_risk_check.py` prints `Circuit Breakers: ALL GREEN`
- `python run_execution.py --execute` does not abort with `KILL_SWITCH ACTIVE`

## Common gotchas (this task)

**Halt re-fires after clearing.** The kill-switch is fired by current state, not historical event. If today's P&L is still −2.7% and the limit is −2.5%, the breaker triggers again. Wait for a new trading day or recover.

**Daily P&L shows 0.0% but I just lost 3%.** `daily_pnl` compares current portfolio value against the previous value stored in `risk/risk_state.json`. If the state file is missing or stale (e.g., never updated today), the comparison is meaningless. Run `python run_risk_check.py` first to update the state, then check again.

**Drawdown is huge but I haven't lost much today.** Drawdown is from peak, not from yesterday. If the portfolio value peaked weeks ago and has been drifting down, drawdown can be far larger than today's daily P&L. The `drawdown_limit` is a slow-moving guardrail.

**Circuit breaker fires immediately on a fresh database.** No `risk_state.json` exists yet, so `peak_value` defaults to 100,000 (also the default `portfolio_value`). Drawdown computes as 0%. Daily P&L computes as 0%. No trigger. If you see a trigger anyway, your `risk_state.json` has stale data — delete it:

```bash
rm risk/risk_state.json
python run_risk_check.py
```

## See also

- [Risk management](../concepts/risk-management.md) — full breaker semantics
- [Configuration](../reference/configuration.md) — `risk.*` thresholds
