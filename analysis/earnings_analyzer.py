from utils import get_logger
from data.transcripts import get_transcript
from analysis.ai_client import call_llm, extract_json
from analysis.cache import get_cached, set_cached, make_artifact_id

log = get_logger(__name__)

_ANALYZER = "earnings"
_MAX_CHARS = 120_000

_SYSTEM = (
    "You are a senior equity analyst. Analyze this earnings call transcript "
    "and return JSON only."
)

_USER_TEMPLATE = """Analyze the following earnings call transcript for {ticker}.
Return a JSON object with exactly these keys:
{{
  "management_confidence": <integer 1-10>,
  "revenue_guidance": <integer 1-10>,
  "margin_trajectory": <integer 1-10>,
  "competitive_position": <integer 1-10>,
  "risk_factors": <integer 1-10>,
  "capital_allocation": <integer 1-10>,
  "bull_case": "<one paragraph>",
  "bear_case": "<one paragraph>",
  "key_quotes": ["<quote1>", "<quote2>", "<quote3>"],
  "one_line_summary": "<one sentence>"
}}

Scores of 10 are most positive. Return ONLY the JSON object, no other text.

TRANSCRIPT:
{transcript}"""


def analyze_earnings(ticker: str) -> dict | None:
    transcript = get_transcript(ticker)
    if not transcript:
        log.info(f"No transcript available for {ticker}")
        return None

    transcript = transcript[:_MAX_CHARS]
    artifact_id = make_artifact_id(transcript)

    cached = get_cached(_ANALYZER, ticker, artifact_id)
    if cached:
        log.info(f"Cache hit: earnings/{ticker}")
        return cached

    user = _USER_TEMPLATE.format(ticker=ticker, transcript=transcript)
    raw = call_llm(_SYSTEM, user, temperature=0.1, analyzer=_ANALYZER, ticker=ticker)
    if raw is None:
        return None

    result = extract_json(raw)
    if result is None:
        log.warning(f"earnings_analyzer: JSON extraction failed for {ticker}")
        return None

    set_cached(_ANALYZER, ticker, artifact_id, result)
    return result
