# Execution

Layer 6. Sends approved trades to Alpaca paper trading as limit orders, reconciles fills, tracks slippage.

## What it is

A package of five modules under `execution/` plus the `run_execution.py` entry point.

## Module map

| Module | Role |
|--------|------|
| `execution/broker.py` | Singleton `broker` wrapping `alpaca-py` `TradingClient`; account sync; paper/live mode gating |
| `execution/order_manager.py` | Submit limit orders; reconcile statuses with Alpaca; cancel pending |
| `execution/short_check.py` | Check borrow availability before submitting a short |
| `execution/slippage.py` | Per-fill slippage measurement; rolling 30-day stats |
| `execution/executor.py` | Top-level `execute_approved_trades` orchestrator |

## How a trade flows

```
position_approvals
  status=APPROVED
       │
       ▼
   pre_trade_veto (Layer 5) ──── reject ─→ veto_log
       │
       ▼ approve
   short borrow check (if SHORT) ── unavailable ─→ skip
       │
       ▼
   build limit order:
     buy:  limit = last_close * 1.001  (10 bps above)
     sell: limit = last_close * 0.999  (10 bps below)
       │
       ▼
   broker.submit_order  ──── Alpaca paper API
       │
       ▼
   sync_order_status  ─────  poll Alpaca for fills
       │
       ▼
   on FILLED:
     - update portfolio_positions
     - record slippage in fills
     - log to portfolio_history
```

## Broker singleton

`execution/broker.py` exports a module-level `broker` object built once at import. It holds the `TradingClient` if both `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` are set; otherwise `broker.client = None`. The `--dry-run` mode in `run_execution.py` explicitly nulls the client to be safe.

The broker's `paper` flag is computed by `_is_live_mode()`:

```python
mode = config.execution.mode  # "paper" | "live"
confirmed = os.getenv("ALPACA_LIVE_CONFIRMED", "")
live = (mode == "live" and confirmed == "YES_I_UNDERSTAND_THE_RISKS")
```

Live mode requires both the config switch *and* the explicit env var. Either alone leaves the broker in paper mode. See [ADR-003](../architecture/adr/003-paper-trading-only.md).

## Order parameters

Every JARVIS order is:

| Field | Value |
|-------|-------|
| Order type | `LIMIT` |
| Time in force | `gtc` (default; configurable via `execution.time_in_force`). **Auto-overridden to `day` for fractional shares** — Alpaca rejects GTC for fractional quantities. See [changelog #12](../changelog.md#12-alpaca-rejected-every-paper-order-fractional-orders-must-be-day-orders). |
| Limit offset | ±10 bps from last close |
| Side | `BUY`, `SELL`, `SELL_SHORT`, `BUY_TO_COVER` (mapped from JARVIS LONG/SHORT semantics) |
| Quantity | Computed from target weight × portfolio value / last close, rounded to 4 decimal places |

Market orders are intentionally not supported. A limit at ±10 bps balances fill probability against slippage in normal market conditions.

## Sizing

Position size = `target_weight × portfolio_value`. The portfolio value comes from Alpaca's account endpoint at execution time (the `--execute` path), which means trades are sized against the *broker's* known equity, not a stale local value.

Default `portfolio.max_position_pct` is 5%, so the maximum single trade is 5% × portfolio value at the time of execution.

## Slippage tracking

After Alpaca reports a fill, `execution/slippage.py: record_fill(order_id, fill_price, ...)` writes a row to the `fills` table with:

```python
slippage_bps = (fill_price - limit_price) / limit_price * 10_000
              * (1 if side in (BUY, BUY_TO_COVER) else -1)
```

A positive number = paid more than expected (worse fill). Negative = paid less (better fill). The 30-day rollup is in `get_slippage_stats()`:

| Metric | Description |
|--------|-------------|
| `avg_bps` | Mean slippage |
| `median_bps` | Median |
| `p95_bps` | 95th percentile |
| `total_cost_usd` | Sum of `slippage_bps * trade_usd / 10000` |
| `worst_5_fills` | Top 5 worst by `slippage_bps` |

These appear in `run_execution.py --status` and the dashboard's Execution tab.

## SIGINT handling

`run_execution.py --execute` installs a SIGINT handler before placing any orders. If you Ctrl-C mid-execution, the handler calls `cancel_all_pending` first, then exits. This prevents leaving stale orders on Alpaca if you bail out.

## What happens without Alpaca keys

| Mode | `broker.client` | Behavior |
|------|-----------------|----------|
| Keys set, paper | `TradingClient(paper=True)` | Real paper-trading orders |
| Keys set, live + confirmed | `TradingClient(paper=False)` | Real live orders (use with extreme care) |
| Keys missing | `None` | `SIMULATED` mode — orders are logged with synthetic fills, no Alpaca calls |

In `SIMULATED` mode, fills are generated as `limit_price * (1 ± random(0, 5bps))`. The fills feed `portfolio_positions` and the slippage table just like real fills, so all downstream layers behave identically.

## Short borrow check

Before submitting a `SELL_SHORT`, `execution/short_check.py: is_borrowable(ticker)` calls Alpaca's `get_asset(ticker)` and checks the `easy_to_borrow` flag. If false, JARVIS skips the trade and logs it to `veto_log` with reason "ETB unavailable."

In `SIMULATED` mode, all tickers are treated as borrowable.

## Order status sync

`execution/order_manager.py: sync_order_status()` polls Alpaca for the status of every order in the `orders` table that isn't terminal (`FILLED`, `CANCELED`, `EXPIRED`). It updates the local row and, on transition to `FILLED`, calls `record_fill` and `_apply_to_positions`.

Run `python run_execution.py --status` to trigger a sync and print the results.

## Cancel-all

`python run_execution.py --cancel-all` cancels every open order. Use it before running a fresh `--execute` cycle if a previous run left stale orders. Also called on SIGINT mid-execute.

## Common gotchas

**Orders show `accepted` but never `filled`.** Limit price was off — market moved beyond the limit before the order could fill. Either tighten the limit offset (edit `_run_execute` in `run_execution.py`) or wait for the order to expire (with `gtc`, this is end of day).

**`broker.client = None` even with keys set.** Either `alpaca-py` is not installed (`pip install alpaca-py`), or the keys in `.env` are blank/typo. Check `python -c "import alpaca; print(alpaca.__version__)"`.

**Slippage is consistently positive.** The ±10 bps limit offset is too tight for the assets you're trading. Widen the offset in `_run_execute` and `order_manager.py` or accept the cost.

**Live mode confirmed but still paper.** `ALPACA_LIVE_CONFIRMED` is checked against the exact string `"YES_I_UNDERSTAND_THE_RISKS"`. Anything else (including `"yes"`, `"1"`, `"true"`) leaves the broker in paper mode.

**`portfolio_value` reads as $100,000 in dry-run.** Default. Without an Alpaca account, the script can't know your actual equity, so it falls back to a $100k notional. Adjust at the top of `run_execution.py` if needed.

## See also

- [Reporting and dashboard](reporting-and-dashboard.md) — what visualizes the fills next
- [ADR-003](../architecture/adr/003-paper-trading-only.md) — why live mode is intentionally hard to enable
- [Database schema](../reference/database-schema.md) — `orders`, `fills`
