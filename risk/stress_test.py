import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path
from utils import ROOT, get_logger
from portfolio.state import get_positions

log = get_logger(__name__)

CACHE_PATH = ROOT / "cache" / "stress_cache.parquet"
CACHE_TTL_DAYS = 7

# Historical stress scenarios: (name, start, end)
HISTORICAL_SCENARIOS = [
    ("2008 Financial Crisis", "2008-09-15", "2009-03-09"),
    ("2020 Covid Crash", "2020-02-19", "2020-04-23"),
    ("2022 Rate Hikes", "2022-01-03", "2022-10-13"),
]


def _cache_fresh() -> bool:
    if not CACHE_PATH.exists():
        return False
    mtime = datetime.fromtimestamp(CACHE_PATH.stat().st_mtime)
    return (datetime.utcnow() - mtime).days < CACHE_TTL_DAYS


def _fetch_scenario_returns(tickers: list[str]) -> pd.DataFrame:
    # Fetch price data covering all historical scenario windows
    # Use 2006-01-01 to 2023-01-01 to cover all three scenarios
    overall_start = "2006-01-01"
    overall_end = "2023-12-31"
    try:
        log.info(f"Fetching stress scenario returns for {len(tickers)} tickers...")
        if len(tickers) == 1:
            df = yf.download(tickers[0], start=overall_start, end=overall_end,
                             auto_adjust=True, progress=False, threads=False)
            if not df.empty:
                df.columns = pd.MultiIndex.from_tuples([(c, tickers[0]) for c in df.columns])
        else:
            df = yf.download(tickers, start=overall_start, end=overall_end,
                             auto_adjust=True, progress=False, threads=False)
        return df
    except Exception as e:
        log.error(f"Failed to download stress scenario data: {e}")
        return pd.DataFrame()


def _load_or_refresh_cache(tickers: list[str]) -> pd.DataFrame:
    if _cache_fresh():
        try:
            df = pd.read_parquet(CACHE_PATH)
            # Check if all tickers are present
            if all(t in df.columns for t in tickers):
                return df
        except Exception as e:
            log.warning(f"Cache read failed: {e}")

    # Fetch and rebuild cache
    raw = _fetch_scenario_returns(tickers)
    if raw.empty:
        return pd.DataFrame()

    # Extract Close prices — handle multi-ticker MultiIndex
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw["Adj Close"]
        else:
            close = raw[["Close"]] if "Close" in raw.columns else raw[["Adj Close"]]
            close.columns = tickers[:1]
    except Exception as e:
        log.warning(f"Could not extract close prices from cache data: {e}")
        return pd.DataFrame()

    # Compute cumulative period returns for each scenario
    # Store daily returns indexed by date
    daily_returns = close.pct_change().dropna()

    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        daily_returns.to_parquet(CACHE_PATH)
        log.info(f"Stress cache written: {CACHE_PATH}")
    except Exception as e:
        log.warning(f"Failed to write stress cache: {e}")

    return daily_returns


def _scenario_return(daily_returns: pd.DataFrame, start: str, end: str, ticker: str) -> float:
    # Compound return over scenario window
    try:
        mask = (daily_returns.index >= start) & (daily_returns.index <= end)
        period = daily_returns.loc[mask]
        if period.empty or ticker not in period.columns:
            return None
        col = period[ticker].dropna()
        if col.empty:
            return None
        # Compound: prod(1+r) - 1
        return float((1 + col).prod() - 1)
    except Exception:
        return None


def _sector_avg_return(daily_returns: pd.DataFrame, start: str, end: str,
                       sector: str, positions_df: pd.DataFrame) -> float:
    # Get average return of tickers in the same sector
    sector_tickers = []
    if not positions_df.empty and "sector" in positions_df.columns:
        sector_tickers = positions_df[
            positions_df["sector"] == sector
        ]["ticker"].tolist()

    returns = []
    for t in sector_tickers:
        r = _scenario_return(daily_returns, start, end, t)
        if r is not None:
            returns.append(r)
    return float(np.mean(returns)) if returns else -0.15  # -15% fallback


def run_stress_tests(weights: dict) -> list[dict]:
    if not weights:
        log.info("No weights provided for stress test")
        return []

    # Get current positions for sector info
    try:
        positions_df = get_positions()
    except Exception:
        positions_df = pd.DataFrame()

    tickers = [t for t in weights.keys() if weights[t] != 0]
    if not tickers:
        return []

    # Load or refresh return cache
    daily_returns = _load_or_refresh_cache(tickers)

    results = []

    # --- Historical scenarios ---
    for scenario_name, start, end in HISTORICAL_SCENARIOS:
        long_pnl = 0.0
        short_pnl = 0.0
        contributors = []

        for ticker, w in weights.items():
            if w == 0:
                continue

            r = _scenario_return(daily_returns, start, end, ticker)

            if r is None:
                # Use sector average
                sector = "Unknown"
                if not positions_df.empty and "sector" in positions_df.columns:
                    row = positions_df[positions_df["ticker"] == ticker]
                    if not row.empty:
                        sector = row.iloc[0].get("sector", "Unknown")
                r = _sector_avg_return(daily_returns, start, end, sector, positions_df)
                log.debug(f"{scenario_name}: {ticker} using sector avg {r:.2%}")

            # PnL for this position: weight * scenario return
            # Long: positive r is good; Short: negative r is good
            if w > 0:  # LONG
                pos_pnl = w * r
                long_pnl += pos_pnl
            else:  # SHORT
                pos_pnl = w * r  # w is negative, r in crisis is negative → positive
                short_pnl += pos_pnl

            contributors.append((ticker, round(w * r, 4)))

        total_pnl = long_pnl + short_pnl
        # Worst contributors (most negative contribution)
        contributors.sort(key=lambda x: x[1])
        worst = [{"ticker": t, "contribution": c} for t, c in contributors[:3]]

        results.append({
            "scenario_name": scenario_name,
            "long_pnl_pct": round(long_pnl, 4),
            "short_pnl_pct": round(short_pnl, 4),
            "total_pnl_pct": round(total_pnl, 4),
            "worst_contributors": worst,
        })

    # --- Synthetic scenarios ---

    # 4. Sector Shock: -30% to most concentrated sector
    sector_weights = {}
    for ticker, w in weights.items():
        if w == 0:
            continue
        sector = "Unknown"
        if not positions_df.empty and "sector" in positions_df.columns:
            row = positions_df[positions_df["ticker"] == ticker]
            if not row.empty:
                sector = row.iloc[0].get("sector", "Unknown")
        sector_weights[sector] = sector_weights.get(sector, 0.0) + abs(w)

    target_sector = max(sector_weights, key=sector_weights.get) if sector_weights else None
    sector_shock_pnl = 0.0
    sector_contributors = []
    for ticker, w in weights.items():
        if w == 0:
            continue
        sector = "Unknown"
        if not positions_df.empty and "sector" in positions_df.columns:
            row = positions_df[positions_df["ticker"] == ticker]
            if not row.empty:
                sector = row.iloc[0].get("sector", "Unknown")
        shock_r = -0.30 if sector == target_sector else 0.0
        contrib = w * shock_r
        sector_shock_pnl += contrib
        if shock_r != 0:
            sector_contributors.append((ticker, round(contrib, 4)))
    sector_contributors.sort(key=lambda x: x[1])
    results.append({
        "scenario_name": f"Sector Shock ({target_sector})",
        "long_pnl_pct": round(sum(w * (-0.30 if _get_ticker_sector(t, positions_df) == target_sector else 0)
                                  for t, w in weights.items() if w > 0), 4),
        "short_pnl_pct": round(sum(w * (-0.30 if _get_ticker_sector(t, positions_df) == target_sector else 0)
                                   for t, w in weights.items() if w < 0), 4),
        "total_pnl_pct": round(sector_shock_pnl, 4),
        "worst_contributors": [{"ticker": t, "contribution": c} for t, c in sector_contributors[:3]],
    })

    # 5. Momentum Reversal: top quintile -30%, bottom quintile +20%
    # Identify top/bottom quintile by weight magnitude
    all_weights = [(t, w) for t, w in weights.items() if w != 0]
    if all_weights:
        sorted_by_abs = sorted(all_weights, key=lambda x: abs(x[1]), reverse=True)
        n = len(sorted_by_abs)
        top_q = {t for t, _ in sorted_by_abs[:max(1, n // 5)]}
        bot_q = {t for t, _ in sorted_by_abs[max(1, n - n // 5):]}
    else:
        top_q, bot_q = set(), set()

    mom_rev_pnl = 0.0
    mom_contributors = []
    for ticker, w in weights.items():
        if w == 0:
            continue
        if ticker in top_q:
            r = -0.30
        elif ticker in bot_q:
            r = 0.20
        else:
            r = 0.0
        contrib = w * r
        mom_rev_pnl += contrib
        if r != 0:
            mom_contributors.append((ticker, round(contrib, 4)))
    mom_contributors.sort(key=lambda x: x[1])
    results.append({
        "scenario_name": "Momentum Reversal",
        "long_pnl_pct": round(sum(w * (-0.30 if t in top_q else 0.20 if t in bot_q else 0)
                                  for t, w in weights.items() if w > 0), 4),
        "short_pnl_pct": round(sum(w * (-0.30 if t in top_q else 0.20 if t in bot_q else 0)
                                   for t, w in weights.items() if w < 0), 4),
        "total_pnl_pct": round(mom_rev_pnl, 4),
        "worst_contributors": [{"ticker": t, "contribution": c} for t, c in mom_contributors[:3]],
    })

    # 6. Short Squeeze: all shorts +30%
    sq_pnl = 0.0
    sq_contributors = []
    for ticker, w in weights.items():
        if w < 0:  # short position
            contrib = w * 0.30  # w negative * positive return = loss
            sq_pnl += contrib
            sq_contributors.append((ticker, round(contrib, 4)))
    sq_contributors.sort(key=lambda x: x[1])
    results.append({
        "scenario_name": "Short Squeeze",
        "long_pnl_pct": 0.0,
        "short_pnl_pct": round(sq_pnl, 4),
        "total_pnl_pct": round(sq_pnl, 4),
        "worst_contributors": [{"ticker": t, "contribution": c} for t, c in sq_contributors[:3]],
    })

    return results


def _get_ticker_sector(ticker: str, positions_df: pd.DataFrame) -> str:
    if positions_df.empty or "sector" not in positions_df.columns:
        return "Unknown"
    row = positions_df[positions_df["ticker"] == ticker]
    if row.empty:
        return "Unknown"
    return row.iloc[0].get("sector", "Unknown")
