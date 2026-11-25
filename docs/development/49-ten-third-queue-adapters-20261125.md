# 第三批十家管理人接入报告（2026-11-25）

## 结论

已按“新公司接入 SOP”接入浦银国际、华安、工银瑞信、三井住友、中邮国际、交银国际、尚渤投资、招商永隆、凯基、建信，创建独立页表、适配器并完成临时副本身份校验后正式回填。

|管理人|页表基金数|落盘入口|保留缺口|官方来源/处理|
|---|---:|---:|---:|---|
|浦银国际|6|0|6|当前未核验到可复现的官方香港月报目录，保持空目录|
|华安|5|1|4|华安香港官方基金文件页，已核验投资级债券基金|
|工银瑞信|5|5|0|工银资管官方基金价格/基金月报目录|
|三井住友|4|4|0|SMD-AM Hong Kong Fund Centre|
|中邮国际|4|4|0|China Post Global 官方 Documents 目录|
|交银国际|4|4|0|BOCOM International 官方 Monthly Fund Factsheet 目录|
|尚渤投资|4|3|1|Thornburg WMCube 官方 Factsheet，3 条 PDF 可复现|
|招商永隆|4|4|0|CMB Wing Lung Asset Management 官方 Public Fund 页面|
|凯基|3|1|2|KGI 官方基金详情页，已核验多元化成长收益基金|
|建信|3|1|2|Principal 官方 CCB Principal Factsheet，已核验优选成长基金|
|**合计**|**42**|**27**|**15**|—|

浦银国际、华安、尚渤、凯基、建信的剩余缺口均保留，未用名称相近的全球版本、旧版销售文件或第三方页面冒充当前月报。交银国际官方目录在本地 requests 环境出现 TLS 重试，但官方目录已由搜索与页面证据核验，入口照常落盘，实际下载由具备原生浏览器权限的 worker 运行。

## 代码与数据落盘

- `code/companies/puyin/puyin_fund_link.py`
- `code/companies/huaan/huaan_fund_link.py`
- `code/companies/icbcubs/icbcubs_fund_link.py`
- `code/companies/smdam/smdam_fund_link.py`
- `code/companies/chinapost/chinapost_fund_link.py`
- `code/companies/bocom/bocom_fund_link.py`
- `code/companies/thornburg/thornburg_fund_link.py`
- `code/companies/cmbwinglung/cmbwinglung_fund_link.py`
- `code/companies/kgi/kgi_fund_link.py`
- `code/companies/ccbprincipal/ccbprincipal_fund_link.py`
- `code/common/queue_static_adapter.py`
- `tools/add_third_queue_adapter_sheets.py`

正式 `爬虫list.xlsx` 已新增十家页表并完成 I/J 回填；每家均生成 `爬虫list.xlsx.before-<管理人>-promote-20261125.bak` 备份。适配器总数由 76 增至 86。

## 覆盖审计

- 主数据：2,479 只基金、187 家管理人
- 爬虫工作簿：4,140 行
- 月报入口：2,087 条
- 去重管理人-基金组合：2,134
- 已覆盖：1,751
- 缺口：383

完整降序清单见 `docs/development/artifacts/monthly-gap-by-manager-20261125.md`。

## 下一家无适配器队列

按 `data/monthly_coverage_baseline.json` 的 `adapter_gaps`（只统计尚无适配器标签的管理人）排序，下一家缺口最大的是 **招证资管：3 只基金，3 只均缺月报入口**。同为 3 只的还有瑞士盈丰、资产管理集团、铭基环球；随后为中欧、信达、信银资本等各 2 只。

## 验证

- 七十六个既有适配器加本轮十个适配器共 **86** 个 `*_fund_link.py`。
- `python3 -m compileall` 通过。
- 字段流水线回归：`8 passed`。
- 所有临时副本晋升均 `rejected=0`，未发生基金身份错配。
