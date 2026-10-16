"""Optional Eulerpool provider for verified equity market data.

The API token is read only from ``EULERPOOL_API_KEY`` and is never persisted.
"""
from __future__ import annotations

import os
from typing import Any

import requests
from data_models.security import SecurityRecord


class EulerpoolProvider:
    BASE_URL = "https://api.eulerpool.com/api/1"

    def __init__(self, token: str | None = None, timeout: int = 20):
        self.token = token or os.getenv("EULERPOOL_API_KEY")
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.token)

    def _get(self, path: str) -> dict[str, Any] | None:
        if not self.token:
            return None
        try:
            response = requests.get(
                f"{self.BASE_URL}/{path.lstrip('/')}",
                params={"token": self.token},
                headers={"User-Agent": "index-pe-tracker/1.0"},
                timeout=self.timeout,
            )
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    def profile(self, symbol: str) -> dict[str, Any] | None:
        return self._get(f"equity/profile/{symbol}")

    def market_cap(self, symbol: str) -> dict[str, Any] | None:
        return self._get(f"equity/market-cap/{symbol}")

    def overview(self, symbol: str) -> dict[str, Any] | None:
        return self._get(f"equity/overview/{symbol}")

    def metrics(self, symbol: str) -> dict[str, Any] | None:
        return self._get(f"equity/metrics/{symbol}")

    def financials_ltm(self, symbol: str) -> dict[str, Any] | None:
        return self._get(f"equity/financials-ltm/{symbol}")

    def income_statement(self, symbol: str) -> list[dict[str, Any]] | None:
        payload = self._get(f"equity/incomestatement/{symbol}")
        return payload if isinstance(payload, list) else None

    def quotes(self, symbol: str, range_name: str = "5y") -> list[dict[str, Any]] | None:
        return self._get(f"equity/quotes/{symbol}")

    def market_cap_history(self, symbol: str, range_name: str = "5y") -> dict[str, Any] | None:
        return self._get(f"equity/market-cap/{symbol}")

    def search(self, query: str) -> list[dict[str, Any]]:
        payload = self._get(f"screener/search/{query}")
        return payload if isinstance(payload, list) else []

    def resolve_isin(self, query: str, preferred_ticker: str | None = None) -> str | None:
        for item in self.search(query):
            ticker = str(item.get("ticker") or "").upper()
            if preferred_ticker and ticker == preferred_ticker.upper():
                return item.get("isin")
        for item in self.search(query):
            if item.get("isin"):
                return item["isin"]
        return None

    def verified_inputs_for(self, identifiers: list[str]) -> dict[str, Any] | None:
        """Try preferred identifiers in order, usually ISIN before ticker."""
        for identifier in identifiers:
            result = self.verified_inputs(identifier)
            if result:
                result["identifier"] = identifier
                return result
        return None

    def security_record(self, identifiers: list[str], security_code: str) -> SecurityRecord | None:
        inputs = self.verified_inputs_for(identifiers)
        financial = self.financial_inputs(identifiers)
        if not inputs and not financial:
            return None
        merged = {"security_code": security_code}
        merged.update(inputs or {})
        merged.update(financial or {})
        merged["market_cap_method"] = "reported_market_cap" if merged.get("market_cap") is not None else None
        merged["price_currency"] = merged.get("market_cap_currency") or merged.get("currency")
        return SecurityRecord.from_mapping(merged)

    def financial_inputs(self, identifiers: list[str]) -> dict[str, Any] | None:
        """Return normalized LTM profit/EPS/date alongside profile currency."""
        for identifier in identifiers:
            profile = self.profile(identifier) or {}
            financials = self.financials_ltm(identifier) or {}
            income = financials.get("income", {}) if isinstance(financials, dict) else {}
            profit = income.get("netIncome") or income.get("bottomLineNetIncome")
            eps = income.get("epsDiluted") or income.get("eps")
            as_of = financials.get("asOf") if isinstance(financials, dict) else None
            if profit is None and eps is None:
                continue
            return {
                "profit_ttm": float(profit) * 1_000_000 if profit is not None else None,
                "eps_ttm": float(eps) if eps is not None else None,
                "financial_as_of": as_of,
                "currency": profile.get("currency"),
                "source": "eulerpool",
                "data_source": "eulerpool",
            }
        return None

    def verified_inputs(self, symbol: str) -> dict[str, Any] | None:
        profile = self.profile(symbol) or {}
        market = self.market_cap(symbol) or {}
        mcap = profile.get("mcap") or market.get("marketCap")
        shares = profile.get("shares") or market.get("sharesOutstanding")
        currency = profile.get("currency") or market.get("currency")
        if mcap is None and shares is None:
            return None
        return {
            "market_cap": float(mcap) * 1_000_000 if mcap is not None else None,
            "verified_shares": float(shares) * 1_000_000 if shares is not None else None,
            "market_cap_currency": currency,
            "source": "eulerpool",
            "data_source": "eulerpool",
        }
