import os
import time
import requests
import pandas as pd
from io import StringIO
from datetime import datetime, timedelta
from utils import get_config, get_db, get_logger, is_dev_mode

log = get_logger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
REFRESH_DAYS = 7


def _create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS universe (
            ticker TEXT PRIMARY KEY,
            company TEXT,
            sector TEXT,
            sub_industry TEXT,
            is_benchmark INTEGER DEFAULT 0,
            last_updated TEXT
        )
    """)
    conn.commit()


def _needs_refresh(conn) -> bool:
    row = conn.execute(
        "SELECT last_updated FROM universe WHERE is_benchmark=0 LIMIT 1"
    ).fetchone()
    if not row or not row["last_updated"]:
        return True
    last = datetime.fromisoformat(row["last_updated"])
    return datetime.utcnow() - last > timedelta(days=REFRESH_DAYS)


def _fetch_sp500() -> pd.DataFrame:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Meridian Capital Research)"}
    try:
        r = requests.get(WIKI_URL, headers=headers, timeout=15)
        r.raise_for_status()
        tables = pd.read_html(StringIO(r.text))
        df = tables[0]
    except Exception as e:
        log.error(f"Failed to fetch S&P 500 from Wikipedia: {e}")
        return pd.DataFrame()

    # Normalize column names
    df.columns = [c.strip() for c in df.columns]
    rename = {
        "Symbol": "ticker",
        "Security": "company",
        "GICS Sector": "sector",
        "GICS Sub-Industry": "sub_industry",
    }
    df = df.rename(columns=rename)[["ticker", "company", "sector", "sub_industry"]]

    # yfinance uses dashes not dots
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    return df


def _upsert_sp500(conn, df: pd.DataFrame):
    now = datetime.utcnow().isoformat()
    with conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO universe (ticker, company, sector, sub_industry, is_benchmark, last_updated)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            [(row.ticker, row.company, row.sector, row.sub_industry, now) for row in df.itertuples()],
        )


def _upsert_benchmarks(conn, tickers: list[str]):
    now = datetime.utcnow().isoformat()
    with conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO universe (ticker, company, sector, sub_industry, is_benchmark, last_updated)
            VALUES (?, ?, 'Benchmark', 'Benchmark ETF', 1, ?)
            """,
            [(t, t, now) for t in tickers],
        )


def get_universe() -> list[str]:
    cfg = get_config()

    # dev mode override: env var set by run_data.py --dev flag, or config
    force_dev = os.getenv("FORCE_DEV") == "1" or is_dev_mode()

    if force_dev:
        dev_tickers = cfg.get("dev_tickers", [])
        log.info(f"Dev mode: returning {len(dev_tickers)} tickers")
        # Still ensure DB table exists and benchmarks are stored
        conn = get_db()
        _create_table(conn)
        benchmarks = cfg.get("universe", {}).get("benchmark_tickers", [])
        _upsert_benchmarks(conn, benchmarks)
        conn.close()
        return dev_tickers

    conn = get_db()
    _create_table(conn)

    benchmarks = cfg.get("universe", {}).get("benchmark_tickers", [])
    _upsert_benchmarks(conn, benchmarks)

    if _needs_refresh(conn):
        log.info("Refreshing S&P 500 universe from Wikipedia")
        df = _fetch_sp500()
        if df.empty:
            log.warning("S&P 500 fetch returned empty; using cached universe if available")
        else:
            _upsert_sp500(conn, df)
            log.info(f"Stored {len(df)} S&P 500 tickers")

    rows = conn.execute(
        "SELECT ticker FROM universe WHERE is_benchmark=0 ORDER BY ticker"
    ).fetchall()
    tickers = [r["ticker"] for r in rows]
    conn.close()

    log.info(f"Universe: {len(tickers)} tickers")
    return tickers
