import sys
import time
import json
import subprocess
from pathlib import Path
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import streamlit as st

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="JARVIS | Meridian Capital Partners",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* Hide Streamlit chrome */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* Dark theme base */
    .stApp {
        background-color: #000e17;
        color: #e0e6f0;
    }

    /* Card style */
    .card {
        background: linear-gradient(135deg, #131827, #1a2635);
        border-radius: 10px;
        padding: 16px 20px;
        margin-bottom: 12px;
        border: 1px solid #1e2d42;
    }

    /* KPI metric */
    .kpi-value {
        font-size: 2rem;
        font-weight: 700;
        color: #6366f1;
    }
    .kpi-label {
        font-size: 0.75rem;
        color: #7a8fa6;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }

    /* Badges */
    .badge-green { background:#10b981; color:#fff; padding:2px 8px; border-radius:12px; font-size:0.8rem; }
    .badge-red   { background:#f43f5e; color:#fff; padding:2px 8px; border-radius:12px; font-size:0.8rem; }
    .badge-yellow{ background:#f59e0b; color:#fff; padding:2px 8px; border-radius:12px; font-size:0.8rem; }
    .badge-gray  { background:#374151; color:#d1d5db; padding:2px 8px; border-radius:12px; font-size:0.8rem; }

    /* JARVIS chat box */
    .jarvis-response {
        background: linear-gradient(135deg, #131827, #1a2635);
        border-left: 3px solid #6366f1;
        padding: 16px;
        border-radius: 8px;
        margin-top: 8px;
        white-space: pre-wrap;
    }

    /* Stacked bar / chart containers */
    div[data-testid="stPlotlyChart"] {
        background: transparent !important;
    }

    /* Tab bar */
    .stTabs [data-baseweb="tab-list"] {
        background-color: #0a1520;
        border-radius: 8px;
        padding: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        color: #7a8fa6;
        border-radius: 6px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #6366f1 !important;
        color: #fff !important;
    }

    /* DataFrames */
    .stDataFrame { background: transparent; }

    /* Dividers */
    hr { border-color: #1e2d42; }
</style>
""", unsafe_allow_html=True)


# ── Session state init ────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "optimizer_mode" not in st.session_state:
    st.session_state.optimizer_mode = "MVO"
if "approval_state" not in st.session_state:
    st.session_state.approval_state = {}
if "live_feed" not in st.session_state:
    st.session_state.live_feed = False


# ── Helper: safe import wrappers ──────────────────────────────────────────────
def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        return default


def _load_scored_df() -> pd.DataFrame | None:
    csv = Path(__file__).parent.parent / "output" / "scored_universe_latest.csv"
    if not csv.exists():
        return None
    try:
        return pd.read_csv(csv)
    except Exception:
        return None


def _vix_badge(vix: float) -> str:
    if vix < 15:
        return f'<span class="badge-green">LOW {vix:.1f}</span>'
    if vix < 25:
        return f'<span class="badge-gray">NORMAL {vix:.1f}</span>'
    if vix < 33:
        return f'<span class="badge-yellow">HIGH {vix:.1f}</span>'
    return f'<span class="badge-red">EXTREME {vix:.1f}</span>'


def _pct(val: float, decimals: int = 2) -> str:
    return f"{val * 100:.{decimals}f}%"


# ── Tabs ──────────────────────────────────────────────────────────────────────
tabs = st.tabs([
    "I · PORTFOLIO",
    "II · RESEARCH",
    "III · RISK",
    "IV · PERFORMANCE",
    "V · EXECUTION",
    "VI · LETTER",
    "VII · BACKTEST",
])


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE I — PORTFOLIO
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[0]:
    st.markdown("## JARVIS — Meridian Capital Partners")

    # ── KPI row ────────────────────────────────────────────────────────────────
    scored_df = _load_scored_df()

    universe_size = 0
    long_candidates = 0
    short_candidates = 0
    if scored_df is not None and not scored_df.empty:
        universe_size = len(scored_df)
        long_candidates = int((scored_df["signal"] == "LONG").sum()) if "signal" in scored_df.columns else 0
        short_candidates = int((scored_df["signal"] == "SHORT").sum()) if "signal" in scored_df.columns else 0

    positions_df = _safe(lambda: __import__("portfolio.state", fromlist=["get_positions"]).get_positions(), pd.DataFrame())
    open_positions = 0 if positions_df is None or (isinstance(positions_df, pd.DataFrame) and positions_df.empty) else len(positions_df)

    # Insider events count
    insider_count = 0
    try:
        from utils import get_db
        conn = get_db()
        cutoff = (datetime.utcnow() - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM insider_transactions WHERE transaction_date >= ?",
            (cutoff,),
        ).fetchone()
        insider_count = int(row["cnt"]) if row else 0
        conn.close()
    except Exception:
        pass

    vix_val = _safe(lambda: __import__("data.providers", fromlist=["get_vix"]).get_vix(), 20.0)

    kpi_cols = st.columns(6)
    kpi_data = [
        ("Universe", universe_size),
        ("Longs", long_candidates),
        ("Shorts", short_candidates),
        ("Positions", open_positions),
        ("Insider Events", insider_count),
        ("VIX", f"{vix_val:.1f}"),
    ]
    for col, (label, val) in zip(kpi_cols, kpi_data):
        with col:
            st.markdown(
                f'<div class="card"><div class="kpi-value">{val}</div>'
                f'<div class="kpi-label">{label}</div></div>',
                unsafe_allow_html=True,
            )

    # ── Status strip ──────────────────────────────────────────────────────────
    st.markdown("---")
    status_cols = st.columns([2, 2, 2])
    with status_cols[0]:
        st.markdown(f"**VIX Regime:** {_vix_badge(vix_val)}", unsafe_allow_html=True)

    with status_cols[1]:
        try:
            from data.market_data import get_prices
            spy = get_prices("SPY", days=3)
            last_date = spy.index[-1].strftime("%Y-%m-%d") if not spy.empty else "N/A"
            st.markdown(f"**Prices:** {last_date}")
        except Exception:
            st.markdown("**Prices:** N/A")

    with status_cols[2]:
        try:
            from data.earnings_calendar import get_upcoming_earnings
            earnings_df = get_upcoming_earnings(days=7)
            n_earnings = len(earnings_df) if not earnings_df.empty else 0
            st.markdown(f"**Earnings This Week:** {n_earnings}")
        except Exception:
            st.markdown("**Earnings This Week:** N/A")

    # ── JARVIS Chat ───────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Ask JARVIS")

    # Display conversation history
    for msg in st.session_state.messages:
        role_label = "You" if msg["role"] == "user" else "JARVIS"
        if msg["role"] == "user":
            st.markdown(f"**{role_label}:** {msg['content']}")
        else:
            st.markdown(
                f'<div class="jarvis-response"><strong>JARVIS:</strong><br>{msg["content"]}</div>',
                unsafe_allow_html=True,
            )

    user_input = st.text_input(
        "Question",
        placeholder="Give me a summary for today",
        key="jarvis_input",
    )

    if st.button("Ask JARVIS") and user_input:
        # Build context snapshot
        ctx = {
            "date": str(date.today()),
            "vix": vix_val,
        }
        try:
            from risk.risk_state import load_risk_state
            ctx["risk_state"] = load_risk_state()
        except Exception:
            pass

        if positions_df is not None and isinstance(positions_df, pd.DataFrame) and not positions_df.empty:
            ctx["portfolio"] = positions_df.to_dict(orient="records")

        if scored_df is not None and not scored_df.empty:
            if "signal" in scored_df.columns:
                ctx["top_longs"] = scored_df[scored_df["signal"] == "LONG"].head(10)[
                    ["ticker", "composite", "sector"] if all(c in scored_df.columns for c in ["ticker", "composite", "sector"]) else scored_df.columns[:3]
                ].to_dict(orient="records")
                ctx["top_shorts"] = scored_df[scored_df["signal"] == "SHORT"].head(10)[
                    ["ticker", "composite", "sector"] if all(c in scored_df.columns for c in ["ticker", "composite", "sector"]) else scored_df.columns[:3]
                ].to_dict(orient="records")

        context_str = json.dumps(ctx, default=str)
        if len(context_str) > 50_000:
            context_str = context_str[:50_000] + "...[truncated]"

        # Include prior turns (last 8 messages)
        history = st.session_state.messages[-8:]
        history_text = ""
        for m in history:
            role = "User" if m["role"] == "user" else "JARVIS"
            history_text += f"{role}: {m['content']}\n\n"

        system = (
            "You are JARVIS, the AI portfolio analyst for Meridian Capital Partners. "
            "Answer the user's question about the portfolio, market conditions, or investment strategy. "
            "Be concise, data-driven, and professional. Reference specific numbers from the context when relevant."
        )
        full_user = (
            f"Portfolio Context:\n{context_str}\n\n"
            f"Conversation history:\n{history_text}"
            f"User: {user_input}"
        )

        st.session_state.messages.append({"role": "user", "content": user_input})

        with st.spinner("JARVIS is thinking..."):
            try:
                from analysis.ai_client import call_llm
                response = call_llm(system, full_user, temperature=0.3, analyzer="jarvis_chat", ticker="PORTFOLIO")
            except Exception as e:
                response = f"Error: {e}"

        if response:
            st.session_state.messages.append({"role": "assistant", "content": response})
        else:
            st.session_state.messages.append({"role": "assistant", "content": "Unable to respond — check OPENROUTER_API_KEY."})

        # Keep only last 8 turns (16 messages)
        if len(st.session_state.messages) > 16:
            st.session_state.messages = st.session_state.messages[-16:]

        st.rerun()

    if st.button("Clear Chat"):
        st.session_state.messages = []
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE II — RESEARCH
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    st.markdown("## Research & Signals")

    scored_df = _load_scored_df()
    if scored_df is None:
        st.warning("No scored universe found. Run `python run_scoring.py` first.")
    else:
        # ── Banners ───────────────────────────────────────────────────────────
        try:
            from factors.crowding import detect_crowding
            crowding = detect_crowding()
            crowded_pairs = [v for v in crowding.values() if v.get("is_crowded")]
            if crowded_pairs:
                msgs = [v["warning_message"] for v in crowded_pairs]
                st.warning("CROWDING ALERT: " + " | ".join(msgs))
        except Exception:
            pass

        try:
            from portfolio.rebalance_schedule import get_rebalance_warnings
            warnings = get_rebalance_warnings(scored_df)
            if warnings:
                st.warning("REBALANCE ADVISORY: " + " | ".join(warnings))
        except Exception:
            pass

        # ── Mode toggle ───────────────────────────────────────────────────────
        mode_col1, mode_col2 = st.columns([3, 1])
        with mode_col2:
            mode = st.radio("Optimizer", ["MVO", "Conviction"], horizontal=True, key="opt_mode")
            st.session_state.optimizer_mode = mode

        # ── Factor heatmap ────────────────────────────────────────────────────
        st.markdown("### Factor Heatmap — Top 30 + Bottom 30")
        factor_cols = ["momentum", "value", "quality", "growth",
                       "revisions", "short_interest", "insider", "institutional"]
        factor_cols_present = [c for c in factor_cols if c in scored_df.columns]

        if factor_cols_present and "composite" in scored_df.columns:
            top30 = scored_df.nlargest(30, "composite")
            bot30 = scored_df.nsmallest(30, "composite")
            heatmap_df = pd.concat([top30, bot30]).drop_duplicates("ticker")

            display_cols = ["ticker", "sector", "composite", "signal"] + factor_cols_present
            display_cols = [c for c in display_cols if c in heatmap_df.columns]
            heatmap_view = heatmap_df[display_cols].reset_index(drop=True)

            styled = heatmap_view.style.background_gradient(
                cmap="RdYlGn", subset=factor_cols_present, vmin=0, vmax=100
            )
            st.dataframe(styled, use_container_width=True, height=600)

        # ── Candidate cards ───────────────────────────────────────────────────
        st.markdown("---")
        long_col, short_col = st.columns(2)

        if "signal" in scored_df.columns:
            top_longs = scored_df[scored_df["signal"] == "LONG"].head(10)
            top_shorts = scored_df[scored_df["signal"] == "SHORT"].head(10)
        else:
            top_longs = scored_df.head(10)
            top_shorts = scored_df.tail(10)

        def _approval_buttons(ticker: str, side: str):
            key_base = f"{ticker}_{side}"
            current = st.session_state.approval_state.get(key_base, "PENDING")
            st.markdown(f"Status: **{current}**")

            btn_cols = st.columns(3)
            with btn_cols[0]:
                if st.button("Approve", key=f"approve_{key_base}"):
                    from portfolio.state import approve_position
                    approve_position(ticker, side)
                    st.session_state.approval_state[key_base] = "APPROVED"
                    st.rerun()
            with btn_cols[1]:
                if st.button("Reject", key=f"reject_{key_base}"):
                    st.session_state.approval_state[key_base] = "REJECTING"
            with btn_cols[2]:
                if st.button("Reset", key=f"reset_{key_base}"):
                    from portfolio.state import reset_position
                    reset_position(ticker)
                    st.session_state.approval_state[key_base] = "PENDING"
                    st.rerun()

            if st.session_state.approval_state.get(key_base) == "REJECTING":
                reason = st.text_input("Rejection reason", key=f"reason_{key_base}")
                if st.button("Confirm Reject", key=f"confirm_reject_{key_base}") and reason:
                    from portfolio.state import reject_position
                    reject_position(ticker, reason)
                    st.session_state.approval_state[key_base] = "REJECTED"
                    st.rerun()

        def _render_candidates(df: pd.DataFrame, side: str, header: str):
            st.markdown(f"### {header}")
            if df.empty:
                st.info(f"No {side} candidates.")
                return

            for _, row in df.iterrows():
                ticker = row.get("ticker", "?")
                sector = row.get("sector", "?")
                composite = row.get("composite", 0)
                piotroski = row.get("piotroski_f")
                altman_label = row.get("altman_label", "")

                with st.expander(f"{ticker} — {sector} | Score: {composite:.0f}", expanded=False):
                    meta_cols = st.columns([2, 1, 1])
                    with meta_cols[0]:
                        st.markdown(f"**Sector:** {sector}")
                        st.markdown(f"**Composite:** {composite:.1f}/100")
                    with meta_cols[1]:
                        if piotroski is not None and not pd.isna(piotroski):
                            color = "badge-green" if piotroski >= 7 else ("badge-yellow" if piotroski >= 4 else "badge-red")
                            st.markdown(f'F-Score: <span class="{color}">{int(piotroski)}</span>', unsafe_allow_html=True)
                    with meta_cols[2]:
                        if altman_label:
                            color = "badge-red" if "distress" in altman_label.lower() else "badge-green"
                            st.markdown(f'Z: <span class="{color}">{altman_label}</span>', unsafe_allow_html=True)

                    _approval_buttons(ticker, side)

                    # Claude Analysis from reports
                    report_dirs = sorted(
                        (Path(__file__).parent.parent / "output").glob("reports_*"),
                        reverse=True,
                    )
                    for rdir in report_dirs[:3]:
                        report_file = rdir / f"{ticker}.md"
                        if report_file.exists():
                            with st.expander("Claude Analysis"):
                                st.markdown(report_file.read_text(encoding="utf-8"))
                            break

        with long_col:
            _render_candidates(top_longs, "LONG", "Top 10 Long Candidates")

        with short_col:
            _render_candidates(top_shorts, "SHORT", "Top 10 Short Candidates")

        # ── Execute button ────────────────────────────────────────────────────
        st.markdown("---")
        if st.button("Execute Approved Trades", type="primary"):
            with st.spinner("Running pre-trade veto checks..."):
                try:
                    from risk.risk_state import load_risk_state
                    risk_state = load_risk_state()
                    portfolio_value = float(risk_state.get("portfolio_value", 100_000))

                    from utils import get_db as _gdb
                    conn = _gdb()
                    approved = conn.execute(
                        "SELECT ticker, side FROM position_approvals WHERE status='APPROVED'"
                    ).fetchall()
                    conn.close()

                    if not approved:
                        st.info("No approved positions to execute.")
                    else:
                        veto_results = []
                        for row in approved:
                            ticker = row["ticker"]
                            side = row["side"]
                            try:
                                from data.market_data import get_prices as gp
                                px = gp(ticker, 2)
                                price = float(px["close"].iloc[-1]) if not px.empty else 0.0
                                shares = round(portfolio_value * 0.05 / price, 4) if price > 0 else 0

                                from risk.pre_trade import pre_trade_veto
                                ok, reason = pre_trade_veto(ticker, side, shares, price, portfolio_value)
                                veto_results.append({
                                    "ticker": ticker,
                                    "side": side,
                                    "approved": ok,
                                    "reason": reason,
                                })
                            except Exception as e:
                                veto_results.append({"ticker": ticker, "side": side, "approved": False, "reason": str(e)})

                        vetoed = [r for r in veto_results if not r["approved"]]
                        passed = [r for r in veto_results if r["approved"]]

                        if vetoed:
                            st.error(f"{len(vetoed)} trade(s) vetoed:")
                            for v in vetoed:
                                st.markdown(f"- **{v['ticker']}** ({v['side']}): {v['reason']}")

                        if passed:
                            st.success(f"{len(passed)} trade(s) cleared pre-trade check.")
                            from execution.order_manager import execute_approved_trades
                            results = execute_approved_trades(portfolio_value)
                            st.success(f"Executed {len(results)} trade(s).")
                        else:
                            st.warning("No trades cleared pre-trade veto.")
                except Exception as e:
                    st.error(f"Execution failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE III — RISK
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    st.markdown("## Risk Dashboard")

    try:
        import plotly.graph_objects as go
        import plotly.express as px
        PLOTLY_OK = True
    except ImportError:
        PLOTLY_OK = False
        st.warning("Plotly not installed. Install with `pip install plotly`.")

    try:
        from risk.risk_state import load_risk_state
        risk_state = load_risk_state()
    except Exception:
        risk_state = {}

    portfolio_value = float(risk_state.get("portfolio_value", 100_000))
    daily_pnl = float(risk_state.get("daily_pnl", 0.0))
    weekly_pnl = float(risk_state.get("weekly_pnl", 0.0))
    drawdown = float(risk_state.get("drawdown_from_peak", 0.0))

    # ── Circuit breaker bars ──────────────────────────────────────────────────
    st.markdown("### Circuit Breakers")
    cb_cols = st.columns(3)

    def _progress_bar(label: str, value: float, thresholds: tuple, col):
        abs_val = abs(value)
        warn, critical = thresholds
        if abs_val < warn:
            color = "normal"
            status = "OK"
        elif abs_val < critical:
            color = "warning"
            status = "WARNING"
        else:
            color = "error"
            status = "ALERT"

        with col:
            st.markdown(f"**{label}**")
            st.progress(min(abs_val / (critical * 1.5), 1.0))
            sign = "-" if value < 0 else "+"
            st.markdown(f"{sign}{_pct(abs_val)} — _{status}_")

    _progress_bar("Daily P&L", daily_pnl, (0.015, 0.025), cb_cols[0])
    _progress_bar("Weekly P&L", weekly_pnl, (0.04, 0.08), cb_cols[1])
    _progress_bar("Drawdown from Peak", drawdown, (0.04, 0.08), cb_cols[2])

    # ── Tail risk ─────────────────────────────────────────────────────────────
    st.markdown("---")
    tail_cols = st.columns(2)
    with tail_cols[0]:
        st.markdown(f"**VIX:** {_vix_badge(vix_val if 'vix_val' in dir() else 20.0)}", unsafe_allow_html=True)

    with tail_cols[1]:
        try:
            from data.providers import get_credit_spread
            cs = get_credit_spread()
            if cs is not None:
                st.markdown(f"**Credit Spread (HY):** {cs:.2f}%")
            else:
                st.markdown("**Credit Spread:** N/A (set FRED_API_KEY)")
        except Exception:
            st.markdown("**Credit Spread:** N/A")

    # ── Risk decomposition donut ──────────────────────────────────────────────
    st.markdown("---")
    risk_decomp = risk_state.get("risk_decomposition", {"factor_pct": 0.5, "specific_pct": 0.5})
    factor_pct = float(risk_decomp.get("factor_pct", 0.5)) * 100
    specific_pct = float(risk_decomp.get("specific_pct", 0.5)) * 100

    decomp_col, mctr_col = st.columns([1, 2])
    with decomp_col:
        st.markdown("### Risk Decomposition")
        if PLOTLY_OK:
            fig_donut = go.Figure(go.Pie(
                labels=["Factor Risk", "Specific Risk"],
                values=[factor_pct, specific_pct],
                hole=0.55,
                marker_colors=["#6366f1", "#10b981"],
            ))
            fig_donut.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e6f0",
                showlegend=True,
                height=280,
                annotations=[dict(
                    text=f"Factor: {factor_pct:.0f}%<br>Specific: {specific_pct:.0f}%",
                    x=0.5, y=0.5, font_size=12, showarrow=False,
                )],
            )
            st.plotly_chart(fig_donut, use_container_width=True)

    # ── MCTR table ────────────────────────────────────────────────────────────
    with mctr_col:
        st.markdown("### MCTR")
        mctr_data = risk_state.get("mctr", {})
        if mctr_data:
            mctr_rows = []
            for ticker, info in mctr_data.items():
                if isinstance(info, dict):
                    mctr_rows.append({
                        "ticker": ticker,
                        "weight": round(float(info.get("weight", 0)), 4),
                        "mctr": round(float(info.get("mctr", 0)), 6),
                        "flag": "YES" if info.get("disproportionate") else "",
                    })
            if mctr_rows:
                mctr_df = pd.DataFrame(mctr_rows).sort_values("flag", ascending=False)
                st.dataframe(mctr_df, use_container_width=True, hide_index=True)
        else:
            st.info("Run risk check to populate MCTR data.")

    # ── Factor exposure bars ──────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Factor Exposures")
    scored_df_r = _load_scored_df()
    if scored_df_r is not None and not scored_df_r.empty:
        try:
            from portfolio.factor_exposure import get_factor_exposures
            positions_r = _safe(lambda: __import__("portfolio.state", fromlist=["get_positions"]).get_positions(), pd.DataFrame())
            if positions_r is not None and not positions_r.empty and "side" in positions_r.columns:
                longs_r = positions_r[positions_r["side"] == "LONG"]["ticker"].tolist()
                shorts_r = positions_r[positions_r["side"] == "SHORT"]["ticker"].tolist()
                exposures = get_factor_exposures(longs_r, shorts_r, scored_df_r)

                if exposures and PLOTLY_OK:
                    factors_e = list(exposures.keys())
                    long_avgs = [exposures[f]["long_avg"] for f in factors_e]
                    short_avgs = [exposures[f]["short_avg"] for f in factors_e]

                    fig_exp = go.Figure()
                    fig_exp.add_bar(name="Long", x=factors_e, y=long_avgs, marker_color="#10b981")
                    fig_exp.add_bar(name="Short", x=factors_e, y=short_avgs, marker_color="#f43f5e")
                    fig_exp.update_layout(
                        barmode="group",
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font_color="#e0e6f0",
                        height=300,
                        yaxis_title="Avg Score",
                    )
                    st.plotly_chart(fig_exp, use_container_width=True)
        except Exception as e:
            st.info(f"Factor exposure unavailable: {e}")

    # ── Stress tests ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Stress Test Scenarios")
    try:
        from risk.stress_test import run_stress_tests
        positions_s = _safe(lambda: __import__("portfolio.state", fromlist=["get_positions"]).get_positions(), pd.DataFrame())
        if positions_s is not None and not positions_s.empty:
            weights_s = {}
            for _, row in positions_s.iterrows():
                t = row.get("ticker", "")
                shares = float(row.get("shares", 0) or 0)
                price = float(row.get("current_price") or row.get("entry_price", 0) or 0)
                mv = shares * price
                w = mv / portfolio_value if portfolio_value else 0
                weights_s[t] = w if row.get("side") == "LONG" else -w

            stress_results = run_stress_tests(weights_s)
            if stress_results:
                stress_df = pd.DataFrame([{
                    "Scenario": r["scenario_name"],
                    "Long P&L": _pct(r["long_pnl_pct"]),
                    "Short P&L": _pct(r["short_pnl_pct"]),
                    "Total P&L": _pct(r["total_pnl_pct"]),
                } for r in stress_results])

                def _color_pnl(val):
                    try:
                        v = float(val.replace("%", ""))
                        return "color: #10b981" if v > 0 else "color: #f43f5e"
                    except Exception:
                        return ""

                styled_stress = stress_df.style.map(_color_pnl, subset=["Long P&L", "Short P&L", "Total P&L"])
                st.dataframe(styled_stress, use_container_width=True, hide_index=True)
    except Exception as e:
        st.info(f"Stress tests unavailable: {e}")

    # ── Correlation heatmap ───────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Correlation Heatmap")
    try:
        from risk.correlation_monitor import check_correlations, _get_return_series
        positions_c = _safe(lambda: __import__("portfolio.state", fromlist=["get_positions"]).get_positions(), pd.DataFrame())
        if positions_c is not None and not positions_c.empty and len(positions_c) >= 2:
            tickers_c = positions_c["ticker"].tolist()
            rets_c = _get_return_series(tickers_c)
            if not rets_c.empty and PLOTLY_OK:
                corr_matrix = rets_c.corr()
                fig_corr = px.imshow(
                    corr_matrix,
                    color_continuous_scale="RdBu_r",
                    zmin=-1, zmax=1,
                    text_auto=".2f",
                )
                fig_corr.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#e0e6f0",
                    height=400,
                )
                st.plotly_chart(fig_corr, use_container_width=True)

                # Flag pairs > 0.80
                high_pairs = []
                tickers_list = list(corr_matrix.columns)
                for i in range(len(tickers_list)):
                    for j in range(i + 1, len(tickers_list)):
                        c = corr_matrix.iloc[i, j]
                        if abs(c) > 0.80:
                            high_pairs.append(f"{tickers_list[i]}/{tickers_list[j]} = {c:.2f}")
                if high_pairs:
                    st.warning("High correlation pairs (>0.80): " + ", ".join(high_pairs))
    except Exception as e:
        st.info(f"Correlation heatmap unavailable: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE IV — PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[3]:
    st.markdown("## Performance")

    try:
        import plotly.graph_objects as go
        PLOTLY_OK_P = True
    except ImportError:
        PLOTLY_OK_P = False

    # ── Equity curve ──────────────────────────────────────────────────────────
    st.markdown("### Equity Curve")
    log_scale = st.checkbox("Log Scale", value=False)

    try:
        from reporting.tear_sheet import get_equity_curve, get_metrics_vs_spy
        curve_df = get_equity_curve(days=252)

        if curve_df.empty:
            st.info("No equity curve data yet. Portfolio NAV history is needed.")
        elif PLOTLY_OK_P:
            fig_eq = go.Figure()
            fig_eq.add_scatter(
                x=curve_df["date"], y=curve_df["portfolio_value"],
                name="Portfolio", line=dict(color="#6366f1", width=2),
            )
            if "spy_value" in curve_df.columns:
                fig_eq.add_scatter(
                    x=curve_df["date"], y=curve_df["spy_value"],
                    name="SPY", line=dict(color="#7a8fa6", width=1, dash="dot"),
                )
            fig_eq.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e6f0",
                yaxis_type="log" if log_scale else "linear",
                height=350,
                legend=dict(bgcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig_eq, use_container_width=True)
    except Exception as e:
        st.info(f"Equity curve unavailable: {e}")

    # ── Metrics strip ─────────────────────────────────────────────────────────
    try:
        metrics = get_metrics_vs_spy(days=252)
        m_cols = st.columns(7)
        metric_items = [
            ("Sharpe", f"{metrics['sharpe']:.2f}"),
            ("Max DD", _pct(metrics["max_drawdown"])),
            ("Calmar", f"{metrics['calmar']:.2f}"),
            ("Beta", f"{metrics['beta']:.2f}"),
            ("Alpha", _pct(metrics["alpha"])),
            ("Corr SPY", f"{metrics['correlation']:.2f}"),
            ("Win Rate", _pct(metrics["win_rate"])),
        ]
        for col, (label, val) in zip(m_cols, metric_items):
            with col:
                st.markdown(
                    f'<div class="card"><div class="kpi-value" style="font-size:1.4rem">{val}</div>'
                    f'<div class="kpi-label">{label}</div></div>',
                    unsafe_allow_html=True,
                )
    except Exception as e:
        st.info(f"Metrics unavailable: {e}")

    # ── Monthly returns grid ──────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Monthly Returns")
    try:
        from reporting.tear_sheet import get_monthly_returns_grid
        monthly_df = get_monthly_returns_grid()
        if monthly_df.empty:
            st.info("Insufficient history for monthly returns grid.")
        else:
            month_cols_list = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Annual"]
            present = [c for c in month_cols_list if c in monthly_df.columns]

            def _color_return(val):
                if pd.isna(val):
                    return ""
                return "background-color: rgba(16,185,129,0.3)" if val > 0 else "background-color: rgba(244,63,94,0.3)"

            styled_monthly = monthly_df[present].style.map(_color_return).format("{:.2%}", na_rep="-")
            st.dataframe(styled_monthly, use_container_width=True)
    except Exception as e:
        st.info(f"Monthly returns unavailable: {e}")

    # ── Drawdown chart ────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Drawdown")
    try:
        if not curve_df.empty and "drawdown" in curve_df.columns and PLOTLY_OK_P:
            fig_dd = go.Figure()
            fig_dd.add_scatter(
                x=curve_df["date"], y=curve_df["drawdown"] * 100,
                fill="tozeroy",
                fillcolor="rgba(244,63,94,0.3)",
                line=dict(color="#f43f5e"),
                name="Drawdown %",
            )
            fig_dd.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e6f0",
                height=200,
                yaxis_title="%",
            )
            st.plotly_chart(fig_dd, use_container_width=True)
    except Exception as e:
        st.info(f"Drawdown chart unavailable: {e}")

    # ── P&L Attribution stacked bar ───────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Daily P&L Attribution (90 Days)")
    try:
        from reporting.pnl_attribution import get_attribution_history
        attr_df = get_attribution_history(days=90)

        if attr_df.empty:
            st.info("No attribution history. Attribution is computed during daily risk updates.")
        elif PLOTLY_OK_P:
            fig_attr = go.Figure()
            fig_attr.add_bar(x=attr_df["date"], y=attr_df["beta_return"] * 100, name="Beta", marker_color="#3b82f6")
            fig_attr.add_bar(x=attr_df["date"], y=attr_df["sector_return"] * 100, name="Sector", marker_color="#8b5cf6")
            fig_attr.add_bar(x=attr_df["date"], y=attr_df["factor_return"] * 100, name="Factor", marker_color="#f59e0b")
            fig_attr.add_bar(x=attr_df["date"], y=attr_df["alpha_residual"] * 100, name="Alpha", marker_color="#10b981")
            fig_attr.update_layout(
                barmode="stack",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e0e6f0",
                height=300,
                yaxis_title="%",
                legend=dict(bgcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig_attr, use_container_width=True)
    except Exception as e:
        st.info(f"Attribution history unavailable: {e}")

    # ── Sector relative alpha ─────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Sector Relative Alpha")
    try:
        from reporting.sector_performance import get_sector_relative_performance
        sector_df = get_sector_relative_performance(days=90)
        if sector_df.empty:
            st.info("No sector performance data yet.")
        else:
            def _color_alpha(val):
                if pd.isna(val):
                    return ""
                return "color: #10b981" if val > 0 else "color: #f43f5e"

            styled_sector = sector_df.style.map(_color_alpha, subset=["alpha"]).format({
                "portfolio_return": "{:.2%}",
                "etf_return": "{:.2%}",
                "alpha": "{:.2%}",
            })
            st.dataframe(styled_sector, use_container_width=True, hide_index=True)
    except Exception as e:
        st.info(f"Sector performance unavailable: {e}")

    # ── Turnover analytics ────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Turnover Analytics")
    try:
        from reporting.turnover import get_turnover_stats
        to_stats = get_turnover_stats()
        to_cols = st.columns(4)
        to_items = [
            ("Turnover 30d", _pct(to_stats["turnover_30d"])),
            ("Turnover 90d", _pct(to_stats["turnover_90d"])),
            ("Ann. Turnover", _pct(to_stats["turnover_annualized"])),
            ("Est. Tax Drag", _pct(to_stats["est_tax_drag_pct"])),
        ]
        for col, (label, val) in zip(to_cols, to_items):
            with col:
                st.markdown(
                    f'<div class="card"><div class="kpi-value" style="font-size:1.4rem">{val}</div>'
                    f'<div class="kpi-label">{label}</div></div>',
                    unsafe_allow_html=True,
                )
    except Exception as e:
        st.info(f"Turnover stats unavailable: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE V — EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[4]:
    st.markdown("## Execution Monitor")

    # ── Live feed toggle ──────────────────────────────────────────────────────
    live_toggle = st.toggle("Live Feed (auto-refresh every 5s)", value=st.session_state.live_feed)
    st.session_state.live_feed = live_toggle

    if live_toggle:
        time.sleep(5)
        st.rerun()

    # ── Open orders ───────────────────────────────────────────────────────────
    st.markdown("### Open Orders")
    try:
        from execution.order_manager import get_open_orders
        orders_df = get_open_orders()
        if orders_df.empty:
            st.info("No open orders.")
        else:
            st.dataframe(orders_df, use_container_width=True, hide_index=True)
    except Exception as e:
        st.info(f"Open orders unavailable: {e}")

    # ── Recent fills ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Recent Fills (Last 20)")
    try:
        from execution.slippage import get_slippage_dashboard
        fills_df = get_slippage_dashboard()
        if fills_df.empty:
            st.info("No fills recorded.")
        else:
            display_cols = ["timestamp", "ticker", "side", "shares", "limit_price", "fill_price", "slippage_bps"]
            display_cols = [c for c in display_cols if c in fills_df.columns]
            st.dataframe(fills_df[display_cols].head(20), use_container_width=True, hide_index=True)
    except Exception as e:
        st.info(f"Fills data unavailable: {e}")

    # ── Position monitor ──────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Position Monitor")
    try:
        from portfolio.state import get_positions
        pos_df = get_positions()
        if pos_df.empty:
            st.info("No open positions.")
        else:
            display_cols = ["ticker", "side", "shares", "entry_price", "current_price", "unrealized_pnl"]
            display_cols = [c for c in display_cols if c in pos_df.columns]
            pos_view = pos_df[display_cols].copy()

            if "unrealized_pnl" in pos_view.columns and "entry_price" in pos_view.columns:
                pos_view["unrealized_pnl_pct"] = pos_view.apply(
                    lambda r: (r["unrealized_pnl"] / (r["entry_price"] * r.get("shares", 1)))
                    if r.get("entry_price") and r.get("shares") else 0.0,
                    axis=1,
                )

            def _color_pnl_cell(val):
                if pd.isna(val):
                    return ""
                return "color: #10b981" if val > 0 else "color: #f43f5e"

            styled_pos = pos_view.style
            if "unrealized_pnl" in pos_view.columns:
                styled_pos = styled_pos.map(_color_pnl_cell, subset=["unrealized_pnl"])

            st.dataframe(styled_pos, use_container_width=True, hide_index=True)
    except Exception as e:
        st.info(f"Positions unavailable: {e}")

    # ── Short availability ────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Short Availability")
    try:
        from portfolio.state import get_positions as _gp2
        from utils import get_db as _gdb2
        shorts_pos = _gp2()
        if shorts_pos is not None and not shorts_pos.empty and "side" in shorts_pos.columns:
            short_tickers = shorts_pos[shorts_pos["side"] == "SHORT"]["ticker"].tolist()
            if short_tickers:
                conn2 = _gdb2()
                sa_rows = conn2.execute(
                    "SELECT ticker, shortable, easy_to_borrow FROM short_availability WHERE ticker IN ({})".format(
                        ",".join("?" * len(short_tickers))
                    ),
                    short_tickers,
                ).fetchall()
                conn2.close()

                if sa_rows:
                    sa_df = pd.DataFrame([dict(r) for r in sa_rows])
                    sa_df["shortable"] = sa_df["shortable"].apply(lambda x: "YES" if x else "NO")
                    sa_df["easy_to_borrow"] = sa_df["easy_to_borrow"].apply(lambda x: "YES" if x else "NO")
                    st.dataframe(sa_df, use_container_width=True, hide_index=True)
                else:
                    st.info("No short availability data cached.")
            else:
                st.info("No short positions.")
    except Exception as e:
        st.info(f"Short availability unavailable: {e}")

    # ── Slippage stats ────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Slippage Summary (30 Days)")
    try:
        from execution.slippage import get_slippage_stats
        slip = get_slippage_stats()
        slip_cols = st.columns(4)
        slip_items = [
            ("Avg Slippage", f"{slip['avg_bps']:.1f} bps"),
            ("Median", f"{slip['median_bps']:.1f} bps"),
            ("P95", f"{slip['p95_bps']:.1f} bps"),
            ("Total Cost", f"${slip['total_cost_usd']:,.0f}"),
        ]
        for col, (label, val) in zip(slip_cols, slip_items):
            with col:
                st.markdown(
                    f'<div class="card"><div class="kpi-value" style="font-size:1.4rem">{val}</div>'
                    f'<div class="kpi-label">{label}</div></div>',
                    unsafe_allow_html=True,
                )
    except Exception as e:
        st.info(f"Slippage stats unavailable: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE VI — LETTER
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[5]:
    st.markdown("## JARVIS Communications")

    # ── Weekly Commentary ─────────────────────────────────────────────────────
    st.markdown("### Weekly Commentary")
    if st.button("Generate Weekly Commentary"):
        with st.spinner("JARVIS is writing the weekly commentary..."):
            try:
                from reporting.commentary import generate_weekly_commentary
                commentary = generate_weekly_commentary()
                st.session_state["weekly_commentary"] = commentary
            except Exception as e:
                st.error(f"Failed: {e}")

    if "weekly_commentary" in st.session_state:
        st.markdown(
            f'<div class="jarvis-response">{st.session_state["weekly_commentary"]}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # ── LP Letter ─────────────────────────────────────────────────────────────
    st.markdown("### Daily LP Letter")
    if st.button("Generate LP Letter"):
        with st.spinner("JARVIS is drafting the LP letter..."):
            try:
                from reporting.commentary import generate_lp_letter
                letter = generate_lp_letter()
                st.session_state["lp_letter"] = letter
            except Exception as e:
                st.error(f"Failed: {e}")

    if "lp_letter" in st.session_state:
        letter_text = st.session_state["lp_letter"]

        # Display as formatted content
        st.markdown(
            f'<div class="jarvis-response" style="font-family:Georgia,serif; line-height:1.7;">'
            f'{letter_text.replace(chr(10), "<br>")}'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Download options
        dl_cols = st.columns(2)
        with dl_cols[0]:
            st.download_button(
                label="Download as Text",
                data=letter_text,
                file_name=f"lp_letter_{date.today()}.txt",
                mime="text/plain",
            )

        with dl_cols[1]:
            # HTML download
            html_letter = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body {{ font-family: Georgia, serif; max-width: 720px; margin: 60px auto; line-height: 1.7; color: #1a1a2e; }}
h1 {{ font-size: 1.4rem; letter-spacing: 0.1em; border-bottom: 2px solid #1a1a2e; padding-bottom: 8px; }}
footer {{ margin-top: 40px; font-size: 0.8rem; color: #6b7280; border-top: 1px solid #e5e7eb; padding-top: 12px; }}
</style></head><body>
<pre style="white-space:pre-wrap; font-family:inherit;">{letter_text}</pre>
</body></html>"""
            st.download_button(
                label="Download as HTML",
                data=html_letter,
                file_name=f"lp_letter_{date.today()}.html",
                mime="text/html",
            )


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE VII — BACKTEST
# ═══════════════════════════════════════════════════════════════════════════════
with tabs[6]:
    st.markdown("## Backtest")

    backtest_script = Path(__file__).parent.parent / "run_backtest.py"

    with st.form("backtest_form"):
        bt_cols = st.columns(2)
        with bt_cols[0]:
            start_date = st.date_input("Start Date", value=date(2023, 1, 1))
        with bt_cols[1]:
            end_date = st.date_input("End Date", value=date.today())

        submitted = st.form_submit_button("Run Backtest")

    if submitted:
        if not backtest_script.exists():
            st.error(f"Backtest script not found at {backtest_script}")
        elif start_date >= end_date:
            st.error("Start date must be before end date.")
        else:
            with st.spinner("Running backtest... this may take a few minutes."):
                try:
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(backtest_script),
                            "--start", str(start_date),
                            "--end", str(end_date),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=300,
                        cwd=str(backtest_script.parent),
                    )

                    if result.returncode == 0:
                        st.success("Backtest complete.")
                        if result.stdout:
                            st.code(result.stdout, language="text")
                    else:
                        st.error(f"Backtest failed (exit code {result.returncode})")
                        if result.stderr:
                            st.code(result.stderr, language="text")
                        if result.stdout:
                            st.code(result.stdout, language="text")
                except subprocess.TimeoutExpired:
                    st.error("Backtest timed out after 5 minutes.")
                except Exception as e:
                    st.error(f"Failed to run backtest: {e}")
