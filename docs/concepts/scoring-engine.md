# The scoring engine

Layer 2. Reads `fund.db`, computes eight factor scores per ticker, ranks them within sector, and produces `output/scored_universe_latest.csv`.

## What it is

A package of twelve modules under `factors/` plus the `run_scoring.py` entry point. Each factor module owns one factor and its 2–4 subfactors.

## Module map

| Module | Factor | Default weight | Higher score means |
|--------|--------|----------------|---------------------|
| `factors/momentum.py` | Momentum | 0.20 | Strong recent price action |
| `factors/value.py` | Value | 0.15 | Cheap on multiples |
| `factors/quality.py` | Quality | 0.20 | Stable, profitable, low-debt |
| `factors/growth.py` | Growth | 0.10 | Revenue/EPS growing |
| `factors/revisions.py` | Revisions | 0.15 | Analysts raising estimates |
| `factors/short_interest.py` | Short interest | 0.05 | Low short interest (longs); high (shorts) |
| `factors/insider.py` | Insider | 0.10 | Net insider buying |
| `factors/institutional.py` | Institutional | 0.05 | Concentrated, smart-money holders |

Plus three orchestration modules:

| Module | Role |
|--------|------|
| `factors/base.py` | Sector normalization, sector-relative percentile ranking, winsorization, safe arithmetic helpers |
| `factors/regime_weights.py` | Optional regime-conditional weighting (off by default) |
| `factors/composite.py` | Orchestrator: calls every factor, computes composite, assigns LONG/SHORT/HOLD signal, writes CSV |
| `factors/crowding.py` | Tracks factor return correlations and IRRs; flags crowded pairs |

## How scoring works

The orchestrator `factors/composite.py` runs in three passes per ticker:

```
                ┌── 8 factor modules (each computes raw subfactor metrics)
                │
                ▼
   compute_*_raw(ticker, sector)
                │
                ▼
   per-sector percentile rank → 0–100 subfactor score
                │
                ▼
   subfactor scores → factor score (mean within factor)
                │
                ▼
   factor scores → composite (weighted by config: scoring.weights)
                │
                ▼
   composite + thresholds → signal: LONG / SHORT / HOLD
```

The key step is **sector-relative ranking**. A high quality score doesn't mean "high quality in absolute terms" — it means "high quality compared to other stocks in the same GICS sector." Banks are ranked against banks, software against software. This avoids meaningless cross-sector comparisons (a bank's debt ratio is structurally different from a tech firm's).

Sector groups smaller than 3 tickers (rare in S&P 500 but common in dev mode) get a fallback score of 50 — the sector median. Otherwise small groups produce noisy ranks.

## Subfactors per factor

| Factor | Subfactors |
|--------|------------|
| Momentum | 12-1 month return, 6-1 month return, risk-adjusted (return/vol) |
| Value | P/E, P/B, EV/EBITDA, FCF yield |
| Quality | ROIC, gross margin stability, debt/equity, Piotroski F-Score, Altman Z-Score |
| Growth | Revenue growth, EPS growth, FCF growth |
| Revisions | EPS revision direction (up vs. down count), magnitude |
| Short interest | Days-to-cover, short interest ratio, change in short interest |
| Insider | Net buy/sell ratio, cluster buys, dollar-weighted activity |
| Institutional | Concentration (top-10 holder share), 13F change in holdings |

Each subfactor is computed by a `compute_*_raw(ticker, sector)` function in its module. The function returns a dict of raw values; the orchestrator handles winsorization (1st/99th percentile clip) and the sector-relative percentile rank.

## Composite and signal

```python
composite = sum(factor_score[f] * weight[f] for f in FACTOR_NAMES)
```

The default `signal` thresholds are:

| Composite range | Signal |
|-----------------|--------|
| > 70 | LONG |
| < 30 | SHORT |
| 30–70 | HOLD |

Tied at the breakpoints, the higher-decile rank wins. Adjust thresholds in `factors/composite.py: _assign_signal` if you want a wider or narrower signal band.

## Quality diagnostics

Two non-factor metrics are surfaced alongside the quality score for fast triage:

- **Piotroski F-Score** (0–9). A 9-point checklist of profitability, leverage, and operating efficiency improvements year-over-year. ≥7 is strong; ≤3 is weak.
- **Altman Z-Score**. A bankruptcy-risk model. JARVIS labels:
  - `SAFE` — Z > 2.99
  - `GREY` — 1.81 ≤ Z ≤ 2.99
  - `DISTRESS` — Z < 1.81

These are diagnostics, not factor inputs. They appear in the per-ticker view (`run_scoring.py --ticker AAPL`).

## Crowding detection

`factors/crowding.py` builds a daily time series of factor returns (long top quintile minus short bottom quintile) and computes the rolling correlation between factor pairs. When a pair's 60-day correlation exceeds 0.7 *and* both legs are running positive IRR, the pair is flagged as "crowded" — the same trade is showing up in too many funds. The output appears in `run_scoring.py` as a warning at the end.

Crowding signal needs ~60 days of factor-return history to populate. On a fresh database it returns "no crowding data yet."

## Regime conditional weighting

If `scoring.regime_conditional_weights: true` in `config.yaml` (default `false`), the weights vary by VIX regime:

| Regime | Risk-on emphasis | Defensive emphasis |
|--------|-------------------|---------------------|
| LOW (VIX < 25) | momentum + growth +5% each | quality −5% |
| ELEVATED (25–33) | unchanged | unchanged |
| HIGH (>33) | momentum −10% | quality + value +5% each |

Implementation in `factors/regime_weights.py`. Off by default to keep scoring runs deterministic across regime changes.

## Output schema

`output/scored_universe_latest.csv` (replaced on every run) and `output/scored_universe_YYYYMMDD.csv` (kept). One row per ticker:

| Column | Description |
|--------|-------------|
| `ticker` | GICS ticker |
| `sector` | GICS sector (normalized) |
| `momentum`, `value`, `quality`, `growth`, `revisions`, `short_interest`, `insider`, `institutional` | 0–100 sector-relative factor score |
| `composite` | Weighted composite (0–100) |
| `signal` | `LONG`, `SHORT`, `HOLD` |
| `piotroski_f` | 0–9 |
| `altman_z` | Float |
| `altman_label` | `SAFE`, `GREY`, `DISTRESS` |

## Common gotchas

**Many factors return `N/A` in dev mode.** Sector groups of 1 (which happens with 10 dev tickers across many sectors) fall back to score 50 for the affected metric. The composite still computes, but the spread between LONG and SHORT candidates is narrow. Use the full universe to see meaningful differentiation.

**Composite score doesn't change after re-running.** Scoring is deterministic given the data. Re-run `run_data.py` first if you expect new data to flow in.

**`run_scoring.py --ticker AAPL` shows scores even though the ticker isn't in dev_tickers.** Single-ticker mode adds the requested ticker to the universe set so it can be scored against the actual sector group.

## See also

- [Configuration](../reference/configuration.md) — `scoring.weights`, `scoring.regime_conditional_weights`
- [Onboarding](../getting-started/onboarding.md) — what factors are and why they work
- [AI analysis](ai-analysis.md) — what consumes this CSV next
