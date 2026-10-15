"""Shared valuation data quality gates."""

from datetime import date, datetime, timezone
import math


QUALITY_LEVELS = ("A", "B", "C")
MARKET_CAP_METHODS = (
    "reported_market_cap",
    "price_times_reported_shares",
    "derived_profit_div_eps",
)
_MIN_COVERAGE = 0.95


def _utc_date(value):
    """Return an input date in UTC, or ``None`` when it is not valid."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.astimezone(timezone.utc).date()


def is_stale(report_date: str | None, as_of: str | None, max_age_days: int = 180) -> bool:
    """Return whether a report is missing, malformed, or too old as of UTC."""
    report = _utc_date(report_date)
    reference = _utc_date(as_of)
    if report is None or reference is None:
        return True
    return (reference - report).days > max_age_days


def quality_level(
    *,
    source_type: str | None,
    weight_coverage: float,
    financial_stale_count: int,
    derived_market_cap_count: int,
    quote_disagreement_count: int,
) -> str:
    """Grade valuation inputs conservatively from provenance and diagnostics."""
    try:
        complete_coverage = math.isfinite(float(weight_coverage)) and float(weight_coverage) >= _MIN_COVERAGE
    except (TypeError, ValueError):
        complete_coverage = False
    no_disqualifying_issues = (
        financial_stale_count is not None
        and financial_stale_count == 0
        and quote_disagreement_count is not None
        and quote_disagreement_count == 0
    )
    if not complete_coverage or not no_disqualifying_issues:
        return "C"
    if source_type == "csi_official" and derived_market_cap_count == 0:
        return "A"
    if source_type == "etf_disclosure" or (derived_market_cap_count is not None and derived_market_cap_count > 0):
        return "B"
    return "C"


def append_reason(existing: str | None, code: str) -> str:
    """Return canonical semicolon-separated diagnostic codes in first-seen order."""
    codes = []
    for item in (existing or "").split(";") + [code or ""]:
        item = item.strip()
        if item and item not in codes:
            codes.append(item)
    return ";".join(codes)


def is_valid_market_cap_method(value: str | None) -> bool:
    """Return whether a market-cap method is one of the supported values."""
    return value is None or value in MARKET_CAP_METHODS
