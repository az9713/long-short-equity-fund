import json
from datetime import datetime, timedelta
from pathlib import Path
from utils import ROOT, get_logger, get_config
from portfolio.state import get_positions

log = get_logger(__name__)
cfg = get_config().get("risk", {})

HALT_LOCK_PATH = ROOT / "risk" / "halt.lock"
STATE_PATH = ROOT / "risk" / "risk_state.json"


def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            with open(STATE_PATH) as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"Could not read risk_state.json: {e}")
    return {}


def get_daily_pnl(portfolio_value: float) -> float:
    state = _load_state()
    prev = state.get("portfolio_value")
    if prev and prev > 0:
        return (portfolio_value - prev) / prev
    return 0.0


def get_weekly_pnl(portfolio_value: float) -> float:
    # Weekly PnL is persisted by update_risk_state; returns 0.0 if no history exists yet
    state = _load_state()
    val = state.get("weekly_pnl")
    if val is None:
        return 0.0
    return float(val)


def get_peak_value() -> float:
    state = _load_state()
    peak = state.get("peak_value")
    if peak and peak > 0:
        return float(peak)
    # If no state, treat current portfolio as peak — read from state's portfolio_value
    return float(state.get("portfolio_value", 100_000.0))


def write_halt_lock(reason: str):
    HALT_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().isoformat()
    content = f"{timestamp}\n{reason}\n"
    HALT_LOCK_PATH.write_text(content)
    log.critical(f"SYSTEM HALTED — {reason}")


def clear_halt_lock():
    if HALT_LOCK_PATH.exists():
        HALT_LOCK_PATH.unlink()
        log.info("Halt lock cleared")
    else:
        log.info("No halt lock to clear")


def check_circuit_breakers(portfolio_value: float) -> list[dict]:
    triggered = []

    daily_limit = cfg.get("daily_loss_limit", 0.015)
    halt_limit = cfg.get("daily_halt_limit", 0.025)
    weekly_limit = cfg.get("weekly_loss_limit", 0.04)
    drawdown_limit = cfg.get("drawdown_limit", 0.08)
    position_nav_limit = cfg.get("single_position_nav_limit", 0.03)

    daily_pnl = get_daily_pnl(portfolio_value)
    weekly_pnl = get_weekly_pnl(portfolio_value)
    peak = get_peak_value()
    drawdown = (portfolio_value - peak) / peak if peak > 0 else 0.0

    # 1. Daily loss > 1.5% → SIZE_DOWN_30
    if daily_pnl < -daily_limit:
        triggered.append({
            "level": "WARNING",
            "action": "SIZE_DOWN_30",
            "reason": f"Daily loss {daily_pnl:.2%} exceeds {daily_limit:.1%} limit",
            "threshold": -daily_limit,
            "current": round(daily_pnl, 4),
        })

    # 2. Daily loss > 2.5% → CLOSE_ALL_TODAY
    if daily_pnl < -halt_limit:
        triggered.append({
            "level": "CRITICAL",
            "action": "CLOSE_ALL_TODAY",
            "reason": f"Daily loss {daily_pnl:.2%} exceeds {halt_limit:.1%} halt limit",
            "threshold": -halt_limit,
            "current": round(daily_pnl, 4),
        })

    # 3. Weekly loss > 4% → SIZE_DOWN_30
    if weekly_pnl < -weekly_limit:
        triggered.append({
            "level": "WARNING",
            "action": "SIZE_DOWN_30",
            "reason": f"Weekly loss {weekly_pnl:.2%} exceeds {weekly_limit:.1%} limit",
            "threshold": -weekly_limit,
            "current": round(weekly_pnl, 4),
        })

    # 4. Drawdown > 8% → KILL_SWITCH
    if drawdown < -drawdown_limit:
        write_halt_lock(
            f"Drawdown {drawdown:.2%} exceeded {drawdown_limit:.1%} limit at {datetime.utcnow().isoformat()}"
        )
        triggered.append({
            "level": "CRITICAL",
            "action": "KILL_SWITCH",
            "reason": f"Drawdown {drawdown:.2%} exceeds {drawdown_limit:.1%} limit — SYSTEM HALTED",
            "threshold": -drawdown_limit,
            "current": round(drawdown, 4),
        })

    # 5. Single position unrealized loss > 3% NAV
    try:
        positions = get_positions()
        if not positions.empty and "unrealized_pnl" in positions.columns:
            for _, row in positions.iterrows():
                pnl = row.get("unrealized_pnl") or 0.0
                if portfolio_value > 0:
                    pnl_pct = pnl / portfolio_value
                else:
                    pnl_pct = 0.0
                if pnl_pct < -position_nav_limit:
                    ticker = row.get("ticker", "UNKNOWN")
                    triggered.append({
                        "level": "CRITICAL",
                        "action": f"FORCE_CLOSE_{ticker}",
                        "reason": (
                            f"{ticker} unrealized loss {pnl_pct:.2%} exceeds "
                            f"{position_nav_limit:.1%} single-position NAV limit"
                        ),
                        "threshold": -position_nav_limit,
                        "current": round(pnl_pct, 4),
                    })
    except Exception as e:
        log.warning(f"Could not check position-level circuit breakers: {e}")

    if triggered:
        for t in triggered:
            log.warning(f"CIRCUIT BREAKER: {t['action']} — {t['reason']}")
    return triggered
