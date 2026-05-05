# Onboarding

A patient walkthrough of JARVIS for someone who hasn't worked on a hedge-fund stack before. If you already know what long-short equity is and just want to run things, the [quickstart](quickstart.md) is faster.

## What is a long-short equity fund?

A long-short equity fund buys stocks it thinks will go up (long positions) and short-sells stocks it thinks will go down (short positions). If the longs and shorts are sized to roughly cancel each other's market exposure, the fund's P&L depends mainly on the *relative* performance of its longs versus its shorts — not on the overall market direction.

That last sentence is the whole point. A long-only fund needs the market to go up to make money. A market-neutral long-short fund makes money when its longs *outperform* its shorts, even if both go down together.

JARVIS implements this idea on a small, single-machine scale. It picks the longs and shorts with quantitative factor scoring + AI analysis, sizes them with portfolio optimization, monitors risk, and executes through Alpaca paper trading.

## What's a "factor"?

A factor is a quantifiable property of a stock that has historically predicted future returns. Classic examples:

- **Momentum** — stocks that went up over the past year tend to keep going up over the next month
- **Value** — stocks that are cheap on price-to-earnings or price-to-book tend to outperform expensive ones
- **Quality** — companies with stable earnings, low debt, and high return on capital tend to outperform
- **Insider activity** — stocks where executives are buying their own company tend to outperform

Each factor produces a single number per stock. Combine them (weighted) and you get a composite score. Buy the highest-scored stocks, short the lowest-scored. That's the quant skeleton of JARVIS's Layer 2.

## The seven layers, in plain English

| # | Layer | What a junior PM would say it does |
|---|-------|------------------------------------|
| 1 | Data | "Pull every number we need into one database — prices, balance sheets, who's selling shares, who's buying." |
| 2 | Scoring | "Rank every stock on eight factors. Spit out the top longs and top shorts." |
| 3 | AI analysis | "Have an analyst read the recent filings, the earnings call, and the insider activity. Summarize each one." |
| 4 | Portfolio | "Decide how many dollars to put in each name so the book is balanced and risk-controlled." |
| 5 | Risk | "Make sure no single name, sector, or factor blows us up. Halt trading if we breach a loss limit." |
| 6 | Execution | "Place the trades on Alpaca paper trading as limit orders. Don't slip more than a few bps." |
| 7 | Reporting | "Show me a dashboard and write me a weekly letter." |

## Why does each layer write to SQLite?

State has to live somewhere. A streaming pipeline (each layer pushes directly into the next) would be elegant but means re-running Layer 6 forces re-running Layer 1. JARVIS's design favors *idempotent layers reading and writing a shared database*. You can re-run any single layer in isolation, and tomorrow's run is just another iteration.

The database file is `data/fund.db`. You can open it with any SQLite browser to inspect everything — there are no hidden state files (with one exception: `risk/halt.lock`, a marker file written when the kill-switch fires).

## Why isn't AI used to pick stocks directly?

You could imagine handing an LLM the factor scores and asking it to pick the longs. JARVIS doesn't, because:

1. **Determinism.** Two runs of the same scoring engine on the same data produce identical results. Two runs of an LLM don't.
2. **Auditability.** "Why is AAPL a LONG?" has a deterministic answer (factor breakdown) you can verify against the source data.
3. **Cost and speed.** Scoring 500 stocks across 8 factors takes seconds. Asking an LLM to do it costs money and time.

The LLM is therefore used for *qualitative* additions — reading the latest 10-K, summarizing the earnings call, flagging unusual insider activity — that the quant engine can't easily encode. The combined score is still anchored to the deterministic quant composite.

## Why OpenRouter and not the Anthropic API directly?

Cost. With the configured free Gemini 2.0 Flash model on OpenRouter, every analyzer call costs effectively $0. The Anthropic API would charge per token. See [ADR-001](../architecture/adr/001-openrouter-over-anthropic-api.md).

If you want to swap to Claude or GPT-4, you only need to change two lines in `config.yaml`: the `ai.model` and `ai.base_url` (set the latter to `https://api.anthropic.com/v1` or `https://api.openai.com/v1`).

## Why is execution paper-only?

Because this is a personal-scale research project. Live trading means real money, account compliance, and the possibility of losing it all to a bug. Paper trading uses the same Alpaca API but against a simulated account.

Live mode exists in the code (`config.yaml: execution.mode: live`) but is gated by an explicit environment variable: `ALPACA_LIVE_CONFIRMED=YES_I_UNDERSTAND_THE_RISKS`. The variable's verbosity is intentional. See [ADR-003](../architecture/adr/003-paper-trading-only.md).

## A realistic end-to-end day

Suppose it's Tuesday evening, market just closed. A typical run looks like this.

You start by refreshing the data with `python run_data.py`. This takes 15–30 minutes on the full S&P 500. It pulls today's prices from Yahoo Finance, refreshes any fundamentals that the company filed, and grabs new SEC Form 4 (insider) filings. Everything writes into `fund.db`.

You then run `python run_scoring.py`. This takes about 90 seconds. It computes momentum, value, quality, growth, revisions, short-interest, insider, and institutional factors for every stock, ranks them within their GICS sector, and writes the result to `output/scored_universe_latest.csv`. The script also detects "crowding" — situations where the same factor pairs are showing unusually high return correlation, suggesting many other quant funds are running the same trade.

Next, AI analysis: `python run_analysis.py`. This takes 5–15 minutes depending on rate limits. For the top 20 longs and top 20 shorts in the scoring CSV, JARVIS calls four LLM analyzers each:

- **Filing analyzer** reads the latest 10-K/10-Q and pulls out forward-looking statements, risk factors, and accounting changes.
- **Earnings analyzer** reads the latest call transcript (or summary if no transcript) and flags margin guidance, capex changes, and tone.
- **Risk analyzer** synthesizes a risk score from the filing and the recent stock action.
- **Insider analyzer** looks at recent Form 4 trades and flags clusters or unusual sizes.

The four outputs feed into a sector-level synthesis (e.g., "Tech outlook neutral — top long NVDA, top short INTC") and finally into a *combined score* per ticker that blends quant (70%) and AI (30%).

Then portfolio construction: `python run_portfolio.py --whatif`. JARVIS pulls the candidate longs and shorts, runs Mean-Variance Optimization with constraints (per-position 5%, per-sector 25%, gross 150%, net beta ±15%), and prints the proposed weights and trades. You inspect them. If they look reasonable, run `python run_portfolio.py --rebalance` to commit them as `PENDING` rows in the `position_approvals` table.

Risk check: `python run_risk_check.py`. Prints a dashboard — circuit breakers, factor exposures, correlation, MCTR. If anything is yellow, you investigate.

You then promote `PENDING` to `APPROVED` (manually, with a SQL update or a small script). This is the human-in-the-loop step.

Execution: `python run_execution.py --execute`. JARVIS runs the eight-check pre-trade veto on every approved trade. Any that pass become Alpaca limit orders, sized as a percentage of the portfolio value the broker reports. Slippage is tracked per fill.

Finally: `python run_dashboard.py`. Open `http://localhost:8502`. Tab I shows the new positions, Tab II the latest factor breakdowns, Tab III the risk dashboard, Tab IV the P&L attribution, Tab V the order book and slippage stats, Tab VI the weekly investor letter (regenerated by Layer 7), Tab VII the backtest console.

## Where to go next

| If you want to | Read |
|----------------|------|
| Run the loop above for real | [Run the full pipeline](../guides/run-the-full-pipeline.md) |
| Understand a layer in depth | The matching `concepts/X.md` doc |
| Look up a config field or CLI flag | [Reference](../reference/cli.md) |
| Debug something that broke | [Troubleshooting](../troubleshooting/common-issues.md) |
