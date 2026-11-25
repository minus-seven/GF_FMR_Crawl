# 木星资管（Jupiter Asset Management）适配器接入

日期：2026-11-25

## 范围与官方入口

`爬虫list.xlsx` 的“木星资管”页共 15 行。官方来源为 [Jupiter 香港 Fund Centre](https://www.jupiteram.com/hk/en/individual/fund-centre/)，其 DIFRAAS API 为：

- `getShareClassList`：香港可售 share class 目录；
- `getDocumentCategories`：按 `shareclassExternalId` 返回 Marketing documents、Shareholder information 等文档；
- 文档 PDF 由 `dms-api.jupiteram.instinctcentral.io` 提供稳定 permalink。

报告选择按“英文优先 + 类型优先 + 日期降序”执行：Monthly Investment Report > Factsheet > Quarterly Investment Report。当前目录中 9 只使用 Factsheet、1 只使用 Monthly Investment Report、2 只使用 Quarterly Investment Report；3 只产品没有可验证报告，保留缺口。

## 实现与验证

- 新增 `code/companies/jupiter/jupiter_fund_link.py`、`download_jupiter_factsheet.py`、`tools/add_jupiter_sheet.py`。
- 新增“木星资管”页表 15 行，正式工作簿 I/J 保持空白；备份为 `爬虫list.xlsx.jupiter-before-20261125.bak`。
- 临时副本 `/tmp/jupiter.xlsx` 回填：**12 OK、3 SKIP、0 FAIL**。
- 明确缺口：木星全球价值基金、木星环球债券基金（英文目录为 Global Fixed Income Fund）、木星新兴市场债券基金。木星香港 Fund Centre 当前未返回同名可下载报告，未用相似产品替代。
- `run_all_downloaders.py --sheet 木星资管 --list` 能识别 12 行；在原生网络权限下 12/12 官方 PDF 下载成功，归档于 `data/jupiter-20261125/factsheet/`，PDF 文件头校验通过。

## 调度、审计与边界

已在统一调度器注册“木星资管”、Jupiter 英文别名，并在月报覆盖审计中登记；适配器总数由 60 增至 **61**。动态 API 每次运行重新查询当前 share class 和文档版本，不把某一月 PDF 当作永久目录入口。季度报告仅在官网没有 Factsheet/Monthly Investment Report 时使用，并保留原始英文标题和日期；后续若官网补发月报，下一次运行会自动优先月报。
