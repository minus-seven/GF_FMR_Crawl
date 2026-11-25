# 富兰克林邓普顿香港月报适配器接入记录

日期：2026-11-24  
依据：`agents/新公司接入SOP.md`

## 结论

已完成“富兰克林邓普顿”页表、官方文档目录适配器、下载器和调度器注册。HKEX 主数据中按管理人简称精确筛得 84 只基金；富兰克林香港官方 literature API 当前返回 75 份 Factsheet，其中 74 份与这 84 只基金强匹配，10 只暂未找到可验证的当前 Factsheet（月报）。正式工作簿的 I/J 按 SOP 阶段 4 保持为空，供 GUI“爬虫补全”验收时回填。

## 官方来源与运行时策略

- 文档入口：<https://www.franklintempleton.com.hk/en-hk/our-funds/literature/fund-documents>
- 财报入口（用于后续半年报/年报通道）：<https://www.franklintempleton.com.hk/en-hk/our-funds/literature/financial-reports>
- 运行时目录接口：`https://www.franklintempleton.com.hk/api/literature/v1/documents?channel=en-hk&cache=true&audience=investor`

适配器每次运行重新读取官方目录，按 `dctermsType=Factsheet`、英文基金名匹配，并按 `frkReferenceDate` 选择最新文档。I 列保存官方 Factsheet 文档入口，J 列调用下载器；下载器重新读取 API，优先使用 `downloadUrl`，校验 `%PDF-` 后按项目标准月报路径归档。没有猜测随机 PDF 地址，也没有把产品资料、公告或财报替代为月报。

## 缺口清单

以下 10 只基金当前 API 未返回可验证的 Factsheet，适配器输出 `SKIP`，原因统一为 `NO_CURRENT_FACTSHEET`：

- FTGF 富兰克林超短期债券入息基金
- 富兰克林浮动息率母信托基金
- 邓普顿中国 A 股基金
- FTGF 富兰克林 MV 亚太（日本除外）股票增长及收益基金
- FTGF 凯利灵活入息基金
- FTGF 凯利美国进取型增长基金
- FTGF 马丁可利亚太城市趋势入息基金
- FTGF 西方资产欧元核心增值债券基金
- FTGF 西方资产亚洲入息基金
- 美盛西方资产短期高入息债券基金

这些结果只说明当前公开目录没有可复现的 Factsheet，不等同于“基金已终止”。后续应按 SOP 缺口决策树继续检查基金页、英文站/系列文档以及 HKEX IFP 财报正文；在确认报告类型前不写入 I/J。

## 代码与注册

- `code/companies/franklin/franklin_fund_link.py`：目录读取、名称归一化、回填/缺口输出。
- `code/companies/franklin/download_franklin_factsheet.py`：运行时发现最新 Factsheet、PDF 校验和归档。
- `code/run_all_downloaders.py`：注册 `富兰克林邓普顿`、`Franklin Templeton`、`franklin` 别名。
- `platform/scripts/audit_monthly_coverage.py`：加入管理人适配器清单。
- `爬虫list.xlsx`：新增“富兰克林邓普顿”页表（84 行）；备份为 `爬虫list.xlsx.franklin-before-20261124.bak`。

## 验证

- `py_compile`：通过。
- `--probe`：正式页表 84 行全部识别为待补全，符合 I/J 为空的 SOP 基线。
- `--backfill`：`OK 74 / SKIP 10 / FAIL 0`；结果保留于 `/tmp/franklin_backfill.tsv`。
- 下载样本：Franklin Technology Fund 的 2026-10 Factsheet 下载并归档成功，输出目录 `/tmp/franklin-smoke/data/2026-10-01/`。
- 临时副本端到端回填已验证：84 行中 74 行回填成功，随后补跑因同一管理人串行下载耗时较长，人工中断于已完成 33/74；正式工作簿未被写入。

## 后续动作

1. 在具备浏览器/网络权限的正式 Worker 中运行 GUI 阶段“爬虫补全”，完成 74 行的正式回填与下载。
2. 对 10 条缺口按 SOP 逐条检查生命周期、系列级财报和 HKEX IFP；如果找到半年报/年报正文，应新增独立财报入口并标注“非月报”。
3. GUI 验收通过后，再执行 D1 来源导入，避免把未验收链接写入发布快照。
