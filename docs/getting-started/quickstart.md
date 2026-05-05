# Quickstart

Get JARVIS running end-to-end against a 10-ticker dev universe in about 15 minutes.

This guide assumes you've already met the [prerequisites](prerequisites.md). The dev mode universe is hard-coded to ten large-cap names (`AAPL, MSFT, GOOGL, AMZN, NVDA, JPM, JNJ, UNH, XOM, V`) so you can validate every layer fast before scaling up.

## 1. Install dependencies

From the `ls_equity_fund/` directory:

```bash
pip install -r requirements.txt
```

Verify:

```bash
python -c "import yfinance, pandas, scipy, openai, alpaca; print('ok')"
```

Expected output:

```
ok
```

## 2. Configure environment

Copy the template and edit the new `.env` file:

```bash
cp .env.example .env
```

Open `.env` and fill in the keys you have. Minimum to get started:

```
SEC_USER_AGENT_EMAIL=your_real_email@example.com
SEC_USER_AGENT_NAME=LS_Equity_Research

OPENROUTER_API_KEY=sk-or-v1-...
ALPACA_API_KEY=PK...
ALPACA_SECRET_KEY=...
```

You can leave any of OpenRouter or Alpaca empty for now — the affected layer will skip gracefully and the rest of the pipeline still runs.

## 3. Confirm dev mode is on

Open `config.yaml` and confirm:

```yaml
dev_mode: true
dev_tickers: ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "JPM", "JNJ", "UNH", "XOM", "V"]
```

This is the default. With `dev_mode: true`, every layer operates on these ten tickers only.

## 4. Run the data layer

```bash
python run_data.py --dev --no-filings --no-13f
```

The `--no-filings` and `--no-13f` flags skip the slowest steps for first-run validation. Expected output ends with:

```
JARVIS Data Refresh Complete
  Tickers updated:        10
  Price bars stored:      ~25,000
  Insider transactions:   0  (skipped — no SEC fetch)
  Filings cached:         0  (skipped)
  Elapsed time:           ~30s
```

The SQLite database is created at `data/fund.db`.

## 5. Score the universe

```bash
python run_scoring.py
```

Expected output ends with:

```
  Universe scored:  10 tickers
  LONG candidates:  ~3
  SHORT candidates: ~3

  Top 5 LONG Candidates:
  Ticker     Score  Sector
  ...

  Output saved to: output/scored_universe_latest.csv
```

Inspect the CSV:

```bash
head -5 output/scored_universe_latest.csv
```

Each row has factor scores (0–100), a composite, a signal (`LONG`/`SHORT`/`HOLD`), and quality diagnostics (Piotroski F, Altman Z).

## 6. Launch the dashboard

```bash
python run_dashboard.py
```

Open `http://localhost:8502` in your browser. You will see seven tabs:

```
I · PORTFOLIO   II · RESEARCH   III · RISK   IV · PERFORMANCE
V · EXECUTION   VI · LETTER     VII · BACKTEST
```

The Research tab shows the scored universe you just produced. Other tabs will be sparse until you run more layers.

## What happened

You built a small SQLite database of prices and fundamentals for ten stocks, ran an eight-factor sector-relative scoring engine over them, and surfaced the results in a Streamlit dashboard. The scoring CSV is the input that the AI analysis (Layer 3) and portfolio construction (Layer 4) layers consume next.

## Next steps

| To do this | Read |
|------------|------|
| Run the full 7-layer pipeline | [Run the full pipeline](../guides/run-the-full-pipeline.md) |
| Understand what just happened conceptually | [Onboarding](onboarding.md) |
| Score a single ticker in detail | [Analyze a single ticker](../guides/analyze-a-single-ticker.md) |
| Add SEC filings and insider data | Re-run with `python run_data.py --dev` (drop `--no-filings`/`--no-13f`) |
| Move to the full S&P 500 | [Switch to the full universe](../guides/switch-to-full-universe.md) |
