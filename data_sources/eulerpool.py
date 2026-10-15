"""Optional Eulerpool provider for verified equity market data.

The API token is read only from ``EULERPOOL_API_KEY`` and is never persisted.
"""
from __future__ import annotations

import os
from typing import Any

import requests


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
        response = requests.get(
            f"{self.BASE_URL}/{path.lstrip('/')}",
            params={"token": self.token},
            headers={"User-Agent": "index-pe-tracker/1.0"},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    def profile(self, symbol: str) -> dict[str, Any] | None:
        return self._get(f"equity/profile/{symbol}")

    def market_cap(self, symbol: str) -> dict[str, Any] | None:
        return self._get(f"equity/market-cap/{symbol}")

    def verified_inputs_for(self, identifiers: list[str]) -> dict[str, Any] | None:
        """Try preferred identifiers in order, usually ISIN before ticker."""
        for identifier in identifiers:
            result = self.verified_inputs(identifier)
            if result:
                result["identifier"] = identifier
                return result
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
