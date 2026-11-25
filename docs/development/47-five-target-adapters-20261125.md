# 五家无适配器管理人接入记录（2026-11-25）

本轮按《新公司接入 SOP》执行：先从 `Sheet2` 建立管理人页表，再用临时副本运行
`tools/apply_adapter_backfill.py`，通过基金/伞/英文三元组晋升到正式 `爬虫list.xlsx`，
最后重新运行覆盖审计。正式工作簿备份为：
`爬虫list.xlsx.before-five-target-adapters-20261125.bak` 及每家晋升前的分家备份。

## 回填结果

|管理人|页表行|正式回填|仍缺口|入口/下载策略|
|---|---:|---:|---:|---|
|思博瑞资管（Allspring）|11|11|0|官方 UCITS 产品页；下载器从 Documents 重新发现 Monthly Fact Sheet|
|摩根斯坦利|11|11|0|官方香港产品页；下载器从动态 Documents 重新发现 Factsheet|
|工银资管|10|4|6|已核验的官方 Factsheet PDF：USD/HKD 货币、RMB 固收、中国香港愿景|
|未来（Mirae Asset）|10|0|10|当前公开可核验的是 KFS/宣传册，未把非月报资料冒充月报|
|野村|10|8|2|Nomura UK 官方 Funds Ireland Factsheet（A/I 类可用文件）|

合计新增正式入口 **34 条**。本轮没有写入“名称相近、全球其他地区版本、KFS、宣传册、
半年报/年报”作为月报入口。

## 代码

- `code/companies/allspring/`：Allspring 产品页目录与动态月报下载器。
- `code/companies/morganstanley/`：Morgan Stanley 香港产品页目录与动态 Factsheet 下载器。
- `code/companies/icbcamg/`：ICBC 官方 PDF Factsheet 目录与归档下载器。
- `code/companies/mirae/`：保守适配器；目录暂无可核验单只月报时明确返回缺口原因。
- `code/companies/nomura/`：Nomura Funds Ireland 官方 Factsheet 目录与归档下载器。
- `code/common/static_adapter.py`：静态官方目录名称匹配共用逻辑，优先最长匹配，避免
  “Emerging Markets Equity” 抢配 “Emerging Markets Equity Advantage”。
- `tools/add_target_adapter_sheets.py`：按主数据身份创建五家页表并自动备份。

## 验证与边界

- 工银 USD Money Market 与野村 US High Yield 抽样下载成功并归档到 `data/2026-09-01/`
  与 `data/2026-10-01/`。
- Allspring 与 Morgan 的产品页在普通 requests 环境分别受动态文档/反爬影响；链接本身为
  官方产品页，正式 Worker 需具备原生浏览器权限才能执行动态 Factsheet 发现。
- Mirae 的 HK 官网当前可查到 KFS、marketing communication 和 prospectus，但本轮未找到
  可复现的单只 Monthly Factsheet，因此保留 10 条缺口，待浏览器/人工入口核验后再回填。
- 野村 `Japan Small Cap Equity Fund`、`Corporate Hybrid Bond Fund` 当前目录未核验到可用
  Factsheet 文件，保留 2 条缺口。

## 后续运行

```bash
python code/run_all_downloaders.py --only 思博瑞资管
python code/run_all_downloaders.py --only 摩根斯坦利
python code/run_all_downloaders.py --only 工银资管
python code/run_all_downloaders.py --only 野村
```

Allspring/Morgan 运行时必须使用已安装 Chromium 的正式 Worker；若站点继续返回 403，记录
为 `forbidden`，不要把页面错误 HTML 归档为 PDF。
