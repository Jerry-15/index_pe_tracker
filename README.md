# 板块指数估值追踪器 (Index PE Tracker)

自下而上计算 A 股各板块/指数 PE(TTM) 的 Streamlit 应用。

> ⚠️ **免责声明**
> 本项目数据来自公开网络接口（新浪财经、申万指数、乐咕乐股），仅供学习与研究参考，**不构成任何投资建议**。数据可能存在延迟或误差，使用者需自行承担风险。请勿将本项目用于商业数据转售。

## 核心逻辑

```
指数PE(TTM) = Σ(成分股收盘价 × 流通股数) / Σ(成分股归母净利润TTM)
```

- **市值**：通过新浪财经日K线接口获取每日收盘价 + 流通股数，逐日计算流通市值代理值
- **利润TTM**：通过新浪财经财务报表接口获取季度归母净利润，按公告日期对齐计算连续4季和；季度拆分严格按同一年度
- **统计指标**：滚动均值 ± 标准差作为高估/低估线，当日PE在历史中的百分位

## 在线体验

- **Streamlit 交互版**：部署到 Streamlit Community Cloud 或 Hugging Face Spaces
- **静态图表版（Netlify）**：`static/` 目录为纯静态 ECharts 页面，见下文

## 静态图表版（Netlify 托管）

`static/` 目录是独立的静态应用（无需 Python 运行时），适合 Netlify 托管：

```
static/
├── index.html           # ECharts 前端页面
└── data/                # 导出的 JSON 数据
    ├── index_list.json
    └── 000300.json ...  # 各指数/行业数据
```

### 部署到 Netlify

1. 连接 GitHub 仓库 `Jerry-15/index_pe_tracker` 到 Netlify
2. 构建配置已写在 `netlify.toml`（发布目录 `static`）
3. 部署后访问 `https://<你的子域名>.netlify.app`

### 更新数据（静态版）

**一键更新**（更新行情→重算PE→导出→推送GitHub→Netlify自动部署）：

```bash
python scripts/update_all.py
```

- 完整更新较慢（全量下载行情，约1-3小时），适合定期运行
- 只刷新数据（不下载）：`python scripts/update_all.py --export-only`（约1分钟）
- 不推送：`python scripts/update_all.py --no-push`

数据更新流程：
1. 增量合并最新行情
2. 拉取新财报并重建季度利润
3. 重算全部板块 PE
4. 导出 JSON 到 `static/data/`
5. git push → Netlify 自动重新部署

## 快速开始

```bash
git clone https://github.com/Jerry-15/index_pe_tracker.git
cd index_pe_tracker
pip install -r requirements.txt
streamlit run app/main.py
```

首次运行会从新浪接口下载成分股数据并缓存在本地 SQLite。

## 数据源

| 数据 | 来源 | 用途 |
|------|------|------|
| 行情 & 财务 | 新浪财经（`akshare` 封装） | 核心数据 |
| 行业成分股 | 申万指数 | 行业板块 |
| 行业官方PE | 申万指数 | 参考对照 |
| 指数PE参考 | 乐咕乐股 | 验证对照 |
| 缓存 | 本地 SQLite | 避免重复下载 |

## 项目结构

```
index_pe_tracker/
├── config.py              # 配置文件
├── requirements.txt
├── data_sources/
│   ├── base.py            # 数据源抽象接口
│   └── sina.py            # 新浪财经/申万行业实现
├── cache/
│   └── db.py              # SQLite 缓存层
├── etl/
│   └── pipeline.py        # ETL 计算引擎（bisect 快速TTM）
├── scripts/
│   └── validate_pe.py     # 数据准确性校验
└── app/
    └── main.py            # Streamlit 主应用
```

## 支持板块

### 宽基指数（新浪数据源）

| 板块 | 指数代码 |
|------|---------|
| 沪深300 | 000300 |
| 中证500 | 000905 |
| 深证成指 | 399001 |
| 创业板指 | 399006 |

### 申万一级行业（31个，标准分类）

银行、食品饮料、电子、医药生物、计算机等 31 个申万一级行业。
- 成分股来自申万官方（`index_component_sw`）
- 官方 TTM PE 作为参考对照（图中灰色水平线）
- 在侧边栏切换「宽基指数 / 申万行业」即可选择

> ⚠️ 大行业（如电子494只、医药479只）首次下载需较长时间，已有部分股票数据缓存。

## 在线部署 (Streamlit Community Cloud)

1. 将本仓库推送到 GitHub
2. 访问 [share.streamlit.io](https://share.streamlit.io) → 用 GitHub 账号登录
3. 点击 **New app** → 选择仓库 `Jerry-15/index_pe_tracker` → 主文件 `app/main.py`
4. 点击 **Deploy**，等待部署完成即获得公开网址

> ⚠️ **公开部署注意**
> - 首次运行需下载约 4800 只股票数据（数小时），Streamlit Cloud 免费版有限时限制
> - 多人并发访问时新浪接口可能封 IP，建议低并发使用
> - 公开环境建议关闭自动更新，改用预构建数据或限定更新频率

## 注意事项

1. **成分股变化**：首次运行使用当前成分股列表回溯，存在轻微幸存者偏差
2. **TTM 对齐**：净利润按公告日期对齐，仅使用交易日前已公告的连续四季度；缺失前置报告不跨年度补齐
3. **负PE过滤**：总利润 ≤ 0 的日期不参与统计（市场极端情况）
4. **首次运行耗时**：300只股票 × 8年日K线 ≈ 5-10分钟，后续增量更新较快
5. **交易日**：生产计算使用真实 A 股交易日历，交易日历不可用时不以周一至周五替代
6. **质量字段**：输出包含有效行情数、财务有效数、盈利股票数、覆盖率、市值口径和成分股回溯基础

### 可靠更新

```bash
python scripts/update_all.py --resume
python scripts/update_all.py --only 000300,801080 --no-push
python scripts/export_static_data.py
python scripts/validate_export.py
```

更新过程中单只股票失败会记录到 SQLite checkpoint；行情失败率超过阈值时停止发布，不执行推送。

### 港股个股

`02498.HK` 使用独立的港股数据表和估值路径，不参与 A 股指数聚合。亏损时不显示负 PE，改为显示 PS，并保留 HKD 和市值估算标记。

## 数据准确性验证

运行 `python scripts/validate_pe.py` 可执行两重验证：

1. **TTM 双算法交叉验证**：季度拆分法 vs 标准公式法
   `TTM = 最近累计净利 + (上年全年净利 - 上年同期累计净利)`
   → 结果偏差 <0.3%，证明 TTM 计算正确。

2. **指数PE对照**（自下而上 vs 乐咕乐股参考PE）：
   - 绝对水平比参考口径低 20-40%（流通市值 vs 总市值、幸存者偏差、口径差异）
   - 趋势相关性中等（近8年 r≈0.7-0.8），**估值区间判断需谨慎**

## 已实现功能（对照优化方案）

| 优化方案要点 | 实现状态 |
|---|---|
| 参考PE叠加层（官方月度对照） | ✅ 图表灰线叠加 |
| 正常估值区间背景色带（均值±1σ） | ✅ 灰色填充 |
| 数据覆盖率展示（覆盖/总数） | ✅ 指标下方 caption |
| TTM 双算法交叉验证 | ✅ scripts/validate_pe.py |
| PE 历史百分位视图 | ✅ 百分位 Tab |
| 负利润 / 停牌处理 | ✅ 自动剔除，文档说明 |
| is_valid 标记 | ✅ 过滤 PE<0 或 >100 的日期 |
| 向量化性能优化 | ✅ 全量PE计算 <5s |
