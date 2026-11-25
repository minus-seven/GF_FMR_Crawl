# Case 01 开发实现说明：货币基金流水线

## 1. 范围与口径

- 主数据：`HKEX基金综合资料库_完整_20261008.xlsx`，权威过滤条件为 `资产类别一级分类 == 货币`；中资过滤条件为 `管理人中外资 == 中资`。
- 规模：`2026-08-31规模（亿港币）`，单位为亿港币；不要将基金基础货币误作规模换算币种。
- 研究排名：中资集合按快照规模降序；识别广发 BTE502 后向上取 5 的倍数，生成前 15 名。
- 报告/证据：本地 PDF、MinerU `full.md`、结构化 JSON/Excel 各自记录来源；找不到时写“缺失”，不回填邻近报告。

## 2. 已吸收到项目的代码

1. `code/batch_layout.py`：把 `货币` 加入标准批次类别，保证 factsheet/markdown/structure_data 目录发现一致。
2. `platform/api/app/worker/adapters.py`：将 `货币` 加入结构化白名单，任务不再因为类别未支持而跳过。
3. `code/run_all_structured_data.py`：货币类别单独建立 pipeline，使用 `money_market_prompt.md`、`money_market` 工作目录和 `货币_基金提取结果` 输出；与固收 pipeline 并行，避免原先“多个 fixed 类别只取第一个”的遗漏。
4. `code/run_all_fixed_income.py` / `code/structured_data/run_batch.py` / `extract_fixed_income.py`：增加 `--category`、`--prompt`、`--output-suffix`，复用稳定的 MinerU/本地模型/证据契约，而不复制一套抽取器。
5. `code/structured_data/extract_fixed_income.py`：新增可选 `money_market_metrics`，包括七日/年化收益率、WAM、WAL、每日/每周流动资产、资产类型和银行敞口；旧固收记录仍可为 null。
6. `combine_fixed_income.py`：汇总货币指标和“货币资产类型”sheet/CSV；`platform/api/app/importers/service.py` 将标量指标和资产类型导入通用 FundMetric/FundAllocation，知识库 Agent 可按报告期追溯。
7. `platform/web/src/pages/FundsPage.tsx`：增加“货币市场基金”快捷筛选，仍以 HKEX 一级分类为事实源。`TasksPage.tsx`：货币类别出现在任务下拉；数据库快照尚未导入时保留待导入提示。

## 3. 数据流

```text
HKEX 主数据
  -> category=货币 / manager_origin=中资
  -> FundsPage 快捷筛选 或 TasksPage 按资产类别
  -> download -> parse(MinerU) -> structure
  -> money_market_prompt.md + local-first(category=money)
  -> per_pdf/*.money_market.json
  -> 货币_基金提取结果_combined.json / xlsx / CSV
  -> D1ImportService FundMetric/FundHolding/FundAllocation
  -> 基金详情与知识库 Agent（报告期、来源文档、证据链）
```

`money_market_metrics` 是可选字段，避免破坏既有固收 JSON；提示词明确 WAM/WAL、收益率、流动性和持仓关键词只在原文披露时填写。前十大持仓仍进入统一 `FundHolding`，资产类型和银行敞口进入 allocation dimension=`asset_type`/`bank_exposure`。

## 4. 货币基金专用口径

- 收益率：七日年化、三十日/年度化等字段分开，不把累计回报或派息率替代七日年化。
- 流动性：WAM/WAL 与每日/每周流动资产是不同概念；WAM/WAL 以天为单位，流动资产以百分比为单位。
- 银行与资产类型：`bank_exposure` 只接受报告明确的银行发行人/存款敞口；前十大持仓保留完整证券名称，后续可按银行词典和发行人类型分类。
- 股债比：货币市场工具、存款、回购、现金不能被强制归为普通股票/债券；没有清晰披露时返回“未披露”。
- 半年报：月报通道可以先提供收益率/流动性/持仓线索，但半年报必须保存独立 source document，并在报告中标明报告期。

## 5. 本地数据和联网缺口

- 本地发现：`data/` 下多个批次已有货币 PDF/Markdown；前 15 组的覆盖和路径由 `01-money-market-top15.csv` 固化。
- 缺失文件处理：已下载 `case/sources/money-market/BQN919_PingAn_MoneyMarket_Interim-Report-EN.pdf`（2022H2）和 `BTE097_Bosera_HKD_MoneyMarketETF_Interim-2024-06-30.pdf`（2024H1），并从 HKEX 官方接口保存 BTE502 的财务报告索引 JSON（2026-06-30）。BTE502 文件流连续返回 HTTP 500，故未把索引记录伪装为 PDF；三者的来源、报告期和缺口均写入 `01-money-market-comparison.csv`。
- 监管：2024 UT Handbook 的 WAM/WAL/397 天口径、24EC64 流动性通函、23EC49 年化回报通函及 2026 修订过渡安排均已在客户报告给出官方链接；正式合规判断需以当期规则文本为准。

## 6. 本次运行记录

- Ollama `qwen3:4b` 已在 `127.0.0.1:11434` 运行；API、PostgreSQL、Worker 均已启动。
- API 创建任务：`75a732b1-383d-4a92-9741-a21f9a353749`，范围 `货币`，输入任务 `39fa6155-3446-44c4-ad9b-ab0210305814`，49 份已有 MinerU 缓存，结构化 provider=`local`，primary/fallback=`qwen3:4b`，vision 为空。
- 运行日志确认 `--primary-model qwen3:4b --fallback-model qwen3:4b`，并由环境变量固定 `FACTSHEET_STRUCTURED_PROVIDER=local`，不会回落到全局 DeepSeek 配置。首轮 API 任务曾因 3 份 JSON 不完整返回退出码 2；将 `evidence_contract.py` 的 `json_repair` 预算从 500 提高到 1800 后，本地重跑最终达到 49/49，逐 PDF `meta.json` 全部为 `review`。

## 7. 验证方式

- 已执行 `$FACTSHEET_VENV_ROOT/bin/python -m py_compile` 检查所有修改的 Python 模块；货币流水线相关回归测试此前为 44 passed。
- 本地 Qwen 结果允许 `review`，但比较表脚本会检查 `field_evidence` 是否只有标题块；只有有具体页内证据的值才可升级为客户结论。
- 已检查最终 `structured_run_summary_20261102_043433.json`：货币 stage 成功 49、失败 0；本地 `qwen3:4b` 共 101 次调用、总 324,746 token；比较表脚本仍会把模型结果标为 `review`。`review` 只代表模型完成候选抽取，不代表页内证据已人工审核通过。

## 8. 可供项目继续吸收的经验

- “分类”应由 HKEX 主数据枚举驱动，UI 快捷入口只是别名；不要通过基金名称关键字判定货币基金。
- 每个资产类别应拥有明确的 prompt/schema/output stem，但尽量复用同一证据契约、模型账户熔断、MinerU 缓存和导入器。
- 结构化字段必须带报告期、原文证据和来源文件；知识库 Agent 的答案只能引用这些节点，并把“未披露”和“本地缺失”分开。
- 市场容量、收益率和监管口径是三类不同事实：分别来自 HKEX 快照、基金报告/事实资料、SFC 规则；报告必须显示来源和快照日期。
