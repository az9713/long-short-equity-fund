import os
from datetime import datetime
from pathlib import Path
import pandas as pd
from utils import get_logger
from analysis.cache import get_latest_for_ticker

log = get_logger(__name__)

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output"


def _fmt(val, default="N/A"):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return str(val)


def _score_bar(score, max_score=10):
    """Visual bar for numeric scores."""
    if score is None:
        return "N/A"
    filled = int((score / max_score) * 10)
    return f"{'#' * filled}{'.' * (10 - filled)} {score}/10"


def generate_report(ticker: str, scored_row: pd.Series) -> str:
    cache = get_latest_for_ticker(ticker)
    earnings = cache.get("earnings")
    filing = cache.get("filing")
    risk = cache.get("risk")
    insider = cache.get("insider")

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    composite = _fmt(scored_row.get("composite"))
    combined = _fmt(scored_row.get("combined_score"))
    signal = _fmt(scored_row.get("signal"))
    sector = _fmt(scored_row.get("sector"))
    has_ai = bool(cache)

    lines = []
    lines.append(f"# {ticker} — Research Report")
    lines.append(f"**Generated:** {now_str}  ")
    lines.append(f"**Sector:** {sector}  ")
    lines.append(f"**Signal:** {signal}  ")
    lines.append(f"**Quant Score (Layer 2):** {composite}/100  ")
    lines.append(f"**Combined Score (L2+L3):** {combined}/100  ")
    lines.append(f"**AI Analysis Available:** {'Yes' if has_ai else 'No'}")
    lines.append("")

    # --- Quantitative factor scores ---
    lines.append("## Quantitative Factor Scores")
    lines.append("| Factor | Score |")
    lines.append("|--------|-------|")
    for factor in ["momentum", "value", "quality", "growth", "revisions",
                   "short_interest", "insider", "institutional"]:
        val = scored_row.get(factor)
        lines.append(f"| {factor.replace('_', ' ').title()} | {_fmt(val)} |")
    lines.append("")

    piotroski = scored_row.get("piotroski_f")
    altman_z = scored_row.get("altman_z")
    altman_label = scored_row.get("altman_label")
    if piotroski is not None or altman_z is not None:
        lines.append("### Quality Diagnostics")
        lines.append(f"- Piotroski F-Score: {_fmt(piotroski)}")
        lines.append(f"- Altman Z-Score: {_fmt(altman_z)} ({_fmt(altman_label)})")
        lines.append("")

    # --- Earnings Call Analysis ---
    if earnings:
        lines.append("## Earnings Call Analysis")
        lines.append(f"**Summary:** {_fmt(earnings.get('one_line_summary'))}")
        lines.append("")
        lines.append("### Scores")
        score_keys = [
            ("management_confidence", "Management Confidence"),
            ("revenue_guidance", "Revenue Guidance"),
            ("margin_trajectory", "Margin Trajectory"),
            ("competitive_position", "Competitive Position"),
            ("risk_factors", "Risk Factors"),
            ("capital_allocation", "Capital Allocation"),
        ]
        for key, label in score_keys:
            val = earnings.get(key)
            lines.append(f"- **{label}:** {_score_bar(val)}")
        lines.append("")
        lines.append(f"**Bull Case:** {_fmt(earnings.get('bull_case'))}")
        lines.append("")
        lines.append(f"**Bear Case:** {_fmt(earnings.get('bear_case'))}")
        lines.append("")
        key_quotes = earnings.get("key_quotes", [])
        if key_quotes:
            lines.append("### Key Quotes")
            for q in key_quotes:
                lines.append(f'> "{q}"')
            lines.append("")

    # --- Filing / Forensic Analysis ---
    if filing:
        lines.append("## Forensic Accounting Review")
        lines.append(f"**Summary:** {_fmt(filing.get('one_line_summary'))}")
        lines.append("")
        eq = filing.get("earnings_quality_score")
        bs = filing.get("balance_sheet_score")
        lines.append(f"- **Earnings Quality:** {_score_bar(eq)}")
        lines.append(f"- **Balance Sheet:** {_score_bar(bs)}")
        lines.append(f"- **Risk Level:** {_fmt(filing.get('risk_level'))}")
        lines.append(f"- **Accruals:** {_fmt(filing.get('accruals_assessment'))}")
        lines.append("")
        red_flags = filing.get("red_flags", [])
        if red_flags:
            lines.append("### Red Flags")
            for f_ in red_flags:
                lines.append(f"- {f_}")
            lines.append("")
        green_flags = filing.get("green_flags", [])
        if green_flags:
            lines.append("### Green Flags")
            for f_ in green_flags:
                lines.append(f"- {f_}")
            lines.append("")

    # --- Risk Analysis ---
    if risk:
        lines.append("## 10-K Risk Analysis")
        lines.append(f"**Summary:** {_fmt(risk.get('one_line_summary'))}")
        lines.append("")
        lines.append(f"- **Risk Severity:** {_fmt(risk.get('risk_severity'))}")
        lines.append(f"- **Boilerplate Estimate:** {_fmt(risk.get('boilerplate_percentage'))}%")
        lines.append("")
        material = risk.get("material_risks", [])
        if material:
            lines.append("### Material Risks")
            for r in material:
                lines.append(f"- {r}")
            lines.append("")
        new_risks = risk.get("new_risks", [])
        if new_risks:
            lines.append("### New / Emerging Risks")
            for r in new_risks:
                lines.append(f"- {r}")
            lines.append("")

    # --- Insider Activity ---
    if insider:
        lines.append("## Insider Activity (90 Days)")
        lines.append(f"**Summary:** {_fmt(insider.get('one_line_summary'))}")
        lines.append("")
        lines.append(f"- **Signal:** {_fmt(insider.get('signal_strength'))}")
        lines.append(f"- **Confidence:** {_fmt(insider.get('confidence'))}/10")
        lines.append(f"- **Reasoning:** {_fmt(insider.get('reasoning'))}")
        lines.append("")
        key_txns = insider.get("key_transactions", [])
        if key_txns:
            lines.append("### Key Transactions")
            for t in key_txns:
                lines.append(f"- {t}")
            lines.append("")

    # --- Footer ---
    lines.append("---")
    lines.append("*Generated by JARVIS / Meridian Capital Partners — for internal use only.*")

    return "\n".join(lines)


def run_report_generation(scored_df: pd.DataFrame):
    if scored_df.empty:
        log.warning("No tickers in scored_df — skipping report generation")
        return

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    report_dir = OUTPUT_DIR / f"reports_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"Writing reports to {report_dir}")

    # Determine if ticker is in index or column
    if scored_df.index.name == "ticker":
        df_iter = scored_df.reset_index()
    else:
        df_iter = scored_df

    # Only generate for LONG and SHORT candidates
    candidates = df_iter[df_iter["signal"].isin(["LONG", "SHORT"])]
    if candidates.empty:
        log.warning("No LONG/SHORT candidates in scored_df")

    for _, row in candidates.iterrows():
        ticker = row.get("ticker")
        if not ticker:
            continue
        try:
            md = generate_report(ticker, row)
            path = report_dir / f"{ticker}.md"
            path.write_text(md, encoding="utf-8")
            log.info(f"Report written: {path}")
        except Exception as e:
            log.error(f"Failed to generate report for {ticker}: {e}")

    log.info(f"Report generation complete: {len(candidates)} reports in {report_dir}")
    print(f"  Reports saved to: {report_dir}")
