import pandas as pd
from utils import get_logger
from data.sec_data import get_insider_transactions
from analysis.ai_client import call_llm, extract_json
from analysis.cache import get_cached, set_cached, make_artifact_id

log = get_logger(__name__)

_ANALYZER = "insider"

_SYSTEM = (
    "You are an insider trading analyst. Interpret these Form 4 transactions "
    "and return JSON only."
)

_USER_TEMPLATE = """Analyze these Form 4 insider transactions for {ticker} from the past 90 days.
Return a JSON object with exactly these keys:
{{
  "signal_strength": "<STRONG BUY|BUY|NEUTRAL|SELL|STRONG SELL>",
  "confidence": <integer 1-10>,
  "key_transactions": ["<description1>", "<description2>"],
  "reasoning": "<2-3 sentences>",
  "one_line_summary": "<one sentence>"
}}

Return ONLY the JSON object, no other text.

FORM 4 TRANSACTIONS:
{transactions}"""


def _format_transactions(df: pd.DataFrame) -> str:
    lines = []
    for _, row in df.iterrows():
        code = row.get("transaction_code", "?")
        code_desc = {
            "P": "Open market purchase",
            "S": "Open market sale",
            "A": "Grant/award",
            "D": "Disposition",
            "M": "Option exercise",
            "G": "Gift",
            "F": "Tax withholding",
        }.get(code, f"Code {code}")

        shares = row.get("shares")
        price = row.get("price")
        shares_str = f"{shares:,.0f}" if shares and pd.notna(shares) else "N/A"
        price_str = f"${price:.2f}" if price and pd.notna(price) else "N/A"
        value_str = "N/A"
        if shares and price and pd.notna(shares) and pd.notna(price):
            value_str = f"${shares * price:,.0f}"

        cluster = " [CLUSTER BUY]" if row.get("is_cluster_buy") else ""
        lines.append(
            f"Date: {row.get('transaction_date', 'N/A')} | "
            f"Insider: {row.get('insider_name', 'N/A')} ({row.get('insider_title', 'N/A')}) | "
            f"Type: {code_desc} | "
            f"Shares: {shares_str} | "
            f"Price: {price_str} | "
            f"Value: {value_str}{cluster}"
        )

    return "\n".join(lines)


def analyze_insider(ticker: str) -> dict | None:
    df = get_insider_transactions(ticker, days=90)
    if df is None or df.empty:
        log.info(f"No insider transactions available for {ticker}")
        return None

    transactions_text = _format_transactions(df)
    artifact_id = make_artifact_id(transactions_text)

    cached = get_cached(_ANALYZER, ticker, artifact_id)
    if cached:
        log.info(f"Cache hit: insider/{ticker}")
        return cached

    user = _USER_TEMPLATE.format(ticker=ticker, transactions=transactions_text)
    raw = call_llm(_SYSTEM, user, temperature=0.1, analyzer=_ANALYZER, ticker=ticker)
    if raw is None:
        return None

    result = extract_json(raw)
    if result is None:
        log.warning(f"insider_analyzer: JSON extraction failed for {ticker}")
        return None

    set_cached(_ANALYZER, ticker, artifact_id, result)
    return result
