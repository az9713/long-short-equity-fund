import json
from datetime import date
from utils import get_logger, get_config
from analysis.ai_client import call_llm

log = get_logger(__name__)


def _build_context(include_weekly: bool = False) -> str:
    ctx = {}

    try:
        from risk.risk_state import load_risk_state
        ctx["risk_state"] = load_risk_state()
    except Exception as e:
        log.warning(f"Could not load risk_state: {e}")
        ctx["risk_state"] = {}

    try:
        from portfolio.state import get_positions
        positions = get_positions()
        if not positions.empty:
            ctx["positions"] = positions[["ticker", "side", "shares", "entry_price", "unrealized_pnl"]].to_dict(orient="records")
    except Exception as e:
        log.warning(f"Could not load positions: {e}")

    try:
        from data.providers import get_vix
        ctx["vix"] = get_vix()
    except Exception:
        ctx["vix"] = None

    ctx["date"] = str(date.today())

    if include_weekly:
        try:
            from reporting.win_loss import get_win_loss_stats
            ctx["win_loss"] = get_win_loss_stats()
        except Exception as e:
            log.warning(f"Could not load win/loss stats: {e}")

        try:
            from reporting.tear_sheet import get_metrics_vs_spy
            ctx["metrics"] = get_metrics_vs_spy(days=30)
        except Exception as e:
            log.warning(f"Could not load metrics: {e}")

    # Truncate context to ~50K chars
    raw = json.dumps(ctx, default=str)
    if len(raw) > 50_000:
        raw = raw[:50_000] + "...[truncated]"

    return raw


def generate_weekly_commentary() -> str:
    context = _build_context(include_weekly=True)

    system = (
        "You are JARVIS, the AI portfolio analyst for Meridian Capital Partners. "
        "Write a professional weekly performance commentary in 3-4 paragraphs. "
        "Cover: (1) market context and VIX regime, (2) portfolio performance and key drivers, "
        "(3) factor positioning and any risk events, (4) outlook for the coming week. "
        "Be concise, data-driven, and use a confident institutional tone. "
        "Do not use bullet points. Write in flowing prose."
    )

    user = f"Weekly portfolio data as of {date.today()}:\n\n{context}"

    result = call_llm(system, user, temperature=0.4, analyzer="commentary", ticker="PORTFOLIO")

    if not result:
        return "Commentary unavailable — LLM call failed or API key not set."

    return result


def generate_lp_letter() -> str:
    context = _build_context(include_weekly=False)

    system = (
        "You are JARVIS, the AI portfolio analyst for Meridian Capital Partners. "
        "Write a professional daily LP letter in exactly 3-4 paragraphs. "
        "Format: Start with 'Dear Limited Partners,' then cover today's performance, "
        "current positioning, and brief market outlook. "
        "Sign off as '— JARVIS, Portfolio Analyst'. "
        "Use a formal, institutional tone appropriate for a hedge fund LP communication. "
        "Include factual data where available. Do not fabricate specific numbers not in the context."
    )

    user = (
        f"Today's portfolio snapshot ({date.today()}):\n\n{context}\n\n"
        "Write the LP letter now."
    )

    body = call_llm(system, user, temperature=0.3, analyzer="lp_letter", ticker="PORTFOLIO")

    if not body:
        body = (
            "Dear Limited Partners,\n\n"
            "Portfolio data is currently unavailable. Please check back shortly.\n\n"
            "— JARVIS, Portfolio Analyst"
        )

    today_str = date.today().strftime("%B %d, %Y")
    letter = f"""MERIDIAN CAPITAL PARTNERS
{today_str}

{body}

---
This communication is for informational purposes only and does not constitute investment advice. Past performance is not indicative of future results. This material is intended solely for the use of the named recipient(s) and may contain confidential information.
"""
    return letter
