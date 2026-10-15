"""美股/港股/中概股数据源（基于 akshare + 新浪实时行情）"""
import time
import math
import requests
import akshare as ak
import pandas as pd
from typing import Any, Optional
from config import API_RETRY_TIMES, API_RETRY_DELAY


class USDataSource:
    """美股数据获取：行情 + 财务指标（EPS_TTM / ADS校正）"""

    # ADS比例：1 ADS = N 股普通股（BABA 为 8:1）
    ADS_RATIO = {
        "BABA": 8, "PDD": 1, "JD": 1, "BIDU": 1, "NTES": 1, "MOMO": 1,
        "TCEHY": 1, "NCTY": 1, "VIPS": 1, "WB": 1, "IQ": 1, "FENG": 1,
        "TCOM": 1, "LI": 1, "NIO": 1, "XPEV": 1, "TIGR": 1, "FUTU": 1,
        "SINA": 1, "SOHU": 1, "YUMC": 1, "GDS": 1, "VNET": 1,
    }

    def __init__(self):
        self._fx_rate: Optional[float] = None
        self._fx_cached_at: float = 0

    @staticmethod
    def build_input_record(
        code: str,
        price: Optional[float],
        currency: Optional[str],
        shares_or_market_cap: Optional[float],
        profit_ttm: Optional[float],
        financial_as_of: Optional[str],
        source: str,
        ads_ratio: Optional[float] = None,
    ) -> dict[str, Any]:
        """Build a common quote/financial record without sentinel zeros."""
        missing = []
        invalid = []
        for field, value, positive in (
            ("price", price, True),
            ("shares_or_market_cap", shares_or_market_cap, True),
            ("profit_ttm", profit_ttm, True),
        ):
            if value is None:
                missing.append(field)
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                invalid.append(field)
                continue
            if not math.isfinite(numeric_value) or (positive and numeric_value <= 0):
                invalid.append(field)
        if currency is None:
            missing.append("currency")
        if financial_as_of is None:
            missing.append("financial_as_of")
        ads_ratio_invalid = False
        if ads_ratio is None:
            missing.append("ads_ratio")
        else:
            try:
                ads_ratio = float(ads_ratio)
                ads_ratio_invalid = not math.isfinite(ads_ratio) or ads_ratio <= 0
            except (TypeError, ValueError):
                ads_ratio_invalid = True
            if ads_ratio_invalid:
                invalid.append("ads_ratio")
        return {
            "code": code,
            "price": price,
            "currency": currency,
            "shares_or_market_cap": shares_or_market_cap,
            "profit_ttm": profit_ttm,
            "financial_as_of": financial_as_of,
            "source": source,
            "ads_ratio": ads_ratio,
            "eps_ttm": None,
            "validity_reason": "; ".join(
                [*(f"missing {field}" for field in missing),
                 *(f"invalid {field}" for field in invalid)]
            ) or None,
        }

    def _get_fx_rate(self) -> Optional[float]:
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
        return None

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
                    keep = ["date", "open", "high", "low", "close", "volume", "amount",
                            "market_cap", "marketCap", "MARKET_CAP", "total_market_cap",
                            "shares_outstanding", "outstanding_share", "total_shares", "shares"]
                    existing = [c for c in keep if c in df.columns]
                    df = df[existing]
                    if "outstanding_share" not in df.columns:
                        df["outstanding_share"] = float("nan")
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
                    keep = ["date", "open", "high", "low", "close", "volume", "amount",
                            "market_cap", "marketCap", "MARKET_CAP", "total_market_cap",
                            "shares_outstanding", "outstanding_share", "total_shares", "shares"]
                    existing = [c for c in keep if c in df.columns]
                    df = df[existing]
                    if "outstanding_share" not in df.columns:
                        df["outstanding_share"] = float("nan")
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

    def get_current_pe(self, stock_code: str) -> dict:
        """获取当前 PE，并始终返回统一的行情/财务输入记录。"""
        symbol = stock_code.replace(".HK", "")
        ads_ratio = 1.0 if self._is_hk(stock_code) else (
            float(self.ADS_RATIO[symbol]) if symbol in self.ADS_RATIO else None
        )
        price = None
        currency = "HKD" if self._is_hk(stock_code) else None
        eps_ttm = None
        profit_ttm = None
        financial_as_of = None
        market_cap = None
        verified_shares = None
        unsupported_currency = None
        try:
            quotes = self.get_daily_quotes(stock_code)
            if not quotes.empty and "close" in quotes.columns:
                value = quotes.iloc[-1]["close"]
                if pd.notna(value):
                    price = float(value)
                quote = quotes.iloc[-1]
                for col in ("market_cap", "marketCap", "MARKET_CAP", "total_market_cap"):
                    if col in quote.index and pd.notna(quote[col]):
                        market_cap = float(quote[col])
                        break
                for col in ("verified_shares", "shares_outstanding", "outstanding_share",
                            "total_shares", "shares"):
                    if col in quote.index and pd.notna(quote[col]):
                        verified_shares = float(quote[col])
                        break
        except Exception:
            pass

        try:
            fin = self.get_financials(stock_code)
            if not fin.empty:
                for col in ("EPS_TTM", "BASIC_EPS"):
                    if col in fin.columns and pd.notna(fin.iloc[0][col]):
                        eps_ttm = float(fin.iloc[0][col])
                        break
                for col in (
                    "PROFIT_TTM", "NET_PROFIT_TTM", "TTM_NET_PROFIT",
                    "profit_ttm", "net_profit_ttm", "ttm_net_profit",
                ):
                    if col in fin.columns and pd.notna(fin.iloc[0][col]):
                        profit_ttm = float(fin.iloc[0][col])
                        break
                for col in ("market_cap", "marketCap", "MARKET_CAP", "total_market_cap", "总市值"):
                    if market_cap is None and col in fin.columns and pd.notna(fin.iloc[0][col]):
                        market_cap = float(fin.iloc[0][col])
                        break
                for col in ("verified_shares", "shares_outstanding", "outstanding_share",
                            "total_shares", "shares", "TOTAL_SHARES", "总股本"):
                    if verified_shares is None and col in fin.columns and pd.notna(fin.iloc[0][col]):
                        verified_shares = float(fin.iloc[0][col])
                        break
                for col in ("REPORT_DATE", "report_date", "REPORT_PERIOD", "date", "日期"):
                    if col in fin.columns and pd.notna(fin.iloc[0][col]):
                        financial_as_of = str(fin.iloc[0][col])
                        break
                if "CURRENCY" in fin.columns and pd.notna(fin.iloc[0]["CURRENCY"]):
                    curr_str = str(fin.iloc[0]["CURRENCY"]).strip()
                    currency = None
                    if "美元" in curr_str or "USD" in curr_str.upper():
                        currency = "USD"
                    elif "港元" in curr_str or "HKD" in curr_str.upper():
                        currency = "HKD"
                    elif "人民币" in curr_str or "CNY" in curr_str.upper():
                        currency = "CNY"
                    else:
                        unsupported_currency = curr_str
        except Exception:
            pass

        record = self.build_input_record(
            stock_code, round(price, 2) if price is not None else None, currency,
            market_cap if market_cap is not None else verified_shares, profit_ttm, financial_as_of,
            "akshare", ads_ratio,
        )
        if unsupported_currency:
            record["validity_reason"] = "; ".join(filter(None, [
                record["validity_reason"],
                f"unsupported currency: {unsupported_currency}",
            ]))
        record["eps_ttm"] = round(eps_ttm, 4) if eps_ttm is not None else None
        record["eps"] = record["eps_ttm"]
        record["market_cap"] = market_cap
        record["verified_shares"] = verified_shares
        record["market_cap_currency"] = "HKD" if self._is_hk(stock_code) else "USD"
        record["profit_currency"] = currency
        pe_inputs_missing = any(
            record[field] is None
            for field in ("price", "currency", "shares_or_market_cap", "ads_ratio")
        )
        if profit_ttm is not None and profit_ttm <= 0:
            record["validity_reason"] = "; ".join(filter(None, [
                record["validity_reason"], "non_positive profit_ttm"
            ]))
        if currency == "HKD" and not self._is_hk(stock_code):
            record["validity_reason"] = "; ".join(filter(None, [
                record["validity_reason"], "HKD/USD conversion unavailable"
            ]))
        if (pe_inputs_missing or profit_ttm is None or profit_ttm <= 0 or
                eps_ttm is None or eps_ttm <= 0 or currency == "HKD"):
            if eps_ttm is not None and eps_ttm <= 0:
                record["validity_reason"] = "; ".join(
                    filter(None, [record["validity_reason"], "non_positive eps_ttm"])
                )
            return record

        if self._is_hk(stock_code):
            pe = price / eps_ttm if price is not None else None
            record.update({"eps_ttm": round(eps_ttm, 4), "pe": round(pe, 2) if pe else None})
            return record

        rate = self._get_fx_rate()
        if currency != "USD" and rate is None:
            record["validity_reason"] = "; ".join(filter(None, [
                record["validity_reason"], "CNY/USD conversion unavailable"
            ]))
            return record
        eps_usd = eps_ttm * ads_ratio if currency == "USD" else eps_ttm * ads_ratio / rate
        pe = price / eps_usd if price is not None and eps_usd > 0 else None
        record.update({
            "eps_ttm_cny": eps_ttm if currency != "USD" else None,
            "eps_ttm_usd": round(eps_usd, 4), "pe": round(pe, 2) if pe else None,
            "rate": rate,
        })
        return record

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
