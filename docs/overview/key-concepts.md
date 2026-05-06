# Key concepts

Definitions for every term used elsewhere in the docs. Sorted alphabetically.

## Portfolio and position terms

**Composite score** — A single 0–100 number per ticker produced by Layer 2, combining all eight factor scores using the weights in `config.yaml: scoring.weights`. Higher is better for longs, lower is better for shorts.

**Combined score** — A blend of the quant composite (Layer 2) and AI analyses (Layer 3). Produced only when AI analyses are available; otherwise equals the composite.

**Conviction-tilt optimizer** — A simple ranking-based portfolio optimizer that allocates more weight to higher-scoring names and applies hard caps (position, sector, beta). Used as a fallback when MVO is infeasible. See [portfolio construction](../concepts/portfolio-construction.md).

**Crowding** — The state where the same long and short ideas are being held by many funds at once, raising the risk of correlated unwinds. JARVIS detects crowding by tracking factor return correlations and IRRs. See `factors/crowding.py`.

**Dev mode** — A reduced 10-ticker universe (`AAPL, MSFT, NVDA, INTC, JNJ, UNH, LLY, JPM, GS, BAC`) used for fast end-to-end validation. Three sectors with ≥3 tickers each (IT, Health Care, Financials) so sector-relative scoring produces meaningful spreads. Toggled via `dev_mode: true` in `config.yaml` or `--dev` flag on `run_data.py`.

**Gross exposure** — Sum of absolute position weights. A 60% long / 40% short portfolio has 100% gross. Capped at `gross_limit` in config (default 1.50, i.e. 150%).

**MVO** — Mean-Variance Optimization. Markowitz's quadratic optimization of expected return minus a risk penalty. JARVIS implements it via `scipy.optimize.minimize` (SLSQP) in `portfolio/mvo_optimizer.py`. Falls back to conviction-tilt if infeasible.

**Net exposure** — Sum of signed position weights (longs positive, shorts negative). A 60% long / 40% short portfolio has 20% net. Capped at `net_limit` (default 10%).

**Net beta** — Net market beta of the portfolio. Capped at `max_beta` (default 0.15) to keep the book close to market-neutral.

**Position approval** — A pending trade in the `position_approvals` table. Trades sit as `PENDING` after Layer 4, must be promoted to `APPROVED` (manually or via a tool) before Layer 6 will execute them.

## Factor terms

**Eight factors** — Momentum, Value, Quality, Growth, Revisions, Short interest, Insider, Institutional. Each has 2–4 sub-metrics that roll up to a single 0–100 score.

**Factor return** — The daily return of a long-top-quintile / short-bottom-quintile portfolio for one factor. Used in crowding detection.

**Piotroski F-Score** — A 9-point fundamental quality checklist (profitability, leverage, operating efficiency). Surfaced as a quality diagnostic alongside the quality factor score.

**Altman Z-Score** — A bankruptcy-risk metric. Three labels: `SAFE` (>2.99), `GREY` (1.81–2.99), `DISTRESS` (<1.81). Surfaced as a quality diagnostic.

**Sector-relative ranking** — Every factor score is computed by ranking tickers within their GICS sector, not against the full universe. A high quality score means high quality *for that sector*. Avoids spurious cross-sector comparisons (e.g., banks vs. software).

**Signal** — `LONG`, `SHORT`, or `NEUTRAL` per ticker. Assigned by `factors/composite.py` as the top/bottom 20% of composite-score percentile within the universe.

**Subfactor** — A sub-metric within a factor. Example: the value factor has subfactors `pe_ratio`, `pb_ratio`, `ev_ebitda`, `fcf_yield`. Each is computed in its own factor module.

## Risk terms

**Circuit breaker** — A hard rule that blocks new positions when a loss limit is hit. Three thresholds: daily, weekly, drawdown-from-peak. Implemented in `risk/circuit_breakers.py`. The kill-switch level writes a `halt.lock` file that all execution paths check.

**Factor risk model** — A Barra-style cross-sectional regression of stock returns on factor exposures, used to decompose portfolio risk into factor risk + specific risk. See `risk/factor_risk_model.py`.

**Halt lock** — The file `risk/halt.lock`. Its presence blocks all new trades. Cleared with `python run_risk_check.py --clear-halt`.

**Kill switch** — The most severe circuit-breaker action. Writes the halt lock and aborts in-flight execution. Triggered by daily P&L below `daily_halt_limit` (default −2.5%) or drawdown beyond `drawdown_limit` (default −8%).

**MCTR** — Marginal Contribution to Risk. The change in portfolio volatility from a 1% weight increase in a position. Used to flag positions that contribute disproportionately to risk relative to their weight.

**Pre-trade veto** — Eight checks run before any trade leaves the system: halt lock, earnings blackout, liquidity (5% of ADV), position size, sector exposure, gross exposure, net beta, correlation. See `risk/pre_trade.py`.

**Stress test** — A counterfactual scenario applied to current weights. Six scenarios: 2008 GFC, 2020 COVID, 2022 inflation/rate-hike, plus three synthetic (beta-1 shock, liquidity stress, factor crowding unwind).

**VIX regime** — Three regimes used by the tail-risk monitor: `LOW` (VIX < 25), `ELEVATED` (25–33), `HIGH` (>33). Drives position-size reduction and ultimately a halt.

## Execution terms

**ADV** — Average Daily Volume in dollars. Single trades are vetoed if they exceed 5% of ADV (config: `max_order_pct_adv` for the looser execution check, hard-coded 5% in pre-trade).

**Alpaca paper trading** — Free Alpaca account that executes simulated orders against real prices via the same API as live trading. Set via `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` in `.env`.

**Limit order** — All JARVIS orders are limit orders, never market. Limit price is set 10 bps above last close for buys, 10 bps below for sells.

**Slippage** — The difference between the limit price and the actual fill price, measured in bps. Tracked per fill in the `fills` table; rolled up into 30-day stats by `execution/slippage.py`.

## AI analysis terms

**Analyzer** — One of four LLM-driven modules in `analysis/`: `earnings_analyzer`, `filing_analyzer`, `risk_analyzer`, `insider_analyzer`. Each returns structured JSON for one ticker.

**OpenRouter** — A unified API gateway to many LLM providers. JARVIS uses it via the `openai` Python SDK with a custom `base_url`. Default model is `openai/gpt-oss-20b:free`. See [ADR-001](../architecture/adr/001-openrouter-over-anthropic-api.md).

**Cost tracker** — `analysis/cost_tracker.py`. Records token usage per call to a JSON file so the cost ceiling in config can stop runaway runs. With the free Gemini model, real cost is $0.

**Sector synthesis** — `analysis/sector_analysis.py`. Aggregates per-ticker analyses into a sector-level outlook (`top_long_idea`, `top_short_idea`, `sector_outlook`).

## Project nouns

**JARVIS** — The AI analyst persona. Used in dashboard branding and analyzer prompts.

**Meridian Capital Partners** — The fund's public-facing name in reports and the dashboard. Both names are configurable in `config.yaml: fund`.

**Layer** — One of seven major subsystems. Each layer is a Python package and has its own `run_*.py` entry point.

**fund.db** — The SQLite database at `data/fund.db`. The single source of truth for everything: universe, prices, fundamentals, filings, positions, approvals, fills, slippage, vetoes.

**`output/scored_universe_latest.csv`** — The artifact produced by Layer 2. Layer 3 and Layer 4 read it. Replaced on every scoring run.
