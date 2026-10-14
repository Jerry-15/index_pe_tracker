"""Constituent snapshots for CSI H30533.

The official CSI response is preferred.  ETF holdings are only a deliberately
labelled fallback and are never presented as an official index snapshot.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Callable, Mapping, Optional

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

    def __init__(self, message: str, code: str = "invalid_response", **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class H30533Source:
    """Load H30533 constituent snapshots from CSI, then ETF disclosure.

    The CSI endpoint is the public ``/csindex-home/index/constituents`` route;
    the request uses ``indexCode`` and the response maps ``data.asOfDate`` and
    ``data.constituents`` to the normalized snapshot.  The fallback endpoint
    is the 513050 holdings page, mapping ``as_of`` and ``holdings``.  These
    mappings reflect the payloads covered by the parser tests and are kept
    here so endpoint drift fails loudly instead of becoming a custom basket.
    """
    DEFAULT_OFFICIAL_URL = "https://www.csindex.com.cn/csindex-home/index/constituents"
    DEFAULT_ETF_URL = "https://www.efunds.com.cn/etrade/fund/513050/holdings"
    REQUIRED_COLUMNS = [
        "index_code", "security_code", "name", "exchange", "weight",
        "as_of", "source_type", "raw_metadata",
    ]
    COLUMNS = REQUIRED_COLUMNS

    def __init__(
        self,
        fetcher: Optional[Callable[[str, Mapping[str, Any]], Any]] = None,
        timeout: int = H30533_SOURCE_TIMEOUT,
        retries: int = H30533_SOURCE_RETRIES,
        official_url: Optional[str] = None,
        etf_url: Optional[str] = None,
    ) -> None:
        self.fetcher = fetcher or self._request_json
        self.timeout = timeout
        self.retries = retries
        self.official_url = official_url or os.getenv("H30533_OFFICIAL_URL", self.DEFAULT_OFFICIAL_URL)
        self.etf_url = etf_url or os.getenv("H30533_ETF_URL", self.DEFAULT_ETF_URL)
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
            ("csi_official", self.official_url, self.parse_official, {"indexCode": CHINA_INTL_INDEX_CODE}),
            ("etf_disclosure", self.etf_url, self.parse_etf_disclosure, {"fundCode": CHINA_INTL_FUND_CODE}),
        ):
            for attempt in range(self.retries + 1):
                try:
                    result = parser(self.fetcher(url, params))
                    if result.empty:
                        raise H30533SourceError("source returned no valid constituent rows", "empty_snapshot")
                    return result
                except H30533SourceError as exc:
                    errors.append({"source_type": source_type, "code": exc.code, "message": str(exc)})
                    if attempt < self.retries and exc.code == "transport_error":
                        time.sleep(0.2 * (attempt + 1))
                    else:
                        break
                except Exception as exc:
                    errors.append({"source_type": source_type, "code": "transport_error", "message": str(exc)})
                    if attempt < self.retries:
                        time.sleep(0.2 * (attempt + 1))
        raise H30533SourceError(
            "; ".join(error["message"] for error in errors),
            "all_sources_rejected", errors=errors
        )

    def parse_official(self, payload: Any) -> pd.DataFrame:
        return self._parse(payload, "csi_official")

    def parse_etf_disclosure(self, payload: Any) -> pd.DataFrame:
        return self._parse(payload, "etf_disclosure")

    def normalize_code(self, code: Any, exchange: Any) -> str:
        value = str(code).strip().upper()
        market = str(exchange or "").strip().upper()
        if not value:
            raise ValueError("invalid security code")
        market = {
            "HK": "HK", "HKG": "HK", "港股": "HK", "香港": "HK",
            "SH": "SH", "SSE": "SH", "沪": "SH", "上海": "SH",
            "SZ": "SZ", "SZSE": "SZ", "深": "SZ", "深圳": "SZ",
            "US": "US", "NASDAQ": "US", "NYSE": "US",
        }.get(market)
        if market is None:
            raise ValueError("unknown exchange")
        suffix = {"HK": ".HK", "SH": ".SH", "SZ": ".SZ"}.get(market)
        if suffix and value.endswith(suffix):
            value = value[:-len(suffix)]
        if market in {"HK", "SH", "SZ"} and not value.isdigit():
            raise ValueError("invalid security code")
        if market == "US" and not re.fullmatch(r"[A-Z][A-Z0-9.-]*", value):
            raise ValueError("invalid security code")
        if market == "HK":
            return f"{value.zfill(5)}.HK"
        if market == "SH":
            return f"{value.zfill(6)}.SH"
        if market == "SZ":
            return f"{value.zfill(6)}.SZ"
        return value

    def _parse(self, payload: Any, source_type: str) -> pd.DataFrame:
        self.rejected_rows = []
        self.last_source_type = source_type
        self._validate_index_identity(payload, source_type)
        date_fields = {
            "csi_official": ("data.asOfDate",),
            "etf_disclosure": ("as_of", "disclosureDate"),
        }[source_type]
        source_date_keys = ("asOfDate",) if source_type == "csi_official" else ("as_of", "disclosureDate")
        as_of = self._source_field(payload, source_type, *source_date_keys, data_only=source_type == "csi_official")
        if not as_of:
            raise H30533SourceError(
                f"{source_type} response has no as_of date",
                "missing_as_of",
                expected_fields=date_fields,
            )
        as_of = self._normalize_date(as_of)
        rows = self._find_source_rows(payload, source_type)
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
                if code is None or not str(code).strip():
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
        return pd.DataFrame(normalized, columns=self.REQUIRED_COLUMNS)

    @staticmethod
    def _validate_index_identity(payload: Any, source_type: str) -> None:
        identity_key = "indexCode" if source_type == "csi_official" else "fundCode"
        expected = CHINA_INTL_INDEX_CODE if source_type == "csi_official" else CHINA_INTL_FUND_CODE
        identity = H30533Source._source_field(
            payload, source_type, identity_key,
            "index_code" if source_type == "csi_official" else "fund_code",
            "indexId" if source_type == "csi_official" else "fundId",
        )
        if identity is None:
            raise H30533SourceError(
                f"{source_type} response does not identify its {('index' if source_type == 'csi_official' else 'fund')}",
                "missing_index_identity" if source_type == "csi_official" else "missing_fund_identity",
                expected=expected,
            )
        if str(identity).strip().upper() != expected:
            raise H30533SourceError(
                f"{source_type} response identifies {identity}, not {expected}",
                "wrong_index" if source_type == "csi_official" else "wrong_fund",
                expected=expected,
                actual=str(identity),
            )

    @staticmethod
    def _source_field(payload: Any, source_type: str, *keys: str, data_only: bool = False) -> Any:
        if not isinstance(payload, Mapping):
            return None
        containers = [payload]
        if source_type == "csi_official":
            if not isinstance(payload.get("data"), Mapping):
                return None if data_only else H30533Source._find_value(payload, *keys)
            containers = [payload["data"]] if data_only else [payload["data"], payload]
        for container in containers:
            value = H30533Source._find_value(container, *keys)
            if value is not None:
                return value
        return None

    @staticmethod
    def _find_source_rows(payload: Any, source_type: str) -> list[Mapping[str, Any]]:
        if not isinstance(payload, Mapping):
            raise H30533SourceError(
                "source response is not an object", "malformed_rows"
            )
        containers = [payload]
        if source_type == "csi_official" and isinstance(payload.get("data"), Mapping):
            containers.insert(0, payload["data"])
        fields = ("constituents", "rows") if source_type == "csi_official" else ("holdings", "rows")
        for container in containers:
            for field in fields:
                if field in container:
                    value = container[field]
                    if not isinstance(value, list) or not all(isinstance(row, Mapping) for row in value):
                        raise H30533SourceError(
                            f"{field} must contain object rows",
                            "malformed_rows",
                            field=field,
                        )
                    return value
        raise H30533SourceError(
            "source response has no constituent rows", "malformed_rows"
        )

    @staticmethod
    def _find_value(value: Any, *keys: str) -> Any:
        if not isinstance(value, Mapping):
            return None
        for key in keys:
            if key in value and value[key] not in (None, ""):
                return value[key]
        return None

    @staticmethod
    def _normalize_date(value: Any) -> str:
        text = str(value).strip()
        if not re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", text):
            raise H30533SourceError(
                f"invalid as_of date: {value}", "invalid_as_of", value=text
            )
        parsed = pd.to_datetime(text, format="%Y/%m/%d" if "/" in text else "%Y-%m-%d", errors="coerce")
        if pd.isna(parsed):
            raise H30533SourceError(
                f"invalid as_of date: {value}", "invalid_as_of", value=str(value)
            )
        return parsed.strftime("%Y-%m-%d")

    @staticmethod
    def _weight_fraction(value: Any) -> float:
        text = str(value).strip().replace(",", "").replace("%", "")
        number = float(text)
        return number / 100.0
