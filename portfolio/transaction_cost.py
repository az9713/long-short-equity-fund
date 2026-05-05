import numpy as np
from utils import get_logger
from data.market_data import get_prices, get_adv

log = get_logger(__name__)


def estimate_cost_bps(ticker: str, trade_size_usd: float) -> float:
    df = get_prices(ticker, days=30)
    if df.empty or len(df) < 5:
        log.warning(f"Insufficient price data for cost estimate: {ticker}")
        return 10.0  # conservative default

    recent = df.tail(20)

    # Spread cost: 5% of average daily H-L range as fraction of close
    if "high" in recent.columns and "low" in recent.columns and "close" in recent.columns:
        hl_range = recent["high"] - recent["low"]
        spread_bps = 0.05 * (hl_range / recent["close"]).mean() * 10000
    else:
        spread_bps = 5.0

    # Market impact: 0.10 * (trade_size / adv) * daily_vol
    adv = get_adv(ticker)
    if adv > 0 and "close" in recent.columns and len(recent) >= 5:
        returns = recent["close"].pct_change().dropna()
        daily_vol_pct = float(returns.std()) if len(returns) >= 2 else 0.02
        impact_bps = 0.10 * (trade_size_usd / adv) * daily_vol_pct * 10000
    else:
        impact_bps = 5.0

    return float(spread_bps + impact_bps)


def cost_as_return(ticker: str, trade_size_usd: float) -> float:
    return estimate_cost_bps(ticker, trade_size_usd) / 10000
