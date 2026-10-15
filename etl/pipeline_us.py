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
from etl.quality import append_reason, is_stale, quality_level


def aggregate_h30533(rows: pd.DataFrame, min_weight_coverage: float = 0.95,
                     fx_rates: Optional[dict] = None) -> dict:
    """Aggregate eligible securities without renormalizing surviving weights."""
    if not 0 <= min_weight_coverage <= 1:
        raise ValueError("min_weight_coverage must be between 0 and 1")

    coverage = 0.0
    earnings_yield = 0.0
    valid_count = 0
    negative_profit_count = 0
    missing_count = 0
    invalid_count = 0
    financial_stale_count = 0
    derived_market_cap_count = 0
    quote_disagreement_count = 0
    excluded_count = 0
    reasons = []
    fx_rates = fx_rates or {}

    for _, row in rows.iterrows():
        try:
            weight = float(row.get("weight"))
        except (TypeError, ValueError):
            invalid_count += 1
            excluded_count += 1
            reasons.append("invalid weight")
            continue
        if not np.isfinite(weight) or weight < 0:
            invalid_count += 1
            excluded_count += 1
            reasons.append("invalid weight")
            continue
        reason = row.get("validity_reason")
        if reason is not None and pd.isna(reason):
            reason = None
        if is_stale(row.get("financial_as_of"), row.get("as_of") or
                    row.get("constituent_as_of") or row.get("source_date")):
            financial_stale_count += 1
            excluded_count += 1
            reasons.append("financial_stale")
            continue
        if bool(row.get("quote_disagreement")):
            quote_disagreement_count += 1
            excluded_count += 1
            reasons.append("quote_disagreement")
            continue

        market_cap = row.get("market_cap")
        method = row.get("market_cap_method")
        if market_cap is None or pd.isna(market_cap):
            shares, price = row.get("verified_shares"), row.get("price")
            if shares is not None and price is not None and not pd.isna(shares) and not pd.isna(price):
                try:
                    market_cap = float(shares) * float(price)
                    method = method or "price_times_reported_shares"
                except (TypeError, ValueError):
                    market_cap = None
            if market_cap is None and row.get("profit_ttm") is not None and row.get("eps_ttm"):
                try:
                    market_cap = float(row["price"]) * float(row["profit_ttm"]) / float(row["eps_ttm"])
                    method = method or "derived_profit_div_eps"
                except (TypeError, ValueError, ZeroDivisionError):
                    market_cap = None
        if method in ("price_times_reported_shares", "derived_profit_div_eps"):
            derived_market_cap_count += 1
        profit = row.get("profit_ttm")
        if market_cap is None or profit is None or pd.isna(market_cap) or pd.isna(profit):
            missing_count += 1
            excluded_count += 1
            reasons.append(reason or "missing valuation")
            continue
        try:
            market_cap, profit = float(market_cap), float(profit)
        except (TypeError, ValueError):
            invalid_count += 1
            excluded_count += 1
            reasons.append(reason or "invalid valuation")
            continue
        if not np.isfinite(market_cap) or not np.isfinite(profit) or market_cap <= 0:
            invalid_count += 1
            excluded_count += 1
            reasons.append(reason or "invalid valuation")
            continue
        market_currency_value = row.get("market_cap_currency")
        profit_currency_value = row.get("profit_currency")
        market_currency = "" if market_currency_value is None or pd.isna(market_currency_value) else str(market_currency_value).upper()
        profit_currency = "" if profit_currency_value is None or pd.isna(profit_currency_value) else str(profit_currency_value).upper()
        if not market_currency or not profit_currency:
            missing_count += 1
            excluded_count += 1
            reasons.append("missing currency")
            continue
        if market_currency != profit_currency:
            rate = fx_rates.get(f"{profit_currency}/{market_currency}")
            if rate is None or not np.isfinite(float(rate)) or float(rate) <= 0:
                missing_count += 1
                excluded_count += 1
                reasons.append("currency_unavailable")
                continue
            profit /= float(rate)
        if profit <= 0:
            negative_profit_count += 1
            excluded_count += 1
            continue
        coverage += weight
        earnings_yield += weight * profit / market_cap
        valid_count += 1

    quality = quality_level(
        source_type=rows.iloc[0].get("source_type") if not rows.empty else None,
        weight_coverage=coverage,
        financial_stale_count=financial_stale_count,
        derived_market_cap_count=derived_market_cap_count,
        quote_disagreement_count=quote_disagreement_count,
    )
    if coverage < min_weight_coverage:
        reasons.append(f"insufficient weight coverage: {coverage:.6f} < {min_weight_coverage:.6f}")
    validity_reason = None
    for reason in reasons:
        validity_reason = append_reason(validity_reason, reason)
    valid = coverage >= min_weight_coverage and earnings_yield > 0 and not financial_stale_count and not quote_disagreement_count
    return {
        "pe": 1 / earnings_yield if valid else None,
        "earnings_yield": earnings_yield,
        "weight_coverage": coverage,
        "valid_weight": coverage,
        "total_weight": float(pd.to_numeric(rows.get("weight"), errors="coerce").fillna(0).sum()) if not rows.empty else 0.0,
        "valid_count": valid_count,
        "missing_count": missing_count,
        "negative_profit_count": negative_profit_count,
        "invalid_count": invalid_count,
        "financial_stale_count": financial_stale_count,
        "derived_market_cap_count": derived_market_cap_count,
        "quote_disagreement_count": quote_disagreement_count,
        "excluded_count": excluded_count,
        "quality_level": quality,
        "valid": valid,
        "reason": validity_reason,
        "validity_reason": validity_reason,
    }


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
        constituents["index_code"] = self.index_code
        constituents["validity_reason"] = constituents.get("validity_reason", pd.Series(index=constituents.index))
        constituents["constituent_as_of"] = constituents["as_of"]
        constituents["snapshot_date"] = constituents["as_of"]
        source_meta = getattr(self.h30533_source, "source_metadata", {}) or {}
        for key in ("source_url", "fetched_at", "response_hash"):
            if key not in constituents:
                constituents[key] = source_meta.get(key)
            else:
                constituents[key] = constituents[key].where(
                    constituents[key].notna(), source_meta.get(key)
                )
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
                "price": price, "currency": record.get("currency"),
                "market_cap_currency": record.get("market_cap_currency", record.get("currency")),
                "profit_currency": record.get("profit_currency", record.get("currency")),
                "market_cap": market_cap,
                "verified_shares": shares, "profit_ttm": profit,
                "as_of": constituent.get("as_of"),
                "market_cap_method": record.get("market_cap_method") or (
                    "price_times_reported_shares" if market_cap is not None and record.get("market_cap") is None else None
                ),
                "quote_disagreement": record.get("quote_disagreement", False),
                "quote_source_count": record.get("quote_source_count"),
                "fx_rate": record.get("rate"),
                "normalized_profit_ttm": record.get("normalized_profit_ttm"),
                "financial_fresh": record.get("financial_fresh"),
                "source_url": constituent.get("source_url"),
                "fetched_at": constituent.get("fetched_at"),
                "response_hash": constituent.get("response_hash"),
                "earnings_yield": (profit / market_cap) if valid else None,
                "index_pe": pe if valid else None, "pe": pe if valid else None,
                "financial_as_of": record.get("financial_as_of"),
                "source_date": constituent.get("as_of"),
                "source_type": constituent.get("source_type"),
                "source": record.get("source"), "data_source": record.get("data_source", record.get("source")),
                "validity_reason": record.get("validity_reason") or (None if valid else "missing or invalid valuation"),
                "quality_level": "A" if valid else "C",
                "current_constituents_backfill": False,
                "current_constituents_caveat": None,
            })
            if valid:
                results.append(valuation_rows[-1])
            time.sleep(API_BATCH_DELAY)

        aggregate = aggregate_h30533(
            pd.DataFrame(valuation_rows),
            fx_rates={"CNY/USD": self.source._get_fx_rate(), "HKD/USD": self.source._get_fx_rate()},
        )

        if not results or aggregate["pe"] is None:
            today = pd.Timestamp.today().strftime("%Y-%m-%d")
            diagnostic = pd.DataFrame([{
                "trade_date": today,
                "index_code": self.index_code,
                "index_pe": None,
                "pe_ratio": None,
                "earnings_yield": None,
                "total_market_cap": None,
                "total_profit_ttm": None,
                "stock_count": total,
                "valid_count": 0,
                "weight_coverage": aggregate["weight_coverage"],
                "negative_profit_count": aggregate["negative_profit_count"],
                "missing_count": aggregate["missing_count"],
                "financial_stale_count": aggregate["financial_stale_count"],
                "derived_market_cap_count": aggregate["derived_market_cap_count"],
                "quote_disagreement_count": aggregate["quote_disagreement_count"],
                "excluded_count": aggregate["excluded_count"],
                "invalid_count": aggregate["invalid_count"],
                "quality_level": aggregate["quality_level"],
                "source_type": constituents.iloc[0].get("source_type") if total else None,
                "source_date": constituents.iloc[0].get("as_of") if total else None,
                "constituent_as_of": constituents.iloc[0].get("as_of") if total else None,
                "constituent_basis": "H30533",
                "source_url": constituents.iloc[0].get("source_url") if total else None,
                "fetched_at": constituents.iloc[0].get("fetched_at") if total else None,
                "response_hash": constituents.iloc[0].get("response_hash") if total else None,
                "validity_reason": aggregate["validity_reason"] or "no valid security valuations",
                "current_constituents_backfill": False,
                "current_constituents_caveat": None,
            }])
            self.cache.save_h30533_snapshot(
                self.index_code, constituents, diagnostic, pd.DataFrame(valuation_rows)
            )
            return diagnostic

        df = pd.DataFrame(results)
        today = pd.Timestamp.today().strftime("%Y-%m-%d")

        index_pe = aggregate["pe"]
        total_mv = float(sum(row["market_cap"] for row in valuation_rows if row in results))
        total_profit = float(total_mv / index_pe) if index_pe else None

        row = {
            "trade_date": today,
            "index_code": self.index_code,
            "index_pe": index_pe,
            "earnings_yield": aggregate["earnings_yield"],
            "total_market_cap": round(total_mv, 6),
            "total_profit_ttm": round(total_profit, 6),
            "pe_ratio": index_pe,
            "stock_count": aggregate["valid_count"], "valid_count": aggregate["valid_count"],
            "weight_coverage": aggregate["weight_coverage"],
            "negative_profit_count": aggregate["negative_profit_count"],
            "missing_count": aggregate["missing_count"],
            "financial_stale_count": aggregate["financial_stale_count"],
            "derived_market_cap_count": aggregate["derived_market_cap_count"],
            "quote_disagreement_count": aggregate["quote_disagreement_count"],
            "excluded_count": aggregate["excluded_count"],
            "invalid_count": aggregate["invalid_count"],
            "quality_level": aggregate["quality_level"],
            "source_type": constituents.iloc[0].get("source_type"),
            "source_date": constituents.iloc[0].get("as_of"),
            "constituent_as_of": constituents.iloc[0].get("as_of"),
            "constituent_basis": "H30533",
            "source_url": constituents.iloc[0].get("source_url"),
            "fetched_at": constituents.iloc[0].get("fetched_at"),
            "response_hash": constituents.iloc[0].get("response_hash"),
            "validity_reason": aggregate["validity_reason"],
            "current_constituents_backfill": False,
            "current_constituents_caveat": None,
        }
        pe_df = pd.DataFrame([row])

        # Persist the source snapshot and all enriched security rows together.
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
            source_meta = getattr(self.h30533_source, "source_metadata", {}) or {}
            pe_df["quality_level"] = "C"
            pe_df["financial_stale_count"] = 0
            pe_df["derived_market_cap_count"] = 0
            pe_df["quote_disagreement_count"] = 0
            pe_df["excluded_count"] = 0
            pe_df["invalid_count"] = 0
            pe_df["validity_reason"] = "current_constituents_backfill"
            pe_df["current_constituents_backfill"] = True
            pe_df["current_constituents_caveat"] = "historical values use current constituents"
            pe_df["source_url"] = source_meta.get("source_url")
            pe_df["fetched_at"] = source_meta.get("fetched_at")
            pe_df["response_hash"] = source_meta.get("response_hash")
            pe_df["source_type"] = source_meta.get("source_type", "current_constituents_backfill")
            pe_df["source_date"] = source_meta.get("as_of")
            pe_df["constituent_as_of"] = source_meta.get("as_of")
            pe_df["constituent_basis"] = "H30533"
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
