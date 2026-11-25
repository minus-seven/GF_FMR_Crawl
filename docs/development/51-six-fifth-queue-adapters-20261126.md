# 第五批六家管理人适配器接入验收

日期：2026-11-26  
范围：农银国际、农银汇理、安本、广发基金、建银、德意志资管

## 结果

按 SOP 创建独立页表、适配器和调度器别名；每家均在 `/tmp` 临时验收副本中运行回填，再按“基金、伞、英文”三元组身份校验晋升正式 `爬虫list.xlsx`。六家各 2 个去重基金组合，共 12 个组合全部写入官方入口，拒绝新增/错配行 0 条。

|管理人|去重基金组合|回填成功|仍缺口|官方入口|
|---|---:|---:|---:|---|
|农银国际|2|2|0|HKEX IFP BTJ522、BDW216|
|农银汇理|2|2|0|HKEX IFP BHK958、BHK957|
|安本|2|2|0|HKEX IFP BWO745、BXI864|
|广发基金|2|2|0|HKEX IFP BFT955、BFT957|
|建银|2|2|0|HKEX IFP BWL295、BXE160|
|德意志资管|2|2|0|HKEX IFP BNG751、APU615|

说明：这里的“月报入口”是项目统一使用的官方基金详情入口。IFP 页面中的具体月报附件由下载阶段再通过 API/原生浏览器解析；静态 requests 无法看到动态附件时，不将第三方或猜测 PDF 写入工作簿。

## 全局复核

- 适配器脚本：99 个。
- `爬虫list.xlsx`：4172 行；按（管理人、规范化基金名称）去重 2134 个组合。
- 有月报入口 1780 个，缺口 354 个；原始页表链接 2116 条。
- 六家目标管理人均已标记为“有适配器”，且正式表已落盘。
- 最新无适配器且缺口最大的管理人为 **民银资本：2 个组合缺口**。永明、申万宏源、罗安贝、英仕曼、融通基金、誉信同为 2 个缺口，需按 SOP 继续逐家接入。

## 代码与备份

- 新增：`code/companies/abci/`、`abc_ca/`、`aberdeenbasic/`、`gffund/`、`ccbintlbasic/`、`dws/`。
- 新增：`tools/add_fifth_queue_adapter_sheets.py`。
- 注册：`code/run_all_downloaders.py`、`platform/scripts/audit_monthly_coverage.py`、`tools/update_monthly_gap_report.py`。
- 页表备份：`爬虫list.xlsx.before-six-fifth-queue-adapters-20261126.bak`。
- 六家晋升前备份：`爬虫list.xlsx.before-农银国际-promote-20261126.bak` 等六份。

## 官方来源核验

基金身份入口均来自港交所 IFP 官方基金目录；安本、广发基金、建银、DWS 的基金名称/编号与官方检索结果一致。农银汇理两只基金也通过 IFP 搜索接口精确返回 BHK958/BHK957。部分页面的附件列表依赖动态 API，不能在静态 HTML 结果中直接断言“当前月报 PDF 已下载”，因此后续正式下载仍需运行 browser-worker，并在下载报告中记录附件缺失或访问限制。

