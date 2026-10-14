"""Constituent snapshots for CSI H30533.

The official CSI response is preferred.  ETF holdings are only a deliberately
labelled fallback and are never presented as an official index snapshot.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Iterable, Mapping, Optional

import pandas as pd
import requests

from config import (
    CHINA_INTL_FUND_CODE,
    CHINA_INTL_INDEX_CODE,
    H30533_SOURCE_RETRIES,
    H30533_SOURCE_TIMEOUT,
)


class H30533SourceError(RuntimeError):
    """Raised when a source response cannot produce a valid snapshot."""


class H30533Source:
    OFFICIAL_URL = os.getenv(
        "H30533_OFFICIAL_URL",
        "https://www.csindex.com.cn/csindex-home/index/constituents",
    )
    ETF_URL = os.getenv(
        "H30533_ETF_URL",
        "https://www.efunds.com.cn/etrade/fund/513050/holdings",
    )
    COLUMNS = [
        "index_code", "security_code", "name", "exchange", "weight",
        "as_of", "source_type", "raw_metadata",
    ]

    def __init__(
        self,
        fetcher: Optional[Callable[[str, Mapping[str, Any]], Any]] = None,
        timeout: int = H30533_SOURCE_TIMEOUT,
        retries: int = H30533_SOURCE_RETRIES,
    ) -> None:
        self.fetcher = fetcher or self._request_json
        self.timeout = timeout
        self.retries = retries
        self.rejected_rows: list[dict[str, Any]] = []
        self.last_source_type: Optional[str] = None

    def _request_json(self, url: str, params: Mapping[str, Any]) -> Any:
        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_snapshot(self) -> pd.DataFrame:
        """Return the official snapshot, falling back to ETF disclosure."""
        errors = []
        for source_type, url, parser, params in (
            ("csi_official", self.OFFICIAL_URL, self.parse_official, {"indexCode": CHINA_INTL_INDEX_CODE}),
            ("etf_disclosure", self.ETF_URL, self.parse_etf_disclosure, {"fundCode": CHINA_INTL_FUND_CODE}),
        ):
            for attempt in range(self.retries + 1):
                try:
                    result = parser(self.fetcher(url, params))
                    if result.empty:
                        raise H30533SourceError("source returned no valid constituent rows")
                    return result
                except Exception as exc:
                    errors.append(f"{source_type}: {exc}")
                    if attempt < self.retries:
                        time.sleep(0.2 * (attempt + 1))
        raise H30533SourceError("; ".join(errors))

    def parse_official(self, payload: Any) -> pd.DataFrame:
        return self._parse(payload, "csi_official")

    def parse_etf_disclosure(self, payload: Any) -> pd.DataFrame:
        return self._parse(payload, "etf_disclosure")

    def normalize_code(self, code: Any, exchange: Any) -> str:
        value = str(code).strip().upper()
        market = str(exchange or "").strip().upper()
        if value.endswith((".HK", ".SH", ".SZ", ".US")):
            return value
        if market in {"HK", "HKG", "港股", "香港"}:
            return f"{value.zfill(5)}.HK"
        if market in {"SH", "SSE", "沪", "上海"}:
            return f"{value.zfill(6)}.SH"
        if market in {"SZ", "SZSE", "深", "深圳"}:
            return f"{value.zfill(6)}.SZ"
        return value

    def _parse(self, payload: Any, source_type: str) -> pd.DataFrame:
        self.rejected_rows = []
        self.last_source_type = source_type
        as_of = self._find_nested_value(payload, "as_of", "asOfDate", "date", "disclosureDate")
        if not as_of:
            raise H30533SourceError(f"{source_type} response has no as_of date")
        as_of = self._normalize_date(as_of)
        rows = self._find_rows(payload)
        normalized = []
        for raw in rows:
            try:
                code = self._find_value(
                    raw, "security_code", "securityCode", "code", "stockCode", "成分券代码", "证券代码"
                )
                name = self._find_value(raw, "name", "securityName", "stockName", "成分券名称", "证券简称")
                exchange = self._find_value(raw, "exchange", "market", "marketCode", "交易所")
                weight_raw = self._find_value(
                    raw, "weight", "weight_pct", "weightPercent", "ratio", "权重", "权重(%)"
                )
                if not code:
                    raise ValueError("missing security code")
                if weight_raw is None or str(weight_raw).strip() == "":
                    raise ValueError("missing weight")
                try:
                    weight = self._weight_fraction(weight_raw)
                except ValueError as exc:
                    raise ValueError(f"invalid weight: {exc}") from exc
                if not 0 <= weight <= 1:
                    raise ValueError("weight out of range")
                normalized.append({
                    "index_code": CHINA_INTL_INDEX_CODE,
                    "security_code": self.normalize_code(code, exchange),
                    "name": str(name or "").strip(),
                    "exchange": str(exchange or "").strip().upper(),
                    "weight": weight,
                    "as_of": as_of,
                    "source_type": source_type,
                    "raw_metadata": dict(raw),
                })
            except (TypeError, ValueError) as exc:
                self.rejected_rows.append({"row": raw, "reason": str(exc)})
        return pd.DataFrame(normalized, columns=self.COLUMNS)

    @staticmethod
    def _find_rows(payload: Any) -> list[Mapping[str, Any]]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, Mapping):
            raise H30533SourceError("source response is not an object or list")
        for key in ("constituents", "holdings", "data", "result", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, Mapping):
                try:
                    return H30533Source._find_rows(value)
                except H30533SourceError:
                    pass
        raise H30533SourceError("source response has no constituent rows")

    @staticmethod
    def _find_value(value: Any, *keys: str) -> Any:
        if not isinstance(value, Mapping):
            return None
        for key in keys:
            if key in value and value[key] not in (None, ""):
                return value[key]
        return None

    @staticmethod
    def _find_nested_value(value: Any, *keys: str) -> Any:
        result = H30533Source._find_value(value, *keys)
        if result is not None:
            return result
        if isinstance(value, Mapping):
            for nested in value.values():
                if isinstance(nested, Mapping):
                    result = H30533Source._find_nested_value(nested, *keys)
                    if result is not None:
                        return result
        return None

    @staticmethod
    def _normalize_date(value: Any) -> str:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            raise H30533SourceError(f"invalid as_of date: {value}")
        return parsed.strftime("%Y-%m-%d")

    @staticmethod
    def _weight_fraction(value: Any) -> float:
        text = str(value).strip().replace(",", "").replace("%", "")
        number = float(text)
        return number / 100.0
