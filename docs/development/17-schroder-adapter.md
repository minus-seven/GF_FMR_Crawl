# 施罗德香港月报适配器

## 范围

按 HKEX/`爬虫list.xlsx` Sheet2 的 `管理人=施罗德` 精确筛选，建立 81 行“施罗德”页表。正式页表的 I/J 列按 SOP 阶段 4 保持为空；阶段 4 的回填只在临时副本验证。

## 官方入口与数据流

- 基金目录：施罗德香港 Fund Centre 的公开前端接口 `https://fev2-vite.schdr.eu-central-1.isgdigital.com/api/v1/getShareClassList`，按 `pageIndex/pageSize` 分页，当前目录 78 个份额。
- 文档分类：`/api/v1/getDocumentCategories`，参数使用历史接口要求的 `shareclassExternalId`（小写 c）。接口返回 Fund literature 下最新 Factsheet 的官方 `api.schroders.com/document-store/...pdf`，以及历史版本日期。
- I 列：稳定的官方 Fund Centre 详情页（含 `externalId`）；J 列：`download_schroder_factsheet.py`，运行时重新读取文档分类接口、校验 `%PDF-`，按项目标准路径归档。
- 适配器：`code/companies/schroder/schroder_fund_link.py`；下载器：`code/companies/schroder/download_schroder_factsheet.py`。

## 匹配与缺口

匹配先去掉 `Schroder International Selection Fund`/`Schroder` 系列前缀，再去掉 A/C/A1 份额类别、Accumulation/Distribution、币种及 `Fund` 尾缀；只接受归一化核心名精确相等，禁止 startswith，避免将“Emerging Markets Multi-Asset”误配为“Emerging Markets”。类别选择遵循港元→美元→其他币种。

2026-11-24 临时副本 `--backfill` 结果：**OK 76、SKIP 5、FAIL 0**。

| 基金 | 结果 | 原因 |
|---|---|---|
| 施罗德 Salvia 环球配置基金 | SKIP | 当前 Fund Centre 目录未返回可验证份额 |
| 环球股债增长收息 | SKIP | 目录有份额，但文档分类接口未返回 Factsheet |
| 施罗德灵活配置总回报基金 | SKIP | 目录有份额，但文档分类接口未返回 Factsheet |
| 新兴市场股债 | SKIP | 当前目录未返回对应 Multi-Asset 份额（不能降级匹配 Emerging Markets） |
| 亚洲小型公司 | SKIP | 当前 Fund Centre 目录未返回可验证份额 |

这些 SKIP 不是猜测“官网没有资料”的结论；按 SOP 后续可继续查英文/国际站、基金生命周期及 HKEX IFP 财报正文，但不能把相似基金或产品资料写成月报。

## 抽样与验证

- `py_compile`：适配器和下载器通过。
- 单基金回填：欧元企业债券返回官方详情页、`SCHDR_F0GBR04DV6` 和可执行 J 命令。
- 单 PDF：官方 Factsheet（October 2026）下载成功，PDF 归档于临时输出根目录并通过文件头校验。
- 全量回填在临时副本执行，正式页表 I/J 未写入。

## 限制

施罗德主站在部分自动化环境会返回 403；适配器使用公开前端 API，不依赖 Playwright。若 API 限流，按公共补全逻辑重试；Worker/GUI 阶段仍应在具备原生网络权限的正式服务中运行。
