# Unified Security Data Architecture

## Goal

Create one auditable security-level data contract for A shares, Hong Kong stocks, ADRs, and H30533 constituents while migrating gradually and preserving the existing A-share/industry calculation path until the new standard is validated.

## Phased Scope

### Phase 1

- Add `SecurityRecord` standard model.
- Normalize Eulerpool/AkShare inputs into that model.
- Use the model in H30533 only.
- Keep existing A-share and industry PE formulas unchanged.
- Produce a side-by-side comparison report for old/new H30533 coverage and PE.

### Later phases

- Wrap A-share and industry records with `SecurityRecord`.
- Add PB, PS, ROE, FCF yield, revenue growth, and profit growth.
- Migrate SQLite/static schemas to versioned standard records.
- Remove legacy special paths only after comparison tests pass.

## SecurityRecord Contract

Each normalized record contains:

```text
security_code, isin, issuer_id, exchange, name
price, price_currency, quote_as_of, quote_source
market_cap, market_cap_currency, shares, market_cap_method
profit_ttm, normalized_profit_ttm, revenue_ttm, eps_ttm
book_value, operating_cash_flow, free_cash_flow
financial_as_of, financial_source, ads_ratio, fx_rate
quality_level, validity_reason, source_url, fetched_at, response_hash
```

Missing values remain `None`. A field is never filled with zero or a guessed conversion. `market_cap_method` must be one of `reported_market_cap`, `price_times_reported_shares`, `derived_profit_div_eps`, or `None`.

## Source Priority

- Eulerpool API is the primary provider for US/ADR profile, market cap, shares, LTM financials, quotes, and ISIN resolution.
- AkShare remains the fallback provider.
- CSI H30533 is the official constituent source when available.
- 513050 ETF disclosure is the explicitly labelled constituent fallback.
- Source URL, retrieval time, response hash, and report/as-of dates are preserved.

## Quality Rules

- Missing/unsupported currency or ADS ratio excludes a security from PE.
- Financial data older than 180 days is stale and excluded from current PE.
- Negative profit is counted and excluded from PE.
- Market-cap derivation is recorded and caps quality at `B`.
- Weight coverage below 95% produces quality `C`; current PE may be shown only as a clearly labelled research estimate when coverage is at least 80%.
- Historical current-constituent backfill always has quality `C` and a visible caveat.

## H30533 Migration

H30533 consumes `SecurityRecord` values through an adapter. Its weighted earnings-yield aggregator remains the only index calculation:

```text
earnings_yield_i = normalized_profit_i / market_cap_i
index_earnings_yield = sum(weight_i * earnings_yield_i)
index_pe = 1 / index_earnings_yield
```

The current H30533 path must persist enough normalized security data to recalculate the index PE independently from the exported rows.

## Compatibility

- Existing A-share/industry tables and output files remain readable.
- No regular static payload files are changed during Phase 1 except controlled test fixtures.
- Existing H30533 data is migrated through additive columns and retains historical caveats.
- API credentials remain environment-only and never enter source, database, JSON, or client-side code.

## Acceptance

- A fixture can create a `SecurityRecord` from Eulerpool profile, market-cap, and LTM financial payloads.
- H30533 uses Eulerpool data when available and AkShare when not.
- A live smoke test reports per-source coverage and quality level.
- New and old H30533 calculations can be compared without silently replacing the old result.
- Full tests pass before the production branch is changed.
