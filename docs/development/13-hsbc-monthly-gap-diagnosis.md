# 汇丰月报覆盖缺口排查

> 排查日期：2026-11-24（Asia/Hong_Kong）  
> 结论：执行前缺口主要是“补全链路没有成功运行/落盘”，而不是 HSBC 官网整体没有月报；本次原生权限批量运行后，汇丰爬虫页已补到 93/94 条，剩余 1 条页内基金和 3 条主数据漏收基金没有可验证来源。

## 1. 执行前基线

| 对象 | 数量 | 说明 |
|---|---:|---|
| `爬虫list.xlsx` 汇丰页 | 94 | 其中权益 38、固收 31、混合 22、货币 3 |
| 已填月报链接 | 1 | 汇丰亚洲高入息债券基金，行 759 |
| 待补全行 | 93 | `hsbc_fund_link.py --probe` 实测输出 93 行 |
| HKEX 主数据中的汇丰基金 | 97 | 以 `管理人简称=汇丰` 统计 |
| 主数据未进入爬虫清单 | 3 | ALU745、AOU953、BTG943 |

主数据缺失的三只基金为：

- `ALU745`：`(HSBC) ABF香港创富债券指数基金`
- `AOU953`：`汇丰中国翔龙基金`
- `BTG943`：`ESG短期信用债券`

因此，汇丰缺口由两部分组成：

1. 94 条已进入清单的基金中，93 条没有成功回填 I/J；
2. 3 条基金尚未进入 `爬虫list.xlsx`，适配器即使运行成功也不会处理它们。

## 2. 本次补全结果

已在原生浏览器权限环境中调用：

```bash
PYTHONPATH=code $FACTSHEET_VENV_ROOT/bin/python \
  code/companies/hsbc/hsbc_fund_link.py \
  --backfill --excel 爬虫list.xlsx --sheet 汇丰
```

结果：

- 93 条待补全行中 92 条返回 `OK`，已写回 I/J 列；
- 1 条 `SKIP`：`汇丰环球多元化实际回报基金`，原因是汇丰基金中心与港交所 IFP 均未找到可用月报/财报；
- 原汇丰页现在 94 条中 93 条有月报链接和运行命令；
- 另对主数据中未进入爬虫清单的 3 只基金做了同适配器查询，均未找到可验证月报/财报：`ALU745`、`AOU953`、`BTG943`；
- 回写前已生成备份：`爬虫list.xlsx.hsbc-before-20261124.bak`；完整结果记录在 `data/hsbc_backfill_20261124_result.json`，原始适配器日志为 `data/hsbc_backfill_20261124_native.log`。

因此当前可验证缺口为 4 只，而不是执行前的 96 只：1 只已在爬虫清单中但无可用来源，3 只主数据基金尚未纳入爬虫清单且同样未找到可用来源。

## 3. 根因定位

### 3.1 当前适配器依赖浏览器捕获 SPA 授权请求

`code/companies/hsbc/hsbc_fund_link.py` 并不是直接调用一个稳定的公开目录接口。它首先用 Playwright 打开 HSBC 基金中心，捕获：

```text
POST /api/v1/nav/funds
```

随后才用捕获到的请求体、Referer 和 Authorization 回放目录及搜索请求。这个设计可以绕过前端接口的动态 token，但也意味着：

- 浏览器无法启动、站点未完成渲染、SPA 改版或请求路径改变时，目录阶段整体失败；
- 目录阶段失败会在批量补全前就影响全部待补全基金，不是逐只基金独立失败；
- 在当前受限执行环境内实测命令：

```text
python code/companies/hsbc/hsbc_fund_link.py 汇丰亚洲债券基金 --excel 爬虫list.xlsx --sheet 汇丰
```

返回 `TargetClosedError`，Chromium/Edge 进程因受限环境的 macOS Mach 服务权限限制退出，最终显示“未找到 Chrome/Edge”。因此该环境内的运行没有进入 HSBC 目录查询和月报探测。

随后在受限环境外做了只读复核：

- Chrome、Edge、自带 Chromium 均成功启动；
- HSBC 中文/英文基金中心均返回 HTTP 200；
- 中文目录页成功触发 `POST /api/v1/nav/funds`；
- `汇丰亚洲债券基金` 和 `汇丰环球投资基金 - 超短期债券` 均成功返回可下载月报链接；
- `American Index Fund` 成功通过英国官网回退返回 factsheet 页面。

因此，Playwright 版本、浏览器安装和 HSBC 当前目录接口均可用；需要修复的是正式 Worker 的进程运行权限/部署方式，不能在受限 Codex 子进程里直接执行浏览器补全任务。

这属于 `SOURCE_BLOCKED/ENVIRONMENT` 类问题，不能归类为 `PAGE_FOUND_NO_MONTHLY`。

### 3.2 补全脚本本身只输出契约，不直接修改正式 Excel

`code/common/fund_link_backfill.py` 的 `--backfill` 只输出 `OK`/`SKIP` 行。正式回写由平台 Worker 完成：

1. 复制根目录 `爬虫list.xlsx` 到任务目录 `data/jobs/<job_id>/backfill/爬虫list.xlsx`；
2. 清空待补全行的 I/J；
3. 解析脚本输出并把成功结果写回任务工作簿；
4. 将结果写入 `fund_report_sources` 的来源元数据。

所以：

- 直接在命令行运行适配器，不会把 93 条结果写回根目录 Excel；
- 根目录仍显示“1 条已填、93 条空白”并不代表平台任务从未得到过候选结果；必须同时检查任务事件、任务级工作簿和 `fund_report_sources`；
- 当前根目录 Excel 是覆盖审计底表，不应被当作唯一运行时状态。

### 3.3 适配器的名称匹配偏严格，且依赖英文列

`_resolve_single` 的主路径是中文/英文规范化精确匹配；搜索回退 `_search_record` 在没有英文名称时直接返回空。当前 94 条汇丰行的英文列并不为空，但仍有以下潜在误差源：

- `HSBC Global Investment Funds`、`HSBC Global Funds ICAV`、`HSBC Index Tracker Investment Funds`、`HSBC Managed Funds`、`HSBC Portfolios` 等伞级前缀和香港页名称不完全一致；
- `Local Bond`/`当地债券`、ETF、指数基金、货币基金和管理基金可能只有伞级年报或国际站月报；
- 同名不同份额类别时，接口只返回一组基金记录和多个 ISIN，当前脚本以“能下载的第一个 ISIN”为准，缺少币种/份额类别校验；
- `_has_factsheet` 仅接受 HTTP 200 且 `Content-Type` 包含 `pdf`。若站点返回 `application/octet-stream`、重定向后的非标准 MIME 或需要先带会话 cookie，可能产生假阴性。

这些是第二层原因，必须在浏览器/接口恢复后通过逐行失败分类确认，不能与本次已确认的浏览器启动失败混为一谈。

### 3.4 适配器已写了回退，但回退不能替代目录恢复

当前代码有三类回退：

- 英国 HSBC 官网基金页及英文 factsheet；
- GIF 伞级年报章节；
- 港交所 IFP/指数基金报告章节。

这些回退只在“已找到基金记录但没有可用香港 factsheet”或“香港目录搜索未命中”时触发。若第一步浏览器无法捕获 `nav/funds`，目录加载就整体失败，回退不会逐只启动。因此本次缺口的首要修复点仍是浏览器/SPA 会话，而不是继续增加名称别名。

## 4. 为什么当前看起来像“汇丰仍有很大缺口”

可以用下面的关系解释当前数字：

```text
HKEX 汇丰基金 97
  ├─ 已进入爬虫清单 94
  │   ├─ 已有月报链接 1
  │   └─ 待适配器补全 93
  └─ 尚未进入爬虫清单 3
```

现有全局审计中的“适配器数量”只能回答“是否有 HSBC 适配器”，不能回答“该适配器对本管理人覆盖了多少基金”。所以 HSBC 会出现在“有适配器”集合中，同时仍有很大的基金级缺口。

## 5. 建议的修复顺序

### P0：恢复可重复的目录抓取

1. 在服务运行环境安装并固定 Playwright Chromium；Windows 包内随附浏览器或改为 Edge/CDP 连接，不依赖开发机浏览器。
2. 将浏览器捕获到的 `nav/funds` 请求（URL、body、必要 headers、捕获时间、响应摘要）写入任务日志；失败时明确记录 `BROWSER_LAUNCH_FAILED`、`NAV_REQUEST_NOT_CAPTURED` 或 `NAV_HTTP_ERROR`。
3. 增加无浏览器模式：允许从最近一次有效的 catalog 快照读取基金名/ISIN；快照过期再进入浏览器抓取，而不是整批全部 SKIP。

### P1：提高逐基金可解释性

1. `_has_factsheet` 改为同时校验重定向后的最终 URL、前 5 个字节 `%PDF-` 和常见二进制 MIME；记录 HTTP 状态、Content-Type、最终 URL。
2. 将失败原因标准化为 `NO_FUND_PAGE`、`PAGE_FOUND_NO_MONTHLY`、`SOURCE_BLOCKED`、`NAME_AMBIGUOUS`、`NO_VERIFIABLE_FILE`、`FUND_CLOSED`、`ENVIRONMENT`。
3. 对多个 ISIN按份额类别/币种与 HKEX 基金记录做校验，不能只取第一个可下载 ISIN。
4. 对 index tracker、ICAV、货币基金和管理基金分别做回归样本；当前清单已有这四类代表。

### P1：补齐主数据未入清单的 3 只基金

先按基金代码加入来源对账底表，再决定是接入 HSBC 适配器、HKEX/披露易候选来源，还是标记为无公开月报。不能仅按基金名称追加，避免同名/改名造成错配。

### P2：明确“来源成功”和“下载成功”是两件事

覆盖看板应同时显示：

- `candidate_found`：适配器找到候选 URL；
- `verified_downloadable`：候选 URL 可下载且前 5 字节是 PDF；
- `downloaded`：本批任务已落地 PDF；
- `structured`：解析/结构化完成。

否则一个适配器即使返回了 80 条候选，Excel 仍为空或下载失败时，页面会继续显示为同一类“缺口”。

## 6. 复核命令

只读探测待补全行：

```bash
PYTHONPATH=code $FACTSHEET_VENV_ROOT/bin/python \
  code/companies/hsbc/hsbc_fund_link.py \
  --probe --excel 爬虫list.xlsx --sheet 汇丰
```

单只基金验证目录/网络环境：

```bash
PYTHONPATH=code $FACTSHEET_VENV_ROOT/bin/python \
  code/companies/hsbc/hsbc_fund_link.py \
  "汇丰亚洲债券基金" --excel 爬虫list.xlsx --sheet 汇丰
```

验证时应先确认浏览器捕获阶段成功，再分析名称匹配或 PDF 探测结果；否则所有逐基金结论都不可靠。

