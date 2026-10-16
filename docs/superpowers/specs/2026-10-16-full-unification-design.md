# Index PE Tracker 全量统一设计

## 目标

在不替换现有 SQLite、PEETL、Streamlit 和 GitHub + Netlify 架构的前提下，统一沪深300及其他 A 股指数/行业的估值计算口径、数据质量契约、更新可靠性、静态导出协议和前端状态展示，并为 `02498.HK` 提供隔离的港股个股估值模块。

## 背景与现状

当前系统已经能够通过公开数据源获取成分股、日行情和财务报表，按照“成分股流通市值代理值 / 成分股归母净利润 TTM”计算历史 PE，并导出为静态 JSON。现有实现已经包含质量等级、覆盖率、有效性原因和 H30533 独立估值链路，但仍存在以下不统一：

- PE 日期使用工作日模拟，没有统一的真实 A 股交易日历。
- 累计财报拆分时可能跨年度匹配前置报告。
- TTM 对季度连续性的约束不足。
- `stock_count` 未明确区分行情有效、财务有效、盈利和最终参与计算的股票数量。
- 更新脚本失败记录不足，任务中断后不能可靠续跑。
- 静态导出、Streamlit 和 H30533 的质量字段命名不完全一致。
- 港股个股数据若接入，不能与 A 股指数聚合共用相同的财务和市值语义。

## 设计原则

1. **口径优先**：任何不可验证或不可比较的数据都显示为不可用，不转换成 0 或伪造有效 PE。
2. **公告日可见性**：交易日只能使用该交易日前已公告的财务数据。
3. **数据隔离**：A 股指数、H30533 和港股个股分别使用适合自身市场的输入和计算路径。
4. **向后兼容**：保留现有核心表和静态行数组格式，通过迁移和扩展字段加入新语义。
5. **失败可见**：单项失败必须记录，整体失败达到阈值时不得发布静态数据。
6. **先测试后实现**：所有新规则先通过失败测试锁定行为，再修改生产代码。

## 统一数据口径

### A 股指数和行业

所有新浪宽基指数和申万行业使用同一个 A 股 PE 计算契约：

```text
指数 PE(TTM) = Σ(成分股收盘价 × 流通股数代理值)
              / Σ(有效成分股归母净利润 TTM)
```

具体语义：

- 市值口径：`close * outstanding_share`，标记为 `circulating_market_cap_proxy`。
- 利润口径：归属于母公司股东的净利润。
- 财务时点：按公告日期对齐，禁止使用未来公告数据。
- 成分股口径：默认使用当前缓存成分股回溯，标记为 `current_constituents_backfill`，不声称重建历史成分股。
- PE 范围：仅 `0 < pe_ratio < 100` 作为正常有效 PE。
- 总利润小于等于 0：PE 不可用，原因写入 `non_positive_aggregate_profit`。
- 成分股缺少行情、股数或有效 TTM：不进入相应分子或分母，并计入诊断字段。

### TTM 与季度拆分

累计财报严格按同一自然年度拆分：

```text
Q1 = Q1 累计
Q2 = H1 累计 - 同年度 Q1 累计
Q3 = Q3 累计 - 同年度 H1 累计
Q4 = Annual 累计 - 同年度 Q3 累计
```

规则：

- 前置报告必须与当前报告的 `report_date.year` 相同。
- 同年度前置报告缺失时，不生成该派生季度。
- 不使用上一年度报告补齐，不用 0 静默替代缺失值。
- TTM 只能使用报告期连续的最近四个季度。
- 四季度不足或不连续时，该股票在该交易日没有有效 TTM，原因为 `insufficient_ttm_history`。
- 交易日只能使用 `publish_date <= trade_date` 的季度结果。
- 输出 `financial_as_of`，表示参与计算的最新财务报告/公告时点。

### 交易日

增加真实 A 股交易日历缓存表：

```text
trading_calendar(
    trade_date TEXT PRIMARY KEY,
    is_open INTEGER NOT NULL,
    source TEXT NOT NULL,
    updated_at REAL
)
```

生产计算只使用数据源或缓存表中的开市日。工作日推算仅允许存在于离线测试 fixture，不得作为生产回退。若交易日历不可用，更新阶段失败并记录明确错误。

### 统一质量字段

A 股 PE 日表和导出目录项统一使用以下字段：

```text
constituent_count
quote_valid_count
financial_valid_count
profitable_stock_count
valid_stock_count
coverage_ratio
validity_reason
quality_level
data_source
market_cap_method
constituent_basis
financial_as_of
generated_at
```

字段语义：

- `constituent_count`：当前指数成分股总数。
- `quote_valid_count`：存在有效收盘价和正流通股数的成分股数。
- `financial_valid_count`：存在公告可见且连续四季度 TTM 的成分股数。
- `profitable_stock_count`：有效 TTM 利润大于 0 的成分股数。
- `valid_stock_count`：最终同时满足行情和利润条件、参与 PE 计算的成分股数。
- `coverage_ratio`：默认等于 `valid_stock_count / constituent_count`，并在配置中保持统一阈值。
- `validity_reason`：有效或不可用的机器可读原因；缺失原因不能伪装成正常值。

PE 日记录即使 `pe_ratio` 不可用也应保留，便于前端区分“非交易日”和“交易日但数据不足”。

## 架构与数据流

```text
数据源适配器
  ├── A 股成分股、行情、财报、交易日历
  ├── H30533 快照数据
  └── 港股 02498.HK 行情与财务
        ↓
SQLite 缓存
  ├── A 股原始表和 PE 结果表
  ├── trading_calendar
  ├── update_checkpoint / update_log
  └── hk_quotes / hk_financials / hk_valuation
        ↓
计算层
  ├── A 股 PEETL
  ├── H30533 独立 ETL
  └── HK 个股估值
        ↓
统一发布层
  ├── manifest.json
  ├── index_list.json
  ├── A 股指数 JSON
  └── H30533 / 港股独立 JSON 或 Streamlit 视图
```

A 股历史成分股不在本次范围内重建。港股个股不写入 A 股 `stock_quotes`、`stock_financials`，也不参与任何 A 股指数聚合。

## 更新可靠性

`scripts/update_all.py` 仍是唯一完整更新入口，支持：

```bash
python scripts/update_all.py
python scripts/update_all.py --resume
python scripts/update_all.py --only 000300,801080
python scripts/update_all.py --export-only
python scripts/update_all.py --no-push
```

更新阶段：

1. 加载或创建 `run_id` 和 checkpoint。
2. 获取/更新交易日历。
3. 增量更新股票行情。
4. 更新过期财报并重建季度利润。
5. 重算指定指数或受影响指数。
6. 导出静态 JSON 和 manifest。
7. 验证所有引用文件、有限数值和 schema。
8. 仅在验证通过且失败率低于阈值时提交和推送。

checkpoint 至少记录：

```text
run_id
stage
item_key
status
error_msg
updated_at
```

`item_key` 在行情/财务阶段使用股票代码，在重算阶段使用指数代码。`--resume` 跳过同一 run 中已经成功的项目，失败项目重新执行。阶段失败率超过配置阈值时，命令返回非零状态且不得执行 Git push。

## 静态导出契约

保持已有基础行数组：

```text
[date, pe, mean, high, low, percentile, market_cap, profit, ...metadata]
```

新增和统一的信息放在 `index_list.json` 和 `manifest.json`。每个目录项至少包含：

```text
code
name
type
data_file
last_updated
generated_at
quality_level
data_source
market_cap_method
constituent_basis
coverage
financial_as_of
validity
```

`manifest.json` 至少包含：

```text
schema_version
generated_at
source_revision
artifacts
item_count
date_ranges
```

导出必须排序稳定、只写有限数值、保留 `null`，不得将缺失 PE 转换成 0。所有目录项引用的 JSON 文件必须存在，导出验证失败不得推送。

## 前端行为

Streamlit 和静态页面统一展示：

- 最新交易日和生成时间。
- 有效股票数、总成分股数和覆盖率。
- 市值口径、成分股回溯说明和财务截至日期。
- PE 不可用时的明确原因。
- 数据加载中、成功但质量受限、文件错误三类状态。

静态页面加载 JSON 失败时显示具体 URL 和 HTTP 状态，保留当前选择，不清空已有选择器状态。`null` PE 不绘制为 0，也不参与均值、标准差和百分位计算。

## 港股个股模块

为 `02498.HK` 新增独立数据源和缓存命名空间：

```text
hk_quotes
hk_financials
hk_valuation
```

标准化快照字段：

```text
price
revenue
net_profit
eps
market_cap
ps_ratio
pe_ratio
currency
profit_status
valuation_metric
data_as_of
```

约束：

- 货币保留 HKD。
- 没有可靠股本数据时，市值标记为估算，不冒充审计市场数据。
- TTM 净利润小于等于 0 时，`pe_ratio = null`，`valuation_metric = PS`，`profit_status = loss-making`。
- 港股视图展示价格、PS、收入、净利润、EPS、价格历史、数据日期和估算标签。
- 港股个股不得进入 A 股指数选择器和聚合计算。

## 错误处理

- 网络请求使用有限次数重试和递增等待。
- 单股票失败必须进入 checkpoint 或失败日志。
- 数据源返回空数据时标记为 unavailable，不转换为 0。
- 交易日历缺失、导出 schema 错误和失败率超阈值均阻止发布。
- 旧 SQLite 表通过 `ALTER TABLE` 迁移补充字段，保留未知的 `extra_*` 扩展字段。
- 任何迁移必须可在临时 SQLite 数据库中重复执行。

## 测试与验收

必须新增或补充以下测试：

1. 同年度 Q2/Q3/Q4 拆分结果正确，缺少同年度前置报告时不跨年补齐。
2. 标准 TTM 公式与季度拆分结果一致，财报公告日前不可见。
3. 非交易日不生成正常 PE，交易日历缺失时显式失败。
4. 不足四个连续季度、负利润和覆盖率不足时保留无效记录及原因。
5. SQLite 迁移和索引可重复执行，旧数据和扩展字段仍可读取。
6. checkpoint 能跳过成功项、重试失败项并按 run 清理。
7. 更新失败率超过阈值时返回非零且不调用 Git push。
8. 静态导出 manifest、目录项和 JSON 文件互相匹配，所有数值为有限值。
9. 前端处理加载中、HTTP/JSON 错误、空数据和质量提示。
10. `02498.HK` 亏损 fixture 输出 PS、空 PE、HKD 和 loss-making 状态。
11. 现有 A 股、H30533 静态数据仍可读取。
12. SQLite 数据库仍被 Git 忽略。

最终验收命令：

```bash
pytest tests -q
python -m compileall app cache data_sources etl scripts
python scripts/export_static_data.py
git diff --check
git status --short
```

## 分阶段交付

1. 统一 A 股交易日、TTM、质量字段和 SQLite 迁移。
2. 统一更新 checkpoint、失败阈值、选择性重算和推送门禁。
3. 统一静态导出 manifest、前端加载错误和质量展示。
4. 增加港股个股独立估值模块。
5. 执行全量测试、静态服务检查、文档更新和发布前审查。

## 非目标

- 不重建每个历史交易日的完整指数成分股。
- 不将第三方参考 PE 作为主计算结果。
- 不为 Netlify 增加服务端 API。
- 不将 SQLite 数据库提交到 Git。
- 不把港股个股并入 A 股指数估值。
