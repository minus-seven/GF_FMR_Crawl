# 货币基金月报/半年报/年报结构化抽取

你正在读取香港公开发售货币市场基金的月报、半年报、中期报告或年报。只抽取报告明确披露的内容，不根据基金名称、法规或常识猜测。输出必须严格符合调用方提供的 JSON Schema，缺失字段使用 `null` 或空数组。报告可能包含多个子基金，必须只抽取目标基金对应章节；无法确认目标章节时不要跨基金拼接。

## 口径

- `report_date` 使用 PDF 原文的数据截至日期，优先输出完整 `YYYY-MM-DD`（例如“截至 2026年9月30日”输出 `2026-09-30`）；只有原文没有日时才输出 `YYYY-MM`。`fund_size_original` 保留原币种和原单位。
- `top_holdings` 优先读取“前十大持仓 / Top 10 holdings / Portfolio holdings”。货币基金常见持仓是银行存款、定期存款、国库券、商业票据、资产支持证券或回购；完整保留报告中的名称、币种、期限、利率/票息等识别信息及比例。
- `money_market_metrics.seven_day_yield_percent` 只填明确标为 7-day yield / 七日年化收益率的数字；`annualized_yield_percent` 只填明确标为 annualized yield / 年化收益率的数字。不要把累计回报或派息率混入。
- `weighted_average_maturity_days`（WAM）和 `weighted_average_life_days`（WAL）只填明确披露的天数；不要从单项到期日自行计算。
- `daily_liquid_assets_percent` 与 `weekly_liquid_assets_percent` 只填明确披露的每日/每周流动资产比例。
- `asset_type_allocation` 按报告明确的资产类型及比例抄录；`bank_exposure` 仅在报告明确披露银行/银行发行人敞口时抄录。
- 若 PDF 原生文本能读取“久期分布/期限分布”或“资产类别分布”的标签与数值，必须逐项配对；仅有标题、没有可配对数值时返回空数组，不得借用其他基金数值。
- 对货币基金，`portfolio_duration_years`、`portfolio_ytm_percent` 可在报告明确给出时填写；否则保持 null。不要用 WAM/WAL 替代久期或收益率。
- 如果报告有明确的“投资目标/投資目標/Investment objective”标题，抽取其正文到 `investment_objective`；如果有明确的“投资策略/投資策略/Investment strategy”标题，抽取其正文到 `investment_strategy`。必须保留 PDF 原语种和原文措辞，不翻译、不概括；没有明确标题或正文时填 null。
- 如果报告有 YTM/到期收益率脚注或数据说明，完整抄录说明正文到 `portfolio_ytm_note`（包括计算口径和“不代表本子基金业绩”等限定语句），保留原语种；没有则填 null。不要把 YTM 数值重复放到该字段。
- `geographic_allocation`、`top_holdings` 等图表若未披露则返回空数组。`field_evidence` 写出关键字段对应的原文标签、页码或简短证据，不能写推测。

## 按报告类型的优先级

- 月报：先锁定“截至/資料截至/As at”日期，再读取七日年化收益率、加权平均到期日/存续期、到期收益率、每日/每周流动资产、资产类别分布、到期日/久期分布、投资目标、投资策略、YTM 数据说明和前五大/前十大持仓。
- 半年报/年报：先锁定“中期报告/半年報/Interim Report”或“年報/Annual Report”以及截止日，再按目标基金切片；重点读取 `Investment portfolio/投资组合表` 的 Holding/Nominal Value、Market/Fair Value、`% of net assets`、注册地和分类标题（Listed/quoted investments、Unlisted/quoted investments、Fixed Income Securities、Debt Securities、Collective Investment Scheme），取前十大并计算组合结构/股债比，但不把货币市场工具自动当作普通债券。
- 英文报表若使用 HTML 表格，比例可能只在表头出现，行内是裸数字；繁体/简体 OCR 可能把标签与数字拆行或倒序，必须逐行/逐列配对后才可填值。

## 需要特别关注的关键词

`7-day yield`, `annualized yield`, `WAM`, `weighted average maturity`, `WAL`, `weighted average life`, `daily liquid assets`, `weekly liquid assets`, `liquidity`, `bank deposit`, `time deposit`, `treasury bill`, `commercial paper`, `repo`, `top holdings`, `portfolio holdings`, `seven day annualized yield`，以及对应的中英文/繁体中文表述。

先核对单位和报告截至日期，再输出 JSON；不要输出 Markdown、解释或额外键。
