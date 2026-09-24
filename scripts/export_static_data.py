"""
导出静态数据（供 Netlify 静态图表版使用）

将 SQLite 中的 PE 数据导出为 JSON 文件到 static/data/ 目录。
由 GitHub Actions 定时运行，push 后触发 Netlify 重新部署。

用法: python scripts/export_static_data.py
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from cache.db import CacheDB
from data_sources import get_source
from config import INDEX_CONFIG

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
            **_validity_metadata(pe_df),
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
            "pe_ref": _finite_float(r["pe_ttm"]),
            **_validity_metadata(pe_df),
        })

    # 保存指数列表
    with open(OUT / "index_list.json", "w", encoding="utf-8") as f:
        json.dump(index_list, f, ensure_ascii=False, indent=1, allow_nan=False)

    print(f"导出完成: {len(index_list)} 个指数/行业 -> {OUT}")


def _to_rows(pe_df: pd.DataFrame) -> list:
    """转换为JSON安全行数组，尾部保留PE有效性元数据。"""
    df = pe_df.sort_values("trade_date").tail(MAX_DAYS)
    rows = []
    for _, r in df.iterrows():
        mean = _finite_float(r.get("pe_mean"))
        high = _finite_float(r.get("pe_high_line"))
        low = _finite_float(r.get("pe_low_line"))
        pct = _finite_float(r.get("pe_percentile"))
        mv = _finite_float(r.get("total_market_cap"))
        profit = _finite_float(r.get("total_profit_ttm"))
        pe = _finite_float(r.get("pe_ratio"))
        rows.append([
            r["trade_date"], round(pe, 3) if pe is not None else None,
            mean, high, low, pct, mv, profit,
            _optional_int(r.get("valid_stock_count")),
            _optional_int(r.get("profitable_stock_count")),
            _optional_float(r.get("coverage_ratio")),
            r.get("validity_reason") if pd.notna(r.get("validity_reason")) else None,
        ])
    return rows


def _save(code: str, rows: list):
    with open(OUT / f"{code}.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, allow_nan=False)


def _optional_int(value):
    value = _finite_float(value)
    return int(value) if value is not None else None


def _optional_float(value):
    return _finite_float(value)


def _finite_float(value):
    if pd.isna(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _validity_metadata(pe_df: pd.DataFrame) -> dict:
    latest = pe_df.sort_values("trade_date").iloc[-1]
    return {
        "valid_stock_count": _optional_int(latest.get("valid_stock_count")),
        "profitable_stock_count": _optional_int(latest.get("profitable_stock_count")),
        "coverage_ratio": _optional_float(latest.get("coverage_ratio")),
        "validity_reason": latest.get("validity_reason") if pd.notna(latest.get("validity_reason")) else None,
    }


def _get_reference_pe(source, index_name: str) -> float | None:
    """宽基指数的当前参考PE（乐咕乐股最近值）"""
    from config import INDEX_REFERENCE
    ref_name = INDEX_REFERENCE.get(index_name)
    if not ref_name:
        return None
    try:
        df = source.get_index_pe_history(ref_name)
        if df is not None and not df.empty:
            return _finite_float(df["pe_ttm"].iloc[-1])
    except Exception:
        pass
    return None


if __name__ == "__main__":
    export_all()
