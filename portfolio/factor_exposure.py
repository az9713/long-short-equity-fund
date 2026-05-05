import numpy as np
import pandas as pd
from utils import get_logger

log = get_logger(__name__)

FACTOR_NAMES = [
    "momentum", "value", "quality", "growth",
    "revisions", "short_interest", "insider", "institutional",
]


def get_factor_exposures(
    longs: list[str], shorts: list[str], scored_df: pd.DataFrame
) -> dict:
    # Normalize index so we can lookup by ticker
    if scored_df.index.name == "ticker":
        df = scored_df
    elif "ticker" in scored_df.columns:
        df = scored_df.set_index("ticker")
    else:
        df = scored_df

    result = {}
    for factor in FACTOR_NAMES:
        if factor not in df.columns:
            continue

        long_scores = [
            float(df.at[t, factor])
            for t in longs
            if t in df.index and pd.notna(df.at[t, factor])
        ]
        short_scores = [
            float(df.at[t, factor])
            for t in shorts
            if t in df.index and pd.notna(df.at[t, factor])
        ]

        long_avg = float(np.mean(long_scores)) if long_scores else 50.0
        short_avg = float(np.mean(short_scores)) if short_scores else 50.0
        spread = long_avg - short_avg

        result[factor] = {
            "long_avg": round(long_avg, 2),
            "short_avg": round(short_avg, 2),
            "spread": round(spread, 2),
        }

    return result


def check_factor_spread_alert(
    exposures: dict, history_df: pd.DataFrame = None
) -> list[str]:
    if history_df is None or history_df.empty:
        return []

    warnings = []
    for factor, stats in exposures.items():
        if factor not in history_df.columns:
            continue
        hist = history_df[factor].dropna()
        if len(hist) < 2:
            continue
        mean = hist.mean()
        std = hist.std()
        if std == 0:
            continue
        spread = stats["spread"]
        z = (spread - mean) / std
        if abs(z) > 1.0:
            direction = "high" if z > 0 else "low"
            warnings.append(
                f"WARN: {factor} spread={spread:.1f} is {abs(z):.1f} std devs {direction} of historical mean ({mean:.1f})"
            )

    return warnings
