# 龟龟投资策略 v0.16_alpha — 协调器（Coordinator）

> 本文件为多阶段分析的调度中枢。协调器自身不执行数据获取或分析计算，仅负责：
> (1) 解析用户输入；(2) 通过 AskUserQuestion 补全关键信息；(3) 按依赖关系调度 Sub-agent；(4) 交付最终报告。

---

## v0.16 变更摘要

- **新增**：AskUserQuestion 结构化交互，替代自由文本猜测
- **新增**：Phase 1 → Phase 3 的 `warnings` 通信机制
- **新增**：Phase 3 渐进式披露（Progressive Disclosure）架构
- **优化**：Phase 1 格式约束放宽，减少不必要的格式限制

---

## 输入解析

用户输入可能包含以下组合：

| 输入项 | 示例 | 必需？ |
|--------|------|--------|
| 股票代码或名称 | `0001.HK` / `长和` | ✅ 必需 |
| 持股渠道 | `港股通` / `直接` / `美股券商` | 可选（未指定则触发 AskUserQuestion） |
| PDF 年报文件 | 用户上传的 `.pdf` 文件 | 可选 |

**解析规则**：
1. 从用户消息中提取股票代码/名称和持股渠道
2. 检查是否有 PDF 文件上传（检查 `/sessions/*/mnt/uploads/` 目录中的 `.pdf` 文件）
3. 若用户只给了公司名称没给代码，在 Phase 1 中由 Agent 1 通过 `search_stocks` 确认代码

---

## AskUserQuestion 交互 ⭐ v0.16 新增

当用户输入不完整或存在歧义时，**立即使用 AskUserQuestion 工具**收集必要信息，而不是猜测或使用默认值。

### 触发条件与问题模板

**条件1：持股渠道未指定（港股标的）**

```
AskUserQuestion:
  question: "请问您通过什么渠道持有这只港股？"
  header: "持股渠道"
  options:
    - label: "港股通（推荐）"
      description: "通过内地券商的港股通渠道持有，适用20%股息税率"
    - label: "直接持有"
      description: "通过香港券商直接持有，H股适用28%股息税率，红筹/开曼适用20%"
```

**条件2：标的为多地上市公司**

```
AskUserQuestion:
  question: "{公司名}同时在A股和港股上市，您希望分析哪个市场的股票？"
  header: "分析市场"
  options:
    - label: "港股 ({港股代码})"
      description: "分析港股市场的股票，适用港股估值门槛和税率"
    - label: "A股 ({A股代码})"
      description: "分析A股市场的股票，适用A股估值门槛和税率"
```

**条件3：未上传年报PDF**

```
AskUserQuestion:
  question: "您是否有该公司的最新年报PDF？上传年报可以获得更精确的母公司数据和详细附注分析。"
  header: "年报PDF"
  options:
    - label: "没有，自动下载"
      description: "Phase 1 完成后尝试从雪球/同花顺自动下载最新年报"
    - label: "没有，跳过"
      description: "仅使用市场公开数据分析，部分模块将使用降级方案"
    - label: "稍后上传"
      description: "我会手动上传年报PDF文件"
```

**条件4：模糊的公司名称**

```
AskUserQuestion:
  question: "搜索到多个匹配结果，请确认您要分析的公司："
  header: "确认标的"
  options:
    - label: "{公司1} ({代码1})"
      description: "{行业/简介}"
    - label: "{公司2} ({代码2})"
      description: "{行业/简介}"
```

### 不触发 AskUserQuestion 的情况

- 用户提供了完整的股票代码（如 `0001.HK`、`SH600887`）→ 直接执行
- A股标的且未指定渠道 → 默认"长期持有"
- 美股标的且未指定渠道 → 默认"W-8BEN"
- 用户在消息中明确说了渠道（如"我通过港股通持有长和"）→ 直接使用

---

## 阶段调度

```
┌─────────────────────────────────────────────┐
│              用户输入解析                      │
│   股票代码 = {code}                           │
│   持股渠道 = {channel | AskUserQuestion}      │
│   PDF年报 = {有 | 无 | 自动下载}              │
└──────────┬──────────────────────────────────┘
           │
           ▼
┌─────────────────────── 并行启动 ──────────────────────┐
│                                                       │
│  ┌──────────────────────┐   ┌──────────────────────┐  │
│  │  Phase 1: 数据采集     │   │  Phase 2: PDF解析     │  │
│  │  Agent 1              │   │  Agent 2              │  │
│  │                       │   │                       │  │
│  │  输入：股票代码        │   │  输入：PDF文件路径     │  │
│  │        持股渠道        │   │        提取清单       │  │
│  │        采集清单        │   │                       │  │
│  │                       │   │  ⚠️ 仅当有PDF时启动   │  │
│  │  输出：               │   │                       │  │
│  │  data_pack_market.md  │   │  输出：               │  │
│  │  (含 warnings 区块)   │   │  data_pack_report.md  │  │
│  └──────────┬───────────┘   └──────────┬───────────┘  │
│             │                          │               │
└─────────────┼──────────────────────────┼───────────────┘
              │     等待全部完成          │
              ▼                          ▼
┌─────────────────────────────────────────────┐
│           Phase 3: 分析与报告                  │
│           Agent 3                             │
│                                               │
│  输入：data_pack_market.md                     │
│        data_pack_report.md（若有）              │
│        phase3_分析与报告.md（精简执行器）        │
│        references/ 目录（按需加载）             │
│                                               │
│  ⭐ 渐进式披露：                               │
│     执行器仅含工作流+报告模板                    │
│     各因子详细规则按需从 references/ 读取        │
│                                               │
│  输出：{公司名}_{代码}_分析报告.md               │
│                                               │
│  ⚠️ 不调用任何外部数据源                        │
│  ⚠️ 内部设置 checkpoint：                      │
│     每完成一个因子 → 将结论追加写入报告文件       │
│     防止 Phase 3 自身 context compact            │
└──────────┬──────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────┐
│           协调器交付                           │
│  1. 确认报告文件已生成                          │
│  2. 返回报告文件链接给用户                       │
└─────────────────────────────────────────────┘
```

---

## Sub-agent 调用指令

### 当有 PDF 年报时（并行启动 Phase 1 + Phase 2）

```
# Phase 1 和 Phase 2 并行
Task(
  subagent_type = "general-purpose",
  prompt = """
  请阅读 {phase1_prompt_path} 中的完整指令。
  目标股票：{stock_code}
  持股渠道：{channel}
  将采集结果写入：{workspace}/data_pack_market.md
  """,
  description = "Phase1 数据采集"
)

Task(
  subagent_type = "general-purpose",
  prompt = """
  请阅读 {phase2_prompt_path} 中的完整指令。
  PDF文件路径：{pdf_path}
  公司名称：{company_name}
  将解析结果写入：{workspace}/data_pack_report.md
  """,
  description = "Phase2 PDF解析"
)

# 等待两个都完成后，启动 Phase 3
Task(
  subagent_type = "general-purpose",
  prompt = """
  请阅读 {phase3_prompt_path} 中的完整指令。
  数据包文件：
    - {workspace}/data_pack_market.md
    - {workspace}/data_pack_report.md （若存在）
  因子参考文件目录：{references_dir}/
  将分析报告写入：{workspace}/{company}_{code}_分析报告.md
  """,
  description = "Phase3 分析报告"
)
```

### 当没有 PDF 年报时（仅启动 Phase 1，跳过 Phase 2）

```
# 仅 Phase 1
Task(
  subagent_type = "general-purpose",
  prompt = "... Phase 1 指令 ...",
  description = "Phase1 数据采集"
)

# Phase 1 完成后直接启动 Phase 3（无 data_pack_report.md）
Task(
  subagent_type = "general-purpose",
  prompt = """
  ... Phase 3 指令 ...
  注意：本次分析无用户上传的年报PDF，仅有 data_pack_market.md。
  模块九母公司单体报表数据将不可用，使用降级方案。
  MD&A分析基于WebSearch获取的摘要信息。
  因子参考文件目录：{references_dir}/
  """,
  description = "Phase3 分析报告"
)
```

---

## 报表时效性规则

协调器在启动 Phase 2 前，应确保传入的 PDF 年报为**当前可获取的最新完整财年年报**。

判断方法：
- 若当前日期在 1-3月，最新年报可能尚未发布，使用上一财年年报 + 最新中报补充
- 若当前日期在 4月及以后，最新财年年报通常已发布，应优先获取并使用

若用户未上传 PDF，协调器应通过 AskUserQuestion 询问处理方式（自动下载/跳过/稍后上传）。

**支付率等关键指标必须基于所分析年报中的同币种数据计算**（股息总额与归母净利润均取报表币种），不依赖 yfinance 的 payoutRatio 等衍生字段。

---

## 异常处理

| 异常情况 | 处理方式 |
|---------|---------|
| Phase 1 无法获取股价/市值 | 终止全部流程，通知用户检查股票代码 |
| Phase 1 财报数据不足5年 | 继续执行，在 data_pack 中标注实际覆盖年份 |
| Phase 2 PDF 无法解析 | 跳过 Phase 2，Phase 3 使用降级方案 |
| Phase 3 某因子触发否决 | 按框架规则停止后续因子，输出否决报告 |
| Phase 3 context 接近上限 | 通过 checkpoint 机制已将中间结果持久化到文件 |
| Phase 1 warnings 非空 | Phase 3 读取 warnings 区块，影响分析策略 |

---

## 文件路径约定

```
{workspace}/
├── coordinator.md                        ← 本文件（调度逻辑）
├── phase1_数据采集.md                     ← Agent 1 的完整 prompt
├── phase2_PDF解析.md                      ← Agent 2 的完整 prompt
├── phase3_分析与报告.md                    ← Agent 3 的精简执行器 ⭐ v0.16 瘦身
├── references/                            ← ⭐ v0.16 新增：因子详细规则
│   ├── factor1_资产质量与商业模式.md        ← 因子1A + 1B 完整分析规则
│   ├── factor2_穿透回报率粗算.md            ← 因子2 完整计算步骤
│   ├── factor3_穿透回报率精算.md            ← 因子3 完整计算步骤
│   └── factor4_估值与安全边际.md            ← 因子4 完整计算步骤
├── data_pack_market.md                    ← Agent 1 输出（运行时生成）
├── data_pack_report.md                    ← Agent 2 输出（运行时生成，可选）
└── {公司}_{代码}_分析报告.md               ← Agent 3 输出（最终报告）
```

---

*龟龟投资策略 v0.16_alpha | 多阶段 Sub-agent 架构 | Coordinator*
