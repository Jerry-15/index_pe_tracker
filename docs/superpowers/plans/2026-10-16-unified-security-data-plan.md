# Unified Security Data Implementation Plan

> **For agentic workers:** Execute task-by-task with tests and review checkpoints. Do not push production until the local live comparison and full test suite pass.

**Goal:** Introduce a unified auditable security data layer, use it for H30533 first, and compare old/new valuation results before migrating other indices.

**Architecture:** Add a typed `SecurityRecord` normalization layer independent of providers. Eulerpool is the primary US/ADR provider with AkShare fallback; H30533 consumes normalized records, while A-share and industry calculations remain unchanged in Phase 1.

**Tech Stack:** Python dataclasses, pandas, requests, SQLite, unittest, Eulerpool REST API.

---

## Task 1: SecurityRecord Contract

**Files:**
- Create `data_models/security.py`
- Test `tests/test_security_record.py`

- [ ] Write failing tests for required fields, allowed market-cap methods, missing-value preservation, and serialization without secrets.
- [ ] Run `python3 -m unittest tests.test_security_record -v` and confirm failure.
- [ ] Implement a dataclass with `from_mapping`, `to_dict`, and `quality_reasons` methods. Reject non-finite numeric values and unknown market-cap methods.
- [ ] Verify the API key never appears in `to_dict`.
- [ ] Run focused/full tests and commit `feat: add unified security record`.

## Task 2: Eulerpool Normalizer

**Files:**
- Modify `data_sources/eulerpool.py`
- Create `data_sources/security_provider.py`
- Test `tests/test_security_provider.py`

- [ ] Add fixture tests for Eulerpool profile, market-cap, income LTM, shares, and quote payloads.
- [ ] Normalize millions/units, currency, ISIN, report date, market-cap method, and source provenance into `SecurityRecord`.
- [ ] Add a provider fallback that uses AkShare only when Eulerpool lacks a required field or returns 401/404/timeout.
- [ ] Add per-symbol coverage diagnostics and never default ADS/currency/share values.
- [ ] Run tests and commit `feat: normalize Eulerpool security data`.

## Task 3: H30533 Adapter Migration

**Files:**
- Modify `etl/pipeline_us.py`
- Modify `data_sources/us.py`
- Test `tests/test_h30533.py`

- [ ] Add a failing test proving an Eulerpool fixture becomes a SecurityRecord and is accepted by H30533 aggregation.
- [ ] Replace ad hoc current-PE field assembly with the normalizer while preserving the existing weighted earnings-yield formula and quality gates.
- [ ] Persist normalized currency, FX, method, source, dates, and provenance in the existing additive H30533 schema.
- [ ] Keep a comparison function that calculates old and new coverage/PE without changing the old result automatically.
- [ ] Run tests and commit `feat: migrate H30533 to unified security records`.

## Task 4: Metrics and UI Contract

**Files:**
- Modify `etl/pipeline_us.py`
- Modify `scripts/export_static_data.py`
- Modify `app/main.py`
- Modify `static/index.html`
- Test `tests/test_static_export.py`, `tests/test_h30533.py`

- [ ] Add normalized PB, PS, ROE, FCF yield, revenue growth, and profit growth fields where verified.
- [ ] Export versioned `SecurityRecord` fields and quality metadata without exposing credentials.
- [ ] Display one common quality/source panel; H30533 only adds constituent diagnostics.
- [ ] Preserve unavailable values as `null` and show the exact gate reason.
- [ ] Run static JSON, manifest, Python, JavaScript, and test checks; commit `feat: expose unified security metrics`.

## Task 5: Local Live Comparison

**Files:**
- Create `scripts/compare_valuation_paths.py`
- Test `tests/test_security_provider.py`

- [ ] Set `EULERPOOL_API_KEY` only in the shell environment.
- [ ] Fetch all current 513050 constituents and report source coverage by Eulerpool/AkShare.
- [ ] Calculate old/new H30533 coverage, quality, and PE side by side.
- [ ] Independently recompute new PE from exported security rows and assert equality within rounding tolerance.
- [ ] Keep the production branch unchanged until the report is reviewed locally.

## Task 6: Release Gate

- [ ] Run `python3 -m unittest discover -s tests -p 'test*.py' -v`.
- [ ] Run `python3 -m compileall -q app cache data_sources etl scripts tests`.
- [ ] Run static Node syntax and JSON/manifest reference checks.
- [ ] Confirm no regular `static/data/*.json` files are staged unintentionally.
- [ ] Push feature branch, then merge/push `main` only after all local checks pass.
