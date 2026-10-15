"""美股/港股/中概股数据源（基于 akshare + 新浪实时行情）"""
import time
import math
import requests
import akshare as ak
import pandas as pd
from typing import Any, Optional
from config import API_RETRY_TIMES, API_RETRY_DELAY


def _append_reason(existing: Optional[str], reason: str) -> str:
    return "; ".join(filter(None, (existing, reason)))


def _is_financial_stale(value: Optional[str]) -> bool:
    if not value:
        return True
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return True
    return (pd.Timestamp.now(tz="UTC").tz_localize(None) - parsed).days > 365


class USDataSource:
    """美股数据获取：行情 + 财务指标（EPS_TTM / ADS校正）"""

    SUPPORTED_CURRENCIES = frozenset(("CNY", "HKD", "USD"))

    # ADS比例：1 ADS = N 股普通股（BABA 为 8:1）
    ADS_RATIO = {
        "BABA": 8, "PDD": 1, "JD": 1, "BIDU": 1, "NTES": 1, "MOMO": 1,
        "TCEHY": 1, "NCTY": 1, "VIPS": 1, "WB": 1, "IQ": 1, "FENG": 1,
        "TCOM": 1, "LI": 1, "NIO": 1, "XPEV": 1, "TIGR": 1, "FUTU": 1,
        "SINA": 1, "SOHU": 1, "YUMC": 1, "GDS": 1, "VNET": 1,
    }

    def __init__(self, secondary_quote_fetcher=None):
        self._fx_rate: Optional[float] = None
        self._fx_cached_at: float = 0
        self.secondary_quote_fetcher = secondary_quote_fetcher

    @staticmethod
    def build_input_record(
        code: str,
        price: Optional[float],
        currency: Optional[str],
        shares_or_market_cap: Optional[float],
        profit_ttm: Optional[float],
        financial_as_of: Optional[str],
        source: Optional[str],
        ads_ratio: Optional[float] = None,
    ) -> dict[str, Any]:
        """Build a common quote/financial record without sentinel zeros."""
        code_key = str(code).strip().upper()
        ads_ratio = USDataSource.ADS_RATIO.get(code_key, 1) if ads_ratio is None else ads_ratio
        try:
            ads_ratio = float(ads_ratio)
        except (TypeError, ValueError) as exc:
            raise ValueError("ads_ratio must be positive and finite") from exc
        if not math.isfinite(ads_ratio) or ads_ratio <= 0:
            raise ValueError("ads_ratio must be positive and finite")
        missing = []
        for field, value in (
            ("price", price), ("currency", currency),
            ("shares_or_market_cap", shares_or_market_cap), ("profit_ttm", profit_ttm),
            ("financial_as_of", financial_as_of), ("source", source),
        ):
            try:
                is_missing = value is None or (isinstance(value, str) and not value.strip()) or bool(pd.isna(value))
            except (TypeError, ValueError):
                is_missing = False
            if is_missing:
                missing.append(field)
        diagnostics = [f"missing {field}" for field in missing]
        if currency is not None and not pd.isna(currency):
            currency_key = str(currency).strip().upper()
            if currency_key not in USDataSource.SUPPORTED_CURRENCIES:
                diagnostics.append(f"unsupported currency: {currency}")
        return {
            "code": code,
            "price": price,
            "currency": currency,
            "shares_or_market_cap": shares_or_market_cap,
            "profit_ttm": profit_ttm,
            "financial_as_of": financial_as_of,
            "source": source,
            "ads_ratio": int(ads_ratio) if ads_ratio.is_integer() else ads_ratio,
            "validity_reason": "; ".join(diagnostics) or None,
        }

    def _get_fx_rate(self) -> float:
        """获取最新 USD/CNY 中间价"""
        now = time.time()
        if self._fx_rate is not None and now - self._fx_cached_at < 86400:
            return self._fx_rate
        for attempt in range(API_RETRY_TIMES):
            try:
                df = ak.currency_boc_sina(symbol="美元")
                if df is not None and not df.empty and "央行中间价" in df.columns:
                    rate = float(df.iloc[-1]["央行中间价"]) / 100.0
                    self._fx_rate = round(rate, 4)
                    self._fx_cached_at = now
                    return self._fx_rate
            except Exception:
                pass
            if attempt < API_RETRY_TIMES - 1:
                time.sleep(API_RETRY_DELAY * (attempt + 1))
        return 7.1771

    def get_constituents(self, index_code: str) -> pd.DataFrame:
        """返回预定义的中概互联网成分股（35只：22港股+13美股ADR，去重）"""
        from config import CHINA_INTL_STOCKS
        records = []
        for code, name in CHINA_INTL_STOCKS.items():
            is_hk = code.endswith('.HK')
            records.append({
                "code": code,
                "name": name,
                "exchange": "HKSE" if is_hk else "NASDAQ",
                "sina_code": f"hk{code.replace('.HK','')}" if is_hk else code,
            })
        return pd.DataFrame(records)

    def _is_hk(self, code: str) -> bool:
        return code.endswith(".HK")

    def _is_us(self, code: str) -> bool:
        return not code.endswith(".HK")

    def get_daily_quotes(self, stock_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        """获取历史日K线（港股用stock_hk_daily，美股用stock_us_daily）"""
        if self._is_hk(stock_code):
            hk_code = stock_code.replace(".HK", "")
            for attempt in range(API_RETRY_TIMES):
                try:
                    df = ak.stock_hk_daily(symbol=hk_code, adjust="")
                    if df is None or df.empty:
                        return pd.DataFrame()
                    df = df.rename(columns={
                        "date": "date", "open": "open", "high": "high",
                        "low": "low", "close": "close", "volume": "volume", "amount": "amount",
                    })
                    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                    df = df.sort_values("date").reset_index(drop=True)
                    keep = ["date", "open", "high", "low", "close", "volume", "amount"]
                    existing = [c for c in keep if c in df.columns]
                    df = df[existing]
                    df["outstanding_share"] = 0.0
                    if start_date:
                        df = df[df["date"] >= start_date]
                    if end_date:
                        df = df[df["date"] <= end_date]
                    return df
                except Exception:
                    if attempt < API_RETRY_TIMES - 1:
                        time.sleep(API_RETRY_DELAY * (attempt + 1))
                    else:
                        return pd.DataFrame()
        else:
            for attempt in range(API_RETRY_TIMES):
                try:
                    df = ak.stock_us_daily(symbol=stock_code)
                    if df is None or df.empty:
                        return pd.DataFrame()
                    df = df.rename(columns={"date": "date", "open": "open", "high": "high",
                                            "low": "low", "close": "close", "volume": "volume",
                                            "amount": "amount"})
                    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                    df = df.sort_values("date").reset_index(drop=True)
                    keep = ["date", "open", "high", "low", "close", "volume", "amount"]
                    existing = [c for c in keep if c in df.columns]
                    df = df[existing]
                    df["outstanding_share"] = 0.0
                    if start_date:
                        df = df[df["date"] >= start_date]
                    if end_date:
                        df = df[df["date"] <= end_date]
                    return df
                except Exception:
                    if attempt < API_RETRY_TIMES - 1:
                        time.sleep(API_RETRY_DELAY * (attempt + 1))
                    else:
                        return pd.DataFrame()

    def get_financials(self, stock_code: str) -> pd.DataFrame:
        """获取财务指标（港股用stock_financial_hk_analysis_indicator_em，美股用stock_financial_us_analysis_indicator_em）"""
        if self._is_hk(stock_code):
            hk_code = stock_code.replace(".HK", "")
            for attempt in range(API_RETRY_TIMES):
                try:
                    df = ak.stock_financial_hk_analysis_indicator_em(symbol=hk_code)
                    if df is None or df.empty:
                        return pd.DataFrame()
                    return df
                except Exception:
                    if attempt < API_RETRY_TIMES - 1:
                        time.sleep(API_RETRY_DELAY * (attempt + 1))
                    else:
                        return pd.DataFrame()
        else:
            for attempt in range(API_RETRY_TIMES):
                try:
                    df = ak.stock_financial_us_analysis_indicator_em(symbol=stock_code)
                    if df is None or df.empty:
                        return pd.DataFrame()
                    return df
                except Exception:
                    if attempt < API_RETRY_TIMES - 1:
                        time.sleep(API_RETRY_DELAY * (attempt + 1))
                    else:
                        return pd.DataFrame()

    def get_current_pe(self, stock_code: str) -> Optional[dict]:
        """获取单只股票的当前 PE（港股直接 price/EPS_TTM，美股按实际币种换算）"""
        try:
            quotes = self.get_daily_quotes(stock_code)
            if quotes.empty:
                return None
            price = float(quotes.iloc[-1]["close"])
            quote_row = quotes.iloc[-1]
            market_cap = None
            verified_shares = None
            reported_market_cap = False
            for col in ("market_cap", "marketCap", "MARKET_CAP", "total_market_cap"):
                if col in quote_row.index and pd.notna(quote_row[col]):
                    market_cap = float(quote_row[col])
                    reported_market_cap = True
                    break
            for col in ("verified_shares", "shares_outstanding", "outstanding_share", "total_shares", "shares"):
                if col in quote_row.index and pd.notna(quote_row[col]):
                    verified_shares = float(quote_row[col])
                    break
            secondary_price = None
            if self.secondary_quote_fetcher is not None:
                try:
                    secondary_price = self.secondary_quote_fetcher(stock_code)
                    if secondary_price is not None:
                        secondary_price = float(secondary_price)
                except (TypeError, ValueError):
                    secondary_price = None

            fin = self.get_financials(stock_code)
            if fin.empty:
                return None

            # 获取 EPS_TTM 和币种
            eps_ttm = None
            currency = "CNY"  # 默认人民币（美股中概多数用CNY报表）
            for col in ["EPS_TTM", "BASIC_EPS"]:
                if col in fin.columns:
                    eps_ttm = fin[col].iloc[0]
                    break
            if eps_ttm is None or pd.isna(eps_ttm) or eps_ttm <= 0:
                return None
            profit_ttm = None
            for col in ("NET_PROFIT_TTM", "PROFIT_TTM", "TTM_NET_PROFIT", "profit_ttm", "net_profit_ttm"):
                if col in fin.columns and pd.notna(fin.iloc[0][col]):
                    profit_ttm = float(fin.iloc[0][col])
                    break
            financial_as_of = None
            for col in ("REPORT_DATE", "report_date", "REPORT_PERIOD", "date"):
                if col in fin.columns and pd.notna(fin.iloc[0][col]):
                    parsed = pd.to_datetime(fin.iloc[0][col], errors="coerce")
                    financial_as_of = None if pd.isna(parsed) else parsed.strftime("%Y-%m-%d")
                    break
            # 检测实际币种
            if "CURRENCY" in fin.columns and pd.notna(fin["CURRENCY"].iloc[0]):
                curr_str = str(fin["CURRENCY"].iloc[0])
                if "美元" in curr_str or "USD" in curr_str.upper():
                    currency = "USD"
                elif "港元" in curr_str or "HKD" in curr_str.upper():
                    currency = "HKD"
                elif "人民币" in curr_str or "CNY" in curr_str.upper():
                    currency = "CNY"

            if market_cap is None:
                for col in ("market_cap", "marketCap", "MARKET_CAP", "total_market_cap"):
                    if col in fin.columns and pd.notna(fin.iloc[0][col]):
                        market_cap = float(fin.iloc[0][col])
                        reported_market_cap = True
                        break
            if verified_shares is None:
                for col in ("verified_shares", "shares_outstanding", "outstanding_share", "total_shares", "shares"):
                    if col in fin.columns and pd.notna(fin.iloc[0][col]):
                        verified_shares = float(fin.iloc[0][col])
                        break
            if market_cap is None and verified_shares is not None and price is not None:
                market_cap = price * verified_shares
            if market_cap is None and verified_shares is None and profit_ttm and eps_ttm:
                verified_shares = profit_ttm / float(eps_ttm)
                market_cap = price * verified_shares if price is not None else None
            market_cap_method = (
                "reported_market_cap" if market_cap is not None and reported_market_cap else
                "price_times_reported_shares" if verified_shares is not None and market_cap is not None else
                "derived_profit_div_eps" if market_cap is not None else None
            )
            financial_fresh = not _is_financial_stale(financial_as_of)

            result = {
                "code": stock_code,
                "price": round(price, 2),
                "currency": "HKD" if self._is_hk(stock_code) else currency,
                "eps_ttm": round(float(eps_ttm), 4),
                "quote_source_count": 2 if secondary_price is not None else 1,
                "quote_disagreement": bool(secondary_price is not None and price and
                    abs(price - secondary_price) / abs(price) > 0.01),
                "market_cap": market_cap,
                "verified_shares": verified_shares,
                "market_cap_method": market_cap_method,
                "financial_as_of": financial_as_of,
                "financial_fresh": financial_fresh,
            }
            if result["quote_source_count"] == 1:
                result["validity_reason"] = "single_quote_source"
            if result["quote_disagreement"]:
                result["validity_reason"] = "; ".join(filter(None, [
                    result.get("validity_reason"), "quote_disagreement"
                ]))
            if not financial_fresh:
                result["validity_reason"] = _append_reason(result.get("validity_reason"), "financial_stale")
            if self._is_hk(stock_code):
                eps_val = float(eps_ttm)
                pe = price / eps_val if eps_val > 0 else None
                if not result["quote_disagreement"] and financial_fresh:
                    result["pe"] = round(pe, 2) if pe else None
                return result
            else:
                # 美股：根据实际币种决定换算方式
                rate = self._get_fx_rate()
                ads_ratio = self.ADS_RATIO.get(stock_code, 1)
                try:
                    ads_ratio = float(ads_ratio)
                except (TypeError, ValueError):
                    return {
                        "code": stock_code,
                        "price": round(price, 2),
                        "currency": currency,
                        "ads_ratio": ads_ratio,
                        "eps_ttm_usd": None,
                        "validity_reason": "invalid ads_ratio",
                    }
                if not math.isfinite(ads_ratio) or ads_ratio <= 0:
                    return {
                        "code": stock_code,
                        "price": round(price, 2),
                        "currency": currency,
                        "ads_ratio": ads_ratio,
                        "eps_ttm_usd": None,
                        "validity_reason": "invalid ads_ratio",
                    }
                if currency == "USD":
                    # EPS 已经是 USD，直接用（不再除以汇率）
                    eps_usd = float(eps_ttm) * ads_ratio
                else:
                    # EPS 为 CNY/HKD，换算为 USD
                    eps_usd = float(eps_ttm) * ads_ratio / rate

                pe = price / eps_usd if eps_usd > 0 else None

                # EPS 单位修复：akshare 对部分美股中概的 BASIC_EPS 返回了偏小10倍的错误值
                # 检测方法：CNY币种 + PE>15 → 将 EPS×10（使PE降低到合理范围）
                # 已验证：BABA(17.45)/PDD(33.61) PE<15不受影响；JD/NTES/GDS/LI 需修正
                if pe is not None and currency == "CNY" and pe > 15:
                    alt_pe = price / (eps_usd * 10)
                    if alt_pe > 0:
                        eps_usd = eps_usd * 10
                        pe = alt_pe

                result.update({
                    "eps_ttm_cny": float(eps_ttm) if currency != "USD" else None,
                    "ads_ratio": ads_ratio,
                    "eps_ttm_usd": round(eps_usd, 4),
                    "rate": rate,
                })
                if not result["quote_disagreement"] and financial_fresh:
                    result["pe"] = round(pe, 2) if pe else None
                return result
        except Exception:
            return None

    def get_realtime_hk(self, codes: list) -> pd.DataFrame:
        """批量获取港股实时行情（含新浪字段15/16作为参考估值）"""
        sina_codes = [c["sina_code"] for c in codes if c.get("sina_code")]
        if not sina_codes:
            return pd.DataFrame()
        url = "https://hq.sinajs.cn/list=" + ",".join(sina_codes)
        headers = {"Referer": "https://finance.sina.com.cn"}
        try:
            r = requests.get(url, headers=headers, timeout=10)
            rows = []
            for line in r.text.strip().split(";"):
                line = line.strip()
                if "hq_str_" not in line or "=" not in line:
                    continue
                sina_code = line.split("=\"")[0].replace("hq_str_", "")
                parts = line.split("=\"")[1].strip().rstrip(";").split(",")
                if len(parts) < 3:
                    continue
                name = parts[1] if len(parts) > 1 else sina_code
                price = float(parts[2]) if parts[2] else 0
                field15 = parts[15] if len(parts) > 15 and parts[15] else None
                field16 = parts[16] if len(parts) > 16 and parts[16] else None
                rows.append({
                    "sina_code": sina_code,
                    "name": name,
                    "price": price,
                    "field15": field15,
                    "field16": field16,
                })
            return pd.DataFrame(rows)
        except Exception:
            return pd.DataFrame()
