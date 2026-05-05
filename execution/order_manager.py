import pandas as pd
from datetime import datetime
from utils import get_db, get_logger
from execution.broker import broker

log = get_logger(__name__)


def _init_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS open_orders (
            alpaca_order_id TEXT PRIMARY KEY,
            ticker TEXT,
            side TEXT,
            shares REAL,
            limit_price REAL,
            status TEXT,
            created_at TEXT
        )
    """)
    conn.commit()


def _register_open_order(
    alpaca_order_id: str,
    ticker: str,
    side: str,
    shares: float,
    limit_price: float,
):
    conn = get_db()
    try:
        _init_tables(conn)
        now = datetime.utcnow().isoformat()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO open_orders
                (alpaca_order_id, ticker, side, shares, limit_price, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
                """,
                (alpaca_order_id, ticker, side, shares, limit_price, now),
            )
    except Exception as e:
        log.error(f"_register_open_order failed: {e}")
    finally:
        conn.close()


def get_open_orders() -> pd.DataFrame:
    conn = get_db()
    try:
        _init_tables(conn)
        rows = conn.execute(
            "SELECT * FROM open_orders WHERE status NOT IN ('FILLED', 'CANCELLED', 'SIMULATED')"
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["created_at"] = pd.to_datetime(df["created_at"])
        return df
    except Exception as e:
        log.error(f"get_open_orders failed: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def cancel_all_pending():
    client = broker.get_client()
    pending = get_open_orders()

    if pending.empty:
        log.info("cancel_all_pending: no pending orders")
        return

    conn = get_db()
    try:
        _init_tables(conn)
        for _, row in pending.iterrows():
            oid = row.get("alpaca_order_id", "")
            ticker = row.get("ticker", "")
            if client is not None and oid:
                try:
                    client.cancel_order_by_id(oid)
                    log.info(f"Cancelled order {oid} ({ticker})")
                except Exception as e:
                    log.warning(f"Could not cancel {oid}: {e}")

            with conn:
                conn.execute(
                    "UPDATE open_orders SET status='CANCELLED' WHERE alpaca_order_id=?",
                    (oid,),
                )
    except Exception as e:
        log.error(f"cancel_all_pending failed: {e}")
    finally:
        conn.close()

    log.info(f"cancel_all_pending complete: {len(pending)} order(s) processed")


def sync_order_status():
    client = broker.get_client()
    pending = get_open_orders()

    if pending.empty:
        log.info("sync_order_status: no open orders to sync")
        return

    conn = get_db()
    try:
        _init_tables(conn)
        for _, row in pending.iterrows():
            oid = row.get("alpaca_order_id", "")
            if not oid or client is None:
                continue
            try:
                order = client.get_order_by_id(oid)
                status = str(order.status).lower()
                normalized = "FILLED" if status == "filled" else (
                    "CANCELLED" if status in ("cancelled", "canceled", "expired") else "PENDING"
                )
                with conn:
                    conn.execute(
                        "UPDATE open_orders SET status=? WHERE alpaca_order_id=?",
                        (normalized, oid),
                    )
                log.info(f"Order {oid} ({row['ticker']}) status: {normalized}")
            except Exception as e:
                log.warning(f"sync_order_status failed for {oid}: {e}")
    except Exception as e:
        log.error(f"sync_order_status failed: {e}")
    finally:
        conn.close()


def execute_approved_trades(portfolio_value: float = 100_000.0) -> list[dict]:
    # Fetch APPROVED entries from position_approvals, then derive trade size
    # from the latest rebalance output and execute each via execute_trade.
    from portfolio.state import get_positions
    from data.market_data import get_prices
    from execution.executor import execute_trade

    conn = get_db()
    results = []

    try:
        # Ensure tables exist
        from portfolio.state import _init_tables as _init_state
        _init_state(conn)

        approved = conn.execute(
            "SELECT ticker, side FROM position_approvals WHERE status='APPROVED'"
        ).fetchall()

        if not approved:
            log.info("No APPROVED positions to execute")
            return results

        current_positions = get_positions()

        for row in approved:
            ticker = row["ticker"]
            side = row["side"]  # 'LONG' or 'SHORT'

            try:
                px = get_prices(ticker, 2)
                if px.empty:
                    log.warning(f"No price data for {ticker} — skipping")
                    continue
                price = float(px.iloc[-1]["close"])

                # Derive shares from target weight in config (5% max position)
                from utils import get_config
                port_cfg = get_config().get("portfolio", {})
                target_pct = port_cfg.get("max_position_pct", 0.05)
                target_usd = portfolio_value * target_pct
                shares = round(target_usd / price, 4) if price > 0 else 0.0

                if shares <= 0:
                    log.warning(f"Calculated 0 shares for {ticker} — skipping")
                    continue

                # Map portfolio side to execution side
                # Check if we're opening or closing
                exec_side = "BUY" if side == "LONG" else "SHORT"
                if not current_positions.empty and ticker in current_positions["ticker"].values:
                    existing = current_positions[current_positions["ticker"] == ticker].iloc[0]
                    existing_side = existing.get("side", "")
                    if existing_side == "LONG" and side == "SHORT":
                        exec_side = "SELL"  # close the long first
                    elif existing_side == "SHORT" and side == "LONG":
                        exec_side = "COVER"

                log.info(f"Executing approved trade: {exec_side} {shares:.2f} {ticker}")
                result = execute_trade(
                    ticker=ticker,
                    side=exec_side,
                    shares=shares,
                    portfolio_value=portfolio_value,
                )

                if result:
                    results.append(result)
                    # Mark approval as consumed by resetting to avoid re-execution
                    with conn:
                        conn.execute(
                            "UPDATE position_approvals SET status='EXECUTED' WHERE ticker=?",
                            (ticker,),
                        )
            except Exception as e:
                log.error(f"execute_approved_trades failed for {ticker}: {e}")

    except Exception as e:
        log.error(f"execute_approved_trades outer error: {e}")
    finally:
        conn.close()

    log.info(f"execute_approved_trades: {len(results)} trades executed")
    return results
