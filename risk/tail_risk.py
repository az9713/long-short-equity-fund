from datetime import datetime, timedelta
from utils import get_db, get_logger, get_config
from data.providers import get_vix, get_credit_spread

log = get_logger(__name__)
cfg = get_config().get("risk", {})


def _init_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS credit_spread_history (
            date TEXT PRIMARY KEY,
            spread REAL NOT NULL
        )
    """)
    conn.commit()


def _store_credit_spread(spread: float):
    conn = get_db()
    try:
        _init_table(conn)
        today = datetime.utcnow().date().isoformat()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO credit_spread_history (date, spread) VALUES (?, ?)",
                (today, spread),
            )
    except Exception as e:
        log.warning(f"Failed to store credit spread: {e}")
    finally:
        conn.close()


def _get_credit_spread_history(days: int = 60) -> list[float]:
    conn = get_db()
    try:
        _init_table(conn)
        cutoff = (datetime.utcnow().date() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT spread FROM credit_spread_history WHERE date >= ? ORDER BY date",
            (cutoff,),
        ).fetchall()
        return [r["spread"] for r in rows]
    except Exception as e:
        log.warning(f"Failed to fetch credit spread history: {e}")
        return []
    finally:
        conn.close()


def _vix_regime(vix: float) -> str:
    if vix < 15:
        return "LOW"
    if vix < 25:
        return "NORMAL"
    if vix < 33:
        return "HIGH"
    return "EXTREME"


def check_tail_risk() -> dict:
    vix = get_vix()
    regime = _vix_regime(vix)

    vix_action = "OK"
    vix_msg = f"VIX={vix:.1f} ({regime})"

    vix_reduce = cfg.get("vix_reduce_threshold", 25)
    vix_halt = cfg.get("vix_halt_threshold", 33)

    if vix >= vix_halt:
        vix_action = "REDUCE_GROSS_50"
        vix_msg = f"VIX={vix:.1f} EXTREME — reduce gross 50%"
    elif vix >= vix_reduce:
        vix_action = "REDUCE_GROSS_20"
        vix_msg = f"VIX={vix:.1f} elevated — reduce gross 20%"

    # Credit spread
    cs = get_credit_spread()
    cs_zscore = None
    cs_action = "OK"

    if cs is not None:
        _store_credit_spread(cs)
        history = _get_credit_spread_history(days=60)
        if len(history) >= 5:
            import numpy as np
            arr = np.array(history[:-1])  # exclude today for z-score baseline
            mean = float(arr.mean())
            std = float(arr.std())
            if std > 0:
                cs_zscore = round((cs - mean) / std, 3)
                if cs_zscore >= 1.0:
                    cs_action = "REDUCE_GROSS_20"

    # Overall action: worst of vix_action and cs_action
    action_priority = {"OK": 0, "REDUCE_GROSS_20": 1, "REDUCE_GROSS_50": 2}
    final_action = vix_action
    if action_priority.get(cs_action, 0) > action_priority.get(vix_action, 0):
        final_action = cs_action

    parts = [vix_msg]
    if cs is not None and cs_zscore is not None and cs_zscore >= 1.0:
        parts.append(f"credit spread z={cs_zscore:.2f} — reduce gross 20%")

    return {
        "vix": round(vix, 2),
        "vix_regime": regime,
        "credit_spread": round(cs, 4) if cs is not None else None,
        "cs_zscore": cs_zscore,
        "action": final_action,
        "message": " | ".join(parts),
    }
