import os
import requests
from datetime import datetime
from utils import get_db, get_logger

log = get_logger(__name__)

FMP_BASE = "https://financialmodelingprep.com/api/v3"
MAX_CHARS = 120_000


def _create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            ticker TEXT NOT NULL,
            fiscal_quarter TEXT NOT NULL,
            content TEXT,
            fetched_date TEXT,
            PRIMARY KEY (ticker, fiscal_quarter)
        )
    """)
    conn.commit()


def _fetch_from_fmp(ticker: str, fiscal_quarter: str | None) -> str | None:
    api_key = os.getenv("FMP_API_KEY")
    if not api_key:
        log.info("FMP key not set, skipping transcripts")
        return None

    try:
        # Get list of available transcripts
        url = f"{FMP_BASE}/earning_call_transcript/{ticker}"
        params = {"apikey": api_key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        items = r.json()

        if not items:
            log.warning(f"No transcripts available for {ticker}")
            return None

        # Pick the requested quarter or the most recent
        if fiscal_quarter:
            match = next(
                (i for i in items if i.get("quarter") and f"Q{i['quarter']} {i.get('year', '')}" == fiscal_quarter),
                None,
            )
            if not match:
                log.warning(f"No transcript matching {fiscal_quarter} for {ticker}")
                return None
            item = match
        else:
            item = items[0]

        content = item.get("content", "")
        if content:
            return content[:MAX_CHARS]

    except Exception as e:
        log.error(f"FMP transcript fetch failed for {ticker}: {e}")

    return None


def get_transcript(ticker: str, fiscal_quarter: str = None) -> str | None:
    api_key = os.getenv("FMP_API_KEY")
    if not api_key:
        log.info("FMP key not set, skipping transcripts")
        return None

    conn = get_db()
    _create_table(conn)

    try:
        # Check cache first
        if fiscal_quarter:
            row = conn.execute(
                "SELECT content FROM transcripts WHERE ticker=? AND fiscal_quarter=?",
                (ticker, fiscal_quarter),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT content FROM transcripts WHERE ticker=? ORDER BY fiscal_quarter DESC LIMIT 1",
                (ticker,),
            ).fetchone()

        if row and row["content"]:
            return row["content"]

        # Fetch from FMP
        content = _fetch_from_fmp(ticker, fiscal_quarter)
        if not content:
            return None

        quarter_key = fiscal_quarter or "latest"
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO transcripts (ticker, fiscal_quarter, content, fetched_date)
                VALUES (?, ?, ?, ?)
                """,
                (ticker, quarter_key, content, datetime.utcnow().date().isoformat()),
            )

        return content

    except Exception as e:
        log.error(f"get_transcript failed for {ticker}: {e}")
        return None
    finally:
        conn.close()
