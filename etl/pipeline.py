"""ETL 管道：从原始数据计算指数 PE"""
import time
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from data_sources import get_source
from cache.db import CacheDB
from config import DEFAULT_LOOKBACK_DAYS, API_BATCH_DELAY
from etl.quality import quality_level


class PEETL:
    """指数 PE 计算引擎 - 向量化版本"""

    def __init__(self, cache: CacheDB):
        self.cache = cache
        self.source = get_source()

    def _get_trading_dates(self, days: int = DEFAULT_LOOKBACK_DAYS) -> List[str]:
        today = pd.Timestamp.today()
        dates = []
        current = today
        while len(dates) < days:
            if current.weekday() < 5:
                dates.append(current.strftime("%Y-%m-%d"))
            current -= pd.Timedelta(days=1)
        return list(reversed(dates))

    def _parse_report_period(self, report_date: pd.Timestamp) -> str:
        month, day = report_date.month, report_date.day
        if month == 3 and day == 31: return "Q1"
        elif month == 6 and day == 30: return "H1"
        elif month == 9 and day == 30: return "Q3"
        elif month == 12 and day == 31: return "Annual"
        return "Unknown"

    def _decompose_quarterly(self, financial_df: pd.DataFrame) -> pd.DataFrame:
        """将累计财报拆分为独立季度利润"""
        if financial_df.empty:
            return pd.DataFrame()

        df = financial_df.copy()
        df["period"] = df["report_date"].apply(self._parse_report_period)
        df = df.sort_values("report_date")

        quarters = []
        for i, row in df.iterrows():
            rd = row["report_date"]
            profit = row["net_profit_attr"]
            pub = row["publish_date"]
            period = row["period"]

            if period == "Q1":
                quarters.append({
                    "report_date": rd,
                    "quarter_profit": profit,
                    "publish_date": pub,
                })
            elif period == "H1":
                # H1 cumulative, find previous Q1 to derive Q2
                q1_rows = df[(df["period"] == "Q1") & (df["report_date"] < rd)]
                q1_profit = q1_rows.iloc[-1]["net_profit_attr"] if not q1_rows.empty else 0
                quarters.append({"report_date": rd, "quarter_profit": profit - q1_profit, "publish_date": pub})
            elif period == "Q3":
                # Q3 cumulative (9 months), find previous H1 to derive Q3
                h1_rows = df[(df["period"] == "H1") & (df["report_date"] < rd)]
                h1_profit = h1_rows.iloc[-1]["net_profit_attr"] if not h1_rows.empty else 0
                quarters.append({"report_date": rd, "quarter_profit": profit - h1_profit, "publish_date": pub})
            elif period == "Annual":
                # Annual cumulative (12 months), find previous Q3 to derive Q4
                q3_rows = df[(df["period"] == "Q3") & (df["report_date"] < rd)]
                q3_profit = q3_rows.iloc[-1]["net_profit_attr"] if not q3_rows.empty else 0
                quarters.append({"report_date": rd, "quarter_profit": profit - q3_profit, "publish_date": pub})

        return pd.DataFrame(quarters)

    def _compute_ttm_from_quarters(self, quarterly_df: pd.DataFrame, target_date: pd.Timestamp) -> Optional[float]:
        """从已拆分的季度利润计算 TTM"""
        if quarterly_df.empty:
            return None

        # 找到在 target_date 之前已发布的最近4个季度
        available = quarterly_df[
            (quarterly_df["publish_date"] <= target_date)
            & ((target_date - quarterly_df["publish_date"]).dt.days <= 400)
        ].sort_values("report_date", ascending=False)

        if len(available) < 2:
            return None

        # 取最近4个季度
        recent = available.head(4)
        ttm = recent["quarter_profit"].sum()

        return ttm if ttm > 0 else None

    def _precompute_quarterly_profits(self, stocks: List[Dict], progress_cb=None):
        """预处理所有股票的季度利润分解（只执行一次）"""
        processed = 0
        for stock in stocks:
            code = stock["code"]
            # 检查是否已有季度利润缓存
            cached = self.cache.load_quarterly_profits(code)
            if not cached.empty:
                processed += 1
                continue

            fin_df = self.cache.load_financials(code)
            if fin_df.empty:
                continue

            quarterly = self._decompose_quarterly(fin_df)
            if not quarterly.empty:
                self.cache.save_quarterly_profits(code, quarterly)
            processed += 1

            if progress_cb and processed % 50 == 0:
                progress_cb(f"拆分财报 [{processed}/{len(stocks)}]")

    def _fetch_stock_data(self, stock_code: str, quotes_cache, financials_cache, progress_cb=None):
        if stock_code not in quotes_cache:
            cached = self.cache.load_quotes(stock_code)
            if not cached.empty:
                quotes_cache[stock_code] = cached
            else:
                try:
                    df = self.source.get_daily_quotes(stock_code)
                    quotes_cache[stock_code] = df
                    self.cache.save_quotes(stock_code, df)
                except Exception as e:
                    if progress_cb:
                        progress_cb(f"  ⚠ {stock_code} 行情失败")

        if stock_code not in financials_cache:
            cached = self.cache.load_financials(stock_code)
            if not cached.empty:
                financials_cache[stock_code] = cached
            else:
                try:
                    df = self.source.get_financial_reports(stock_code)
                    if not df.empty:
                        df["period_type"] = df["report_date"].apply(self._parse_report_period)
                    financials_cache[stock_code] = df
                    self.cache.save_financials(stock_code, df)
                except Exception as e:
                    if progress_cb:
                        progress_cb(f"  ⚠ {stock_code} 财务失败")

    def run_index(self, index_code: str, index_name: str, progress_cb=None) -> pd.DataFrame:
        """运行完整 ETL"""

        # 1. 获取成分股
        cached = self.cache.load_constituents(index_code)
        if cached.empty:
            if progress_cb: progress_cb(f"获取 {index_name} 成分股...")
            df = self.source.get_constituents(index_code)
            self.cache.save_constituents(index_code, df)
        else:
            df = cached

        stocks = df[["code", "name"]].to_dict("records")
        total = len(stocks)
        if progress_cb:
            progress_cb(f"开始处理 {index_name}：{total} 只成分股")

        # 2. 批量下载原始数据
        quotes_cache, financials_cache = {}, {}
        for i, stock in enumerate(stocks):
            if progress_cb and i % 30 == 0:
                progress_cb(f"下载数据 [{i}/{total}]")
            self._fetch_stock_data(stock["code"], quotes_cache, financials_cache, progress_cb)
            time.sleep(API_BATCH_DELAY)

        if progress_cb:
            progress_cb(f"数据下载完成，拆分财报...")

        # 3. 预处理季度利润
        self._precompute_quarterly_profits(stocks, progress_cb=progress_cb)

        if progress_cb:
            progress_cb(f"计算 PE 历史...")

        # 4. 向量化 PE 计算（带日期维度 TTM）
        pe_df = self._vectorized_compute_pe(stocks, quotes_cache, progress_cb)

        if pe_df.empty:
            return pe_df

        generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        source_type = "sw_official" if str(index_code).startswith("801") else "sina"
        pe_df["quality_level"] = pe_df["stock_count"].apply(
            lambda count: quality_level(
                source_type=source_type,
                weight_coverage=count / total,
                financial_stale_count=0,
                derived_market_cap_count=0,
                quote_disagreement_count=0,
            )
        )
        pe_df["data_source"] = source_type
        pe_df["generated_at"] = generated_at
        pe_df["coverage_ratio"] = pe_df["stock_count"] / total
        pe_df["financial_stale_count"] = 0
        pe_df["validity_reason"] = pe_df["coverage_ratio"].apply(
            lambda coverage: None if coverage >= 0.95 else "partial coverage"
        )

        self.cache.save_pe_daily(index_code, pe_df)
        self._compute_statistics(index_code, pe_df)

        if progress_cb:
            progress_cb(f"✅ 完成：{len(pe_df)} 个交易日")

        return pe_df

    def _vectorized_compute_pe(
        self,
        stocks: List[Dict],
        quotes_cache: Dict[str, pd.DataFrame],
        progress_cb=None,
    ) -> pd.DataFrame:
        """向量化 PE 计算，按日期动态计算 TTM（bisect 快速查找）"""
        from bisect import bisect_right

        # 合并行情数据
        all_quotes = []
        for code, df in quotes_cache.items():
            if not df.empty:
                df = df.copy()
                df["stock_code"] = code
                all_quotes.append(df)

        if not all_quotes:
            return pd.DataFrame()

        combined = pd.concat(all_quotes, ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"])
        combined = combined.sort_values(["date", "stock_code"])

        close_mat = combined.pivot(index="date", columns="stock_code", values="close")
        share_mat = combined.pivot(index="date", columns="stock_code", values="outstanding_share")

        # 预计算每只股票的 TTM 快照 [(publish_date, ttm)]
        # 关键优化：TTM 只在财报发布时变化，预计算后逐日用 bisect 查找
        ttm_by_code = {}
        for stock in stocks:
            code = stock["code"]
            qdf = self.cache.load_quarterly_profits(code)
            if qdf.empty:
                continue
            qdf = qdf.sort_values("publish_date")
            events = []
            for _, row in qdf.iterrows():
                pub = row["publish_date"]
                ttm = self._compute_ttm_from_quarters(qdf, pub)
                if ttm and ttm > 0:
                    events.append((pub, ttm))
            if events:
                ttm_by_code[code] = events

        valid_codes = [c for c in close_mat.columns if c in ttm_by_code]
        if not valid_codes:
            return pd.DataFrame()

        close_mat = close_mat[valid_codes]
        share_mat = share_mat[valid_codes]

        # 转 numpy 数组加速
        pub_dates_arr = np.array(
            [[e[0].value for e in ttm_by_code[c]] for c in valid_codes], dtype=object
        )
        ttm_vals_arr = np.array(
            [[e[1] for e in ttm_by_code[c]] for c in valid_codes], dtype=object
        )

        dates = close_mat.index.tolist()
        n_dates = len(dates)
        n_stocks = len(valid_codes)
        trade_dates_ns = np.array([pd.Timestamp(d).value for d in dates])
        min_required = max(10, int(n_stocks * 0.3))

        if progress_cb:
            progress_cb(f"计算 {n_dates} 个交易日 × {n_stocks} 只股票的 PE...")

        results = []
        for i, trade_date in enumerate(dates):
            td_ns = trade_dates_ns[i]
            close_row = close_mat.iloc[i].values
            share_row = share_mat.iloc[i].values

            valid_mask = (~np.isnan(close_row)) & (share_row > 0)
            n_valid = int(valid_mask.sum())
            if n_valid < min_required:
                continue

            total_mv = float(np.sum(close_row[valid_mask] * share_row[valid_mask]))

            # bisect 查找每只股票的 TTM
            total_ttm = 0.0
            for j in range(n_stocks):
                if not valid_mask[j]:
                    continue
                pd_arr = pub_dates_arr[j]
                if len(pd_arr) == 0:
                    continue
                idx = bisect_right(pd_arr, td_ns) - 1
                if idx >= 0:
                    total_ttm += ttm_vals_arr[j][idx]

            if total_ttm <= 0:
                continue

            pe = total_mv / total_ttm
            if 0 < pe < 100:
                results.append({
                    "trade_date": trade_date.strftime("%Y-%m-%d"),
                    "total_market_cap": total_mv,
                    "total_profit_ttm": total_ttm,
                    "pe_ratio": pe,
                    "stock_count": n_valid,
                })

            if progress_cb and (i + 1) % 500 == 0:
                progress_cb(f"  进度: {i+1}/{n_dates} ({(i+1)*100//n_dates}%)")

        if progress_cb:
            progress_cb(f"  进度: {n_dates}/{n_dates} (100%)")

        return pd.DataFrame(results)

    def _compute_statistics(self, index_code: str, pe_df: pd.DataFrame):
        from config import ROLLING_WINDOW
        pe_df = pe_df.copy().sort_values("trade_date").reset_index(drop=True)

        window = min(ROLLING_WINDOW, len(pe_df))
        pe_df["pe_mean"] = pe_df["pe_ratio"].rolling(window=window, min_periods=window // 2).mean()
        pe_df["pe_std"] = pe_df["pe_ratio"].rolling(window=window, min_periods=window // 2).std()
        pe_df["pe_high_line"] = pe_df["pe_mean"] + pe_df["pe_std"]
        pe_df["pe_low_line"] = pe_df["pe_mean"] - pe_df["pe_std"]

        sorted_pe = pe_df["pe_ratio"].sort_values().values
        pe_df["pe_percentile"] = pe_df["pe_ratio"].apply(
            lambda x: np.searchsorted(sorted_pe, x, side="left") / len(sorted_pe) * 100
        )

        metadata = ["quality_level", "data_source", "generated_at", "coverage_ratio",
                    "financial_stale_count", "validity_reason"]
        for column in metadata:
            if column not in pe_df:
                pe_df[column] = None

        self.cache.save_pe_stats(
            index_code,
            pe_df[["trade_date", "pe_mean", "pe_std", "pe_high_line", "pe_low_line",
                   "pe_percentile"] + metadata]
        )

    def load_result(self, index_code: str) -> Dict:
        pe_df = self.cache.load_pe_daily(index_code)
        stats_df = self.cache.load_pe_stats(index_code)
        if not pe_df.empty and not stats_df.empty:
            # Merge without duplicating index_code and updated_at
            stats_cols = [c for c in stats_df.columns if c not in pe_df.columns]
            pe_df = pe_df.merge(stats_df[["trade_date"] + stats_cols], on="trade_date", how="left")
        return {
            "pe_data": pe_df,
            "last_updated": self.cache.get_last_updated(index_code),
        }
