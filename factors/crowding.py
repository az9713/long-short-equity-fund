import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from utils import get_db, get_logger

log = get_logger(__name__)

ROOT = Path(__file__).parent.parent

# Academic baseline pairwise correlations between factors
_BASELINES = {
    ("momentum", "value"): -0.3,
    ("momentum", "quality"): -0.1,
}

_MIN_HISTORY_DAYS = 60


def _create_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS factor_returns (
            date TEXT NOT NULL,
            factor_name TEXT NOT NULL,
            return_val REAL,
            PRIMARY KEY (date, factor_name)
        )
    """)
    conn.commit()


def store_factor_returns(date_str: str, factor_returns: dict[str, float]):
    """Store daily factor quintile return (top - bottom) for each factor."""
    conn = get_db()
    _create_table(conn)
    try:
        rows = [(date_str, factor, ret) for factor, ret in factor_returns.items()]
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO factor_returns (date, factor_name, return_val) VALUES (?,?,?)",
                rows,
            )
    except Exception as e:
        log.error(f"Failed to store factor returns: {e}")
    finally:
        conn.close()


def detect_crowding() -> dict:
    """
    Detect crowding by computing 60-day rolling pairwise correlations between
    factor return series. Returns empty dict if < 60 days of history.
    """
    conn = get_db()
    _create_table(conn)
    try:
        rows = conn.execute(
            "SELECT date, factor_name, return_val FROM factor_returns ORDER BY date"
        ).fetchall()
    except Exception as e:
        log.error(f"Failed to query factor_returns: {e}")
        return {}
    finally:
        conn.close()

    if not rows:
        return {}

    df = pd.DataFrame([dict(r) for r in rows])
    pivot = df.pivot(index="date", columns="factor_name", values="return_val")
    pivot = pivot.sort_index()

    if len(pivot) < _MIN_HISTORY_DAYS:
        log.info(f"Only {len(pivot)} days of factor return history; need {_MIN_HISTORY_DAYS} for crowding detection")
        return {}

    recent = pivot.tail(_MIN_HISTORY_DAYS)
    corr = recent.corr()
    factors = list(recent.columns)

    result = {}
    for i in range(len(factors)):
        for j in range(i + 1, len(factors)):
            f1, f2 = factors[i], factors[j]
            pair_key = (f1, f2)
            actual_corr = corr.loc[f1, f2] if f1 in corr.index and f2 in corr.columns else None
            if actual_corr is None or np.isnan(actual_corr):
                continue

            baseline = _BASELINES.get(pair_key) or _BASELINES.get((f2, f1))
            is_crowded = False
            warning = ""

            if baseline is not None:
                deviation = abs(actual_corr - baseline)
                is_crowded = deviation > 0.4
                if is_crowded:
                    warning = (
                        f"CROWDING ALERT: {f1}/{f2} correlation={actual_corr:.2f} "
                        f"vs baseline={baseline:.2f} (deviation={deviation:.2f})"
                    )
                    log.warning(warning)

            result[f"{f1}_{f2}"] = {
                "factor_1": f1,
                "factor_2": f2,
                "correlation": float(actual_corr),
                "baseline": baseline,
                "is_crowded": is_crowded,
                "warning_message": warning,
            }

    return result
