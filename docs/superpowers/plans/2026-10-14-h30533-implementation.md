# H30533 Valuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the custom China internet stock basket with a traceable H30533-based valuation pipeline using official constituents/weights, real market-cap inputs, and explicit coverage metadata.

**Architecture:** Add a dedicated H30533 data-source adapter that normalizes official index or ETF-disclosure constituent snapshots into one schema. Refactor the US ETL to calculate weighted earnings yield from price, shares/market cap, and TTM profit; preserve historical data as current-constituent backfill with an explicit caveat. Extend SQLite and static export schemas so every result carries source date, source type, weight coverage, and validity status.

**Tech Stack:** Python, pandas, SQLite, AkShare, requests, Streamlit, static JSON export, unittest/pytest-compatible tests.

---

## File Map

- Create `data_sources/h30533.py`: official CSI endpoint discovery, ETF-disclosure fallback, response parsing, code normalization, and issuer de-duplication.
- Modify `config.py`: identify `H30533` and `513050`; remove fixed US reference PE from the primary path.
- Modify `data_sources/us.py`: expose normalized quote, share-count, currency, and financial inputs required by H30533.
- Modify `etl/pipeline_us.py`: replace `price² / PE` aggregation with weighted earnings yield and explicit validity metadata.
- Modify `cache/db.py`: persist constituent snapshots and valuation metadata with migration-safe explicit-column inserts.
- Modify `scripts/export_static_data.py`: export H30533 source metadata, coverage, validity, and constituent snapshot data.
- Modify `app/main.py`: rename the view and display source/date/coverage/caveat information.
- Create `tests/test_h30533.py`: parser, normalization, aggregation, coverage, duplicate, and export tests.
- Create `docs/superpowers/specs/2026-10-14-h30533-design.md`: approved design already committed; keep it synchronized if implementation constraints change.

## Task 1: Lock Official Source Contract

**Files:**
- Create: `data_sources/h30533.py`
- Modify: `config.py`
- Test: `tests/test_h30533.py`

- [ ] **Step 1: Write failing parser tests**

Test a representative official response fixture and a 513050 disclosure fixture. Assert normalized rows contain `security_code`, `name`, `exchange`, `weight`, `as_of`, and `source_type`; assert weights are numeric percentages converted to fractions; assert malformed rows are rejected with a recorded reason.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python3 -m pytest tests/test_h30533.py -q`
Expected: FAIL because the adapter and parser do not exist yet. If pytest is unavailable, run `python3 -m unittest tests.test_h30533 -v` and record the environment limitation.

- [ ] **Step 3: Discover and document the live endpoint**

Inspect the CSI web bundle/network calls or a supported public CSI endpoint. The adapter must return a structured error when the endpoint changes, never silently return the old custom basket. Keep the endpoint and response field mapping in `h30533.py`, not scattered through the ETL.

- [ ] **Step 4: Implement the normalized snapshot adapter**

Implement:

```python
class H30533Source:
    def get_snapshot(self) -> pd.DataFrame: ...
    def parse_official(self, payload) -> pd.DataFrame: ...
    def parse_etf_disclosure(self, payload) -> pd.DataFrame: ...
    def normalize_code(self, code, exchange) -> str: ...
```

Use `source_type="csi_official"` for CSI data and `source_type="etf_disclosure"` for the 513050 fallback. Require `as_of`, reject missing weights, and preserve raw source metadata. Do not invent weights for missing constituents.

- [ ] **Step 5: Add configuration constants**

Define `CHINA_INTL_INDEX_CODE = "H30533"`, `CHINA_INTL_FUND_CODE = "513050"`, and a source timeout/retry setting. Keep the old custom stock map only as migration data, not as the H30533 constituent source.

- [ ] **Step 6: Run tests and commit**

Run: `python3 -m pytest tests/test_h30533.py -q`
Expected: parser and normalization tests pass.
Commit: `git add data_sources/h30533.py config.py tests/test_h30533.py && git commit -m "feat: add H30533 constituent source"`

## Task 2: Normalize Securities and Prevent Duplicate Issuers

**Files:**
- Modify: `data_sources/h30533.py`
- Modify: `data_sources/us.py`
- Test: `tests/test_h30533.py`

- [ ] **Step 1: Add duplicate and ADS tests**

Use fixtures containing both a Hong Kong line and an ADR line for the same issuer. Assert that the normalized snapshot retains the official security line and weight once, preserves ADS metadata, and does not sum duplicate economic exposure.

- [ ] **Step 2: Implement issuer identity normalization**

Add an explicit issuer identity mapping only where the source provides an unambiguous relationship. Do not infer identity from similar names alone. Keep `security_code` and `issuer_id` separate so reporting can show the actual index security while aggregation can prevent duplicate issuers.

- [ ] **Step 3: Expose quote and financial inputs**

Extend `USDataSource` methods to return a common record with `price`, `currency`, `shares_or_market_cap`, `profit_ttm`, `financial_as_of`, `source`, and `validity_reason`. Preserve ADS ratio and currency conversions as explicit fields. If shares or profit cannot be verified, return `None` and a reason instead of a zero.

- [ ] **Step 4: Verify duplicate behavior**

Run: `python3 -m pytest tests/test_h30533.py -q`
Expected: duplicate issuer tests pass and no row is silently dropped without metadata.
Commit: `git add data_sources/h30533.py data_sources/us.py tests/test_h30533.py && git commit -m "feat: normalize H30533 securities"`

## Task 3: Implement Weighted Earnings-Yield Aggregation

**Files:**
- Modify: `etl/pipeline_us.py`
- Test: `tests/test_h30533.py`

- [ ] **Step 1: Write aggregation tests**

Test a two-security fixture with known weights, market caps, and profits. Assert:

```python
yield_i = profit_ttm_i / market_cap_i
index_yield = sum(weight_i * yield_i for i in valid_rows)
index_pe = 1 / index_yield
```

Also assert that `price ** 2 / pe` never appears in the aggregation path, that negative-profit rows are excluded and counted, and that insufficient valid-weight coverage returns `pe=None` with a reason.

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m pytest tests/test_h30533.py -q`
Expected: aggregation tests fail against the current `price² / PE` implementation.

- [ ] **Step 3: Add a pure aggregation function**

Implement a side-effect-free function in `etl/pipeline_us.py`:

```python
def aggregate_h30533(rows: pd.DataFrame, min_weight_coverage: float = 0.8) -> dict:
    """Return PE, earnings yield, coverage, counts, and validity reason."""
```

Use real `market_cap` when available, otherwise calculate it only from verified `price * shares`. Do not renormalize weights when rows are missing. Return `valid=False` when coverage is below threshold or aggregate earnings yield is non-positive.

- [ ] **Step 4: Refactor current snapshot execution**

Make `run()` obtain the H30533 snapshot, fetch current inputs, call `aggregate_h30533`, and persist the result. Store source type/date and per-security diagnostics. Remove `US_REF_PE` from this execution path.

- [ ] **Step 5: Refactor historical execution**

Keep current-constituent historical backfill as a separate mode. Use historical prices and the latest available verified financial snapshot, label every row `constituent_basis="current_constituents_backfill"`, and do not claim official historical index membership.

- [ ] **Step 6: Run focused tests and commit**

Run: `python3 -m pytest tests/test_h30533.py -q`
Expected: all aggregation and coverage tests pass.
Commit: `git add etl/pipeline_us.py tests/test_h30533.py && git commit -m "feat: calculate H30533 PE from weighted earnings yield"`

## Task 4: Persist Metadata and Migrate Safely

**Files:**
- Modify: `cache/db.py`
- Test: `tests/test_h30533.py`

- [ ] **Step 1: Write SQLite round-trip tests**

Use a temporary database. Save and load a constituent snapshot and valuation row containing source, dates, coverage, missing count, negative-profit count, and validity reason. Test against both a fresh schema and an existing schema with extra legacy columns.

- [ ] **Step 2: Add migration-safe tables/columns**

Add explicit-column writes for H30533 constituent and PE tables. Use additive migrations only. Never use `INSERT INTO table VALUES (...)` for these tables because the existing cache already has legacy columns.

- [ ] **Step 3: Implement persistence methods**

Add methods for saving/loading the normalized H30533 snapshot and the enriched daily valuation rows. Ensure a failed update cannot delete the last valid snapshot before the replacement has passed validation.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m pytest tests/test_h30533.py -q`
Expected: fresh and legacy-schema round trips pass.
Commit: `git add cache/db.py tests/test_h30533.py && git commit -m "feat: persist H30533 valuation metadata"`

## Task 5: Export and Frontend Labels

**Files:**
- Modify: `scripts/export_static_data.py`
- Modify: `app/main.py`
- Modify: `static/index.html`
- Test: `tests/test_h30533.py`

- [ ] **Step 1: Write export tests**

Assert that exported `index_list.json` contains exactly one `H30533` entry, the referenced `CHINA_INTL.json` exists, and the latest row includes source date, coverage, validity, and constituent-basis metadata. Assert old `US_REF_PE` labels are absent from the H30533 view.

- [ ] **Step 2: Export enriched JSON**

Update `_export_china_intl` to replace existing H30533 entries rather than append-only behavior. Export all source and coverage metadata, current constituent rows, and an explicit unavailable state when no valid PE exists.

- [ ] **Step 3: Update UI labels and warnings**

Rename the selector/page to `中证海外中国互联网50（H30533）`. Display current PE only when valid; otherwise show the stored validity reason. Always show source type, constituent date, financial date/coverage, and the current-constituent backfill caveat for historical rows. Remove wording that claims 23 US-listed companies or fixed reference PE.

- [ ] **Step 4: Run export and frontend smoke checks**

Run: `python3 scripts/export_static_data.py`
Run: `python3 -m py_compile app/main.py scripts/export_static_data.py`
Expected: export succeeds, JSON references resolve, and no stale custom-basket entry remains.

- [ ] **Step 5: Commit**

```bash
git add scripts/export_static_data.py app/main.py static/index.html tests/test_h30533.py
git commit -m "feat: expose H30533 source and coverage metadata"
```

## Task 6: End-to-End Verification and Delivery

**Files:**
- Modify only files required by failed verification.

- [ ] **Step 1: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: all existing and new tests pass. If pytest is unavailable, install/use the project environment or run `python3 -m unittest discover -s tests -v`; do not claim the full suite passed when zero tests were discovered.

- [ ] **Step 2: Run a live source smoke test**

Fetch the H30533 snapshot and print source type, `as_of`, row count, weight sum, duplicate issuer count, and missing-input count. Require either official CSI data or an explicitly marked ETF-disclosure fallback; reject the old custom map.

- [ ] **Step 3: Run a live valuation smoke test**

Run the H30533 update and print PE, earnings yield, weight coverage, valid count, negative-profit count, and validity reason. Independently recompute PE from exported constituent rows and assert the difference is below `1e-9` for the test fixture or below rounding tolerance for live data.

- [ ] **Step 4: Validate repository state**

Run: `git diff --check`
Run: `git status --short`
Confirm the unrelated existing `static/data/000300.json` modification is not staged.

- [ ] **Step 5: Push the implementation branch**

Use the verified key explicitly:

```bash
GIT_SSH_COMMAND='ssh -i /Users/mac/.ssh/id_github -o IdentitiesOnly=yes' git push origin feat/system-optimization
```

Expected: branch updates on `git@github.com:Jerry-15/index_pe_tracker.git`.
