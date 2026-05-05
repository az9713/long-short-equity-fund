import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from utils import get_db, get_logger

log = get_logger(__name__)

PRICE_HISTORY_DAYS = 730  # 2 years for initial backfill


def _create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            adj_close REAL,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.commit()


def _get_last_date(conn, ticker: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) as last_date FROM daily_prices WHERE ticker=?", (ticker,)
    ).fetchone()
    return row["last_date"] if row else None


def _fetch_prices(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    try:
        df = yf.download(
            tickers,
            start=start,
            end=end,
            auto_adjust=False,
            group_by="ticker",
            progress=False,
            threads=False,
        )
        return df
    except Exception as e:
        log.error(f"yfinance download failed: {e}")
        return pd.DataFrame()


def _normalize_single(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    # Normalize column names to lowercase
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    col_map = {
        "adj_close": "adj_close",
        "adj close": "adj_close",
    }
    df = df.rename(columns=col_map)
    df = df.dropna(subset=["close"])
    df.index = pd.to_datetime(df.index)
    return df


def update_prices(tickers: list[str]):
    if not tickers:
        return

    conn = get_db()
    _create_table(conn)

    today = datetime.utcnow().date()
    default_start = (today - timedelta(days=PRICE_HISTORY_DAYS)).isoformat()

    # Group tickers by their start date for batch efficiency
    # Use a simple approach: batch all tickers that need same start date
    full_backfill = []
    incremental = {}  # ticker -> start_date

    for ticker in tickers:
        last = _get_last_date(conn, ticker)
        if not last:
            full_backfill.append(ticker)
        else:
            next_day = (datetime.fromisoformat(last).date() + timedelta(days=1)).isoformat()
            if next_day <= today.isoformat():
                incremental[ticker] = next_day

    def _insert_batch(df_raw, batch_tickers, start):
        if df_raw.empty:
            return 0
        inserted = 0
        for t in batch_tickers:
            try:
                if len(batch_tickers) == 1:
                    # Single ticker: columns are flat
                    tdf = _normalize_single(df_raw.copy(), t)
                else:
                    if t not in df_raw.columns.get_level_values(0):
                        log.warning(f"No price data returned for {t}")
                        continue
                    tdf = df_raw[t].copy()
                    tdf = _normalize_single(tdf, t)

                if tdf.empty:
                    continue

                rows = []
                for date, row in tdf.iterrows():
                    rows.append((
                        t,
                        date.strftime("%Y-%m-%d"),
                        float(row.get("open", 0) or 0),
                        float(row.get("high", 0) or 0),
                        float(row.get("low", 0) or 0),
                        float(row.get("close", 0) or 0),
                        float(row.get("volume", 0) or 0),
                        float(row.get("adj_close", row.get("close", 0)) or 0),
                    ))

                with conn:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO daily_prices
                        (ticker, date, open, high, low, close, volume, adj_close)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        rows,
                    )
                inserted += len(rows)
                log.info(f"Inserted {len(rows)} price rows for {t}")
            except Exception as e:
                log.error(f"Failed inserting prices for {t}: {e}")
        return inserted

    # Batch download for full backfill
    if full_backfill:
        log.info(f"Backfilling {len(full_backfill)} tickers from {default_start}")
        # Process in chunks of 50 to avoid yfinance limits
        chunk_size = 50
        for i in range(0, len(full_backfill), chunk_size):
            chunk = full_backfill[i : i + chunk_size]
            df_raw = _fetch_prices(chunk, default_start, today.isoformat())
            _insert_batch(df_raw, chunk, default_start)
            if i + chunk_size < len(full_backfill):
                time.sleep(1)

    # Incremental updates — group by start date and batch
    if incremental:
        # Find earliest start to do one big batch fetch
        earliest = min(incremental.values())
        inc_tickers = list(incremental.keys())
        log.info(f"Incremental update for {len(inc_tickers)} tickers from {earliest}")
        chunk_size = 50
        for i in range(0, len(inc_tickers), chunk_size):
            chunk = inc_tickers[i : i + chunk_size]
            df_raw = _fetch_prices(chunk, earliest, today.isoformat())
            _insert_batch(df_raw, chunk, earliest)
            if i + chunk_size < len(inc_tickers):
                time.sleep(1)

    conn.close()
    log.info("Price update complete")


def get_prices(ticker: str, days: int = 252) -> pd.DataFrame:
    conn = get_db()
    try:
        cutoff = (datetime.utcnow().date() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume, adj_close
            FROM daily_prices
            WHERE ticker=? AND date>=?
            ORDER BY date
            """,
            (ticker, cutoff),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        return df
    except Exception as e:
        log.error(f"get_prices failed for {ticker}: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def get_adv(ticker: str) -> float:
    """Returns 20-day average daily volume in dollars (avg(close * volume))."""
    df = get_prices(ticker, days=30)
    if df.empty or "close" not in df.columns or "volume" not in df.columns:
        log.warning(f"Insufficient price data for ADV: {ticker}")
        return 0.0
    dv = df["close"] * df["volume"]
    return float(dv.tail(20).mean())
