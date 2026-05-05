import time
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from utils import get_db, get_logger

log = get_logger(__name__)


def _create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analyst_estimates (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            forward_eps REAL,
            price_target_mean REAL,
            price_target_high REAL,
            price_target_low REAL,
            recommendation TEXT,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.commit()


def update_estimates(tickers: list[str]):
    if not tickers:
        return

    conn = get_db()
    _create_table(conn)

    today = datetime.utcnow().date().isoformat()

    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            forward_eps = info.get("forwardEps")
            pt_mean = info.get("targetMeanPrice")
            pt_high = info.get("targetHighPrice")
            pt_low = info.get("targetLowPrice")
            recommendation = info.get("recommendationKey")

            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO analyst_estimates
                    (ticker, date, forward_eps, price_target_mean, price_target_high,
                     price_target_low, recommendation)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ticker, today, forward_eps, pt_mean, pt_high, pt_low, recommendation),
                )
            log.info(f"Estimates updated for {ticker}: fwd_eps={forward_eps}, pt={pt_mean}")
        except Exception as e:
            log.error(f"Failed estimates for {ticker}: {e}")

        time.sleep(0.3)

    conn.close()
    log.info("Estimates update complete")


def get_estimate_revisions(ticker: str) -> dict:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT date, forward_eps FROM analyst_estimates
            WHERE ticker=? AND forward_eps IS NOT NULL
            ORDER BY date DESC
            LIMIT 95
            """,
            (ticker,),
        ).fetchall()

        if not rows:
            return {"delta_30d": None, "delta_60d": None, "delta_90d": None}

        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")

        # Use tz-naive today to match stored date strings parsed without tz
        today = pd.Timestamp.now().normalize()
        latest = df.iloc[-1]["forward_eps"]

        def delta_at(days):
            cutoff = today - timedelta(days=days)
            past = df[df["date"] <= cutoff]
            if past.empty:
                return None
            past_eps = past.iloc[-1]["forward_eps"]
            if past_eps == 0 or past_eps is None:
                return None
            return float(latest - past_eps)

        return {
            "delta_30d": delta_at(30),
            "delta_60d": delta_at(60),
            "delta_90d": delta_at(90),
        }
    except Exception as e:
        log.error(f"get_estimate_revisions failed for {ticker}: {e}")
        return {"delta_30d": None, "delta_60d": None, "delta_90d": None}
    finally:
        conn.close()
