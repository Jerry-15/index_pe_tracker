# Market Data Quality Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all valuation outputs auditable by validating source provenance, dates, currencies, prices, profits, share counts, and coverage before calculating PE.

**Architecture:** Add shared quality and statistics helpers used by both the existing A-share/industry pipeline and H30533. Keep source adapters responsible for raw-field normalization and provenance, keep the pipeline responsible for eligibility and aggregation, and keep SQLite/static export responsible for preserving every quality field. The UI consumes the same quality schema for all index types.

**Tech Stack:** Python, pandas, SQLite, AkShare, requests, Streamlit, static JSON, ECharts, unittest/pytest-compatible tests.

---

## File Map

- Create `etl/quality.py`: quality-level calculation, date freshness, quote disagreement, market-cap method, and standardized diagnostics.
- Modify `data_sources/h30533.py`: source URL/hash metadata, full fallback provenance, weight coverage metadata, and source snapshot validation.
- Modify `data_sources/us.py`: dual-price source contract, financial freshness/currency/ADS diagnostics, and explicit `market_cap_method`.
- Modify `etl/pipeline_us.py`: enforce quality gates before weighted earnings yield and persist quality metadata.
- Modify `etl/pipeline.py`: attach the same quality schema to ordinary index/industry PE rows without changing their existing PE formula.
- Modify `cache/db.py`: migrate and persist quality fields for H30533 and regular index rows.
- Modify `scripts/export_static_data.py`: export quality level, provenance, freshness, coverage, and exclusion counts.
- Modify `app/main.py`: show one common quality/source/coverage panel for every index.
- Modify `static/index.html`: consume the same metadata fields and render identical quality states.
- Modify `tests/test_h30533.py`, `tests/test_ttm.py`, and `tests/test_static_export.py`: gate, persistence, export, and UI contract coverage.

## Task 1: Shared Quality Model

**Files:**
- Create: `etl/quality.py`
- Test: `tests/test_h30533.py`

- [ ] **Step 1: Write failing quality tests**

Add tests for:

```python
assert quality_level(source_type="csi_official", weight_coverage=0.98,
                     financial_stale_count=0, derived_market_cap_count=0,
                     quote_disagreement_count=0) == "A"
assert quality_level(source_type="etf_disclosure", weight_coverage=0.98,
                     financial_stale_count=0, derived_market_cap_count=2,
                     quote_disagreement_count=0) == "B"
assert quality_level(source_type="etf_disclosure", weight_coverage=0.70,
                     financial_stale_count=0, derived_market_cap_count=0,
                     quote_disagreement_count=0) == "C"
```

Test `is_stale(report_date, as_of, max_age_days=180)`, `market_cap_method`, and stable diagnostic code formatting.

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest tests.test_h30533 -v`  
Expected: FAIL because `etl.quality` does not exist.

- [ ] **Step 3: Implement the shared helpers**

Implement:

```python
QUALITY_LEVELS = ("A", "B", "C")

def is_stale(report_date: str | None, as_of: str | None, max_age_days: int = 180) -> bool: ...
def quality_level(*, source_type: str | None, weight_coverage: float,
                  financial_stale_count: int, derived_market_cap_count: int,
                  quote_disagreement_count: int) -> str: ...
def append_reason(existing: str | None, code: str) -> str: ...
```

The helper must use UTC dates, reject malformed dates as stale, and never silently treat missing coverage as zero-quality valid data.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_h30533 -v`  
Expected: quality helper tests pass.  
Commit: `git add etl/quality.py tests/test_h30533.py && git commit -m "feat: add shared valuation quality gates"`

## Task 2: Source Provenance and Per-Security Gates

**Files:**
- Modify: `data_sources/h30533.py`
- Modify: `data_sources/us.py`
- Test: `tests/test_h30533.py`

- [ ] **Step 1: Add failing source and security tests**

Test that H30533 rows include `source_url`, `fetched_at`, `response_hash`, and `source_type`; ETF rows must have `source_type=etf_disclosure`. Test stale financial dates, unsupported currencies, missing ADS ratios, non-positive prices, and quote disagreement diagnostics. Test market-cap method values are one of `reported_market_cap`, `price_times_reported_shares`, or `derived_profit_div_eps`.

- [ ] **Step 2: Implement source provenance**

In `H30533Source`, capture request URL, UTC fetch timestamp, and SHA-256 of the raw response before parsing. Store these fields on each constituent snapshot and in structured source errors. Keep the CSI-first/ETF-fallback order and preserve the actual fallback source.

- [ ] **Step 3: Implement financial freshness and input methods**

In `USDataSource.get_current_pe`, add:

```python
"financial_fresh": bool,
"market_cap_method": "reported_market_cap" | "price_times_reported_shares" | "derived_profit_div_eps" | None,
"quote_source_count": int,
"quote_disagreement": bool,
```

Use `HOLDER_PROFIT`/`PARENT_HOLDER_NETPROFIT` only when paired with EPS and a valid report date. Do not default ADS or currency. Record stale/missing/unsupported reasons.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_h30533 -v`  
Expected: source hash, stale-date, quote disagreement, and method tests pass.  
Commit: `git add data_sources/h30533.py data_sources/us.py tests/test_h30533.py && git commit -m "feat: add auditable market data provenance"`

## Task 3: Enforce Gates in H30533 Aggregation

**Files:**
- Modify: `etl/pipeline_us.py`
- Test: `tests/test_h30533.py`

- [ ] **Step 1: Add failing aggregation gate tests**

Assert that:

- Weight coverage below `0.95` produces `quality_level="C"` and `pe=None`.
- Stale financial data is excluded and counted.
- Quote disagreement is excluded and counted.
- Derived market cap is allowed only with a `B` ceiling when otherwise valid.
- No ADS/currency verification produces `pe=None` for that security.
- The output can be independently recalculated from exported security rows.

- [ ] **Step 2: Extend `aggregate_h30533`**

Return `quality_level`, `weight_coverage`, `financial_stale_count`, `derived_market_cap_count`, `quote_disagreement_count`, `excluded_count`, and `validity_reason`. Use `0.95` as the default minimum H30533 weight coverage. Do not renormalize surviving weights. Preserve an invalid daily row with diagnostics when no PE is available.

- [ ] **Step 3: Persist complete current and historical metadata**

Ensure `run()` and `run_historical()` store quality level, source URL/hash, dates, market-cap methods, and exclusion reasons. Historical rows must retain `current_constituents_backfill` and cannot be graded `A` unless official historical membership exists.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_h30533 -v`  
Expected: all H30533 gate tests pass.  
Commit: `git add etl/pipeline_us.py tests/test_h30533.py && git commit -m "feat: enforce H30533 valuation quality gates"`

## Task 4: Apply Common Metadata to Regular Indices

**Files:**
- Modify: `etl/pipeline.py`
- Modify: `cache/db.py`
- Test: `tests/test_ttm.py`

- [ ] **Step 1: Add regular-index metadata tests**

Assert existing A-share/industry PE calculations remain numerically unchanged while rows gain `quality_level`, `data_source`, `generated_at`, `coverage_ratio`, `financial_stale_count`, and `validity_reason`.

- [ ] **Step 2: Add migration-safe columns**

Add quality fields to regular PE tables with explicit-column inserts and additive SQLite migrations. Existing legacy databases and extra columns must continue to load.

- [ ] **Step 3: Populate common metadata**

Use the shared quality helper. Preserve existing regular-index formula and existing reference PE fields; only add provenance and validity metadata.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_ttm -v`  
Expected: existing numerical assertions and new metadata assertions pass.  
Commit: `git add etl/pipeline.py cache/db.py tests/test_ttm.py && git commit -m "feat: add quality metadata to regular valuations"`

## Task 5: Export and UI Quality Surface

**Files:**
- Modify: `scripts/export_static_data.py`
- Modify: `app/main.py`
- Modify: `static/index.html`
- Test: `tests/test_static_export.py`, `tests/test_h30533.py`

- [ ] **Step 1: Add export contract tests**

Assert every exported index row includes `quality_level`, source, generated time, coverage, financial date, and validity reason. Assert H30533 includes source URL/hash, constituent date, market-cap method counts, excluded counts, and current-backfill basis.

- [ ] **Step 2: Export full metadata**

Extend compact rows only with documented stable positions or add a versioned metadata object. Ensure `manifest.json` records generated time and every referenced artifact. Never convert unavailable PE into zero.

- [ ] **Step 3: Unify Streamlit and static quality panel**

Use the same labels and colors for quality levels and the same valuation bands already shared by all indices. Show a compact source/coverage/freshness panel, then show H30533 diagnostics only when selected. Keep the history caveat visible.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest discover -s tests -p 'test*.py'`  
Run: `python3 -m py_compile app/main.py scripts/export_static_data.py etl/pipeline_us.py`  
Run: extract inline JavaScript and execute `node --check`.  
Commit: `git add scripts/export_static_data.py app/main.py static/index.html tests/test_static_export.py tests/test_h30533.py && git commit -m "feat: display valuation data quality"`

## Task 6: Live Rebuild and Release

**Files:**
- Modify only H30533 static artifacts generated by the exporter.

- [ ] **Step 1: Run live H30533 source and input smoke checks**

Print source type, source URL/hash, constituent count, weight sum, as-of date, quote source counts, stale count, derived market-cap count, and exclusion count. Require a non-empty source and an explicit quality level.

- [ ] **Step 2: Run live historical rebuild**

Rebuild the longer current-constituent backfill, preserve the caveat, compute shared mean/high/low/percentile fields, and verify that post-warmup band fields are non-null.

- [ ] **Step 3: Export and validate artifacts**

Run: `python3 scripts/export_static_data.py`  
Verify exactly one H30533 entry, all manifest references exist, all JSON parses, and no unrelated regular `static/data/*.json` files are staged.

- [ ] **Step 4: Run full verification**

Run: `python3 -m unittest discover -s tests -p 'test*.py'`  
Run: `python3 -m compileall -q app cache data_sources etl scripts tests`  
Run: `git diff --check`  
Record that pytest is unavailable if it remains unavailable.

- [ ] **Step 5: Push feature and production branches**

Push the feature branch with the verified SSH key, merge it into `main` in an isolated worktree, rerun verification, and push `main` to trigger Netlify.
