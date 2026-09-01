"""
一键更新脚本：更新数据 -> 重算PE -> 导出静态数据 -> 推送到GitHub(触发Netlify部署)

用法:
    python scripts/update_all.py                # 完整更新(慢,约1-3小时,首次或需全量)
    python scripts/update_all.py --export-only  # 只重新导出+推送(数据已最新时用,约1分钟)
    python scripts/update_all.py --no-push      # 更新但不推送
"""
import sys
import time
import argparse
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from cache.db import CacheDB
from data_sources import get_source
from etl.pipeline import PEETL
from config import INDEX_CONFIG

REFRESH_FIN_DAYS = 30  # 最近财报发布超过N天的股票才重新拉取财报


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def update_quotes(cache, source, codes):
    """增量更新行情（merge 新日期）"""
    ok, fail = 0, 0
    for i, code in enumerate(codes):
        try:
            df = source.get_daily_quotes(code)
            if not df.empty:
                cache.upsert_quotes(code, df)
                ok += 1
        except Exception:
            fail += 1
        if (i + 1) % 100 == 0:
            log(f"行情更新 [{i+1}/{len(codes)}] 成功{ok} 失败{fail}")
        time.sleep(0.05)
    log(f"行情更新完成: 成功{ok} 失败{fail}")


def update_financials(cache, source, codes):
    """增量更新财报 + 重建季度利润"""
    today = pd.Timestamp.today()
    updated = 0
    for i, code in enumerate(codes):
        try:
            fin = cache.load_financials(code)
            needs = fin.empty
            if not needs and "publish_date" in fin.columns:
                latest_pub = pd.to_datetime(fin["publish_date"]).max()
                needs = (today - latest_pub).days > REFRESH_FIN_DAYS
            if needs:
                f = source.get_financial_reports(code)
                if not f.empty:
                    cache.save_financials(code, f)
                    etl._precompute_quarterly_profits([{"code": code}])
                    updated += 1
        except Exception:
            pass
        if (i + 1) % 200 == 0:
            log(f"财报检查 [{i+1}/{len(codes)}] 更新{updated}")
        time.sleep(0.03)
    log(f"财报更新完成: 新增/更新 {updated} 只")


def recompute_all(etl):
    """重算所有板块 PE"""
    codes = []
    for name, cfg in INDEX_CONFIG.items():
        codes.append((cfg["code"], name))
    try:
        ref = etl.source.get_industry_reference()
        for _, r in ref.iterrows():
            codes.append((r["code"], r["name"]))
    except Exception:
        pass

    for i, (code, name) in enumerate(codes):
        log(f"重算 [{i+1}/{len(codes)}] {name} ...")
        try:
            pe_df = etl.run_index(code, name)
            log(f"  -> {len(pe_df)}条, 最新PE={pe_df.iloc[-1]['pe_ratio']:.2f}" if not pe_df.empty else "  -> 无数据")
        except Exception as e:
            log(f"  -> 失败: {str(e)[:60]}")
    log(f"重算完成: {len(codes)} 个板块")


def export_static():
    from scripts.export_static_data import export_all
    export_all()


def git_push():
    repo = Path(__file__).resolve().parent.parent
    ssh_cmd = "ssh -i ~/.ssh/id_github"
    env = {**dict(__import__("os").environ), "GIT_SSH_COMMAND": ssh_cmd}
    try:
        subprocess.run(["git", "add", "static/data"], cwd=repo, check=True, env=env)
        subprocess.run(
            ["git", "commit", "-m", "update static data"], cwd=repo, check=True, env=env,
            capture_output=True,
        )
        subprocess.run(["git", "push", "origin", "main"], cwd=repo, check=True, env=env,
                       capture_output=True)
        log("✅ 已推送到 GitHub，Netlify 将自动重新部署")
    except subprocess.CalledProcessError as e:
        log(f"⚠️ 推送失败（可能无新数据或网络问题）: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-only", action="store_true", help="只导出+推送")
    parser.add_argument("--no-push", action="store_true", help="不推送")
    args = parser.parse_args()

    cache = CacheDB()
    source = get_source()
    global etl
    etl = PEETL(cache)

    if args.export_only:
        log("导出静态数据...")
        export_static()
        if not args.no_push:
            git_push()
        log("完成")
        return

    # 获取全部股票代码（db 中已有的）
    with cache._conn() as conn:
        codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_quotes").fetchall()]
    log(f"待更新股票: {len(codes)} 只")

    log("=== 1/4 更新行情 ===")
    update_quotes(cache, source, codes)

    log("=== 2/4 更新财报 ===")
    update_financials(cache, source, codes)

    log("=== 3/4 重算PE ===")
    recompute_all(etl)

    log("=== 4/4 导出静态数据 ===")
    export_static()

    if not args.no_push:
        git_push()
    log("✅ 一键更新完成")


if __name__ == "__main__":
    main()
