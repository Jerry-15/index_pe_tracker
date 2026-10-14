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
    """导出 H30533 快照及其来源、覆盖率和诊断信息。"""
    OUT.mkdir(parents=True, exist_ok=True)
    snapshot = cache.load_h30533_snapshot("H30533")
    pe_df = snapshot["daily"]
    constituents = snapshot["constituents"]
    securities = snapshot["securities"]
    if pe_df.empty or constituents.empty:
        return
    latest_row = pe_df.iloc[-1]
    rows = _to_rows(pe_df, h30533=True)
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
        diagnostics = _records(securities)
        entry = {
            "code": "H30533",
            "name": "中证海外中国互联网50（H30533）",
            "type": "h30533",
            "pe_ref": None,
            "data_file": f"{code}.json",
            "last_updated": str(latest_row.get("trade_date", "")),
            "source_type": _text(latest_row.get("source_type")),
            "source_date": _text(latest_row.get("source_date")),
            "constituent_as_of": _text(latest_row.get("constituent_as_of")),
            "constituent_basis": _text(latest_row.get("constituent_basis")),
            "coverage": _number(latest_row.get("weight_coverage")),
            "counts": {
                "constituents": len(constituents),
                "valid": _number(latest_row.get("valid_count", latest_row.get("stock_count"))) or 0,
                "negative_profit": _number(latest_row.get("negative_profit_count")) or 0,
                "missing": _number(latest_row.get("missing_count")) or 0,
                "invalid": _number(latest_row.get("invalid_count")) or 0,
            },
            "validity_reason": _text(latest_row.get("validity_reason")),
            "stock_diagnostics": diagnostics,
            "unavailable": _number(latest_row.get("pe_ratio")) is None,
        }
        idx_list = [i for i in idx_list if i.get("code") not in ("H30533", "CHINA_INTL")]
        existing = next((i for i in idx_list if i.get("code") == code), None)
        if existing is None:
            idx_list.append(entry)
        else:
            existing.update(entry)
        with open(list_path, "w", encoding="utf-8") as f:
            json.dump(idx_list, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _to_rows(pe_df: pd.DataFrame, h30533: bool = False) -> list:
    """转换为紧凑行数组 [date, pe, mean, high, low, pct, mv, profit]"""
    df = pe_df.sort_values("trade_date").tail(MAX_DAYS)
    rows = []
    for _, r in df.iterrows():
        mean = _number(r.get("pe_mean"))
        high = _number(r.get("pe_high_line"))
        low = _number(r.get("pe_low_line"))
        pct = _number(r.get("pe_percentile"))
        mv = _number(r.get("total_market_cap"))
        profit = _number(r.get("total_profit_ttm"))
        pe = _number(r.get("pe_ratio"))
        row = [r["trade_date"], round(pe, 3) if pe is not None else None,
               mean, high, low, pct, mv, profit]
        if h30533:
            row.extend([
                _number(r.get("stock_count", r.get("valid_count"))),
                _number(r.get("valid_count", r.get("stock_count"))),
                _number(r.get("weight_coverage")),
                _number(r.get("negative_profit_count")),
                _number(r.get("missing_count")),
                _number(r.get("invalid_count")),
                _text(r.get("source_type")), _text(r.get("source_date")),
                _text(r.get("constituent_as_of")), _text(r.get("constituent_basis")),
                _text(r.get("validity_reason")),
            ])
        else:
            row.extend([_number(r.get("valid_stock_count")),
                        _number(r.get("profitable_stock_count")),
                        _number(r.get("coverage_ratio")),
                        _text(r.get("validity_reason"))])
        rows.append(row)
    return rows


def _number(value):
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _text(value):
    return None if pd.isna(value) else str(value)


def _records(frame: pd.DataFrame) -> list:
    if frame.empty:
        return []
    records = frame.where(pd.notna(frame), None).to_dict("records")
    for record in records:
        for key, value in record.items():
            if isinstance(value, (int, float)) and pd.isna(value):
                record[key] = None
    return records


def _save(code: str, rows: list):
    with open(OUT / f"{code}.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, allow_nan=False)


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
