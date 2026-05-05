from utils import get_logger
from data.sec_data import get_filing_text
from analysis.ai_client import call_llm, extract_json
from analysis.cache import get_cached, set_cached, make_artifact_id

log = get_logger(__name__)

_ANALYZER = "risk"
_MAX_CHARS = 80_000

_SYSTEM = (
    "You are a risk analyst. Extract and assess material risks from this 10-K filing "
    "and return JSON only."
)

_USER_TEMPLATE = """Analyze the following 10-K risk factors section for {ticker}.
Separate material company-specific risks from generic boilerplate language.
Return a JSON object with exactly these keys:
{{
  "new_risks": ["<risk1>", "<risk2>"],
  "material_risks": ["<risk1>", "<risk2>"],
  "boilerplate_percentage": <integer 0-100>,
  "risk_severity": "<LOW|MEDIUM|HIGH|CRITICAL>",
  "one_line_summary": "<one sentence>"
}}

Return ONLY the JSON object, no other text.

10-K RISK FACTORS:
{filing_text}"""


def analyze_risk(ticker: str) -> dict | None:
    filing_text = get_filing_text(ticker, "10-K")
    if not filing_text:
        log.info(f"No 10-K cached for {ticker} — skipping risk analysis")
        return None

    filing_text = filing_text[:_MAX_CHARS]
    artifact_id = make_artifact_id(filing_text)

    cached = get_cached(_ANALYZER, ticker, artifact_id)
    if cached:
        log.info(f"Cache hit: risk/{ticker}")
        return cached

    user = _USER_TEMPLATE.format(ticker=ticker, filing_text=filing_text)
    raw = call_llm(_SYSTEM, user, temperature=0.1, analyzer=_ANALYZER, ticker=ticker)
    if raw is None:
        return None

    result = extract_json(raw)
    if result is None:
        log.warning(f"risk_analyzer: JSON extraction failed for {ticker}")
        return None

    set_cached(_ANALYZER, ticker, artifact_id, result)
    return result
