"""
批量回填申万一级行业 PE 数据（可断点续跑）

用法:
    python scripts/backfill_industries.py              # 全部行业
    python scripts/backfill_industries.py --max-size 200   # 只处理成分股<=200的行业
    python scripts/backfill_industries.py --start 食品饮料 # 从指定行业开始
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from data_sources import get_source
from cache.db import CacheDB
from etl.pipeline import PEETL


def fetch_missing(code, cache, source, progress=None):
    """串行下载单只股票缺失数据（行情 + 财务）"""
    try:
        if cache.load_quotes(code).empty:
            cache.save_quotes(code, source.get_daily_quotes(code))
        if cache.load_financials(code).empty:
            f = source.get_financial_reports(code)
            if not f.empty:
                cache.save_financials(code, f)
        return True
    except Exception:
        return False


def process_industry(code, name, cache, source, etl):
    """处理单个行业：成分股 -> 下载 -> 计算PE -> 保存"""
    stocks_df = cache.load_constituents(code)
    if stocks_df.empty:
        stocks_df = source.get_constituents(code)
        if stocks_df.empty:
            return "无成分股"
        cache.save_constituents(code, stocks_df)

    codes = stocks_df["code"].tolist()

    # 已有PE数据则跳过（避免重算）
    existing = cache.load_pe_daily(code)
    if not existing.empty and existing["trade_date"].max() >= "2026-01-01":
        return f"已有数据({len(existing)}条)，跳过"

    # 下载缺失数据（串行，安全）
    need = [c for c in codes if cache.load_quotes(c).empty or cache.load_financials(c).empty]
    if need:
        ok = 0
        for i, c in enumerate(need):
            if fetch_missing(c, cache, source):
                ok += 1
            if (i + 1) % 20 == 0:
                print(f"    下载 [{i+1}/{len(need)}]")
            time.sleep(0.05)
        print(f"    下载 {ok}/{len(need)} 只")

    # 计算并保存 PE + 统计
    pe_df = etl.run_index(code, name)
    if pe_df.empty:
        return "无有效PE"
    latest = pe_df.iloc[-1]
    return f"完成: {len(pe_df)}条, 最新PE={latest['pe_ratio']:.2f}"


def main():
    cache = CacheDB()
    source = get_source()
    etl = PEETL(cache)

    max_size = None
    start_name = None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--max-size" and i + 1 < len(args):
            max_size = int(args[i + 1])
        if a == "--start" and i + 1 < len(args):
            start_name = args[i + 1]

    ref = source.get_industry_reference()
    if ref.empty:
        print("无法获取行业列表")
        return

    ref = ref.sort_values("stock_count").reset_index(drop=True)
    if max_size:
        ref = ref[ref["stock_count"] <= max_size]
    if start_name:
        ref = ref[ref["name"] >= start_name]

    total = len(ref)
    print(f"待处理行业: {total} 个")

    for idx, row in ref.iterrows():
        code, name = row["code"], row["name"]
        n = int(row["stock_count"])
        print(f"[{idx+1}/{total}] {name}({code}) {n}只 ...")
        t0 = time.time()
        try:
            msg = process_industry(code, name, cache, source, etl)
        except Exception as e:
            msg = f"异常: {str(e)[:60]}"
        print(f"    {msg} 用时{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
