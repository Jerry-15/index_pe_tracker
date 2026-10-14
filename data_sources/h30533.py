"""Constituent snapshots for CSI H30533.

The official CSI response is preferred.  ETF holdings are only a deliberately
labelled fallback and are never presented as an official index snapshot.
"""
from __future__ import annotations

import os
import re
import time
import math
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
    OFFICIAL_URL = os.getenv(
        "H30533_OFFICIAL_URL",
        "https://www.csindex.com.cn/csindex-home/index/constituents",
    )
    ETF_URL = os.getenv(
        "H30533_ETF_URL",
        "https://www.efunds.com.cn/etrade/fund/513050/holdings",
    )
    REQUIRED_COLUMNS = [
        "index_code", "security_code", "name", "exchange", "weight",
        "as_of", "source_type", "issuer_id", "security_type", "ads_ratio",
        "is_duplicate_issuer", "raw_metadata",
    ]
    COLUMNS = REQUIRED_COLUMNS

    def __init__(
        self,
        fetcher: Optional[Callable[[str, Mapping[str, Any]], Any]] = None,
        timeout: int = H30533_SOURCE_TIMEOUT,
        retries: int = H30533_SOURCE_RETRIES,
    ) -> None:
        self.fetcher = fetcher or self._request_json
        self.official_url = os.getenv("H30533_OFFICIAL_URL", self.OFFICIAL_URL)
        self.etf_url = os.getenv("H30533_ETF_URL", self.ETF_URL)
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
            ("csi_official", self.official_url, self.parse_official, {"indexCode": CHINA_INTL_INDEX_CODE}),
            ("etf_disclosure", self.etf_url, self.parse_etf_disclosure, {"fundCode": CHINA_INTL_FUND_CODE}),
        ):
            for attempt in range(self.retries + 1):
                try:
                    result = parser(self.fetcher(url, params))
                    if result.empty:
                        raise H30533SourceError("source returned no valid constituent rows")
                    return self.deduplicate_issuers(result)
                except H30533SourceError:
                    raise
                except requests.RequestException as exc:
                    errors.append(f"{source_type}: {exc}")
                    if attempt < self.retries:
                        time.sleep(0.2 * (attempt + 1))
                    continue
                except Exception as exc:
                    errors.append(f"{source_type}: {exc}")
                    break
        raise H30533SourceError(
            "; ".join(errors), "all_sources_rejected", errors=errors
        )

    def parse_official(self, payload: Any) -> pd.DataFrame:
        return self._parse(payload, "csi_official")

    def parse_etf_disclosure(self, payload: Any) -> pd.DataFrame:
        return self._parse(payload, "etf_disclosure")

    def normalize_code(self, code: Any, exchange: Any) -> str:
        value = str(code)
        if not value or value != value.strip() or re.search(r"\s", value):
            raise ValueError("invalid security code")
        value = value.upper()
        market = str(exchange or "").strip().upper()
        if value.endswith((".HK", ".SH", ".SZ", ".US")):
            if not re.fullmatch(r"(?:\d{5}\.HK|\d{6}\.(?:SH|SZ)|[A-Z0-9.-]+\.US)", value):
                raise ValueError("invalid security code")
            return value
        if market in {"HK", "HKG", "港股", "香港"}:
            if not re.fullmatch(r"\d{1,5}", value):
                raise ValueError("invalid security code")
            return f"{value.zfill(5)}.HK"
        if market in {"SH", "SSE", "沪", "上海"}:
            if not re.fullmatch(r"\d{1,6}", value):
                raise ValueError("invalid security code")
            return f"{value.zfill(6)}.SH"
        if market in {"SZ", "SZSE", "深", "深圳"}:
            if not re.fullmatch(r"\d{1,6}", value):
                raise ValueError("invalid security code")
            return f"{value.zfill(6)}.SZ"
        if not re.fullmatch(r"[A-Z0-9.-]+", value):
            raise ValueError("invalid security code")
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
        seen_security_codes = set()
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
                security_code = self.normalize_code(code, exchange)
                if security_code in seen_security_codes:
                    self.rejected_rows.append({"row": raw, "reason": "duplicate security code"})
                    continue
                seen_security_codes.add(security_code)
                normalized.append({
                    "index_code": CHINA_INTL_INDEX_CODE,
                    "security_code": security_code,
                    "name": str(name or "").strip(),
                    "exchange": str(exchange or "").strip().upper(),
                    "weight": weight,
                    "as_of": as_of,
                    "source_type": source_type,
                    "issuer_id": H30533Source._issuer_id(
                        raw, "issuer_id", "issuerId", "issuerCode", "issuer_code"
                    ),
                    "security_type": H30533Source._optional_text(
                        raw, "security_type", "securityType", "instrumentType"
                    ),
                    "ads_ratio": H30533Source._optional_number(raw, "ads_ratio", "adsRatio"),
                    "is_duplicate_issuer": False,
                    "raw_metadata": dict(raw),
                })
            except (TypeError, ValueError) as exc:
                self.rejected_rows.append({"row": raw, "reason": str(exc)})
        result = pd.DataFrame(normalized, columns=self.REQUIRED_COLUMNS)
        known_issuers = set()
        for index, row in result.iterrows():
            issuer_id = row["issuer_id"]
            if pd.notna(issuer_id) and issuer_id in known_issuers:
                result.at[index, "is_duplicate_issuer"] = True
            elif pd.notna(issuer_id):
                known_issuers.add(issuer_id)
        return result

    @staticmethod
    def deduplicate_issuers(snapshot: pd.DataFrame) -> pd.DataFrame:
        """Return one official security per explicitly identified issuer."""
        if snapshot.empty:
            return snapshot.copy()
        selected = []
        for issuer_id, group in snapshot.groupby("issuer_id", dropna=False, sort=False):
            if pd.isna(issuer_id):
                selected.extend(group.to_dict("records"))
                continue
            ranked = group.assign(
                _ordinary=group["security_type"].fillna("").str.strip().str.lower().isin(
                    {"ordinary", "common", "common_stock"}
                ).astype(int)
            ).sort_values(["_ordinary", "security_code"], ascending=[False, True], kind="stable")
            row = ranked.iloc[0].drop(labels="_ordinary").to_dict()
            metadata = dict(row.get("raw_metadata") or {})
            metadata["duplicate_issuer_rows"] = [
                dict(item.get("raw_metadata") or {}) for item in group.to_dict("records")
            ]
            metadata["duplicate_issuer_rows"] = sorted(
                metadata["duplicate_issuer_rows"],
                key=lambda item: str(item.get("security_code", item.get("code", ""))),
            )
            row["raw_metadata"] = metadata
            row["is_duplicate_issuer"] = False
            selected.append(row)
        return (
            pd.DataFrame(selected, columns=snapshot.columns)
            .sort_values("security_code", kind="stable")
            .reset_index(drop=True)
        )

    @staticmethod
    def _validate_index_identity(payload: Any, source_type: str) -> None:
        if source_type == "etf_disclosure":
            identity = H30533Source._source_field(payload, source_type, "fundCode", "fund_code", "fundId")
            if identity is None:
                raise H30533SourceError(
                    "etf_disclosure response does not identify a fund",
                    "missing_fund_identity",
                    expected=CHINA_INTL_FUND_CODE,
                )
            if str(identity).strip() != CHINA_INTL_FUND_CODE:
                raise H30533SourceError(
                    f"etf_disclosure response identifies {identity}, not {CHINA_INTL_FUND_CODE}",
                    "wrong_fund",
                    expected=CHINA_INTL_FUND_CODE,
                    actual=str(identity),
                )
            return
        if source_type != "csi_official":
            return
        identity = H30533Source._source_field(
            payload, source_type, "indexCode", "index_code", "indexId"
        )
        if identity is None:
            raise H30533SourceError(
                "csi_official response does not identify an index",
                "missing_index_identity",
                expected=CHINA_INTL_INDEX_CODE,
            )
        if str(identity).strip().upper() != CHINA_INTL_INDEX_CODE:
            raise H30533SourceError(
                f"csi_official response identifies {identity}, not {CHINA_INTL_INDEX_CODE}",
                "wrong_index",
                expected=CHINA_INTL_INDEX_CODE,
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
    def _optional_text(value: Mapping[str, Any], *keys: str) -> Optional[str]:
        found = H30533Source._find_value(value, *keys)
        return str(found).strip() if found is not None else None

    @staticmethod
    def _optional_number(value: Mapping[str, Any], *keys: str) -> Optional[float]:
        found = H30533Source._find_value(value, *keys)
        if found is None:
            return None
        try:
            number = float(found)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid numeric metadata: {found}") from exc
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"invalid ads_ratio: {found}")
        return int(number) if number.is_integer() else number

    @staticmethod
    def _issuer_id(value: Mapping[str, Any], *keys: str) -> Optional[str]:
        for key in keys:
            if key in value and value[key] is not None:
                issuer_id = str(value[key]).strip().upper()
                if not issuer_id:
                    raise ValueError("blank issuer_id")
                return issuer_id
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
