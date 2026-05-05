import pandas as pd
import numpy as np
from utils import get_logger, get_db
from data.market_data import get_prices
from data.fundamentals import get_fundamentals
from factors.base import safe_divide, winsorize, sector_percentile_rank, ttm_sum

log = get_logger(__name__)


def _get_forward_eps(ticker: str) -> float | None:
    """Fetch cached forward EPS from analyst_estimates table."""
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT forward_eps FROM analyst_estimates
            WHERE ticker=? AND forward_eps IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (ticker,),
        ).fetchone()
        return float(row["forward_eps"]) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def compute_value_raw(ticker: str, sector: str) -> dict:
    result = {
        "fwd_pe_inv": None,
        "fcf_yield": None,
        "ev_ebitda_inv": None,
        "shareholder_yield": None,
        "sales_to_ev": None,
        "book_to_price": None,
        "_sector": sector,
        "_ticker": ticker,
    }

    price_df = get_prices(ticker, days=5)
    if price_df.empty or "adj_close" not in price_df.columns:
        return result
    price = float(price_df["adj_close"].dropna().iloc[-1])

    fund_df = get_fundamentals(ticker, quarters=8)
    if fund_df.empty:
        return result

    # Balance sheet: most recent quarter is last row (ascending sort)
    shares = fund_df["shares_outstanding"].dropna()
    if shares.empty:
        return result
    shares_latest = float(shares.iloc[-1])

    market_cap = price * shares_latest
    if market_cap <= 0:
        return result

    # TTM flow items
    revenue_ttm = ttm_sum(fund_df, "revenue")
    fcf_ttm = ttm_sum(fund_df, "fcf")
    ebitda_ttm = ttm_sum(fund_df, "ebitda")
    dividends_ttm = ttm_sum(fund_df, "dividends_paid")
    buybacks_ttm = ttm_sum(fund_df, "buybacks")

    # Most recent balance sheet values
    total_debt = fund_df["total_debt"].dropna()
    total_debt_val = float(total_debt.iloc[-1]) if not total_debt.empty else 0.0

    total_equity = fund_df["total_equity"].dropna()
    total_equity_val = float(total_equity.iloc[-1]) if not total_equity.empty else None

    # Cash approximation: working_capital proxy or assume 0 if unavailable
    # Use current_assets - current_liabilities as working capital proxy for cash
    current_assets = fund_df["current_assets"].dropna()
    current_liabilities = fund_df["current_liabilities"].dropna()
    if not current_assets.empty and not current_liabilities.empty:
        cash_approx = max(float(current_assets.iloc[-1]) - float(current_liabilities.iloc[-1]), 0)
    else:
        cash_approx = 0.0

    ev = market_cap + total_debt_val - cash_approx

    # 1. Forward P/E inverse (earnings yield)
    fwd_eps = _get_forward_eps(ticker)
    if fwd_eps is not None and fwd_eps > 0:
        fwd_pe = safe_divide(price, fwd_eps)
        if fwd_pe is not None and fwd_pe > 0:
            result["fwd_pe_inv"] = safe_divide(1.0, fwd_pe)
        # Negative EPS → leave as None (will be scored 50 not 0, per task: "score 0")
        # Task says P/E negative → score 0 → we keep None and handle in scoring
    elif fwd_eps is not None and fwd_eps <= 0:
        result["fwd_pe_inv"] = None  # loss-making; fill to 0 at rank stage

    # 2. FCF yield
    if fcf_ttm is not None:
        result["fcf_yield"] = safe_divide(fcf_ttm, market_cap)

    # 3. EV/EBITDA inverse
    if ebitda_ttm is not None and ebitda_ttm > 0 and ev > 0:
        ev_ebitda = safe_divide(ev, ebitda_ttm)
        if ev_ebitda is not None and ev_ebitda > 0:
            result["ev_ebitda_inv"] = safe_divide(1.0, ev_ebitda)

    # 4. Shareholder yield
    div = dividends_ttm or 0.0
    buy = buybacks_ttm or 0.0
    total_return = div + buy
    if total_return >= 0:
        result["shareholder_yield"] = safe_divide(total_return, market_cap)

    # 5. Sales-to-EV
    if revenue_ttm is not None and ev > 0:
        result["sales_to_ev"] = safe_divide(revenue_ttm, ev)

    # 6. Book-to-price
    if total_equity_val is not None and total_equity_val > 0:
        result["book_to_price"] = safe_divide(total_equity_val, market_cap)

    return result


def score_value(raw_rows: list[dict]) -> pd.Series:
    df = pd.DataFrame(raw_rows).set_index("_ticker")
    df["_sector"] = df["_sector"].fillna("Unknown")

    subfactors = ["fwd_pe_inv", "fcf_yield", "ev_ebitda_inv", "shareholder_yield", "sales_to_ev", "book_to_price"]

    ranked = pd.DataFrame(index=df.index)

    for sf in subfactors:
        col = df[sf].copy().astype(float)
        # Winsorize on valid values only
        valid = col.dropna()
        if not valid.empty:
            col = winsorize(valid).reindex(df.index)

        grp = pd.Series(dtype=float)
        for sector, group_idx in df.groupby("_sector").groups.items():
            sub = col.loc[group_idx]
            grp = pd.concat([grp, sector_percentile_rank(sub, higher_is_better=True)])

        # None values (e.g., negative P/E per spec) become score 0, not 50
        if sf == "fwd_pe_inv":
            orig_none = df[sf].isna()
            grp = grp.reindex(df.index)
            # Check if ticker had fwd_eps <= 0 (negative EPS → score 0)
            grp[orig_none] = 0.0
        else:
            grp = grp.reindex(df.index).fillna(50.0)

        ranked[sf] = grp

    scores = ranked[subfactors].mean(axis=1)
    scores.name = "value"
    return scores
