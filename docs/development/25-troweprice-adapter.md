# 普信（T. Rowe Price）适配器

日期：2026-11-25  
范围：`爬虫list.xlsx` 中 `管理人=普信` 的 32 行，伞为“普徕仕(卢森堡)系列”。

## 结论

普信香港官方 [Investment Funds 页面](https://www.troweprice.com/financial-intermediary/hk/en/funds.table.html)
使用公开产品 API 返回基金英文名、份额代码、产品页和 ISIN；月报通过官方
`/literature/public/.../monthly-factsheet/...` Literature Service 提供。适配器运行时从官方页面
读取 API key，优先选择 A 类、港元/美元份额，并把产品代码传给月报服务，避免固化每月变化的 PDF 地址。

临时副本回归结果：**OK 28｜SKIP 4｜FAIL 0**。统一下载器回填 28 行并抽样完成 **1/1** PDF 下载；
正式工作簿 I/J 保持空白。

## 新增内容

- `code/companies/troweprice/troweprice_fund_link.py`：官方产品 API 适配器，支持 `--probe`、
  `--backfill` 和单基金查询；份额选择优先 A 类、港元、美元。
- `code/companies/troweprice/download_troweprice_factsheet.py`：下载、校验 `%PDF-`、按报告月份归档，
  403 时使用真实浏览器兜底。
- `爬虫list.xlsx`：新增“普信”页表 32 行，位于“恒生”之后；备份为
  `爬虫list.xlsx.troweprice-before-20261125.bak`。
- `code/run_all_downloaders.py`：登记 `普信`、`troweprice`、`T. Rowe Price` 等别名。
- `platform/scripts/audit_monthly_coverage.py`：将普信纳入适配器覆盖审计。

## 当前缺口（4 只）

1. **新兴市场本地债券基金**：只核验到其他地区产品页，当前香港产品 API 未列出。
2. **环球高收益债券基金**：当前香港 API 未列出，不映射到名称相近的 High Yield Bond Fund。
3. **亚洲机遇股票基金**：官方公告显示已进入终止/清算流程，不映射到 Asian ex-Japan Equity Fund。
4. **智选退休配置基金 - 3**：官方招股书仍提及，但当前香港 API 没有可验证的单只 Monthly Factsheet。

上述缺口按 SOP 保留为可复核 SKIP，不把其他地区页面、招股书或相近策略产品冒充香港月报。

## 验收命令

```bash
# 正式页表只探测，不写 I/J
PYTHONPATH=code $FACTSHEET_VENV_ROOT/bin/python code/companies/troweprice/troweprice_fund_link.py \
  --excel 爬虫list.xlsx --sheet 普信 --probe

# 端到端只使用副本
cp 爬虫list.xlsx /tmp/troweprice-cli.xlsx
$FACTSHEET_VENV_ROOT/bin/python code/run_all_downloaders.py \
  --crawler-list /tmp/troweprice-cli.xlsx --sheet 普信 --only '环球增长股票基金' \
  --workers 1 --allow-failures --no-interactive-retry --backfill \
  --run-id troweprice_sop_sample --report /tmp/troweprice-report.json
```

覆盖审计（2026-11-25）已移除普信的“无适配器”缺口；下一家缺口最大的管理人为**晋达（30 只）**，
其次为资本集团（29 只）。
