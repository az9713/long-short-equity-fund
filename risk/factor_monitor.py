import numpy as np
import pandas as pd
from utils import get_db, get_logger, get_config
from factors.crowding import detect_crowding

log = get_logger(__name__)
cfg = get_config().get("risk", {})

_FACTOR_NAMES = [
    "momentum", "value", "quality", "growth",
    "revisions", "short_interest", "insider", "institutional",
]
_LOOKBACK = 60


def _get_factor_spread_history() -> pd.DataFrame:
    # Reuse the factor_returns table that composite.py populates daily
    # Values there are already top-quintile minus bottom-quintile spreads
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT date, factor_name, return_val
            FROM factor_returns
            ORDER BY date DESC
            LIMIT ?
            """,
            (_LOOKBACK * len(_FACTOR_NAMES),),
        ).fetchall()
    except Exception as e:
        log.warning(f"Could not query factor_returns: {e}")
        return pd.DataFrame()
    finally:
        conn.close()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    pivot = df.pivot(index="date", columns="factor_name", values="return_val").sort_index()
    return pivot


def check_factor_monitor(scored_df: pd.DataFrame) -> list[dict]:
    if scored_df is None or scored_df.empty:
        return []

    zscore_alert = cfg.get("factor_zscore_alert", 1.5)

    # Normalize index
    if "ticker" in scored_df.columns:
        df = scored_df.set_index("ticker")
    else:
        df = scored_df

    # Get crowding info from crowding.py
    try:
        crowding = detect_crowding()
    except Exception as e:
        log.warning(f"detect_crowding failed: {e}")
        crowding = {}

    # Build set of crowded factors
    crowded_factors = set()
    for pair_key, info in crowding.items():
        if info.get("is_crowded"):
            crowded_factors.add(info.get("factor_1", ""))
            crowded_factors.add(info.get("factor_2", ""))

    # Get historical spread data from SQLite
    history = _get_factor_spread_history()

    alerts = []
    for factor in _FACTOR_NAMES:
        if factor not in df.columns:
            continue

        scores = df[factor].dropna().astype(float)
        if scores.empty:
            continue

        # Current day factor spread: top quintile mean - bottom quintile mean
        top_mask = scores >= 80
        bot_mask = scores <= 20
        top_mean = scores[top_mask].mean() if top_mask.any() else scores.quantile(0.8)
        bot_mean = scores[bot_mask].mean() if bot_mask.any() else scores.quantile(0.2)
        current_spread = float(top_mean - bot_mean)

        zscore = None
        if not history.empty and factor in history.columns:
            hist_series = history[factor].dropna()
            if len(hist_series) >= 5:
                mean = float(hist_series.mean())
                std = float(hist_series.std())
                if std > 0:
                    zscore = round((current_spread - mean) / std, 3)

        if zscore is None or abs(zscore) <= zscore_alert:
            continue

        is_crowded = factor in crowded_factors

        # Priority: HIGH if both factor stress + crowding, else MEDIUM
        priority = "HIGH" if is_crowded else "MEDIUM"

        direction = "above" if zscore > 0 else "below"
        alerts.append({
            "factor": factor,
            "zscore": zscore,
            "current_spread": round(current_spread, 3),
            "is_crowded": is_crowded,
            "priority": priority,
            "message": (
                f"{factor} spread z={zscore:.2f} ({direction} normal)"
                + (" + crowding detected" if is_crowded else "")
            ),
        })

    # Sort HIGH priority first
    alerts.sort(key=lambda x: (0 if x["priority"] == "HIGH" else 1, -abs(x["zscore"])))
    return alerts
