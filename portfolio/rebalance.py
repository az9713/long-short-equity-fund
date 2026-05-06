import numpy as np
import pandas as pd
from utils import get_logger, get_config
from data.market_data import get_prices
from portfolio.state import get_positions, log_trade
from portfolio.transaction_cost import estimate_cost_bps

log = get_logger(__name__)


def generate_rebalance(
    target_weights: dict[str, float],
    portfolio_value: float,
    whatif: bool = False,
) -> pd.DataFrame:
    if not target_weights:
        log.warning("Empty target weights — nothing to rebalance")
        return pd.DataFrame()

    config = get_config().get("portfolio", {})
    turnover_budget = config.get("turnover_budget", 0.30)

    current_positions = get_positions()

    # Build current weight map: positive = long, negative = short
    current_weights: dict[str, float] = {}
    if not current_positions.empty:
        for _, row in current_positions.iterrows():
            ticker = row["ticker"]
            shares = row.get("shares", 0.0) or 0.0
            price = row.get("current_price") or row.get("entry_price", 0.0) or 0.0
            mv = shares * price / portfolio_value
            if row.get("side") == "SHORT":
                mv = -mv
            current_weights[ticker] = mv

    # All tickers involved (union of current + target)
    all_tickers = set(current_weights.keys()) | set(target_weights.keys())

    # Compute raw trades: target - current
    raw_trades = []
    for ticker in all_tickers:
        current_w = current_weights.get(ticker, 0.0)
        target_w = target_weights.get(ticker, 0.0)
        delta_w = target_w - current_w

        if abs(delta_w) < 1e-4:
            continue

        # Fetch current price
        px_df = get_prices(ticker, days=5)
        if px_df.empty:
            log.warning(f"No price data for {ticker}, skipping trade")
            continue
        price = float(px_df["close"].iloc[-1])

        delta_usd = delta_w * portfolio_value
        shares = delta_usd / price if price > 0 else 0.0

        # Determine action
        if delta_w > 0:
            action = "BUY" if target_w > 0 else "COVER"
        else:
            action = "SHORT" if target_w < 0 else "SELL"

        side = "LONG" if target_w >= 0 else "SHORT"

        cost_bps = estimate_cost_bps(ticker, abs(delta_usd))

        raw_trades.append({
            "ticker": ticker,
            "action": action,
            "side": side,
            "delta_weight": delta_w,
            "target_weight": target_w,
            "current_weight": current_w,
            "shares": shares,
            "price": price,
            "trade_usd": abs(delta_usd),
            "estimated_cost_bps": cost_bps,
        })

    if not raw_trades:
        log.info("No trades required")
        return pd.DataFrame()

    trades_df = pd.DataFrame(raw_trades)

    # Apply turnover budget: sort by abs(delta_weight) descending, trim from smallest up.
    # Skip on the initial build from an empty book — turnover budgeting is meant to
    # limit churn between existing books, not to throttle first-time portfolio construction.
    trades_df = trades_df.sort_values("delta_weight", key=abs, ascending=False)
    is_initial_build = not current_weights

    total_turnover = trades_df["trade_usd"].sum() / portfolio_value
    if not is_initial_build and total_turnover > turnover_budget:
        # Keep the largest trades up to the budget
        trades_df["cum_turnover"] = trades_df["trade_usd"].cumsum() / portfolio_value
        trades_df = trades_df[trades_df["cum_turnover"] <= turnover_budget].copy()
        trades_df = trades_df.drop(columns=["cum_turnover"])
        log.info(
            f"Turnover budget ({turnover_budget:.0%}) applied: "
            f"trimmed to {len(trades_df)} trades"
        )
    elif is_initial_build:
        log.info(f"Initial build from empty book ({total_turnover:.1%}) — turnover budget not applied")

    if whatif:
        print(f"\n{'='*65}")
        print("  JARVIS — What-If Rebalance (not committed)")
        print(f"{'='*65}")
        print(f"  {'Ticker':<8} {'Action':<7} {'Shares':>9}  {'Price':>8}  {'Cost(bps)':>10}")
        print(f"  {'-'*60}")
        for _, row in trades_df.iterrows():
            print(
                f"  {row['ticker']:<8} {row['action']:<7} {row['shares']:>9.1f}  "
                f"{row['price']:>8.2f}  {row['estimated_cost_bps']:>10.1f}"
            )
        total_cost = (trades_df["estimated_cost_bps"] * trades_df["trade_usd"]).sum() / portfolio_value / 100
        print(f"\n  Total estimated cost: {total_cost*100:.2f} bps of portfolio")
        print(f"  Trades: {len(trades_df)}")
        print()
        return trades_df

    # Write PENDING approvals to state
    from portfolio.state import queue_approval
    try:
        for _, row in trades_df.iterrows():
            queue_approval(row["ticker"], row["side"])
        log.info(f"Queued {len(trades_df)} trades for PM approval")
    except Exception as e:
        log.error(f"Failed to write approval records: {e}")

    return trades_df
