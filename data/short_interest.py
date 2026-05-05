import time
import yfinance as yf
from datetime import datetime
from utils import get_db, get_logger

log = get_logger(__name__)


def _create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS short_interest (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            shares_short REAL,
            short_ratio REAL,
            short_percent_float REAL,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.commit()


def update_short_interest(tickers: list[str]):
    if not tickers:
        return

    conn = get_db()
    _create_table(conn)

    today = datetime.utcnow().date().isoformat()

    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            shares_short = info.get("sharesShort")
            short_ratio = info.get("shortRatio")
            short_pct = info.get("shortPercentOfFloat")

            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO short_interest
                    (ticker, date, shares_short, short_ratio, short_percent_float)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (ticker, today, shares_short, short_ratio, short_pct),
                )
            log.info(f"Short interest updated for {ticker}: pct_float={short_pct}")
        except Exception as e:
            log.error(f"Failed short interest for {ticker}: {e}")

        time.sleep(0.3)

    conn.close()
    log.info("Short interest update complete")


def get_short_interest(ticker: str) -> dict:
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT shares_short, short_ratio, short_percent_float, date
            FROM short_interest
            WHERE ticker=?
            ORDER BY date DESC
            LIMIT 1
            """,
            (ticker,),
        ).fetchone()
        if not row:
            return {}
        return dict(row)
    except Exception as e:
        log.error(f"get_short_interest failed for {ticker}: {e}")
        return {}
    finally:
        conn.close()
