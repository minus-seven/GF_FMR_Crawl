# 美富信（MFS Meridian Funds）月报适配器

日期：2026-11-25

## 范围与官方入口

- Sheet2 精确筛出管理人“美富信” **23** 行，全部为 MFS Meridian Funds；新增页表，正式 I/J 保持为空。
- 官方入口：[MFS Meridian Funds Hong Kong](https://www.mfs.com/content/mfs-enterprise/microsites/meridian/mfs-meridian-funds.html)。页面公开调用 `services.mfs.com/MFSServices/products/v1/reports/meridian/reportfilter` 的香港区域目录。
- 每只产品再调用 `productdetails4MeridianMicro`，选择 `Fact Sheet` 且优先 `English (Hong Kong)`，得到官方 Factsheet PDF。

## 实现

- `code/companies/mfs/mfs_fund_link.py`：读取 `portRegionMap.hk` 的 23 个 ISIN/名称，逐只解析产品详情和英文 Factsheet；归一化时去除 `MFS Meridian Funds`、® 和标点，避免中文页表与英文产品前缀造成误配。
- `code/companies/mfs/download_mfs_factsheet.py`：保留官方英文 PDF，校验 `%PDF-`，识别报告月份后归档。
- `tools/add_mfs_sheet.py`：从 Sheet2 复制 23 行并保留样式，备份 `爬虫list.xlsx.mfs-before-20261125.bak`；临时副本由 `tools/apply_adapter_backfill.py` 生成。
- 统一调度器注册 `美富信`、`MFS` 和 `MFS Investment Management` 别名；覆盖审计登记为适配器。

## 验证

- `/tmp/mfs.xlsx` 临时回填：**OK 23｜SKIP 0｜FAIL 0**；统一调度器列出 23 条任务。
- 抽样 `Asia Ex-Japan Fund` 下载成功并归档：`data/2026-09-01/mfs_MFS_Meridian_Funds_-_Asia_Ex-Japan_Fund.pdf`。
- 适配器和调度器编译通过；正式工作簿新页 I/J 填充数为 0。

## 边界

当前接口按 A1USD 查询产品详情，但 Factsheet 选择以香港英语报告为准；若某产品暂时没有英文 Factsheet，脚本会显式 SKIP，不回退到 KIID、SRD II 或其他非月报文件。
