import pandas as pd
import numpy as np
from utils import get_db, get_logger

log = get_logger(__name__)

# Minimum sector group size for percentile ranking; smaller groups fall back to 50
MIN_GROUP_SIZE = 3


def sector_percentile_rank(values: pd.Series, higher_is_better: bool = True) -> pd.Series:
    # values is a Series within a groupby context (already one sector)
    n = len(values)
    if n < MIN_GROUP_SIZE:
        return pd.Series(50.0, index=values.index)
    if higher_is_better:
        ranks = values.rank(method="average", na_option="keep", ascending=True)
    else:
        ranks = values.rank(method="average", na_option="keep", ascending=False)
    # Map [1, n] → [0, 100]
    pct = (ranks - 1) / (n - 1) * 100
    # Fill NaN (missing values) with 50 (sector median)
    return pct.fillna(50.0)


def safe_divide(a, b, default=None):
    try:
        if b is None or b == 0 or (isinstance(b, float) and np.isnan(b)):
            return default
        if a is None or (isinstance(a, float) and np.isnan(a)):
            return default
        return a / b
    except Exception:
        return default


def winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    lo = series.quantile(lower)
    hi = series.quantile(upper)
    return series.clip(lower=lo, upper=hi)


# yfinance sector names differ from GICS strings used in SECTOR_ETF and Wikipedia universe data
_YF_TO_GICS = {
    "Technology": "Information Technology",
    "Financial Services": "Financials",
    "Healthcare": "Health Care",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Basic Materials": "Materials",
    "Communication Services": "Communication Services",
    "Energy": "Energy",
    "Industrials": "Industrials",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}


def _normalize_sector(name: str | None) -> str:
    if not name:
        return "Unknown"
    return _YF_TO_GICS.get(name, name)


def get_sector_map(tickers: list[str]) -> dict[str, str]:
    """Return {ticker: GICS sector} for given tickers. Queries DB first; falls back to yfinance."""
    conn = get_db()
    try:
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, sector FROM universe WHERE ticker IN ({placeholders}) AND is_benchmark=0",
            tickers,
        ).fetchall()
        # DB stores Wikipedia GICS strings; normalize in case of inconsistency
        result = {r["ticker"]: _normalize_sector(r["sector"]) for r in rows if r["sector"]}
    except Exception as e:
        log.warning(f"sector_map DB query failed: {e}")
        result = {}
    finally:
        conn.close()

    missing = [t for t in tickers if t not in result]
    if missing:
        log.info(f"Fetching sectors from yfinance for {len(missing)} tickers: {missing}")
        try:
            import yfinance as yf
            for t in missing:
                try:
                    info = yf.Ticker(t).info
                    raw_sector = info.get("sector") or info.get("sectorDisp")
                    result[t] = _normalize_sector(raw_sector)
                except Exception as e:
                    log.warning(f"Could not get sector for {t}: {e}")
                    result[t] = "Unknown"
        except ImportError:
            for t in missing:
                result[t] = "Unknown"

    return result


def ttm_sum(df: pd.DataFrame, col: str) -> float | None:
    """Sum of last 4 quarters for flow items. Returns None if no valid data."""
    if df.empty or col not in df.columns:
        return None
    vals = df[col].dropna().tail(4)
    if vals.empty:
        return None
    return float(vals.sum())


def ttm_sum_offset(df: pd.DataFrame, col: str, offset_quarters: int = 4) -> float | None:
    """TTM sum starting offset_quarters ago (for year-ago comparisons)."""
    if df.empty or col not in df.columns:
        return None
    all_vals = df[col].dropna()
    if len(all_vals) < offset_quarters + 4:
        return None
    vals = all_vals.iloc[-(offset_quarters + 4):-offset_quarters]
    return float(vals.sum())
