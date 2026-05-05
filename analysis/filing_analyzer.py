import pandas as pd
from utils import get_logger
from data.fundamentals import get_fundamentals
from analysis.ai_client import call_llm, extract_json
from analysis.cache import get_cached, set_cached, make_artifact_id

log = get_logger(__name__)

_ANALYZER = "filing"

_SYSTEM = (
    "You are a forensic accounting analyst. Review these financial statements "
    "and return JSON only."
)

_USER_TEMPLATE = """Analyze the following financial data for {ticker} and assess earnings quality.
Return a JSON object with exactly these keys:
{{
  "earnings_quality_score": <integer 1-10>,
  "balance_sheet_score": <integer 1-10>,
  "red_flags": ["<flag1>", "<flag2>"],
  "green_flags": ["<flag1>", "<flag2>"],
  "risk_level": "<LOW|MEDIUM|HIGH>",
  "accruals_assessment": "<one sentence>",
  "one_line_summary": "<one sentence>"
}}

Scores of 10 are highest quality. Return ONLY the JSON object, no other text.

FINANCIAL DATA (8 quarters, most recent first):
{financials}"""


def _format_fundamentals(df: pd.DataFrame) -> str:
    if df.empty:
        return "No data available."

    lines = []
    for period, row in df.iterrows():
        period_str = str(period)[:10] if hasattr(period, "__str__") else str(period)

        def fmt(val):
            if val is None or pd.isna(val):
                return "N/A"
            return f"{val:,.0f}"

        def pct(a, b):
            if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
                return "N/A"
            return f"{a / b * 100:.1f}%"

        revenue = row.get("revenue")
        ni = row.get("ni")
        cfo = row.get("cfo")
        fcf = row.get("fcf")
        total_debt = row.get("total_debt")
        total_equity = row.get("total_equity")

        de_ratio = "N/A"
        if total_debt is not None and total_equity is not None:
            if not pd.isna(total_debt) and not pd.isna(total_equity) and total_equity != 0:
                de_ratio = f"{total_debt / total_equity:.2f}x"

        lines.append(
            f"Period: {period_str} | "
            f"Revenue: {fmt(revenue)} | "
            f"Net Income: {fmt(ni)} | "
            f"CFO: {fmt(cfo)} | "
            f"FCF: {fmt(fcf)} | "
            f"Gross Margin: {pct(row.get('gross_profit'), revenue)} | "
            f"Net Margin: {pct(ni, revenue)} | "
            f"D/E Ratio: {de_ratio}"
        )

    return "\n".join(lines)


def analyze_filing(ticker: str) -> dict | None:
    df = get_fundamentals(ticker, quarters=8)
    if df.empty:
        log.info(f"No fundamentals available for {ticker}")
        return None

    financials_text = _format_fundamentals(df)
    artifact_id = make_artifact_id(financials_text)

    cached = get_cached(_ANALYZER, ticker, artifact_id)
    if cached:
        log.info(f"Cache hit: filing/{ticker}")
        return cached

    user = _USER_TEMPLATE.format(ticker=ticker, financials=financials_text)
    raw = call_llm(_SYSTEM, user, temperature=0.1, analyzer=_ANALYZER, ticker=ticker)
    if raw is None:
        return None

    result = extract_json(raw)
    if result is None:
        log.warning(f"filing_analyzer: JSON extraction failed for {ticker}")
        return None

    set_cached(_ANALYZER, ticker, artifact_id, result)
    return result
