"""Constituent snapshots for CSI H30533.

The official CSI response is preferred.  ETF holdings are only a deliberately
labelled fallback and are never presented as an official index snapshot.
"""
from __future__ import annotations

import os
import re
import time
import math
import json
from html.parser import HTMLParser
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


class _ETFTableParser(HTMLParser):
    """Extract disclosure rows from Eastmoney's small HTML table."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: Optional[list[str]] = None
        self._cell: Optional[list[str]] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"th", "td"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
            self._row = None

    def parse(self, html: str) -> list[dict[str, str]]:
        self.feed(html)
        if len(self.rows) < 2:
            raise H30533SourceError("etf_disclosure response has no holdings table", "malformed_rows")
        headers = [re.sub(r"\s+", "", header) for header in self.rows[0]]

        def column(*names: str) -> int:
            for name in names:
                if name in headers:
                    return headers.index(name)
            for index, header in enumerate(headers):
                if any(header.startswith(name) for name in names):
                    return index
            raise H30533SourceError(
                "etf_disclosure holdings table is missing a required column",
                "malformed_rows",
                expected_fields=list(names),
            )

        code_index = column("股票代码", "证券代码", "成分券代码")
        name_index = column("股票名称", "证券名称", "成分券名称")
        weight_index = column("占净值比例", "占基金净值比例", "权重(%)", "权重")
        rows = []
        for values in self.rows[1:]:
            if max(code_index, name_index, weight_index) >= len(values):
                continue
            rows.append({
                "code": values[code_index],
                "name": values[name_index],
                "market": "HK" if re.fullmatch(r"\d{1,5}", values[code_index]) else "US",
                "weight_pct": values[weight_index],
            })
        return rows


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
        "https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
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
        self.fetcher = fetcher or self._request
        self.official_url = os.getenv("H30533_OFFICIAL_URL", self.OFFICIAL_URL)
        self.etf_url = os.getenv("H30533_ETF_URL", self.ETF_URL)
        self.timeout = timeout
        self.retries = retries
        self.rejected_rows: list[dict[str, Any]] = []
        self.source_errors: list[dict[str, Any]] = []
        self.last_source_type: Optional[str] = None

    def _request(self, url: str, params: Mapping[str, Any]) -> Any:
        response = requests.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        if "FundArchivesDatas.aspx" in url:
            response.encoding = response.apparent_encoding or response.encoding
            return response.text
        return response.json()

    def get_snapshot(self) -> pd.DataFrame:
        """Return the official snapshot, falling back to ETF disclosure."""
        errors = []
        self.source_errors = []
        for source_type, url, parser, params in (
            ("csi_official", self.official_url, self.parse_official, {"indexCode": CHINA_INTL_INDEX_CODE}),
            ("etf_disclosure", self.etf_url, self.parse_etf_disclosure, {
                "type": "jjcc",
                "code": CHINA_INTL_FUND_CODE,
                "topline": "10",
                "year": "",
                "month": "",
                "rt": "1",
            }),
        ):
            for attempt in range(self.retries + 1):
                try:
                    result = parser(self.fetcher(url, params))
                    if result.empty:
                        raise H30533SourceError("source returned no valid constituent rows")
                    return self.deduplicate_issuers(result)
                except H30533SourceError as exc:
                    error = {"source_type": source_type, "code": exc.code,
                             "message": str(exc), "details": exc.details}
                    self.source_errors.append(error)
                    errors.append(f"{source_type}: {exc}")
                    break
                except requests.RequestException as exc:
                    errors.append(f"{source_type}: {exc}")
                    if attempt < self.retries:
                        time.sleep(0.2 * (attempt + 1))
                    continue
                except Exception as exc:
                    errors.append(f"{source_type}: {exc}")
                    break
        raise H30533SourceError(
            "; ".join(errors),
            self.source_errors[0]["code"] if self.source_errors else "all_sources_rejected",
            errors=self.source_errors or errors,
        )

    def parse_official(self, payload: Any) -> pd.DataFrame:
        return self._parse(payload, "csi_official")

    def parse_etf_disclosure(self, payload: Any) -> pd.DataFrame:
        if isinstance(payload, str):
            payload = self._parse_etf_script(payload)
        return self._parse(payload, "etf_disclosure")

    @staticmethod
    def _parse_etf_script(payload: str) -> dict[str, Any]:
        match = re.search(
            r"(?:var\s+)?apidata\s*=\s*\{.*?content\s*:\s*(\"(?:\\.|[^\"])*\")",
            payload,
            flags=re.DOTALL,
        )
        if not match:
            raise H30533SourceError("etf_disclosure response has no apidata content", "malformed_response")
        try:
            content = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise H30533SourceError("etf_disclosure content is not valid JavaScript text", "malformed_response") from exc

        code = re.search(r"(?:^|[,\{])\s*(?:code|fundCode)\s*:\s*[\"']?(\d{6})", payload)
        date = re.search(
            r"(?:^|[,\{])\s*(?:reportDate|disclosureDate)\s*:\s*[\"']?(\d{4}[-/]\d{2}[-/]\d{2})",
            payload,
        )
        if not date:
            date = re.search(r"(?:截止日期|报告期|报告日期)\s*[:：]?\s*(\d{4}[-/]\d{2}[-/]\d{2}|\d{4}年\d{2}月\d{2}日)", content)
        if not date:
            raise H30533SourceError(
                "etf_disclosure response has no report date",
                "missing_as_of",
            )
        date_value = date.group(1).replace("年", "-").replace("月", "-").replace("日", "")
        rows = _ETFTableParser().parse(content)
        return {
            "fundCode": code.group(1) if code else CHINA_INTL_FUND_CODE,
            "as_of": date_value,
            "holdings": rows,
        }

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
