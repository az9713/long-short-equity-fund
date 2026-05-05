import json
import pandas as pd
from datetime import datetime
from utils import get_db, get_logger
from data.market_data import get_prices

log = get_logger(__name__)


def init_tables(conn):
    _init_tables(conn)


def _init_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            ticker TEXT PRIMARY KEY,
            side TEXT,
            shares REAL,
            entry_price REAL,
            entry_date TEXT,
            current_price REAL,
            unrealized_pnl REAL,
            sector TEXT,
            factor_scores TEXT
        );

        CREATE TABLE IF NOT EXISTS portfolio_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            ticker TEXT,
            action TEXT,
            shares REAL,
            price REAL,
            reason TEXT
        );

        CREATE TABLE IF NOT EXISTS position_approvals (
            ticker TEXT PRIMARY KEY,
            side TEXT,
            status TEXT,
            approved_at TEXT,
            rejected_reason TEXT
        );
    """)
    conn.commit()


def get_positions() -> pd.DataFrame:
    conn = get_db()
    try:
        _init_tables(conn)
        rows = conn.execute("SELECT * FROM portfolio_positions").fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception as e:
        log.error(f"get_positions failed: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def get_pending_approvals() -> pd.DataFrame:
    conn = get_db()
    try:
        _init_tables(conn)
        rows = conn.execute(
            "SELECT * FROM position_approvals WHERE status='PENDING'"
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception as e:
        log.error(f"get_pending_approvals failed: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def approve_position(ticker: str, side: str):
    conn = get_db()
    try:
        _init_tables(conn)
        now = datetime.utcnow().isoformat()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO position_approvals (ticker, side, status, approved_at, rejected_reason)
                VALUES (?, ?, 'APPROVED', ?, NULL)
                """,
                (ticker, side, now),
            )
        log.info(f"Approved: {ticker} {side}")
    except Exception as e:
        log.error(f"approve_position failed for {ticker}: {e}")
    finally:
        conn.close()


def reject_position(ticker: str, reason: str):
    conn = get_db()
    try:
        _init_tables(conn)
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO position_approvals (ticker, side, status, approved_at, rejected_reason)
                VALUES (?, (SELECT side FROM position_approvals WHERE ticker=?), 'REJECTED', NULL, ?)
                """,
                (ticker, ticker, reason),
            )
        log.info(f"Rejected: {ticker} — {reason}")
    except Exception as e:
        log.error(f"reject_position failed for {ticker}: {e}")
    finally:
        conn.close()


def reset_position(ticker: str):
    conn = get_db()
    try:
        _init_tables(conn)
        with conn:
            conn.execute(
                "UPDATE position_approvals SET status='PENDING', approved_at=NULL, rejected_reason=NULL WHERE ticker=?",
                (ticker,),
            )
        log.info(f"Reset to PENDING: {ticker}")
    except Exception as e:
        log.error(f"reset_position failed for {ticker}: {e}")
    finally:
        conn.close()


def update_current_prices():
    conn = get_db()
    try:
        _init_tables(conn)
        rows = conn.execute("SELECT ticker, side, shares, entry_price FROM portfolio_positions").fetchall()
        if not rows:
            return

        for row in rows:
            ticker = row["ticker"]
            side = row["side"]
            shares = row["shares"]
            entry_price = row["entry_price"]

            try:
                px_df = get_prices(ticker, days=5)
                if px_df.empty:
                    continue
                current_price = float(px_df["close"].iloc[-1])

                if side == "LONG":
                    unrealized_pnl = (current_price - entry_price) * shares
                else:
                    # SHORT: profit when price falls
                    unrealized_pnl = (entry_price - current_price) * shares

                with conn:
                    conn.execute(
                        "UPDATE portfolio_positions SET current_price=?, unrealized_pnl=? WHERE ticker=?",
                        (current_price, unrealized_pnl, ticker),
                    )
            except Exception as e:
                log.warning(f"Could not update price for {ticker}: {e}")

        log.info("Current prices updated")
    except Exception as e:
        log.error(f"update_current_prices failed: {e}")
    finally:
        conn.close()


def log_trade(
    ticker: str,
    side: str,
    shares: float,
    price: float,
    action: str,
    reason: str = "",
):
    conn = get_db()
    try:
        _init_tables(conn)
        now = datetime.utcnow().isoformat()

        with conn:
            conn.execute(
                """
                INSERT INTO portfolio_history (date, ticker, action, shares, price, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (now, ticker, action, shares, price, reason),
            )

        # Update portfolio_positions for open/close actions
        if action in ("BUY", "SHORT"):
            # If side flip (LONG->SHORT or SHORT->LONG), log a close entry first
            existing = conn.execute(
                "SELECT side, shares FROM portfolio_positions WHERE ticker=?", (ticker,)
            ).fetchone()
            if existing and existing["side"] != side:
                close_action = "SELL" if existing["side"] == "LONG" else "COVER"
                with conn:
                    conn.execute(
                        "INSERT INTO portfolio_history (date, ticker, action, shares, price, reason) VALUES (?, ?, ?, ?, ?, ?)",
                        (now, ticker, close_action, existing["shares"], price, "side flip"),
                    )
                    conn.execute("DELETE FROM portfolio_positions WHERE ticker=?", (ticker,))
                log.info(f"Closed existing {existing['side']} position in {ticker} before opening {side}")

            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO portfolio_positions
                    (ticker, side, shares, entry_price, entry_date, current_price, unrealized_pnl, sector, factor_scores)
                    VALUES (?, ?, ?, ?, ?, ?, 0.0, '', '{}')
                    """,
                    (ticker, side, shares, price, now, price),
                )

        elif action in ("SELL", "COVER"):
            with conn:
                conn.execute("DELETE FROM portfolio_positions WHERE ticker=?", (ticker,))

        log.info(f"Trade logged: {action} {shares} {ticker} @ {price:.2f}")
    except Exception as e:
        log.error(f"log_trade failed for {ticker}: {e}")
    finally:
        conn.close()


def queue_approval(ticker: str, side: str):
    conn = get_db()
    try:
        _init_tables(conn)
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO position_approvals (ticker, side, status, approved_at, rejected_reason)
                VALUES (?, ?, 'PENDING', NULL, NULL)
                """,
                (ticker, side),
            )
    except Exception as e:
        log.error(f"queue_approval failed for {ticker}: {e}")
    finally:
        conn.close()


def get_portfolio_value(cash: float = 100_000) -> float:
    positions = get_positions()
    if positions.empty:
        return cash

    try:
        # Long market value adds to cash; short market value subtracts
        long_mv = 0.0
        short_mv = 0.0
        for _, row in positions.iterrows():
            shares = row.get("shares", 0.0) or 0.0
            price = row.get("current_price") or row.get("entry_price", 0.0) or 0.0
            mv = shares * price
            if row.get("side") == "LONG":
                long_mv += mv
            elif row.get("side") == "SHORT":
                short_mv += mv

        # Standard L/S: cash + long_mv - short_mv (short proceeds already in cash)
        return cash + long_mv - short_mv
    except Exception as e:
        log.error(f"get_portfolio_value failed: {e}")
        return cash
