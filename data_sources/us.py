"""美股/中概股数据源（基于 akshare Sina 接口）"""
import time
import akshare as ak
import pandas as pd
from typing import Optional
from config import API_RETRY_TIMES, API_RETRY_DELAY


class USDataSource:
    """美股数据获取：行情 + 财务指标（EPS_TTM / PB 等）"""

    # ADS比例：1 ADS = N 股普通股（BABA 为 8:1，其余多数为 1:1）
    ADS_RATIO = {
        "BABA": 8, "PDD": 1, "JD": 1, "BIDU": 1, "NTES": 1, "MOMO": 1,
        "TCEHY": 1, "NCTY": 1, "VIPS": 1, "WB": 1, "IQ": 1, "FENG": 1,
        "TCOM": 1, "LI": 1, "NIO": 1, "XPEV": 1, "TIGR": 1, "FUTU": 1,
        "SINA": 1, "SOHU": 1, "YUMC": 1, "GDS": 1, "VNET": 1,
    }

    def __init__(self):
        self._fx_rate: Optional[float] = None
        self._fx_cached_at: float = 0

    def _get_fx_rate(self) -> float:
        """获取最新 USD/CNY 中间价（四舍五入到小数点后4位）"""
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
        return 7.1771  # 备用默认值

    def get_constituents(self, index_code: str) -> pd.DataFrame:
        """返回预定义的中概互联网成分股"""
        stocks = {
            "BABA":   {"name": "阿里巴巴",  "exchange": "NASDAQ"},
            "PDD":    {"name": "拼多多",    "exchange": "NASDAQ"},
            "JD":     {"name": "京东",      "exchange": "NASDAQ"},
            "BIDU":   {"name": "百度",      "exchange": "NASDAQ"},
            "NTES":   {"name": "网易",      "exchange": "NASDAQ"},
            "MOMO":   {"name": "陌陌",      "exchange": "NASDAQ"},
            "TCEHY":  {"name": "腾讯",      "exchange": "OTC"},
            "NCTY":   {"name": "世纪华通",  "exchange": "OTC"},
            "VIPS":   {"name": "唯品会",    "exchange": "NYSE"},
            "WB":     {"name": "微博",      "exchange": "NASDAQ"},
            "IQ":     {"name": "爱奇艺",    "exchange": "NASDAQ"},
            "FENG":   {"name": "凤凰卫视",  "exchange": "NASDAQ"},
            "TCOM":   {"name": "携程",      "exchange": "NASDAQ"},
            "LI":     {"name": "理想汽车",  "exchange": "NASDAQ"},
            "NIO":    {"name": "蔚来",      "exchange": "NYSE"},
            "XPEV":   {"name": "小鹏汽车",  "exchange": "NYSE"},
            "TIGR":   {"name": "老虎证券",  "exchange": "NASDAQ"},
            "FUTU":   {"name": "富途控股",  "exchange": "NASDAQ"},
            "SINA":   {"name": "新浪",      "exchange": "NASDAQ"},
            "SOHU":   {"name": "搜狐",      "exchange": "NASDAQ"},
            "YUMC":   {"name": "百胜中国",  "exchange": "NYSE"},
            "GDS":    {"name": "世纪互联",  "exchange": "NASDAQ"},
            "VNET":   {"name": "网宿科技",  "exchange": "NASDAQ"},
        }
        records = []
        for code, info in stocks.items():
            records.append({
                "code": code,
                "name": info["name"],
                "exchange": info["exchange"],
            })
        return pd.DataFrame(records)

    def get_daily_quotes(self, stock_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        """获取美股历史日K线（akshare stock_us_daily）"""
        for attempt in range(API_RETRY_TIMES):
            try:
                df = ak.stock_us_daily(symbol=stock_code)
                if df is None or df.empty:
                    return pd.DataFrame()
                df = df.rename(columns={
                    "date": "date",
                    "open": "open",
                    "high": "high",
                    "low": "low",
                    "close": "close",
                    "volume": "volume",
                    "amount": "amount",
                })
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                df = df.sort_values("date").reset_index(drop=True)
                keep_cols = ["date", "open", "high", "low", "close", "volume", "amount"]
                existing = [c for c in keep_cols if c in df.columns]
                df = df[existing]
                if "total_share" in df.columns:
                    df["outstanding_share"] = df["total_share"]
                else:
                    df["outstanding_share"] = 0.0
                return df
            except Exception as e:
                if attempt < API_RETRY_TIMES - 1:
                    time.sleep(API_RETRY_DELAY * (attempt + 1))
                else:
                    return pd.DataFrame()

    def get_financials(self, stock_code: str) -> pd.DataFrame:
        """获取美股财务指标（含 EPS_TTM、PB 等）"""
        for attempt in range(API_RETRY_TIMES):
            try:
                df = ak.stock_financial_us_analysis_indicator_em(symbol=stock_code)
                if df is None or df.empty:
                    return pd.DataFrame()
                return df
            except Exception as e:
                if attempt < API_RETRY_TIMES - 1:
                    time.sleep(API_RETRY_DELAY * (attempt + 1))
                else:
                    return pd.DataFrame()

    def get_current_pe(self, stock_code: str) -> Optional[dict]:
        """获取单只股票的当前 PE（价格/EPS_TTM，已按汇率+ADS比例换算）"""
        try:
            quotes = self.get_daily_quotes(stock_code)
            if quotes.empty:
                return None
            price = float(quotes.iloc[-1]["close"])

            fin = self.get_financials(stock_code)
            if fin.empty:
                return None

            eps_ttm = None
            for col in ["BASIC_EPS", "EPS_TTM", "基本每股收益(TTM)"]:
                if col in fin.columns:
                    eps_ttm = fin[col].iloc[0]
                    break
            if eps_ttm is None or pd.isna(eps_ttm) or eps_ttm <= 0:
                return None

            rate = self._get_fx_rate()
            # EPS 单位为 CNY/股，需按 ADS 比例折算为每 ADS 的 EPS
            ads_ratio = self.ADS_RATIO.get(stock_code, 1)
            eps_usd = float(eps_ttm) * ads_ratio / rate
            pe = price / eps_usd if eps_usd > 0 else None

            return {
                "code": stock_code,
                "price": round(price, 2),
                "eps_ttm_cny": float(eps_ttm),
                "ads_ratio": ads_ratio,
                "eps_ttm_usd": round(eps_usd, 4),
                "pe": round(pe, 2) if pe else None,
                "rate": rate,
            }
        except Exception:
            return None
