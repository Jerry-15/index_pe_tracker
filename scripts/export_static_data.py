"""
导出静态数据（供 Netlify 静态图表版使用）

将 SQLite 中的 PE 数据导出为 JSON 文件到 static/data/ 目录。
由 GitHub Actions 定时运行，push 后触发 Netlify 重新部署。

用法: python scripts/export_static_data.py
"""
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
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

    list_path = OUT / "index_list.json"
    if list_path.exists():
        with open(list_path, "r", encoding="utf-8") as f:
            index_list = json.load(f)
    else:
        index_list = []
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

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
        rows = _to_rows(pe_df, generated_at=generated_at)
        if not rows:
            continue
        _save(code, rows)
        entry = {
            "code": code, "name": name, "type": "index",
            "pe_ref": _get_reference_pe(source, name),
        }
        _add_quality_catalog_metadata(entry, pe_df.iloc[-1], generated_at)
        index_list = [item for item in index_list if item.get("code") != code]
        index_list.append(entry)

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
        rows = _to_rows(pe_df, generated_at=generated_at)
        if not rows:
            continue
        _save(code, rows)
        entry = {
            "code": code, "name": name, "type": "industry",
            "pe_ref": float(r["pe_ttm"]) if pd.notna(r["pe_ttm"]) else None,
        }
        _add_quality_catalog_metadata(entry, pe_df.iloc[-1], generated_at)
        index_list = [item for item in index_list if item.get("code") != code]
        index_list.append(entry)

    # 保存指数列表
    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(index_list, f, ensure_ascii=False, indent=1)

    # 导出中概互联网数据
    _export_china_intl(cache, source, generated_at=generated_at)
    _save_manifest(generated_at)

    print(f"导出完成: {len(index_list)} 个指数/行业 -> {OUT}")


def _export_china_intl(cache: CacheDB, source, generated_at=None):
    """导出 H30533 指数 PE 数据及其来源、覆盖率和诊断信息。"""
    snapshot = cache.load_h30533_snapshot("H30533")
    pe_df = snapshot["daily"]
    constituents = snapshot["constituents"]
    securities = snapshot["securities"]
    unavailable_reason = "H30533 snapshot is unavailable; no H30533 daily valuation rows" if pe_df.empty else None
    # 合并统计指标
    try:
        stats_df = cache.load_us_pe_stats()
        if not stats_df.empty:
            pe_df = pe_df.merge(
                stats_df[["trade_date", "pe_mean", "pe_std", "pe_high_line", "pe_low_line", "pe_percentile"]],
                on="trade_date", how="left",
            )
    except Exception:
        pass
    latest_row = pe_df.iloc[-1] if not pe_df.empty else pd.Series(dtype=object)
    provenance_row = latest_row
    if provenance_row.empty and not constituents.empty:
        provenance_row = constituents.iloc[0]
    if unavailable_reason and not pe_df.empty:
        unavailable_reason = _text(latest_row.get("validity_reason")) or unavailable_reason
    rows = _to_rows(pe_df, h30533=True, generated_at=generated_at) if not pe_df.empty else []
    code = "H30533"
    _save(code, rows)

    financial_as_of = _text(latest_row.get("financial_as_of"))
    if financial_as_of is None and not securities.empty and "financial_as_of" in securities:
        dates = securities["financial_as_of"].dropna().astype(str)
        financial_as_of = dates.max() if not dates.empty else None

    # 更新 index_list
    try:
        list_path = OUT / "index_list.json"
        if list_path.exists():
            with open(list_path, "r", encoding="utf-8") as f:
                idx_list = json.load(f)
        else:
            idx_list = []
        entry = {
            "code": "H30533", "name": "中证海外中国互联网50（H30533）", "type": "h30533",
            "pe_ref": None, "data_file": f"{code}.json",
            "last_updated": _text(latest_row.get("trade_date")) if not pe_df.empty else None,
            "source_type": _text(provenance_row.get("source_type")),
            "source_date": _text(provenance_row.get("source_date")) or _text(provenance_row.get("as_of")),
            "constituent_as_of": _text(latest_row.get("constituent_as_of")) if not pe_df.empty else None,
            "constituent_basis": _text(latest_row.get("constituent_basis")) if not pe_df.empty else None,
            "financial_as_of": financial_as_of,
            "coverage": _number(latest_row.get("weight_coverage")) if not pe_df.empty else None,
             "quality_level": _text(latest_row.get("quality_level")) or ("C" if unavailable_reason else None),
             "data_source": _text(provenance_row.get("data_source")) or _text(provenance_row.get("source_type")),
            "source_url": _text(provenance_row.get("source_url")),
            "fetched_at": _text(provenance_row.get("fetched_at")),
            "response_hash": _text(provenance_row.get("response_hash")),
            "current_constituents_backfill": bool(provenance_row.get("current_constituents_backfill", False)),
            "current_constituents_caveat": _text(latest_row.get("current_constituents_caveat")) if not pe_df.empty else None,
            "counts": {
                "constituents": len(constituents),
                "valid": _number(latest_row.get("valid_count", latest_row.get("stock_count"))) or 0,
                "negative_profit": _number(latest_row.get("negative_profit_count")) or 0,
                "missing": _number(latest_row.get("missing_count")) or 0,
            },
            "diagnostics": {
                key: _number(latest_row.get(key)) or 0
                for key in ("financial_stale_count", "derived_market_cap_count",
                            "quote_disagreement_count", "excluded_count", "invalid_count")
            },
            "validity_reason": unavailable_reason or _text(latest_row.get("validity_reason")),
            "stock_diagnostics": _records(securities),
             "unavailable": unavailable_reason is not None,
        }
        existing_index = next(
            (n for n, item in enumerate(idx_list)
             if item.get("code") in ("H30533", "CHINA_INTL")), None
        )
        if existing_index is None:
            idx_list.append(entry)
            existing_index = len(idx_list) - 1
        else:
            preserved = dict(idx_list[existing_index])
            preserved.update(entry)
            target = preserved
            idx_list[existing_index] = target
            idx_list = [
                item for n, item in enumerate(idx_list)
                if item is target or item.get("code") not in ("H30533", "CHINA_INTL")
            ]
            existing_index = next(n for n, item in enumerate(idx_list) if item is target)
        if generated_at:
            idx_list[existing_index]["generated_at"] = generated_at
        with open(list_path, "w", encoding="utf-8") as f:
            json.dump(idx_list, f, ensure_ascii=False, indent=1)
    except Exception:
        raise


def _to_rows(pe_df: pd.DataFrame, h30533: bool = False, generated_at=None) -> list:
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
        pe = _number(r.get("pe_ratio"))
        row = [r["trade_date"], round(pe, 3) if pe is not None else None,
               mean, high, low, pct, mv, profit]
        if h30533:
            row.extend([_number(r.get("stock_count", r.get("valid_count"))),
                        _number(r.get("valid_count", r.get("stock_count"))),
                        _number(r.get("weight_coverage")),
                        _number(r.get("negative_profit_count")),
                        _number(r.get("missing_count")),
                        _text(r.get("source_type")), _text(r.get("source_date")),
                         _text(r.get("constituent_as_of")), _text(r.get("constituent_basis")),
                         _text(r.get("validity_reason")), _text(r.get("financial_as_of")),
                         _text(r.get("quality_level")), _text(r.get("source_type")),
                         _text(r.get("generated_at")) or generated_at,
                         _number(r.get("weight_coverage")),
                         _text(r.get("financial_as_of")), _text(r.get("validity_reason")),
                          True if bool(r.get("current_constituents_backfill", False)) else None])
        else:
            row.extend([_number(r.get("valid_stock_count")),
                        _number(r.get("profitable_stock_count")),
                        _number(r.get("coverage_ratio")), _text(r.get("validity_reason")),
                        _text(r.get("quality_level")), _text(r.get("data_source")),
                        _text(r.get("generated_at")) or generated_at,
                        _number(r.get("coverage_ratio")), _text(r.get("financial_as_of")),
                        _text(r.get("validity_reason"))])
        rows.append(row)
    return rows


def _number(value):
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _text(value):
    return None if value is None or pd.isna(value) else str(value)


def _records(frame: pd.DataFrame) -> list:
    if frame.empty:
        return []
    return frame.where(pd.notna(frame), None).to_dict("records")


def _save(code: str, rows: list):
    with open(OUT / f"{code}.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, allow_nan=False)


def _save_manifest(generated_at):
    artifacts = sorted(
        path.name for path in OUT.glob("*.json") if path.name != "manifest.json"
    )
    date_ranges = {}
    item_count = 0
    for artifact in artifacts:
        with open(OUT / artifact, "r", encoding="utf-8") as source:
            payload = json.load(source)
        if artifact == "index_list.json":
            item_count += len(payload)
            continue
        item_count += len(payload)
        dates = [row[0] for row in payload if isinstance(row, list) and row and row[0]]
        date_ranges[artifact] = {"min": min(dates) if dates else None,
                                 "max": max(dates) if dates else None}
    with open(OUT / "manifest.json", "w", encoding="utf-8") as f:
        json.dump({"schema_version": "quality-export-v1", "source_revision": _source_revision(),
                   "generated_at": generated_at, "index_list": "index_list.json",
                   "artifacts": artifacts, "item_count": item_count,
                   "date_ranges": date_ranges}, f,
                  ensure_ascii=False, indent=1)


def _source_revision():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=BASE, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _add_quality_catalog_metadata(entry, latest, generated_at):
    reason = _text(latest.get("validity_reason"))
    quality = _text(latest.get("quality_level"))
    if quality is None:
        quality = "C"
        reason = reason or "quality metadata unavailable from pipeline/cache"
    entry.update({
        "quality_level": quality,
        "source": _text(latest.get("data_source")),
        "data_source": _text(latest.get("data_source")),
        "generated_at": _text(latest.get("generated_at")) or generated_at,
        "coverage": _number(latest.get("coverage_ratio")),
        "financial_as_of": _text(latest.get("financial_as_of")),
        "validity": reason,
    })


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
