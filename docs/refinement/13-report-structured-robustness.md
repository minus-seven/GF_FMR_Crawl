# 月报、半年报、年报结构化鲁棒性与纠错补正方案

> 状态：已落地第一版统一契约层（2026-11-03）
>
> 适用链路：下载 → 多基金切片 → MinerU/原生 PDF 解析 → 本地关键词预提 → 模型抽取 → 字段证据校验 → 纠错补正 → 数据库/UI。

## 1. 本轮目标

过去的结构化问题并非单一模型能力不足，而是“报告识别、版式识别、字段关键词、目标基金范围、数值校验”分散在不同层，导致同一份 PDF 在不同入口得到不同结论。本方案把这些规则收敛为一个报告契约，并继续保留管理人专用解析器作为补充。

已新增：

- `code/structured_data/report_contract.py`：统一关键词注册表、报告类型/语言/版式识别、截止日识别、模型提示约束和安全纠错闸门。
- `extract_fixed_income.py`：所有最终记录进入报告契约纠错；最终数据和 `.meta.json` 保存 `report_contract` 与 `correction_audit`。
- `money_market_prompt.md`、`fixed_income_prompt.md`：增加按月报/半年报/年报和 HTML/Markdown/OCR 版式的抽取规则。
- `platform/api/tests/test_report_contract.py`：覆盖繁体月报、英文半年报、年报、错误值清理和英文截止日。

## 2. 已覆盖的字段关键词

关键词均同时收录简体、繁体和英文变体，解析时先做 NFKC、空白归一化和安全 HTML 标签清理；`<1周`、`>3个月` 等期限比较符不会被误删。

| 字段 | 关键词族 | 典型来源 |
|---|---|---|
| 报告日期 | 截至、資料截至、As at、As of、For the six months/year ended | 三类报告标题、脚注、PDF Subject |
| 规模 | 基金规模、基金總值、Total Net Assets、AUM、Net Assets | 月报资料页、半年报摘要 |
| 成立日期 | 成立日期、類別成立日期、Launch Date、Inception Date | 月报基金资料、半年报基金资料 |
| 投资目标/策略 | 投资目标、投資目標、Investment objective；投资策略、投資策略、Investment strategy | 月报长文本卡片 |
| 收益率 | 到期收益率、到期孳息率、加权平均收益率、YTM、YTW、Yield to maturity/worst | 月报组合特点、半年报摘要 |
| 久期 | 组合久期、平均久期、加权平均存续期、Duration、Average duration | 月报组合特点、图表 |
| WAM/WAL | 加权平均到期日、平均屆滿期、Weighted average maturity/WAM；加权平均存续期、Weighted average life/WAL | 货币基金月报/半年报 |
| 期限/久期分布 | 久期分布、到期日分布、期限分布、Duration/Maturity distribution、Days to maturity | 月报图表 |
| 资产/地区分布 | 资产类别分布、資產分佈、Portfolio structure、Asset allocation；地区/国家/市场分布 | 月报图表、半年报组合表 |
| 持仓 | 前十大/前五大/五大主要/主要持仓、十大投资项目、Top holdings、Investment portfolio | 月报前五大、半年报投资组合表 |
| 半年报持仓附加列 | Holding/Nominal Value、Market/Fair Value、注册地、Listed/quoted、Unlisted/quoted、Fixed Income Securities、Debt Securities、Collective Investment Scheme | 英文半年报 HTML 表 |
| 货币基金流动性 | 每日/每周流动资产、Daily/Weekly liquid assets | 货币基金月报/半年报 |
| 货币基金收益 | 七日年化收益率、7-day yield、Annualized yield | 货币基金月报 |
| 派息与说明 | 派息说明、Dividend、Distribution、YTM 说明及“不代表业绩” | 月报长文本/派息表 |
| 银行敞口 | 银行敞口、银行存款敞口、Bank exposure、Bank deposits | 货币基金月报 |
| 股债比 | 股债比、權益/債券、Equity/Debt ratio | 半年报/年报持仓汇总；未披露不推定 |

## 3. 报告类型、语言和版式识别

### 3.1 类型优先级

1. 半年报/中期报告：`半年报`、`中期报告`、`Interim Report`、`Semi-annual`、`Half-year`。
2. 年报：`年报`、`年度报告`、`Annual Report`、`Year-end report`。
3. 月报：`月报`、`Monthly Report/Factsheet`；若没有显式“月报”，但有“YYYY年M月”且不是歧义性的 6 月/12 月半年/年报，则按月报。
4. 仍不确定：`supplementary`，不强行套用月报口径。

半年报优先于“annual”子串，避免 `semi-annual` 被误判为年报。报告类型结果写入 `report_contract.report_kind`。

### 3.2 语言

- `zh-Hans`：简体中文；`zh-Hant`：繁体中文；`en`：英文；`zh-Hans+en` / `zh-Hant+en`：双语；未能判断则 `unknown`。
- 原文长文本（投资目标、投资策略、YTM 说明）不翻译、不做繁简转换；数值标签可以归一化，持仓名称保留报告原文。

### 3.3 版式

- `html_table`：MinerU 输出 `<table>/<tr>/<td>`；比例可能只在表头出现，行值是裸数字。
- `markdown_table`：Markdown 管道表；按同一行/同一列配对。
- `native_text`：PDF 原生文字层；适用于 PowerPoint 导出的月报和 MinerU 失败回退。
- `image_ocr`：图表只有图片，通过 MinerU 图像或 macOS Vision OCR 补回。
- `mixed`：多种来源并存；结构化使用“同页/同行优先，跨层只作补充”。

## 4. 完整结构化流程

```text
下载与 SHA 校验
    ↓
报告类型/语言/版式识别 + 原文截止日识别
    ↓
半年报/年报：按基金名称、代码、中英文别名切片
    ↓
MinerU 精准解析；失败时保留 PDF 原生文字层
    ↓
本地关键词预提（日期、规模、WAM/WAL、久期、YTM、表格锚点）
    ↓
模型只抽取剩余复杂字段，并注入报告契约提示
    ↓
报告类型专用解析器：月报图表 / 半年报投资组合表 / 年报持仓汇总
    ↓
字段证据校验：标签、同页/同行、单位、比例合计、币种
    ↓
纠错补正：清理模板污染、重复值、越界值、错误分布、错误日期
    ↓
保留修正审计，状态为 ok / review / failed，最后落库
```

### 4.1 月报

先从“截至/資料截至/As at”锁定精确日期；再按以下顺序解析：

1. 基金资料：规模、币种、成立日期。
2. 组合特点：YTM/加权平均收益率、WAM/WAL、组合久期。
3. 流动性：每日/每周流动资产。
4. 图表：资产类别分布、久期/到期日分布；标签和值必须配对且完整合计约 100%。
5. 长文本：投资目标、投资策略、YTM 数据说明；单独存储并保留原语种。
6. 前五大/前十大持仓：名称、票面利率、到期日、比例；只保留报告中实际出现的项目。

对“数值在前、图例在后”和“图例在前、数值在后”两种 OCR 阅读顺序都尝试，但只有合计在 95%–105% 才接受。

### 4.2 半年报和年报

半年报/年报先按目标基金切片，再解析；不能把同一 PDF 中其他子基金的表格合并进当前基金。投资组合表支持：

- `Holding/Nominal Value` + `Market/Fair Value` + `% of net assets` 三列/四列变体；
- 比例只在表头出现、行内为裸数字；
- 分类行状态继承：`Listed/quoted investments`、`Unlisted/quoted investments`、`Fixed Income Securities`、`Debt Securities`、`Collective Investment Scheme`；
- 国家/注册地分组行状态继承到后续持仓；
- 以比例排序取前十大；保留份额/名义价值、市值、注册地、类型；
- 按持仓分类计算组合结构和股债比；若原文只有债券/货币市场工具，不把“货币市场工具”自动改写成普通债券。

如果报告是英文 HTML 表，优先表格行解析；如果是原生文字或图片，则进入 OCR/模型补提，但必须保留证据页和表格锚点。

## 5. 纠错补正规则

统一闸门位于 `correct_structured_record`，目前执行以下“宁空勿错”规则：

1. 原文有明确截止日时覆盖模型日期；优先精确到日，不用发布日期、成立日期或持仓到期日代替。
2. 久期/YTM 没有对应原文锚点时清空；禁止把收益率填进久期，或把久期图百分比填进 YTM。
3. WAM/WAL、日/周流动资产、七日年化收益率和年化收益率必须有各自标签；值越界或缺锚点时清空。
4. 持仓、分布项必须有名称和 0–100 比例；删除占位符（如 `Holding A`、`货币市场工具A`、`Example Bank`）、重复项和异常值；持仓最多 10 条。
5. 期限/资产/地区分布若是百分比，合计不在 95%–105% 时清空并标记复核；若是期权调整久期贡献（年），允许 0–30 年但不按 100% 解释。
6. 检测久期与 YTM 完全相同、疑似复用模板值时标记 `review`，交由文本/视觉二次核对，不静默保留为可信值。
7. 多基金半年报有投资组合表但目标基金没有持仓时标记 `multi_fund_portfolio_without_target_holdings`，阻止跨基金填充。
8. 所有修正写入 `disclosure_info.correction_audit`，包括 `issues`、`corrections`、修正计数和完整 `report_contract`。

## 6. 已记录问题与根因归纳

| 现象 | 根因 | 现在的处理 |
|---|---|---|
| BPJ877 报告期/图表需人工复核 | 模型把成立日期、持仓日期或图表数字当报告期/久期 | 截止日锚点优先；久期/YTM 互斥标签；分布合计校验 |
| BUE512 月报字段缺失或错位 | 标签与数字拆行、图表顺序变化，模板值被模型带入 | 原生文本和 HTML 行解析；无锚点清空；长文本按标题截断 |
| BQN919 投资策略变成超长串、WAM/收益率/分布缺失 | Markdown 块边界丢失、标题没有作为章节边界、图表值未配对 | 标题章节截取；关键词注册表；同页/同行配对；缺失走定向补提 |
| BTF715 成立日期、资产/到期分布、前五大缺失 | “Launch Date”英文标签、图表 OCR 方向和固定收益工具表格式未覆盖 | 英文成立日期、两种图表方向、固定收益工具行解析 |
| BSG677/BSG589/BNW892 半年报持仓错位 | 一份半年报包含多个基金；分类/注册地是分组行状态；比例只在表头 | 先切片；状态继承；三列/四列 HTML 表兼容；前十大排序 |
| BPJ877 半年报美元规模未换算 | 原始单位/币种和展示单位混在模型层 | 原始金额、币种、单位先落证据；代码层统一汇率换算 |
| BTE502 久期图/收益率混淆 | OCR 读序与“到期收益率”文字靠近，模型按邻近数字猜测 | 只接受明确 YTM 标签；图表须完整合计；不满足就留空 |
| 结构化成功但数值可疑 | Qwen/DeepSeek 复用同类基金模板；缺少原文存在性门控 | 原文锚点门控、范围校验、占位符清理、修正审计 |
| MinerU 失败后字段全部缺失 | 远程解析失败或 PDF 只有图片/原生文字层 | 保留 native PDF text fallback；货币基金追加原生层和 Vision OCR |

## 7. 运行与排查口径

单份结果：

- `*.money_market.json` / `*.fixed_income.json`：最终落库字段。
- `*.meta.json`：模型输出、调用决策、MinerU 路径、证据合同、`report_contract`、`correction_audit`、校验问题。

排查顺序：

1. 先看 `report_contract.report_kind/cutoff_date/language/layout/multi_fund` 是否正确。
2. 再看 `correction_audit.issues/corrections`，确认是“原文缺失”还是“候选被安全清空”。
3. 查看 `pdf_text_layer`、MinerU `markdown_path`、`content_list_path` 和 `field_evidence`，确认标签和数值是否同页/同行。
4. 如果是半年报，核对切片页范围和目标基金别名命中；不要直接在整份合并报告上重跑模型。
5. 如果是图片图表，确认 `[IMAGE_OCR]` 是否存在；无 OCR 证据时只能标记待复核，不能手填到通用解析器。

## 8. 后续建议

- 将 `correction_audit` 映射到“数据质量”看板，按报告类型、缺失字段、未锚定值和跨基金风险筛选。
- 把人工确认过的版式转为 `pipelines/*.json` 管理人契约，而不是继续增加基金 SHA 硬编码。
- 对月报、半年报、年报分别维护一组黄金样本，新增管理人先跑离线回归测试，再开放批量任务。
- 对无法通过原文锚点验证的字段保留 `null`/空数组，并在 UI 显示“未披露/待复核”，不要以模型置信度替代证据。
