import pandas as pd
import numpy as np
from utils import get_logger
from data.market_data import get_prices
from factors.base import safe_divide, winsorize, sector_percentile_rank

log = get_logger(__name__)

SECTOR_ETF = {
    "Information Technology": "XLK",
    "Financials": "XLF",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Health Care": "XLV",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}

# Number of trading days fetched; enough for 252 + buffer
_PRICE_DAYS = 280


def _safe_ret(prices: pd.Series, i_start: int, i_end: int) -> float | None:
    """Return price return between two integer iloc positions. Returns None if out of range."""
    try:
        if len(prices) <= abs(i_start) or len(prices) <= abs(i_end):
            return None
        p_start = prices.iloc[i_start]
        p_end = prices.iloc[i_end]
        if p_start is None or p_end is None or p_start == 0:
            return None
        return float(p_end / p_start - 1)
    except Exception:
        return None


def compute_momentum_raw(ticker: str, sector: str) -> dict:
    """Compute raw momentum subfactor values for a single ticker. Returns dict with keys matching subfactors."""
    result = {
        "ret_12_1": None,
        "ret_6_1": None,
        "ret_3_1": None,
        "acceleration": None,
        "high_52w_proximity": None,
        "rs_vs_sector": None,
        "_sector": sector,
        "_ticker": ticker,
    }

    df = get_prices(ticker, days=_PRICE_DAYS)
    if df.empty or "adj_close" not in df.columns:
        log.warning(f"No price data for momentum: {ticker}")
        return result

    prices = df["adj_close"].dropna()
    n = len(prices)

    # Need at least 252 bars for 12m signals
    if n >= 252:
        result["ret_12_1"] = _safe_ret(prices, -252, -21)
        result["high_52w_proximity"] = safe_divide(
            float(prices.iloc[-1]),
            float(prices.iloc[-252:].max()),
        )

    if n >= 126:
        result["ret_6_1"] = _safe_ret(prices, -126, -21)

    if n >= 63:
        result["ret_3_1"] = _safe_ret(prices, -63, -21)
        recent_3m = _safe_ret(prices, -63, -21)
        prior_3m = _safe_ret(prices, -126, -63)
        if recent_3m is not None and prior_3m is not None:
            result["acceleration"] = recent_3m - prior_3m

    # rs_vs_sector: stock 3m return minus sector ETF 3m return
    etf = SECTOR_ETF.get(sector)
    if etf and n >= 63:
        stock_3m = _safe_ret(prices, -63, -21)
        if stock_3m is not None:
            etf_df = get_prices(etf, days=_PRICE_DAYS)
            if not etf_df.empty and "adj_close" in etf_df.columns:
                etf_prices = etf_df["adj_close"].dropna()
                etf_3m = _safe_ret(etf_prices, -63, -21)
                if etf_3m is not None:
                    result["rs_vs_sector"] = stock_3m - etf_3m

    return result


def score_momentum(raw_rows: list[dict], sector_map: dict[str, str]) -> pd.Series:
    """
    Given a list of raw dicts from compute_momentum_raw, return a Series of
    final momentum scores indexed by ticker.
    """
    df = pd.DataFrame(raw_rows).set_index("_ticker")
    df["_sector"] = df["_sector"].fillna("Unknown")

    subfactors = ["ret_12_1", "ret_6_1", "ret_3_1", "acceleration", "high_52w_proximity", "rs_vs_sector"]

    ranked = pd.DataFrame(index=df.index)

    for sf in subfactors:
        col = df[sf].copy().astype(float)
        col = winsorize(col.dropna()).reindex(df.index)
        # Rank within sector; higher is better for all momentum subfactors
        grp = pd.Series(dtype=float)
        for sector, group_idx in df.groupby("_sector").groups.items():
            sub = col.loc[group_idx]
            grp = pd.concat([grp, sector_percentile_rank(sub, higher_is_better=True)])
        # Fill missing (None original values) with 50
        grp = grp.reindex(df.index).fillna(50.0)
        ranked[sf] = grp

    # Equal-weight subfactors
    scores = ranked[subfactors].mean(axis=1)
    scores.name = "momentum"
    return scores
