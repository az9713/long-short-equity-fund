import numpy as np
import pandas as pd
from utils import get_logger, get_config
from data.market_data import get_adv
from data.earnings_calendar import days_to_earnings
from portfolio.beta import get_beta
from portfolio.transaction_cost import estimate_cost_bps

log = get_logger(__name__)

_GROSS_TARGET = 0.75  # each side sums to this
_EARNINGS_WINDOW = 5  # halve size if earnings within this many days


def optimize_conviction(
    candidates_df: pd.DataFrame,
    portfolio_value: float = 100_000,
    config: dict = None,
) -> dict[str, float]:
    if config is None:
        config = get_config().get("portfolio", {})

    max_pos = config.get("max_position_pct", 0.05)
    max_sector = config.get("max_sector_pct", 0.25)
    max_beta = config.get("max_beta", 0.15)

    # Normalize index
    if "ticker" in candidates_df.columns:
        df = candidates_df.set_index("ticker")
    else:
        df = candidates_df.copy()

    longs_df = df[df["signal"] == "LONG"].copy()
    shorts_df = df[df["signal"] == "SHORT"].copy()

    if longs_df.empty and shorts_df.empty:
        log.warning("No LONG or SHORT candidates — returning empty weights")
        return {}
    if longs_df.empty:
        log.warning("No LONG candidates")
    if shorts_df.empty:
        log.warning("No SHORT candidates")

    def _build_weights(side_df: pd.DataFrame) -> pd.Series:
        if side_df.empty:
            return pd.Series(dtype=float)

        score_col = "combined_score" if "combined_score" in side_df.columns else "composite"
        scores = side_df[score_col].fillna(50.0)

        # Equal-weight base
        weights = pd.Series(1.0, index=side_df.index)

        # Conviction tilt by score percentile within this side
        pct = scores.rank(pct=True)
        weights[pct >= 0.95] = 1.5
        weights[(pct >= 0.90) & (pct < 0.95)] = 1.25

        return weights

    long_weights = _build_weights(longs_df)
    short_weights = _build_weights(shorts_df)

    # Apply liquidity constraint: cap at 5% of 20-day ADV
    def _apply_liquidity(weights: pd.Series) -> pd.Series:
        for ticker in weights.index:
            adv = get_adv(ticker)
            if adv <= 0:
                continue
            max_trade_usd = 0.05 * adv
            target_usd = weights[ticker] / weights.sum() * _GROSS_TARGET * portfolio_value
            if target_usd > max_trade_usd:
                weights[ticker] *= max_trade_usd / target_usd
        return weights

    long_weights = _apply_liquidity(long_weights)
    short_weights = _apply_liquidity(short_weights)

    # Apply earnings constraint: halve size if earnings within _EARNINGS_WINDOW days
    def _apply_earnings(weights: pd.Series) -> pd.Series:
        for ticker in weights.index:
            dte = days_to_earnings(ticker)
            if dte is not None and 0 <= dte <= _EARNINGS_WINDOW:
                weights[ticker] *= 0.5
        return weights

    long_weights = _apply_earnings(long_weights)
    short_weights = _apply_earnings(short_weights)

    # Apply sector cap: single-side sector <= max_sector_pct of gross
    def _apply_sector_cap(weights: pd.Series, side_df: pd.DataFrame) -> pd.Series:
        if weights.empty or "sector" not in side_df.columns:
            return weights
        total = weights.sum()
        if total == 0:
            return weights
        # Normalize temporarily to check sector fractions
        normed = weights / total * _GROSS_TARGET
        sectors = side_df.loc[weights.index, "sector"] if "sector" in side_df.columns else pd.Series("Unknown", index=weights.index)
        # Two-pass: cap and re-normalize once
        for sector in sectors.unique():
            sec_tickers = sectors[sectors == sector].index
            sec_weight = normed[sec_tickers].sum()
            if sec_weight > max_sector:
                scale = max_sector / sec_weight
                weights[sec_tickers] *= scale
        return weights

    long_weights = _apply_sector_cap(long_weights, longs_df)
    short_weights = _apply_sector_cap(short_weights, shorts_df)

    # Normalize to gross targets
    def _normalize(weights: pd.Series, gross: float) -> pd.Series:
        total = weights.sum()
        if total <= 0:
            return weights
        return weights / total * gross

    long_weights = _normalize(long_weights, _GROSS_TARGET)
    short_weights = _normalize(short_weights, _GROSS_TARGET)

    # Beta-adjust: reduce exposure on the dominant-beta side to push net_beta toward 0.
    # We shift the gross allocation between books rather than applying a uniform scale
    # (a uniform scale followed by re-normalize is a no-op on relative weights).
    # Target: long_gross * long_beta_per_unit - short_gross * short_beta_per_unit ≈ 0
    all_longs = long_weights.index.tolist()
    all_shorts = short_weights.index.tolist()

    if all_longs and all_shorts:
        long_unit_beta = sum(
            (long_weights[t] / _GROSS_TARGET) * get_beta(t) for t in all_longs
        )
        short_unit_beta = sum(
            (short_weights[t] / _GROSS_TARGET) * get_beta(t) for t in all_shorts
        )

        # net_beta = G_L * long_unit_beta - G_S * short_unit_beta
        # Currently G_L = G_S = _GROSS_TARGET. Solve for ratio that zeros net_beta,
        # capped so each side stays in [0.5 * _GROSS_TARGET, _GROSS_TARGET].
        net_beta_current = _GROSS_TARGET * long_unit_beta - _GROSS_TARGET * short_unit_beta

        if abs(net_beta_current) > max_beta and long_unit_beta > 0 and short_unit_beta > 0:
            # optimal ratio: G_L / G_S = short_unit_beta / long_unit_beta
            ratio = short_unit_beta / long_unit_beta
            # Keep sum G_L + G_S = 2 * _GROSS_TARGET, solve for G_L
            # G_L = ratio * G_S, ratio * G_S + G_S = 2 * _GROSS_TARGET
            g_s = 2 * _GROSS_TARGET / (1 + ratio)
            g_l = 2 * _GROSS_TARGET - g_s
            # Clamp each side to [0.5 * _GROSS_TARGET, _GROSS_TARGET]
            lo, hi = 0.5 * _GROSS_TARGET, _GROSS_TARGET
            g_l = max(lo, min(hi, g_l))
            g_s = max(lo, min(hi, g_s))
            long_weights = _normalize(long_weights, g_l)
            short_weights = _normalize(short_weights, g_s)

    # Apply per-position cap after all adjustments
    long_weights = long_weights.clip(upper=max_pos)
    short_weights = short_weights.clip(upper=max_pos)

    # Final re-normalize after clipping
    long_weights = _normalize(long_weights, _GROSS_TARGET)
    short_weights = _normalize(short_weights, _GROSS_TARGET)

    result = {}
    for ticker in long_weights.index:
        w = float(long_weights[ticker])
        if w > 1e-6:
            result[ticker] = w
    for ticker in short_weights.index:
        w = float(short_weights[ticker])
        if w > 1e-6:
            result[ticker] = -w

    return result
