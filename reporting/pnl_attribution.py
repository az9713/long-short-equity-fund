import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from utils import get_db, get_logger
from data.market_data import get_prices

log = get_logger(__name__)

# Sector ETF map for Brinson attribution
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


def _init_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_attribution (
            date TEXT PRIMARY KEY,
            beta_return REAL,
            sector_return REAL,
            factor_return REAL,
            alpha_residual REAL,
            total_return REAL
        )
    """)
    conn.commit()


def _get_spy_return_today() -> float:
    try:
        df = get_prices("SPY", days=5)
        if df.empty or len(df) < 2:
            return 0.0
        rets = df["close"].pct_change().dropna()
        return float(rets.iloc[-1])
    except Exception as e:
        log.warning(f"SPY return fetch failed: {e}")
        return 0.0


def _get_factor_returns_today() -> dict:
    conn = get_db()
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT factor_name, return_val FROM factor_returns WHERE date=?",
            (today,),
        ).fetchall()
        return {r["factor_name"]: float(r["return_val"]) for r in rows}
    except Exception as e:
        log.warning(f"factor_returns query failed: {e}")
        return {}
    finally:
        conn.close()


def _get_sector_return(sector: str) -> float:
    etf = SECTOR_ETFS.get(sector)
    if not etf:
        return 0.0
    try:
        df = get_prices(etf, days=5)
        if df.empty or len(df) < 2:
            return 0.0
        rets = df["close"].pct_change().dropna()
        return float(rets.iloc[-1])
    except Exception:
        return 0.0


def _compute_brinson_sector(
    positions_df: pd.DataFrame,
    portfolio_value: float,
) -> float:
    if positions_df.empty or portfolio_value <= 0:
        return 0.0

    total = 0.0
    for _, row in positions_df.iterrows():
        sector = row.get("sector", "Unknown")
        shares = float(row.get("shares", 0) or 0)
        price = float(row.get("current_price") or row.get("entry_price", 0) or 0)
        mv = shares * price
        port_weight = mv / portfolio_value if portfolio_value else 0.0

        # Benchmark weight is equal-weight across sectors (1/len(SECTOR_ETFS))
        bench_weight = 1.0 / len(SECTOR_ETFS)
        sector_ret = _get_sector_return(sector)
        side = row.get("side", "LONG")
        signed_weight = port_weight if side == "LONG" else -port_weight

        total += (signed_weight - bench_weight) * sector_ret

    return total


def compute_daily_attribution(
    portfolio_value: float,
    prev_portfolio_value: float,
) -> dict:
    if prev_portfolio_value <= 0:
        return {
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
            "beta_return": 0.0,
            "sector_return": 0.0,
            "factor_return": 0.0,
            "alpha_residual": 0.0,
            "total_return": 0.0,
        }

    total_return = (portfolio_value - prev_portfolio_value) / prev_portfolio_value

    spy_ret = _get_spy_return_today()

    # Beta attribution — compute net_beta directly from current positions
    net_beta = 0.0
    positions_df = pd.DataFrame()
    try:
        from portfolio.state import get_positions
        from portfolio.beta import get_portfolio_beta
        positions_df = get_positions()
        if not positions_df.empty and "side" in positions_df.columns:
            longs = positions_df[positions_df["side"] == "LONG"]["ticker"].tolist()
            shorts = positions_df[positions_df["side"] == "SHORT"]["ticker"].tolist()
            weights = {}
            for _, row in positions_df.iterrows():
                t = row.get("ticker", "")
                shares = float(row.get("shares", 0) or 0)
                price = float(row.get("current_price") or row.get("entry_price", 0) or 0)
                mv = shares * price
                w = mv / portfolio_value if portfolio_value else 0.0
                weights[t] = w if row.get("side") == "LONG" else -w
            beta_dict = get_portfolio_beta(longs, shorts, {t: abs(w) for t, w in weights.items()})
            net_beta = float(beta_dict.get("net_beta", 0.0))
    except Exception as e:
        log.warning(f"Beta computation failed: {e}")
    beta_return = net_beta * spy_ret

    # Sector attribution (Brinson)
    sector_return = 0.0
    try:
        if positions_df.empty:
            from portfolio.state import get_positions
            positions_df = get_positions()
        sector_return = _compute_brinson_sector(positions_df, portfolio_value)
    except Exception as e:
        log.warning(f"Brinson sector attribution failed: {e}")

    # Factor attribution — dot-product of portfolio factor exposures * today's factor returns
    # Exposure proxy: (long_avg_score - short_avg_score) / 100, normalized to unit scale
    factor_return = 0.0
    try:
        factor_rets = _get_factor_returns_today()
        if factor_rets and not positions_df.empty:
            from portfolio.factor_exposure import get_factor_exposures, FACTOR_NAMES

            # Load scored_df if available to get factor scores; fall back to empty
            scored_df = pd.DataFrame()
            try:
                from pathlib import Path as _Path
                csv = _Path(__file__).parent.parent / "output" / "scored_universe_latest.csv"
                if csv.exists():
                    scored_df = pd.read_csv(csv)
            except Exception:
                pass

            if not scored_df.empty and "side" in positions_df.columns:
                longs_f = positions_df[positions_df["side"] == "LONG"]["ticker"].tolist()
                shorts_f = positions_df[positions_df["side"] == "SHORT"]["ticker"].tolist()
                exposures = get_factor_exposures(longs_f, shorts_f, scored_df)
                for factor, stats in exposures.items():
                    if factor in factor_rets:
                        # Spread normalized to [-1, 1]: divide by 100 (max possible spread)
                        spread_norm = stats["spread"] / 100.0
                        factor_return += spread_norm * factor_rets[factor]
            elif factor_rets:
                # No scored_df available — use simple mean of factor returns as proxy
                factor_return = float(np.mean(list(factor_rets.values())))
    except Exception as e:
        log.warning(f"Factor return attribution failed: {e}")

    alpha_residual = total_return - beta_return - sector_return - factor_return

    result = {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "beta_return": round(beta_return, 6),
        "sector_return": round(sector_return, 6),
        "factor_return": round(factor_return, 6),
        "alpha_residual": round(alpha_residual, 6),
        "total_return": round(total_return, 6),
    }

    # Persist to DB
    try:
        conn = get_db()
        _init_tables(conn)
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_attribution
                (date, beta_return, sector_return, factor_return, alpha_residual, total_return)
                VALUES (:date, :beta_return, :sector_return, :factor_return, :alpha_residual, :total_return)
                """,
                result,
            )
        conn.close()
    except Exception as e:
        log.error(f"Failed to persist daily_attribution: {e}")

    return result


def get_attribution_history(days: int = 90) -> pd.DataFrame:
    conn = get_db()
    try:
        _init_tables(conn)
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """
            SELECT date, beta_return, sector_return, factor_return, alpha_residual, total_return
            FROM daily_attribution
            WHERE date >= ?
            ORDER BY date
            """,
            (cutoff,),
        ).fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception as e:
        log.error(f"get_attribution_history failed: {e}")
        return pd.DataFrame()
    finally:
        conn.close()
