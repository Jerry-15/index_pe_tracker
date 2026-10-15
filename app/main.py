"""板块指数估值追踪器 - Streamlit 主应用"""
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import INDEX_CONFIG, INDEX_REFERENCE, CHART_COLORS, CHINA_INTL_CONFIG
from cache.db import CacheDB
from etl.pipeline import PEETL
from etl.pipeline_us import USETL


st.set_page_config(
    page_title="板块指数估值追踪器",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_etl():
    cache = CacheDB()
    return PEETL(cache)


@st.cache_data(ttl=3600)
def get_constituents(index_code):
    cache = CacheDB()
    return cache.load_constituents(index_code)


def build_pe_chart(pe_df, index_name, reference_df=None, industry_pe=None):
    """构建 PE 主图：PE 折线 + 均值线 + 高估/低估线 + 参考PE"""
    if pe_df is None or pe_df.empty:
        st.warning(f"暂无 {index_name} 的 PE 数据，请先运行数据更新。")
        return None

    fig = go.Figure()

    # 正常估值区间色带（均值±1σ 之间填充）
    if "pe_high_line" in pe_df.columns and pe_df["pe_high_line"].notna().any():
        band_x = pe_df["trade_date"].tolist()
        fig.add_trace(go.Scatter(
            x=band_x, y=pe_df["pe_high_line"].tolist(),
            mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=band_x, y=pe_df["pe_low_line"].tolist(),
            mode="lines", line=dict(width=0), fill="tonexty",
            fillcolor="rgba(128,128,128,0.15)", showlegend=False, hoverinfo="skip",
        ))

    fig.add_trace(go.Scatter(
        x=pe_df["trade_date"], y=pe_df["pe_ratio"],
        name="指数PE(TTM)", line=dict(color=CHART_COLORS["pe"], width=2),
        hovertemplate="%(x|%Y-%m-%d)<br>PE: %(y:.2f)<extra></extra>",
    ))

    if "pe_mean" in pe_df.columns and pe_df["pe_mean"].notna().any():
        fig.add_trace(go.Scatter(
            x=pe_df["trade_date"], y=pe_df["pe_mean"],
            name="滚动均值", line=dict(color=CHART_COLORS["mean"], width=1.5, dash="dash"),
        ))
        fig.add_trace(go.Scatter(
            x=pe_df["trade_date"], y=pe_df["pe_high_line"],
            name="高估线(均值+1σ)", line=dict(color=CHART_COLORS["high_line"], width=1.5, dash="dot"),
        ))
        fig.add_trace(go.Scatter(
            x=pe_df["trade_date"], y=pe_df["pe_low_line"],
            name="低估线(均值-1σ)", line=dict(color=CHART_COLORS["low_line"], width=1.5, dash="dot"),
        ))

    # 参考PE叠加层（官方/理杏仁 月度数据，用于验证）
    if reference_df is not None and not reference_df.empty:
        fig.add_trace(go.Scatter(
            x=reference_df["date"], y=reference_df["pe_ttm"],
            name="参考PE(月度)", line=dict(color=CHART_COLORS["reference"], width=1.5, dash="dash"),
            opacity=0.8,
        ))
    # 申万行业官方PE当前值（水平虚线）
    if industry_pe is not None and industry_pe > 0:
        fig.add_hline(y=industry_pe, line_dash="dash", line_color=CHART_COLORS["reference"],
                      annotation_text=f"申万官方PE {industry_pe:.1f}", annotation_position="top left")

    # 标记当前值
    latest = pe_df.iloc[-1]
    fig.add_annotation(
        x=latest["trade_date"], y=latest["pe_ratio"],
        text=f"当前 PE: {latest['pe_ratio']:.2f}",
        showarrow=True, arrowhead=2, arrowsize=1, arrowwidth=2,
        ax=40, ay=-40,
        bgcolor="rgba(255,255,255,0.8)", bordercolor="#333", borderwidth=1,
    )

    fig.update_layout(
        title=f"<b>{index_name}</b> — 指数 PE(TTM) 历史走势",
        xaxis_title="日期", yaxis_title="市盈率 (PE)",
        height=500, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        template="plotly_white",
    )
    return fig


def build_percentile_chart(pe_df, index_name):
    """PE 百分位图"""
    if pe_df is None or pe_df.empty or "pe_percentile" not in pe_df.columns:
        return None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pe_df["trade_date"], y=pe_df["pe_percentile"],
        name="PE百分位", line=dict(color=CHART_COLORS["percentile"], width=2),
    ))
    fig.add_hline(y=80, line_dash="dash", line_color="red", annotation_text="高估区(80%)")
    fig.add_hline(y=20, line_dash="dash", line_color="green", annotation_text="低估区(20%)")
    fig.add_hline(y=50, line_dash="solid", line_color="gray", opacity=0.5)

    fig.update_layout(
        title=f"<b>{index_name}</b> — PE 历史百分位",
        xaxis_title="日期", yaxis_title="百分位 (%)",
        height=300, hovermode="x unified",
        template="plotly_white",
    )
    return fig


def build_detail_charts(pe_df, index_name):
    """市值和利润时序图"""
    if pe_df is None or pe_df.empty:
        return None, None

    fig_mv = go.Figure(go.Scatter(
        x=pe_df["trade_date"], y=pe_df["total_market_cap"] / 1e8,
        name="总市值(亿元)", line=dict(color="#1f77b4", width=1.5),
    ))
    fig_mv.update_layout(title=f"{index_name} — 成分股总市值", height=280,
                         template="plotly_white", hovermode="x unified")

    fig_profit = go.Figure(go.Scatter(
        x=pe_df["trade_date"], y=pe_df["total_profit_ttm"] / 1e8,
        name="总利润TTM(亿元)", line=dict(color="#ff7f0e", width=1.5),
    ))
    fig_profit.update_layout(title=f"{index_name} — 成分股净利润TTM", height=280,
                             template="plotly_white", hovermode="x unified")

    return fig_mv, fig_profit


def run_update(index_code, index_name, progress_container):
    """执行数据更新"""
    etl = get_etl()
    progress_bar = progress_container.progress(0)
    status_text = progress_container.text(f"开始更新 {index_name}...")

    def progress_cb(msg):
        status_text.text(msg)
        if "下载" in msg or "处理" in msg:
            try:
                num = int(msg.split("[")[1].split("]")[0])
                total = int(msg.split("/")[1].split("]")[0])
                progress_bar.progress(min(num / total, 1.0))
            except (IndexError, ValueError):
                pass

    try:
        pe_df = etl.run_index(index_code, index_name, progress_cb=progress_cb)
        progress_bar.progress(1.0)
        status_text.text(f"✅ {index_name} 更新完成！")
        time.sleep(0.5)
        st.rerun()
    except Exception as e:
        progress_bar.progress(1.0)
        status_text.text(f"❌ 更新失败: {e}")
        st.error(f"更新失败: {e}")


def run_us_update(us_etl, progress_container):
    """执行中概互联网数据更新"""
    progress_bar = progress_container.progress(0)
    status_text = progress_container.text("开始更新 H30533...")

    def progress_cb(msg):
        status_text.text(msg)
        try:
            num = int(msg.split("[")[1].split("]")[0])
            total = int(msg.split("/")[1].split("]")[0])
            progress_bar.progress(min(num / total, 1.0))
        except (IndexError, ValueError):
            pass

    try:
        historical = us_etl.run_historical(progress_cb=progress_cb)
        if historical is None or historical.empty:
            raise RuntimeError("历史回溯未产生可持久化结果")
        progress_bar.progress(1.0)
        status_text.text("✅ H30533 更新完成！")
        time.sleep(0.5)
        st.rerun()
    except Exception as e:
        progress_bar.progress(1.0)
        status_text.text(f"❌ 更新失败: {e}")
        st.error(f"更新失败: {e}")


def _render_china_intl(us_etl):
    """渲染 H30533 估值页面。"""
    st.markdown("### 🌏 中证海外中国互联网50（H30533）")
    snapshot = us_etl.cache.load_h30533_snapshot(CHINA_INTL_CONFIG["code"])
    pe_df = snapshot["daily"]
    stock_pe = snapshot["securities"]

    if pe_df is not None and not pe_df.empty:
        latest = pe_df.iloc[-1]
        col1, col2, col3, col4 = st.columns(4)
        pe = latest.get("pe_ratio")
        col1.metric("当前 PE(TTM)", f"{pe:.2f}" if pd.notna(pe) else "不可用")
        if "pe_mean" in pe_df.columns and pd.notna(latest.get("pe_mean")):
            col2.metric("滚动均值", f"{latest['pe_mean']:.2f}")
        if "pe_high_line" in pe_df.columns and pd.notna(latest.get("pe_high_line")):
            col3.metric("高估线", f"{latest['pe_high_line']:.2f}")
        if "pe_low_line" in pe_df.columns and pd.notna(latest.get("pe_low_line")):
            col4.metric("低估线", f"{latest['pe_low_line']:.2f}")

        st.divider()

        st.caption(
            f"来源：{latest.get('source_type') or '未知'} | 来源日期：{latest.get('source_date') or '--'} | "
            f"成分日期：{latest.get('constituent_as_of') or '--'} | "
            f"权重覆盖：{latest.get('weight_coverage') if pd.notna(latest.get('weight_coverage')) else '--'} | "
            f"财务截至：{stock_pe['financial_as_of'].max() if 'financial_as_of' in stock_pe and stock_pe['financial_as_of'].notna().any() else '--'}"
        )
        generated_at = latest.get("updated_at")
        generated_label = (
            datetime.fromtimestamp(float(generated_at)).isoformat(timespec="seconds")
            if pd.notna(generated_at) else "--"
        )
        st.caption(f"成分数：{len(snapshot['constituents'])} | 生成时间：{generated_label}")
        if pd.isna(pe):
            st.warning(f"当前 PE 不可用：{latest.get('validity_reason') or '有效覆盖不足'}")
        tab1, tab2, tab3 = st.tabs(["PE 走势", "成分股诊断", "说明"])

        with tab1:
            if not pe_df.empty and "pe_ratio" in pe_df.columns:
                fig = build_pe_chart(pe_df, "中证海外中国互联网50（H30533）")
                st.plotly_chart(fig, use_container_width=True)

        with tab2:
            if stock_pe is not None and not stock_pe.empty:
                show_cols = ["security_code", "name", "price", "pe", "validity_reason", "financial_as_of"]
                avail = [c for c in show_cols if c in stock_pe.columns]
                pe_show = stock_pe[avail].copy()
                pe_show.columns = ["代码", "名称", "最新价", "PE(TTM)", "有效性原因", "财务日期"][:len(avail)]
                st.dataframe(pe_show, use_container_width=True, height=500)
            else:
                st.info("暂无个股 PE 数据，请更新")

        with tab3:
            st.markdown("""
            - **成分与权重**：来自 H30533 指数成分快照，页面同时展示来源类型和成分生效日期。
            - **计算口径**：按成分权重汇总经核验的市值与 TTM 利润，形成加权盈利收益率。
            - **数据覆盖**：当前 PE 仅在有效输入和最低权重覆盖条件满足时显示，否则保留不可用状态及原因。
            - **财务截至**：成分证券财务输入的最新报告日期会随快照展示。
            - **历史说明**：历史序列使用当前成分回溯，不代表历史真实调仓，需谨慎解读。
            """)
    else:
        st.info("暂无 H30533 数据，请点击左侧「更新数据」开始拉取。")
        st.markdown("**首次运行说明：** H30533 成分快照、证券财务截至日期、来源、覆盖率和有效性原因会随数据保存。")

def main():
    etl = get_etl()
    us_etl = USETL()

    # Sidebar: 指数选择 & 操作
    with st.sidebar:
        st.header("⚙️ 设置")
        index_type = st.radio("指数类型", ["宽基指数", "申万行业", "中证海外中国互联网50（H30533）"])

        # 记录行业官方参考PE（当前快照）
        industry_pe_ref = None
        index_code = None
        index_name = None

        if index_type == "宽基指数":
            index_options = list(INDEX_CONFIG.keys())
            selected_index = st.selectbox("选择板块/指数", index_options)
            index_code = INDEX_CONFIG[selected_index]["code"]
            index_name = INDEX_CONFIG[selected_index]["name"]
        elif index_type == "申万行业":
            # 动态加载申万一级行业
            industry_ref = etl.source.get_industry_reference()
            if industry_ref.empty:
                st.error("无法获取申万行业列表，请检查网络")
                industry_ref = None
                industry_names = ["（加载失败）"]
            else:
                industry_names = industry_ref["name"].tolist()
            selected_industry = st.selectbox("选择行业", industry_names)
            if industry_ref is not None and not industry_ref.empty:
                row = industry_ref[industry_ref["name"] == selected_industry]
                if not row.empty:
                    row = row.iloc[0]
                    index_code = row["code"]
                    index_name = row["name"]
                    industry_pe_ref = row["pe_ttm"] if pd.notna(row["pe_ttm"]) else None
                else:
                    index_code, index_name = "801010", "农林牧渔"
            else:
                index_code, index_name = "801010", "农林牧渔"
        else:
            index_code = CHINA_INTL_CONFIG["code"]
            index_name = CHINA_INTL_CONFIG["name"]

        st.divider()

        if index_type == "中证海外中国互联网50（H30533）":
            last_updated = us_etl.cache.get_us_last_updated()
        else:
            last_updated = etl.cache.get_last_updated(index_code)

        if last_updated:
            st.success(f"上次更新: {last_updated}")
        else:
            st.info("尚未更新")

        if st.button("🔄 更新数据", use_container_width=True, type="primary"):
            with st.sidebar:
                progress_container = st.container()
            if index_type == "中证海外中国互联网50（H30533）":
                run_us_update(us_etl, progress_container)
            else:
                run_update(index_code, index_name, progress_container)

        st.divider()
        if index_type != "中证海外中国互联网50（H30533）" and st.button("🗑️ 清除缓存", use_container_width=True):
            etl.cache.clear_index(index_code)
            st.success("缓存已清除，请重新更新")
            st.rerun()

    # Main content
    if index_type == "中证海外中国互联网50（H30533）":
        _render_china_intl(us_etl)
        return

    result = etl.load_result(index_code)
    pe_df = result["pe_data"]

    # 加载参考PE（宽基：乐咕月度历史；行业：申万官方当前值）
    reference_df = None
    ref_name = None
    if index_type == "宽基指数":
        ref_name = INDEX_REFERENCE.get(index_name)
        if ref_name:
            try:
                reference_df = etl.source.get_index_pe_history(ref_name)
            except Exception:
                reference_df = None

    # 当前估值卡片
    if pe_df is not None and not pe_df.empty:
        latest = pe_df.iloc[-1]
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("当前 PE(TTM)", f"{latest['pe_ratio']:.2f}")
        if pd.notna(latest.get("pe_mean")):
            col2.metric("滚动均值", f"{latest['pe_mean']:.2f}")
        if pd.notna(latest.get("pe_high_line")):
            col3.metric("高估线", f"{latest['pe_high_line']:.2f}")
        if pd.notna(latest.get("pe_low_line")):
            col4.metric("低估线", f"{latest['pe_low_line']:.2f}")

        # 数据覆盖率说明
        total_constituents = len(get_constituents(index_code))
        coverage = latest.get("stock_count", 0)
        ref_text = ""
        if reference_df is not None and not reference_df.empty:
            ref_text = (f" | 参考PE对比：自下而上 {latest['pe_ratio']:.2f}"
                        f" vs 月度参考 {reference_df['pe_ttm'].iloc[-1]:.2f}")
        elif industry_pe_ref is not None:
            ref_text = f" | 申万官方参考PE: {industry_pe_ref:.2f}"
        st.caption(f"📊 数据覆盖：{coverage}/{total_constituents} 只成分股 | "
                   f"更新至 {latest['trade_date']}{ref_text}")

        st.divider()

        # Tab 1: PE 主图
        tab1, tab2, tab3, tab4 = st.tabs(["PE 走势", "百分位", "市值/利润", "成分股"])

        with tab1:
            fig = build_pe_chart(pe_df, index_name, reference_df, industry_pe=industry_pe_ref)
            if fig:
                st.plotly_chart(fig, use_container_width=True)

            with st.expander("ℹ️ 数据说明与局限性"):
                if index_type == "申万行业":
                    st.markdown(f"""
                    - **行业分类**：申万一级行业（标准分类，31个）
                    - **计算口径**：`行业PE = Σ(成分股收盘价 × 流通股数) / Σ(归母净利润TTM)`
                    - **官方参考**：申万官方TTM PE 为当前快照，本图为自下而上历史序列
                    - ⚠️ **成分股按申万官方权重口径**，自下而上含所有成分股，两者存在差异
                    - ⚠️ **首次下载**：大行业（如电子494只）成分股多，需较长时间；数据已部分缓存
                    - 💡 验证方法：`python scripts/validate_pe.py`
                    """)
                else:
                    st.markdown(f"""
                    - **计算口径**：`指数PE = Σ(成分股收盘价 × 流通股数) / Σ(归母净利润TTM)`
                    - **TTM对齐**：按财报公告日期对齐，滚动取最近4个季度；发布前沿用上一期数据
                    - **停牌处理**：当日无行情的成分股自动剔除（不影响分子分母）
                    - **负利润**：TTM利润 ≤ 0 的成分股不参与聚合，避免PE失真
                    - ⚠️ **与参考PE的差异**：使用流通市值 + 当前成分股回溯，绝对水平比参考口径低 20-40%，趋势相关性中等。**估值区间判断需谨慎**，建议同时参考「参考PE」线
                    - ⚠️ **历史成分股**：未考虑每半年调仓，精确回溯需历史成分股数据（成本高）
                    - 💡 验证方法：`python scripts/validate_pe.py` 可查看 TTM 双算法交叉验证和 PE 对照
                    """)

        with tab2:
            fig = build_percentile_chart(pe_df, index_name)
            if fig:
                st.plotly_chart(fig, use_container_width=True)

            # 当前位置描述
            if not pe_df.empty and "pe_percentile" in pe_df.columns:
                pct = latest.get("pe_percentile", 0)
                if pd.isna(pct):
                    desc = "数据不足"
                elif pct > 80:
                    desc = f"🔴 高估区域 (百分位: {pct:.1f}%)"
                elif pct > 60:
                    desc = f"🟡 偏高区域 (百分位: {pct:.1f}%)"
                elif pct > 40:
                    desc = f"🟢 合理区域 (百分位: {pct:.1f}%)"
                elif pct > 20:
                    desc = f"🟡 偏低区域 (百分位: {pct:.1f}%)"
                else:
                    desc = f"🟢 低估区域 (百分位: {pct:.1f}%)"
                st.markdown(f"**当前估值位置**: {desc}")

        with tab3:
            fig_mv, fig_profit = build_detail_charts(pe_df, index_name)
            if fig_mv and fig_profit:
                c1, c2 = st.columns(2)
                with c1:
                    st.plotly_chart(fig_mv, use_container_width=True)
                with c2:
                    st.plotly_chart(fig_profit, use_container_width=True)

        with tab4:
            constituents = get_constituents(index_code)
            if not constituents.empty:
                st.markdown(f"**{index_name} 当前成分股（{len(constituents)} 只）**")
                st.dataframe(constituents, use_container_width=True, height=400)
            else:
                st.info("暂无成分股数据，请先更新")

        # 数据表格
        with st.expander("📋 查看原始数据"):
            cols = ["trade_date", "pe_ratio", "total_market_cap", "total_profit_ttm", "stock_count"]
            if "pe_percentile" in pe_df.columns:
                cols.append("pe_percentile")
            show_df = pe_df[cols].copy()
            rename_map = {"trade_date": "日期", "pe_ratio": "PE(TTM)",
                          "total_market_cap": "总市值(元)", "total_profit_ttm": "总利润TTM(元)",
                          "stock_count": "成分股数", "pe_percentile": "PE百分位"}
            show_df = show_df.rename(columns=rename_map)
            show_df["总市值(亿)"] = (show_df["总市值(元)"] / 1e8).round(2)
            show_df["总利润TTM(亿)"] = (show_df["总利润TTM(元)"] / 1e8).round(2)
            out_cols = ["日期", "PE(TTM)", "总市值(亿)", "总利润TTM(亿)", "成分股数"]
            if "PE百分位" in show_df.columns:
                show_df["PE百分位"] = show_df["PE百分位"].round(1)
                out_cols.append("PE百分位")
            st.dataframe(show_df[out_cols].sort_values("日期", ascending=False),
                        use_container_width=True, height=400)

            # 导出按钮
            csv = show_df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button("⬇️ 导出 CSV", data=csv,
                               file_name=f"{index_name}_PE历史.csv",
                               mime="text/csv")
    else:
        st.info(f"暂无 {index_name} 的数据，请在左侧点击「更新数据」开始拉取。")
        st.markdown("**首次运行说明：** 请在左侧更新数据。数据会缓存在本地数据库中，页面仅展示当前选择指数的有效历史结果。")


if __name__ == "__main__":
    main()
