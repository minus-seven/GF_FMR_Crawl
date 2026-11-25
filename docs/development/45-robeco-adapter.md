# 荷宝适配器（2026-11-25）

## 来源与策略

使用荷宝香港官方基金目录 `https://www.robeco.com/en-hk/products/funds`。目录列出每个份额的官方详情页；适配器按英文基金名归一化匹配，港元份额优先，其次其他币种。详情页中的 `Fund documents → Factsheet` 是月度入口，下载器每次运行时重新解析最新 PDF，不固化易变附件地址。

## 验证与回填

- 页表范围：9 只荷宝缺口基金。
- 官方目录匹配：3/9；6 只因当前香港目录未返回同名可验证详情页而保留缺口。
- 已回填：荷宝 BP 环球卓越股票、荷宝 BP 美国卓越股票、荷宝债券收益。
- 抽样下载：荷宝 BP 环球卓越股票 Factsheet 成功，PDF 校验和归档通过。
- 正式工作簿回填前生成 `爬虫list.xlsx.before-robeco-promote-20261125.bak`，只接受身份校验通过的行。

## 代码

- `code/companies/robeco/robeco_fund_link.py`
- `code/companies/robeco/download_robeco_factsheet.py`
- `code/run_all_downloaders.py` 已注册“荷宝/robeco”别名。

