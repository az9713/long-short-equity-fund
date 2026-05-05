import pandas as pd
import numpy as np
from utils import get_logger
from data.fundamentals import get_fundamentals
from factors.base import safe_divide, winsorize, sector_percentile_rank, ttm_sum, ttm_sum_offset

log = get_logger(__name__)

_GROWTH_CAP = 2.0  # +/- 200%


def _cap_growth(val: float | None) -> float | None:
    if val is None:
        return None
    return max(min(float(val), _GROWTH_CAP), -_GROWTH_CAP)


def compute_growth_raw(ticker: str, sector: str) -> dict:
    result = {
        "revenue_growth_yoy": None,
        "earnings_growth_yoy": None,
        "revenue_acceleration": None,
        "rd_intensity": None,
        "fcf_growth_yoy": None,
        "_sector": sector,
        "_ticker": ticker,
    }

    fund_df = get_fundamentals(ticker, quarters=12)
    if fund_df.empty:
        return result

    # TTM (most recent 4Q)
    rev_ttm = ttm_sum(fund_df, "revenue")
    ni_ttm = ttm_sum(fund_df, "ni")
    fcf_ttm = ttm_sum(fund_df, "fcf")
    rd_ttm = ttm_sum(fund_df, "r_and_d_expense")

    # TTM 1yr ago (4Q ending 4Q ago)
    rev_ttm_ya = ttm_sum_offset(fund_df, "revenue", offset_quarters=4)
    ni_ttm_ya = ttm_sum_offset(fund_df, "ni", offset_quarters=4)
    fcf_ttm_ya = ttm_sum_offset(fund_df, "fcf", offset_quarters=4)

    # 1. Revenue growth YoY
    if rev_ttm is not None and rev_ttm_ya is not None and rev_ttm_ya != 0:
        result["revenue_growth_yoy"] = safe_divide(
            rev_ttm - rev_ttm_ya, abs(rev_ttm_ya)
        )

    # 2. Earnings growth YoY (capped)
    if ni_ttm is not None and ni_ttm_ya is not None and ni_ttm_ya != 0:
        raw = safe_divide(ni_ttm - ni_ttm_ya, abs(ni_ttm_ya))
        result["earnings_growth_yoy"] = _cap_growth(raw)

    # 3. Revenue acceleration: latest YoY growth minus prior YoY growth
    # Prior: 4Q ending 4Q ago vs the 4Q before that (8Q ago)
    rev_ttm_2ya = ttm_sum_offset(fund_df, "revenue", offset_quarters=8)
    if (rev_ttm is not None and rev_ttm_ya is not None and rev_ttm_ya != 0
            and rev_ttm_2ya is not None and rev_ttm_2ya != 0):
        latest_yoy = safe_divide(rev_ttm - rev_ttm_ya, abs(rev_ttm_ya))
        prior_yoy = safe_divide(rev_ttm_ya - rev_ttm_2ya, abs(rev_ttm_2ya))
        if latest_yoy is not None and prior_yoy is not None:
            result["revenue_acceleration"] = latest_yoy - prior_yoy

    # 4. R&D intensity
    if rd_ttm is not None and rev_ttm is not None and rev_ttm != 0:
        rd_int = safe_divide(rd_ttm, rev_ttm)
        # 0 R&D gets None (will become 50 median at scoring time — non-R&D sectors)
        result["rd_intensity"] = rd_int if (rd_int is not None and rd_int > 0) else None

    # 5. FCF growth YoY (capped)
    if fcf_ttm is not None and fcf_ttm_ya is not None and fcf_ttm_ya != 0:
        raw = safe_divide(fcf_ttm - fcf_ttm_ya, abs(fcf_ttm_ya))
        result["fcf_growth_yoy"] = _cap_growth(raw)

    return result


def score_growth(raw_rows: list[dict]) -> pd.Series:
    df = pd.DataFrame(raw_rows).set_index("_ticker")
    df["_sector"] = df["_sector"].fillna("Unknown")

    subfactors = ["revenue_growth_yoy", "earnings_growth_yoy", "revenue_acceleration",
                  "rd_intensity", "fcf_growth_yoy"]

    ranked = pd.DataFrame(index=df.index)

    for sf in subfactors:
        col = df[sf].copy().astype(float)
        valid = col.dropna()
        if not valid.empty:
            col = winsorize(valid).reindex(df.index)

        grp = pd.Series(dtype=float)
        for sector, group_idx in df.groupby("_sector").groups.items():
            sub = col.loc[group_idx]
            grp = pd.concat([grp, sector_percentile_rank(sub, higher_is_better=True)])

        ranked[sf] = grp.reindex(df.index).fillna(50.0)

    scores = ranked[subfactors].mean(axis=1)
    scores.name = "growth"
    return scores
