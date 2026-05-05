import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from utils import get_db, get_logger
from data.market_data import get_prices
from risk.risk_state import load_risk_state

log = get_logger(__name__)


def _get_nav_series(days: int) -> pd.DataFrame:
    """Pull NAV history from risk_state + portfolio_history to build equity curve."""
    state = load_risk_state()
    nav_history = state.get("nav_history", [])

    if not nav_history:
        return pd.DataFrame()

    df = pd.DataFrame(nav_history)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=days)
    df = df[df.index >= cutoff]
    return df


def _build_equity_curve(days: int) -> pd.DataFrame:
    nav_df = _get_nav_series(days)
    spy_df = get_prices("SPY", days=days + 10)

    if nav_df.empty:
        return pd.DataFrame()

    # Normalize NAV to 100
    nav_series = nav_df["value"].copy()
    nav_norm = nav_series / nav_series.iloc[0] * 100

    result = pd.DataFrame({"date": nav_norm.index, "portfolio_value": nav_norm.values})
    result = result.set_index("date")

    if not spy_df.empty and "close" in spy_df.columns:
        spy = spy_df["close"].copy()
        spy.index = pd.to_datetime(spy.index)
        spy_aligned = spy.reindex(result.index, method="ffill")
        if not spy_aligned.empty and spy_aligned.iloc[0] != 0:
            result["spy_value"] = spy_aligned / spy_aligned.iloc[0] * 100
        else:
            result["spy_value"] = np.nan
    else:
        result["spy_value"] = np.nan

    # Drawdown
    rolling_max = result["portfolio_value"].cummax()
    result["drawdown"] = (result["portfolio_value"] - rolling_max) / rolling_max

    result = result.reset_index()
    return result


def _sharpe(returns: pd.Series) -> float:
    if returns.empty or returns.std() == 0:
        return 0.0
    annualized = returns.mean() * 252
    vol = returns.std() * np.sqrt(252)
    return round(float(annualized / vol), 4)


def _max_drawdown(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    rolling_max = series.cummax()
    drawdown = (series - rolling_max) / rolling_max
    return round(float(drawdown.min()), 4)


def _calmar(returns: pd.Series, series: pd.Series) -> float:
    ann_ret = float(returns.mean() * 252)
    mdd = abs(_max_drawdown(series))
    if mdd == 0:
        return 0.0
    return round(ann_ret / mdd, 4)


def get_metrics_vs_spy(days: int = 252) -> dict:
    curve = _build_equity_curve(days)

    empty = {
        "sharpe": 0.0, "max_drawdown": 0.0, "calmar": 0.0,
        "beta": 0.0, "alpha": 0.0, "correlation": 0.0, "win_rate": 0.0,
    }

    if curve.empty:
        return empty

    try:
        port_rets = curve.set_index("date")["portfolio_value"].pct_change().dropna()
        spy_col = "spy_value" if "spy_value" in curve.columns else None

        sharpe = _sharpe(port_rets)
        mdd = _max_drawdown(curve.set_index("date")["portfolio_value"])
        calmar = _calmar(port_rets, curve.set_index("date")["portfolio_value"])
        win_rate = round(float((port_rets > 0).mean()), 4) if len(port_rets) > 0 else 0.0

        beta = 0.0
        alpha = 0.0
        correlation = 0.0

        if spy_col and "spy_value" in curve.columns:
            spy_rets = curve.set_index("date")["spy_value"].pct_change().dropna()
            aligned = pd.concat([port_rets, spy_rets], axis=1, join="inner").dropna()
            aligned.columns = ["port", "spy"]
            if len(aligned) >= 20:
                cov = np.cov(aligned["port"], aligned["spy"])
                var_spy = cov[1, 1]
                if var_spy > 0:
                    beta = round(float(cov[0, 1] / var_spy), 4)
                alpha = round(float((aligned["port"].mean() - beta * aligned["spy"].mean()) * 252), 4)
                correlation = round(float(aligned["port"].corr(aligned["spy"])), 4)

        return {
            "sharpe": sharpe,
            "max_drawdown": mdd,
            "calmar": calmar,
            "beta": beta,
            "alpha": alpha,
            "correlation": correlation,
            "win_rate": win_rate,
        }
    except Exception as e:
        log.error(f"get_metrics_vs_spy failed: {e}")
        return empty


def get_monthly_returns_grid() -> pd.DataFrame:
    state = load_risk_state()
    nav_history = state.get("nav_history", [])

    if len(nav_history) < 2:
        return pd.DataFrame()

    df = pd.DataFrame(nav_history)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df["value"].resample("ME").last()

    monthly_rets = df.pct_change().dropna()

    if monthly_rets.empty:
        return pd.DataFrame()

    rows = []
    for year, group in monthly_rets.groupby(monthly_rets.index.year):
        row = {"Year": year}
        annual_cum = 1.0
        for dt, r in group.items():
            month_name = dt.strftime("%b")
            row[month_name] = round(float(r), 4)
            annual_cum *= (1 + r)
        row["Annual"] = round(float(annual_cum - 1), 4)
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    month_cols = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Annual"]
    result = pd.DataFrame(rows).set_index("Year")

    # Add missing month columns as NaN
    for col in month_cols:
        if col not in result.columns:
            result[col] = np.nan

    return result[month_cols]


def get_equity_curve(days: int = 252) -> pd.DataFrame:
    return _build_equity_curve(days)
