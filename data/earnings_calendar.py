import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from utils import get_db, get_logger

log = get_logger(__name__)


def _create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            ticker TEXT NOT NULL,
            earnings_date TEXT NOT NULL,
            fiscal_quarter TEXT,
            PRIMARY KEY (ticker, earnings_date)
        )
    """)
    conn.commit()


def update_earnings_calendar(tickers: list[str]):
    if not tickers:
        return

    conn = get_db()
    _create_table(conn)

    today = datetime.utcnow().date()
    end = today + timedelta(days=60)  # fetch slightly beyond 30 days for buffer

    for ticker in tickers:
        try:
            tk = yf.Ticker(ticker)
            try:
                cal = tk.calendar
            except Exception:
                cal = None

            earnings_date = None

            # calendar is sometimes a dict, sometimes a DataFrame
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed and len(ed) > 0:
                    earnings_date = pd.Timestamp(ed[0]).date().isoformat()
            elif isinstance(cal, pd.DataFrame) and not cal.empty:
                if "Earnings Date" in cal.index:
                    ed = cal.loc["Earnings Date"].iloc[0]
                    if pd.notna(ed):
                        earnings_date = pd.Timestamp(ed).date().isoformat()

            # Also try get_earnings_dates
            if not earnings_date:
                try:
                    dates_df = tk.get_earnings_dates(limit=4)
                    if dates_df is not None and not dates_df.empty:
                        future = dates_df[dates_df.index.tz_localize(None) >= pd.Timestamp(today)]
                        if not future.empty:
                            earnings_date = future.index[0].date().isoformat()
                except Exception:
                    pass

            if earnings_date:
                with conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO earnings_calendar (ticker, earnings_date, fiscal_quarter)
                        VALUES (?, ?, ?)
                        """,
                        (ticker, earnings_date, None),
                    )
                log.info(f"Earnings date for {ticker}: {earnings_date}")
            else:
                log.warning(f"No upcoming earnings date found for {ticker}")

        except Exception as e:
            log.error(f"Failed earnings calendar for {ticker}: {e}")

        time.sleep(0.3)

    conn.close()
    log.info("Earnings calendar update complete")


def get_upcoming_earnings(days: int = 30) -> pd.DataFrame:
    conn = get_db()
    try:
        today = datetime.utcnow().date().isoformat()
        end = (datetime.utcnow().date() + timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT ticker, earnings_date, fiscal_quarter
            FROM earnings_calendar
            WHERE earnings_date >= ? AND earnings_date <= ?
            ORDER BY earnings_date
            """,
            (today, end),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception as e:
        log.error(f"get_upcoming_earnings failed: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def days_to_earnings(ticker: str) -> int | None:
    conn = get_db()
    try:
        today = datetime.utcnow().date().isoformat()
        row = conn.execute(
            """
            SELECT earnings_date FROM earnings_calendar
            WHERE ticker=? AND earnings_date >= ?
            ORDER BY earnings_date
            LIMIT 1
            """,
            (ticker, today),
        ).fetchone()
        if not row:
            return None
        ed = datetime.fromisoformat(row["earnings_date"]).date()
        return (ed - datetime.utcnow().date()).days
    except Exception as e:
        log.error(f"days_to_earnings failed for {ticker}: {e}")
        return None
    finally:
        conn.close()
