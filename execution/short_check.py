from datetime import datetime, timedelta
from utils import get_db, get_logger

log = get_logger(__name__)

CACHE_TTL_DAYS = 7


def _init_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS short_availability (
            ticker TEXT PRIMARY KEY,
            shortable INTEGER,
            easy_to_borrow INTEGER,
            checked_at TEXT
        )
    """)
    conn.commit()


def _get_cached(conn, ticker: str) -> dict | None:
    cutoff = (datetime.utcnow() - timedelta(days=CACHE_TTL_DAYS)).isoformat()
    row = conn.execute(
        "SELECT shortable, easy_to_borrow, checked_at FROM short_availability WHERE ticker=? AND checked_at>=?",
        (ticker, cutoff),
    ).fetchone()
    if row:
        return {"shortable": bool(row["shortable"]), "easy_to_borrow": bool(row["easy_to_borrow"])}
    return None


def _upsert_cache(conn, ticker: str, shortable: bool, easy_to_borrow: bool):
    now = datetime.utcnow().isoformat()
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO short_availability (ticker, shortable, easy_to_borrow, checked_at)
            VALUES (?, ?, ?, ?)
            """,
            (ticker, int(shortable), int(easy_to_borrow), now),
        )


def _fetch_from_alpaca(ticker: str) -> tuple[bool, bool]:
    # Returns (shortable, easy_to_borrow)
    try:
        from alpaca.trading.client import TradingClient
        import os
        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        if not api_key or not secret_key:
            return True, True  # assume shortable when no broker connection

        from utils import get_config
        cfg = get_config().get("execution", {})
        from execution.broker import _is_live_mode
        paper = not _is_live_mode()

        client = TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)
        asset = client.get_asset(ticker)
        shortable = bool(getattr(asset, "shortable", False))
        etb = bool(getattr(asset, "easy_to_borrow", False))
        return shortable, etb
    except ImportError:
        # alpaca-py not installed
        return True, True
    except Exception as e:
        log.warning(f"Alpaca asset check failed for {ticker}: {e}")
        # Fail open — treat as shortable so we don't silently block trades
        return True, True


def is_shortable(ticker: str) -> bool:
    conn = get_db()
    try:
        _init_tables(conn)
        cached = _get_cached(conn, ticker)
        if cached is not None:
            if not cached["shortable"]:
                log.info(f"SKIP: {ticker} not available to short (cached)")
                return False
            return True

        shortable, etb = _fetch_from_alpaca(ticker)
        _upsert_cache(conn, ticker, shortable, etb)

        if not shortable:
            log.info(f"SKIP: {ticker} not available to short")
            return False
        return True
    except Exception as e:
        log.error(f"is_shortable failed for {ticker}: {e}")
        return True  # fail open
    finally:
        conn.close()


def update_short_cache(tickers: list[str]):
    if not tickers:
        return

    conn = get_db()
    try:
        _init_tables(conn)
        for ticker in tickers:
            try:
                shortable, etb = _fetch_from_alpaca(ticker)
                _upsert_cache(conn, ticker, shortable, etb)
                log.info(f"Short cache updated: {ticker} shortable={shortable} etb={etb}")
            except Exception as e:
                log.warning(f"Could not update short cache for {ticker}: {e}")
    except Exception as e:
        log.error(f"update_short_cache failed: {e}")
    finally:
        conn.close()
