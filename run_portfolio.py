"""
run_portfolio.py — Meridian Capital Partners / JARVIS
Layer 4 Portfolio Construction entry point.

Usage:
  python run_portfolio.py --current                         # show current positions + P&L
  python run_portfolio.py --whatif                          # show proposed rebalance without committing
  python run_portfolio.py --rebalance                       # generate and queue trades for approval
  python run_portfolio.py --optimize-method mvo|conviction  # override config optimizer
"""

import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Meridian Capital Partners — JARVIS Portfolio Construction"
    )
    parser.add_argument("--current", action="store_true", help="Show current positions and P&L")
    parser.add_argument("--whatif", action="store_true", help="Show proposed rebalance without committing")
    parser.add_argument("--rebalance", action="store_true", help="Generate and queue trades for approval")
    parser.add_argument(
        "--optimize-method",
        type=str,
        choices=["mvo", "conviction"],
        default=None,
        help="Override config optimizer method",
    )
    return parser.parse_args()


def _load_scored_universe():
    import pandas as pd
    path = ROOT / "output" / "scored_universe_latest.csv"
    if not path.exists():
        print(f"  ERROR: {path} not found.")
        print("  Run 'python run_scoring.py' first to generate the scored universe.")
        sys.exit(1)
    df = pd.read_csv(path)
    print(f"  Loaded scored universe: {len(df)} tickers")
    return df


def _print_current(portfolio_value: float):
    from portfolio.state import get_positions, get_pending_approvals, update_current_prices
    from portfolio.beta import get_portfolio_beta
    from portfolio.factor_exposure import get_factor_exposures

    print("  Refreshing current prices...")
    update_current_prices()

    positions = get_positions()
    if positions.empty:
        print("  No open positions.")
        return

    longs = positions[positions["side"] == "LONG"]
    shorts = positions[positions["side"] == "SHORT"]

    print(f"\n  {'Ticker':<8} {'Side':<6} {'Shares':>8}  {'Entry':>8}  {'Current':>8}  {'P&L':>10}  {'Sector'}")
    print(f"  {'-'*72}")
    for _, row in positions.sort_values("side").iterrows():
        pnl = row.get("unrealized_pnl") or 0.0
        print(
            f"  {row['ticker']:<8} {row['side']:<6} {row['shares']:>8.1f}  "
            f"{row.get('entry_price', 0):>8.2f}  {row.get('current_price', 0):>8.2f}  "
            f"{pnl:>+10.2f}  {row.get('sector', '')}"
        )

    total_pnl = positions.get("unrealized_pnl", 0).sum() if "unrealized_pnl" in positions.columns else 0.0
    print(f"\n  Open positions:  {len(positions)} ({len(longs)} long, {len(shorts)} short)")
    print(f"  Total unrealized P&L: ${total_pnl:+,.2f}")

    # Portfolio beta
    long_tickers = longs["ticker"].tolist() if not longs.empty else []
    short_tickers = shorts["ticker"].tolist() if not shorts.empty else []
    weights = {}
    for _, row in positions.iterrows():
        t = row["ticker"]
        shares = row.get("shares", 0.0) or 0.0
        price = row.get("current_price") or row.get("entry_price", 0.0) or 0.0
        weights[t] = shares * price / portfolio_value

    beta_result = get_portfolio_beta(long_tickers, short_tickers, weights)
    print(f"\n  Portfolio Beta:  net={beta_result['net_beta']:+.3f}  "
          f"long={beta_result['long_beta']:.3f}  short={beta_result['short_beta']:.3f}")

    pending = get_pending_approvals()
    if not pending.empty:
        print(f"\n  Pending approvals: {len(pending)}")
        for _, row in pending.iterrows():
            print(f"    {row['ticker']} {row['side']}")


def _run_optimizer(candidates_df, portfolio_value: float, method: str) -> dict:
    if method == "conviction":
        from portfolio.optimizer import optimize_conviction
        return optimize_conviction(candidates_df, portfolio_value)
    else:
        from portfolio.mvo_optimizer import optimize_mvo
        return optimize_mvo(candidates_df, portfolio_value)


def _print_rebalance_summary(trades_df, warnings: list, portfolio_value: float):
    import pandas as pd

    if trades_df.empty:
        print("  No trades required.")
        return

    longs = trades_df[trades_df["side"] == "LONG"]
    shorts = trades_df[trades_df["side"] == "SHORT"]

    total_trade_usd = trades_df["trade_usd"].sum()
    turnover_pct = total_trade_usd / portfolio_value * 100

    print(f"\n  Proposed Trades: {len(trades_df)} ({len(longs)} long, {len(shorts)} short)")
    print(f"  Estimated Turnover: {turnover_pct:.1f}% of portfolio")

    total_cost_bps = (trades_df["estimated_cost_bps"] * trades_df["trade_usd"]).sum() / portfolio_value / 100
    print(f"  Total Cost Estimate: {total_cost_bps:.1f} bps")

    if warnings:
        print(f"\n  Rebalance Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    {w}")
    else:
        print("\n  No rebalance warnings.")


def main():
    args = parse_args()

    from utils import get_logger, get_config
    log = get_logger("run_portfolio")

    print("=" * 60)
    print("  Meridian Capital Partners — JARVIS Portfolio Construction")
    print("=" * 60)

    cfg = get_config()
    portfolio_cfg = cfg.get("portfolio", {})

    # Portfolio value: default 100k paper account
    portfolio_value = 100_000.0

    # --current: show open positions
    if args.current:
        print("\n  Mode: current positions\n")
        _print_current(portfolio_value)
        return

    # For --whatif and --rebalance we need a scored universe
    if args.whatif or args.rebalance:
        print(f"\n  Mode: {'what-if rebalance' if args.whatif else 'rebalance + queue for approval'}\n")

        scored_df = _load_scored_universe()

        # Run combined scoring if available
        try:
            from analysis.combined_score import run_combined_scoring
            scored_df = run_combined_scoring(scored_df)
        except Exception as e:
            log.warning(f"Could not run combined scoring: {e} — using quant scores only")

        # Filter to LONG + SHORT candidates
        candidates = scored_df[scored_df["signal"].isin(["LONG", "SHORT"])].copy()
        if candidates.empty:
            print("  No LONG or SHORT candidates in scored universe.")
            print("  Adjust scoring thresholds or expand the universe.")
            sys.exit(1)

        longs = candidates[candidates["signal"] == "LONG"]
        shorts = candidates[candidates["signal"] == "SHORT"]
        print(f"  Candidates: {len(longs)} LONG, {len(shorts)} SHORT")

        # Determine optimizer
        method = args.optimize_method or portfolio_cfg.get("optimize_method", "mvo")
        print(f"  Optimizer: {method.upper()}")

        # Run optimizer
        target_weights = _run_optimizer(candidates, portfolio_value, method)

        if not target_weights:
            print("  Optimizer returned no weights — nothing to do.")
            sys.exit(1)

        n_long_w = sum(1 for w in target_weights.values() if w > 0)
        n_short_w = sum(1 for w in target_weights.values() if w < 0)
        gross = sum(abs(w) for w in target_weights.values())
        net = sum(target_weights.values())
        print(f"  Target portfolio: {n_long_w} longs, {n_short_w} shorts  "
              f"gross={gross:.2f}  net={net:+.3f}")

        # Get rebalance warnings
        from portfolio.rebalance_schedule import get_rebalance_warnings
        warnings = get_rebalance_warnings(scored_df)

        # Generate rebalance trade list
        from portfolio.rebalance import generate_rebalance
        trades_df = generate_rebalance(
            target_weights,
            portfolio_value,
            whatif=args.whatif,
        )

        if not args.whatif:
            _print_rebalance_summary(trades_df, warnings, portfolio_value)
            pending_count = len(trades_df) if not trades_df.empty else 0
            print(f"\n  {pending_count} trade(s) queued as PENDING in position_approvals table.")
            print("  Use --current to review open positions.")

        return

    # Default: print help
    print("\n  No action specified. Use --current, --whatif, or --rebalance.")
    print("  Run with --help for usage.")


if __name__ == "__main__":
    main()
