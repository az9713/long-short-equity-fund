import json
from utils import get_logger
from analysis.ai_client import call_llm, extract_json
from analysis.cache import get_cached, set_cached, make_artifact_id

log = get_logger(__name__)

_ANALYZER = "sector"

_SYSTEM = (
    "You are a sector strategist. Based on these stock analyses, return JSON only."
)

_USER_TEMPLATE = """Based on the following fundamental analyses for stocks in the {sector} sector,
rank and compare the investment opportunities.

Return a JSON object with exactly these keys:
{{
  "top_long_idea": "<TICKER>",
  "top_short_idea": "<TICKER>",
  "sector_outlook": "<BULLISH|NEUTRAL|BEARISH>",
  "rankings": [
    {{"ticker": "<TICKER>", "rank": 1, "reasoning": "<one sentence>"}},
    ...
  ],
  "one_line_summary": "<one sentence>"
}}

Include all tickers in rankings. Return ONLY the JSON object, no other text.

TICKER ANALYSES:
{analyses}"""


def analyze_sector(sector: str, ticker_results: dict) -> dict | None:
    if not ticker_results:
        log.info(f"No ticker results for sector {sector}")
        return None

    # Serialize all ticker results to structured text
    lines = []
    for ticker, result in ticker_results.items():
        lines.append(f"--- {ticker} ---")
        lines.append(json.dumps(result, indent=2))

    analyses_text = "\n".join(lines)
    artifact_id = make_artifact_id(f"{sector}:{analyses_text}")

    cached = get_cached(_ANALYZER, sector, artifact_id)
    if cached:
        log.info(f"Cache hit: sector/{sector}")
        return cached

    user = _USER_TEMPLATE.format(sector=sector, analyses=analyses_text)
    # Use sector name as both analyzer context and ticker slot
    raw = call_llm(_SYSTEM, user, temperature=0.1, analyzer=_ANALYZER, ticker=sector)
    if raw is None:
        return None

    result = extract_json(raw)
    if result is None:
        log.warning(f"sector_analysis: JSON extraction failed for {sector}")
        return None

    set_cached(_ANALYZER, sector, artifact_id, result)
    return result
