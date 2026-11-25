# 恒生投资管理（Hang Seng Investment Management）适配器

日期：2026-11-25  
范围：`爬虫list.xlsx` 中 `管理人=恒生` 的 34 行。

## 结论

恒生香港官方 [Resources 页面](https://www.hangsenginvestment.com/en-hk/individual-investor/resources/)
通过资源目录接口返回基金英文名、基金类型和最新 Monthly Factsheet 文档。适配器读取该目录，
以官方资源页作为 I 列稳定入口，在 J 列运行时使用当前 PDF 直链；API 返回的 `/ivp/...` 路径
统一补上站点实际需要的 `/cms` 前缀。

本轮临时副本回归结果：**OK 32｜SKIP 2｜FAIL 0**。统一下载器端到端回归中，恒生中国股票基金
完成“补全 32 行 → 阶段 2 下载 1/1”，PDF 成功归档；正式工作簿 I/J 保持空白。

## 新增内容

- `code/companies/hangseng/hangseng_fund_link.py`：官方资源目录适配器，支持 `--probe`、
  `--backfill` 和单基金查询；对 ETF 的 Listed/Unlisted Class 做同名前缀匹配。
- `code/companies/hangseng/download_hangseng_factsheet.py`：直连 PDF、校验 `%PDF-`、按报告月份归档。
- `爬虫list.xlsx`：新增“恒生”页表 34 行，位于“安本标准”之后；备份为
  `爬虫list.xlsx.hangseng-before-20261125.bak`。
- `code/run_all_downloaders.py`：登记 `恒生`、`hangseng`、`Hang Seng` 等别名。
- `platform/scripts/audit_monthly_coverage.py`：将恒生纳入适配器覆盖审计。

## 当前缺口（2 只）

1. **恒生富时香港股票ETF**：官方目录存在 Listed/Unlisted Class，但当前目录没有单只 Monthly
   Factsheet，因此不以公告、招股书或中期报告冒充月报。
2. **恒生香港股票基金**：当前资源目录未返回该英文产品名，暂未发现可安全认领的单只 Monthly
   Factsheet；相关系列通知/中期报告仅作为候选证据，不写入 I/J。

官方 [Our Products](https://www.hangsenginvestment.com/en-hk/individual-investor/our-products/)
与 [About Us](https://www.hangsenginvestment.com/en-hk/individual-investor/about-us/) 可用于后续
核验产品状态。缺口不代表产品不存在，而是当前可验证的单只月报入口不足。

## 验收命令

```bash
# 正式页表只探测，不写 I/J
PYTHONPATH=code $FACTSHEET_VENV_ROOT/bin/python code/companies/hangseng/hangseng_fund_link.py \
  --excel 爬虫list.xlsx --sheet 恒生 --probe

# 端到端只使用副本
cp 爬虫list.xlsx /tmp/hangseng-cli.xlsx
$FACTSHEET_VENV_ROOT/bin/python code/run_all_downloaders.py \
  --crawler-list /tmp/hangseng-cli.xlsx --sheet 恒生 --only '恒生中国股票基金' \
  --workers 1 --allow-failures --no-interactive-retry --backfill \
  --run-id hangseng_sop_sample --report /tmp/hangseng-report.json
```

覆盖审计（2026-11-25）已移除恒生的“无适配器”缺口；下一家缺口最大的是**普信（32 只）**，其次为
晋达（30 只）。
