# 第二批七家管理人接入报告（2026-11-25）

## 结论

已按“新公司接入 SOP”依次接入 Muzinich、中信、友邦、英卓、中国人寿富兰克林、动量环球、圣詹姆斯广场，创建独立页表和适配器，使用临时副本回填并按“基金/伞/英文”三元组晋升到正式 `爬虫list.xlsx`。正式工作簿当前为 4,098 行、2,060 条月报入口、76 个适配器；去重后的管理人-基金组合覆盖 1,724/2,134，缺口 410。

本轮正式回填：

|管理人|页表基金数|落盘入口|保留缺口|来源策略|
|---|---:|---:|---:|---|
|Muzinich|9|7|2|Muzinich 官方产品页，执行时解析当前 PDF|
|中信|9|2|7|CITIC CLSA 官方 Factsheet/产品入口|
|友邦|9|0|9|旧 UAT factsheet 已返回 404，未写入失效链接|
|英卓|8|4|4|M&G 官方 Monthly Fund Review/Factsheet PDF|
|中国人寿富兰克林|6|1|5|官方 USD MMF 披露页|
|动量环球|6|6|0|Momentum 官方 MGF factsheet library，下载器按基金名选 PDF|
|圣詹姆斯广场|6|0|6|当前未核验到公开、可复现的香港月报入口|
|**合计**|**53**|**20**|**33**|—|

“友邦”仍为 9 条缺口并非漏接：搜索索引中的 AIA Global Select Equity factsheet 当前 HTTP 404，已主动撤销；其余基金未找到当前可复现的香港官方月报入口。圣詹姆斯广场同理保留空缺，避免把非月报的 KFS/招股书冒充月报。

## 代码与数据落盘

- `code/companies/muzinich/muzinich_fund_link.py`
- `code/companies/citic/citic_fund_link.py`
- `code/companies/aia/aia_fund_link.py`
- `code/companies/mg/mg_fund_link.py`
- `code/companies/chinalifefranklin/chinalifefranklin_fund_link.py`
- `code/companies/momentum/momentum_fund_link.py`
- `code/companies/stjamesplace/stjamesplace_fund_link.py`
- `code/companies/muzinich/download_static_factsheet.py`（可处理 PDF 直链、产品页和基金列表页）
- `tools/add_second_queue_adapter_sheets.py`
- `爬虫list.xlsx` 新增七家页表并完成 I/J 晋升；逐家生成 `爬虫list.xlsx.before-<管理人>-promote-20261125.bak`

统一调度器、覆盖审计和缺口报表已登记七家别名；适配器脚本数由 69 增至 76。

## 官方来源核验样本

- [Muzinich 产品页](https://www.muzinich.com/funds/muzinich-americayield-fund)
- [CLSA USD Money Market Fund Factsheet](https://www.clsa.com/wp-content/uploads/2026/06/CLSA-USD-Money-Market-Fund-Factsheet-Eng-Class_B_20260529.pdf)
- [M&G Total Return Credit Monthly Fund Review](https://www.mandginvestments.com/dam/investments/professional-investor/shared/en/documents/funds-literature/factsheet/total-return-credit-investment-fund_usd_ph_acc_sg_monthly-fund-review_eng_pe_lu3082798045.pdf)
- [China Life Franklin 官方产品披露页](https://www.clamc.com.hk/index.php?a=Ehome&f=product&p=1&pid=7)
- [Momentum MGF factsheet library](https://momentum.co.uk/mgf-fact-sheets)

## 下一家队列

按最新覆盖审计的“无适配器管理人”口径，下一家缺口最大的是 **浦银国际：6 只基金，6 只均缺月报入口**。随后是华安 5、工银瑞信 5；三井住友、中邮国际、交银国际、尚渤投资各 4。完整排序见 `docs/development/artifacts/monthly-gap-by-manager-20261125.md`。

## 验证

- `python3 -m compileall`：七组适配器、下载器和建表工具通过。
- 适配器目录加载：Muzinich 7、中信 2、友邦 0、英卓 4、中国人寿富兰克林 1、动量环球 6、圣詹姆斯广场 0。
- 覆盖审计：主数据 2,479 只、爬虫行 4,098、月报入口 2,060、适配器 76。
- 未核验链接不写入正式表；所有保留缺口均可由适配器重跑重新确认。
