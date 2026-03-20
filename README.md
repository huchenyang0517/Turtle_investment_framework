# 龟龟投资框架 (Turtle Investment Framework)

AI 辅助的 A 股/港股基本面分析系统。混合架构：Python 脚本完成确定性数据采集，LLM 提示词驱动定性分析与多因子评估。

## 核心功能

| 阶段 | 名称 | 实现方式 | 说明 |
|------|------|----------|------|
| Phase 1A | 数据采集 | Python / Tushare Pro | 5 年财务数据，24+ API 接口，自动单位换算 |
| Phase 1B | 网络研究 | Agent / WebSearch | 治理结构、行业竞争、子公司、管理层讨论 |
| Phase 2A | 年报预处理 | Python / pdfplumber | PDF 关键章节提取（7 个目标节） |
| Phase 2B | 年报精提取 | Agent / 结构化提取 | 附注数据（5+1 项） |
| Phase 3 | 四因子分析 | Agent / 渐进式分析 | 资产质量 → 穿透回报率 → 精算 → 估值 |
| 选股器 | 龟龟选股器 | Python / 两级筛选 | Tier 1 批量过滤 + Tier 2 深度分析 |
| 回测 | 组合回测 | Python / 月末调仓 | 基于 composite_score 的 TopK 等权组合 vs 基准 |

## 系统架构

```
用户输入 (股票代码 + 年报PDF)
         │
    ┌────▼────┐
    │ Phase 0 │  自动下载年报 (/download-report)
    └────┬────┘
         │
    ┌────▼────┬──────────────┐
    │Phase 1A │  Phase 2A    │  ← 并行运行
    │Tushare  │  PDF预处理    │
    │数据采集  │  (pdfplumber) │
    └────┬────┴──────┬───────┘
         │           │
    ┌────▼────┐      │
    │Phase 1B │      │
    │WebSearch│      │
    │(§7§8§9B)│      │
    └────┬────┘      │
         │      ┌────▼────┐
         │      │Phase 2B │
         │      │Agent    │
         │      │精提取    │
         └──┬───┴────┬────┘
            │        │
       ┌────▼────────▼────┐
       │     Phase 3      │
       │  四因子分析 + 报告 │
       └──────────────────┘
              │
     output/{code}_分析报告.md
```

### 各阶段角色

- **Phase 0** — 调用 `/download-report` 命令自动搜索并下载最新年报 PDF
- **Phase 1A** — `tushare_collector.py` 通过 Tushare Pro API 采集结构化数据，输出 `data_pack_market.md`（含 §1–§17 共 17 个数据段）
- **Phase 1B** — Agent 执行 WebSearch 补充治理/行业/子公司等非结构化信息
- **Phase 2A** — `pdf_preprocessor.py` 使用关键词匹配定位年报 7 个目标章节，输出 `pdf_sections.json`
- **Phase 2B** — Agent 从 PDF 章节中精确提取附注数据
- **Phase 3** — Agent 执行四因子分析（资产质量 / 穿透回报率粗算 / 精算 / 估值），输出分析报告

## 快速开始

### 环境要求

- Python >= 3.10
- [Tushare Pro](https://tushare.pro/) 账号及 API Token
- （可选）pdfplumber 用于 PDF 解析
- （内置）`/download-report` 命令用于自动下载年报

### 安装

**首次安装：**

```bash
git clone https://github.com/terancejiang/Turtle_investment_framework.git
cd Turtle_investment_framework

# 一键初始化（创建 venv、安装依赖、验证环境）
bash init.sh
```

**更新已有项目：**

```bash
cd Turtle_investment_framework
git pull

# 重新安装依赖（确保新增的包被安装）
bash init.sh --force-install
```

`init.sh` 会自动完成：
1. 查找系统中 Python >= 3.10，创建 `.venv`
2. 安装 `requirements.txt` 中的依赖
3. 检查 `TUSHARE_TOKEN` 环境变量
4. 运行测试验证环境

### 配置 Tushare Token

```bash
cp .env.sample .env
# 编辑 .env，填入你的 Token
# TUSHARE_TOKEN=your_token_here
```

或者直接设置环境变量：

```bash
export TUSHARE_TOKEN='your_token_here'
```

## 使用方法

### 单股分析（完整流程）

在 [Claude Code](https://claude.com/claude-code) 中使用 slash command：

```
/turtle-analysis 600887
```

这会自动执行完整的 Phase 0 → 1A → 1B → 2A → 2B → 3 流程。

### 数据采集（仅 Phase 1A）

```bash
# 基本用法
.venv/bin/python scripts/tushare_collector.py --code 600887.SH

# 指定输出路径
.venv/bin/python scripts/tushare_collector.py --code 600887.SH --output output/data_pack_market.md

# 附加额外字段
.venv/bin/python scripts/tushare_collector.py --code 00700.HK --extra-fields balancesheet.defer_tax_assets

# 试运行（不调用 API）
.venv/bin/python scripts/tushare_collector.py --code 600887 --dry-run
```

输出 Markdown 包含以下数据段：

| 段号 | 内容 | 来源 |
|------|------|------|
| §1 | 基本信息 | stock_basic + daily_basic |
| §2 | 行情数据（52 周范围） | pro_bar weekly |
| §3 | 合并利润表（5 年） | income |
| §3P | 母公司利润表 | income (report_type=4) |
| §4 | 合并资产负债表（5 年） | balancesheet |
| §4P | 母公司资产负债表 | balancesheet (report_type=4) |
| §5 | 合并现金流量表 + FCF | cashflow |
| §6 | 分红历史 | dividend |
| §6.1 | 占位符（分红承诺） | Phase 1B（公司公告：未来三年回报规划、业绩会等） |
| §7–§10 | 占位符（WebSearch 补充） | Phase 1B |
| §11 | 周线数据（10 年） | weekly |
| §12 | 财务指标（ROE/毛利率等） | fina_indicator |
| §13 | 风险警告 | 自动检测 + Agent |
| §14 | 无风险利率 | yc_cb |
| §15 | 股份回购 | repurchase |
| §16 | 股权质押 | pledge_stat |
| §17 | 衍生指标预计算 | compute_derived_metrics |

### 年报解析（仅 Phase 2A）

```bash
# 基本用法
.venv/bin/python scripts/pdf_preprocessor.py --pdf 伊利股份_2024_年报.pdf

# 指定输出 + 详细日志
.venv/bin/python scripts/pdf_preprocessor.py --pdf report.pdf --output output/pdf_sections.json --verbose

# 使用 TOC hints 覆盖关键词匹配
.venv/bin/python scripts/pdf_preprocessor.py --pdf report.pdf --hints toc_hints.json
```

提取的 7 个目标章节：

| 缩写 | 章节 | 说明 |
|------|------|------|
| P2 | 公司治理 | 公司治理结构 |
| P3 | 会计政策 | 重要会计政策和会计估计 |
| P4 | 应收账款 | 应收账款/票据附注 |
| P6 | 合并报表 | 合并财务报表附注 |
| P13 | 风险 | 风险提示/重大事项 |
| MDA | 管理层讨论 | 经营情况讨论与分析 |
| SUB | 子公司 | 主要子公司/长期股权投资明细 |

### 批量选股（龟龟选股器）

```bash
# 完整流程（Tier 1 + Tier 2）
.venv/bin/python scripts/screener_core.py

# 仅 Tier 1 快速筛选
.venv/bin/python scripts/screener_core.py --tier1-only

# 限制 Tier 2 分析数量
.venv/bin/python scripts/screener_core.py --tier2-limit 50

# 自定义阈值
.venv/bin/python scripts/screener_core.py --min-roe 10 --max-pe 30 --min-gross-margin 20

# 导出结果
.venv/bin/python scripts/screener_core.py --csv output/screener.csv --html output/screener.html

# 刷新缓存
.venv/bin/python scripts/screener_core.py --cache-refresh          # 全部刷新
.venv/bin/python scripts/screener_core.py --cache-tier2-refresh    # 仅刷新 Tier 2
```

### 组合回测

基于龟龟选股器的 composite_score，对 TopK 等权组合进行历史回测，并与基准指数对比净值曲线。

**策略逻辑**：
1. 每个调仓日（月末/季末/半年末/年末等，由 `--rebalance-freq` 决定）运行龟龟选股器（Tier1+Tier2），按 composite_score 取 TopK（默认 10）
2. 若 TopK 股票集合相对上次调仓日发生变化，则在本调仓日收盘调仓到新的 TopK（等权、一次性全仓）
3. 若集合未变化，则持有不动
4. 与沪深 300（默认 399300.SZ）等基准进行净值曲线对比，输出可视化图表

**重要提示**：Tier2 会对最多 200 只股票做深度分析，十年按月运行耗时较长。可用 `--tier2-limit` 缩小 Tier2 范围以加速（得到的是「近似 TopK」）。

```bash
# 基本用法（默认：10 年、月末调仓、Top10、沪深 300 基准）
.venv/bin/python scripts/portfolio_rebalance_backtest_composite.py

# 指定调仓频率
.venv/bin/python scripts/portfolio_rebalance_backtest_composite.py --rebalance-freq annual       # 年末
.venv/bin/python scripts/portfolio_rebalance_backtest_composite.py --rebalance-freq semi_annual # 半年末（6月/12月）
.venv/bin/python scripts/portfolio_rebalance_backtest_composite.py --rebalance-freq quarterly   # 季末
.venv/bin/python scripts/portfolio_rebalance_backtest_composite.py --rebalance-freq weekly      # 周末

# 自定义参数
.venv/bin/python scripts/portfolio_rebalance_backtest_composite.py --top-k 20 --years 5
.venv/bin/python scripts/portfolio_rebalance_backtest_composite.py --start-date 2015-01-01 --end-date 2024-12-31

# 快速验证（限制 Tier2 数量 + 最多回测月数）
.venv/bin/python scripts/portfolio_rebalance_backtest_composite.py --tier2-limit 50 --max-months 12

# 多基准对比
.venv/bin/python scripts/portfolio_rebalance_backtest_composite.py --benchmarks "399300.SZ,000300.SH"

# 详细调仓日志
.venv/bin/python scripts/portfolio_rebalance_backtest_composite.py --verbose-rebalance
```

**输出文件**（位于 `output/` 目录；`--cache-dir` 仅用于 TopK 中间缓存；文件名中的 `{freq}` 随 `--rebalance-freq` 变化，如 monthly/quarterly/semi_annual/annual）：

| 文件 | 说明 |
|------|------|
| `rebalance_composite_{freq}_equity_curve.csv` | 组合每日净值序列 |
| `rebalance_composite_{freq}_events.csv` | 每次调仓日期及 TopK 股票代码/名称 |
| `rebalance_composite_{freq}_summary.csv` | 组合与基准的 CAGR、MDD 等汇总指标 |
| `rebalance_composite_{freq}_vs_benchmarks.png` | 净值曲线对比图 |
| `rebalance_composite_{freq}_vs_benchmarks.csv` | 组合与基准归一化净值（便于自定义绘图） |
| `rebalance_composite_{freq}_metrics_by_instrument.csv` | 组合与各基准的 CAGR、简单年均收益、最大回撤（按标的汇总） |
| `rebalance_composite_{freq}_stock_interval_pl.csv` | 每只股票各持有区间的收益明细（有持仓时生成） |
| `rebalance_composite_{freq}_stock_pl_sorted.csv` | 每只股票累计收益排序（有持仓时生成） |

**常用参数**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--rebalance-freq` | monthly | 调仓频率：annual / semi_annual / quarterly / monthly / weekly / daily |
| `--top-k` | 10 | 持仓股票数量 |
| `--years` | 10 | 回测年数（近似） |
| `--start-date` / `--end-date` | 自动推算 | 回测起止日期 |
| `--tier2-limit` | 200 | Tier2 分析股票数量上限 |
| `--max-months` | 0 | 仅用于快速验证：最多回测多少个月末（0=不限制） |
| `--benchmarks` | 399300.SZ | 基准指数代码，逗号分隔 |
| `--initial-capital` | 1000000 | 初始资金（元） |
| `--adj` | hfq | 复权类型：qfq / hfq / none |

### Jupyter Notebook

```bash
cd notebooks
jupyter notebook screener.ipynb
```

Notebook 包含 7 个 Cell：初始化 → Tier 1 过滤 → 排名 → Tier 2 分析 → 评分 → 导出 → 个股详情，附 matplotlib 可视化图表。

## 项目结构

```
Turtle_investment_framework/
├── scripts/                       # Python 脚本
│   ├── config.py                  # 配置工具（Token、股票代码验证、PDF检查）
│   ├── format_utils.py            # 格式化工具（数字、表格、标题）
│   ├── tushare_collector.py       # Tushare 数据采集 + §17 衍生指标
│   ├── pdf_preprocessor.py        # PDF 年报章节提取
│   ├── screener_config.py         # 选股器配置（ScreenerConfig dataclass）
│   ├── screener_core.py           # 选股器核心（TushareScreener + CLI）
│   ├── download_report.py         # 年报PDF下载（含重试、PDF验证）
│   └── portfolio_rebalance_backtest_composite.py  # 组合回测（TopK 等权 vs 基准）
├── prompts/                       # LLM 分析提示词
│   ├── coordinator.md             # 协调器（多阶段调度中枢）
│   ├── phase1_数据采集.md          # Phase 1A/1B 数据采集指令
│   ├── phase2_PDF解析.md           # Phase 2A/2B PDF 解析指令
│   ├── phase3_分析与报告.md        # Phase 3 四因子分析指令
│   └── references/                # 四因子参考文档
│       ├── factor1_资产质量与商业模式.md
│       ├── factor2_穿透回报率粗算.md
│       ├── factor3_穿透回报率精算.md
│       └── factor4_估值与安全边际.md
├── notebooks/
│   └── screener.ipynb             # 选股器交互式 Notebook
├── tests/                         # 测试套件（451+ tests）
│   ├── conftest.py
│   ├── fixtures/mock_tushare_responses/  # 17 个 Mock JSON
│   ├── test_config.py             # 26 tests
│   ├── test_format_utils.py       # 21 tests
│   ├── test_tushare_client.py     # 74 tests
│   ├── test_pdf_preprocessor.py   # 72 tests
│   ├── test_phase1b_prompt.py     # 21 tests
│   ├── test_phase3_prompt.py      # 112 tests
│   ├── test_derived_metrics.py    # 78 tests
│   └── test_screener.py           # 84 tests
├── output/                        # 输出目录（gitignored）
├── init.sh                        # 环境初始化脚本
├── requirements.txt               # Python 依赖
├── feature_list.json              # 功能清单（87/108 完成）
└── claude-progress.txt            # 开发进度日志
```

## 分析框架详解

### 四因子模型

龟龟投资策略基于四因子模型对上市公司进行全面评估：

**因子 1：资产质量与商业模式**
- 模块 0：数据异常扫描、利润校准、现金校准
- 模块 1–4：轻资产/重资产/杠杆型/平台型商业模式判别
- 模块 5–8：应收质量、存货、固定资产、无形资产
- 模块 9：母公司 vs 合并口径对比（含子公司识别 §9B）
- 输出：资产质量评分 + 商业模式类型

**因子 2：穿透回报率粗算**
- 步骤 1：从 §17.2 读取 C/B/M/N/OE 等参数 + OE 纠偏
- 步骤 2：否决门（ROE < 8% 或负值 → 直接否决）
- 步骤 3：计算穿透回报率 R% = M × (1 − 分红税率) × OE
- 步骤 4：否决判定（R% < Rf → 否决）

**因子 3：穿透回报率精算**
- 步骤 1–3：真实现金收入（S/T/U、保守基础、收款比率）
- 步骤 4–6：经营性流出（W1–W4：供应商/员工/税/利息）
- 步骤 7–9：基础盈余、AA（含/不含资本化）、CV、λ 系数
- 步骤 10–11：分配意愿（M）、可预测性
- 输出：精算后 R% + 可靠性标签

**因子 4：估值与安全边际**
- 步骤 1–4：相对估值（PE/PB 分位数、历史对比）
- 步骤 5-1：绝对估值指标（EV/EBITDA、现金调整 PE、FCF 收益率等 11 项）
- 步骤 5-2：基准价（5 种方法取算术平均）+ 溢价分析
- 输出：买入/观察/规避 评级

### 龟龟选股器

两级筛选系统，从全 A 股 5000+ 只中筛选优质标的：

**Tier 1 — 批量过滤（仅市场数据，~5 秒）**

| 过滤条件 | 默认阈值 |
|----------|----------|
| 排除 ST/PT/退市整理 | — |
| 上市年限 | ≥ 3 年 |
| 市值 | ≥ 5 亿元 |
| 日换手率 | ≥ 0.1% |
| PB | 0 < PB ≤ 10 |
| 股息率 | > 0 |
| PE（主通道） | 0 < PE_TTM ≤ 50 |
| PE（观察通道） | PE_TTM < 0，按市值取前 50 |

排名公式：`Score = 0.4 × dv_ttm + 0.3 × (1/PE) + 0.3 × (1/PB)`

主通道取前 150 名 + 观察通道 50 名 → 共 200 只进入 Tier 2。

**Tier 2 — 深度分析（逐只调用 API）**

- 硬否决：质押比 > 70%、非标审计意见
- 财务质量：ROE ≥ 8%、毛利率 ≥ 15%、资产负债率 ≤ 70%
- 因子 2 指标：分配意愿 M、穿透率 R、Threshold II
- 因子 4 指标：EV/EBITDA、现金调整 PE、FCF 收益率、商誉占比
- 基准价：5 种方法（净流动资产/BVPS/10 年低点/股息隐含/悲观 FCF）取算术平均

**底价（Floor Price）— 5 种方法取算术平均**

底价是多维度安全边际锚点，综合物理资产、历史价格、分红能力和现金流生成能力：

| # | 方法 | 公式 | 说明 |
|---|------|------|------|
| 1 | 净流动资产/股 | (现金 + 交易资产 − 有息负债) / 总股数 | 清算视角的底线价值 |
| 2 | 每股净资产 (BVPS) | 归属股东权益(不含少数) / 总股数 | 账面价值锚点 |
| 3 | 10 年历史最低价 | 过去 10 年周收盘价最小值 | 历史极端情绪底部 |
| 4 | 分红折现价 | 近 3 年平均每股分红 / max(Rf, 3%) | 股息率等于折现率时的价格 |
| 5 | 悲观 FCF 资本化价 | 近 5 年最小 FCF / Rf% / 总股数 | 仅当 5 年 FCF 全为正时有效 |

复合基准取有效方法值的 **算术平均**。底价溢价率 = (当前价 / 基准价 − 1) × 100%。

| 溢价率 | 区间含义 |
|--------|----------|
| ≤ 0% | "买入就是胜利"区间 — 当前价低于底价 |
| 0–30% | 安全边际充足 |
| 30–80% | 合理溢价，需成长验证 |
| > 80% | 高溢价，需强成长预期支撑 |

**综合评分权重**

| 维度 | 权重 |
|------|------|
| ROE | 20% |
| FCF 收益率 | 20% |
| 穿透率 R | 25% |
| EV/EBITDA（逆序） | 15% |
| 基准价溢价（逆序） | 20% |

## 测试

```bash
# 运行全部测试
.venv/bin/python -m pytest tests/ -v

# 运行单个测试文件
.venv/bin/python -m pytest tests/test_screener.py -v

# 快速模式（遇到失败即停止）
.venv/bin/python -m pytest tests/ -x -q

# 查看覆盖率
.venv/bin/python -m pytest tests/ --cov=scripts --cov-report=term-missing
```

测试覆盖范围：
- 配置与工具函数（`test_config.py`, `test_format_utils.py`）
- Tushare 数据采集 + 衍生指标（`test_tushare_client.py`, `test_derived_metrics.py`）
- PDF 预处理（`test_pdf_preprocessor.py`）
- LLM 提示词验证（`test_phase1b_prompt.py`, `test_phase3_prompt.py`）
- 选股器全流程（`test_screener.py`）

所有测试使用 Mock 数据，不需要 Tushare Token 即可运行。

## 技术细节

### 数据单位约定

所有金额统一为 **百万元（RMB）**。Tushare 返回的原始数据（元）在采集时自动除以 1,000,000，并使用千分位格式化显示。

### 缓存策略

选股器使用 Parquet 格式的分层缓存：

| 数据类型 | 缓存 TTL |
|----------|----------|
| stock_basic（全量股票列表） | 7 天 |
| daily_basic（每日行情） | 当日有效 |
| Tier 2 财务数据（年报类） | 168 小时（7 天） |
| Tier 2 行情数据（周线） | 24 小时 |
| 全局数据（无风险利率） | 24 小时 |

缓存目录：`output/.screener_cache/`

### 速率限制

Tushare API 调用自动限速：每次请求间隔 ≥ 0.3 秒，失败自动重试（指数退避）。

## 开发指南

### 功能开发流程

1. 查看 `feature_list.json` 中下一个待实现的 feature
2. 按 feature 的 `steps` 数组顺序实现
3. 同步编写测试（不允许跳过测试）
4. 完成后在 `feature_list.json` 中标记 `passes: true`
5. 按规范提交 commit

### 提交规范

```
feat(category): description [feature #N]
fix(category): description [feature #N]
test(category): description [feature #N]
```

Category 对应 `feature_list.json` 中的分类：`infrastructure`, `phase1a_tushare`, `phase1b_websearch`, `phase2a_pdf_preprocess`, `phase3_analysis`, `screener` 等。

### 里程碑

| 标签 | 范围 | 状态 |
|------|------|------|
| v1.0-alpha | 基础设施 (#1–#8) | 已完成 |
| v1.0-beta | 全部脚本 (#1–#47) | 已完成 |
| v1.0-rc1 | 集成测试 (#1–#76) | 进行中 |
| v1.0 | 打包发布 (#1–#78) | 进行中 |

## 依赖

```
tushare>=1.2.89       # A 股数据接口
pandas>=1.5.0         # 数据处理
pdfplumber>=0.9.0     # PDF 文本提取
requests>=2.28.0      # HTTP 请求
pytest>=7.0.0         # 测试框架
pyarrow>=10.0.0       # Parquet 缓存
matplotlib>=3.5.0     # 可视化（Notebook）
tqdm>=4.60.0          # 进度条
jupyter>=1.0.0        # Notebook 运行环境
jinja2>=3.0.0         # HTML 模板（选股器导出）
```

## License

MIT
