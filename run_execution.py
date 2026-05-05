"""
run_execution.py — Meridian Capital Partners / JARVIS
Layer 6 Execution entry point.

Usage:
  python run_execution.py --dry-run     # log what would happen, no real orders
  python run_execution.py --execute     # place orders for all APPROVED positions
  python run_execution.py --status      # show order book and recent fills
  python run_execution.py --cancel-all  # cancel all pending orders
"""

import sys
import signal
import argparse
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Meridian Capital Partners — JARVIS Execution Layer"
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without placing orders")
    parser.add_argument("--execute", action="store_true", help="Execute all APPROVED positions")
    parser.add_argument("--status", action="store_true", help="Show order book and recent fills")
    parser.add_argument("--cancel-all", action="store_true", help="Cancel all pending orders")
    return parser.parse_args()


def _install_sigint_handler():
    from execution.order_manager import cancel_all_pending
    from utils import get_logger
    log = get_logger("run_execution")

    def _handle_sigint(sig, frame):
        log.warning("SIGINT received — cancelling all pending orders before exit")
        try:
            cancel_all_pending()
        except Exception as e:
            log.error(f"cancel_all_pending on SIGINT failed: {e}")
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_sigint)


def _check_circuit_breakers(portfolio_value: float) -> bool:
    from risk.circuit_breakers import check_circuit_breakers
    breakers = check_circuit_breakers(portfolio_value)
    kill = any(b.get("action") == "KILL_SWITCH" for b in breakers)
    if kill:
        print("\n  KILL_SWITCH ACTIVE — execution aborted")
        for b in breakers:
            print(f"    [{b['level']}] {b['action']}: {b['reason']}")
        return False
    if breakers:
        print(f"\n  Circuit breakers triggered ({len(breakers)}):")
        for b in breakers:
            print(f"    [{b['level']}] {b['action']}: {b['reason']}")
    return True


def _print_status():
    from execution.order_manager import get_open_orders, sync_order_status
    from execution.slippage import get_slippage_stats, get_slippage_dashboard

    print("\nSyncing order status with Alpaca...")
    try:
        sync_order_status()
    except Exception as e:
        print(f"  WARNING: sync failed: {e}")

    print("\nOpen Orders:")
    print("-" * 72)
    orders = get_open_orders()
    if orders.empty:
        print("  No pending orders")
    else:
        print(f"  {'ID':<6} {'Ticker':<8} {'Side':<7} {'Shares':>9}  {'Limit':>8}  {'Status':<12}  Created")
        print(f"  {'-'*70}")
        for _, row in orders.iterrows():
            created = row["created_at"].strftime("%Y-%m-%d %H:%M") if hasattr(row["created_at"], "strftime") else str(row["created_at"])
            print(
                f"  {row.get('alpaca_order_id', '')[:6]:<6} {row['ticker']:<8} {row['side']:<7} "
                f"{row['shares']:>9.2f}  {row['limit_price']:>8.2f}  {row['status']:<12}  {created}"
            )

    print("\nSlippage Stats (30-day):")
    print("-" * 44)
    try:
        stats = get_slippage_stats()
        print(f"  Avg:          {stats['avg_bps']:+.1f} bps")
        print(f"  Median:       {stats['median_bps']:+.1f} bps")
        print(f"  P95:          {stats['p95_bps']:+.1f} bps")
        print(f"  Total cost:   ${stats['total_cost_usd']:,.2f}")
        worst = stats.get("worst_5_fills", [])
        if worst:
            print(f"\n  Worst 5 fills:")
            for w in worst:
                print(f"    Order {w['id']} {w['ticker']} {w['side']}: {w['slippage_bps']:.1f} bps (cost ${w['cost_usd']:.2f})")
    except Exception as e:
        print(f"  ERROR: {e}")

    print("\nRecent Fills (last 30 days):")
    print("-" * 72)
    try:
        df = get_slippage_dashboard()
        if df.empty:
            print("  No filled orders in last 30 days")
        else:
            print(f"  {'Date':<12} {'Ticker':<8} {'Side':<7} {'Shares':>9}  {'Fill':>8}  {'Slippage':>10}")
            print(f"  {'-'*70}")
            for _, row in df.head(20).iterrows():
                dt = row["timestamp"].strftime("%Y-%m-%d") if hasattr(row["timestamp"], "strftime") else str(row["timestamp"])[:10]
                slip = row.get("slippage_bps")
                slip_str = f"{slip:+.1f} bps" if slip is not None else "N/A"
                print(
                    f"  {dt:<12} {row['ticker']:<8} {row['side']:<7} "
                    f"{row['shares']:>9.2f}  {row.get('fill_price', 0):>8.2f}  {slip_str:>10}"
                )
    except Exception as e:
        print(f"  ERROR: {e}")


def _run_dry_run(portfolio_value: float):
    from data.market_data import get_prices, get_adv
    from utils import get_config, get_db
    from portfolio.state import _init_tables

    cfg = get_config().get("execution", {})
    max_adv = cfg.get("max_order_pct_adv", 0.02)
    spread_bps = cfg.get("slippage_spread_bps", 5)

    conn = get_db()
    try:
        _init_tables(conn)
        approved = conn.execute(
            "SELECT ticker, side FROM position_approvals WHERE status='APPROVED'"
        ).fetchall()
    except Exception as e:
        print(f"  ERROR reading approvals: {e}")
        return
    finally:
        conn.close()

    if not approved:
        print("\n  No APPROVED positions to dry-run")
        return

    port_cfg = get_config().get("portfolio", {})
    target_pct = port_cfg.get("max_position_pct", 0.05)

    print(f"\n  {'Ticker':<8} {'Side':<7} {'Shares':>9}  {'Price':>8}  {'Limit':>8}  {'Est.Slip':>10}  {'ADV Pct':>8}")
    print(f"  {'-'*75}")

    for row in approved:
        ticker = row["ticker"]
        side = row["side"]
        try:
            px = get_prices(ticker, 2)
            if px.empty:
                print(f"  {ticker:<8} {side:<7} {'NO PRICE':>9}")
                continue
            close = float(px.iloc[-1]["close"])
            target_usd = portfolio_value * target_pct
            shares = round(target_usd / close, 4) if close > 0 else 0.0

            exec_side = "BUY" if side == "LONG" else "SHORT"
            if exec_side in ("BUY", "COVER"):
                limit_price = round(close * 1.001, 2)
            else:
                limit_price = round(close * 0.999, 2)

            adv_usd = get_adv(ticker)
            trade_usd = shares * close
            adv_pct = trade_usd / adv_usd * 100 if adv_usd > 0 else 0.0
            est_slip = spread_bps

            print(
                f"  {ticker:<8} {exec_side:<7} {shares:>9.2f}  {close:>8.2f}  "
                f"{limit_price:>8.2f}  {est_slip:>9.0f}bps  {adv_pct:>7.2f}%"
            )
        except Exception as e:
            print(f"  {ticker:<8} ERROR: {e}")

    print(f"\n  NOTE: Dry-run only — no orders placed (broker.client=None)")


def _run_execute(portfolio_value: float):
    from execution.order_manager import execute_approved_trades

    print(f"\n  Executing approved trades for portfolio value ${portfolio_value:,.0f}...")
    results = execute_approved_trades(portfolio_value)

    if not results:
        print("  No trades executed")
        return

    print(f"\n  {'Ticker':<8} {'Side':<7} {'Shares':>9}  {'Limit':>8}  {'Fill':>8}  {'Slip':>8}  Status")
    print(f"  {'-'*72}")
    for r in results:
        slip = r.get("slippage_bps")
        slip_str = f"{slip:+.1f}" if slip is not None else "N/A"
        print(
            f"  {r['ticker']:<8} {r['side']:<7} {r['shares']:>9.2f}  "
            f"{r.get('limit_price', 0):>8.2f}  {r.get('fill_price', 0):>8.2f}  "
            f"{slip_str:>7}bps  {r['status']}"
        )

    filled = [r for r in results if r["status"] in ("FILLED", "SIMULATED")]
    print(f"\n  Executed: {len(filled)}/{len(results)} orders filled/simulated")


def main():
    args = parse_args()

    from utils import get_logger
    log = get_logger("run_execution")

    print("=" * 60)
    print("  Meridian Capital Partners — JARVIS Execution Layer")
    print("=" * 60)
    print(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    # Broker startup — sync Alpaca positions on execute/dry-run
    from execution.broker import broker
    portfolio_value = 100_000.0

    if args.cancel_all:
        print("\n  Mode: cancel all pending orders\n")
        from execution.order_manager import cancel_all_pending
        cancel_all_pending()
        print("  Done.")
        return

    if args.status:
        print("\n  Mode: order status\n")
        _print_status()
        return

    if args.dry_run:
        print("\n  Mode: dry-run (no orders placed)\n")
        # Override broker client to None so nothing touches Alpaca
        broker.client = None

        # Still get portfolio value from account if available
        try:
            acct = broker.get_account()
            if acct.get("portfolio_value", 0) > 0:
                portfolio_value = acct["portfolio_value"]
        except Exception:
            pass

        _run_dry_run(portfolio_value)
        return

    if args.execute:
        print("\n  Mode: execute APPROVED positions\n")

        # Install SIGINT handler before placing any orders
        _install_sigint_handler()

        # Sync broker state
        try:
            acct = broker.get_account()
            if acct.get("portfolio_value", 0) > 0:
                portfolio_value = acct["portfolio_value"]
            log.info(
                f"Account: cash=${acct['cash']:,.0f} "
                f"portfolio=${acct['portfolio_value']:,.0f} "
                f"buying_power=${acct['buying_power']:,.0f}"
            )
        except Exception as e:
            log.warning(f"Could not fetch account: {e}")

        broker.sync_with_alpaca()

        # Circuit breaker check before any execution
        if not _check_circuit_breakers(portfolio_value):
            return

        _run_execute(portfolio_value)
        return

    print("\n  No action specified. Use --dry-run, --execute, --status, or --cancel-all.")
    print("  Run with --help for usage.")


if __name__ == "__main__":
    main()
