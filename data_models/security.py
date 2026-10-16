"""Provider-neutral, auditable security data contract."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
from typing import Any


MARKET_CAP_METHODS = {
    None,
    "reported_market_cap",
    "price_times_reported_shares",
    "derived_profit_div_eps",
}


@dataclass
class SecurityRecord:
    security_code: str
    isin: str | None = None
    issuer_id: str | None = None
    exchange: str | None = None
    name: str | None = None
    price: float | None = None
    price_currency: str | None = None
    quote_as_of: str | None = None
    quote_source: str | None = None
    market_cap: float | None = None
    market_cap_currency: str | None = None
    shares: float | None = None
    market_cap_method: str | None = None
    profit_ttm: float | None = None
    normalized_profit_ttm: float | None = None
    revenue_ttm: float | None = None
    eps_ttm: float | None = None
    book_value: float | None = None
    operating_cash_flow: float | None = None
    free_cash_flow: float | None = None
    financial_as_of: str | None = None
    financial_source: str | None = None
    ads_ratio: float | None = None
    fx_rate: float | None = None
    quality_level: str | None = None
    validity_reason: str | None = None
    source_url: str | None = None
    fetched_at: str | None = None
    response_hash: str | None = None

    def __post_init__(self):
        if not self.security_code or not self.security_code.strip():
            raise ValueError("security_code is required")
        if self.market_cap_method not in MARKET_CAP_METHODS:
            raise ValueError(f"unsupported market_cap_method: {self.market_cap_method}")
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{field.name} must be finite")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> "SecurityRecord":
        allowed = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in mapping.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def quality_reasons(self) -> list[str]:
        return [item for item in (self.validity_reason or "").split(";") if item]
