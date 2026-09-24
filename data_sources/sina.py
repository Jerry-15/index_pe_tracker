"""Sina 数据源实现"""
import time
import akshare as ak
import pandas as pd
from typing import Optional
from data_sources.base import DataSource
from config import API_RETRY_TIMES, API_RETRY_DELAY, API_BATCH_DELAY


class SinaDataSource(DataSource):

    def get_trading_dates(
        self, start_date: str = "", end_date: str = "", allow_weekday_fallback: bool = False
    ) -> pd.DataFrame:
        """Return the real A-share trading calendar.

        Weekday generation is deliberately opt-in for offline tests only.
        """
        try:
            df = ak.tool_trade_date_hist_sina()
            if df is None or df.empty:
                raise RuntimeError("AkShare trading calendar is empty")
            date_col = "trade_date" if "trade_date" in df.columns else df.columns[0]
            result = pd.DataFrame({"date": pd.to_datetime(df[date_col], errors="coerce")})
            result = result.dropna().drop_duplicates().sort_values("date")
            if start_date:
                result = result[result["date"] >= pd.Timestamp(start_date)]
            if end_date:
                result = result[result["date"] <= pd.Timestamp(end_date)]
            return result.reset_index(drop=True)
        except Exception as exc:
            if not allow_weekday_fallback:
                raise RuntimeError(f"Failed to get trading calendar: {exc}") from exc
            start = pd.Timestamp(start_date or pd.Timestamp.today() - pd.Timedelta(days=3650))
            end = pd.Timestamp(end_date or pd.Timestamp.today())
            return pd.DataFrame({"date": pd.bdate_range(start, end)})

    def _make_stock_symbol(self, code: str) -> str:
        """将股票代码转换为 Sina 格式 (sh600519 / sz000001)"""
        if code.startswith(("6", "5")):
            return f"sh{code}"
        return f"sz{code}"

    def get_constituents(self, index_code: str) -> pd.DataFrame:
        """获取指数成分股（新浪指数 / 申万行业）"""
        # 申万行业代码以 801 开头
        if str(index_code).startswith("801"):
            return self._get_sw_constituents(index_code)
        # 先尝试 sina 接口
        try:
            df = ak.index_stock_cons_sina(symbol=index_code)
            if not df.empty and "code" in df.columns:
                result = pd.DataFrame()
                result["code"] = df["code"]
                result["name"] = df.get("name", "")
                result["exchange"] = result["code"].apply(
                    lambda c: "SH" if str(c).startswith(("6", "5")) else "SZ"
                )
                return result
        except Exception:
            pass
        # 备选：中证指数公司接口
        try:
            df = ak.index_stock_cons_csindex(symbol=index_code)
            if not df.empty:
                result = pd.DataFrame()
                if "成分券代码" in df.columns:
                    result["code"] = df["成分券代码"]
                elif "品种代码" in df.columns:
                    result["code"] = df["品种代码"]
                elif len(df.columns) > 0:
                    result["code"] = df.iloc[:, 0]
                if "成分券名称" in df.columns:
                    result["name"] = df["成分券名称"]
                elif "品种名称" in df.columns:
                    result["name"] = df["品种名称"]
                elif len(df.columns) > 1:
                    result["name"] = df.iloc[:, 1]
                result["exchange"] = result["code"].apply(
                    lambda c: "SH" if str(c).startswith(("6", "5")) else "SZ"
                )
                return result
        except Exception:
            pass
        return pd.DataFrame(columns=["code", "name", "exchange"])

    def _get_sw_constituents(self, index_code: str) -> pd.DataFrame:
        """获取申万行业成分股"""
        code = str(index_code).replace(".SI", "")
        for attempt in range(API_RETRY_TIMES):
            try:
                df = ak.index_component_sw(symbol=code)
                if df is None or df.empty or "证券代码" not in df.columns:
                    return pd.DataFrame(columns=["code", "name", "exchange"])
                result = pd.DataFrame()
                result["code"] = df["证券代码"].astype(str).str.zfill(6)
                result["name"] = df.get("证券名称", "")
                result["exchange"] = result["code"].apply(
                    lambda c: "SH" if c.startswith(("6", "5")) else "SZ"
                )
                return result
            except Exception as e:
                if attempt < API_RETRY_TIMES - 1:
                    time.sleep(API_RETRY_DELAY * (attempt + 1))
                else:
                    return pd.DataFrame(columns=["code", "name", "exchange"])

    def get_industry_reference(self) -> pd.DataFrame:
        """获取申万一级行业列表及官方TTM PE（用于参考对照）"""
        for attempt in range(API_RETRY_TIMES):
            try:
                df = ak.sw_index_first_info()
                if df is None or df.empty:
                    return pd.DataFrame(columns=["code", "name", "pe_ttm", "stock_count"])
                result = pd.DataFrame()
                result["code"] = df["行业代码"].astype(str).str.replace(".SI", "", regex=False)
                result["name"] = df["行业名称"]
                result["pe_ttm"] = pd.to_numeric(df["TTM(滚动)市盈率"], errors="coerce")
                result["stock_count"] = pd.to_numeric(df["成份个数"], errors="coerce")
                return result
            except Exception as e:
                if attempt < API_RETRY_TIMES - 1:
                    time.sleep(API_RETRY_DELAY * (attempt + 1))
                else:
                    return pd.DataFrame(columns=["code", "name", "pe_ttm", "stock_count"])

    def get_daily_quotes(self, stock_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        """获取历史日K线 + 流通股数"""
        symbol = self._make_stock_symbol(stock_code)
        for attempt in range(API_RETRY_TIMES):
            try:
                df = ak.stock_zh_a_daily(symbol=symbol)
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                if start_date:
                    df = df[df["date"] >= start_date]
                if end_date:
                    df = df[df["date"] <= end_date]
                df = df.sort_values("date").reset_index(drop=True)
                return df[["date", "open", "high", "low", "close", "volume",
                           "amount", "outstanding_share", "turnover"]]
            except Exception as e:
                if attempt < API_RETRY_TIMES - 1:
                    time.sleep(API_RETRY_DELAY * (attempt + 1))
                else:
                    raise RuntimeError(f"Failed to get daily quotes for {stock_code}: {e}")

    def get_financial_reports(self, stock_code: str) -> pd.DataFrame:
        """获取利润表（季报/年报）"""
        symbol = self._make_stock_symbol(stock_code)
        for attempt in range(API_RETRY_TIMES):
            try:
                df = ak.stock_financial_report_sina(stock=symbol, symbol="利润表")
                if df is None or df.empty:
                    return pd.DataFrame(columns=["report_date", "net_profit_attr", "publish_date"])
                # 兼容不同列名
                profit_col = None
                for col in ["归属于母公司所有者的净利润", "归属于母公司的净利润", "净利润"]:
                    if col in df.columns:
                        profit_col = col
                        break
                if profit_col is None:
                    return pd.DataFrame(columns=["report_date", "net_profit_attr", "publish_date"])
                # 兼容报告日列名
                date_col = "报告日" if "报告日" in df.columns else df.columns[0]
                pub_col = "公告日期" if "公告日期" in df.columns else (
                    "发布日期" if "发布日期" in df.columns else df.columns[-1]
                )
                result = df[[date_col, profit_col, pub_col]].copy()
                result.columns = ["report_date_raw", "net_profit_attr", "publish_date_raw"]
                result["report_date"] = pd.to_datetime(result["report_date_raw"], format="%Y%m%d", errors="coerce")
                result["publish_date"] = pd.to_datetime(result["publish_date_raw"], errors="coerce")
                result = result.dropna(subset=["net_profit_attr", "report_date", "publish_date"])
                result = result.sort_values("report_date").reset_index(drop=True)
                return result[["report_date", "net_profit_attr", "publish_date"]]
            except Exception as e:
                if attempt < API_RETRY_TIMES - 1:
                    time.sleep(API_RETRY_DELAY * (attempt + 1))
                else:
                    return pd.DataFrame(columns=["report_date", "net_profit_attr", "publish_date"])

    def get_index_pe_history(self, index_name: str) -> pd.DataFrame:
        """获取指数月度PE历史（乐咕乐股，用于参考对照）"""
        for attempt in range(API_RETRY_TIMES):
            try:
                df = ak.stock_index_pe_lg(symbol=index_name)
                if df is None or df.empty or "日期" not in df.columns:
                    return pd.DataFrame(columns=["date", "pe_ttm"])
                result = df[["日期", "滚动市盈率"]].copy()
                result.columns = ["date", "pe_ttm"]
                result["date"] = pd.to_datetime(result["date"])
                result["pe_ttm"] = pd.to_numeric(result["pe_ttm"], errors="coerce")
                return result.dropna(subset=["pe_ttm"])
            except Exception as e:
                if attempt < API_RETRY_TIMES - 1:
                    time.sleep(API_RETRY_DELAY * (attempt + 1))
                else:
                    return pd.DataFrame(columns=["date", "pe_ttm"])
