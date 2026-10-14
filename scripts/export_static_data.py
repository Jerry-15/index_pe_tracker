"""
导出静态数据（供 Netlify 静态图表版使用）

将 SQLite 中的 PE 数据导出为 JSON 文件到 static/data/ 目录。
由 GitHub Actions 定时运行，push 后触发 Netlify 重新部署。

用法: python scripts/export_static_data.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from cache.db import CacheDB
from data_sources import get_source
from config import INDEX_CONFIG
from etl.pipeline_us import USETL

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "static" / "data"

# 每个指数最多导出的天数（近10年，控制前端数据量）
MAX_DAYS = 2500


def export_all():
    cache = CacheDB()
    source = get_source()
    OUT.mkdir(parents=True, exist_ok=True)

    index_list = []

    # 宽基指数
    for name, cfg in INDEX_CONFIG.items():
        code = cfg["code"]
        pe_df = cache.load_pe_daily(code)
        stats_df = cache.load_pe_stats(code)
        if pe_df.empty:
            continue
        if not stats_df.empty:
            pe_df = pe_df.merge(
                stats_df[["trade_date", "pe_mean", "pe_std", "pe_high_line", "pe_low_line", "pe_percentile"]],
                on="trade_date", how="left",
            )
        rows = _to_rows(pe_df)
        if not rows:
            continue
        _save(code, rows)
        index_list.append({
            "code": code, "name": name, "type": "index",
            "pe_ref": _get_reference_pe(source, name),
        })

    # 申万行业
    ref = source.get_industry_reference()
    for _, r in ref.iterrows():
        code, name = r["code"], r["name"]
        pe_df = cache.load_pe_daily(code)
        stats_df = cache.load_pe_stats(code)
        if pe_df.empty:
            continue
        if not stats_df.empty:
            pe_df = pe_df.merge(
                stats_df[["trade_date", "pe_mean", "pe_std", "pe_high_line", "pe_low_line", "pe_percentile"]],
                on="trade_date", how="left",
            )
        rows = _to_rows(pe_df)
        if not rows:
            continue
        _save(code, rows)
        index_list.append({
            "code": code, "name": name, "type": "industry",
            "pe_ref": float(r["pe_ttm"]) if pd.notna(r["pe_ttm"]) else None,
        })

    # 保存指数列表
    with open(OUT / "index_list.json", "w", encoding="utf-8") as f:
        json.dump(index_list, f, ensure_ascii=False, indent=1)

    # 导出中概互联网数据
    _export_china_intl(cache, source)

    print(f"导出完成: {len(index_list)} 个指数/行业 -> {OUT}")


def _export_china_intl(cache: CacheDB, source):
    """导出中概互联网指数 PE 数据"""
    from etl.pipeline_us import USETL
    us_etl = USETL(cache)
    pe_df = cache.load_us_pe_daily()
    if pe_df.empty:
        return
    # 计算最近一次快照
    stock_pe = cache.load_us_stock_pe()
    stock_rows = []
    if not stock_pe.empty:
        for _, r in stock_pe.iterrows():
            stock_rows.append({
                "code": r["code"],
                "name": r["name"],
                "price": r["price"],
                "pe": r["pe"],
            })
    latest_row = pe_df.iloc[-1]
    rows = _to_rows(pe_df)
    code = "CHINA_INTL"
    _save(code, rows)

    # 更新 index_list
    try:
        list_path = OUT / "index_list.json"
        if list_path.exists():
            with open(list_path, "r", encoding="utf-8") as f:
                idx_list = json.load(f)
        else:
            idx_list = []
        entry = {
            "code": code,
            "name": "中概互联网",
            "type": "us_stock",
            "pe_ref": None,
            "stock_pe": stock_rows,
            "last_updated": str(latest_row.get("trade_date", "")),
        }
        existing = next((i for i in idx_list if i.get("code") == code), None)
        if existing is None:
            idx_list.append(entry)
        else:
            existing.update(entry)
        with open(list_path, "w", encoding="utf-8") as f:
            json.dump(idx_list, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _to_rows(pe_df: pd.DataFrame) -> list:
    """转换为紧凑行数组 [date, pe, mean, high, low, pct, mv, profit]"""
    df = pe_df.sort_values("trade_date").tail(MAX_DAYS)
    rows = []
    for _, r in df.iterrows():
        mean = float(r["pe_mean"]) if pd.notna(r.get("pe_mean")) else None
        high = float(r["pe_high_line"]) if pd.notna(r.get("pe_high_line")) else None
        low = float(r["pe_low_line"]) if pd.notna(r.get("pe_low_line")) else None
        pct = float(r["pe_percentile"]) if pd.notna(r.get("pe_percentile")) else None
        mv = float(r["total_market_cap"]) if pd.notna(r.get("total_market_cap")) else None
        profit = float(r["total_profit_ttm"]) if pd.notna(r.get("total_profit_ttm")) else None
        rows.append([r["trade_date"], round(float(r["pe_ratio"]), 3), mean, high, low, pct, mv, profit])
    return rows


def _save(code: str, rows: list):
    with open(OUT / f"{code}.json", "w", encoding="utf-8") as f:
        json.dump(rows, f)


def _get_reference_pe(source, index_name: str) -> float | None:
    """宽基指数的当前参考PE（乐咕乐股最近值）"""
    from config import INDEX_REFERENCE
    ref_name = INDEX_REFERENCE.get(index_name)
    if not ref_name:
        return None
    try:
        df = source.get_index_pe_history(ref_name)
        if df is not None and not df.empty:
            return float(df["pe_ttm"].iloc[-1])
    except Exception:
        pass
    return None


if __name__ == "__main__":
    export_all()
