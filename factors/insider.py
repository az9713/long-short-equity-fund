import pandas as pd
from datetime import datetime, timedelta
from utils import get_logger
from data.sec_data import get_insider_transactions
from factors.base import winsorize, sector_percentile_rank

log = get_logger(__name__)

CEO_CFO_TITLES = {"ceo", "chief executive", "cfo", "chief financial"}


def _is_ceo_cfo(title: str | None) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(k in t for k in CEO_CFO_TITLES)


def _dollar_flow(row) -> float:
    try:
        shares = float(row["shares"]) if pd.notna(row["shares"]) else 0.0
        price = float(row["price"]) if pd.notna(row["price"]) else 0.0
        return shares * price
    except Exception:
        return 0.0


def compute_insider_raw(ticker: str, sector: str) -> dict:
    result = {
        "net_dollar_flow": None,
        "ceo_cfo_weight": None,
        "cluster_buy_flag": None,
        "_sector": sector,
        "_ticker": ticker,
    }

    txns = get_insider_transactions(ticker, days=90)
    if txns.empty:
        return result

    # Only open-market purchases (P) and sales (S)
    txns = txns[txns["transaction_code"].isin(["P", "S"])].copy()
    if txns.empty:
        return result

    purchases = txns[txns["transaction_code"] == "P"]
    sales = txns[txns["transaction_code"] == "S"]

    buy_flow = sum(_dollar_flow(r) for _, r in purchases.iterrows())
    sell_flow = sum(_dollar_flow(r) for _, r in sales.iterrows())
    result["net_dollar_flow"] = buy_flow - sell_flow

    # CEO/CFO weighted flow (3x weight for executives)
    weighted_flow = 0.0
    for _, r in txns.iterrows():
        flow = _dollar_flow(r)
        direction = 1 if r["transaction_code"] == "P" else -1
        multiplier = 3 if _is_ceo_cfo(r.get("insider_title")) else 1
        weighted_flow += direction * flow * multiplier
    result["ceo_cfo_weight"] = weighted_flow

    # Cluster buy flag: count is_cluster_buy events in last 30 days
    cutoff_30d = (datetime.utcnow() - timedelta(days=30)).date().isoformat()
    cluster_txns = txns[
        (txns["transaction_code"] == "P") &
        (txns["transaction_date"] >= cutoff_30d) &
        (txns["is_cluster_buy"] == 1)
    ]
    cluster_count = int(cluster_txns["is_cluster_buy"].sum()) if not cluster_txns.empty else 0

    # Pre-map to 0-100 per spec
    if cluster_count >= 2:
        result["cluster_buy_flag"] = 90.0
    elif cluster_count == 1:
        result["cluster_buy_flag"] = 70.0
    else:
        result["cluster_buy_flag"] = 50.0

    return result


def score_insider(raw_rows: list[dict]) -> pd.Series:
    df = pd.DataFrame(raw_rows).set_index("_ticker")
    df["_sector"] = df["_sector"].fillna("Unknown")

    pct_subfactors = ["net_dollar_flow", "ceo_cfo_weight"]
    pre_scored = ["cluster_buy_flag"]

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

        # No insider data → 50 (sector median)
        ranked[sf] = grp.reindex(df.index).fillna(50.0)

    for sf in pre_scored:
        ranked[sf] = df[sf].astype(float).fillna(50.0)

    all_sfs = pct_subfactors + pre_scored
    scores = ranked[all_sfs].mean(axis=1)
    scores.name = "insider"
    return scores
