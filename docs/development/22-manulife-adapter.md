# 宏利（Manulife Investment Management）适配器

日期：2026-11-25  
范围：`爬虫list.xlsx` 中 `管理人=宏利` 的 38 行。

## 结论

宏利香港的基金文件入口是动态的 Fund Documents 选择器（Provider → Fund → Documents）。
静态 PDF 资产在纯 `requests` 下会返回 403，因此适配器把官方选择器核验过的
Factsheet 代码登记为可审计映射，下载器遇到 403 自动切换项目统一的真实 Chrome/Edge
渠道，并校验 `%PDF-` 后再归档。

本轮临时副本回归结果：**OK 16｜SKIP 22｜FAIL 0**。统一下载器抽样跑“宏利环球基金 - 美元入息基金”：
补全阶段回填 16 行，阶段 2 下载 1/1 成功；正式工作簿的 I/J 仍为 0 行填充。

## 新增内容

- `code/companies/manulife/manulife_fund_link.py`：基于官方 Factsheet 资产代码的补全脚本，支持 `--probe`、`--backfill` 和单基金查询。
- `code/companies/manulife/download_manulife_factsheet.py`：requests → 真实浏览器渠道的 403 兜底下载，调用通用 `finalize_pdf_to_monthly_path` 归档。
- `爬虫list.xlsx`：新增“宏利”页表 38 行，插在“法巴”之后；正式 I/J 保持空白。备份：`爬虫list.xlsx.manulife-before-20261125.bak`。
- `code/run_all_downloaders.py`：登记 `宏利/manulife/Manulife` 别名。
- `platform/scripts/audit_monthly_coverage.py`：把宏利纳入覆盖审计。

## 可验证月报映射（16 只）

以下均指官方英文 Factsheet 资产，运行时下载器会再次检查 PDF 内容和日期：

| 基金 | 官方代码 |
| --- | --- |
| Global Multi-Asset Diversified Income Fund | `MLGMRH` |
| China Value Fund | `CVFAA` |
| Asia Pacific REIT Fund | `MLAPGN` |
| Healthcare Fund | `MLHCAA` |
| Preferred Securities Income Fund | `MLPI1U` |
| India Equity Fund | `IEAA` |
| Sustainable Asia Bond Fund | `MLSAAK` |
| Asian High Yield Fund | `MLAHAB` |
| Global Equity Fund | `IGFAA` |
| Taiwan Equity Fund | `TEAA` |
| Japan Equity Fund | `JGFAA` |
| Global Equity Diversified Income Fund | `MLED1U` |
| Asia Dynamic Income Fund | `MLADMD` |
| Global REIT Fund | `MLGP1B` |
| Investment Grade Preferred Securities Income Fund | `MLIGAU` |
| USD Income Fund | `MLUBMH` |

## 当前缺口（22 只）

缺口统一记录为 `NO_FACTSHEET`，含义是本轮官方当前文件选择器没有核验到单只
Factsheet；不会把 KFS、宣传册、通用招股书或仅有月度问答的文件伪装成月报。

美国股票基金、巨龙增长基金、环球半导体机会基金、可持续亚洲股票基金、领先动力基金、宏利稳定基金、
亚洲创新股票基金、东协股票基金、多元化实质资产基金、亚洲总回报基金、美国特别机会基金、亚洲短期债券基金、
宏利 STARS 收益及增长基金、环球气候行动基金、宏利环球多元资产入息基金，以及中国 A 股独立资产组合、
大湾区增长及收益独立资产组合、人民币债券独立资产组合、美国银行业股票独立资产组合、预定回报独立资产组合 I、
新兴东欧基金共 22 只。

这些行下一步应按 SOP 缺口决策树继续核验：动态选择器的真实文档请求、同伞其他语言/类别、
最新中期/年度报告和 HKEX IFP 伞级报告切片；其中新兴东欧基金还需优先确认存续/终止状态。

## 运行和验收

```bash
# 正式页表只探测，不写 I/J
$FACTSHEET_VENV_ROOT/bin/python code/companies/manulife/manulife_fund_link.py --excel 爬虫list.xlsx --sheet 宏利 --probe

# 端到端只使用副本（示例）
cp 爬虫list.xlsx /tmp/manulife-cli.xlsx
$FACTSHEET_VENV_ROOT/bin/python code/run_all_downloaders.py \
  --crawler-list /tmp/manulife-cli.xlsx --sheet 宏利 --only '美元入息' \
  --workers 1 --allow-failures --no-interactive-retry --backfill \
  --run-id manulife_sop_sample --report /tmp/manulife-report.json
```

官方入口：[Manulife Fund Documents](https://www.manulifeim.com.hk/en/resources/fund-documents.html)。
代表性资产：[USD Income Factsheet](https://www.manulifeim.com.hk/content/dam/wam/hk/en/funds/factsheet/MLUBMH-factsheet.pdf)、
[Asia Pacific REIT Factsheet](https://www.manulifeim.com.hk/content/dam/wam/hk/en/funds/factsheet/MLAPGN-factsheet.pdf)。

覆盖审计（2026-11-25）显示，下一家缺口最大的管理人为**安本标准**：34 只基金均缺少月报链接和运行方式；**恒生**同为 34 只，并列第二候选。
