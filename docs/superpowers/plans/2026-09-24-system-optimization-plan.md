# Index PE Tracker Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the valuation tracker safer to update, clearer about data quality, faster to calculate, more robust on static hosting, and capable of showing a separate Hong Kong individual-stock valuation view.

**Architecture:** Preserve the current SQLite cache, PEETL, Streamlit app, and Netlify static export. Add focused reliability, data-quality, and Hong Kong modules instead of replacing the existing pipeline. Keep A-share index/sector aggregation and Hong Kong stock valuation in separate namespaces and calculation paths.

**Tech Stack:** Python 3.11, pandas, NumPy, SQLite, AkShare, Streamlit, Plotly, ECharts, Netlify, pytest.

---

## File Map

- Create: `tests/conftest.py` — temporary SQLite fixtures and shared sample data.
- Create: `tests/test_ttm.py` — fiscal-year decomposition and TTM formula tests.
- Create: `tests/test_update_checkpoint.py` — checkpoint/resume and failure-threshold tests.
- Create: `tests/test_static_export.py` — static JSON schema and file coverage tests.
- Create: `tests/test_hk_valuation.py` — loss-making Hong Kong stock valuation behavior.
- Modify: `cache/db.py` — SQLite indexes, checkpoint/update-log methods, and safe quote upsert.
- Modify: `etl/pipeline.py` — real trading-date filtering, optimized decomposition, validity metadata, and selective recomputation.
- Modify: `data_sources/sina.py` — explicit source errors, trading-calendar access, and Hong Kong market data adapter.
- Create: `data_sources/hk.py` — Hong Kong individual-stock normalization for price and financial indicators.
- Modify: `scripts/update_all.py` — resumable staged updates, `--resume`, `--only`, failure thresholds, and validated push.
- Modify: `scripts/export_static_data.py` — schema version, generated timestamp, validity metadata, and deterministic output.
- Modify: `app/main.py` — data-quality status, stale/error states, and Hong Kong stock view.
- Modify: `static/index.html` — fetch error handling, loading state, generated timestamp, and mobile layout improvements.
- Modify: `README.md` — optimized update workflow, Hong Kong valuation semantics, and deployment instructions.
- Modify: `requirements.txt` — add `pytest` and any explicitly required runtime dependency.

### Task 1: Add Regression Test Foundation

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_ttm.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add test dependencies and fixtures**

```text
pytest>=8.0.0
```

The fixture must create `CacheDB(tmp_path / "test.db")` and return financial rows with report dates `2024-03-31`, `2024-06-30`, `2024-09-30`, `2024-12-31`, `2025-03-31`, `2025-06-30`, including publish dates after each report date.

- [ ] **Step 2: Write failing TTM tests**

Test that same-year decomposition produces Q2 as `H1 - Q1`, Q3 as `Q3 cumulative - H1`, and Q4 as `Annual - Q3`. Test that the standard formula and decomposed-quarter formula differ by less than `1e-9` for a positive-profit sample and return `None` for insufficient reports.

- [ ] **Step 3: Run the focused tests**

Run: `pytest tests/test_ttm.py -q`

Expected: FAIL only because the new test helpers or stricter same-year behavior are not implemented yet.

- [ ] **Step 4: Commit the test foundation**

```bash
git add requirements.txt tests
git commit -m "test: add valuation regression fixtures"
```

### Task 2: Harden SQLite and Checkpointing

**Files:**
- Modify: `cache/db.py`
- Create: `tests/test_update_checkpoint.py`

- [ ] **Step 1: Add SQLite indexes**

Extend `_init_tables()` with idempotent indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_quotes_code_date ON stock_quotes(code, date);
CREATE INDEX IF NOT EXISTS idx_financials_code_publish ON stock_financials(code, publish_date);
CREATE INDEX IF NOT EXISTS idx_quarterly_code_publish ON stock_quarterly_profit(code, publish_date);
CREATE INDEX IF NOT EXISTS idx_pe_daily_index_date ON index_pe_daily(index_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_pe_stats_index_date ON index_pe_stats(index_code, trade_date);
```

- [ ] **Step 2: Add checkpoint storage methods**

Add a `update_checkpoint` table with `run_id`, `stage`, `item_key`, `status`, `error_msg`, and `updated_at`. Implement `save_checkpoint()`, `load_checkpoint()`, and `clear_checkpoint()` methods. Checkpoint keys must be stock codes for quote/financial stages and index codes for recompute stages.

- [ ] **Step 3: Test resume semantics**

`tests/test_update_checkpoint.py` must verify a successful item is skipped on resume, a failed item is retried, and `clear_checkpoint()` removes only the requested run.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_update_checkpoint.py -q`

Expected: PASS.

```bash
git add cache/db.py tests/test_update_checkpoint.py
git commit -m "feat: add indexed cache and update checkpoints"
```

### Task 3: Correct Dates, TTM Decomposition, and Validity Metadata

**Files:**
- Modify: `data_sources/sina.py`
- Modify: `etl/pipeline.py`
- Modify: `cache/db.py`
- Modify: `tests/test_ttm.py`

- [ ] **Step 1: Add a trading-date source and fallback**

Normalize the available AkShare trading-date history into a `date` column. If the source is unavailable, fail explicitly rather than silently treating every weekday as a trading day. Keep a clearly labeled fallback only for offline test fixtures.

- [ ] **Step 2: Fix same-year financial decomposition**

In `_decompose_quarterly()`, match Q1/H1/Q3 rows by `report_date.dt.year == rd.year` before subtraction. Do not use a prior-year period when the same-year report is missing; emit no derived quarter for that row instead of silently using zero.

- [ ] **Step 3: Add validity fields to PE output**

Add `valid_stock_count`, `profitable_stock_count`, `coverage_ratio`, and `validity_reason` to the in-memory PE result and SQLite `index_pe_daily` schema. A row is valid only when coverage meets the configured threshold, total positive TTM profit exists, and PE is within the configured bounds.

- [ ] **Step 4: Add regression assertions**

Verify same-year matching, announcement-date exclusion before publication, and invalid rows for insufficient coverage or non-positive aggregate profit.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_ttm.py -q`

Expected: PASS with the two TTM methods agreeing within `1%` on all fixtures.

```bash
git add data_sources/sina.py etl/pipeline.py cache/db.py tests/test_ttm.py
git commit -m "fix: align fiscal periods and expose PE validity"
```

### Task 4: Optimize PE Computation and Selective Recompute

**Files:**
- Modify: `etl/pipeline.py`
- Modify: `scripts/update_all.py`

- [ ] **Step 1: Cache quarterly data per run**

Load each stock's quarterly-profit DataFrame once per `run_index()` and pass the mapping into snapshot construction. Do not reopen SQLite once per date or once per repeated stock.

- [ ] **Step 2: Vectorize statistics**

Replace the row-wise percentile lambda with `rank(pct=True) * 100`, preserving NaN for invalid PE rows. Keep rolling mean/std behavior unchanged.

- [ ] **Step 3: Add `--only` and `--resume`**

`--only 000300,801080` must restrict recomputation to those codes. `--resume` must reuse checkpointed successful stock and index stages. A failed-index ratio above the configured threshold must return a non-zero exit code and skip Git push.

- [ ] **Step 4: Add performance smoke test**

Use a fixture with 20 stocks and 100 dates. Assert PE computation completes under 3 seconds locally and produces deterministic rows.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests -q`

Expected: PASS.

```bash
git add etl/pipeline.py scripts/update_all.py tests
git commit -m "perf: cache PE inputs and support selective recompute"
```

### Task 5: Improve Static Export and Netlify UX

**Files:**
- Modify: `scripts/export_static_data.py`
- Modify: `static/index.html`
- Create: `tests/test_static_export.py`

- [ ] **Step 1: Add export metadata**

Write `static/data/manifest.json` containing schema version, generated timestamp, source revision, item count, and data date ranges. Add validity/coverage metadata to each index-list entry.

- [ ] **Step 2: Make export deterministic**

Sort index entries by type/name, sort rows by date, serialize finite numeric values only, and write UTF-8 JSON with stable field order.

- [ ] **Step 3: Add frontend loading/error states**

Show loading text while fetching, an inline error with the failed URL/status when JSON fetch fails, a “last generated” timestamp from `manifest.json`, and a coverage/validity note. Preserve the current selector when a selected series fails.

- [ ] **Step 4: Improve mobile layout**

Use responsive card sizing, reduce chart heights below 600px viewport width, and call `resize()` after layout changes.

- [ ] **Step 5: Test export**

`tests/test_static_export.py` must verify manifest creation, 34+ index entries when data exists, every entry has a matching JSON file, and rows have the eight expected array fields.

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/test_static_export.py -q`

Expected: PASS.

```bash
git add scripts/export_static_data.py static tests/test_static_export.py
git commit -m "feat: harden static export and Netlify UX"
```

### Task 6: Add Hong Kong Individual Valuation Module

**Files:**
- Create: `data_sources/hk.py`
- Create: `tests/test_hk_valuation.py`
- Modify: `cache/db.py`
- Modify: `app/main.py`
- Modify: `README.md`

- [ ] **Step 1: Add normalized Hong Kong adapter**

Create `HKDataSource.get_stock_snapshot("02498")` returning normalized price, report date, revenue, shareholder profit, EPS, estimated shares, estimated market cap, PS, and `is_profitable`. Preserve source currency as HKD.

- [ ] **Step 2: Encode loss-making semantics**

For non-positive TTM profit, return `pe_ratio=None`, `valuation_metric="PS"`, and an explicit `profit_status="loss-making"`. Never display a negative PE as a positive valuation result.

- [ ] **Step 3: Add separate cache tables**

Create `hk_quotes` and `hk_financials` tables without reusing A-share `stock_quotes` or `stock_financials`.

- [ ] **Step 4: Add tests**

Use fixture data for 02498.HK with negative profit and verify PS is positive, PE is null, currency is HKD, and the status is loss-making.

- [ ] **Step 5: Add UI route/tab**

Add a separate “港股个股” mode in Streamlit for 02498.HK. Show price chart, PS, revenue, net profit, EPS, data date, and source/estimate labels. Do not place it in the A-share index selector.

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/test_hk_valuation.py -q`

Expected: PASS.

```bash
git add data_sources/hk.py cache/db.py app/main.py README.md tests/test_hk_valuation.py
git commit -m "feat: add Hong Kong individual valuation view"
```

### Task 7: Full Verification and Release

**Files:**
- Modify: `README.md`
- Verify: all project files and generated static data

- [ ] **Step 1: Run the complete test suite**

Run: `pytest tests -q`

Expected: all tests pass.

- [ ] **Step 2: Run import and syntax checks**

Run: `python -m compileall app cache data_sources etl scripts`

Expected: exit code 0.

- [ ] **Step 3: Export static data and validate paths**

Run: `python scripts/export_static_data.py`

Expected: manifest exists, every listed JSON file exists, and no SQLite file is staged by Git.

- [ ] **Step 4: Test local static serving**

Run: `python -m http.server 8899 --directory static`

Verify with `curl` that `/`, `/data/manifest.json`, `/data/index_list.json`, and one index JSON return HTTP 200.

- [ ] **Step 5: Review Git diff and status**

Run: `git diff --check && git status --short`

Expected: no whitespace errors, database remains ignored, only intended source/tests/docs/static files changed.

- [ ] **Step 6: Commit verification updates**

```bash
git add README.md
git commit -m "docs: finalize optimization and release checks"
```
