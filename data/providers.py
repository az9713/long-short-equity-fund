import os
import yfinance as yf
import requests
from utils import get_config, get_logger, is_dev_mode

log = get_logger(__name__)

cfg = get_config()


def get_price_provider() -> str:
    if os.getenv("POLYGON_API_KEY"):
        log.info("Using Polygon for prices")
        return "polygon"
    log.info("Using yfinance for prices")
    return "yfinance"


def get_vix() -> float:
    try:
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="1d")
        if hist.empty:
            log.warning("VIX history empty")
            return 20.0
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        log.error(f"Failed to fetch VIX: {e}")
        return 20.0


def get_credit_spread() -> float | None:
    fred_key = os.getenv("FRED_API_KEY")
    if not fred_key:
        log.info("No FRED_API_KEY, credit spread unavailable")
        return None
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": "BAMLH0A0HYM2",
            "api_key": fred_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1,
        }
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        obs = r.json().get("observations", [])
        if obs:
            return float(obs[0]["value"])
        return None
    except Exception as e:
        log.error(f"Failed to fetch credit spread from FRED: {e}")
        return None
