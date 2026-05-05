import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from utils import get_db, get_logger, get_config

log = get_logger(__name__)
cfg = get_config()
reporting_cfg = cfg.get("reporting", {})

SHORT_TERM_RATE = reporting_cfg.get("short_term_tax_rate", 0.37)
LONG_TERM_RATE = reporting_cfg.get("long_term_tax_rate", 0.20)


def _get_trade_history(days: int) -> pd.DataFrame:
    conn = get_db()
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """
            SELECT ticker, action, shares, price, date
            FROM portfolio_history
            WHERE date >= ? AND action IN ('BUY','SELL','SHORT','COVER')
            ORDER BY date
            """,
            (cutoff,),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception as e:
        log.error(f"_get_trade_history failed: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def _get_nav_history(days: int) -> list[dict]:
    try:
        from risk.risk_state import load_risk_state
        state = load_risk_state()
        history = state.get("nav_history", [])
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [h for h in history if h.get("date", "") >= cutoff]
    except Exception as e:
        log.warning(f"_get_nav_history failed: {e}")
        return []


def _avg_nav(days: int) -> float:
    history = _get_nav_history(days)
    if not history:
        return 100_000.0
    vals = [h.get("value", 0) for h in history if h.get("value")]
    return float(np.mean(vals)) if vals else 100_000.0


def _sum_trades(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    df = df.copy()
    df["trade_value"] = df["shares"].astype(float) * df["price"].astype(float)
    return float(df["trade_value"].sum())


def _estimate_tax_drag(trades_df: pd.DataFrame, nav: float) -> float:
    """FIFO round-trip tax estimate. Short-term < 365d, long-term >= 365d."""
    if trades_df.empty or nav <= 0:
        return 0.0

    total_tax = 0.0

    open_actions = {"BUY": "LONG", "SHORT": "SHORT"}
    close_actions = {"SELL": "LONG", "COVER": "SHORT"}

    for ticker, grp in trades_df.groupby("ticker"):
        queue = []
        for _, row in grp.sort_values("date").iterrows():
            action = row.get("action", "")
            price = float(row.get("price") or 0)
            shares = float(row.get("shares") or 0)
            date_str = row.get("date", "")

            if action in open_actions:
                queue.append({
                    "entry_date": date_str,
                    "entry_price": price,
                    "shares": shares,
                    "side": open_actions[action],
                })
            elif action in close_actions:
                side = close_actions[action]
                remaining = shares
                while queue and remaining > 0:
                    entry = queue[0]
                    if entry["side"] != side:
                        queue.pop(0)
                        continue
                    matched = min(entry["shares"], remaining)
                    remaining -= matched
                    entry["shares"] -= matched

                    entry_price = entry["entry_price"]
                    if side == "LONG":
                        gain = (price - entry_price) * matched
                    else:
                        gain = (entry_price - price) * matched

                    if gain <= 0:
                        if entry["shares"] <= 0:
                            queue.pop(0)
                        continue

                    try:
                        entry_dt = pd.to_datetime(entry["entry_date"])
                        exit_dt = pd.to_datetime(date_str)
                        hold_days = (exit_dt - entry_dt).days
                    except Exception:
                        hold_days = 0

                    rate = SHORT_TERM_RATE if hold_days < 365 else LONG_TERM_RATE
                    total_tax += gain * rate

                    if entry["shares"] <= 0:
                        queue.pop(0)

    return round(total_tax / nav, 6) if nav > 0 else 0.0


def get_turnover_stats() -> dict:
    df_30 = _get_trade_history(30)
    df_90 = _get_trade_history(90)

    nav_30 = _avg_nav(30)
    nav_90 = _avg_nav(90)

    sum_30 = _sum_trades(df_30)
    sum_90 = _sum_trades(df_90)

    turnover_30d = round(sum_30 / nav_30, 4) if nav_30 > 0 else 0.0
    turnover_90d = round(sum_90 / nav_90, 4) if nav_90 > 0 else 0.0

    # Annualize from 90d: multiply by (252/90)
    turnover_annualized = round(turnover_90d * (252 / 90), 4) if turnover_90d else 0.0

    # Tax drag from all FIFO round-trips in 90d history
    est_tax_drag_pct = _estimate_tax_drag(df_90, nav_90)

    return {
        "turnover_30d": turnover_30d,
        "turnover_90d": turnover_90d,
        "turnover_annualized": turnover_annualized,
        "est_tax_drag_pct": est_tax_drag_pct,
    }
