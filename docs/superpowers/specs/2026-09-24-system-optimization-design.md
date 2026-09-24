# Index PE Tracker Optimization Design

## Goal

Improve the existing A-share index and sector valuation tracker without replacing its current GitHub + Netlify architecture. The system should update more safely, expose data quality clearly, calculate faster, provide a better static experience, and support a separate Hong Kong individual-stock valuation view for RoboSense (02498.HK).

## Scope

### In scope

- Resumable and failure-aware local data updates.
- Real trading-calendar alignment and explicit data-validity metadata.
- Faster quarterly-profit decomposition, SQLite queries, and PE statistics.
- Better Streamlit and Netlify static-page status/error presentation.
- Separate Hong Kong individual-stock valuation module for 02498.HK.

### Out of scope

- Reconstructing the complete historical constituent list for every historical trading day.
- Treating third-party reference PE as the primary calculation.
- Adding a server-side API to Netlify.
- Submitting the SQLite database to GitHub.

## Current Data Semantics

- A-share market value currently uses `close * outstanding_share`, which is a circulating-share proxy rather than guaranteed total market capitalization.
- TTM profit uses announcement-date alignment and decomposed cumulative reports. This remains the primary bottom-up method.
- Historical index and sector calculations use current cached constituent sets, so survivorship bias remains an explicit limitation.
- Hong Kong individual stocks are not mixed into A-share index aggregation.

## Architecture

### Update Reliability

`scripts/update_all.py` becomes the single local update entry point. It will maintain a checkpoint file outside Git-tracked output, record per-stock success/failure, support resuming, and only push static JSON after all required stages pass their failure threshold. `--export-only`, `--no-push`, `--resume`, and `--only` remain explicit modes.

The update stages are:

1. Load or create checkpoint.
2. Update stock quotes with merge semantics.
3. Refresh stale financial reports and quarterly-profit cache.
4. Recompute only selected or affected index/sector PE series.
5. Export static JSON.
6. Validate output files.
7. Commit and push only when validation succeeds.

### Data Quality

Use an actual A-share trading calendar from the available data source or a cached calendar table. PE rows include enough metadata to distinguish valid rows, coverage, and excluded-profit stocks. The UI must show the date basis, market-value basis, constituent basis, and coverage.

TTM decomposition will match periods by fiscal year. A testable standard-formula cross-check remains required for representative stocks.

### Performance

- Build year/period indexes once when decomposing financial reports.
- Add SQLite indexes for `stock_quotes(code, date)`, `stock_financials(code, publish_date)`, `stock_quarterly_profit(code, publish_date)`, and PE tables by index/date.
- Keep the existing bisect-based TTM snapshot lookup.
- Compute percentile values with a vectorized ranking operation.
- Avoid reloading the same constituent and quarterly data repeatedly inside one run.

### Frontend

The Streamlit and static frontends expose:

- Last data date and generated-at timestamp.
- Coverage ratio and valid-profit count.
- Market-value basis and constituent-history caveat.
- Clear empty/error states when JSON or reference data cannot load.
- Mobile-friendly chart sizing and default recent-eight-year view.

### Hong Kong Individual Stock Module

Add a separate data path for `02498.HK`:

- Fetch Hong Kong daily prices and financial indicators through AkShare.
- Store Hong Kong data in separate tables or a clearly separate namespace.
- Calculate market value only when share-count data is available; otherwise label it as estimated.
- If TTM profit is non-positive, do not display PE as a valuation conclusion.
- Display PS, revenue, net profit, EPS, price history, and profitability status.
- Clearly label the result as an auxiliary estimate, not a substitute for audited market data.

## Error Handling

- Network failures are retried with bounded backoff.
- Individual stock failures are recorded and do not silently disappear.
- If failure ratio exceeds the configured threshold, the update exits non-zero and does not push static output.
- Missing or stale data is shown as unavailable, never converted to zero.
- Static frontend fetch failures show an inline error and preserve the selector state.

## Testing and Acceptance

- Unit tests for same-year quarterly decomposition and standard TTM formula equivalence.
- Unit tests for checkpoint resume and failed-stock recording.
- SQLite schema/index smoke test against a temporary database.
- PE statistics test for percentile, rolling mean, and invalid-profit filtering.
- Static export test verifies `index_list.json` and every referenced JSON file.
- Frontend smoke test verifies the HTML, data paths, and JavaScript syntax.
- Hong Kong snapshot test verifies 02498.HK is marked loss-making and does not report a positive PE.
- Existing 34 A-share index/sector datasets remain readable.
- SQLite database remains excluded from Git.

## Delivery Order

1. Reliability and checkpointing.
2. Data-quality metadata and trading-calendar handling.
3. Performance and SQLite indexes.
4. Frontend status/error improvements.
5. Hong Kong individual-stock view.
6. Full verification and documentation.
