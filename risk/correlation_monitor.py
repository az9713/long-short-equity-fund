import numpy as np
import pandas as pd
from utils import get_logger, get_config
from data.market_data import get_prices

log = get_logger(__name__)
cfg = get_config().get("risk", {})

_LOOKBACK = 60


def _get_return_series(tickers: list[str]) -> pd.DataFrame:
    frames = {}
    for ticker in tickers:
        df = get_prices(ticker, days=_LOOKBACK + 10)
        if not df.empty and "close" in df.columns:
            ret = df["close"].pct_change().dropna()
            if len(ret) >= 30:
                frames[ticker] = ret
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, axis=1).dropna()
    return combined.tail(_LOOKBACK)


def _effective_bets(corr_matrix: np.ndarray) -> float:
    # exp(-entropy of normalized eigenvalue distribution) = diversification measure
    if corr_matrix.shape[0] < 2:
        return float(corr_matrix.shape[0])
    eigenvalues = np.linalg.eigvalsh(corr_matrix)
    eigenvalues = np.maximum(eigenvalues, 1e-10)
    total = eigenvalues.sum()
    weights = eigenvalues / total
    entropy = -np.sum(weights * np.log(weights + 1e-15))
    return round(float(np.exp(entropy)), 2)


def check_correlations(positions_df: pd.DataFrame) -> dict:
    empty_result = {
        "avg_long_correlation": None,
        "avg_short_correlation": None,
        "high_corr_pairs": [],
        "effective_bets": None,
        "alerts": [],
    }

    if positions_df is None or positions_df.empty:
        return empty_result

    corr_alert = cfg.get("correlation_alert", 0.60)
    corr_veto = cfg.get("correlation_veto", 0.80)

    longs = positions_df[positions_df["side"] == "LONG"]["ticker"].tolist()
    shorts = positions_df[positions_df["side"] == "SHORT"]["ticker"].tolist()

    alerts = []
    high_corr_pairs = []

    def _analyze_book(tickers: list[str], book_name: str):
        if len(tickers) < 2:
            return None, []
        rets = _get_return_series(tickers)
        if rets.empty or rets.shape[1] < 2:
            return None, []

        corr = rets.corr()
        tickers_present = list(corr.columns)
        n = len(tickers_present)

        # Upper triangle of correlation matrix, excluding diagonal
        pairs = []
        corr_vals = []
        for i in range(n):
            for j in range(i + 1, n):
                t1, t2 = tickers_present[i], tickers_present[j]
                c = corr.iloc[i, j]
                if np.isnan(c):
                    continue
                corr_vals.append(c)
                if c > corr_veto:
                    pairs.append((t1, t2, round(float(c), 4)))

        avg_corr = float(np.mean(corr_vals)) if corr_vals else None

        if avg_corr is not None and avg_corr > corr_alert:
            alerts.append(
                f"High avg correlation in {book_name} book: {avg_corr:.3f} (limit {corr_alert})"
            )

        return avg_corr, pairs

    avg_long, long_pairs = _analyze_book(longs, "long")
    avg_short, short_pairs = _analyze_book(shorts, "short")

    high_corr_pairs = long_pairs + short_pairs
    for t1, t2, c in high_corr_pairs:
        alerts.append(f"High correlation pair: {t1}/{t2} = {c:.3f}")

    # Effective bets across whole book
    all_tickers = longs + shorts
    eff_bets = None
    if len(all_tickers) >= 2:
        rets = _get_return_series(all_tickers)
        if not rets.empty and rets.shape[1] >= 2:
            corr_matrix = rets.corr().values
            eff_bets = _effective_bets(corr_matrix)

    return {
        "avg_long_correlation": round(avg_long, 4) if avg_long is not None else None,
        "avg_short_correlation": round(avg_short, 4) if avg_short is not None else None,
        "high_corr_pairs": [(t1, t2) for t1, t2, _ in high_corr_pairs],
        "effective_bets": eff_bets,
        "alerts": alerts,
    }
