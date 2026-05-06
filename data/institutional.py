import re
import time
import requests
import pandas as pd
from lxml import etree as lxml_etree
from datetime import datetime, timedelta
from ratelimit import limits, sleep_and_retry
from tenacity import retry, stop_after_attempt, wait_exponential
from utils import get_db, get_logger, sec_headers

log = get_logger(__name__)

SEC_BASE = "https://data.sec.gov"

# 9 major funds: name -> CIK (zero-padded to 10 digits)
TRACKED_FUNDS = {
    "Citadel":        "0001423053",
    "Point72":        "0001500217",
    "Bridgewater":    "0001350694",
    "Tiger Global":   "0001167483",
    "Third Point":    "0001040273",
    "Appaloosa":      "0000814585",
    "Baupost":        "0001061768",
    "Pershing Square":"0001336528",
    "Coatue":         "0001358042",
}

# Noise words to strip from issuer names during fuzzy matching
_NOISE = re.compile(
    r"\b(inc|corp|co|ltd|llc|plc|holdings|group|international|the|and|of|class\s+[ab])\b",
    re.IGNORECASE,
)


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


def _create_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS institutional_holdings (
            fund_name TEXT NOT NULL,
            ticker TEXT NOT NULL,
            shares REAL,
            market_value REAL,
            report_date TEXT NOT NULL,
            change_shares REAL,
            PRIMARY KEY (fund_name, ticker, report_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cusip_ticker_map (
            cusip TEXT PRIMARY KEY,
            ticker TEXT,
            matched_name TEXT
        )
    """)
    conn.commit()


def _normalize_name(name: str) -> str:
    name = name.upper().strip()
    name = re.sub(r"[^\w\s]", " ", name)
    name = _NOISE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _build_universe_name_map(conn) -> dict[str, str]:
    """Build {normalized_company_name -> ticker} from universe table."""
    rows = conn.execute("SELECT ticker, company FROM universe WHERE company IS NOT NULL").fetchall()
    result = {}
    for r in rows:
        key = _normalize_name(r["company"])
        if key:
            result[key] = r["ticker"]
    return result


def _match_cusip_to_ticker(conn, cusip: str, issuer_name: str, name_map: dict) -> str | None:
    # Check cache first
    row = conn.execute("SELECT ticker FROM cusip_ticker_map WHERE cusip=?", (cusip,)).fetchone()
    if row:
        return row["ticker"]

    normalized = _normalize_name(issuer_name)
    ticker = None

    # Exact match first
    if normalized in name_map:
        ticker = name_map[normalized]
    else:
        # Partial match: require both that normalized starts with uni_name prefix AND
        # that uni_name is at least 6 chars (avoids short ambiguous matches like "CO")
        for uni_name, uni_ticker in name_map.items():
            if len(uni_name) < 6:
                continue
            # normalized must start with the full uni_name, or uni_name must start with normalized
            if normalized.startswith(uni_name) or uni_name.startswith(normalized):
                ticker = uni_ticker
                break

    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO cusip_ticker_map (cusip, ticker, matched_name) VALUES (?, ?, ?)",
            (cusip, ticker, issuer_name),
        )

    if not ticker:
        log.warning(f"Could not match CUSIP {cusip} / '{issuer_name}' to universe ticker")

    return ticker


def _get_latest_13f_accession(cik: str) -> tuple[str, str] | None:
    """Return (accession_no_dashes, report_date) for the fund's latest 13F-HR."""
    url = f"{SEC_BASE}/submissions/CIK{cik}.json"
    try:
        r = _sec_get_retry(url)
        subs = r.json()
    except Exception as e:
        log.error(f"Failed to fetch submissions for CIK {cik}: {e}")
        return None

    filings = subs.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    accessions = filings.get("accessionNumber", [])
    dates = filings.get("filingDate", [])

    for i, f in enumerate(forms):
        if f in ("13F-HR", "13F-HR/A"):
            return accessions[i].replace("-", ""), dates[i]

    return None


def _parse_13f_xml(xml_text: str) -> list[dict]:
    """Parse 13F information table XML and return list of holdings."""
    holdings = []
    try:
        # Strip namespace declarations and namespaced attrs (e.g. xsi:schemaLocation)
        # that often arrive without an accompanying xmlns:xsi declaration.
        xml_clean = re.sub(r'\s+xmlns[^"]*"[^"]*"', "", xml_text)
        xml_clean = re.sub(r'\s+[A-Za-z_][\w.-]*:[A-Za-z_][\w.-]*="[^"]*"', "", xml_clean)
        parser = lxml_etree.XMLParser(recover=True)
        root = lxml_etree.fromstring(xml_clean.encode("utf-8", errors="replace"), parser)
        if root is None:
            log.warning("Failed to parse 13F XML: empty root after recovery")
            return holdings
    except Exception as e:
        log.warning(f"Failed to parse 13F XML: {e}")
        return holdings

    def txt(node, local_name):
        els = node.xpath(f".//*[local-name()='{local_name}']")
        return (els[0].text or "").strip() if els else ""

    for info in root.xpath("//*[local-name()='infoTable']"):
        name = txt(info, "nameOfIssuer")
        cusip = txt(info, "cusip")
        value = txt(info, "value")       # in thousands of USD
        shares = txt(info, "sshPrnamt")  # shares or principal amount

        try:
            mkt_val = float(value) * 1000 if value else None
        except Exception:
            mkt_val = None

        try:
            sh = float(shares) if shares else None
        except Exception:
            sh = None

        holdings.append({
            "cusip": cusip,
            "issuer_name": name,
            "market_value": mkt_val,
            "shares": sh,
        })

    return holdings


def _fetch_13f_holdings(cik: str, accession: str) -> list[dict]:
    """Fetch and parse 13F information table from SEC EDGAR archives."""
    cik_int = str(int(cik))

    # Try to find the information table document via the filing index
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/"
    try:
        r = _sec_get_retry(
            index_url,
            headers={**sec_headers(), "Accept": "text/html,application/json,*/*"},
        )
        # Look for infotable in links
        doc_name = None
        for line in r.text.split("\n"):
            if "infotable" in line.lower() or "information_table" in line.lower():
                m = re.search(r'href="([^"]+\.xml)"', line, re.IGNORECASE)
                if m:
                    doc_name = m.group(1).split("/")[-1]
                    break

        if not doc_name:
            # Try standard naming
            for candidate in ["informationtable.xml", "infotable.xml", "form13fInfoTable.xml"]:
                try:
                    xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{candidate}"
                    rx = _sec_get_retry(
                        xml_url,
                        headers={**sec_headers(), "Accept": "application/xml,*/*"},
                    )
                    return _parse_13f_xml(rx.text)
                except Exception:
                    pass

        if doc_name:
            xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc_name}"
            rx = _sec_get_retry(
                xml_url,
                headers={**sec_headers(), "Accept": "application/xml,*/*"},
            )
            return _parse_13f_xml(rx.text)

    except Exception as e:
        log.error(f"Failed to fetch 13F holdings for CIK {cik_int} acc {accession}: {e}")

    return []


def update_institutional(tickers: list[str], skip: bool = False):
    if skip:
        log.info("Skipping institutional 13F update (--no-13f)")
        return

    conn = get_db()
    _create_tables(conn)
    name_map = _build_universe_name_map(conn)

    for fund_name, cik in TRACKED_FUNDS.items():
        log.info(f"Processing 13F for {fund_name} (CIK={cik})")

        result = _get_latest_13f_accession(cik)
        if not result:
            log.warning(f"No 13F-HR found for {fund_name}")
            continue

        accession, report_date = result

        # Skip if already have this quarter's data
        existing = conn.execute(
            "SELECT COUNT(*) as cnt FROM institutional_holdings WHERE fund_name=? AND report_date=?",
            (fund_name, report_date),
        ).fetchone()
        if existing and existing["cnt"] > 0:
            log.info(f"{fund_name} {report_date} already stored, skipping")
            continue

        holdings = _fetch_13f_holdings(cik, accession)
        if not holdings:
            log.warning(f"No holdings parsed for {fund_name}")
            continue

        # Prior quarter holdings for change calculation
        prior = conn.execute(
            """
            SELECT ticker, shares FROM institutional_holdings
            WHERE fund_name=?
            ORDER BY report_date DESC
            LIMIT 500
            """,
            (fund_name,),
        ).fetchall()
        prior_map = {r["ticker"]: r["shares"] for r in prior}

        inserted = 0
        for h in holdings:
            cusip = h.get("cusip", "")
            issuer = h.get("issuer_name", "")
            ticker = _match_cusip_to_ticker(conn, cusip, issuer, name_map)

            if not ticker:
                continue

            # Only store if ticker is in our universe
            if tickers and ticker not in tickers:
                continue

            current_shares = h.get("shares")
            prior_shares = prior_map.get(ticker)
            # If prior is None this is a new position; store full shares as change (prior treated as 0)
            if current_shares is not None and prior_shares is not None:
                change = current_shares - prior_shares
            elif current_shares is not None and prior_shares is None:
                change = current_shares  # new position opened this quarter
            else:
                change = None

            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO institutional_holdings
                    (fund_name, ticker, shares, market_value, report_date, change_shares)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (fund_name, ticker, current_shares, h.get("market_value"), report_date, change),
                )
            inserted += 1

        log.info(f"Stored {inserted} holdings for {fund_name} ({report_date})")
        time.sleep(0.5)

    conn.close()
    log.info("Institutional update complete")


def get_institutional_summary(ticker: str) -> dict:
    conn = get_db()
    try:
        # Latest report date across all funds
        latest_date_row = conn.execute(
            "SELECT MAX(report_date) as latest FROM institutional_holdings WHERE ticker=?",
            (ticker,),
        ).fetchone()
        if not latest_date_row or not latest_date_row["latest"]:
            return {"funds_holding": 0, "change_vs_prior": None, "new_entry_flag": False}

        latest_date = latest_date_row["latest"]

        rows = conn.execute(
            """
            SELECT fund_name, shares, change_shares
            FROM institutional_holdings
            WHERE ticker=? AND report_date=?
            """,
            (ticker, latest_date),
        ).fetchall()

        funds_holding = len(rows)
        total_change = sum(r["change_shares"] or 0 for r in rows)

        # New entry: change_shares ≈ shares (prior was zero, i.e. fund opened a brand-new position)
        # We store change = current_shares when prior_shares was None, so change == shares for new entries
        new_entries = sum(
            1 for r in rows
            if r["change_shares"] is not None and r["shares"] is not None
            and r["shares"] > 0
            and abs(r["change_shares"] - r["shares"]) / r["shares"] < 0.01  # within 1%
        )
        new_entry_flag = new_entries >= 3

        return {
            "funds_holding": funds_holding,
            "change_vs_prior": total_change if rows else None,
            "new_entry_flag": new_entry_flag,
        }
    except Exception as e:
        log.error(f"get_institutional_summary failed for {ticker}: {e}")
        return {"funds_holding": 0, "change_vs_prior": None, "new_entry_flag": False}
    finally:
        conn.close()
