import numpy as np
import pandas as pd
from scipy.optimize import minimize
from utils import get_logger, get_config
from data.market_data import get_prices
from portfolio.transaction_cost import cost_as_return
from portfolio.beta import get_beta

log = get_logger(__name__)

_GROSS_TARGET = 0.75
_MIN_RETURN_ROWS = 60  # minimum aligned return rows to include a ticker in MVO
_COV_DAYS = 120


def optimize_mvo(
    candidates_df: pd.DataFrame,
    portfolio_value: float = 100_000,
    config: dict = None,
) -> dict[str, float]:
    if config is None:
        config = get_config().get("portfolio", {})

    max_pos = config.get("max_position_pct", 0.05)
    max_sector = config.get("max_sector_pct", 0.25)
    max_beta = config.get("max_beta", 0.15)
    risk_aversion = config.get("mvo_risk_aversion", 1.0)

    if "ticker" in candidates_df.columns:
        df = candidates_df.set_index("ticker")
    else:
        df = candidates_df.copy()

    longs_df = df[df["signal"] == "LONG"]
    shorts_df = df[df["signal"] == "SHORT"]

    if longs_df.empty and shorts_df.empty:
        log.warning("No LONG or SHORT candidates for MVO — returning empty weights")
        return {}

    # Gather price histories for covariance estimation
    price_series = {}
    for ticker in df.index:
        px = get_prices(ticker, days=_COV_DAYS + 20)
        if px.empty or "close" not in px.columns:
            continue
        price_series[ticker] = px["close"]

    if not price_series:
        log.warning("No price data available for MVO — falling back to conviction")
        return _fallback(candidates_df, portfolio_value, config)

    # Align on common dates, drop tickers with insufficient history
    price_df = pd.DataFrame(price_series).sort_index()
    returns_df = price_df.pct_change().dropna(how="all")

    # Drop tickers with fewer than _MIN_RETURN_ROWS non-NaN rows
    valid_cols = [c for c in returns_df.columns if returns_df[c].notna().sum() >= _MIN_RETURN_ROWS]
    if not valid_cols:
        log.warning("No tickers have sufficient history for MVO — falling back to conviction")
        return _fallback(candidates_df, portfolio_value, config)

    dropped = set(df.index) - set(valid_cols)
    if dropped:
        log.info(f"MVO: dropped {len(dropped)} tickers due to insufficient history: {dropped}")

    returns_df = returns_df[valid_cols].dropna()
    tickers = [t for t in valid_cols if t in df.index]

    if not tickers:
        log.warning("No valid tickers after filtering — falling back to conviction")
        return _fallback(candidates_df, portfolio_value, config)

    n = len(tickers)
    signals = df.loc[tickers, "signal"]
    score_col = "combined_score" if "combined_score" in df.columns else "composite"
    scores = df.loc[tickers, score_col].fillna(50.0)

    n_longs = int((signals == "LONG").sum())
    n_shorts = int((signals == "SHORT").sum())

    # Feasibility check: enough positions to satisfy gross + per-position cap
    # n * max_pos must be >= _GROSS_TARGET for each side
    if n_longs > 0 and n_longs * max_pos < _GROSS_TARGET:
        log.warning(
            f"MVO infeasible: {n_longs} longs × {max_pos} cap = {n_longs * max_pos:.2f} "
            f"< gross target {_GROSS_TARGET} — falling back to conviction"
        )
        return _fallback(candidates_df, portfolio_value, config)
    if n_shorts > 0 and n_shorts * max_pos < _GROSS_TARGET:
        log.warning(
            f"MVO infeasible: {n_shorts} shorts × {max_pos} cap = {n_shorts * max_pos:.2f} "
            f"< gross target {_GROSS_TARGET} — falling back to conviction"
        )
        return _fallback(candidates_df, portfolio_value, config)

    # Expected returns: linear map from composite score
    # score 100 → +15%/yr, score 50 → 0, score 0 → -15%/yr
    mu = ((scores - 50.0) / 50.0 * 0.15).values.astype(float)

    # Subtract transaction costs from expected returns
    # Approximate trade size as max_position_pct of portfolio_value
    approx_trade = max_pos * portfolio_value
    for i, t in enumerate(tickers):
        cost = cost_as_return(t, approx_trade)
        mu[i] -= cost

    # Annualized covariance
    cov = returns_df[tickers].cov().values * 252

    # Regularize covariance: add small diagonal to ensure positive definiteness
    cov += np.eye(n) * 1e-6

    # Build bounds: LONG >= 0, SHORT <= 0
    bounds = []
    for t in tickers:
        if signals[t] == "LONG":
            bounds.append((0.0, max_pos))
        else:
            bounds.append((-max_pos, 0.0))

    # Initial guess: equal weight within each side, clamped to bounds
    w0 = np.zeros(n)
    for i, t in enumerate(tickers):
        if signals[t] == "LONG":
            w0[i] = min(_GROSS_TARGET / n_longs, max_pos) if n_longs > 0 else 0.0
        else:
            w0[i] = max(-_GROSS_TARGET / n_shorts, -max_pos) if n_shorts > 0 else 0.0

    # Build sectors array for constraints
    sector_col = "sector" if "sector" in df.columns else None
    sectors = df.loc[tickers, "sector"].values if sector_col else np.full(n, "Unknown")
    unique_sectors = list(set(sectors))

    betas = np.array([get_beta(t) for t in tickers])

    def objective(w):
        # Negative because we maximize
        return -(mu @ w - risk_aversion * w @ cov @ w)

    def obj_grad(w):
        return -(mu - 2 * risk_aversion * cov @ w)

    constraints = []

    # Long gross = 0.75
    long_mask = np.array([1.0 if signals[t] == "LONG" else 0.0 for t in tickers])
    constraints.append({
        "type": "eq",
        "fun": lambda w, m=long_mask: np.dot(m, w) - _GROSS_TARGET,
        "jac": lambda w, m=long_mask: m,
    })

    # Short gross = 0.75 (sum of abs of short weights; short weights are negative)
    short_mask = np.array([1.0 if signals[t] == "SHORT" else 0.0 for t in tickers])
    constraints.append({
        "type": "eq",
        "fun": lambda w, m=short_mask: -np.dot(m, w) - _GROSS_TARGET,
        "jac": lambda w, m=short_mask: -m,
    })

    # Net beta <= max_beta
    constraints.append({
        "type": "ineq",
        "fun": lambda w: max_beta - (betas @ w),
        "jac": lambda w: -betas,
    })

    # Net beta >= -max_beta
    constraints.append({
        "type": "ineq",
        "fun": lambda w: (betas @ w) + max_beta,
        "jac": lambda w: betas,
    })

    for sector in unique_sectors:
        sec_mask = np.array([1.0 if s == sector else 0.0 for s in sectors])

        # Long-side sector <= max_sector_pct
        long_sec = sec_mask * long_mask
        if long_sec.sum() > 0:
            constraints.append({
                "type": "ineq",
                "fun": lambda w, m=long_sec: max_sector - np.dot(m, w),
                "jac": lambda w, m=long_sec: -m,
            })

        # Short-side sector (absolute) <= max_sector_pct
        short_sec = sec_mask * short_mask
        if short_sec.sum() > 0:
            constraints.append({
                "type": "ineq",
                "fun": lambda w, m=short_sec: max_sector - np.dot(-m, w),
                "jac": lambda w, m=short_sec: m,
            })

        # Per-sector net exposure: |long_sector - short_sector_abs| <= max_sector_pct
        # long contribution: sec_mask * long_mask (positive weights)
        # short contribution: sec_mask * short_mask (negative weights, so -w gives abs)
        # net = long_sec @ w + short_sec @ w (short weights are negative, so net can be ± )
        # ineq form: net <= max_sector  AND  net >= -max_sector
        if long_sec.sum() > 0 or short_sec.sum() > 0:
            net_sec = sec_mask  # signed: long positive, short negative in w
            constraints.append({
                "type": "ineq",
                "fun": lambda w, m=net_sec: max_sector - np.dot(m, w),
                "jac": lambda w, m=net_sec: -m,
            })
            constraints.append({
                "type": "ineq",
                "fun": lambda w, m=net_sec: np.dot(m, w) + max_sector,
                "jac": lambda w, m=net_sec: m,
            })

    try:
        result = minimize(
            objective,
            w0,
            jac=obj_grad,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-9},
        )

        if not result.success:
            log.warning(f"MVO did not converge: {result.message} — falling back to conviction")
            return _fallback(candidates_df, portfolio_value, config)

        weights = result.x
        # Zero out tiny positions (numerical noise)
        weights[np.abs(weights) < 1e-5] = 0.0

        return {tickers[i]: float(weights[i]) for i in range(n) if abs(weights[i]) > 1e-5}

    except Exception as e:
        log.error(f"MVO optimization failed: {e} — falling back to conviction")
        return _fallback(candidates_df, portfolio_value, config)


def _fallback(candidates_df, portfolio_value, config):
    # Lazy import to avoid any circularity
    from portfolio.optimizer import optimize_conviction
    return optimize_conviction(candidates_df, portfolio_value, config)
