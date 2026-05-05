import json
from datetime import datetime
from pathlib import Path
from utils import ROOT, get_logger

log = get_logger(__name__)

STATE_PATH = ROOT / "risk" / "risk_state.json"

_DEFAULTS = {
    "last_updated": None,
    "portfolio_value": 100_000.0,
    "peak_value": 100_000.0,
    "daily_pnl": 0.0,
    "weekly_pnl": 0.0,
    "drawdown_from_peak": 0.0,
    "nav_history": [],          # list of {date, value} for weekly PnL calculation
    "circuit_breaker_usage": [],
    "factor_exposures": {},
    "risk_decomposition": {"factor_pct": 0.0, "specific_pct": 1.0},
    "mctr": {},
    "alerts": [],
}


def load_risk_state() -> dict:
    try:
        if STATE_PATH.exists():
            with open(STATE_PATH) as f:
                state = json.load(f)
            # Merge with defaults for any missing keys
            merged = dict(_DEFAULTS)
            merged.update(state)
            return merged
    except Exception as e:
        log.warning(f"Could not load risk_state.json: {e}")
    return dict(_DEFAULTS)


def save_risk_state(state: dict):
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        state["last_updated"] = datetime.utcnow().isoformat()
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        log.error(f"Failed to save risk_state.json: {e}")


def update_risk_state(portfolio_value: float, scored_df=None, weights: dict = None):
    state = load_risk_state()

    prev_value = state.get("portfolio_value", portfolio_value)
    peak_value = state.get("peak_value", portfolio_value)

    # Update peak
    if portfolio_value > peak_value:
        peak_value = portfolio_value
    state["peak_value"] = peak_value

    # Daily PnL as fraction
    if prev_value and prev_value != portfolio_value:
        state["daily_pnl"] = (portfolio_value - prev_value) / prev_value
    else:
        state["daily_pnl"] = state.get("daily_pnl", 0.0)

    # Drawdown from peak
    if peak_value > 0:
        state["drawdown_from_peak"] = (portfolio_value - peak_value) / peak_value
    else:
        state["drawdown_from_peak"] = 0.0

    state["portfolio_value"] = portfolio_value

    # Track NAV history for weekly PnL — keep last 10 days
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    nav_history = state.get("nav_history", [])
    if not nav_history or nav_history[-1].get("date") != today_str:
        nav_history.append({"date": today_str, "value": portfolio_value})
    else:
        nav_history[-1]["value"] = portfolio_value
    # Keep only last 10 entries
    nav_history = nav_history[-10:]
    state["nav_history"] = nav_history

    # Weekly PnL: compare today vs 5 trading days ago
    if len(nav_history) >= 5:
        week_ago_value = nav_history[-5]["value"]
        if week_ago_value > 0:
            state["weekly_pnl"] = (portfolio_value - week_ago_value) / week_ago_value
    # If fewer than 5 days of history, weekly_pnl stays at prior value

    # Factor risk decomposition
    if weights and scored_df is not None and not scored_df.empty:
        try:
            from risk.factor_risk_model import build_factor_model, decompose_portfolio_risk
            factor_model = build_factor_model(scored_df)
            if factor_model is not None:
                decomp = decompose_portfolio_risk(weights, factor_model)
                state["risk_decomposition"] = {
                    "factor_pct": round(decomp.get("factor_pct", 0.0), 4),
                    "specific_pct": round(decomp.get("specific_pct", 1.0), 4),
                }
                # mctr values are dicts: {mctr, weight, disproportionate}
                state["mctr"] = decomp.get("mctr", {})
        except Exception as e:
            log.warning(f"Could not update factor risk decomposition: {e}")

    # Collect current alerts
    alerts = []
    try:
        from risk.circuit_breakers import check_circuit_breakers
        cb_alerts = check_circuit_breakers(portfolio_value)
        for cb in cb_alerts:
            alerts.append(f"[{cb['level']}] {cb['reason']}")
        state["circuit_breaker_usage"] = cb_alerts
    except Exception as e:
        log.warning(f"Circuit breaker check failed: {e}")

    try:
        from risk.tail_risk import check_tail_risk
        tail = check_tail_risk()
        if tail.get("action") != "OK":
            alerts.append(f"[TAIL] {tail.get('message', '')}")
    except Exception as e:
        log.warning(f"Tail risk check failed: {e}")

    state["alerts"] = alerts
    save_risk_state(state)
    log.info(f"Risk state updated: portfolio_value={portfolio_value:.2f} drawdown={state['drawdown_from_peak']:.3%}")
