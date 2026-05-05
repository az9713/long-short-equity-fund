import pandas as pd
from datetime import datetime, timedelta
from utils import get_logger, get_db
from data.short_interest import get_short_interest
from factors.base import winsorize, sector_percentile_rank

log = get_logger(__name__)


def _get_si_30d_ago(ticker: str) -> float | None:
    """Fetch short_percent_float from ~30 days ago for change calculation."""
    conn = get_db()
    try:
        cutoff = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
        row = conn.execute(
            """
            SELECT short_percent_float FROM short_interest
            WHERE ticker=? AND date <= ?
            ORDER BY date DESC LIMIT 1
            """,
            (ticker, cutoff),
        ).fetchone()
        return float(row["short_percent_float"]) if row and row["short_percent_float"] is not None else None
    except Exception as e:
        log.warning(f"Failed SI history for {ticker}: {e}")
        return None
    finally:
        conn.close()


def compute_si_raw(ticker: str, sector: str) -> dict:
    result = {
        "short_pct_float": None,
        "days_to_cover": None,
        "si_change": None,
        "_sector": sector,
        "_ticker": ticker,
    }

    si = get_short_interest(ticker)
    if not si:
        return result

    spf = si.get("short_percent_float")
    sr = si.get("short_ratio")

    if spf is not None:
        result["short_pct_float"] = float(spf)

    if sr is not None:
        result["days_to_cover"] = float(sr)

    # Change vs 30d ago
    if spf is not None:
        prior = _get_si_30d_ago(ticker)
        if prior is not None:
            result["si_change"] = float(spf) - prior

    return result


def score_short_interest(raw_rows: list[dict]) -> pd.Series:
    """
    Score short interest for LONG candidates: lower SI = higher score.
    All three subfactors are inverted (higher_is_better=False means high SI is bad).
    Composite.py handles flipping for SHORT candidates.
    """
    df = pd.DataFrame(raw_rows).set_index("_ticker")
    df["_sector"] = df["_sector"].fillna("Unknown")

    # For longs: lower SI = better, so rank with higher_is_better=False (invert)
    subfactors = {
        "short_pct_float": False,   # lower pct float = better for longs
        "days_to_cover": False,     # lower DTC = better for longs
        "si_change": False,         # declining SI = better for longs
    }

    ranked = pd.DataFrame(index=df.index)

    for sf, hib in subfactors.items():
        col = df[sf].copy().astype(float)
        valid = col.dropna()
        if not valid.empty:
            col = winsorize(valid).reindex(df.index)

        grp = pd.Series(dtype=float)
        for sector, group_idx in df.groupby("_sector").groups.items():
            sub = col.loc[group_idx]
            grp = pd.concat([grp, sector_percentile_rank(sub, higher_is_better=hib)])

        ranked[sf] = grp.reindex(df.index).fillna(50.0)

    scores = ranked[list(subfactors.keys())].mean(axis=1)
    scores.name = "short_interest"
    return scores
