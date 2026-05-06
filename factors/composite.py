import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils import get_logger
from data.universe import get_universe
from factors.base import get_sector_map, sector_percentile_rank
from factors.regime_weights import get_weights
from factors.crowding import detect_crowding, store_factor_returns

import factors.momentum as _mom
import factors.value as _val
import factors.quality as _qual
import factors.growth as _grow
import factors.revisions as _rev
import factors.short_interest as _si
import factors.insider as _ins
import factors.institutional as _inst

log = get_logger(__name__)

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output"

FACTOR_NAMES = ["momentum", "value", "quality", "growth", "revisions",
                "short_interest", "insider", "institutional"]


def _compute_ticker(ticker: str, sector: str) -> dict:
    """Compute all raw factor metrics for a single ticker. Safe — never raises."""
    try:
        mom = _mom.compute_momentum_raw(ticker, sector)
    except Exception as e:
        log.warning(f"momentum raw failed for {ticker}: {e}")
        mom = {"_ticker": ticker, "_sector": sector}

    try:
        val = _val.compute_value_raw(ticker, sector)
    except Exception as e:
        log.warning(f"value raw failed for {ticker}: {e}")
        val = {"_ticker": ticker, "_sector": sector}

    try:
        qual = _qual.compute_quality_raw(ticker, sector)
    except Exception as e:
        log.warning(f"quality raw failed for {ticker}: {e}")
        qual = {"_ticker": ticker, "_sector": sector}

    try:
        grow = _grow.compute_growth_raw(ticker, sector)
    except Exception as e:
        log.warning(f"growth raw failed for {ticker}: {e}")
        grow = {"_ticker": ticker, "_sector": sector}

    try:
        rev = _rev.compute_revisions_raw(ticker, sector)
    except Exception as e:
        log.warning(f"revisions raw failed for {ticker}: {e}")
        rev = {"_ticker": ticker, "_sector": sector}

    try:
        si = _si.compute_si_raw(ticker, sector)
    except Exception as e:
        log.warning(f"short_interest raw failed for {ticker}: {e}")
        si = {"_ticker": ticker, "_sector": sector}

    try:
        ins = _ins.compute_insider_raw(ticker, sector)
    except Exception as e:
        log.warning(f"insider raw failed for {ticker}: {e}")
        ins = {"_ticker": ticker, "_sector": sector}

    try:
        inst = _inst.compute_institutional_raw(ticker, sector)
    except Exception as e:
        log.warning(f"institutional raw failed for {ticker}: {e}")
        inst = {"_ticker": ticker, "_sector": sector}

    return {
        "mom": mom,
        "val": val,
        "qual": qual,
        "grow": grow,
        "rev": rev,
        "si": si,
        "ins": ins,
        "inst": inst,
    }


def run_scoring(tickers: list[str] = None) -> pd.DataFrame:
    if tickers is None:
        tickers = get_universe()

    if not tickers:
        log.error("No tickers to score")
        return pd.DataFrame()

    log.info(f"Scoring {len(tickers)} tickers...")

    sector_map = get_sector_map(tickers)
    for t in tickers:
        if t not in sector_map:
            sector_map[t] = "Unknown"

    # Parallel raw metric collection
    raw_by_factor = {
        "mom": [], "val": [], "qual": [], "grow": [],
        "rev": [], "si": [], "ins": [], "inst": [],
    }

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_compute_ticker, t, sector_map.get(t, "Unknown")): t
            for t in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                raw = future.result()
                for key in raw_by_factor:
                    raw_by_factor[key].append(raw[key])
            except Exception as e:
                log.error(f"Compute failed for {ticker}: {e}")
                # Fill with empty dicts for failed tickers
                sector = sector_map.get(ticker, "Unknown")
                for key in raw_by_factor:
                    raw_by_factor[key].append({"_ticker": ticker, "_sector": sector})

    log.info("Raw metrics collected. Computing factor scores...")

    # Score each factor (sector-relative ranking happens inside each score_ function)
    mom_scores = _mom.score_momentum(raw_by_factor["mom"], sector_map)
    val_scores = _val.score_value(raw_by_factor["val"])
    qual_out = _qual.score_quality(raw_by_factor["qual"])
    grow_scores = _grow.score_growth(raw_by_factor["grow"])
    rev_scores = _rev.score_revisions(raw_by_factor["rev"])
    si_scores = _si.score_short_interest(raw_by_factor["si"])
    ins_scores = _ins.score_insider(raw_by_factor["ins"])
    inst_scores = _inst.score_institutional(raw_by_factor["inst"])

    # Assemble master DataFrame
    result = pd.DataFrame(index=tickers)
    result.index.name = "ticker"
    result["sector"] = [sector_map.get(t, "Unknown") for t in tickers]

    result["momentum"] = mom_scores.reindex(tickers).fillna(50.0)
    result["value"] = val_scores.reindex(tickers).fillna(50.0)

    qual_aligned = qual_out.reindex(tickers)
    result["quality"] = qual_aligned["quality"].fillna(50.0)
    result["piotroski_f"] = qual_aligned["piotroski_f"]
    result["altman_z"] = qual_aligned["altman_z"]
    result["altman_label"] = qual_aligned["altman_label"].fillna("unknown")

    result["growth"] = grow_scores.reindex(tickers).fillna(50.0)
    result["revisions"] = rev_scores.reindex(tickers).fillna(50.0)
    result["short_interest"] = si_scores.reindex(tickers).fillna(50.0)
    result["insider"] = ins_scores.reindex(tickers).fillna(50.0)
    result["institutional"] = inst_scores.reindex(tickers).fillna(50.0)

    # Apply factor weights
    weights = get_weights()
    log.info(f"Applying weights: {weights}")

    # Validate weights sum to ~1
    total_w = sum(weights.values())
    if abs(total_w - 1.0) > 0.05:
        log.warning(f"Weights sum to {total_w:.3f} (expected 1.0)")

    composite = pd.Series(0.0, index=tickers)
    for factor, w in weights.items():
        if factor in result.columns:
            composite += result[factor] * w

    result["composite"] = composite.round(2)

    # Generate signals: top quintile LONG, bottom quintile SHORT.
    # Use rank-based quintiles so signals scale with universe size — absolute
    # 80/20 thresholds never fire on small universes (e.g. dev mode).
    result["signal"] = "NEUTRAL"
    n = len(result)
    if n >= 5:
        comp_pct = result["composite"].rank(pct=True) * 100
        result.loc[comp_pct >= 80, "signal"] = "LONG"
        result.loc[comp_pct <= 20, "signal"] = "SHORT"

    log.info(
        f"Signals: {(result['signal']=='LONG').sum()} LONG, "
        f"{(result['signal']=='SHORT').sum()} SHORT, "
        f"{(result['signal']=='NEUTRAL').sum()} NEUTRAL"
    )

    # Store daily factor quintile returns for crowding detection
    _store_factor_quintile_returns(result)

    # Save outputs
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.utcnow().strftime("%Y%m%d")
    latest_path = OUTPUT_DIR / "scored_universe_latest.csv"
    dated_path = OUTPUT_DIR / f"scored_universe_{today_str}.csv"

    col_order = [
        "sector", "momentum", "value", "quality", "growth",
        "revisions", "short_interest", "insider", "institutional",
        "composite", "signal", "piotroski_f", "altman_z", "altman_label",
    ]
    out_df = result.reset_index()[["ticker"] + col_order]
    out_df.to_csv(latest_path, index=False)
    out_df.to_csv(dated_path, index=False)
    log.info(f"Saved scored universe to {latest_path}")

    return result


def _store_factor_quintile_returns(result: pd.DataFrame):
    """
    Compute proxy daily factor return as top-quintile mean composite minus
    bottom-quintile mean composite per factor. Store in SQLite.
    """
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        factor_rets = {}
        for factor in FACTOR_NAMES:
            if factor not in result.columns:
                continue
            scores = result[factor].astype(float).dropna()
            top = scores[scores >= 80].mean()
            bottom = scores[scores <= 20].mean()
            if not pd.isna(top) and not pd.isna(bottom) and bottom != 0:
                factor_rets[factor] = float(top - bottom)

        if factor_rets:
            store_factor_returns(today_str, factor_rets)
    except Exception as e:
        log.warning(f"Failed to store factor quintile returns: {e}")
