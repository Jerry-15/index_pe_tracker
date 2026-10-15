"""Shared valuation data quality gates."""

from datetime import date, datetime, timezone
import math


QUALITY_LEVELS = ("A", "B", "C")
REGULAR_SOURCE_TYPES = {
    "sina",
    "sw_official",
    "SinaDataSource",
    "SWOfficialDataSource",
}
MARKET_CAP_METHODS = (
    "reported_market_cap",
    "price_times_reported_shares",
    "derived_profit_div_eps",
)
_MIN_COVERAGE = 0.95


def _utc_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return parsed.astimezone(timezone.utc).date() if parsed.tzinfo else parsed.date()


def is_stale(report_date, as_of, max_age_days=180):
    report = _utc_date(report_date)
    reference = _utc_date(as_of)
    return report is None or reference is None or (reference - report).days > max_age_days


def quality_level(*, source_type, weight_coverage, financial_stale_count,
                  derived_market_cap_count, quote_disagreement_count):
    """Classify data quality without relaxing the H30533 safety gates.

    Complete, non-stale regular-source data is A when market cap is direct.
    Derived market caps are B for both regular and H30533 sources. Any
    incomplete, stale, or disputed data remains C before source-specific rules.
    """
    try:
        complete = math.isfinite(float(weight_coverage)) and float(weight_coverage) >= _MIN_COVERAGE
    except (TypeError, ValueError):
        complete = False
    if not complete or financial_stale_count != 0 or quote_disagreement_count != 0:
        return "C"
    if source_type == "csi_official" and derived_market_cap_count == 0:
        return "A"
    if source_type in REGULAR_SOURCE_TYPES and derived_market_cap_count == 0:
        return "A"
    if source_type == "etf_disclosure" or derived_market_cap_count > 0:
        return "B"
    return "C"


def append_reason(existing, code):
    values = []
    for value in (existing or "").split(";") + [code or ""]:
        value = value.strip()
        if value and value not in values:
            values.append(value)
    return ";".join(values)
