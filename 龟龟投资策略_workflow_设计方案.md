# 龟龟投资策略 — Workflow 化 + Tushare 接入设计方案

> 目标：将 v0.15/v0.16_alpha 的纯 Prompt 驱动架构，升级为 **Python 脚本采集 + Prompt 分析** 的混合架构，用 Tushare Pro 替代 yfinance 作为 A 股/港股主数据源，PDF 解析改为 **Python 预处理 + Agent 精提取** 两段式。

---

## 一、为什么要改

### 当前架构的问题

| 问题 | 影响 | 根因 |
|------|------|------|
| yfinance 中国市场数据不准 | 归母净利润 vs 集团净利润混淆、支付率跨币种失真 | yfinance 不区分合并/母公司报表，中国特有科目缺失 |
| Agent 采集耗时太长 | Phase 1 需要 15-30 个工具调用，经常超时 | 每个数据项都要单独调 MCP + 等 AI 解析 |
| 数据格式不稳定 | Phase 3 经常因格式偏差报错 | Agent 生成的 markdown 表格格式不可控 |
| 单位问题 | 百万 vs 亿换算出错 | yfinance 输出单位不一致，需要人工标注 |

### Tushare 能解决什么

| Tushare 优势 | 对应解决的问题 |
|-------------|--------------|
| `comp_type=1` 参数 → 母公司单体报表 | 控股折价分析不再需要从 PDF 中手动提取 |
| `dividend` 端点含股息总额 | 支付率可直接用同币种数据计算，不依赖 DPS×股本反推 |
| `balancesheet` 含合同负债/预收款 | Phase 3 EV 口径计算直接可得 |
| Python 脚本统一输出 | 格式 100% 可控，单位统一为百万元，消除格式偏差 |
| 批量调用效率高 | 一次 API 调用获取 5 年数据，替代 5 次 MCP 调用 |

---

## 二、目标架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    Coordinator（Prompt）                           │
│  - 解析用户输入（AskUserQuestion）                                  │
│  - 调度 Phase 0/1/2/3                                             │
│  - 交付最终报告                                                    │
└───┬──────────────────┬────────────────────┬──────────────────────┘
    │                  │                    │
    ▼                  ▼                    ▼
┌────────────┐  ┌──────────────┐  ┌─────────────────────────────┐
│ Phase 0     │  │ Phase 1      │  │ Phase 2（混合，⭐ 重新设计）  │
│ ⭐ PDF 获取  │  │ 数据采集     │  │                             │
│             │  │              │  │  ┌────────────────────────┐ │
│ snowball-   │  │ ┌──────────┐ │  │  │ pdf_preprocessor.py    │ │
│ report-     │  │ │ tushare_ │ │  │  │ Python 预处理           │ │
│ downloader  │  │ │collector │ │  │  │ 关键词定位 5 个章节     │ │
│             │  │ │ .py      │ │  │  │ → pdf_sections.json    │ │
│ 自动搜索    │  │ └────┬─────┘ │  │  └──────────┬─────────────┘ │
│ 下载年报    │  │      │       │  │             │               │
│ PDF        │  │ ┌────▼─────┐ │  │  ┌──────────▼─────────────┐ │
│             │  │ │ Agent    │ │  │  │ Agent 精提取            │ │
│ 输出：      │  │ │WebSearch │ │  │  │ 5 段文本 → 结构化数据   │ │
│ annual_    │  │ │管理层/   │ │  │  │ → data_pack_report.md  │ │
│ report.pdf │  │ │行业/MD&A │ │  │  └──────────┬─────────────┘ │
│             │  │ └────┬─────┘ │  │             │               │
└──────┬──────┘  │      │       │  │  输出：data_pack_report.md │
       │         │ 输出：       │  │  (P2/P3/P4/P6/P13)        │
       │         │ data_pack_  │  └─────────────┬───────────────┘
       │         │ market.md   │                │
       │         └──────┬──────┘                │
       │                │                       │
       ▼                ▼                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  Phase 3（Prompt + 渐进式披露）                                    │
│  读取 data_pack_market.md + data_pack_report.md                   │
│  按需加载 references/ 因子参考文件                                   │
│  输出：分析报告.md                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**核心变化**：
1. ⭐ **Phase 0（新增）**：集成 `snowball-report-downloader` 插件，自动从雪球/同花顺搜索并下载年报 PDF，实现全自动化
2. Phase 1 从「纯 Agent 工具调用」变为「Python 脚本采集 + Agent 补充」两段式
3. Phase 2 从「纯 Agent 逐页阅读 PDF」变为「Python 预处理定位 + Agent 精提取」两段式
4. 所有金额单位统一为 **百万元**（Tushare 原始单位元 ÷ 1e6）

---

## 三、Phase 0 — PDF 自动获取（⭐ 集成 snowball-report-downloader）

### 3.0 设计思路

v0.15 中，用户需要手动下载年报 PDF 并上传。v1.0 集成已有的 `snowball-report-downloader` 插件，实现 PDF 的自动搜索和下载，使整个分析流程从「输入股票代码」到「输出报告」全程自动化。

### 3.1 `snowball-report-downloader` 能力

| 项 | 说明 |
|----|------|
| 数据源 | 雪球（stockn.xueqiu.com）为主，同花顺（notice.10jqka.com.cn）为备 |
| 支持市场 | 沪市 A 股（6xxxxx）、深市 A 股（0/3xxxxx）、港股（1-5 位数字） |
| 报告类型 | 年报、中报、一季报、三季报 |
| 工作方式 | Agent 通过 WebSearch 搜索 → 筛选 PDF 链接 → Python 脚本下载 |
| 输出 | 年报 PDF 文件（如 `600887_2024_annual_report.pdf`） |
| 校验 | 检查 PDF magic bytes 和文件大小，确保下载完整 |

### 3.2 在 v1.0 中的集成方式

```
用户输入"分析伊利股份"
    │
    ▼
Coordinator 解析输入 → 股票代码 600887，年份 2024（默认最新）
    │
    ▼
Phase 0：自动获取 PDF
    │
    ├─ 检查本地是否已有 PDF → 如有则跳过下载
    │
    ├─ 调用 snowball-report-downloader skill/command
    │   → WebSearch 搜索"伊利股份 2024 年报 PDF site:stockn.xueqiu.com"
    │   → 筛选正式年报链接（排除摘要、审计报告）
    │   → Python 下载 PDF 到工作目录
    │
    ├─ 下载成功 → 传递 PDF 路径给 Phase 2
    │
    └─ 下载失败 → 标注 Warning，Phase 2 跳过，进入无 PDF 模式（~85% 精度）
```

### 3.3 与现有插件的关系

`snowball-report-downloader` 作为**依赖插件**被 v1.0 项目引用，而非复制代码。两种集成方式：

| 环境 | 集成方式 |
|------|---------|
| Claude Code | `CLAUDE.md` 中声明依赖 `snowball-report-downloader`，通过 `/download-report` 命令调用 |
| Cowork | `plugin.json` 中声明依赖，Coordinator 通过 Skill 工具调用 `report-download` |

---

## 四、Phase 1 改造详细设计

### 4.1 拆分为两步

```
Phase 1 = Step A（Python 脚本，确定性数据）+ Step B（Agent 搜索，非结构化数据）
```

**Step A：`tushare_collector.py`**
- 输入：股票代码、Tushare token
- 输出：`data_pack_market.md` 的 §1-§6, §11, 附录A（结构化数据部分）
- 特点：100% 确定性，格式固定，单位统一，无 AI 参与

**Step B：Agent WebSearch 补充**
- 输入：Step A 的输出 + Phase 1 prompt 中 §7-§10 的搜索指令
- 输出：将 §7（管理层与治理）、§8（行业与竞争）、§10（MD&A）、§13（Warnings）追加到 `data_pack_market.md`
- 特点：需要 AI 理解和总结搜索结果，不可脚本化

### 4.2 `tushare_collector.py` 功能说明

#### 输入 / 输出

| 项 | 说明 |
|----|------|
| 输入 | 股票代码（如 `600887.SH`）、Tushare Token |
| 输出 | `data_pack_market.md` 的 §1-§6, §11, 附录A + `available_fields.json`（可用字段清单） |
| 单位 | 所有金额 ÷ 1e6 → 百万元，格式千位逗号分隔 |
| Fallback | Tushare 失败时降级调用 yfinance MCP，标注数据来源 |

#### 调用的 Tushare 端点

脚本按 Phase 3 分析需求，预选以下 Tushare 端点的**核心字段**输出到 data_pack。同时将各端点的**完整字段清单**写入 `available_fields.json`，供 Phase 3 按需追加。

| 端点 | 采集内容 | 输出章节 | 报表口径 | 积分 |
|------|---------|---------|---------|------|
| `stock_basic` + `daily_basic` | 公司名称、行业、市值、PE/PB、总股本、流通股本 | §1 基础信息, §2 市场数据 | — | 2000 |
| `income` | 营业收入、归母净利润、少数股东损益、财务费用、利息收入等 | §3 合并损益表, §3P 母公司损益表 | `report_type=1` 合并 + `report_type=4` 母公司 | 2000 |
| `balancesheet` | 总资产、总负债、现金、有息负债、合同负债、预收款、商誉、使用权资产等 | §4 合并资产负债表, §4P 母公司资产负债表 | `report_type=1` 合并 + `report_type=4` 母公司 | 2000 |
| `cashflow` | 经营/投资/筹资现金流、折旧摊销、资本开支、股息支付等 | §5 现金流量表 | `report_type=1` 合并 | 2000 |
| `dividend` | 每股股息、股息总额（`cash_div_tax`）、除权日、分红方案 | §6 股息历史 | — | 2000 |
| `weekly` | 10 年周线 OHLCV | §11 价格历史, 附录A | — | 500 |
| `fina_indicator` | ROE、毛利率、净利率、研发费用率、非经常性损益合计 | §12 关键财务指标（⭐ 新增） | — | 2000 |
| `fina_mainbz_ts` | 分部业务收入/利润构成 | §9 分部业务 | — | 2000 |
| `top10_holders` | 十大股东持股 | §7 股东信息 | — | 2000 |
| `fina_audit` | 审计意见类型 | §7 审计信息 | — | 2000 |

#### 数据选用策略：预处理为主 + LLM 可追加

```
┌─────────────────────────────────────────────────────────────┐
│  tushare_collector.py                                       │
│                                                             │
│  1. 按上表预选核心字段 → data_pack_market.md（~30 个字段）    │
│  2. 同时输出 available_fields.json：                         │
│     {                                                       │
│       "income": ["revenue", "n_income_attr_p", ...全部字段], │
│       "balancesheet": ["total_assets", ...全部字段],         │
│       "cashflow": [...全部字段],                             │
│       ...                                                   │
│     }                                                       │
│  3. 若 Tushare 端点失败 → 降级 yfinance → 标注来源          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 3 分析过程中                                          │
│                                                             │
│  正常路径：直接使用 data_pack_market.md 中的预选数据          │
│                                                             │
│  追加路径（当预选数据不足时）：                                │
│  1. Phase 3 读取 available_fields.json                       │
│  2. 判断需要哪些额外字段                                      │
│  3. 指示 Coordinator 调用脚本的 --extra-fields 参数追加拉取   │
│     例如：python3 tushare_collector.py --code 600887.SH     │
│           --extra-fields "balancesheet.defer_tax_assets,     │
│            income.operate_profit"                            │
│  4. 脚本追加输出到 data_pack_market_extra.md                 │
│  5. Phase 3 合并使用                                         │
└─────────────────────────────────────────────────────────────┘
```

这种设计的优势：预选覆盖 95% 的分析场景（确定性、快、省 token），剩余 5% 由 LLM 按需追加（灵活性），避免了「全拉全喂」导致的 token 浪费和不稳定。

### 4.3 输出格式统一

```markdown
# data_pack_market.md 输出规范（v1.0 Tushare 版）

所有金额单位：百万元（人民币）
所有价格单位：元（人民币）
所有股本单位：百万股
数据精度：金额保留2位小数，比率保留2位小数（百分比）

## 3. 五年损益表

> 数据来源：Tushare Pro `income` 端点
> 报表类型：合并报表（report_type=1）
> 单位：百万元（人民币）

| 科目 | 2020 | 2021 | 2022 | 2023 | 2024 |
|:-----|-----:|-----:|-----:|-----:|-----:|
| 营业收入 | 96,886.00 | 110,595.00 | 123,171.00 | 126,179.00 | 122,915.00 |
| ... | | | | | |

## 3P. 五年母公司损益表（⭐ 新增）

> 报表类型：母公司单体报表（report_type=4）
> 仅列出与合并口径差异显著的关键科目
> 单位：百万元（人民币）

| 科目 | 2020 | 2021 | 2022 | 2023 | 2024 |
|:-----|-----:|-----:|-----:|-----:|-----:|
| 营业收入 | ... | ... | ... | ... | ... |
| 归母净利润 | ... | ... | ... | ... | ... |
```

---

## 五、Phase 2 改造详细设计（⭐ 传统预处理 + Agent）

### 5.1 当前痛点

Phase 2 纯 Agent 处理 PDF 的问题：

| 问题 | 影响 |
|------|------|
| 200+ 页 PDF 导致 context 爆炸 | Agent 逐页阅读，经常 context compact |
| 处理太慢 | 15-30 分钟处理一份年报 |
| 格式不稳定 | 提取格式依赖 AI 理解，每次输出略有不同 |
| 提取项过多 | 原来 18 项，其中 13 项 Tushare 已覆盖或可砍 |

### 5.2 新方案：Python 预处理 + Agent 精提取

Phase 2 仅需提取 **5 项**（Tushare 无法覆盖的 footnote 级别数据）：

| 项 | 内容 | Phase 3 用途 |
|:---|:-----|:------------|
| P2 | 受限现金明细 | 因子3步骤8 现金质量 |
| P3 | 应收账款账龄 | 因子1B模块2 + 因子3步骤2 |
| P4 | 关联交易明细 | 因子1B模块6 治理 |
| P6 | 或有负债与承诺 | 因子3步骤6 表外风险 |
| P13 | 非经常性损益明细 | 因子3步骤3 分类 |

### 5.3 `pdf_preprocessor.py` 功能说明

#### 输入 / 输出

| 项 | 说明 |
|----|------|
| 输入 | 年报 PDF 文件路径 |
| 输出 | `pdf_sections.json`（5 段文本片段，总计约 5000-10000 字） |
| 依赖 | `pdfplumber`（Python PDF 提取库） |

#### 处理流程

```
PDF 文件（200+ 页）
    │
    ▼
Step 1：逐页提取纯文本
    pdfplumber 提取每页文本，保留页码索引
    │
    ▼
Step 2：关键词章节定位
    对 5 个目标项，用预定义关键词库搜索匹配页码
    每项支持：简体中文关键词 + 繁体中文关键词 + 英文关键词 + 备选关键词
    │
    ▼
Step 3：上下文提取
    命中页 ± 1 页（共 3 页）文本，截断 2000 字
    │
    ▼
Step 4：输出 JSON
    { "P2": "受限现金相关文本...", "P3": "账龄分析文本...", ... }
    未命中项输出 null
```

#### 关键词库设计

每项支持多语言多同义词匹配，确保覆盖 A 股（简体）和港股（繁体/英文）年报：

| 目标项 | 简体关键词 | 繁体/英文关键词 | 备选关键词 |
|--------|-----------|---------------|-----------|
| P2 受限现金 | 受限资产、受限制存款、抵押存款、质押存款 | Restricted, Pledged deposits | 现金及等价物 |
| P3 应收账龄 | 应收账款、账龄分析 | Trade receivables, Ageing analysis | — |
| P4 关联交易 | 关联方交易、关联交易 | Related party, Connected transaction | — |
| P6 或有负债 | 或有负债、或有事项、对外担保、重大诉讼、资本承诺 | Contingent, Commitments, Guarantees, Litigation | — |
| P13 非经常性损益 | 非经常性损益、其他收益、资产处置 | Non-recurring, Other income, Other gains | — |

#### 容错机制

| 场景 | 处理方式 |
|------|---------|
| 关键词未命中 | 该项返回 `null`，Agent 跳过并在 data_pack_report.md 标注 Warning |
| 表格跨页断裂 | 提取 ±1 页缓冲区（共 3 页），覆盖大部分跨页场景 |
| PDF 加密/扫描版 | 检测并报错，建议用户提供可复制文本的 PDF |
| 文本提取乱码 | 尝试 PyMuPDF 作为备选提取引擎 |

### 5.4 Agent 精提取（Step B）

Agent 仅需消费 `pdf_sections.json`（约 5000 字），而非 200 页 PDF：

```markdown
## Phase 2 Step B — Agent 提取指令

输入：pdf_sections.json（5 段文本片段）
输出：data_pack_report.md

对每个非 null 的片段，提取结构化数据：

### P2（受限现金）→ 输出表格
| 类别 | 金额（百万元） |
|------|------------|
| 质押存款 | xxx |
| 保证金存款 | xxx |
| 合计 | xxx |

### P3（应收账款账龄）→ 输出表格
| 账龄 | 金额（百万元） | 占比 |
|------|------------|------|
| 1年以内 | xxx | xx% |
| 1-2年 | xxx | xx% |
| 2-3年 | xxx | xx% |
| 3年以上 | xxx | xx% |

### P4（关联交易）→ 按交易类型列出前5大关联交易
### P6（或有负债）→ 列出所有担保/诉讼/承诺金额
### P13（非经常性损益）→ 按科目列出，单位百万元

若某段文本为 null，在 data_pack_report.md 对应章节标注：
> ⚠️ PDF 未找到相关章节，跳过此项
```

### 5.5 预估效果

| 指标 | 纯 Agent（v0.15） | 预处理 + Agent（新方案） |
|------|---------------|---------------------|
| PDF 处理时间 | 15-30 分钟 | 2-3 分钟 |
| Context 消耗 | ~100K tokens | ~10K tokens |
| Context compact 风险 | 高 | 极低 |
| 提取准确率 | ~80% | ~90%（定位更精准） |
| 提取项数 | 18 项 | 5 项（其余已被 Tushare 覆盖） |

---

## 六、对 Phase 3 的影响

**影响中等**。需要调整的地方：

| 调整项 | 原因 |
|--------|------|
| 单位换算逻辑统一 | 脚本输出统一为百万元，Phase 3 全程使用百万元，无需换算 |
| 母公司数据获取路径变更 | 从"仅 PDF 可得"变为"data_pack §3P/4P 已有" |
| 支付率计算简化 | 股息总额直接可得，不再需要 DPS×总股本反推 |
| 合同负债/预收款直接引用 | 不再需要从 PDF 提取或标注缺失 |
| PDF 数据减少为 5 项 | Phase 3 消费 data_pack_report.md 时仅读取 P2/P3/P4/P6/P13 |
| Warnings 消费逻辑 | 脚本自动生成的 Warnings 更结构化，Phase 3 解析更简单 |

---

## 七、兼容性设计（Claude Code + Cowork）

### 文件结构

```
turtle-investment-strategy/
├── CLAUDE.md                          ← Claude Code 入口
├── .claude/
│   ├── commands/
│   │   └── turtle-analysis.md         ← /turtle-analysis slash command
│   └── settings.local.json
├── scripts/
│   ├── tushare_collector.py           ← ⭐ Phase 1 Step A 数据采集（输出 data_pack + available_fields.json）
│   ├── pdf_preprocessor.py            ← ⭐ Phase 2 Step A PDF 预处理（输出 pdf_sections.json）
│   ├── config.py                      ← token 管理（从环境变量读取）
│   └── requirements.txt               ← tushare, pandas, pdfplumber
├── prompts/
│   ├── coordinator.md
│   ├── phase1_数据采集.md              ← 精简版：仅含 Step B 搜索指令
│   ├── phase2_PDF解析.md              ← ⭐ 精简版：仅含 Step B 5项精提取
│   ├── phase3_分析与报告.md            ← 精简执行器
│   └── references/
│       ├── factor1_资产质量与商业模式.md
│       ├── factor2_穿透回报率粗算.md
│       ├── factor3_穿透回报率精算.md
│       └── factor4_估值与安全边际.md
├── .claude-plugin/                    ← Cowork 插件结构
│   └── plugin.json
├── skills/
│   └── turtle-analysis/
│       └── SKILL.md
└── README.md
```

### 运行流程

**Claude Code 中**：
```bash
# 用户执行 /turtle-analysis 600887
# Phase 0：自动下载年报 PDF（调用 /download-report 600887 2024 年报）
#   → 输出 600887_2024_annual_report.pdf

# Phase 1 Step A：运行 Tushare 采集脚本
python3 scripts/tushare_collector.py --code 600887.SH --output ./data_pack_market.md

# Phase 1 Step B：Agent 执行 WebSearch 补充 §7-§10, §13

# Phase 2 Step A：运行 PDF 预处理脚本
python3 scripts/pdf_preprocessor.py --pdf ./600887_2024_annual_report.pdf --output ./pdf_sections.json

# Phase 2 Step B：Agent 读取 pdf_sections.json，精提取 5 项 → data_pack_report.md

# Phase 3：Agent 读取 data_pack_market.md + data_pack_report.md，执行分析
```

**Cowork 中**：
```
# 用户说"分析伊利股份"（无需手动上传 PDF）
# Coordinator 通过 AskUserQuestion 确认参数（股票代码、年份）
# Phase 0：调用 snowball-report-downloader Skill 自动下载年报 PDF
#   → 下载成功：传递 PDF 路径给 Phase 2
#   → 下载失败：进入无 PDF 模式（~85% 精度）
# Phase 1：调用 Bash 运行 tushare_collector.py → data_pack_market.md
#           调用 Task 启动 Phase 1 Step B（WebSearch）
# Phase 2：调用 Bash 运行 pdf_preprocessor.py → pdf_sections.json
#           调用 Task 启动 Phase 2 Step B（精提取）
# Phase 3：调用 Task 启动分析报告生成
```

**用户也可手动上传 PDF**（跳过 Phase 0 自动下载）。

### Token 管理

```python
# config.py
import os

TUSHARE_TOKEN = os.environ.get('TUSHARE_TOKEN', '')

# Claude Code：用户在 .bashrc 或 .zshrc 中设置
#   export TUSHARE_TOKEN='your_token_here'

# Cowork：用户首次运行时通过 AskUserQuestion 输入
#   → 存储到 workspace/.env 文件中
```

---

## 八、实施路线图

### Phase α：MVP — tushare_collector.py（1-2天）

```
目标：tushare_collector.py 能输出完整的 data_pack_market.md §1-§6, §11
单位：所有金额百万元（Tushare 元 ÷ 1e6）

任务：
1. 编写 tushare_collector.py 核心模块
   - get_basic_info() → §1, §2
   - get_income() → §3 (合并+母公司)
   - get_balance_sheet() → §4 (合并+母公司)
   - get_cashflow() → §5
   - get_dividends() → §6
   - get_weekly_prices() → §11, 附录A
2. 编写 to_markdown() 格式化输出（百万元，千位逗号分隔）
3. 用伊利股份(600887.SH)测试，对比 yfinance 输出差异

验收标准：输出的 data_pack_market.md 能被现有 Phase 3 prompt 正确消费
```

### Phase β：MVP — pdf_preprocessor.py（1天）

```
目标：pdf_preprocessor.py 能定位并提取 5 个目标章节

任务：
1. 编写 pdf_preprocessor.py
   - pdfplumber 逐页提取文本
   - SECTION_KEYWORDS 关键词匹配定位
   - 目标页 ± 1 页文本提取，截断 2000 字
2. 输出 pdf_sections.json
3. 用伊利股份 2024 年报测试，验证 5 项定位准确率

验收标准：5 项中至少 4 项能正确定位到目标章节
```

### Phase γ：集成（1天）

```
目标：将两个脚本接入 coordinator 工作流

任务：
1. 更新 coordinator.md：Phase 1 = 脚本 + Agent，Phase 2 = 脚本 + Agent
2. 精简 phase1_数据采集.md：删除 §1-§6 MCP 调用，保留 §7-§10 WebSearch
3. 重写 phase2_PDF解析.md：从 18 项精简为 5 项精提取指令
4. 更新 phase3_分析与报告.md：统一百万元、更新数据引用路径
5. 端到端测试（伊利 600887，有 PDF + 无 PDF 两种模式）
```

### Phase δ：港股支持（1天）

```
目标：支持港股标的（如 0001.HK 长和）

任务：
1. tushare_collector.py 添加 HK 股票支持
   - ts_code 格式：00001.HK
   - 港币报表 → 标注汇率，金额仍输出百万元
   - 税率自动判断
2. pdf_preprocessor.py 添加繁体中文/英文关键词兼容
3. 测试港股标的
```

### Phase ε：打包发布（0.5天）

```
目标：同时发布 Claude Code skill + Cowork plugin

任务：
1. 更新 CLAUDE.md / SKILL.md
2. claude plugin validate && claude plugin pack
3. 更新 README.md 使用说明
4. 推送 GitHub
```

---

## 九、风险与降级方案

| 风险 | 概率 | 降级方案 |
|------|------|---------|
| Tushare 某端点返回空数据 | 中 | 脚本内置 fallback：该字段标注 `⚠️ Tushare无数据`，Phase 1 Step B 用 WebSearch 补充 |
| Tushare 港股数据不全 | 中 | 港股降级使用 yfinance MCP（保留原有 Phase 1 prompt 作为备用路径） |
| 积分不够调用某端点 | 低（用户已 ≥2000） | 脚本检测权限错误，自动跳过并标注 |
| Python 环境缺失（Cowork） | 低 | Cowork VM 预装 Python3，pip install tushare pdfplumber 即可 |
| Tushare API 频率限制 | 低 | 脚本内置 time.sleep(0.3) 节流 |
| PDF 自动下载失败 | 中 | 雪球/同花顺链接失效或反爬 → 提示用户手动上传 PDF，或进入无 PDF 模式 |
| PDF 关键词未命中 | 中 | pdf_preprocessor.py 对该项返回 null，Agent 跳过并标注 Warning |
| PDF 表格跨页断裂 | 中 | pdfplumber 逐页提取，跨页表格可能不完整 → 提取 ±1 页缓冲 |

---

## 十、关键决策点（全部已确认 ✅）

| # | 决策项 | 结论 | 状态 |
|---|--------|------|------|
| 1 | 是否保留 yfinance 作为备用？ | **保留**，Tushare 缺失时 fallback（尤其港股） | ✅ 已确认 |
| 2 | 增加母公司报表章节 §3P/§4P？ | **增加**，Tushare `report_type=4` 直接获取 | ✅ 已确认 |
| 3 | 单位统一标准 | **百万元**（Tushare 元 ÷ 1e6） | ✅ 已确认 |
| 4 | PDF 是否保留 | **保留**，但改为预处理 + Agent，仅提取 5 项 | ✅ 已确认 |
| 5 | 版本号命名 | **v1.0** — 标志从 Prompt-only 到 Workflow 的架构升级 | ✅ 已确认 |
| 6 | 集成 snowball-report-downloader | **集成**，作为依赖插件实现 PDF 自动获取 | ✅ 已确认 |

---

## 十一、项目目标与运行平台

### 11.1 项目最终实现目标

**龟龟投资策略 v1.0** 是一个 AI 辅助的上市公司基本面分析工具，目标是帮助价值投资者在 10-15 分钟内完成一家公司的深度基本面评估，输出一份结构化的投资分析报告。

**核心能力**：

| 能力 | 说明 |
|------|------|
| ⭐ 全自动化 | 用户仅需输入股票代码，年报 PDF 自动从雪球/同花顺下载，数据自动采集，分析自动执行 |
| 自动数据采集 | Python 脚本调用 Tushare Pro API 获取 5 年财务数据（合并+母公司），Agent 补充管理层/行业/MD&A 等非结构化信息 |
| PDF 自动获取 | 集成 `snowball-report-downloader` 插件，自动搜索并下载年报 PDF，用户也可手动上传 |
| PDF 年报解析 | Python 预处理定位关键章节 + Agent 精提取 5 项 footnote 级别数据（受限现金、AR 账龄、关联交易、或有负债、非经常性损益） |
| 四因子分析框架 | 因子1 资产质量与商业模式（含 6 项一票否决）、因子2 穿透回报率粗算、因子3 穿透回报率精算（含收入敏感性分析）、因子4 估值与安全边际（含仓位矩阵） |
| 结构化报告输出 | 包含否决检查、评分、关键指标、风险点、买入触发价的完整分析报告 |
| 双数据源冗余 | Tushare Pro 为主，yfinance 为备用 fallback |
| 优雅降级 | PDF 下载或解析失败时自动降级为无 PDF 模式（~85% 精度），不阻塞流程 |

**支持市场**：A 股（沪深）、港股（Phase δ 扩展）

**输入**：股票代码 + Tushare Token（PDF 自动获取，无需手动上传）
**输出**：`分析报告.md`（含因子评分、关键指标、风险警示、买入触发价）

### 11.2 运行平台

项目同时支持两个运行环境，共享同一套代码和 Prompt：

| 平台 | 入口 | 运行方式 | 适合用户 |
|------|------|---------|---------|
| **Claude Code**（终端） | `/turtle-analysis 600887` slash command | 用户在终端执行，脚本和 Agent 自动串联 | 开发者、技术用户 |
| **Cowork**（桌面 App） | 自然语言"分析伊利股份"+ 可选上传 PDF | Coordinator 通过 AskUserQuestion 引导，Bash 调脚本 + Task 调 Agent | 非技术用户、投资者 |

**环境要求**：

| 依赖项 | Claude Code | Cowork |
|--------|------------|--------|
| Python 3.8+ | 用户本地安装 | VM 预装 |
| tushare, pandas | `pip install` | 首次运行自动安装 |
| pdfplumber | `pip install`（PDF 模式） | 首次运行自动安装 |
| Tushare Token | 环境变量 `TUSHARE_TOKEN` | AskUserQuestion 引导 → `.env` |
| yfinance MCP | Claude Code 内置 | Cowork 内置 |
| snowball-report-downloader | 依赖插件（`/download-report`） | 依赖插件（Skill 调用） |

**发布形式**：

| 形式 | 文件 | 说明 |
|------|------|------|
| Claude Code Skill | `CLAUDE.md` + `.claude/commands/turtle-analysis.md` | 通过 `/turtle-analysis` 命令触发 |
| Cowork Plugin | `.claude-plugin/plugin.json` + `skills/turtle-analysis/SKILL.md` | 通过自然语言或技能触发 |
| GitHub Repo | `turtle-investment-strategy/` | 完整项目代码，可 clone 使用 |

---

*龟龟投资策略 v1.0 | Workflow + Tushare 设计方案 | 2026-03-06*
