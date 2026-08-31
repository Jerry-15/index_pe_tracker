"""
数据准确性校验脚本 (对应方案 Phase 6.1)

对比两个维度：
1. 自下而上 PE vs 参考PE（乐咕乐股月度），验证趋势一致性
2. TTM 双方法交叉验证：季度拆分法 vs 标准公式法
   TTM = 最近一期累计净利 + (上年全年净利 - 上年同期累计净利)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from cache.db import CacheDB
from etl.pipeline import PEETL


def _parse_period(rd: pd.Timestamp) -> str:
    m, d = rd.month, rd.day
    if m == 3 and d == 31: return "Q1"
    if m == 6 and d == 30: return "H1"
    if m == 9 and d == 30: return "Q3"
    if m == 12 and d == 31: return "Annual"
    return "Unknown"


def ttm_by_standard_formula(fin_df: pd.DataFrame, target: pd.Timestamp):
    """
    标准公式法：
    TTM = 最近一期累计净利 + (上年全年净利 - 上年同期累计净利)
    """
    fin = fin_df.copy()
    fin["period"] = fin["report_date"].apply(_parse_period)
    fin = fin[(fin["publish_date"] <= target)].sort_values("report_date")

    if fin.empty:
        return None

    latest = fin.iloc[-1]
    latest_rd, latest_profit, latest_period = latest["report_date"], latest["net_profit_attr"], latest["period"]

    # 上年全年净利（报告期 12-31 且早于最近期）
    annuals = fin[(fin["period"] == "Annual") & (fin["report_date"] < latest_rd)]
    if annuals.empty:
        return None
    prior_annual = annuals.iloc[-1]["net_profit_attr"]

    # 上年同期累计净利（同 period 且上年）
    prev_year = latest_rd.year - 1
    if latest_period == "Q1":
        same_period = fin[(fin["period"] == "Q1") & (fin["report_date"].dt.year == prev_year)]
    elif latest_period == "H1":
        same_period = fin[(fin["period"] == "H1") & (fin["report_date"].dt.year == prev_year)]
    elif latest_period == "Q3":
        same_period = fin[(fin["period"] == "Q3") & (fin["report_date"].dt.year == prev_year)]
    else:
        same_period = annuals[annuals["report_date"].dt.year == prev_year]
    if same_period.empty:
        return None
    prior_same = same_period.iloc[-1]["net_profit_attr"]

    ttm = latest_profit + (prior_annual - prior_same)
    return ttm if ttm > 0 else None


def cross_check_ttm(stocks_sample, target: pd.Timestamp):
    """交叉验证 TTM 两种算法"""
    cache = CacheDB()
    etl = PEETL(cache)
    print(f"\n=== TTM 双方法交叉验证 (目标日期 {target.date()}) ===")
    print(f"{'代码':<8}{'季度拆分法':>14}{'标准公式法':>14}{'偏差':>10}")
    for code in stocks_sample:
        fin = cache.load_financials(code)          # 原始累计财报
        qdf = cache.load_quarterly_profits(code)   # 已拆分的单季利润
        if fin.empty or qdf.empty:
            continue
        method_a = etl._compute_ttm_from_quarters(qdf, target)  # 季度拆分法
        method_b = ttm_by_standard_formula(fin, target)          # 标准公式法
        if method_a is None or method_b is None:
            print(f"{code:<8}{'N/A':>14}{'N/A':>14}")
            continue
        diff = (method_a - method_b) / method_b * 100
        print(f"{code:<8}{method_a/1e8:>12.1f}亿{method_b/1e8:>12.1f}亿{diff:>+9.2f}%")
    print("（偏差 ≈ 0 说明两种算法等价，计算正确）")


def validate_against_reference(index_code, index_name):
    """对比自下而上 PE 与乐咕乐股参考PE"""
    cache = CacheDB()
    etl = PEETL(cache)

    our_df = cache.load_pe_daily(index_code)
    if our_df.empty:
        print(f"{index_name}: 无自下而上数据")
        return

    ref_df = etl.source.get_index_pe_history(index_name)
    if ref_df.empty:
        print(f"{index_name}: 无参考PE数据")
        return

    our_df["trade_date"] = pd.to_datetime(our_df["trade_date"])

    print(f"\n=== {index_name}: 自下而上 vs 参考PE(乐咕乐股月度) ===")
    print(f"{'日期':<12}{'自下而上':>10}{'参考PE':>10}{'偏差%':>10}")
    pairs = []
    for _, ref in ref_df.iterrows():
        # 找参考日期当日或之前最近的自下而上值
        mask = our_df["trade_date"] <= ref["date"]
        if not mask.any():
            continue
        row = our_df[mask].sort_values("trade_date").iloc[-1]
        diff = (row["pe_ratio"] - ref["pe_ttm"]) / ref["pe_ttm"] * 100
        pairs.append((ref["date"], row["pe_ratio"], ref["pe_ttm"], diff))
        print(f"{ref['date'].strftime('%Y-%m-%d'):<12}{row['pe_ratio']:>10.2f}{ref['pe_ttm']:>10.2f}{diff:>+9.1f}%")

    if pairs:
        ours = [p[1] for p in pairs]
        refs = [p[2] for p in pairs]
        avg_diff = sum(abs(p[3]) for p in pairs) / len(pairs)
        # 皮尔逊相关系数（衡量趋势一致性）
        import numpy as np
        corr = np.corrcoef(ours, refs)[0, 1]
        # 近8年窗口（约2018年后）单独评估——默认展示区间
        recent_pairs = [p for p in pairs if p[0].year >= 2018]
        if recent_pairs:
            r_ours = [p[1] for p in recent_pairs]
            r_refs = [p[2] for p in recent_pairs]
            r_corr = np.corrcoef(r_ours, r_refs)[0, 1]
            r_avg_diff = sum(abs(p[3]) for p in recent_pairs) / len(recent_pairs)
        else:
            r_corr, r_avg_diff = None, None
        # 分位数位置对比（衡量估值区间判断一致性）
        ours_rank = pd.Series(ours).rank(pct=True).iloc[-1]
        refs_rank = pd.Series(refs).rank(pct=True).iloc[-1]
        print(f"\n平均绝对偏差(全期): {avg_diff:.1f}%")
        if r_corr is not None:
            print(f"平均绝对偏差(近8年): {r_avg_diff:.1f}%")
            print(f"趋势相关性(全期): {corr:.3f}  | 近8年: {r_corr:.3f}")
            print(f"结论: {'✓ 近8年趋势一致，可用于估值区间判断' if r_corr > 0.85 else '✗ 近8年趋势不一致，需排查'}")
        else:
            print(f"趋势相关性: {corr:.3f}")
            print(f"结论: {'✓ 趋势一致' if corr > 0.85 else '✗ 趋势不一致，需排查'}")
        print(f"当前PE分位: 自下而上={ours_rank*100:.0f}%, 参考={refs_rank*100:.0f}%")


if __name__ == "__main__":
    # TTM 算法交叉验证
    sample = ["600519", "000001", "601318", "600036", "601398"]
    cross_check_ttm(sample, pd.Timestamp("2026-08-28"))

    # 指数 PE 对照
    validate_against_reference("000300", "沪深300")
    validate_against_reference("000905", "中证500")
