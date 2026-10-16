"""H30533 weighted earnings-yield valuation pipeline."""
import time
import pandas as pd
import numpy as np
from typing import Dict, Optional
from data_sources.h30533 import H30533Source
from data_sources.us import USDataSource
from cache.db import CacheDB
from config import API_BATCH_DELAY, CHINA_INTL_INDEX_CODE
from etl.stats import add_valuation_stats, STAT_COLUMNS
from etl.quality import quality_level, append_reason


def aggregate_h30533(rows: pd.DataFrame, min_weight_coverage: float = 0.95,
                     fx_rates: Optional[dict] = None) -> dict:
    """Aggregate verified H30533 inputs using weighted earnings yield.

    Weights are always index snapshot weights.  Invalid inputs are omitted from
    the numerator without renormalizing the remaining weights.
    """
    if not 0 <= min_weight_coverage <= 1:
        raise ValueError("min_weight_coverage must be between 0 and 1")

    total_weight = 0.0
    valid_weight = 0.0
    earnings_yield = 0.0
    missing_count = 0
    negative_profit_count = 0
    invalid_count = 0
    valid_count = 0
    fx_rates = fx_rates or {}
    currency_unavailable = False
    financial_stale_count = 0
    derived_market_cap_count = 0
    quote_disagreement_count = 0
    excluded_count = 0

    for _, row in rows.iterrows():
        try:
            weight = float(row.get("weight"))
        except (TypeError, ValueError):
            invalid_count += 1
            continue
        if not np.isfinite(weight) or weight < 0:
            invalid_count += 1
            continue
        total_weight += weight
        row_reason = str(row.get("validity_reason") or "")
        if not row.get("historical_backfill") and (row.get("financial_fresh") is False or "financial_stale" in row_reason or "stale_financial" in row_reason):
            financial_stale_count += 1
            excluded_count += 1
            continue
        if bool(row.get("quote_disagreement")) or "quote_disagreement" in row_reason:
            quote_disagreement_count += 1
            excluded_count += 1
            continue
        if "missing ads_ratio" in row_reason or "missing currency" in row_reason or "unsupported currency" in row_reason:
            missing_count += 1
            excluded_count += 1
            continue

        market_cap = row.get("market_cap")
        if market_cap is None or pd.isna(market_cap):
            shares = row.get("verified_shares")
            price = row.get("price")
            if shares is not None and price is not None and not pd.isna(shares) and not pd.isna(price):
                try:
                    market_cap = float(shares) * float(price)
                except (TypeError, ValueError):
                    market_cap = None
        profit_value = row.get("profit_ttm")
        if market_cap is None or pd.isna(market_cap) or profit_value is None or pd.isna(profit_value):
            missing_count += 1
            excluded_count += 1
            continue
        try:
            market_cap = float(market_cap)
            profit = float(profit_value)
        except (TypeError, ValueError):
            invalid_count += 1
            excluded_count += 1
            continue
        if not np.isfinite(market_cap) or not np.isfinite(profit):
            invalid_count += 1
            excluded_count += 1
            continue
        if market_cap <= 0:
            invalid_count += 1
            excluded_count += 1
            continue
        if row.get("market_cap_method") == "derived_profit_div_eps":
            derived_market_cap_count += 1
        market_currency = str(row.get("market_cap_currency") or row.get("currency") or "").upper()
        profit_currency = str(row.get("profit_currency") or row.get("currency") or "").upper()
        if market_currency and profit_currency and market_currency != profit_currency:
            rate = fx_rates.get(f"{profit_currency}/{market_currency}")
            if rate is None:
                missing_count += 1
                currency_unavailable = True
                excluded_count += 1
                continue
            try:
                rate = float(rate)
            except (TypeError, ValueError):
                invalid_count += 1
                excluded_count += 1
                continue
            if not np.isfinite(rate) or rate <= 0:
                invalid_count += 1
                excluded_count += 1
                continue
            profit /= rate
        if profit <= 0:
            negative_profit_count += 1
            excluded_count += 1
            continue

        valid_weight += weight
        earnings_yield += weight * (profit / market_cap)
        valid_count += 1

    # Coverage is against the published index weights, never against the
    # surviving rows.  This deliberately leaves missing weight unallocated.
    coverage = valid_weight
    reason = None
    if currency_unavailable:
        reason = "unavailable currency conversion"
    elif coverage < min_weight_coverage:
        reason = f"insufficient weight coverage: {coverage:.6f} < {min_weight_coverage:.6f}"
    elif earnings_yield <= 0:
        reason = "aggregate earnings yield is non-positive"
    source_value = rows.iloc[0].get("source_type") if not rows.empty else None
    qlevel = quality_level(
        source_type=source_value if source_value not in (None, "", "nan", "None") else None,
        weight_coverage=coverage,
        financial_stale_count=financial_stale_count,
        derived_market_cap_count=derived_market_cap_count,
        quote_disagreement_count=quote_disagreement_count,
    )
    # Quality C is still usable as a clearly-labelled research estimate once
    # 80% of published weight is covered. A hard currency/zero-earnings failure
    # remains unavailable.
    research_usable = coverage >= 0.80 and earnings_yield > 0 and not currency_unavailable
    if qlevel == "C" and reason is None:
        reason = "quality gate failed"

    return {
        "pe": 1 / earnings_yield if (reason is None or research_usable) else None,
        "earnings_yield": earnings_yield,
        "weight_coverage": coverage,
        "valid_weight": valid_weight,
        "total_weight": total_weight,
        "valid_count": valid_count,
        "missing_count": missing_count,
        "negative_profit_count": negative_profit_count,
        "invalid_count": invalid_count,
        "valid": reason is None,
        "reason": reason,
        "validity_reason": reason,
        "quality_level": qlevel,
        "financial_stale_count": financial_stale_count,
        "derived_market_cap_count": derived_market_cap_count,
        "quote_disagreement_count": quote_disagreement_count,
        "excluded_count": excluded_count,
    }


class USETL:
    """H30533 valuation ETL using the normalized source and input records."""

    def __init__(self, cache: Optional[CacheDB] = None):
        self.cache = cache or CacheDB()
        self.source = USDataSource()
        self.index_code = CHINA_INTL_INDEX_CODE
        self.index_name = "中证海外中国互联网50"
        self.h30533_source = H30533Source()

    def run(self, progress_cb=None) -> pd.DataFrame:
        """运行当前快照更新"""
        if progress_cb:
            progress_cb("获取成分股列表...")

        snapshot = self.h30533_source.get_snapshot()
        snapshot = snapshot.copy()
        snapshot["validity_reason"] = snapshot.get(
            "validity_reason", pd.Series(index=snapshot.index)
        )
        snapshot["constituent_as_of"] = snapshot["as_of"]
        snapshot["snapshot_date"] = snapshot["as_of"]
        total = len(snapshot)

        if progress_cb:
            progress_cb(f"开始更新 {self.index_name}：{total} 只股票")

        results = []
        valuation_rows = []
        for i, constituent in snapshot.iterrows():
            code = constituent["security_code"]
            name = constituent["name"]
            if progress_cb and i % 5 == 0:
                progress_cb(f"更新数据 [{i}/{total}]")

            record = self.source.get_current_pe(code)
            record.update({
                "code": code,
                "name": name,
                "weight": constituent["weight"],
                "source_type": constituent["source_type"],
                "constituent_as_of": constituent["as_of"],
            })
            results.append(record)
            valuation_rows.append({
                "security_code": code,
                "name": name,
                "issuer_id": constituent.get("issuer_id"),
                "weight": constituent["weight"],
                "price": record.get("price"),
                "currency": record.get("currency"),
                "market_cap_currency": record.get("market_cap_currency"),
                "profit_currency": record.get("profit_currency", record.get("currency")),
                "market_cap": record.get("market_cap"),
                "verified_shares": record.get("verified_shares"),
                "profit_ttm": record.get("profit_ttm"),
                "earnings_yield": None,
                "index_pe": None,
                "pe": record.get("pe"),
                "financial_as_of": record.get("financial_as_of"),
                "source_date": constituent["as_of"],
                "source_type": constituent["source_type"],
                "source": record.get("source"),
                "data_source": record.get("data_source", record.get("source")),
                "validity_reason": record.get("validity_reason"),
                "market_cap_method": record.get("market_cap_method"),
                "fx_rate": record.get("rate"),
                "normalized_profit_ttm": record.get("normalized_profit_ttm"),
                "financial_fresh": record.get("financial_fresh"),
                "quote_source_count": record.get("quote_source_count"),
                "quote_disagreement": record.get("quote_disagreement"),
                "source_url": constituent.get("source_url"),
                "fetched_at": constituent.get("fetched_at"),
                "response_hash": constituent.get("response_hash"),
                "current_constituents_backfill": False,
                "current_constituents_caveat": None,
            })
            time.sleep(API_BATCH_DELAY)

        if not results:
            return pd.DataFrame()

        df = pd.DataFrame(results)
        aggregate = aggregate_h30533(df, fx_rates={"CNY/USD": self.source._get_fx_rate()})
        today = pd.Timestamp.today().strftime("%Y-%m-%d")

        row = {
            "trade_date": today,
            "index_code": self.index_code,
            "total_market_cap": None,
            "total_profit_ttm": None,
            "pe_ratio": aggregate["pe"],
            "index_pe": aggregate["pe"],
            "earnings_yield": aggregate["earnings_yield"],
            "stock_count": aggregate["valid_count"],
            "weight_coverage": aggregate["weight_coverage"],
            "negative_profit_count": aggregate["negative_profit_count"],
            "missing_count": aggregate["missing_count"],
            "invalid_count": aggregate["invalid_count"],
            "quality_level": aggregate["quality_level"],
            "financial_stale_count": aggregate["financial_stale_count"],
            "derived_market_cap_count": aggregate["derived_market_cap_count"],
            "quote_disagreement_count": aggregate["quote_disagreement_count"],
            "excluded_count": aggregate["excluded_count"],
            "source_date": snapshot.iloc[0]["as_of"],
            "validity_reason": aggregate["reason"],
            "source_type": snapshot.iloc[0]["source_type"],
            "constituent_as_of": snapshot.iloc[0]["as_of"],
            "source": "H30533 constituent snapshot + USDataSource",
            "source_url": snapshot.iloc[0].get("source_url"),
            "fetched_at": snapshot.iloc[0].get("fetched_at"),
            "response_hash": snapshot.iloc[0].get("response_hash"),
            "current_constituents_backfill": False,
            "current_constituents_caveat": None,
        }
        pe_df = pd.DataFrame([row])

        existing = self.cache.load_h30533_snapshot(self.index_code)["daily"]
        if isinstance(existing, pd.DataFrame) and not existing.empty:
            pe_df = pd.concat([existing, pe_df], ignore_index=True)
            pe_df = pe_df.drop_duplicates("trade_date", keep="last")
        pe_df = add_valuation_stats(pe_df)
        self.cache.save_h30533_snapshot(
            self.index_code, snapshot, pe_df, pd.DataFrame(valuation_rows)
        )
        self.cache.save_us_pe_snapshot(today, pe_df)
        self.cache.save_us_stock_pe(today, df)
        self._recompute_us_stats()

        if progress_cb:
            progress_cb(f"完成：{self.index_name} PE = {aggregate['pe']}（{aggregate['valid_count']}/{total} 只有效）")

        return pe_df

    def load_result(self) -> Dict:
        """加载结果（含完整历史序列 + 最新快照）"""
        pe_df = self.cache.load_us_pe_daily()
        try:
            stats_df = self.cache.load_us_pe_stats()
            if not stats_df.empty:
                stats_cols = [c for c in stats_df.columns if c in ("trade_date", "pe_mean", "pe_std",
                                    "pe_high_line", "pe_low_line", "pe_percentile")]
                pe_df = pe_df.merge(stats_df[stats_cols], on="trade_date", how="left")
        except Exception:
            pass
        stock_pe = self.cache.load_us_stock_pe()
        constituents = self.h30533_source.get_snapshot()
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
        pe_df = add_valuation_stats(pe_df)
        self.cache.save_us_pe_daily(pe_df)
        stats_cols = ["trade_date", *STAT_COLUMNS]
        self.cache.save_us_pe_stats(pe_df[stats_cols])

    def run_historical(self, progress_cb=None) -> pd.DataFrame:
        """Backfill current H30533 constituents using historical closes.

        This is deliberately not presented as official historical membership.
        Current verified financial inputs are held constant while historical
        closes vary, and every row carries the backfill caveat and quality C.
        """
        snapshot = self.h30533_source.get_snapshot()
        snapshot = snapshot.copy()
        source_meta = getattr(self.h30533_source, "source_metadata", {}) or {}
        for key in ("source_url", "fetched_at", "response_hash"):
            if key not in snapshot:
                snapshot[key] = source_meta.get(key)
            else:
                snapshot[key] = snapshot[key].where(snapshot[key].notna(), source_meta.get(key))
        records = []
        for _, constituent in snapshot.iterrows():
            record = self.source.get_current_pe(constituent["security_code"]) or {}
            record.update({
                "security_code": constituent["security_code"],
                "name": constituent.get("name"),
                "weight": constituent["weight"],
                "source_date": constituent["as_of"],
                "source_type": constituent["source_type"],
                "constituent_basis": "current_constituents_backfill",
                "financial_fresh": True,
                "historical_backfill": True,
                "source_url": constituent.get("source_url"),
                "fetched_at": constituent.get("fetched_at"),
                "response_hash": constituent.get("response_hash"),
            })
            records.append(record)
        if not records:
            return pd.DataFrame()

        # Historical quotes are intentionally not presented as official historical membership.
        quotes = {}
        for record in records:
            daily = self.source.get_daily_quotes(record["code"])
            if not daily.empty:
                quotes[record["code"]] = daily
        if not quotes:
            return pd.DataFrame()

        input_by_code = {record["code"]: record for record in records}
        dates = sorted({date for quote in quotes.values() for date in quote["date"].unique()})[-500:]
        results = []
        for date in dates:
            rows = []
            for code, quote in quotes.items():
                day = quote[quote["date"] == date]
                if day.empty:
                    continue
                row = dict(input_by_code[code])
                row["price"] = float(day.iloc[-1]["close"])
                shares = row.get("verified_shares")
                ads_ratio = row.get("ads_ratio") or 1.0
                row["market_cap"] = (
                    row["price"] * float(shares) / float(ads_ratio)
                    if shares is not None and not pd.isna(shares)
                    else None
                )
                row["market_cap_method"] = "price_times_reported_shares" if row["market_cap"] is not None else None
                rows.append(row)
            aggregate = aggregate_h30533(
                pd.DataFrame(rows), min_weight_coverage=0.95,
                fx_rates={"CNY/USD": self.source._get_fx_rate(), "HKD/USD": self.source._get_fx_rate()}
            )
            results.append({
                "trade_date": date,
                "index_code": self.index_code,
                "source_type": snapshot.iloc[0]["source_type"],
                "source_date": snapshot.iloc[0]["as_of"],
                "constituent_as_of": snapshot.iloc[0]["as_of"],
                "source_url": snapshot.iloc[0].get("source_url"),
                "fetched_at": snapshot.iloc[0].get("fetched_at"),
                "response_hash": snapshot.iloc[0].get("response_hash"),
                "total_market_cap": None,
                "total_profit_ttm": None,
                "pe_ratio": aggregate["pe"],
                "index_pe": aggregate["pe"],
                "earnings_yield": aggregate["earnings_yield"],
                "stock_count": aggregate["valid_count"],
                "valid_count": aggregate["valid_count"],
                "weight_coverage": aggregate["weight_coverage"],
                "negative_profit_count": aggregate["negative_profit_count"],
                "missing_count": aggregate["missing_count"],
                "invalid_count": aggregate["invalid_count"],
                "financial_stale_count": aggregate["financial_stale_count"],
                "derived_market_cap_count": aggregate["derived_market_cap_count"],
                "quote_disagreement_count": aggregate["quote_disagreement_count"],
                "excluded_count": aggregate["excluded_count"],
                "quality_level": "C",
                "validity_reason": aggregate["reason"],
                "constituent_basis": "current_constituents_backfill",
                "current_constituents_backfill": True,
                "current_constituents_caveat": "historical values use current constituents and current financial inputs",
            })

        pe_df = add_valuation_stats(pd.DataFrame(results))
        self.cache.save_h30533_snapshot(
            self.index_code, snapshot, pe_df, pd.DataFrame(records)
        )
        self.cache.save_us_pe_daily(pe_df)
        self._recompute_us_stats()
        if progress_cb:
            progress_cb(f"完成历史序列：{len(pe_df)} 个交易日（current_constituents_backfill）")
        return pe_df
