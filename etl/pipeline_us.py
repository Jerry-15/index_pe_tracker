"""美股/中概股 PE 计算引擎（基于 EPS_TTM + ADS比例）"""
import time
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from data_sources.us import USDataSource
from cache.db import CacheDB
from config import API_BATCH_DELAY


class USETL:
    """中概互联网指数 PE 计算"""

    def __init__(self, cache: Optional[CacheDB] = None):
        self.cache = cache or CacheDB()
        self.source = USDataSource()
        self.index_code = "CHINA_INTL"
        self.index_name = "中概互联网"

    def run(self, progress_cb=None) -> pd.DataFrame:
        """运行当前快照更新"""
        if progress_cb:
            progress_cb("获取成分股列表...")

        constituents = self.source.get_constituents(self.index_code)
        stocks = constituents.to_dict("records")
        total = len(stocks)

        if progress_cb:
            progress_cb(f"开始更新 {self.index_name}：{total} 只股票")

        results = []
        for i, stock in enumerate(stocks):
            if progress_cb and i % 5 == 0:
                progress_cb(f"更新数据 [{i}/{total}]")
            data = self.source.get_current_pe(stock["code"])
            if data:
                data["name"] = stock["name"]
                results.append(data)
            time.sleep(API_BATCH_DELAY)

        if not results:
            return pd.DataFrame()

        df = pd.DataFrame(results)
        today = pd.Timestamp.today().strftime("%Y-%m-%d")

        # 计算指数 PE：市值加权 PE（与历史口径一致）
        valid = df[df["pe"].notna() & (df["pe"] > 0) & (df["pe"] < 200)]
        if len(valid) > 0:
            prices = valid["price"].values
            pes = valid["pe"].values
            mvs = (prices ** 2) / pes          # 市值代理（USD）
            index_pe = float((mvs * pes).sum() / mvs.sum())
            total_mv = float(mvs.sum())
            total_profit = float(total_mv / index_pe)
        else:
            total_mv = 0.0
            total_profit = 0.0
            index_pe = None

        row = {
            "trade_date": today,
            "total_market_cap": round(total_mv, 6),
            "total_profit_ttm": round(total_profit, 6),
            "pe_ratio": index_pe,
            "stock_count": len(valid),
        }
        pe_df = pd.DataFrame([row])

        self.cache.save_us_pe_snapshot(today, pe_df)
        self.cache.save_us_stock_pe(today, df)

        # 重新计算统计指标（加入最新快照后更新）
        self._recompute_us_stats()

        if progress_cb:
            status = f"✅ 完成：中概互联网 PE = {index_pe:.2f}x（{len(valid)}/{total} 只有效）" if index_pe else "⚠️ 无有效PE数据"
            progress_cb(status)

        return pe_df

    def load_result(self) -> Dict:
        """加载结果（含完整历史序列 + 最新快照）"""
        pe_df = self.cache.load_us_pe_daily()
        # 合并统计指标
        try:
            stats_df = self.cache.load_us_pe_stats()
            if not stats_df.empty:
                stats_cols = [c for c in stats_df.columns if c in ("trade_date", "pe_mean", "pe_std",
                                    "pe_high_line", "pe_low_line", "pe_percentile")]
                pe_df = pe_df.merge(stats_df[stats_cols], on="trade_date", how="left")
        except Exception:
            pass
        stock_pe = self.cache.load_us_stock_pe()
        constituents = self.source.get_constituents(self.index_code)
        return {
            "pe_data": pe_df,
            "stock_pe": stock_pe,
            "constituents": constituents,
            "last_updated": self.cache.get_us_last_updated(),
        }

    def _recompute_us_stats(self):
        """重新计算并保存 US PE 统计指标"""
        pe_df = self.cache.load_us_pe_daily()
        if pe_df.empty:
            return
        pe_df = pe_df.copy().sort_values("trade_date").reset_index(drop=True)
        window = min(500, len(pe_df))
        pe_df["pe_mean"] = pe_df["pe_ratio"].rolling(window=window, min_periods=max(60, window // 2)).mean()
        pe_df["pe_std"] = pe_df["pe_ratio"].rolling(window=window, min_periods=max(60, window // 2)).std()
        pe_df["pe_high_line"] = pe_df["pe_mean"] + pe_df["pe_std"]
        pe_df["pe_low_line"] = pe_df["pe_mean"] - pe_df["pe_std"]
        sorted_pe = pe_df["pe_ratio"].sort_values().values
        pe_df["pe_percentile"] = pe_df["pe_ratio"].apply(
            lambda x: np.searchsorted(sorted_pe, x, side="left") / len(sorted_pe) * 100
        )
        self.cache.save_us_pe_daily(pe_df)
        stats_cols = ["trade_date", "pe_mean", "pe_std", "pe_high_line", "pe_low_line", "pe_percentile"]
        self.cache.save_us_pe_stats(pe_df[stats_cols])

    def run_historical(self, progress_cb=None) -> pd.DataFrame:
        """构建历史 PE 序列（用近 250 日每日收盘价 + 最新 EPS_TTM）"""
        constituents = self.source.get_constituents(self.index_code)
        stocks = constituents.to_dict("records")

        if progress_cb:
            progress_cb(f"拉取行情数据（{len(stocks)} 只股票）...")

        all_quotes = {}
        for i, stock in enumerate(stocks):
            if progress_cb and i % 5 == 0:
                progress_cb(f"拉取行情 [{i}/{len(stocks)}]")
            quotes = self.source.get_daily_quotes(stock["code"])
            if not quotes.empty:
                all_quotes[stock["code"]] = quotes
            time.sleep(API_BATCH_DELAY)

        if not all_quotes:
            return pd.DataFrame()

        # 获取每只股票的最新 EPS_TTM
        eps_map = {}
        for code in all_quotes:
            fin = self.source.get_financials(code)
            if fin.empty:
                continue
            eps = None
            for col in ["BASIC_EPS", "EPS_TTM"]:
                if col in fin.columns:
                    eps = fin[col].iloc[0]
                    break
            if eps and not pd.isna(eps) and eps > 0:
                rate = self.source._get_fx_rate()
                ads_ratio = self.source.ADS_RATIO.get(code, 1)
                eps_usd = float(eps) * ads_ratio / rate
                eps_map[code] = eps_usd

        if progress_cb:
            progress_cb(f"共同交易日计算 PE...")

        # 找出共同日期
        all_dates = set()
        for q in all_quotes.values():
            all_dates.update(q["date"].unique().tolist())
        common_dates = sorted(all_dates)

        results = []
        for date in common_dates[-500:]:  # 最近500个交易日
            prices = {}
            for code, q in all_quotes.items():
                day_df = q[q["date"] == date]
                if not day_df.empty:
                    prices[code] = float(day_df.iloc[-1]["close"])

            if not prices or len(prices) < 3:
                continue

            pe_sum = 0.0
            mv_sum = 0.0
            count = 0
            for code, price in prices.items():
                eps_usd = eps_map.get(code)
                if eps_usd is None or eps_usd <= 0:
                    continue
                pe = price / eps_usd
                if pe > 0 and pe < 200:
                    mv_proxy = (price ** 2) / pe  # 市值代理
                    pe_sum += mv_proxy * pe
                    mv_sum += mv_proxy
                    count += 1

            if count > 0 and mv_sum > 0:
                # 指数 PE = 总市值 / 总利润（各股市值代理之和 / 各股利润代理之和）
                # MV_proxy = price²/pe  (USD)，Profit_proxy = MV/pe = price×(price/pe)/pe
                # 但 price/pe = EPS_USD，所以 Profit_proxy = price × EPS_USD / pe = EPS_USD²
                # 为得到一致口径，用各股 MV_proxy 求和作为总市值代理，利润代理 = MV_proxy / pe_i 求和
                index_pe = pe_sum / mv_sum
                total_profit = mv_sum / index_pe  # 反推总利润（USD）
                results.append({
                    "trade_date": date,
                    "total_market_cap": round(mv_sum, 6),   # USD 市值代理
                    "total_profit_ttm": round(total_profit, 6),  # USD 利润代理
                    "pe_ratio": index_pe,
                    "stock_count": count,
                })

        if progress_cb:
            progress_cb(f"✅ 完成历史序列：{len(results)} 个交易日")

        if results:
            pe_df = pd.DataFrame(results)
            pe_df = pe_df.sort_values("trade_date").reset_index(drop=True)
            window = min(500, len(pe_df))
            pe_df["pe_mean"] = pe_df["pe_ratio"].rolling(window=window, min_periods=max(60, window // 2)).mean()
            pe_df["pe_std"] = pe_df["pe_ratio"].rolling(window=window, min_periods=max(60, window // 2)).std()
            pe_df["pe_high_line"] = pe_df["pe_mean"] + pe_df["pe_std"]
            pe_df["pe_low_line"] = pe_df["pe_mean"] - pe_df["pe_std"]
            sorted_pe = pe_df["pe_ratio"].sort_values().values
            pe_df["pe_percentile"] = pe_df["pe_ratio"].apply(
                lambda x: np.searchsorted(sorted_pe, x, side="left") / len(sorted_pe) * 100
            )
            self.cache.save_us_pe_daily(pe_df)
            # 保存统计指标到独立表
            stats_cols = ["trade_date", "pe_mean", "pe_std", "pe_high_line", "pe_low_line", "pe_percentile"]
            self.cache.save_us_pe_stats(pe_df[stats_cols])
            return pe_df
        return pd.DataFrame()
