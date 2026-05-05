import pandas as pd
from datetime import datetime, timedelta
from utils import get_db, get_logger

log = get_logger(__name__)


def _init_tables(conn):
    # order_log is also created by executor.py — CREATE IF NOT EXISTS is idempotent
    conn.execute("""
        CREATE TABLE IF NOT EXISTS order_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            ticker TEXT,
            side TEXT,
            shares REAL,
            limit_price REAL,
            fill_price REAL,
            slippage_bps REAL,
            status TEXT,
            portfolio_value_at_trade REAL
        )
    """)
    conn.commit()


def _calc_slippage_bps(signal_price: float, fill_price: float, side: str) -> float:
    if signal_price is None or signal_price == 0:
        return 0.0
    raw = (fill_price - signal_price) / signal_price * 10_000
    # For sells/shorts a higher fill price is better — flip sign so positive = cost
    if side in ("SELL", "SHORT", "COVER"):
        raw = -raw
    return round(raw, 2)


def record_slippage(order_id: int, signal_price: float, fill_price: float, side: str):
    bps = _calc_slippage_bps(signal_price, fill_price, side)
    conn = get_db()
    try:
        _init_tables(conn)
        with conn:
            conn.execute(
                "UPDATE order_log SET slippage_bps=? WHERE id=?",
                (bps, order_id),
            )
        log.info(f"Slippage recorded: order {order_id} {side} signal={signal_price:.4f} "
                 f"fill={fill_price:.4f} => {bps:.1f} bps")
    except Exception as e:
        log.error(f"record_slippage failed for order {order_id}: {e}")
    finally:
        conn.close()


def get_slippage_stats() -> dict:
    conn = get_db()
    try:
        _init_tables(conn)
        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
        rows = conn.execute(
            """
            SELECT id, ticker, side, shares, fill_price, slippage_bps
            FROM order_log
            WHERE status IN ('FILLED','SIMULATED') AND timestamp>=? AND slippage_bps IS NOT NULL
            """,
            (cutoff,),
        ).fetchall()

        if not rows:
            return {
                "avg_bps": 0.0,
                "median_bps": 0.0,
                "p95_bps": 0.0,
                "total_cost_usd": 0.0,
                "worst_5_fills": [],
            }

        df = pd.DataFrame([dict(r) for r in rows])
        bps_series = df["slippage_bps"]

        # Total cost: slippage_bps / 10000 * (shares * fill_price)
        df["trade_value"] = df["shares"] * df["fill_price"]
        df["cost_usd"] = df["slippage_bps"] / 10_000 * df["trade_value"]
        total_cost = float(df["cost_usd"].sum())

        worst_5 = df.nlargest(5, "slippage_bps")[
            ["id", "ticker", "side", "slippage_bps", "cost_usd"]
        ].to_dict(orient="records")

        return {
            "avg_bps": round(float(bps_series.mean()), 2),
            "median_bps": round(float(bps_series.median()), 2),
            "p95_bps": round(float(bps_series.quantile(0.95)), 2),
            "total_cost_usd": round(total_cost, 2),
            "worst_5_fills": worst_5,
        }
    except Exception as e:
        log.error(f"get_slippage_stats failed: {e}")
        return {"avg_bps": 0.0, "median_bps": 0.0, "p95_bps": 0.0, "total_cost_usd": 0.0, "worst_5_fills": []}
    finally:
        conn.close()


def get_slippage_dashboard() -> pd.DataFrame:
    conn = get_db()
    try:
        _init_tables(conn)
        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
        rows = conn.execute(
            """
            SELECT id, timestamp, ticker, side, shares, limit_price, fill_price,
                   slippage_bps, status, portfolio_value_at_trade
            FROM order_log
            WHERE status IN ('FILLED','SIMULATED') AND timestamp>=?
            ORDER BY timestamp DESC
            """,
            (cutoff,),
        ).fetchall()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame([dict(r) for r in rows])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    except Exception as e:
        log.error(f"get_slippage_dashboard failed: {e}")
        return pd.DataFrame()
    finally:
        conn.close()
