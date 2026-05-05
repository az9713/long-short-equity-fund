# Switch to the full universe

Move from the 10-ticker dev mode to the full S&P 500 (~503 tickers).

## Prerequisites

- Quickstart completed in dev mode at least once.
- ~3 GB of free disk for prices and fundamentals.
- Stable network — the full data refresh hits external APIs ~5,000 times.

## Steps

### 1. Edit `config.yaml`

Set:

```yaml
dev_mode: false
```

`dev_tickers` can stay — it's only consulted when `dev_mode: true`.

### 2. Run a fresh data pull

```bash
python run_data.py
```

The first full pull takes 15–30 minutes:

| Step | Approximate time |
|------|------------------|
| Universe (Wikipedia) | <5s |
| Market prices (yfinance, 5y history) | 5–10 min |
| Fundamentals (yfinance, full statements) | 5–10 min |
| SEC filings (10-K, 10-Q, 8-K) | 3–5 min |
| Institutional 13F (SEC EDGAR) | 2–4 min |
| Short interest (FINRA) | <1 min |
| Estimates | 2–4 min |
| Earnings calendar | <1 min |

Subsequent runs are incremental — only new prices and recent filings are fetched, taking ~3–5 minutes.

If you hit a rate limit:

- yfinance: wait 5 minutes, re-run. The script is idempotent (upserts).
- SEC EDGAR: confirm `SEC_USER_AGENT_EMAIL` is a real address in `.env`. EDGAR throttles unidentified scrapers heavily.

### 3. Score the full universe

```bash
python run_scoring.py
```

Takes ~90 seconds on the full S&P 500 (the parallel ThreadPoolExecutor in `factors/composite.py` runs 8 worker threads).

Expected output:

```
Universe scored:  503 tickers
LONG candidates:  ~50
SHORT candidates: ~50
```

The signal counts depend on composite-score thresholds. Default thresholds give roughly 10% LONG and 10% SHORT; the rest are HOLD.

### 4. Verify factor differentiation

In dev mode, sector groups of 1–2 tickers default to score 50 (sector median), and most factors don't differentiate. In full mode, every sector has 20+ peers, and you should see meaningful spread.

Spot-check:

```bash
python run_scoring.py --sector "Information Technology"
```

Top and bottom of the printed list should differ on most factors.

### 5. Re-run the rest of the pipeline

```bash
python run_analysis.py
python run_portfolio.py --whatif
python run_risk_check.py
```

The AI analysis cost is ~160 LLM calls regardless of universe size (top 20 longs + top 20 shorts × 4 analyzers). Universe expansion does not increase Layer 3 cost.

Portfolio construction now selects from a larger candidate set; expect more diverse sector and factor exposures.

## Verification

After step 3:

- `output/scored_universe_latest.csv` should have ~503 rows.
- `python run_scoring.py --ticker AAPL` should show factor scores spanning the full 0–100 range across the eight factors (not all clustered around 50).
- The dashboard's Research tab should show a fully populated factor heatmap.

## Going back to dev mode

```yaml
dev_mode: true
```

The 10 dev tickers are a strict subset of the S&P 500 (assuming you didn't customize `dev_tickers`), so the existing `daily_prices` and `fundamentals_*` rows still apply. No re-fetch needed. Run:

```bash
python run_scoring.py
```

The CSV regenerates with only the 10 tickers.

## Common gotchas (this task)

**SEC fetches all return 403.** `SEC_USER_AGENT_EMAIL` is unset or still the placeholder. Edit `.env`:

```
SEC_USER_AGENT_EMAIL=your_real@email.com
```

EDGAR will block any user-agent containing the word `example` or known placeholder strings.

**yfinance returns empty data for 50+ tickers.** Most likely a transient rate limit. Wait 5 minutes and re-run. yfinance's free tier is fragile; if persistent, consider using `POLYGON_API_KEY` (uncomment in `.env.example` and set; `data/providers.py` will route to Polygon when the key is present).

**Scoring takes 5+ minutes.** Disable factors you don't want by setting their weight to 0 in `config.yaml: scoring.weights`. Alternatively, drop the per-ticker concurrency in `factors/composite.py: ThreadPoolExecutor(max_workers=8)` if your machine is throttling.

**Portfolio construction runs out of memory.** The MVO covariance matrix is 503×503 — fits comfortably in 1 GB. If you hit OOM, you're likely conflating the factor-risk-model regression matrix (which can be larger). Reduce `_COV_DAYS` in `portfolio/mvo_optimizer.py` from 120 to 60.

## See also

- [Run the full pipeline](run-the-full-pipeline.md) — what to do after switching
- [Configuration](../reference/configuration.md) — `dev_mode`, `dev_tickers`, `universe.*`
