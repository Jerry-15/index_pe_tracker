"""中概互联网指数 PE 计算引擎（港股实时EPS + 美股参考PE）"""
import time
import requests
import pandas as pd
import numpy as np
from typing import Dict, Optional
from data_sources.us import USDataSource
from cache.db import CacheDB
from config import API_BATCH_DELAY, CHINA_INTL_STOCKS, US_REF_PE
from data_sources.h30533 import H30533Source


class USETL:
    """中概互联网指数 PE 计算（22港股+13美股ADR，共35只）"""

    def __init__(self, cache: Optional[CacheDB] = None):
        self.cache = cache or CacheDB()
        self.source = USDataSource()
        self.index_code = "H30533"
        self.index_name = "中概互联网"
        self.h30533_source = H30533Source()

    def _get_hk_price(self, code: str) -> float:
        """从新浪获取港股实时价格"""
        try:
            url = f"https://hq.sinajs.cn/list=hk{code.replace('.HK','')}"
            headers = {"Referer": "https://finance.sina.com.cn"}
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200 and len(r.text) > 50:
                parts = r.text.split('="')[1].strip().rstrip(';').strip('"').split(',')
                return float(parts[2]) if len(parts) > 2 and parts[2] else 0
        except Exception:
            pass
        return 0

    def _get_hk_eps(self, code: str) -> Optional[float]:
        """从akshare获取港股EPS_TTM"""
        try:
            import akshare as ak
            df = ak.stock_financial_hk_analysis_indicator_em(symbol=code.replace('.HK',''))
            if not df.empty and 'EPS_TTM' in df.columns:
                eps = df.iloc[0]['EPS_TTM']
                return float(eps) if eps and eps > 0 else None
        except Exception:
            pass
        return None

    def _get_us_price(self, code: str) -> float:
        """从akshare获取美股日K最新价"""
        try:
            import akshare as ak
            df = ak.stock_us_daily(symbol=code)
            if not df.empty:
                return float(df.iloc[-1]['close'])
        except Exception:
            pass
        return 0

    def run(self, progress_cb=None) -> pd.DataFrame:
        """运行当前快照更新"""
        if progress_cb:
            progress_cb("获取成分股列表...")

        constituents = self.h30533_source.get_snapshot()
        constituents = constituents.copy()
        constituents["validity_reason"] = constituents.get("validity_reason", pd.Series(index=constituents.index))
        total = len(constituents)

        if progress_cb:
            progress_cb(f"开始更新 {self.index_name}：{total} 只股票")

        results = []
        valuation_rows = []
        for i, constituent in constituents.iterrows():
            code = constituent["security_code"]
            name = constituent.get("name", "")
            if progress_cb and i % 5 == 0:
                progress_cb(f"更新数据 [{i}/{total}]")

            record = self.source.get_current_pe(code) or {}
            price = record.get("price")
            market_cap = record.get("market_cap")
            shares = record.get("verified_shares")
            if market_cap is None and price is not None and shares is not None:
                market_cap = float(price) * float(shares)
            profit = record.get("profit_ttm")
            pe = record.get("pe")
            if pe is None and market_cap and profit and profit > 0:
                pe = market_cap / profit
            valid = bool(market_cap and profit and profit > 0 and pe and 0 < pe < 500)
            valuation_rows.append({
                "index_code": self.index_code, "security_code": code, "name": name,
                "issuer_id": constituent.get("issuer_id"), "weight": constituent.get("weight"),
                "price": price, "currency": record.get("currency"), "market_cap": market_cap,
                "verified_shares": shares, "profit_ttm": profit,
                "earnings_yield": (profit / market_cap) if valid else None,
                "index_pe": pe if valid else None, "pe": pe if valid else None,
                "financial_as_of": record.get("financial_as_of"),
                "source_date": constituent.get("as_of"),
                "source_type": constituent.get("source_type"),
                "source": record.get("source"), "data_source": record.get("data_source", record.get("source")),
                "validity_reason": record.get("validity_reason") or (None if valid else "missing or invalid valuation"),
            })
            if valid:
                results.append(valuation_rows[-1])
            time.sleep(API_BATCH_DELAY)

        if not results:
            return pd.DataFrame()

        df = pd.DataFrame(results)
        today = pd.Timestamp.today().strftime("%Y-%m-%d")

        # 市值加权PE = Σ(MV_i) / Σ(Profit_i)，MV_proxy = price²/pe
        mvs = df["market_cap"].astype(float).values
        pes = df["pe"].astype(float).values
        total_mv = float(mvs.sum())
        total_profit = float(total_mv / (float((mvs * pes).sum() / total_mv)))
        index_pe = float((mvs * pes).sum() / total_mv)

        row = {
            "trade_date": today,
            "index_code": self.index_code,
            "index_pe": index_pe,
            "earnings_yield": 1 / index_pe,
            "total_market_cap": round(total_mv, 6),
            "total_profit_ttm": round(total_profit, 6),
            "pe_ratio": index_pe,
            "stock_count": len(df), "valid_count": len(df),
            "weight_coverage": float(sum(row.get("weight") or 0 for row in results)),
            "negative_profit_count": sum(1 for row in valuation_rows if row.get("profit_ttm") is not None and row["profit_ttm"] < 0),
            "missing_count": len(valuation_rows) - len(df),
            "source_type": constituents.iloc[0].get("source_type"),
            "source_date": constituents.iloc[0].get("as_of"),
            "constituent_as_of": constituents.iloc[0].get("as_of"),
            "constituent_basis": "H30533",
            "validity_reason": None,
        }
        pe_df = pd.DataFrame([row])

        self.cache.save_h30533_snapshot(self.index_code, constituents, pe_df, pd.DataFrame(valuation_rows))
        self._recompute_us_stats()

        if progress_cb:
            progress_cb(f"✅ 完成：中概互联网 PE = {index_pe:.2f}x（{len(df)}/{total} 只有效）")

        return pe_df

    def load_result(self) -> Dict:
        """加载结果（含完整历史序列 + 最新快照）"""
        snapshot = self.cache.load_h30533_snapshot(self.index_code)
        pe_df = snapshot["daily"]
        stock_pe = snapshot["securities"]
        if "code" not in stock_pe and "security_code" in stock_pe:
            stock_pe = stock_pe.copy()
            stock_pe["code"] = stock_pe["security_code"]
        constituents = snapshot["constituents"]
        return {
            "pe_data": pe_df,
            "stock_pe": stock_pe,
            "constituents": constituents,
            "last_updated": self.cache.get_us_last_updated(),
        }

    def _recompute_us_stats(self):
        """重新计算并保存 US PE 统计指标"""
        snapshot = self.cache.load_h30533_snapshot(self.index_code)
        pe_df = snapshot["daily"]
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
        self.cache.save_h30533_daily(self.index_code, pe_df)

    def run_historical(self, progress_cb=None) -> pd.DataFrame:
        """构建历史 PE 序列（港股用历史EPS，美股用固定参考PE）"""
        stocks = CHINA_INTL_STOCKS
        total = len(stocks)

        if progress_cb:
            progress_cb(f"拉取行情数据（{total} 只股票）...")

        # 获取各股历史日K
        all_quotes = {}
        for i, (code, name) in enumerate(stocks.items()):
            if progress_cb and i % 5 == 0:
                progress_cb(f"拉取行情 [{i}/{total}]")
            if code.endswith('.HK'):
                quotes = self.source.get_daily_quotes(code)
            else:
                quotes = self.source.get_daily_quotes(code)
            if not quotes.empty:
                all_quotes[code] = quotes
            time.sleep(API_BATCH_DELAY)

        if not all_quotes:
            return pd.DataFrame()

        # 港股历史EPS序列
        eps_series = {}
        for code in list(stocks.keys()):
            if not code.endswith('.HK'):
                continue
            try:
                import akshare as ak
                df = ak.stock_financial_hk_analysis_indicator_em(symbol=code.replace('.HK',''))
                if df.empty or 'EPS_TTM' not in df.columns:
                    continue
                events = []
                for _, row in df.iterrows():
                    rd = str(row.get('REPORT_DATE', ''))[:10]
                    eps = row.get('EPS_TTM')
                    if not eps or eps <= 0:
                        continue
                    try:
                        rd_ts = pd.Timestamp(rd)
                    except Exception:
                        continue
                    events.append((rd_ts, float(eps)))
                events.sort()
                if events:
                    eps_series[code] = events
            except Exception:
                pass

        if progress_cb:
            progress_cb(f"共同交易日计算 PE...")

        # 找出共同日期
        all_dates = set()
        for q in all_quotes.values():
            all_dates.update(q["date"].unique().tolist())
        common_dates = sorted(all_dates)

        results = []
        for date in common_dates[-500:]:
            try:
                td = pd.Timestamp(date)
            except Exception:
                continue
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
                if code.endswith('.HK'):
                    # 港股：用历史EPS
                    events = eps_series.get(code)
                    if not events:
                        continue
                    best_eps = None
                    for rd, eps_val in events:
                        if rd <= td:
                            best_eps = eps_val
                        else:
                            break
                    if best_eps is None or best_eps <= 0:
                        continue
                    pe = price / best_eps
                else:
                    # 美股：用固定参考PE
                    ref_pe = US_REF_PE.get(code)
                    if ref_pe is None or ref_pe <= 0:
                        continue
                    pe = ref_pe

                if pe > 0 and pe < 500:
                    mv_proxy = (price ** 2) / pe
                    pe_sum += mv_proxy * pe
                    mv_sum += mv_proxy
                    count += 1

            if count > 0 and mv_sum > 0 and count >= 25:
                index_pe = pe_sum / mv_sum
                results.append({
                    "trade_date": date,
                    "total_market_cap": round(mv_sum, 6),
                    "total_profit_ttm": round(mv_sum / index_pe, 6),
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
            stats_cols = ["trade_date", "pe_mean", "pe_std", "pe_high_line", "pe_low_line", "pe_percentile"]
            self.cache.save_us_pe_stats(pe_df[stats_cols])
            return pe_df
        return pd.DataFrame()
