import numpy as np
import pandas as pd
from utils import get_logger
from data.market_data import get_prices

log = get_logger(__name__)

_ROLLING_DAYS = 60
_FETCH_DAYS = 80


def get_beta(ticker: str) -> float:
    try:
        stock_df = get_prices(ticker, days=_FETCH_DAYS)
        spy_df = get_prices("SPY", days=_FETCH_DAYS)

        if stock_df.empty or spy_df.empty:
            return 1.0

        stock_ret = stock_df["close"].pct_change().dropna()
        spy_ret = spy_df["close"].pct_change().dropna()

        # Align on common dates
        combined = pd.concat([stock_ret, spy_ret], axis=1, join="inner")
        combined.columns = ["stock", "spy"]
        combined = combined.dropna()

        if len(combined) < 20:
            return 1.0

        # Use last _ROLLING_DAYS rows after alignment
        window = combined.tail(_ROLLING_DAYS)
        cov_matrix = np.cov(window["stock"], window["spy"])
        var_spy = cov_matrix[1, 1]

        if var_spy == 0:
            return 1.0

        beta = cov_matrix[0, 1] / var_spy
        # Clip to reasonable range
        return float(np.clip(beta, -3.0, 3.0))
    except Exception as e:
        log.warning(f"get_beta failed for {ticker}: {e}")
        return 1.0


def get_portfolio_beta(
    longs: list[str], shorts: list[str], weights: dict
) -> dict:
    long_beta = 0.0
    short_beta = 0.0

    for ticker in longs:
        w = weights.get(ticker, 0.0)
        b = get_beta(ticker)
        long_beta += w * b

    for ticker in shorts:
        w = abs(weights.get(ticker, 0.0))
        b = get_beta(ticker)
        short_beta += w * b

    net_beta = long_beta - short_beta

    return {
        "long_beta": round(long_beta, 4),
        "short_beta": round(short_beta, 4),
        "net_beta": round(net_beta, 4),
    }
