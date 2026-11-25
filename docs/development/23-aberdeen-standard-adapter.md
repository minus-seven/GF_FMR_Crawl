# 安本标准（abrdn）适配器

日期：2026-11-25  
范围：`爬虫list.xlsx` 中 `管理人=安本标准` 的 34 行。

## 结论

安本香港通过 [Fund Centre](https://www.aberdeeninvestments.com/en-hk/investor/funds/view-all-funds)
提供当前基金目录。页面的同源 gateway `POST /api/gateway/funds/overview` 返回基金名、份额、ISIN
以及最新 Factsheet 直链；适配器通过真实浏览器调用该接口，避免把动态页面 HTML 当成目录。

本轮临时副本回归结果：**OK 28｜SKIP 6｜FAIL 0**。统一下载器抽样跑“前沿市场债券基金”：
补全阶段回填 28 行，阶段 2 下载 1/1 成功；正式工作簿 I/J 仍为 0 行填充。

## 新增内容

- `code/companies/aberdeen/aberdeen_fund_link.py`：官方目录 API 适配器，支持 `--probe`、`--backfill` 和单基金查询；份额优先级为港元、美元、其他。
- `code/companies/aberdeen/download_aberdeen_factsheet.py`：优先直连 `abrdn.kurtosysweb.com` Factsheet PDF，403 时切换真实 Chrome/Edge 渠道，归档前校验 PDF。
- `爬虫list.xlsx`：新增“安本标准”页表 34 行，插在“宏利”之后；正式 I/J 保持空白。备份：`爬虫list.xlsx.aberdeen-before-20261125.bak`。
- `code/run_all_downloaders.py`：登记安本标准、aberdeen、abrdn 别名。
- `platform/scripts/audit_monthly_coverage.py`：把安本标准纳入覆盖审计。

## 当前缺口（6 只）

当前官方目录未返回以下页表产品，因此适配器明确输出 `NO_CURRENT_FUND`，不猜测旧链接：

1. 亚洲可持续发展目标股票基金
2. 拉丁美洲股票基金
3. 新兴市场基建股票基金
4. 安本基金 II - 环球股票基金
5. 安本基金 II - 欧洲焦点股票基金
6. 安本基金 II - 欧洲股票基金

后续按 SOP 缺口决策树继续核验旧名、英国/国际站、基金状态和 HKEX IFP 伞级报告切片。

## 验收命令

```bash
# 正式页表只探测，不写 I/J
PYTHONPATH=code $FACTSHEET_VENV_ROOT/bin/python code/companies/aberdeen/aberdeen_fund_link.py \
  --excel 爬虫list.xlsx --sheet 安本标准 --probe

# 端到端只使用副本
cp 爬虫list.xlsx /tmp/aberdeen-cli.xlsx
$FACTSHEET_VENV_ROOT/bin/python code/run_all_downloaders.py \
  --crawler-list /tmp/aberdeen-cli.xlsx --sheet 安本标准 --only '前沿市场债券' \
  --workers 1 --allow-failures --no-interactive-retry --backfill \
  --run-id aberdeen_sop_sample --report /tmp/aberdeen-report.json
```

代表性官方详情页：[Frontier Markets Bond Fund](https://www.aberdeeninvestments.com/en-hk/investor/funds/view-all-funds/%E5%AE%89%E6%9C%AC%E5%9F%BA%E9%87%91---%E5%89%8D%E6%B2%BF%E5%B8%82%E5%A0%B4%E5%82%B5%E5%88%B8%E5%9F%BA%E9%87%91-lu1725895616)。

覆盖审计（2026-11-25）已移除安本标准的“无适配器”缺口；下一家缺口最大的是**恒生（34 只）**，其次为普信（32 只）。
