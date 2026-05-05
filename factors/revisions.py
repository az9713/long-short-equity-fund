import pandas as pd
from utils import get_logger
from data.estimates import get_estimate_revisions
from factors.base import winsorize, sector_percentile_rank

log = get_logger(__name__)


def compute_revisions_raw(ticker: str, sector: str) -> dict:
    result = {
        "eps_revision_30d": None,
        "eps_revision_60d": None,
        "eps_revision_90d": None,
        "_sector": sector,
        "_ticker": ticker,
    }

    try:
        revisions = get_estimate_revisions(ticker)
        result["eps_revision_30d"] = revisions.get("delta_30d")
        result["eps_revision_60d"] = revisions.get("delta_60d")
        result["eps_revision_90d"] = revisions.get("delta_90d")
    except Exception as e:
        log.warning(f"Failed revisions for {ticker}: {e}")

    return result


def score_revisions(raw_rows: list[dict]) -> pd.Series:
    df = pd.DataFrame(raw_rows).set_index("_ticker")
    df["_sector"] = df["_sector"].fillna("Unknown")

    subfactors = ["eps_revision_30d", "eps_revision_60d", "eps_revision_90d"]

    ranked = pd.DataFrame(index=df.index)

    for sf in subfactors:
        col = df[sf].copy().astype(float)
        valid = col.dropna()

        # If all tickers have None for this delta window, set everyone to 50
        if valid.empty:
            ranked[sf] = 50.0
            continue

        col = winsorize(valid).reindex(df.index)

        grp = pd.Series(dtype=float)
        for sector, group_idx in df.groupby("_sector").groups.items():
            sub = col.loc[group_idx]
            grp = pd.concat([grp, sector_percentile_rank(sub, higher_is_better=True)])

        # None (< 30 days history) → 50 per spec
        ranked[sf] = grp.reindex(df.index).fillna(50.0)

    # Equal-weight only available subfactors per ticker
    scores = ranked[subfactors].mean(axis=1)
    scores.name = "revisions"
    return scores
