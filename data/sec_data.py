import re
import time
import json
import requests
import pandas as pd
from lxml import etree
from datetime import datetime, timedelta
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_exponential
from utils import get_db, get_logger, sec_headers

log = get_logger(__name__)

SEC_BASE = "https://data.sec.gov"
FORM4_CONTENT_CAP = 80_000


# ── rate-limited SEC fetcher ──────────────────────────────────────────────────

@sleep_and_retry
@limits(calls=8, period=1)
def _sec_get(url: str, **kwargs) -> requests.Response:
    headers = {**sec_headers(), "Accept": "application/json", **kwargs.pop("headers", {})}
    r = requests.get(url, headers=headers, timeout=20, **kwargs)
    r.raise_for_status()
    return r


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _sec_get_retry(url: str, **kwargs) -> requests.Response:
    return _sec_get(url, **kwargs)


# ── CIK mapping ───────────────────────────────────────────────────────────────

def _ensure_cik_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cik_map (
            ticker TEXT PRIMARY KEY,
            cik TEXT,
            name TEXT,
            last_updated TEXT
        )
    """)
    conn.commit()


def _load_cik_map(conn) -> dict[str, str]:
    """Load ticker->CIK map; refresh from SEC if empty or older than 7 days."""
    _ensure_cik_table(conn)

    row = conn.execute("SELECT last_updated FROM cik_map LIMIT 1").fetchone()
    needs_refresh = True
    if row and row["last_updated"]:
        last = datetime.fromisoformat(row["last_updated"])
        needs_refresh = datetime.utcnow() - last > timedelta(days=7)

    if needs_refresh:
        try:
            r = _sec_get_retry("https://www.sec.gov/files/company_tickers.json")
            data = r.json()
            now = datetime.utcnow().isoformat()
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO cik_map (ticker, cik, name, last_updated) VALUES (?,?,?,?)",
                    [
                        (v["ticker"], str(v["cik_str"]).zfill(10), v["title"], now)
                        for v in data.values()
                    ],
                )
            log.info(f"Loaded {len(data)} CIK mappings from SEC")
        except Exception as e:
            log.error(f"Failed to load CIK map: {e}")

    rows = conn.execute("SELECT ticker, cik FROM cik_map").fetchall()
    return {r["ticker"]: r["cik"] for r in rows}


def _get_cik(ticker: str, cik_map: dict) -> str | None:
    # Try direct lookup, then without dashes (BRK-B -> BRKB in SEC's map)
    cik = cik_map.get(ticker.upper()) or cik_map.get(ticker.upper().replace("-", ""))
    if not cik:
        log.warning(f"No CIK found for {ticker}")
    return cik


# ── filing tables ─────────────────────────────────────────────────────────────

def _create_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sec_filings (
            ticker TEXT NOT NULL,
            form_type TEXT NOT NULL,
            filing_date TEXT,
            accession_number TEXT NOT NULL,
            content TEXT,
            PRIMARY KEY (ticker, accession_number)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS insider_transactions (
            ticker TEXT NOT NULL,
            accession_number TEXT NOT NULL,
            insider_name TEXT,
            insider_title TEXT,
            transaction_date TEXT,
            transaction_code TEXT,
            shares REAL,
            price REAL,
            shares_after REAL,
            is_cluster_buy INTEGER DEFAULT 0,
            PRIMARY KEY (ticker, accession_number, insider_name, transaction_date, transaction_code)
        )
    """)
    conn.commit()


# ── submissions API ───────────────────────────────────────────────────────────

def _get_submissions(cik: str) -> dict | None:
    url = f"{SEC_BASE}/submissions/CIK{cik}.json"
    try:
        r = _sec_get_retry(url)
        return r.json()
    except Exception as e:
        log.error(f"Failed to fetch submissions for CIK {cik}: {e}")
        return None


def _get_recent_filings(cik: str, form_type: str, limit: int = 5) -> list[dict]:
    """Return list of {accession, filing_date, primary_document} for a given form."""
    subs = _get_submissions(cik)
    if not subs:
        return []

    filings = subs.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    accessions = filings.get("accessionNumber", [])
    dates = filings.get("filingDate", [])
    docs = filings.get("primaryDocument", [])

    results = []
    for i, f in enumerate(forms):
        if f == form_type:
            results.append({
                "accession": accessions[i].replace("-", ""),
                "accession_raw": accessions[i],
                "filing_date": dates[i],
                "primary_doc": docs[i],
            })
            if len(results) >= limit:
                break

    return results


# ── filing content fetchers ───────────────────────────────────────────────────

def _fetch_filing_text(cik: str, accession_no: str, primary_doc: str, cap: int = FORM4_CONTENT_CAP) -> str:
    cik_int = str(int(cik))
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_no}/{primary_doc}"
    try:
        r = _sec_get_retry(url, headers={**sec_headers(), "Accept": "text/html,application/xhtml+xml,text/plain,*/*"})
        text = r.text
        # Strip HTML tags if present
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:cap]
    except Exception as e:
        log.error(f"Failed to fetch filing text {url}: {e}")
        return ""


def _extract_risk_factors(text: str) -> str:
    """Try to extract Item 1A (Risk Factors) section from 10-K text.

    10-Ks contain two occurrences of 'Item 1A': once in the table of contents
    (short, followed quickly by Item 1B) and once at the actual section start.
    We take the LAST match with meaningful content (>500 chars) to skip the TOC.
    """
    pattern = re.compile(
        r"item\s*1a[\.\s]+risk\s+factors(.*?)item\s*1b",
        re.IGNORECASE | re.DOTALL,
    )
    matches = list(pattern.finditer(text))
    # Find the last match that has substantial content (not just a TOC entry)
    for m in reversed(matches):
        content = m.group(1).strip()
        if len(content) > 500:
            return content[:FORM4_CONTENT_CAP]

    # Fallback: try Item 1A to Item 2
    pattern2 = re.compile(
        r"item\s*1a[\.\s]+risk\s+factors(.*?)item\s*2",
        re.IGNORECASE | re.DOTALL,
    )
    matches2 = list(pattern2.finditer(text))
    for m2 in reversed(matches2):
        content = m2.group(1).strip()
        if len(content) > 500:
            return content[:FORM4_CONTENT_CAP]

    log.warning("Could not find Risk Factors section with substantial content, using raw text")
    return text[:FORM4_CONTENT_CAP]


# ── Form 4 parser ─────────────────────────────────────────────────────────────

# Form 4 XML namespaces
_NS = {
    "ns": "http://www.sec.gov/edgar/ownership",
    "ns2": "http://www.sec.gov/edgar/common",
}


def _parse_form4(xml_text: str) -> list[dict]:
    """Parse Form 4 XML and return list of transaction dicts."""
    transactions = []
    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except Exception as e:
        log.warning(f"Failed to parse Form 4 XML: {e}")
        return transactions

    def text(node, path):
        """Extract text via xpath with namespace fallback."""
        # Try with namespace
        for ns_prefix in ["ns:", "ns2:", ""]:
            parts = path.split("/")
            ns_path = "/".join(f"{ns_prefix}{p}" if p and not p.startswith("@") else p for p in parts)
            try:
                els = root.xpath(f".//{ns_path}", namespaces=_NS)
                if els:
                    return (els[0].text or "").strip()
            except Exception:
                pass
        # Fallback: strip namespace and try local-name
        try:
            tag_name = path.split("/")[-1]
            els = root.xpath(f".//*[local-name()='{tag_name}']")
            if els:
                return (els[0].text or "").strip()
        except Exception:
            pass
        return None

    def find_all(local_name):
        return root.xpath(f".//*[local-name()='{local_name}']")

    # Reporter info
    insider_name = None
    insider_title = None
    name_els = find_all("rptOwnerName")
    if name_els:
        insider_name = (name_els[0].text or "").strip()

    title_els = find_all("officerTitle")
    if title_els:
        insider_title = (title_els[0].text or "").strip()

    # Non-derivative transactions
    for txn in find_all("nonDerivativeTransaction"):
        def txn_text(local_name):
            els = txn.xpath(f".//*[local-name()='{local_name}']")
            return (els[0].text or "").strip() if els else None

        code = txn_text("transactionCode")
        date_str = txn_text("transactionDate") or txn_text("value")
        shares_str = txn_text("transactionShares")
        price_str = txn_text("transactionPricePerShare")
        shares_after_str = txn_text("sharesOwnedFollowingTransaction")

        def safe_float(s):
            try:
                return float(s) if s else None
            except Exception:
                return None

        transactions.append({
            "insider_name": insider_name,
            "insider_title": insider_title,
            "transaction_date": date_str,
            "transaction_code": code,
            "shares": safe_float(shares_str),
            "price": safe_float(price_str),
            "shares_after": safe_float(shares_after_str),
        })

    return transactions


# ── cluster buy detection ─────────────────────────────────────────────────────

def _mark_cluster_buys(conn):
    """Flag is_cluster_buy=1 where 3+ insiders bought same ticker within 30 days."""
    try:
        cutoff = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
        # Find tickers with cluster buys
        rows = conn.execute(
            """
            SELECT ticker
            FROM insider_transactions
            WHERE transaction_code='P' AND transaction_date >= ?
            GROUP BY ticker
            HAVING COUNT(DISTINCT insider_name) >= 3
            """,
            (cutoff,),
        ).fetchall()

        cluster_tickers = [r["ticker"] for r in rows]
        if cluster_tickers:
            with conn:
                conn.executemany(
                    """
                    UPDATE insider_transactions
                    SET is_cluster_buy=1
                    WHERE ticker=? AND transaction_code='P' AND transaction_date >= ?
                    """,
                    [(t, cutoff) for t in cluster_tickers],
                )
            log.info(f"Cluster buys flagged for: {cluster_tickers}")
    except Exception as e:
        log.error(f"Failed marking cluster buys: {e}")


# ── main update function ──────────────────────────────────────────────────────

def update_sec_data(tickers: list[str], no_filings: bool = False, forms: list[str] | None = None):
    if not tickers:
        return

    conn = get_db()
    _create_tables(conn)
    cik_map = _load_cik_map(conn)

    cutoff_180 = (datetime.utcnow() - timedelta(days=180)).date().isoformat()
    fetch_forms = forms or (["10-K", "10-Q", "8-K"] if not no_filings else [])

    for ticker in tickers:
        cik = _get_cik(ticker, cik_map)
        if not cik:
            continue

        log.info(f"Processing SEC data for {ticker} (CIK={cik})")

        # ── Document filings ───────────────────────────────────────────────
        if not no_filings:
            for form_type in fetch_forms:
                limit = 1 if form_type == "10-K" else (2 if form_type == "10-Q" else 10)
                filing_list = _get_recent_filings(cik, form_type, limit=limit)

                for filing in filing_list:
                    acc = filing["accession"]
                    filing_date = filing["filing_date"]

                    # Skip if older than 180 days for 8-K
                    if form_type == "8-K" and filing_date < cutoff_180:
                        break

                    # Skip if already stored
                    existing = conn.execute(
                        "SELECT 1 FROM sec_filings WHERE ticker=? AND accession_number=?",
                        (ticker, acc),
                    ).fetchone()
                    if existing:
                        continue

                    text = _fetch_filing_text(cik, acc, filing["primary_doc"])

                    # For 10-K: try to extract just Risk Factors section
                    if form_type == "10-K" and text:
                        text = _extract_risk_factors(text)

                    with conn:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO sec_filings
                            (ticker, form_type, filing_date, accession_number, content)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (ticker, form_type, filing_date, acc, text),
                        )
                    log.info(f"Stored {form_type} for {ticker} ({filing_date})")

        # ── Form 4 (insider transactions) ──────────────────────────────────
        form4_filings = _get_recent_filings(cik, "4", limit=50)

        for filing in form4_filings:
            if filing["filing_date"] < cutoff_180:
                break

            acc = filing["accession"]

            # Skip if already processed
            existing = conn.execute(
                "SELECT 1 FROM insider_transactions WHERE ticker=? AND accession_number=?",
                (ticker, acc),
            ).fetchone()
            if existing:
                continue

            xml_text = ""
            try:
                cik_int = str(int(cik))
                primary = filing.get("primary_doc", "")
                # SEC sometimes returns "xslF345X06/form4.xml" — that's the stylesheet
                # render (HTML), not raw XML. Strip any directory prefix so we hit the
                # raw XML at the filing root.
                if primary.endswith(".xml") and "/" in primary:
                    primary = primary.rsplit("/", 1)[1]
                xml_doc = primary if primary.endswith(".xml") else None

                if xml_doc is None:
                    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/index.json"
                    idx_r = _sec_get_retry(index_url)
                    items = idx_r.json().get("directory", {}).get("item", [])
                    candidates = [
                        it["name"] for it in items
                        if it.get("name", "").endswith(".xml")
                        and not it["name"].lower().startswith("filingsummary")
                    ]
                    if not candidates:
                        log.warning(f"No Form 4 XML found in index for {ticker} {acc}")
                        continue
                    xml_doc = candidates[0]

                xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{xml_doc}"
                r = _sec_get_retry(
                    xml_url,
                    headers={"Accept": "application/xml,text/xml,*/*"},
                )
                xml_text = r.text

            except Exception as e:
                log.warning(f"Failed to fetch Form 4 XML for {ticker} {acc}: {e}")
                continue

            txns = _parse_form4(xml_text)
            if not txns:
                continue

            rows = []
            for txn in txns:
                rows.append((
                    ticker,
                    acc,
                    txn.get("insider_name"),
                    txn.get("insider_title"),
                    txn.get("transaction_date"),
                    txn.get("transaction_code"),
                    txn.get("shares"),
                    txn.get("price"),
                    txn.get("shares_after"),
                    0,  # is_cluster_buy, set later
                ))

            with conn:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO insider_transactions
                    (ticker, accession_number, insider_name, insider_title,
                     transaction_date, transaction_code, shares, price,
                     shares_after, is_cluster_buy)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            log.info(f"Stored {len(rows)} Form 4 transactions for {ticker}")

    # Post-insert: mark cluster buys
    _mark_cluster_buys(conn)
    conn.close()
    log.info("SEC data update complete")


# ── query functions ───────────────────────────────────────────────────────────

def get_insider_transactions(ticker: str, days: int = 90) -> pd.DataFrame:
    conn = get_db()
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
        rows = conn.execute(
            """
            SELECT insider_name, insider_title, transaction_date, transaction_code,
                   shares, price, shares_after, is_cluster_buy
            FROM insider_transactions
            WHERE ticker=? AND transaction_date >= ?
            ORDER BY transaction_date DESC
            """,
            (ticker, cutoff),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception as e:
        log.error(f"get_insider_transactions failed for {ticker}: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def get_filing_text(ticker: str, form_type: str) -> str | None:
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT content FROM sec_filings
            WHERE ticker=? AND form_type=?
            ORDER BY filing_date DESC
            LIMIT 1
            """,
            (ticker, form_type),
        ).fetchone()
        return row["content"] if row else None
    except Exception as e:
        log.error(f"get_filing_text failed for {ticker}/{form_type}: {e}")
        return None
    finally:
        conn.close()
