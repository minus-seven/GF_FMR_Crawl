# 第四批八家管理人接入报告（2026-11-26）

## 结论

已按“新公司接入 SOP”接入招证资管、瑞士盈丰、资产管理集团、铭基环球、中欧、信达、信银资本、光大，完成页表创建、官方入口适配、临时副本回填、基金/伞/英文身份校验及正式工作簿晋升。

|管理人|页表基金数|落盘入口|保留缺口|来源策略|
|---|---:|---:|---:|---|
|招证资管|3|2|1|招商证券资管官方产品页|
|瑞士盈丰|3|3|0|EFG New Capital 官方产品页；站点对 requests 有 403，保留给浏览器 worker|
|资产管理集团|3|3|0|Asset Management Group OPES 官方基金目录|
|铭基环球|3|3|0|Matthews Asia 香港官方基金文件目录|
|中欧|2|1|1|HKEX 官方基金入口|
|信达|2|2|0|Cinda International 官方基金页|
|信银资本|2|1|1|CNCB Capital 官方基金页|
|光大|2|2|0|Everbright Securities International 官方资产管理页|
|**合计**|**20**|**17**|**3**|—|

保留缺口分别为招商美元货币市场基金、中欧全球投资级债券基金、信银资本亚洲投资级债券基金；未用名称相近产品或第三方资料替代。

## 代码与数据落盘

- `code/companies/cmsam/cmsam_fund_link.py`
- `code/companies/newcapital/newcapital_fund_link.py`
- `code/companies/assetmg/assetmg_fund_link.py`
- `code/companies/matthews/matthews_fund_link.py`
- `code/companies/zo/zo_fund_link.py`
- `code/companies/ciam/ciam_fund_link.py`
- `code/companies/cncb/cncb_fund_link.py`
- `code/companies/everbright/everbright_fund_link.py`
- `tools/add_fourth_queue_adapter_sheets.py`

正式 `爬虫list.xlsx` 新增八家页表并完成 I/J 回填；每家均生成 `爬虫list.xlsx.before-<管理人>-promote-20261126.bak`。`cmsam` 原有适配器已按本轮官方产品页覆盖复核，其余七个为新增适配器。

## 覆盖审计

- 主数据：2,479 只基金、187 家管理人
- 爬虫工作簿：4,160 行
- 月报入口：2,104 条
- 去重管理人-基金组合：2,134
- 已覆盖：1,768
- 缺口：366
- `*_fund_link.py` 适配器：93 个

完整清单见 `docs/development/artifacts/monthly-gap-by-manager-20261125.md`。

## 下一家无适配器队列

按 `data/monthly_coverage_baseline.json` 的 `adapter_gaps` 排序，下一家没有适配器且缺口最大的管理人为 **农银国际：2 只基金，2 只均缺月报入口**。农银汇理、安本、广发基金、建银、德意志资管等也各为 2 只，属于并列队列。

## 验证

- `python3 -m compileall` 通过。
- 字段流水线回归测试：`8 passed`。
- 八家晋升均 `rejected=0`。
- 瑞士盈丰站点返回 403，官方页面已由搜索和产品页证据核验，正式入口交由具备浏览器权限的 worker 下载；不因此写入猜测 PDF。
