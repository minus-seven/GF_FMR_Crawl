# 霸菱（Barings）适配器接入记录

日期：2026-11-25

## 范围与官方入口

- 工作簿 `Sheet2` 中 `管理人=霸菱` 共 34 行（含同名基金不同伞、历史/侧袋行）。
- 官方香港英文站：<https://www.barings.com/en-hk/individual/sitemap>；产品页位于 `/en-hk/individual/funds/`，按 `public-fixed-income`、`public-equities`、`multi-asset` 分类。
- 官方产品页会提供 Factsheet、KFS、Prospectus 等文档入口；适配器只认 Factsheet，不以 KFS/招股书替代月报。

## 落盘内容

- `code/companies/barings/barings_fund_link.py`：按官网英文产品名和 slug 精确映射，保留伞字段，支持 `--probe`、`--backfill`、单基金查询。
- `code/companies/barings/download_barings_factsheet.py`：先用 requests 解析官方产品页 Factsheet PDF，页面动态或直连失败时切换 Playwright 原生浏览器；下载后按报告月份归档到 `data/<月份>/`。
- `tools/add_barings_sheet.py`：从 `Sheet2` 复制 34 行创建“霸菱”页表，清空正式 I/J；正式工作簿备份为 `爬虫list.xlsx.barings-before-20261125.bak`。
- `code/run_all_downloaders.py`、`platform/scripts/audit_monthly_coverage.py`：注册霸菱别名和覆盖审计管理人标签。

## 回归结果

使用 `/tmp/barings-probe.xlsx` 临时副本运行：**OK 32｜SKIP 2｜FAIL 0**。

- 32 行映射到香港官方同名产品页；重复名称按工作簿伞字段逐行生成同一官方产品页，未将伞名作为产品名混淆。
- `霸菱全球综合债券基金`（英文 `Baring Global Aggregate Bond Fund`）在 2026-11-25 香港 sitemap 未找到同名产品页，因此保留 SKIP：不映射到 Global Bond/Global Investment Grade Credit 等相近策略。
- `霸菱欧洲高收益债券基金` 的香港英文产品路径返回 404；仅检索到英国/德国地区页面，不把其他地区入口冒充香港月报。
- 抽样运行 `download_barings_factsheet.py` 下载 `Barings Global High Yield Bond Fund` 成功，产出：`data/2026-09-01/barings_Barings_Global_High_Yield_Bond_Fund.pdf`。
- 正式工作簿的 I/J 未写入，保留给 GUI“爬虫补全”验收和正式回填。

## 下一步缺口

接入后重新生成覆盖审计，下一家按“缺少月报链接基金数”排序；若霸菱的 32 个可验证入口均被 GUI 回填，预计下一家为**保诚瀚亚**（需以最新审计 JSON 为准）。

