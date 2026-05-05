import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from utils import get_logger
from data.market_data import get_prices
from portfolio.state import get_positions

log = get_logger(__name__)

SECTOR_ETFS = {
    "Information Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
}


def _period_return(ticker: str, days: int) -> float | None:
    try:
        df = get_prices(ticker, days=days + 10)
        if df.empty or len(df) < 2:
            return None
        first = float(df["close"].iloc[0])
        last = float(df["close"].iloc[-1])
        if first == 0:
            return None
        return (last - first) / first
    except Exception as e:
        log.warning(f"_period_return failed for {ticker}: {e}")
        return None


def get_sector_relative_performance(days: int = 90) -> pd.DataFrame:
    positions = get_positions()
    if positions.empty:
        return pd.DataFrame()

    # Need sector column
    if "sector" not in positions.columns:
        return pd.DataFrame()

    sectors = positions["sector"].dropna().unique()
    rows = []

    for sector in sectors:
        sector_positions = positions[positions["sector"] == sector]
        n = len(sector_positions)

        # Portfolio return: average of individual ticker returns
        ticker_rets = []
        for _, pos in sector_positions.iterrows():
            ticker = pos.get("ticker", "")
            if not ticker:
                continue
            r = _period_return(ticker, days)
            if r is not None:
                side = pos.get("side", "LONG")
                # For shorts, positive portfolio return = price going down
                if side == "SHORT":
                    r = -r
                ticker_rets.append(r)

        if not ticker_rets:
            continue

        portfolio_return = float(np.mean(ticker_rets))

        # ETF return
        etf = SECTOR_ETFS.get(sector)
        etf_return = None
        if etf:
            etf_return = _period_return(etf, days)

        if etf_return is None:
            etf_return = 0.0

        alpha = portfolio_return - etf_return

        rows.append({
            "sector": sector,
            "portfolio_return": round(portfolio_return, 6),
            "etf_return": round(etf_return, 6),
            "alpha": round(alpha, 6),
            "n_positions": n,
        })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values("alpha", ascending=False).reset_index(drop=True)


def get_total_selection_alpha(days: int = 90) -> float:
    df = get_sector_relative_performance(days)
    if df.empty or "alpha" not in df.columns:
        return 0.0
    return round(float(df["alpha"].sum()), 6)
