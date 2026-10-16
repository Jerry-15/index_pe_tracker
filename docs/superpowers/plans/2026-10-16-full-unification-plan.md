# Index PE Tracker 全量统一实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一 A 股 PE 计算口径、真实交易日、数据质量、可靠更新、静态发布、前端状态和港股个股估值，同时保持现有架构和旧数据可读。

**Architecture:** 在现有 `CacheDB`、`PEETL`、Streamlit 和静态导出上做增量改造。A 股、H30533 和港股个股保留独立数据路径，但通过一致的质量字段、manifest 和错误状态对外呈现。所有行为变更先用失败测试锁定，再实现。

**Tech Stack:** Python 3.11, pandas, NumPy, SQLite, AkShare, Streamlit, Plotly, ECharts, pytest, Netlify.

---

## 文件地图

- Modify: `cache/db.py` — 交易日历、质量字段迁移、checkpoint、索引和港股表。
- Modify: `etl/pipeline.py` — 同年度季度拆分、连续 TTM、真实交易日过滤、统一 PE 质量字段。
- Modify: `data_sources/sina.py` — 交易日历接口和显式数据源错误。
- Create: `data_sources/hk.py` — `02498.HK` 行情和财务标准化适配器。
- Modify: `scripts/update_all.py` — checkpoint、`--resume`、`--only`、失败阈值、发布门禁。
- Modify: `scripts/export_static_data.py` — 稳定排序、统一目录元数据和导出校验。
- Modify: `app/main.py` — A 股质量面板和港股独立视图。
- Modify: `static/index.html` — 加载、错误、空数据和质量状态。
- Modify: `config.py` — 质量阈值、失败阈值和市场口径常量。
- Modify: `README.md` — 统一口径、更新命令和限制说明。
- Modify: `requirements.txt` — 保证 pytest 和现有运行依赖明确。
- Create: `tests/conftest.py` — 临时 SQLite、财报、行情和交易日 fixture。
- Modify: `tests/test_ttm.py` — TTM 和有效性回归测试。
- Create: `tests/test_calendar.py` — 交易日历行为。
- Create: `tests/test_update_checkpoint.py` — checkpoint 和更新失败门禁。
- Create: `tests/test_hk_valuation.py` — 港股亏损估值语义。
- Modify: `tests/test_static_export.py` — manifest、目录项和有限数值契约。
- Modify: `tests/test_security_record.py` 或新增对应测试 — 若现有安全/记录测试需要适配新字段。

## Task 1: 建立失败测试和测试 fixture

**Files:**
- Create: `tests/conftest.py`
- Modify: `tests/test_ttm.py`
- Create: `tests/test_calendar.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 添加可重复的临时数据库 fixture**

在 `tests/conftest.py` 创建 `cache_db(tmp_path)` fixture，返回 `CacheDB(tmp_path / "test.db")`；创建同年度财报样本，包含 2024 Q1/H1/Q3/Annual、2025 Q1/H1，公告日期晚于报告日期；创建交易日样本，包含周末和中国节假日日期。

- [ ] **Step 2: 写同年度拆分失败测试**

在 `tests/test_ttm.py` 添加测试：2025 H1 缺少 2025 Q1 时，不得使用 2024 Q1；2025 Q3 缺少 2025 H1 时，不生成 Q3；完整同年度报告必须得到 `Q2 = H1 - Q1`、`Q3 = Q3累计 - H1`、`Q4 = Annual - Q3`。

- [ ] **Step 3: 写连续四季度和公告日失败测试**

添加测试：缺少一个季度时 `_compute_ttm_from_quarters()` 返回 `None`；四季度完整但最后一季公告日晚于目标交易日时，返回 `None`；公告日前的财报不参与 TTM。

- [ ] **Step 4: 写交易日历失败测试**

在 `tests/test_calendar.py` 添加测试：缓存的 `is_open=0` 日期不进入计算日期；交易日历为空时生产路径抛出明确异常；测试 fixture 可以显式传入日期，不依赖周一至周五推算。

- [ ] **Step 5: 运行测试确认按预期失败**

运行：`pytest tests/test_ttm.py tests/test_calendar.py -q`

预期：新增规则测试失败，失败原因应是现有跨年度/工作日逻辑不满足规则，而不是 import 或 fixture 错误。

## Task 2: 统一 SQLite schema、迁移和交易日历

**Files:**
- Modify: `cache/db.py`
- Modify: `config.py`
- Create: `tests/test_db_schema.py`

- [ ] **Step 1: 写 schema 迁移失败测试**

创建旧版临时数据库，仅包含当前 `index_pe_daily` 字段和 `extra_legacy`，初始化 `CacheDB` 两次，断言新增质量字段、`trading_calendar`、checkpoint 表和索引存在，且旧行和 `extra_legacy` 仍可读取。

- [ ] **Step 2: 增加配置常量**

在 `config.py` 增加：

```python
PE_MIN_COVERAGE = 0.30
PE_MIN_RATIO = 0.0
PE_MAX_RATIO = 100.0
UPDATE_FAILURE_THRESHOLD = 0.10
MARKET_CAP_METHOD = "circulating_market_cap_proxy"
CONSTITUENT_BASIS = "current_constituents_backfill"
```

- [ ] **Step 3: 扩展 regular PE 表并添加索引**

在 `_init_tables()` 和迁移函数中增加 `constituent_count`、`quote_valid_count`、`financial_valid_count`、`profitable_stock_count`、`valid_stock_count`、`financial_as_of`、`market_cap_method`、`constituent_basis`。使用 `CREATE INDEX IF NOT EXISTS` 添加行情、财务、季度利润和 PE 日期索引。

- [ ] **Step 4: 增加交易日历读写 API**

实现 `save_trading_calendar(df)`、`load_trading_calendar(start_date, end_date)` 和 `has_trading_calendar()`；日期标准化为 `YYYY-MM-DD`，`is_open` 只接受 0/1，空 DataFrame 不得被解释为全量开市。

- [ ] **Step 5: 增加 checkpoint API**

创建 `update_checkpoint(run_id, stage, item_key, status, error_msg, updated_at)` 表和 `save_checkpoint()`、`load_checkpoint()`、`clear_checkpoint()` 方法。查询结果按阶段和 item key 稳定排序。

- [ ] **Step 6: 运行 schema 测试和现有缓存回归测试**

运行：`pytest tests/test_db_schema.py tests/test_ttm.py -q`

预期：schema 测试通过，TTM 行为测试仍只剩计算层失败。

## Task 3: 修复 TTM、交易日和 PE 质量计算

**Files:**
- Modify: `etl/pipeline.py`
- Modify: `data_sources/sina.py`
- Modify: `tests/test_ttm.py`
- Modify: `tests/test_calendar.py`

- [ ] **Step 1: 增加交易日历数据源方法**

在 `SinaDataSource` 增加 `get_trading_calendar(start_date, end_date)`，将 AkShare 返回值归一化为 `trade_date`、`is_open`；接口异常或返回空值抛出 `RuntimeError`，不静默返回工作日序列。

- [ ] **Step 2: 替换跨年度季度匹配**

重写 `_decompose_quarterly()` 的前置行查找：Q1/H1/Q3/Annual 只能匹配相同 `report_date.year` 的前置期间；找不到时跳过派生季度。先运行：`pytest tests/test_ttm.py -q`，预期 TTM 测试通过。

- [ ] **Step 3: 实现连续季度判断**

在 `_compute_ttm_from_quarters()` 中按报告期构造季度序列，只接受最近四个连续季度；过滤 `publish_date <= target_date`，不足四季度返回 `None`，并保留正负利润信息供上层统计。

- [ ] **Step 4: 接入真实交易日历**

将 `_get_trading_dates()` 改为从 `CacheDB` 读取开市日；缓存不存在时由数据源填充，数据源失败直接抛错。测试调用允许传入 fixture 日期，生产代码不得调用 weekday 推算。

- [ ] **Step 5: 输出统一质量字段和无效记录**

修改 `_vectorized_compute_pe()`：保留每个真实交易日的结果；分别统计行情有效、财务有效、正利润和最终有效股票数；覆盖率不足、利润非正、TTM 不足和 PE 越界时保留 `pe_ratio=None` 及机器可读 `validity_reason`。

- [ ] **Step 6: 改造统计计算**

只对有效 PE 计算 rolling mean/std 和 percentile，使用向量化 `rank(pct=True) * 100`；无效行的统计值保持 `None`，不参与均值和百分位。

- [ ] **Step 7: 运行核心测试**

运行：`pytest tests/test_ttm.py tests/test_calendar.py tests/test_db_schema.py -q`

预期：全部通过，并保持现有 PE 数值 fixture 不变。

## Task 4: 增加更新 checkpoint、失败阈值和选择性重算

**Files:**
- Modify: `scripts/update_all.py`
- Modify: `config.py`
- Create: `tests/test_update_checkpoint.py`

- [ ] **Step 1: 写 checkpoint resume 失败测试**

测试同一 run 中成功股票在 `--resume` 被跳过，失败股票被重新执行；`clear_checkpoint(run_id)` 只清理指定 run；失败率超过阈值时返回非零。

- [ ] **Step 2: 抽取阶段执行器**

将行情、财报、指数重算阶段改为接收 `run_id`、checkpoint 和可选代码集合；每个 item 开始时记录 running，成功记录 success，异常记录 failed 和完整错误文本。

- [ ] **Step 3: 增加命令行参数**

增加：

```python
parser.add_argument("--resume", action="store_true")
parser.add_argument("--only", help="comma-separated index codes")
```

`--only` 只影响指数重算和导出范围；`--export-only` 不触碰原始数据；未指定 `--resume` 时创建新 run。

- [ ] **Step 4: 实现失败门禁**

阶段完成后计算 `failed / attempted`。超过 `UPDATE_FAILURE_THRESHOLD` 时抛出 `UpdateAborted`，主函数返回非零，且不调用 `export_static()` 或 `git_push()`；低于阈值才进入后续阶段。

- [ ] **Step 5: 写 push 调用隔离测试**

用 patch 替换 `git_push`，模拟行情阶段失败率超过阈值，断言 `git_push` 未调用；模拟 `--only 000300`，断言不重算其他指数。

- [ ] **Step 6: 运行更新测试**

运行：`pytest tests/test_update_checkpoint.py -q`

预期：全部通过。

## Task 5: 统一静态导出和发布校验

**Files:**
- Modify: `scripts/export_static_data.py`
- Modify: `tests/test_static_export.py`
- Create: `scripts/validate_export.py`

- [ ] **Step 1: 写导出 schema 失败测试**

补充测试：目录项必须有 `data_file`、`quality_level`、`coverage`、`financial_as_of` 和 `validity`；manifest 中的 artifact 全部存在；行数组数值必须是有限值或 `None`；目录和文件排序稳定。

- [ ] **Step 2: 统一 A 股目录元数据**

在 `_add_quality_catalog_metadata()` 写入 `market_cap_method`、`constituent_basis`、`last_updated`、`data_file` 和统一 coverage 字段；对缺失质量字段使用 `quality_level=C` 和明确 validity，而不是伪造完整质量。

- [ ] **Step 3: 统一导出顺序和数值处理**

按 `type/name/code` 排序目录，按日期排序数据；使用 `_number()` 清除 NaN、Infinity 和 `-Infinity`；保留无效 PE 为 JSON `null`。

- [ ] **Step 4: 实现静态导出验证命令**

创建 `scripts/validate_export.py`，验证 `index_list.json`、manifest、所有 `data_file`、日期范围、JSON 可解析性和有限数值；失败时返回非零。

- [ ] **Step 5: 让更新脚本在 push 前调用验证器**

导出完成后调用验证函数；校验失败不得执行 Git add/commit/push。

- [ ] **Step 6: 运行静态导出测试**

运行：`pytest tests/test_static_export.py -q && python scripts/validate_export.py`

预期：测试通过，当前 `static/data` 全部通过验证。

## Task 6: 统一 Streamlit 和静态页面状态

**Files:**
- Modify: `app/main.py`
- Modify: `static/index.html`
- Modify: `tests/test_static_export.py`
- Create: `tests/test_frontend_contract.py`

- [ ] **Step 1: 写前端契约失败测试**

断言静态页面包含加载状态、`response.ok` 检查、JSON 错误、空数组处理、上一选择保留、生成时间、coverage、quality_level 和 validity 展示。

- [ ] **Step 2: 增强 Streamlit 质量面板**

显示最新交易日、生成时间、有效/总成分股数、覆盖率、市值口径、成分股基础、财务截至日期和 validity reason；`pe_ratio=None` 时显示不可用原因，不画当前值 annotation。

- [ ] **Step 3: 增强静态页面加载状态**

页面加载时显示 loading；fetch 失败显示 URL 和 HTTP 状态；JSON 结构不合法显示格式错误；空数据显示 unavailable；保留 selector 当前值。

- [ ] **Step 4: 统一 null 绘图行为**

绘图前过滤无效 PE，但保留日期轴和质量提示；不把 null 转换成 0；小屏幕降低图表高度并调用 resize。

- [ ] **Step 5: 运行前端契约测试**

运行：`pytest tests/test_frontend_contract.py tests/test_static_export.py -q`

预期：全部通过。

## Task 7: 增加港股 02498.HK 独立估值模块

**Files:**
- Create: `data_sources/hk.py`
- Modify: `cache/db.py`
- Modify: `app/main.py`
- Create: `tests/test_hk_valuation.py`
- Modify: `README.md`

- [ ] **Step 1: 写亏损港股 fixture 测试**

给定 HKD 股价、收入、负 TTM 净利润和 EPS，断言结果为 `currency=HKD`、`profit_status=loss-making`、`valuation_metric=PS`、`ps_ratio>0`、`pe_ratio is None`。

- [ ] **Step 2: 增加港股表迁移**

创建 `hk_quotes`、`hk_financials`、`hk_valuation`，所有主键包含代码和日期；不复用 A 股原始行情/财务表。

- [ ] **Step 3: 实现 HKDataSource**

实现 `get_stock_snapshot("02498")` 和标准化方法，保留 HKD；可靠股本缺失时将 market cap 标记为 estimated；非正利润禁止返回正 PE。

- [ ] **Step 4: 增加 Streamlit 独立模式**

在 A 股指数选择器之外增加“港股个股”视图，展示价格、PS、收入、净利润、EPS、价格历史、数据日期和 source/estimate 标签。

- [ ] **Step 5: 更新文档和测试**

README 明确港股模块不参与 A 股聚合、亏损使用 PS、市场值可能为估算值。

- [ ] **Step 6: 运行港股测试**

运行：`pytest tests/test_hk_valuation.py -q`

预期：全部通过。

## Task 8: 完整回归、文档和发布验收

**Files:**
- Modify: `README.md`
- Modify: `requirements.txt`
- Verify: `static/data/*`, `.gitignore`

- [ ] **Step 1: 更新 README 口径和命令**

补充市值代理口径、当前成分股回溯限制、TTM 公告日规则、质量字段、checkpoint 命令、导出验证命令和港股模块说明。

- [ ] **Step 2: 运行完整测试**

运行：`pytest tests -q`

预期：所有测试通过，现有测试不少于当前基线 76 个，新增测试覆盖统一设计中的 12 类验收行为。

- [ ] **Step 3: 运行语法检查**

运行：`python -m compileall app cache data_sources etl scripts`

预期：退出码 0。

- [ ] **Step 4: 重新导出和验证静态数据**

运行：`python scripts/export_static_data.py && python scripts/validate_export.py`

预期：manifest 存在、所有目录项引用文件存在、无 NaN/Infinity、SQLite 未生成 Git 跟踪变更。

- [ ] **Step 5: 验证本地静态服务**

启动：`python -m http.server 8899 --directory static`

检查 `/`、`/data/manifest.json`、`/data/index_list.json` 和 `/data/000300.json` 返回 HTTP 200，然后停止服务。

- [ ] **Step 6: 检查差异和工作区**

运行：`git diff --check && git status --short`

确认没有空白错误，数据库仍被忽略，只有源代码、测试、文档和允许的静态文件发生变化。未经用户明确要求不执行 commit、push 或 PR。
