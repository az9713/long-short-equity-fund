# Analyze a single ticker

Score and AI-analyze one stock without running the full pipeline. Useful for one-off research or debugging the scoring engine on a problematic name.

## Prerequisites

- `python run_data.py` has been run at least once so `daily_prices` and `fundamentals_*` have data for the target ticker.
- For AI analysis: `OPENROUTER_API_KEY` set in `.env`.

## Steps

### 1. Score the ticker

```bash
python run_scoring.py --ticker AAPL
```

The single-ticker mode prints a detailed breakdown:

```
============================================================
  JARVIS Scoring Detail: AAPL
  Sector: Information Technology
============================================================

  Factor Scores (0-100, sector-relative):
  momentum             82.1  ################
  value                34.5  ######
  quality              91.3  ##################
  growth               67.2  #############
  revisions            74.8  ##############
  short_interest       58.0  ###########
  insider              45.2  #########
  institutional        72.1  ##############

  Composite Score:  68.4
  Signal:           HOLD

  Quality Diagnostics:
    Piotroski F:    8
    Altman Z:       6.42  (SAFE)
```

Note that the ticker is **scored against its actual sector group**, not just the dev universe. JARVIS adds the requested ticker to the universe set internally so the sector-relative ranking has full peer comparison.

### 2. Run the four AI analyzers

```bash
python run_analysis.py --ticker AAPL
```

Output:

```
  Mode: single ticker [AAPL]
  Analyzing AAPL...
    [earnings] ok
    [filing] ok
    [risk] ok
    [insider] ok
```

The four analyzers each return a structured JSON dict. To inspect them directly:

```bash
sqlite3 data/fund.db "SELECT analyzer, output FROM ai_cache WHERE ticker='AAPL' ORDER BY created_at DESC LIMIT 4;"
```

Each row's `output` is the JSON dict from that analyzer.

### 3. View the per-ticker report

`run_analysis.py --ticker` triggers `report_generator.py` if the ticker is in the scored universe CSV. Output goes to:

```
output/reports/AAPL_<date>.md
```

The report combines:

- Quant factor breakdown
- Quality diagnostics
- All four AI analyzer summaries
- A `combined_score` if AI ran

Open the file in any Markdown viewer.

### 4. Review in the dashboard

```bash
python run_dashboard.py
```

In Tab II (Research), filter by ticker. The factor radar and the AI analyses appear inline. The dashboard reads from the same SQLite cache as step 2 — no re-run required.

## Verification

After step 1, the ticker should appear as a row in `output/scored_universe_latest.csv` with non-null factor scores. After step 2, four rows for that ticker should exist in the `ai_cache` table.

## Common gotchas (this task)

**Ticker not found in scoring CSV after `--ticker AAPL`.** Single-ticker mode runs scoring for that one ticker only — the CSV is overwritten with one row. To avoid clobbering a full-universe run, copy the existing CSV first:

```bash
cp output/scored_universe_latest.csv output/scored_universe_full.csv
python run_scoring.py --ticker AAPL
# scored_universe_latest.csv now has only AAPL
```

**All factor scores are 50.** Sector group has fewer than 3 tickers (the `MIN_GROUP_SIZE` threshold in `factors/base.py`). This happens when running in dev mode and the ticker's sector is sparse. Run on the full universe (`dev_mode: false`) for meaningful sector-relative scoring.

**AI analyzer returns "no data."** The underlying source table is empty for that ticker. Check:
- `filing_analyzer` needs `sec_filings` rows. Re-run `python run_data.py` without `--no-filings`.
- `earnings_analyzer` needs `earnings_transcripts`. With no FMP key, transcripts are sparse — the analyzer falls back to the earnings calendar metadata, which still works for major large-caps.
- `insider_analyzer` needs `insider_transactions`. Re-run `python run_data.py` without `--no-13f` is irrelevant — that's institutional data; insiders come from SEC, controlled by `--no-filings`.

## See also

- [Scoring engine](../concepts/scoring-engine.md) — what each factor measures
- [AI analysis](../concepts/ai-analysis.md) — what each analyzer does
