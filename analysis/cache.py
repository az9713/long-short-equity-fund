import json
import hashlib
from datetime import datetime, timedelta
from utils import get_db, get_logger, get_config

log = get_logger(__name__)


def _create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_cache (
            analyzer TEXT NOT NULL,
            ticker TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            result TEXT NOT NULL,
            cached_at DATE NOT NULL,
            PRIMARY KEY (analyzer, ticker, artifact_id)
        )
    """)
    conn.commit()


def make_artifact_id(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def get_cached(analyzer: str, ticker: str, artifact_id: str) -> dict | None:
    cfg = get_config()
    ttl_days = cfg.get("ai", {}).get("cache_ttl_days", 30)
    cutoff = (datetime.utcnow() - timedelta(days=ttl_days)).date().isoformat()

    conn = get_db()
    try:
        _create_table(conn)
        row = conn.execute(
            """
            SELECT result FROM analysis_cache
            WHERE analyzer=? AND ticker=? AND artifact_id=? AND cached_at >= ?
            """,
            (analyzer, ticker, artifact_id, cutoff),
        ).fetchone()
        if row:
            return json.loads(row["result"])
        return None
    except Exception as e:
        log.error(f"Cache read failed for {analyzer}/{ticker}: {e}")
        return None
    finally:
        conn.close()


def set_cached(analyzer: str, ticker: str, artifact_id: str, result: dict):
    # Never cache None or empty
    if not result:
        return

    conn = get_db()
    try:
        _create_table(conn)
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO analysis_cache
                (analyzer, ticker, artifact_id, result, cached_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (analyzer, ticker, artifact_id, json.dumps(result),
                 datetime.utcnow().date().isoformat()),
            )
    except Exception as e:
        log.error(f"Cache write failed for {analyzer}/{ticker}: {e}")
    finally:
        conn.close()


def get_latest_for_ticker(ticker: str) -> dict[str, dict]:
    """Return {analyzer: result_dict} for all most-recent cached results for this ticker."""
    cfg = get_config()
    ttl_days = cfg.get("ai", {}).get("cache_ttl_days", 30)
    cutoff = (datetime.utcnow() - timedelta(days=ttl_days)).date().isoformat()

    conn = get_db()
    try:
        _create_table(conn)
        # MAX(cached_at) ensures SQLite returns the row containing the maximum,
        # not an arbitrary row within each group.
        rows = conn.execute(
            """
            SELECT analyzer, result, MAX(cached_at) AS latest
            FROM analysis_cache
            WHERE ticker=? AND cached_at >= ?
            GROUP BY analyzer
            """,
            (ticker, cutoff),
        ).fetchall()
        return {r["analyzer"]: json.loads(r["result"]) for r in rows}
    except Exception as e:
        log.error(f"Cache latest-for-ticker failed for {ticker}: {e}")
        return {}
    finally:
        conn.close()
