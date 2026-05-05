import pandas as pd
from utils import get_logger
from data.institutional import get_institutional_summary
from factors.base import winsorize, sector_percentile_rank

log = get_logger(__name__)


def compute_institutional_raw(ticker: str, sector: str) -> dict:
    result = {
        "funds_holding_count": None,
        "aggregate_change": None,
        "new_entry_flag": None,
        "_sector": sector,
        "_ticker": ticker,
    }

    try:
        summary = get_institutional_summary(ticker)
    except Exception as e:
        log.warning(f"Failed institutional for {ticker}: {e}")
        return result

    if not summary:
        return result

    funds = summary.get("funds_holding", 0)
    result["funds_holding_count"] = float(funds) if funds is not None else None

    change = summary.get("change_vs_prior")
    result["aggregate_change"] = float(change) if change is not None else None

    # Pre-map new_entry_flag to 0-100
    flag = summary.get("new_entry_flag", False)
    result["new_entry_flag"] = 90.0 if flag else 50.0

    return result


def score_institutional(raw_rows: list[dict]) -> pd.Series:
    df = pd.DataFrame(raw_rows).set_index("_ticker")
    df["_sector"] = df["_sector"].fillna("Unknown")

    pct_subfactors = ["funds_holding_count", "aggregate_change"]
    pre_scored = ["new_entry_flag"]

    ranked = pd.DataFrame(index=df.index)

    for sf in pct_subfactors:
        col = df[sf].copy().astype(float)
        valid = col.dropna()
        if not valid.empty:
            col = winsorize(valid).reindex(df.index)

        grp = pd.Series(dtype=float)
        for sector, group_idx in df.groupby("_sector").groups.items():
            sub = col.loc[group_idx]
            grp = pd.concat([grp, sector_percentile_rank(sub, higher_is_better=True)])

        ranked[sf] = grp.reindex(df.index).fillna(50.0)

    for sf in pre_scored:
        ranked[sf] = df[sf].astype(float).fillna(50.0)

    all_sfs = pct_subfactors + pre_scored
    scores = ranked[all_sfs].mean(axis=1)
    scores.name = "institutional"
    return scores
