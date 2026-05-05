import pandas as pd
from utils import get_logger
from analysis.cache import get_latest_for_ticker

log = get_logger(__name__)

# Blend weights: 60% quant (Layer 2), 40% AI fundamental (Layer 3)
_QUANT_WEIGHT = 0.60
_AI_WEIGHT = 0.40

_RISK_SEVERITY_MAP = {
    "LOW": 80,
    "MEDIUM": 60,
    "HIGH": 30,
    "CRITICAL": 10,
}

_INSIDER_SIGNAL_MAP = {
    "STRONG BUY": 90,
    "BUY": 70,
    "NEUTRAL": 50,
    "SELL": 30,
    "STRONG SELL": 10,
}


def _earnings_score(result: dict) -> float | None:
    keys = [
        "management_confidence", "revenue_guidance", "margin_trajectory",
        "competitive_position", "risk_factors", "capital_allocation",
    ]
    vals = [result.get(k) for k in keys if result.get(k) is not None]
    if not vals:
        return None
    # Average 1-10 scores, scale to 0-100
    return (sum(vals) / len(vals)) * 10.0


def _filing_score(result: dict) -> float | None:
    eq = result.get("earnings_quality_score")
    bs = result.get("balance_sheet_score")
    vals = [v for v in [eq, bs] if v is not None]
    if not vals:
        return None
    return (sum(vals) / len(vals)) * 10.0


def _risk_score(result: dict) -> float | None:
    severity = result.get("risk_severity", "").upper()
    return float(_RISK_SEVERITY_MAP.get(severity, 50))


def _insider_score(result: dict) -> float | None:
    signal = result.get("signal_strength", "").upper()
    return float(_INSIDER_SIGNAL_MAP.get(signal, 50))


def get_combined_score(ticker: str, quant_score: float) -> float:
    cache = get_latest_for_ticker(ticker)

    ai_scores = []

    earnings_result = cache.get("earnings")
    if earnings_result:
        s = _earnings_score(earnings_result)
        if s is not None:
            ai_scores.append(s)

    filing_result = cache.get("filing")
    if filing_result:
        s = _filing_score(filing_result)
        if s is not None:
            ai_scores.append(s)

    risk_result = cache.get("risk")
    if risk_result:
        s = _risk_score(risk_result)
        if s is not None:
            ai_scores.append(s)

    insider_result = cache.get("insider")
    if insider_result:
        s = _insider_score(insider_result)
        if s is not None:
            ai_scores.append(s)

    if not ai_scores:
        # No AI analysis available — use 100% quant score
        return float(quant_score)

    ai_composite = sum(ai_scores) / len(ai_scores)
    combined = _QUANT_WEIGHT * quant_score + _AI_WEIGHT * ai_composite
    return round(combined, 2)


def run_combined_scoring(scored_df: pd.DataFrame) -> pd.DataFrame:
    if scored_df.empty:
        return scored_df

    result = scored_df.copy()

    combined_scores = []
    has_ai = []

    for _, row in result.iterrows():
        ticker = row.get("ticker") or (row.name if result.index.name == "ticker" else None)
        if ticker is None:
            combined_scores.append(row.get("composite", 50.0))
            has_ai.append(False)
            continue

        quant = float(row.get("composite", 50.0))
        cache = get_latest_for_ticker(ticker)
        ai_available = bool(cache)

        combined = get_combined_score(ticker, quant)
        combined_scores.append(combined)
        has_ai.append(ai_available)

    result["combined_score"] = combined_scores
    result["has_ai_analysis"] = has_ai

    # Re-rank combined_score within each sector
    if "sector" in result.columns:
        result["combined_rank"] = result.groupby("sector")["combined_score"].rank(
            ascending=False, method="min"
        )

    log.info(
        f"Combined scoring complete: {sum(has_ai)}/{len(result)} tickers have AI analysis"
    )
    return result
