import numpy as np
import pandas as pd
from utils import get_logger
from data.market_data import get_prices

log = get_logger(__name__)

_FACTOR_NAMES = [
    "momentum", "value", "quality", "growth",
    "revisions", "short_interest", "insider", "institutional",
]
_LOOKBACK_DAYS = 120
_MIN_DAYS = 60


def _zscore(series: pd.Series) -> pd.Series:
    std = series.std()
    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std


def build_factor_model(scored_df: pd.DataFrame) -> dict | None:
    if scored_df is None or scored_df.empty:
        log.warning("build_factor_model: empty scored_df")
        return None

    # Normalize index to ticker
    if "ticker" in scored_df.columns:
        df = scored_df.set_index("ticker")
    else:
        df = scored_df.copy()

    # Only keep factors that are present
    factors = [f for f in _FACTOR_NAMES if f in df.columns]
    if not factors:
        log.warning("build_factor_model: no factor columns found")
        return None

    tickers = list(df.index)
    if len(tickers) < 3:
        log.warning("build_factor_model: too few tickers for regression")
        return None

    # B: N×K matrix of factor exposures, z-scored cross-sectionally
    # Scores are 0–100 sector-relative ranks — z-score to mean=0 std=1
    B_raw = df[factors].astype(float).fillna(50.0)
    B = B_raw.apply(_zscore, axis=0)  # z-score each factor column
    B_matrix = B.values  # N×K

    # Fetch price returns for all tickers over _LOOKBACK_DAYS
    # Returns matrix: T×N (rows=days, cols=tickers)
    returns_dict = {}
    for ticker in tickers:
        px = get_prices(ticker, days=_LOOKBACK_DAYS + 10)
        if not px.empty and "close" in px.columns:
            ret = px["close"].pct_change().dropna()
            returns_dict[ticker] = ret

    if len(returns_dict) < 3:
        log.warning("build_factor_model: insufficient price history for tickers")
        return None

    # Align returns on common dates
    returns_df = pd.concat(returns_dict, axis=1).dropna()
    returns_df = returns_df.tail(_LOOKBACK_DAYS)

    if len(returns_df) < _MIN_DAYS:
        log.warning(
            f"build_factor_model: only {len(returns_df)} trading days, need {_MIN_DAYS}"
        )
        return None

    # Keep only tickers that have both returns and factor scores
    common_tickers = [t for t in tickers if t in returns_df.columns]
    if len(common_tickers) < 3:
        log.warning("build_factor_model: not enough tickers with both returns and scores")
        return None

    returns_df = returns_df[common_tickers]
    B_aligned = B.loc[common_tickers].values  # N×K
    K = B_aligned.shape[1]
    T = len(returns_df)
    N = len(common_tickers)

    # Cross-sectional OLS each day t:
    #   r_t (N×1) = alpha + B_aligned (N×K) * F_t (K×1) + eps_t
    # Design matrix includes intercept: X = [ones(N,1) | B_aligned] → N×(K+1)
    ones = np.ones((N, 1))
    X = np.hstack([ones, B_aligned])  # N×(K+1)
    XtX_inv = np.linalg.pinv(X.T @ X)  # (K+1)×(K+1)

    # Factor returns matrix: T×K (drop intercept column)
    factor_returns_matrix = np.zeros((T, K))
    residuals_matrix = np.zeros((T, N))

    for t in range(T):
        r_t = returns_df.iloc[t].values  # N×1
        # OLS: coefficients = (XtX)^{-1} X' r
        coefs = XtX_inv @ (X.T @ r_t)  # (K+1,)
        alpha_t = coefs[0]
        F_t = coefs[1:]  # K factor returns this day
        factor_returns_matrix[t] = F_t
        # Residuals
        r_hat = alpha_t + B_aligned @ F_t
        residuals_matrix[t] = r_t - r_hat

    # Factor covariance: annualized (K×K)
    factor_cov = np.cov(factor_returns_matrix, rowvar=False) * 252
    if K == 1:
        factor_cov = np.array([[float(np.var(factor_returns_matrix[:, 0]) * 252)]])

    # Specific variance per stock: annualized
    specific_variances = np.var(residuals_matrix, axis=0) * 252  # N,

    return {
        "factor_cov_matrix": factor_cov,           # K×K
        "specific_variances": specific_variances,   # N,
        "factor_returns": factor_returns_matrix,    # T×K
        "factor_loadings": B_aligned,               # N×K
        "tickers": common_tickers,
        "factor_names": factors,
    }


def decompose_portfolio_risk(weights: dict, factor_model: dict) -> dict:
    if not weights or factor_model is None:
        return {
            "factor_var": 0.0, "specific_var": 0.0, "total_var": 0.0,
            "factor_pct": 0.0, "specific_pct": 1.0, "mctr": {},
        }

    tickers = factor_model["tickers"]
    B = factor_model["factor_loadings"]           # N×K
    F_cov = factor_model["factor_cov_matrix"]     # K×K
    spec_var = factor_model["specific_variances"]  # N,

    # Build weight vector aligned to model tickers
    w = np.array([weights.get(t, 0.0) for t in tickers])

    if w.sum() == 0 and np.all(w == 0):
        return {
            "factor_var": 0.0, "specific_var": 0.0, "total_var": 0.0,
            "factor_pct": 0.0, "specific_pct": 1.0, "mctr": {},
        }

    # Factor variance contribution: w' B F_cov B' w
    Bw = B.T @ w  # K,
    factor_var = float(Bw @ F_cov @ Bw)
    factor_var = max(factor_var, 0.0)

    # Specific variance: w' diag(spec_var) w
    specific_var = float(np.dot(w ** 2, spec_var))
    specific_var = max(specific_var, 0.0)

    total_var = factor_var + specific_var
    sigma_p = float(np.sqrt(max(total_var, 1e-12)))

    # Predicted covariance matrix: Sigma = B F_cov B' + diag(spec_var)
    Sigma = B @ F_cov @ B.T + np.diag(spec_var)

    # Risk contribution (MCTR): RC_i = w_i * (Sigma w)_i
    # Normalize so sum(RC_i) = sigma_p
    Sigma_w = Sigma @ w
    RC = w * Sigma_w  # risk contribution (not normalized)

    # MCTR as fraction of total risk
    mctr_dict = {}
    total_w = np.sum(np.abs(w))
    if total_w > 0 and sigma_p > 0:
        for i, ticker in enumerate(tickers):
            if w[i] == 0:
                continue
            mctr_i = RC[i] / sigma_p        # marginal risk contribution
            w_i_norm = abs(w[i]) / total_w  # normalized weight

            # Flag disproportionate risk: MCTR/sigma_p > 1.5 * w_normalized
            flag = (abs(mctr_i) / sigma_p) > (1.5 * w_i_norm) if sigma_p > 0 else False
            mctr_dict[ticker] = {
                "mctr": round(float(mctr_i), 6),
                "weight": round(float(w[i]), 6),
                "disproportionate": bool(flag),
            }

    total_var_safe = total_var if total_var > 0 else 1e-12
    return {
        "factor_var": round(factor_var, 8),
        "specific_var": round(specific_var, 8),
        "total_var": round(total_var, 8),
        "factor_pct": round(factor_var / total_var_safe, 4),
        "specific_pct": round(specific_var / total_var_safe, 4),
        "mctr": mctr_dict,
    }


def get_predicted_cov_matrix(tickers: list[str], factor_model: dict) -> np.ndarray | None:
    if not tickers or factor_model is None:
        return None

    model_tickers = factor_model["tickers"]
    B_full = factor_model["factor_loadings"]     # N_model×K
    F_cov = factor_model["factor_cov_matrix"]    # K×K
    spec_var = factor_model["specific_variances"] # N_model,

    # Select rows for requested tickers
    idx = []
    for t in tickers:
        if t in model_tickers:
            idx.append(model_tickers.index(t))
        else:
            log.warning(f"get_predicted_cov_matrix: {t} not in factor model")
            return None

    B = B_full[idx]          # n×K
    sv = spec_var[idx]       # n,

    Sigma = B @ F_cov @ B.T + np.diag(sv)
    return Sigma
