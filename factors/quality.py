import pandas as pd
import numpy as np
from utils import get_logger
from data.market_data import get_prices
from data.fundamentals import get_fundamentals
from factors.base import safe_divide, winsorize, sector_percentile_rank, ttm_sum

log = get_logger(__name__)


def _piotroski(fund_df: pd.DataFrame) -> tuple[int, str]:
    """Compute Piotroski F-score (0-9) and label."""
    if fund_df.empty or len(fund_df) < 5:
        return 0, "red"

    # Most recent and year-ago quarters (index is ascending period_end)
    recent = fund_df.iloc[-1]
    year_ago = fund_df.iloc[-5] if len(fund_df) >= 5 else fund_df.iloc[0]
    second_recent = fund_df.iloc[-2] if len(fund_df) >= 2 else recent
    second_year_ago = fund_df.iloc[-6] if len(fund_df) >= 6 else year_ago

    def v(row, col):
        val = row.get(col) if isinstance(row, dict) else (row[col] if col in row.index else None)
        return None if pd.isna(val) else float(val)

    score = 0

    # Profitability
    ni = v(recent, "ni")
    ta = v(recent, "total_assets")
    cfo = v(recent, "cfo")
    roa = safe_divide(ni, ta)
    if roa is not None and roa > 0:
        score += 1
    if cfo is not None and cfo > 0:
        score += 1

    ni_ya = v(year_ago, "ni")
    ta_ya = v(year_ago, "total_assets")
    roa_ya = safe_divide(ni_ya, ta_ya)
    if roa is not None and roa_ya is not None and roa > roa_ya:
        score += 1

    # CFO/TA > ROA (accruals negative = cash earnings)
    cfo_ta = safe_divide(cfo, ta)
    if cfo_ta is not None and roa is not None and cfo_ta > roa:
        score += 1

    # Leverage / Liquidity
    td = v(recent, "total_debt")
    te = v(recent, "total_equity")
    td_ya = v(year_ago, "total_debt")
    te_ya = v(year_ago, "total_equity")
    de = safe_divide(td, te)
    de_ya = safe_divide(td_ya, te_ya)
    if de is not None and de_ya is not None and de < de_ya:
        score += 1

    ca = v(recent, "current_assets")
    cl = v(recent, "current_liabilities")
    ca_ya = v(year_ago, "current_assets")
    cl_ya = v(year_ago, "current_liabilities")
    cr = safe_divide(ca, cl)
    cr_ya = safe_divide(ca_ya, cl_ya)
    if cr is not None and cr_ya is not None and cr > cr_ya:
        score += 1

    so = v(recent, "shares_outstanding")
    so_ya = v(year_ago, "shares_outstanding")
    if so is not None and so_ya is not None and so <= so_ya:
        score += 1

    # Efficiency
    gp = v(recent, "gross_profit")
    rev = v(recent, "revenue")
    gp_ya = v(year_ago, "gross_profit")
    rev_ya = v(year_ago, "revenue")
    gm = safe_divide(gp, rev)
    gm_ya = safe_divide(gp_ya, rev_ya)
    if gm is not None and gm_ya is not None and gm > gm_ya:
        score += 1

    at = v(recent, "asset_turnover")
    at_ya = v(year_ago, "asset_turnover")
    if at is not None and at_ya is not None and at > at_ya:
        score += 1

    if score >= 7:
        label = "green"
    elif score >= 3:
        label = "amber"
    else:
        label = "red"

    return score, label


def _altman_z(fund_df: pd.DataFrame, market_cap: float) -> tuple[float | None, str]:
    """Compute Altman Z-score and category label."""
    if fund_df.empty:
        return None, "unknown"

    recent = fund_df.iloc[-1]

    def v(col):
        val = recent[col] if col in recent.index else None
        return None if val is None or (isinstance(val, float) and np.isnan(val)) else float(val)

    wc = v("working_capital")
    ta = v("total_assets")
    re = v("retained_earnings")
    ebit = v("ebit")
    td = v("total_debt")
    revenue = v("revenue")

    if ta is None or ta == 0:
        return None, "unknown"

    wc = wc or 0.0
    re = re or 0.0
    ebit = ebit or 0.0
    td = td or 0.0
    revenue = revenue or 0.0

    z = (
        1.2 * safe_divide(wc, ta, 0)
        + 1.4 * safe_divide(re, ta, 0)
        + 3.3 * safe_divide(ebit, ta, 0)
        + 0.6 * safe_divide(market_cap, td, 10)  # cap ratio at 10 if no debt
        + 1.0 * safe_divide(revenue, ta, 0)
    )

    if z is None:
        return None, "unknown"

    if z > 2.99:
        label = "safe"
    elif z >= 1.81:
        label = "grey"
    else:
        label = "distress"

    return float(z), label


def compute_quality_raw(ticker: str, sector: str) -> dict:
    result = {
        "roe_stability": None,
        "gross_margin_level": None,
        "gross_margin_trend": None,
        "debt_equity_inv": None,
        "cfo_to_ni": None,
        "accruals_inv": None,
        "piotroski_f_score": None,
        "altman_z_score": None,
        "piotroski_f_raw": None,
        "altman_z_raw": None,
        "altman_label": "unknown",
        "_sector": sector,
        "_ticker": ticker,
    }

    fund_df = get_fundamentals(ticker, quarters=12)
    if fund_df.empty:
        return result

    # Market cap for Altman Z
    price_df = get_prices(ticker, days=5)
    price = None
    if not price_df.empty and "adj_close" in price_df.columns:
        price_series = price_df["adj_close"].dropna()
        if not price_series.empty:
            price = float(price_series.iloc[-1])

    shares = fund_df["shares_outstanding"].dropna()
    market_cap = (price * float(shares.iloc[-1])) if (price and not shares.empty) else 0.0

    # 1. ROE stability: std dev of quarterly ROE (lower = better, so invert)
    roe_vals = []
    for i in range(1, len(fund_df)):
        ni = fund_df["ni"].iloc[i]
        eq_cur = fund_df["total_equity"].iloc[i]
        eq_prev = fund_df["total_equity"].iloc[i - 1]
        if pd.notna(ni) and pd.notna(eq_cur) and pd.notna(eq_prev):
            avg_eq = (eq_cur + eq_prev) / 2
            if avg_eq != 0:
                roe_vals.append(ni / avg_eq)

    if len(roe_vals) >= 3:
        # Store negative std so higher = lower std = better
        result["roe_stability"] = -float(np.std(roe_vals))

    # 2. Gross margin level (most recent quarter)
    gp = fund_df["gross_profit"].dropna()
    rev = fund_df["revenue"].dropna()
    if not gp.empty and not rev.empty:
        gm = safe_divide(float(gp.iloc[-1]), float(rev.iloc[-1]))
        if gm is not None:
            result["gross_margin_level"] = gm

    # 3. Gross margin trend
    if len(fund_df) >= 2:
        gp_all = fund_df["gross_profit"].dropna()
        rev_all = fund_df["revenue"].dropna()
        common_idx = gp_all.index.intersection(rev_all.index)
        if len(common_idx) >= 2:
            gm_series = gp_all.loc[common_idx] / rev_all.loc[common_idx]
            result["gross_margin_trend"] = float(gm_series.iloc[-1] - gm_series.iloc[0])

    # 4. Debt/equity inverse (lower leverage = higher score)
    td_s = fund_df["total_debt"].dropna()
    te_s = fund_df["total_equity"].dropna()
    if not td_s.empty and not te_s.empty:
        de = safe_divide(float(td_s.iloc[-1]), float(te_s.iloc[-1]))
        if de is not None and de > 0:
            de = min(de, 10.0)  # cap at 10x
            result["debt_equity_inv"] = safe_divide(1.0, de)

    # 5. CFO to NI (most recent TTM)
    cfo_ttm = ttm_sum(fund_df, "cfo")
    ni_ttm = ttm_sum(fund_df, "ni")
    if cfo_ttm is not None and ni_ttm is not None and ni_ttm != 0:
        ratio = safe_divide(cfo_ttm, ni_ttm)
        # Below 0 (CFO < NI or negative) → score 0; handled by None at rank stage
        result["cfo_to_ni"] = ratio if (ratio is not None and ratio > 0) else None

    # 6. Accruals inverse: -1 * (NI - CFO) / TA
    ta_s = fund_df["total_assets"].dropna()
    if cfo_ttm is not None and ni_ttm is not None and not ta_s.empty:
        ta_val = float(ta_s.iloc[-1])
        if ta_val != 0:
            accruals = safe_divide(ni_ttm - cfo_ttm, ta_val)
            if accruals is not None:
                result["accruals_inv"] = -accruals  # inverted: low accruals = higher score

    # 7. Piotroski F-score
    f_score, f_label = _piotroski(fund_df)
    result["piotroski_f_raw"] = f_score
    # Scale 0-9 to 0-100
    result["piotroski_f_score"] = f_score / 9.0 * 100.0

    # 8. Altman Z-score
    z_val, z_label = _altman_z(fund_df, market_cap)
    result["altman_z_raw"] = z_val
    result["altman_label"] = z_label
    if z_label == "safe":
        result["altman_z_score"] = 90.0
    elif z_label == "grey":
        result["altman_z_score"] = 50.0
    elif z_label == "distress":
        result["altman_z_score"] = 10.0
    else:
        result["altman_z_score"] = None

    return result


def score_quality(raw_rows: list[dict]) -> pd.DataFrame:
    """Returns DataFrame with columns: quality (score), piotroski_f, altman_z, altman_label."""
    df = pd.DataFrame(raw_rows).set_index("_ticker")
    df["_sector"] = df["_sector"].fillna("Unknown")

    # Piotroski and Altman are pre-scored; don't go through percentile rank
    pre_scored = ["piotroski_f_score", "altman_z_score"]
    percentile_sfs = ["roe_stability", "gross_margin_level", "gross_margin_trend",
                      "debt_equity_inv", "cfo_to_ni", "accruals_inv"]

    ranked = pd.DataFrame(index=df.index)

    for sf in percentile_sfs:
        col = df[sf].copy().astype(float)
        valid = col.dropna()
        if not valid.empty:
            col = winsorize(valid).reindex(df.index)

        grp = pd.Series(dtype=float)
        for sector, group_idx in df.groupby("_sector").groups.items():
            sub = col.loc[group_idx]
            grp = pd.concat([grp, sector_percentile_rank(sub, higher_is_better=True)])

        ranked[sf] = grp.reindex(df.index).fillna(50.0)

    # Pre-scored subfactors: use as-is, fill None with 50
    for sf in pre_scored:
        ranked[sf] = df[sf].astype(float).fillna(50.0)

    all_sfs = percentile_sfs + pre_scored
    scores = ranked[all_sfs].mean(axis=1)
    scores.name = "quality"

    out = scores.to_frame()
    out["piotroski_f"] = df["piotroski_f_raw"]
    out["altman_z"] = df["altman_z_raw"]
    out["altman_label"] = df["altman_label"]
    return out
