# 瑞士百达（Pictet）适配器接入记录

日期：2026-11-25

## 官方入口与实现

- 香港基金入口：<https://am.pictet.com/hk/en/individuals/funds>；官方产品页示例：[Pictet - Water](https://am.pictet.com/hk/en/individuals/funds/pictet-water/LU0255980327)。
- 香港站产品路由通常需要 ISIN，且存在反自动化访问门槛；工作簿没有 ISIN，因此适配器按官方香港产品 slug 生成产品入口，下载器在运行时用真实浏览器从产品页文档组件定位最新 Factsheet。
- 新增 `code/companies/pictet/pictet_fund_link.py` 和 `download_pictet_factsheet.py`，覆盖 27 个工作簿英文基金名；不以瑞士/卢森堡地区页面替代香港入口。
- 新增“瑞士百达”工作表 27 行，备份为 `爬虫list.xlsx.pictet-before-20261125.bak`；正式 I/J 保持为空。

## 回归结果

在 `/tmp/three-adapters.xlsx` 上运行：**OK 27｜SKIP 0｜FAIL 0**（静态产品名/slug 回填）。本机用已知 ISIN 的 Water 产品页做下载抽样时，香港站仍返回反自动化空页面，未取得 PDF；正式 worker 需在具备原生浏览器权限的环境验证具体 ISIN 页面和 PDF 下载。下载器对此保留失败提示，不把 KID/招股书当月报。

