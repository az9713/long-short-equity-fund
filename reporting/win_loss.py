import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from utils import get_db, get_logger
from data.providers import get_vix

log = get_logger(__name__)


def _get_closed_trades() -> pd.DataFrame:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT ticker, action, shares, price, date
            FROM portfolio_history
            WHERE action IN ('BUY','SHORT','SELL','COVER')
            ORDER BY ticker, date
            """
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception as e:
        log.error(f"_get_closed_trades failed: {e}")
        return pd.DataFrame()
    finally:
        conn.close()


def _pair_trades(df: pd.DataFrame) -> list[dict]:
    """FIFO pairing of open/close trades into round trips."""
    if df.empty:
        return []

    pairs = []
    open_actions = {"BUY": "LONG", "SHORT": "SHORT"}
    close_actions = {"SELL": "LONG", "COVER": "SHORT"}

    for ticker, grp in df.groupby("ticker"):
        queue = []  # FIFO: list of (entry_date, entry_price, shares, side)
        for _, row in grp.sort_values("date").iterrows():
            action = row["action"]
            price = float(row.get("price") or 0)
            shares = float(row.get("shares") or 0)
            date_str = row.get("date", "")

            if action in open_actions:
                side = open_actions[action]
                queue.append({
                    "entry_date": date_str,
                    "entry_price": price,
                    "shares": shares,
                    "side": side,
                })
            elif action in close_actions:
                side = close_actions[action]
                # Match against FIFO entries of same side
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
                    exit_price = price

                    if side == "LONG":
                        ret_pct = (exit_price - entry_price) / entry_price if entry_price else 0.0
                    else:
                        ret_pct = (entry_price - exit_price) / entry_price if entry_price else 0.0

                    # Holding period in days
                    try:
                        entry_dt = pd.to_datetime(entry["entry_date"])
                        exit_dt = pd.to_datetime(date_str)
                        hold_days = max(0, (exit_dt - entry_dt).days)
                    except Exception:
                        hold_days = 0

                    pairs.append({
                        "ticker": ticker,
                        "side": side,
                        "entry_date": entry["entry_date"],
                        "exit_date": date_str,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "shares": matched,
                        "return_pct": round(ret_pct, 6),
                        "hold_days": hold_days,
                    })

                    if entry["shares"] <= 0:
                        queue.pop(0)

    return pairs


def _holding_period_bin(days: int) -> str:
    if days <= 5:
        return "1-5d"
    if days <= 20:
        return "5-20d"
    if days <= 60:
        return "20-60d"
    return "60+d"


def _vix_regime(vix: float) -> str:
    if vix < 15:
        return "LOW"
    if vix < 25:
        return "NORMAL"
    if vix < 33:
        return "HIGH"
    return "EXTREME"


def _current_streak(returns: list[float]) -> int:
    """Positive = win streak, negative = loss streak."""
    if not returns:
        return 0
    streak = 1 if returns[-1] > 0 else -1
    direction = streak
    for r in reversed(returns[:-1]):
        if (r > 0 and direction > 0) or (r <= 0 and direction < 0):
            streak += direction
        else:
            break
    return streak


def get_win_loss_stats() -> dict:
    df = _get_closed_trades()
    if df.empty:
        return {
            "win_rate": 0.0,
            "pl_ratio": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "current_streak": 0,
            "total_trades": 0,
        }

    pairs = _pair_trades(df)
    if not pairs:
        return {
            "win_rate": 0.0,
            "pl_ratio": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "current_streak": 0,
            "total_trades": 0,
        }

    rets = [p["return_pct"] for p in pairs]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    win_rate = len(wins) / len(rets) if rets else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    pl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

    streak = _current_streak(rets)

    return {
        "win_rate": round(win_rate, 4),
        "pl_ratio": round(pl_ratio, 4),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "current_streak": streak,
        "total_trades": len(rets),
    }


def get_win_loss_by_side() -> pd.DataFrame:
    df = _get_closed_trades()
    if df.empty:
        return pd.DataFrame()

    pairs = _pair_trades(df)
    if not pairs:
        return pd.DataFrame()

    rows = []
    for side in ["LONG", "SHORT"]:
        side_pairs = [p for p in pairs if p["side"] == side]
        if not side_pairs:
            continue
        rets = [p["return_pct"] for p in side_pairs]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        rows.append({
            "side": side,
            "trades": len(rets),
            "win_rate": round(len(wins) / len(rets), 4) if rets else 0.0,
            "avg_return": round(float(np.mean(rets)), 4) if rets else 0.0,
            "avg_win": round(float(np.mean(wins)), 4) if wins else 0.0,
            "avg_loss": round(float(np.mean(losses)), 4) if losses else 0.0,
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def get_win_loss_by_holding_period() -> pd.DataFrame:
    df = _get_closed_trades()
    if df.empty:
        return pd.DataFrame()

    pairs = _pair_trades(df)
    if not pairs:
        return pd.DataFrame()

    rows = []
    for bin_label in ["1-5d", "5-20d", "20-60d", "60+d"]:
        bin_pairs = [p for p in pairs if _holding_period_bin(p["hold_days"]) == bin_label]
        if not bin_pairs:
            continue
        rets = [p["return_pct"] for p in bin_pairs]
        wins = [r for r in rets if r > 0]
        rows.append({
            "period": bin_label,
            "trades": len(rets),
            "win_rate": round(len(wins) / len(rets), 4) if rets else 0.0,
            "avg_return": round(float(np.mean(rets)), 4) if rets else 0.0,
        })

    return pd.DataFrame(rows) if rows else pd.DataFrame()
