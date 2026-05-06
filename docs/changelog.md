# Changelog

Documented bug fixes from the first end-to-end smoke test of the seven-layer pipeline. Every fix below has been verified against `python run_*.py --dev` on a 10-ticker universe.

The fixes fall into three groups:

1. **Layer 1 (data) — SEC integrations were broken.** Form 4 insider transactions and 13F institutional filings produced zero rows on every run.
2. **Layers 2–4 (scoring → portfolio) — dev mode produced no output.** Sector cohorts collapsed to median, signal thresholds never fired, the optimizer silently breached its per-position cap, and turnover budgeting killed the first rebalance from cash.
3. **Layer 6 + dashboard — silent fallbacks masked failures.** Every Alpaca paper order was rejected and only succeeded via the SIMULATED fallback path. Streamlit emitted FutureWarnings on every load.

---

## Layer 1 — Data

### 1. SEC `_sec_get` clobbered caller headers (`TypeError`)

**Symptom:** Every Form 4 insider fetch logged `RetryError[<Future ... raised TypeError>]` and `Insider transactions: 0` after a full data refresh.

**Cause:** `data/sec_data.py:_sec_get` baked a default `Accept` header into a local dict, then passed it to `requests.get(...)` while *also* forwarding the caller's `kwargs` — including their own `headers=` argument. Python raised `TypeError: requests.get() got multiple values for keyword argument 'headers'`. `tenacity` caught it as a `RetryError`, the warning logged, and the loop continued with zero rows stored.

**Fix** — merge caller headers over the defaults:

```python
# data/sec_data.py
def _sec_get(url: str, **kwargs) -> requests.Response:
    headers = {**sec_headers(), "Accept": "application/json", **kwargs.pop("headers", {})}
    r = requests.get(url, headers=headers, timeout=20, **kwargs)
    r.raise_for_status()
    return r
```

The same bug existed in `data/institutional.py:_sec_get` (a copy of the function); identical fix applied there.

### 2. Form 4 `primary_doc` returned the stylesheet, not the XML

**Symptom:** After fix 1, fetches no longer errored — but the parser logged `Failed to parse Form 4 XML: Opening and ending tag mismatch: meta line 4 and head` for every filing. Insider transactions still zero.

**Cause:** SEC's submissions API returns `primary_doc` like `xslF345X06/form4.xml`. That path serves the *stylesheet-rendered HTML view* of the form, not the raw XML. The original code took the `.endswith(".xml")` branch and fetched HTML, which `lxml` then choked on.

**Fix** — strip the directory prefix to land on the raw XML at the filing root, and fall back to enumerating `index.json` when no `.xml` document is named directly:

```python
# data/sec_data.py — Form 4 fetcher
primary = filing.get("primary_doc", "")
if primary.endswith(".xml") and "/" in primary:
    primary = primary.rsplit("/", 1)[1]
xml_doc = primary if primary.endswith(".xml") else None

if xml_doc is None:
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/index.json"
    idx_r = _sec_get_retry(index_url)
    items = idx_r.json().get("directory", {}).get("item", [])
    candidates = [
        it["name"] for it in items
        if it.get("name", "").endswith(".xml")
        and not it["name"].lower().startswith("filingsummary")
    ]
    if not candidates:
        log.warning(f"No Form 4 XML found in index for {ticker} {acc}")
        continue
    xml_doc = candidates[0]

xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{xml_doc}"
```

Verified: `Insider transactions: 0` → `481` on the dev universe.

### 3. 13F XML parser tripped on undeclared `xsi:schemaLocation`

**Symptom:** With fix 1 in place, Form 4 worked but 13F parsing logged `Failed to parse 13F XML: Namespace prefix xsi for schemaLocation on informationTable is not defined`. Citadel/Tiger Global/Pershing Square holdings were dropped.

**Cause:** Many 13F information-table XMLs include `xsi:schemaLocation="..."` on the root element without an accompanying `xmlns:xsi="..."` declaration. `lxml`'s strict parser rejects the unknown namespace prefix. The existing pre-processor stripped `xmlns:*` declarations but didn't touch namespaced *attributes* like `xsi:schemaLocation`.

**Fix** — strip the offending attributes too, and switch to recovery mode:

```python
# data/institutional.py
xml_clean = re.sub(r'\s+xmlns[^"]*"[^"]*"', "", xml_text)
xml_clean = re.sub(r'\s+[A-Za-z_][\w.-]*:[A-Za-z_][\w.-]*="[^"]*"', "", xml_clean)
parser = lxml_etree.XMLParser(recover=True)
root = lxml_etree.fromstring(xml_clean.encode("utf-8", errors="replace"), parser)
```

Verified: Citadel/Tiger Global/Pershing Square 13Fs now parse. (Bridgewater/Third Point/Baupost still log "no holdings parsed" — different filing-shape issue, unrelated to this fix.)

---

## Layer 2 — Scoring

### 4. `dev_tickers` had one ticker per most sectors → every score collapsed to 50

**Symptom:** `python run_scoring.py` produced `0 LONG, 0 SHORT, 10 NEUTRAL` even though Layer 1 was clean.

**Cause:** Sector-relative percentile ranking has a `MIN_GROUP_SIZE = 3` floor (in `factors/base.py`) — sectors with <3 tickers fall back to score 50.0. The original `dev_tickers` had AAPL/MSFT/GOOGL/NVDA/V in IT (one of which — V — actually classifies as IT not Financials), AMZN solo in Consumer Discretionary, JPM solo in Financials, JNJ/UNH in Health Care, XOM solo in Energy. Most sectors had 1 ticker, so most factor scores collapsed to 50, the composite collapsed near 50, and no signal could fire.

**Fix** — pick a list with three sectors, ≥3 tickers each:

```yaml
# config.yaml
dev_tickers: ["AAPL", "MSFT", "NVDA", "INTC", "JNJ", "UNH", "LLY", "JPM", "GS", "BAC"]
```

That gives Information Technology = 4, Health Care = 3, Financials = 3 — every cohort clears the `MIN_GROUP_SIZE` floor.

### 5. Hard-coded `composite >= 80` LONG threshold despite "top quintile" comment

**Symptom:** Even with viable cohorts producing dispersed factor scores (composite range 36.7–60.3), every ticker still came out NEUTRAL.

**Cause:** `factors/composite.py` hard-coded `composite >= 80 → LONG` and `composite <= 20 → SHORT`. The code comment said "top quintile / bottom quintile" but the implementation was an absolute threshold that only fires on huge universes where percentile-of-mean approaches 80/20. With 10 tickers, the most extreme composite was 60 — nowhere near 80.

**Fix** — make the comment true: rank by composite, take top/bottom 20%:

```python
# factors/composite.py
result["signal"] = "NEUTRAL"
n = len(result)
if n >= 5:
    comp_pct = result["composite"].rank(pct=True) * 100
    result.loc[comp_pct >= 80, "signal"] = "LONG"
    result.loc[comp_pct <= 20, "signal"] = "SHORT"
```

Now scales correctly with universe size — 2/2 on a 10-ticker dev book, 100/100 on a 503-ticker S&P 500 run.

---

## Layer 3 — AI analysis

### 6. Default OpenRouter model was deprecated (404)

**Symptom:** `run_analysis.py --ticker AAPL` logged `HTTP 404 Not Found ... 'No endpoints found for google/gemini-2.0-flash-exp:free'` for every analyzer call. Combined score silently fell back to 100% quant.

**Cause:** `google/gemini-2.0-flash-exp:free` was an experimental model that OpenRouter removed. `config.yaml` still pointed at it.

**Fix** — switch to a free model that's actually live and not aggressively rate-limited:

```yaml
# config.yaml
ai:
  model: "openai/gpt-oss-20b:free"
```

Verified live with a one-token round-trip. Google Gemma family (`google/gemma-4-31b-it:free`) is also live but was returning 429s during the smoke test. See ADR 001 for the full rationale.

### 7. Cost-estimate message hard-coded "Gemini 2.0 Flash exp"

**Cause:** `run_analysis.py:96` hard-coded the model name in the cost message even after the config was changed.

**Fix** — derive from `:free` suffix:

```python
# run_analysis.py
is_free = ":free" in (model or "")
tier_label = "Free tier" if is_free else "Paid tier"
print(f"  {tier_label}: estimated cost ~$0.00 for ~160 calls" if is_free
      else f"  {tier_label}: cost depends on model pricing for ~160 calls")
```

---

## Layer 4 — Portfolio

### 8. Conviction optimizer silently breached the per-position cap

**Symptom:** With 3 LONG + 2 SHORT candidates and the 5% per-position cap, the optimizer produced positions sized 25% each — five times the cap.

**Cause:** `portfolio/optimizer.py` clipped each weight to `max_pos`, then *re-normalized* the side back to `_GROSS_TARGET = 0.75`. With only 3 longs at 5% each (sum = 0.15), re-normalizing to 0.75 multiplied every weight by 5×, blowing past the cap.

**Fix** — accept a smaller book when too few candidates exist to fill the gross target without breaching the cap:

```python
# portfolio/optimizer.py
def _cap_and_normalize(weights: pd.Series) -> pd.Series:
    if weights.empty:
        return weights
    max_feasible = max_pos * len(weights)
    target_gross = min(_GROSS_TARGET, max_feasible)
    weights = _normalize(weights, target_gross)
    weights = weights.clip(upper=max_pos)
    return _normalize(weights, target_gross)

long_weights = _cap_and_normalize(long_weights)
short_weights = _cap_and_normalize(short_weights)
```

A small dev book is now legitimately small (gross 0.25 with 5 candidates) instead of fraudulently large.

### 9. Turnover budget killed every initial-build rebalance

**Symptom:** Every first-time `--rebalance` from an empty book logged `Turnover budget (30%) applied: trimmed to 0 trades`.

**Cause:** `portfolio/rebalance.py` always applied the 30% turnover budget, even when current positions were empty. From cash to a target book, every trade is by definition new turnover. With 5 candidates summing to 25% gross, total turnover was already under the 30% budget — but combined with bug 8 (positions sized to 25% each), turnover was 150% and trimmed to zero.

**Fix** — turnover budgeting is meant to limit churn between existing books, not throttle initial construction. Skip it when there's nothing to churn:

```python
# portfolio/rebalance.py
is_initial_build = not current_weights
if not is_initial_build and total_turnover > turnover_budget:
    # ... existing trim logic ...
elif is_initial_build:
    log.info(f"Initial build from empty book ({total_turnover:.1%}) — turnover budget not applied")
```

---

## Layer 5 — Risk

### 10. `run_risk_check.py --stress` printed "No results (empty portfolio)" pre-execution

**Symptom:** Stress test produced no scenarios when run before the first execution — the pass criterion of "six scenario P&Ls" never met.

**Cause:** `_print_stress` pulled positions from `portfolio_positions`. Pre-execution that table is empty, `weights = {}`, and `run_stress_tests({})` returns an empty list.

**Fix** — fall back to the scored signals at the per-position cap (approximates what the optimizer would build):

```python
# run_risk_check.py
if not weights:
    scored_path = ROOT / "output" / "scored_universe_latest.csv"
    if scored_path.exists():
        scored = pd.read_csv(scored_path)
        cap = get_config().get("portfolio", {}).get("max_position_pct", 0.05)
        hypo = {}
        for _, r in scored.iterrows():
            if r.get("signal") == "LONG":
                hypo[r["ticker"]] = cap
            elif r.get("signal") == "SHORT":
                hypo[r["ticker"]] = -cap
        if hypo:
            print(f"  No live positions — stressing hypothetical book "
                  f"({sum(1 for v in hypo.values() if v>0)}L / "
                  f"{sum(1 for v in hypo.values() if v<0)}S at {cap:.0%} each).")
            weights = hypo
```

Six scenarios now print whenever a scored universe exists.

### 11. `datetime.utcnow()` deprecation warnings in `run_risk_check.py` + `run_execution.py`

**Cause:** Python 3.12+ deprecates `datetime.datetime.utcnow()` in favor of timezone-aware `datetime.now(timezone.utc)`. Three callsites surfaced during the test path: lines 66 + 205 in `run_risk_check.py` and line 225 in `run_execution.py`.

**Fix** — replace at each callsite, add `timezone` import:

```python
# run_risk_check.py + run_execution.py
from datetime import datetime, timezone
# ...
datetime.now(timezone.utc).strftime(...)
```

Other files in the repo still call `datetime.utcnow()` but only fire when their code paths are exercised — see "Outstanding" below.

---

## Layer 6 — Execution

### 12. Alpaca rejected every paper order: "fractional orders must be DAY orders"

**Symptom:** `run_execution.py --execute` logged `submit_order failed: {"code":42210000,"message":"fractional orders must be DAY orders"}` for every order, then silently fell back to SIMULATED fills. The summary showed 5/5 success; in reality zero real orders ever reached Alpaca.

**Cause:** Default `time_in_force: "gtc"` in `config.yaml`, but Alpaca paper-trading rejects GTC for fractional shares. JARVIS sizes positions in fractional shares routinely (e.g., `22.17 JNJ`), so every paper order failed at submit.

**Fix** — auto-override GTC → DAY for fractional shares:

```python
# execution/executor.py
tif_str = cfg.get("time_in_force", "gtc").upper()
tif = TimeInForce.GTC if tif_str == "GTC" else TimeInForce.DAY
if tif == TimeInForce.GTC and float(shares) != int(float(shares)):
    log.info(f"Fractional shares ({shares}) — overriding time_in_force GTC -> DAY for {ticker}")
    tif = TimeInForce.DAY
```

Verified: real Alpaca order IDs now appear in the log (e.g., `Order submitted: ec2ed1e5-... BUY 22.17 JNJ`). Whether they fill depends on market hours and the limit; off-hours orders cancel after the 3-attempt timeout, which is correct behavior.

---

## Layer 7 — Dashboard

### 13. `Styler.applymap` deprecated → 4× FutureWarning per dashboard load

**Cause:** `dashboard/app.py` used `pd.io.formats.style.Styler.applymap` in four places. Pandas deprecated it in favor of `Styler.map`.

**Fix** — bulk rename:

```python
# dashboard/app.py — four call sites
styled_stress = stress_df.style.map(_color_pnl, subset=[...])
styled_monthly = monthly_df[present].style.map(_color_return).format("{:.2%}", na_rep="-")
styled_sector = sector_df.style.map(_color_alpha, subset=["alpha"]).format({...})
styled_pos = styled_pos.map(_color_pnl_cell, subset=["unrealized_pnl"])
```

Dashboard now serves HTTP 200 with zero warnings.

---

## Outstanding (not fixed)

These surfaced during the smoke test but were left alone deliberately.

- **`datetime.utcnow()` in 25 other files.** Only fires when their code paths run. Mass-replace risks subtle behavior changes in DB ISO-string columns. Better tackled per-file when each is touched for another reason.
- **Three 13F filings still log "No holdings parsed"** (Bridgewater, Third Point, Baupost). Unrelated to fix 3 — likely a different filing-shape issue (NT-13F amendment or non-standard XML).
- **`output/scored_universe_latest.csv` is overwritten by `--ticker` runs.** Re-run full scoring before Layer 4 if you've spot-checked a single ticker. Not a bug per se — just shared state worth knowing.
- **Off-hours order timeouts.** Real Alpaca paper orders submitted after market close cancel after the 3-attempt retry. Correct behavior; mentioned here so it isn't mistaken for a regression.
- **Alpaca cancel-on-fill mishandled in retry loop.** Surfaced during the [test-run capture](test-run-results.md#execute-all-approved-trades-against-alpaca-paper). When an Alpaca paper limit order fills before our wait-window expires, the subsequent cancel attempt returns `{"code":42210000,"message":"order is already in \"filled\" state"}`. `execution/executor.py` treats every cancel-failure as a generic timeout and re-submits, producing duplicate orders that JARVIS's local `order_log` doesn't reconcile. Fix idea: detect the "already filled" payload and treat it as success (record the fill, exit the retry loop). Worth a follow-up patch when the next change touches the executor.

## See also

- [Common issues](troubleshooting/common-issues.md) — user-facing symptoms for each fix
- [ADR 001](architecture/adr/001-openrouter-over-anthropic-api.md) — model-default rationale
- [Scoring engine](concepts/scoring-engine.md) — rank-based signals
- [Portfolio construction](concepts/portfolio-construction.md) — initial-build turnover behavior
- [Execution](concepts/execution.md) — fractional-share TIF override
