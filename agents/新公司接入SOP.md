# 新公司接入 SOP（爬虫补全 + 下载流水线）

## 2026-11-25 执行记录｜骏利亨德森（Janus Henderson）

- 范围：Sheet2 中管理人“骏利亨德森”24 行；新增同名页表，正式 I/J 保持空白，备份 `爬虫list.xlsx.janushenderson-before-20261125.bak`。
- 官方入口：香港 [Fund Finder](https://www.janushenderson.com/en-hk/investor/product/) 的产品筛选 API，以及 [Documents](https://www.janushenderson.com/en-hk/investor/documents/) 8 页英文 Factsheet 分页。
- 适配器/下载器：`code/companies/janushenderson/janushenderson_fund_link.py`、`download_janushenderson_factsheet.py`；产品页名称归一化兼容 `Horizon Fund -` 命名差异，下载阶段按最新报告日期和 HKD→USD→其他份额优先级选 Factsheet。
- 临时副本回填：**OK 24｜SKIP 0｜FAIL 0**；Balanced Fund 实际 PDF 抽样下载、报告月份识别和归档通过；正式工作簿 I/J 未写入。详见 `docs/development/33-janus-henderson-adapter.md`。
- 覆盖审计：接入后重新运行审计，下一家缺口最大的管理人以最新 `docs/development/artifacts/monthly-coverage-baseline.json` 为准。

## 2026-11-25 执行记录｜保诚瀚亚、瑞士百达、首源投资

- 范围：三个管理人各 27 行；新增“保诚瀚亚”“瑞士百达”“首源投资”页表，正式 I/J 保持空白。
- 官方入口：Eastspring [Downloads](https://www.eastspring.com/hk/funds/downloads)、Pictet [香港基金入口](https://am.pictet.com/hk/en/individuals/funds)、First Sentier [香港文学页](https://www.firstsentierinvestors.com/hk/en/retail/performance/literature.html)。
- 适配器/下载器：分别新增 `code/companies/eastspring/`、`code/companies/pictet/`、`code/companies/firstsentier_global/`；统一注册 `run_all_downloaders.py` 与覆盖审计。
- 临时副本回归：保诚瀚亚 **OK 27｜SKIP 0｜FAIL 0**（并完成 PDF 抽样下载）；瑞士百达 **OK 27｜SKIP 0｜FAIL 0**（静态香港产品 slug，已知 ISIN 下载抽样受香港站反自动化空页面阻断，正式 worker 需用原生浏览器确认 ISIN 文档）；首源投资 **OK 27｜SKIP 0｜FAIL 0**（并完成 PDF 抽样下载）。
- 详细记录：`docs/development/30-eastspring-adapter.md`、`31-pictet-adapter.md`、`32-firstsentier-global-adapter.md`。

## 2026-11-25 执行记录｜霸菱（barings）

- 范围：Sheet2 中 `管理人=霸菱` 的 34 行；新增“霸菱”页表，位于“路博迈”之后，正式 I/J 保持空白。
- 官方入口：Barings 香港英文站 [Sitemap](https://www.barings.com/en-hk/individual/sitemap) 与各基金产品页；产品页的 Factsheet 是月报入口。
- 适配器/下载器：`code/companies/barings/barings_fund_link.py`、`download_barings_factsheet.py`；按官网英文产品名和 slug 精确匹配，requests 失败时使用 Playwright 原生浏览器。
- 临时副本回归：**OK 32｜SKIP 2｜FAIL 0**；抽样下载 **1/1 成功**；正式工作簿未写 I/J。
- 缺口口径：`Baring Global Aggregate Bond Fund` 当前 Barings 香港 sitemap 未列出同名产品页；`Barings European High Yield Bond Fund` 的香港产品路径返回 404。两者均保留 SKIP，不映射其他地区或相近债券策略。详见 `docs/development/29-barings-adapter.md`。
- 覆盖审计下一步：接入后重新运行审计；预期下一家为**保诚瀚亚**，以最新 `docs/development/artifacts/monthly-coverage-baseline.json` 为准。

## 2026-11-25 执行记录｜路博迈（neuberger）

- 范围：Sheet2 中 `管理人=路博迈` 的 29 行；新增“路博迈”页表，位于“资本集团”之后，正式 I/J 保持空白。
- 官方入口：Neuberger [UCITS Funds](https://www.nb.com/products/ucits-funds) 产品页及动态 Fund Literature/Factsheet。
- 适配器/下载器：`code/companies/neuberger/neuberger_fund_link.py`、`download_neuberger_factsheet.py`；按官方 slug 精确匹配产品页，运行时从页面读取最新英文 Factsheet，requests 失败时使用真实浏览器。
- 临时副本回归：补全 **OK 29｜SKIP 0｜FAIL 0**；正式工作簿未写 I/J。本机抽样下载受 nb.com HTTP 429/Vercel 检查影响，已记录并保留原生浏览器兜底。
- 覆盖审计下一步：**霸菱**缺口最大（29 只）。

## 2026-11-25 执行记录｜资本集团（capitalgroup）

- 范围：Sheet2 中 `管理人=资本集团` 的 29 行；新增“资本集团”页表，位于“晋达”之后，正式 I/J 保持空白。
- 官方入口：Capital Group 香港 [Fund Centre](https://www.capitalgroup.com/intermediaries/hk/en/investments/fund-centre.html)；页面内嵌 `fund-data` JSON，包含基金 acronym、ISIN、份额、币种和 Factsheet 可用性。
- 适配器/下载器：`code/companies/capitalgroup/capitalgroup_fund_link.py`、`download_capitalgroup_factsheet.py`；Factsheet 使用官方 `docs.publifund.com/12_FACT/{ISIN}/en_HK`，下载失败时切换真实浏览器。
- 临时副本回归：补全 **OK 28｜SKIP 1｜FAIL 0**；实际下载抽样 **1/1 成功**；正式工作簿未写 I/J。
- 缺口口径：唯一 SKIP 为当前官方目录未返回的 Capital Group European Opportunities (LUX)，不映射到相近欧洲策略，详见 `docs/development/27-capitalgroup-adapter.md`。
- 覆盖审计下一步：**路博迈、霸菱并列 29 只缺口**；按拼音顺序建议先接入路博迈。

## 2026-11-25 执行记录｜晋达（ninetyone）

- 范围：Sheet2 中 `管理人=晋达` 的 30 行；新增“晋达”页表，位于“普信”之后，正式 I/J 保持空白。
- 官方入口：Ninety One 香港 [Funds Literature](https://ninetyone.com/en/hong-kong/funds-literature) 与官方 Factsheet PDF；“晋达”按原 Investec 品牌产品名核验。
- 适配器/下载器：`code/companies/ninetyone/ninetyone_fund_link.py`、`download_ninetyone_factsheet.py`；requests 受 Cloudflare 拒绝时切换真实浏览器下载。
- 临时副本回归：补全 **OK 12｜SKIP 18｜FAIL 0**；正式工作簿未写 I/J。
- 缺口口径：只回填检索到官方同名 Factsheet 的基金；仅有产品页、Key Facts、全球/新加坡版本而未确认香港月报的，保留 SKIP 原因，详见 `docs/development/26-ninetyone-adapter.md`。
- 覆盖审计下一步：**资本集团、路博迈、霸菱并列 29 只缺口**；按拼音顺序建议先接入资本集团。

## 2026-11-25 执行记录｜普信（troweprice）

- 范围：Sheet2 中 `管理人=普信` 的 32 行；新增“普信”页表，位于“恒生”之后，正式 I/J 保持空白。
- 官方入口：T. Rowe Price 香港 [Investment Funds 页面](https://www.troweprice.com/financial-intermediary/hk/en/funds.table.html) 的产品 API 与 Literature Service Monthly Factsheet。
- 适配器/下载器：`code/companies/troweprice/troweprice_fund_link.py`、`download_troweprice_factsheet.py`；运行时读取官方 API key，按 A/港元/美元份额选择 productCode，403 时可切浏览器。
- 临时副本回归：补全 **OK 28｜SKIP 4｜FAIL 0**；统一调度器回填 28 行、抽样下载 **1/1** 成功；正式工作簿未写 I/J。
- 缺口口径：新兴市场本地债券基金、环球高收益债券基金、亚洲机遇股票基金、智选退休配置基金-3 当前没有可安全认领的香港单只 Monthly Factsheet，详见 `docs/development/25-troweprice-adapter.md`。
- 覆盖审计下一步：**晋达**缺口最大（30 只），其次为**资本集团**（29 只）。

## 2026-11-25 执行记录｜恒生（hangseng）

- 范围：Sheet2 中 `管理人=恒生` 的 34 行；新增“恒生”页表，位于“安本标准”之后，正式 I/J 保持空白。
- 官方入口：恒生投资管理香港 [Resources 页面](https://www.hangsenginvestment.com/en-hk/individual-investor/resources/) 及其资源目录接口；API 返回最新 Monthly Factsheet，文档路径需补 `/cms` 前缀。
- 适配器/下载器：`code/companies/hangseng/hangseng_fund_link.py`、`download_hangseng_factsheet.py`；ETF 的 Listed/Unlisted Class 使用同名前缀匹配。
- 临时副本回归：补全 **OK 32｜SKIP 2｜FAIL 0**；统一调度器回填 32 行、抽样下载 **1/1** 成功；正式工作簿未写 I/J。
- 缺口口径：恒生富时香港股票ETF当前目录无单只 Monthly Factsheet；恒生香港股票基金当前资源目录未返回产品名。两者均不以公告/中期报告冒充月报，详见 `docs/development/24-hangseng-adapter.md`。
- 覆盖审计下一步：**普信**缺口最大（32 只），其次为**晋达**（30 只）。

## 2026-11-25 执行记录｜安本标准（aberdeen）

- 范围：Sheet2 中 `管理人=安本标准` 的 34 行；新增“安本标准”页表，插在“宏利”之后，正式 I/J 保持空白。
- 官方入口：安本香港 Fund Centre 的 `funds/overview` 同源 gateway，返回基金、份额、ISIN 和 Factsheet 直链。
- 适配器/下载器：`code/companies/aberdeen/aberdeen_fund_link.py`、`download_aberdeen_factsheet.py`；直连失败时使用 `common.browser_fetch` 的真实浏览器渠道。
- 临时副本回归：补全 **OK 28｜SKIP 6｜FAIL 0**；前沿市场债券基金统一调度器补全→下载 **1/1** 成功；正式工作簿未写 I/J。
- 缺口口径：6 行为当前官方目录未返回，详见 `docs/development/23-aberdeen-standard-adapter.md`。
- 覆盖审计下一步：**恒生**缺口最大（34 只），其次为普信（32 只）。

## 2026-11-25 执行记录｜宏利（manulife）

- 范围：Sheet2 中 `管理人=宏利` 的 38 行；新增“宏利”页表，插在“法巴”之后，正式 I/J 保持空白。
- 官方入口：Manulife 香港 Fund Documents 动态选择器；本轮核验到 16 只稳定 Factsheet 资产代码。
- 适配器/下载器：`code/companies/manulife/manulife_fund_link.py`、`download_manulife_factsheet.py`；纯 requests 403 时切换 `common.browser_fetch` 的真实 Chrome/Edge 渠道。
- 临时副本回归：补全 **OK 16｜SKIP 22｜FAIL 0**；美元入息基金统一调度器补全→下载 **1/1** 成功；正式工作簿未写 I/J。
- 缺口口径：22 行为当前官方文件选择器未核验到单只 Factsheet（不把 KFS/宣传册/通用招股书当月报），详见 `docs/development/22-manulife-adapter.md`。
- 覆盖审计下一步：**安本标准**缺口最大（34 只），**恒生**并列 34 只。

> 适用于为 `爬虫list.xlsx` 新增公司页表并接入“爬虫补全”。参照：大成(dcfund)、博时(bosera)、东方汇理(amundi)、东亚联丰(buim)、贝莱德(blackrock)、富达(fidelity_hk+tw)、富国(fullgoal)、高腾(gaoteng)、海通(gthtam)。
>
> 接入顺序＝公司中文名的**拼音序**（与工作簿页表顺序一致）。
>
> **进度口径**：总公司数＝`Sheet1` 业务清单里的管理人数量（当前 **27** 家）；已接入＝工作簿中已有该公司页表。截至 2026-10-20：**27 / 27 全部建有页表**（柏瑞、贝莱德、博时、大成、东方汇理、东亚联丰、富达、富国、高腾、海通、华夏、汇丰、惠理、汇添富、嘉实、建银国际、首域盈信、摩根、南方、瑞银、睿远、太平、泰康、兴证、易方达、招银国际、中银香港）；各家的未匹配/无月报清单见下文执行记录。

## 阶段 0｜定范围

1. 公司中文名（=页表名）、`管理人` 字段值。
2. 是否**多伞/多站点**（如富达 = HK + TW，需要两个脚本；富国 = SICAV/OFC/全球基金系列/ETF 系列）。
3. 该公司是否已有下载脚本 `download_*.py`；没有先补下载脚本。

## 阶段 1｜建公司页表

1. 新增以公司中文名命名的工作表，列结构对齐现有页表：
   `序号｜基金｜伞｜全称｜英文(可选)｜管理人｜大类｜小类｜是否派息｜月报链接｜运行方式`
2. 从 **Sheet2** 按“管理人=该公司”取全部基金，写入基础列（Sheet2 列序与公司页表一致；Sheet1 无序号/伞/全称）。
3. 从 **Sheet1 / 历史报告 JSON** 取已知的 I/J 填入（历史报告路径：`data/*/reports/download_run_report_*.json` 的 `source_url`/`run_command`）。
4. 校验：**同一基金名跨伞时 C 列必须准确**（伞优先匹配的前提）；无法确定归属的行留空，不写猜测值。

## 阶段 2｜官网摸查

1. 找“基金名 ↔ 代码/产品页”的权威来源（列表页 / JSON 接口 / 菜单树 / 产品总览页签），记录：伞（系列）字段、币种与股份类别、月报可用性判定方式。
2. 记录名称差异：简繁、异体字、系列前缀、尾缀（`(MPF)`、`HK$/US$`、累計/分派等）。
3. 记录反爬情况：是否 403、是否需真实浏览器渠道。以上结论直接作为阶段 3 的输入。

## 阶段 3｜写补全脚本 + 注册

1. 位置 `code/companies/<公司目录>/<公司>_fund_link.py`（多站点每站点一个）；复用 `code/common/fund_link_backfill.py`。
2. 只实现三件事：`catalog_loader()`（官网目录）、`index_builder()`（`(伞, 中文/英文核心名)` 索引）、`resolver(row, index)`（返回 I/J/跳过原因/不认领 None）。
3. 输出契约：`OK\t行\t基金\t伞\tI\tJ`、`SKIP\t行\t基金\t伞\t\t原因`、进度行 `下载进度 - [...]`。
4. 匹配：**伞优先 → 中文核心名 → 英文核心名兜底**（无“英文”列自动回落中文）；多类别取 `港元>美元>其他`、同币种默认第一条；无月报不回填。
5. I/J 模板必须与该下载脚本用法一致（如 `python download_xxx_factsheet.py "<I>"`）。
6. 需真实浏览器时复用 `code/common/browser_fetch.py`（Chrome/Edge 渠道 + Chrome UA + Access Denied 快速失败）。
7. 注册：`run_all_downloaders.py` 的 `BACKFILL_COMPANY_ALIASES` 加“公司中文名→目录名”；一个页表多脚本时登记 `BACKFILL_SHEET_SCRIPTS`。
8. `py_compile` 通过。

## 阶段 4｜CLI 验证（脚本级快检）

1. `--probe`：列出的应正好是 I/J 缺失行。
2. `--backfill`（只打印，不写表）：抽查 OK 的 fundId/URL 与 SKIP 原因是否正确。
3. 端到端：**先把爬虫表复制到 `/tmp`，用 `--crawler-list` 指向副本**再跑 `run_all_downloaders.py --backfill`，确认“回填 → 同批次补跑下载”与报告 `summary`（回填/成功/跳过/失败）。
4. 正式表的 I/J 必须保持为空，留给阶段 5 的 GUI 验收；CLI 验证一律写副本，不得写回正式表。

## 阶段 5｜GUI 端到端验收

1. 运行 `python main.py` → “1. 下载”页选择该公司页表 → 勾选 **“爬虫补全”** → 开始下载。
2. 流程自动完成：**下载已有 I/J 的基金 → 摸查并调用补全脚本回填空缺 I/J（逐行落盘）→ 对补全的基金同批次补跑下载**（均为已实现功能）。
3. 检查：进度条三段、日志汇总（`回填 N｜成功 N｜跳过 N｜失败 N｜共 N`）、结果明细中的“跳过”行。
4. 抽样打开下载的 PDF，确认内容与基金一致（防止跨伞同名下错文件）。

## 阶段 6｜收尾与归档

1. 输出**未匹配清单**（基金 + 原因）交业务确认或另找数据源。
2. 保留备份，便于回滚（如 `/tmp/爬虫list.before_<动作>.xlsx`）。
3. 更新 `agents/AGENTS.md`（公司脚本清单如有变化）。
4. 在 `agents/chat.md` 追加本轮 Q/Intent/Plan/Action；在本文件“执行记录”追加该公司的本轮结果。

## 缺口基金适配经验（必查顺序）

“SKIP”只代表当前脚本没有走通，不代表官网确实没有资料。处理缺口时按以下决策树逐层排查，并记录每一层的证据。

### 1. 先区分四类缺口

1. **目录未收录**：基金详情页或搜索结果实际存在，但批量目录 API 没有返回。
2. **官网已收录但匹配失败**：官方页面能搜到，脚本却因名称、语言、伞名或查询字段处理错误而跳过。
3. **有基金页但没有月报**：基金仍处于存续状态，页面只有产品资料、KID、年报等，没有 Factsheet / Monthly report。
4. **官网确实没有月报**：新认可、暂停披露、已终止，或该产品线本来就不发布月报。

不得直接依据“目录没返回”或“页表名没搜到”得出“官网未收录”；至少验证搜索入口、详情页和同伞产品。

### 2. 官网看似未收录时

1. 逆向官网搜索框的真实请求，确认用的是搜索接口，而不是只抓某个分类列表。重点检查：
   - 参数是字符串还是数组，例如 `searchTerm: ["基金", "名称"]`；
   - 是否只检索当前国家/语言；
   - 是否需要浏览器 token、cookie、CSRF 或 `x-requested-lang`；
   - 是否漏了分页。
2. 用英文名尝试该公司的英文站、国际站或英国站。汇丰案例中，香港目录未收录的 Index Tracker 在英国官网可直接找到，并有最新 `Monthly report`。
3. 如果实际能搜到但脚本仍跳过，按“匹配漏洞”排查：
   - 目录来源是否只覆盖部分 fund type、系列或币种；
   - 中文简繁、`匯/滙/汇`、全角/半角、空格、连字符是否统一；
   - 官网名是否省略了 `Local/当地`、基金/伞名前缀、尾部份额或括号说明；
   - 查询前是否误把官网名截断成过短的通用词；
   - 是否只尝试一个 ISIN，而没有遍历同一基金的其他股份类别；
   - 是否在“目录未命中”时过早返回，没有继续调用已知可用的详情页或月报 API。
4. 如果修正的是匹配逻辑，必须加入一个能约束误配的条件：伞名、中文全名、英文全名或 ISIN 至少有一项强匹配；不能只靠“包含某个地区/资产词”。

### 3. 有基金页但没有月报时

1. 打开基金详情页的 Documents / 文件页签，核对文档类型是否叫 `Factsheet`、`Monthly report`、`基金概览`、`基金月報` 等；不要只搜索“月报”二字。
2. 找**同一伞下已成功下载的基金**作为样板：
   - 对比详情页结构；
   - 在浏览器网络面板确认文档列表/下载接口；
   - 用新基金的 fundId、ISIN、slug 替换样板参数；
   - 验证返回是否 `200 + application/pdf`。
3. 同一基金有多个份额时逐类探测，选择实际有月报的份额；但仍要优先港元、再美元、再其他币种。
4. PDF 文件名含随机 UUID 或哈希时，不要猜链接。优先从基金页或文档 API 现取；若确认该直链是稳定的“始终最新”端点，可复用，但要定期核验内容日期。
5. 同一伞内已知链接可作为**接口结构**的依据，但不能直接把某只基金的 PDF 套给另一只，必须核对首页基金名和数据日期。

### 4. 官网确实找不到月报时

1. 查基金状态：
   - **新认可/新成立**：确认是否尚未发布首期月报，不能把产品资料伪装成月报；
   - **已终止/清算**：确认最后月报、最后完整年报分别在哪一期；
   - **改名/合并**：用旧名、ISIN、英文名和旧伞名交叉搜索。
2. 查公司官网的年度报告、半年度/中期报告和伞级报告；内地公募基金通常没有月报，但可能有最新季度、中期或年度报告，可作为业务允许的替代文件。
3. 查香港证监会认可的港交所综合基金平台 IFP：
   - 先用 `getFundList` 按名称或 ISIN 搜索中央编号；
   - 再用 `getFundFileList` 查询 `FINANCIAL_REPORT`；
   - 从新到旧检查财报，选择**最新一份实际包含该基金正文**的报告，而不是无条件取文件列表第一条。
4. 伞级报告需按章节切分：
   - 优先用目录匹配；
   - 无逐基金目录时，按基金名在连续页面中出现定位；
   - 必须命中 `Statement of Investments`、`Schedule of Investments`、`Statement of Financial Position`、`Portfolio Statement` 等真实章节标题；
   - 标题可能不在连续区间第一页，应检查整个区间的标题；
   - 过滤只有一两页的全伞目录、清算说明、风险表和附录提及；
   - 校验目录页码与 PDF 物理页的偏移；
   - 核对切出文件首页的基金名和报告期。
5. 已关闭基金的“最新报告”不一定仍有完整章节。例如 `Asia ESG Bond` 的 2026 年报和 2025-09 中期报告只含清算说明，应回退到关闭前最后一期完整 2025-03-31 年报的 164–165 页。

### 5. 回填与结论标准

1. 月报优先；确实没有月报时才使用年报/中报，并在归档中明确是“月报”还是“财报章节”。
2. I 列放可稳定复现的基金页、文档页、报告 URL 或 API 文档地址；J 列必须能被 GUI 原样执行。
3. 不得写入未经验证的 URL、随机文件名或仅凭相似名称推断的 ISIN。
4. 完成前至少验证：
   - `--probe` 缺口数量；
   - `/tmp` 副本 `--backfill` 的 OK/SKIP 原因；
   - 每类新通路抽 1 份实际 PDF；
   - PDF 首页基金名、报告类型和报告期与页表一致。
5. 性能要求：目录精确命中后直接复用 ISIN；站内搜索只用于目录缺口。不要把“全量重新搜索”当作防御措施，否则会显著拖慢普通基金。

## 附录：常见坑速查（详见阶段 3/5）

| 坑 | 现象 | 规避 |
|---|---|---|
| 跨系列同名 | 同名基金在 MPF/ORSO 等不同伞各有一支，易配错 | 伞优先匹配（阶段 3.4） |
| 系列前缀不一致 | 页表 `X 系列 - 基金`，官网用裸名或另一前缀 | 归一化去前缀 + 统一货币符号/后缀 |
| 同基金多币种/类别 | 类别几十个 | 港元>美元>其他，同币种默认第一条 |
| 站点 403/自动化指纹 | requests 与自带 Chromium 均 403、Playwright 空等 | `browser_fetch`（真实渠道 + 快速失败） |
| 无“英文”列 | 匹配不到或报错 | 公共模块自动识别，缺失时回落中文 |
| 单元格残留 | 显示空但有值/超链接 | 清空需同时清 `value` + `hyperlink` + 样式 |
| 先跑 CLI 补全把正式表填满 | 正式表 I/J 已有值，GUI「爬虫补全」场景测不到 | CLI 验证用 `/tmp` 副本，正式表 I/J 留空给 GUI |
| 列顺序变动 | 写错列 | 一律按表头名定位列 |

## 执行记录

### 2026-11-25｜法巴（bnp）

- 页表：新增“法巴”（39 行，插在“中银保诚”之后），范围来自 Sheet2 `管理人=法巴` 精确筛选；备份 `爬虫list.xlsx.bnp-before-20261125.bak`。正式 I/J 保持空白。
- 官网摸查：法巴香港 Fund Explorer 使用公开 `https://api.bnpparibas-am.com/push/fundsearchv2/IP_HK-FSE/HKG` 基金目录，文档库使用 `https://api.bnpparibas-am.com/push/doclib/IP_HK-FSE?language=ENG`。单只月报限定为 `DOC_FACTSHEET`，基金页按资产类别、默认份额名称和 ISIN 生成 `fundsheet` 入口；不把 KFS、FP、REPA/REPS 或合订报告当成月报。
- 脚本：`code/companies/bnp/bnp_fund_link.py`、`download_bnp_factsheet.py`；已在 `run_all_downloaders.py` 注册 `法巴/bnp/BNP Paribas/BNP Paribas Asset Management` 别名，在月报覆盖审计中登记管理人。
- 临时副本全量验证：**OK 35｜SKIP 4｜FAIL 0**。3 条为当前 Fundsearch 未命中（法巴可持续亚洲城市债券基金、法巴俄罗斯股票基金、法巴新兴欧洲股票基金），1 条为基金目录存在但没有单只 `DOC_FACTSHEET`（法巴可持续食品基金）。
- 抽样：法巴水资源基金 PDF 下载成功；`run_all_downloaders.py --backfill` 在 `/tmp/bnp-cli.xlsx` 临时副本完成 35 条回填并对 1 条基金补跑成功。正式工作簿 I/J 填充数为 0。
- 覆盖审计：法巴已从未适配器缺口移除，下一家缺口最大的管理人为**宏利（38 只）**，具体数量以 `docs/development/artifacts/monthly-coverage-baseline.json` 最新输出为准。

### 2026-11-25｜中银保诚（bocip）

- 页表：新增“中银保诚”（53 行，插在“联博资产”之后），范围来自 Sheet2 `管理人=中银保诚` 精确筛选；备份 `爬虫list.xlsx.bocip-before-20261125.bak`。正式 I/J 保持空白。
- 官网摸查：中银保诚基金页使用公开 `https://www.boci-pru.com.hk/en/api/UTFund/utListing` JSON 接口；基金详情页路径为 `/en/ut/ut-prices/<utFundCode>`，`pdf.fs` 为单只 Factsheet。接口按份额类别返回重复基金，适配器按基金代码聚合并按港元、美元优先选入口；无 `pdf.fs` 或官网已不收录的历史/MPF产品明确跳过，不回退 KFS、年报或通用资料。
- 脚本：`code/companies/bocip/bocip_fund_link.py`、`download_bocip_factsheet.py`；已在 `run_all_downloaders.py` 注册 `中银保诚/bocip/BOCI-Prudential` 别名，在月报覆盖审计中登记管理人。
- 临时副本全量验证：**OK 44｜SKIP 9｜FAIL 0**。9 条缺口为当前目录未收录 6 条、基金页无单只 Factsheet 3 条；详见 `docs/development/20-bocip-adapter.md`。中银香港港元货币市场基金抽样 PDF 下载成功；统一调度器在临时副本完成回填→补跑抽样成功。
- 当前覆盖审计下一家缺口最大的管理人为**法巴（39 只）**，以审计脚本最新输出为准。

### 2026-11-25｜联博资产（alliancebernstein）

- 页表：新增“联博资产”（54 行，插在“景顺”之后），范围来自 Sheet2 `管理人=联博资产` 精确筛选；备份 `爬虫list.xlsx.alliancebernstein-before-20261125.bak`。正式 I/J 保持空白。
- 官网摸查：联博香港 Fund Finder 使用 `webapi.alliancebernstein.com/v2/funds/hk/en/investor/product-docs`；接口按股份类别重复返回，按 `fundNumber` 去重。单只月报的文档类型为 `productdocdetails-doctype-fact-sheet`，通用 `fact-sheet-book` 不作为单只月报。
- 脚本：`code/companies/alliancebernstein/alliancebernstein_fund_link.py`、`download_alliancebernstein_factsheet.py`；已在 `run_all_downloaders.py` 注册联博资产中英文别名，在月报覆盖审计中登记管理人。
- 临时副本全量验证：**OK 45｜SKIP 9｜FAIL 0**。缺口为 2 只目录未命中、7 只没有单只 Factsheet；详见 `docs/development/19-alliancebernstein-adapter.md`。环球高收益基金抽样 PDF 下载成功，统一调度器回填→补跑抽样成功。
- 覆盖审计：当前下一家缺口最大的管理人为**中银保诚（53 只）**。

### 2026-11-24｜景顺（invesco）

- 页表：新增“景顺”（71 行，插在“建银国际”之后、“首域盈信”之前），范围来自 Sheet2 `管理人=景顺` 精确筛选；备份 `爬虫list.xlsx.invesco-before-20261124.bak`。正式 I/J 保持空白。
- 官网摸查：景顺香港同时维护 `MUTUAL-FUND` 和 `ORSO` 两套目录，统一使用 `dng-api.invesco.com/product/search`；普通基金详情页提供 `data-model-json` 的 Factsheet 文档，文档多数为 Morningstar PDF。ORSO 开放份额目录存在，但公开详情页当前返回 404。
- 脚本：`code/companies/invesco/invesco_fund_link.py`、`download_invesco_factsheet.py`；在 `run_all_downloaders.py` 注册景顺中英文别名，在月报覆盖审计中登记管理人。
- 临时副本全量验证：**OK 45｜SKIP 26｜FAIL 0**。18 条当前目录未命中，7 条 ORSO 公开详情页 404，1 条详情页无 Factsheet；缺口详见 `docs/development/18-invesco-adapter.md`。景顺亚洲动力基金 Factsheet 下载抽样成功。
- 下一步：阶段 5 GUI 端到端验收；若要补 ORSO/旧信托系列，按 SOP 另查历史官网资料和 HKEX IFP 财报正文，不把历史文档当成当前月报。

### 2026-11-24｜施罗德（schroder）

- 页表：新增“施罗德”（81 行，插在“睿远”之后、“太平”之前），范围来自 Sheet2 `管理人=施罗德` 精确筛选；备份 `爬虫list.xlsx.schroder-before-20261124.bak`。正式 I/J 保持空白。
- 官网摸查：Fund Centre 当前目录接口分页返回 78 个份额；文档分类接口按 `shareclassExternalId` 返回 Fund literature/Factsheet 最新 PDF 与历史版本。主站自动化环境可能 403，适配器直接使用官方前端 API。
- 脚本：`code/companies/schroder/schroder_fund_link.py`、`download_schroder_factsheet.py`；在 `run_all_downloaders.py` 注册 `施罗德/schroder/Schroders`，并加入月报覆盖审计。
- 临时副本全量验证：**OK 76｜SKIP 5｜FAIL 0**。缺口为 Salvia 环球配置基金、环球股债增长收息、施罗德灵活配置总回报基金、新兴市场股债、亚洲小型公司；原因分别记录为当前目录未收录或无可验证 Factsheet，详见 `docs/development/17-schroder-adapter.md`。单只欧元企业债券 Factsheet 下载成功并通过 PDF 校验。
- 下一步：阶段 5 GUI 端到端验收；阶段 6 在 Worker 正式回填后再做 D1 来源导入。正式表 I/J 不在 CLI 阶段写入。

### 2026-11-24｜富兰克林邓普顿（franklin）

- 页表：新增“富兰克林邓普顿”（84 行，插在“富国”之后、“高腾”之前），范围来自 Sheet2 `管理人=富兰克林邓普顿` 的精确筛选；备份 `爬虫list.xlsx.franklin-before-20261124.bak`。正式 I/J 保持空白，遵守阶段 4 的临时副本规则。
- 官网摸查：香港 Fund Documents 是动态文档页，公开接口为 `/api/literature/v1/documents?channel=en-hk&cache=true&audience=investor`；当前返回 75 份 Factsheet，记录带 `frkReferenceDate`、`literatureHref` 和 Widen `downloadUrl`。同站另有 Financial Reports 入口，半年报/年报暂未写入月报页表。
- 脚本：`code/companies/franklin/franklin_fund_link.py`（按英文名归一化匹配，运行时取最新 Factsheet）；`code/companies/franklin/download_franklin_factsheet.py`（重读 API、PDF 头校验、标准归档）。已在 `run_all_downloaders.py` 注册中文/英文/目录别名，并加入 `audit_monthly_coverage.py`。
- 脚本验证：`--probe` 识别 84 行待补全；`--backfill` **OK 74｜SKIP 10｜FAIL 0**。10 条缺口均为当前官方文档目录没有可验证 Factsheet，详细基金名见 `docs/development/16-franklin-templeton-adapter.md`，不据此判断已终止。
- 下载抽样：Franklin Technology Fund 的 2026-10 Factsheet 在临时输出根目录下载成功；临时副本的端到端回填已开始并验证 33/74 份下载，因同管理人串行耗时人工中止，正式表未受影响。
- 待办：在正式 Worker/GUI 完成阶段 5 回填后再做 D1 来源导入；10 条缺口按 SOP 决策树继续查基金页、英文站/系列文档和 HKEX IFP 财报正文。

### 2026-10-06｜富国（fullgoal）

- 页表：`富国`（8 支，含“英文”列）；争议的两只债基 I/J 已彻底清空（值+超链接+样式），备份 `/tmp/爬虫list.before_clear_dispute.xlsx`。
- 脚本：`code/companies/fullgoal/fullgoal_fund_link.py`（伞优先 → 中文核心名 → 英文核心名；目录来自官网 `fuguoproducts` 4 页签实测的 5 个产品页）。
- 注册：`BACKFILL_COMPANY_ALIASES["富国"] = "fullgoal"`。
- 本轮（批次 `fg_round_001`）：**回填 4｜成功 4｜跳过 3｜失败 1**。
  - 成功：精选投资级债券(OFC)、投资级债券(全球基金系列)、中国A股(SICAV)、中国中小盘成长(SICAV，原有值)。
  - 跳过：全球科技基金、全球医药基金、恒生港股红利ETF（官网无产品页）。
  - 失败：亚太红利基金（产品页无月报文件，报“下载地址不存在”）。

### 2026-10-06｜高腾（gaoteng）

- 页表：`高腾`（5 支，全部在「高腾微基金」伞下，含“英文”列；插在`富国`之后），基础列取自 Sheet2，备份 `/tmp/爬虫list.before_gaoteng_add.xlsx`。
- 官网摸查：目录走公开接口 `api.gaotengasset.com/gt/website/productService?lang=<lang>&fund_id=10001`（一次返回伞内全量）；月报走 `reportListNoToken&type=3`。5 支基金 `fundId`：亚洲收益 10001、微财货币 10008、微金美元货币 10018、中国人民币债券 10029、精选投资级债券 10039。**无 403/指纹问题，requests 直连可用**；英文站（`lang=en-us`）无数据，故英文名用页表“英文”列固化兜底。
- 脚本：`code/companies/gaoteng/gaoteng_fund_link.py`（合并 `zh-hk`/`zh-cn` 两套目录按 `fundId` 去重；伞优先 → 中文核心名 → 英文核心名）。
- 注册：`BACKFILL_COMPANY_ALIASES["高腾"] = "gaoteng"`。
- 脚本级验证（批次 `gt_round_001`）：**回填 5｜成功 5｜跳过 0｜失败 0**，5 份月报（2026 年 8/9 月）下载成功；抽样比对 PDF 内文标题与页表基金一一对应。
- ⚠️ 本轮踩坑：上述验证误用了**正式表**，把 `高腾` 表 I/J 填满了，导致 GUI「爬虫补全」场景无法验收；已按“清 `value` + `hyperlink` + 样式”清空正式表 I/J 并重新 `--probe` 确认 5 行待补全。备份 `/tmp/爬虫list.before_clear_gaoteng_ij.xlsx`。**今后 CLI 验证一律用 `/tmp` 副本（阶段 4.3）。**
- 说明：亚洲收益基金历史 J 为 `python download_gaoteng_factsheet.py`（默认 10001），本轮统一改为显式带 `fundId` 的写法，5 行风格一致。
- 待办：GUI 端到端验收（阶段 5）由用户执行。

### 2026-10-06｜海通（gthtam）

- 页表：`海通`（8 支，含“英文”列；插在`高腾`之后），基础列取自 Sheet2，备份 `/tmp/爬虫list.before_haitong_add.xlsx`。
- 官网摸查：品牌已并入国泰海通，域名仍是 `gthtam.com.hk`。目录在资料库页面**服务端渲染**的 `FundsDocSelect` 下拉里（`/asm/zh-chs/Home/library/38?series=Funds`），实测 8 支基金 id：亚洲总收益债券 38、美元货币 99、港元货币 96、环球人民币收益 52、亚太精选债券 102、大中华远景 87（已终止）、全球未来科技ETF非上市 115、全球战略资源ETF非上市 118。月报＝资料页的「基金概览」条目。无 403/指纹问题，requests 直连可用。
- 脚本：`code/companies/gthtam/gthtam_fund_link.py`（复用下载脚本的 `GthtamLibraryParser`/`fetch_page`；目录解析 + 逐个资料页判定是否存在「基金概览」；中文核心名优先 → 英文兜底）。
- 注册：`BACKFILL_COMPANY_ALIASES["海通"] = "gthtam"`。
- 端到端（批次 `ht_round_001`，**跑在 `/tmp` 副本上，正式表 I/J 留空给 GUI**）：**回填 5｜成功 5｜跳过 3｜失败 0**，5 份「基金概览」（2026-08）下载成功，PDF 内文与页表基金一一对应。
- 未匹配清单（交业务确认）：海通大中华远景基金（官网标记“已终止”，无基金概览）、海通低碳先锋股票基金、海通香港股票基金（官网资料库未收录）。
- 待办：GUI 端到端验收（阶段 5）由用户执行。

### 2026-10-06｜华夏（chinaamc）

- 页表：`华夏`（27 支，含“英文”列；插在`海通`之后），基础列取自 Sheet2，备份 `/tmp/爬虫list.before_huaxia_add.xlsx`。
- 官网摸查：SPA + jeecg-boot。**基金目录接口免登录可用**：`/jeecg-boot/mainFundPerformance/tMainFundPerformance/getAllFundComprehensiveInfo`（返回 73 支，含 `ssFundId`/`ssFundNameSc`/`Tc`/`En`）；文档接口 `/jeecg-boot/mainWebDailyDocument/tMainWebDailyDocument/listByFundId?fundId=` 亦免登录。其余 `tMainWebFund/list` 等会 401，勿使用。
- 脚本：`code/companies/chinaamc/chinaamc_fund_link.py`（复用下载脚本的 `fetch_documents`/`FACTSHEET_CATEGORY` 判定月报；中文简体/繁体优先 → 英文兜底）。
- 注册：`BACKFILL_COMPANY_ALIASES["华夏"] = "chinaamc"`。
- 端到端（批次 `hx_round_001`，跑在 `/tmp` 副本上，正式表 I/J 留空）：**回填 20｜成功 20｜跳过 7｜失败 0**。
- 未匹配/跳过清单（交业务确认）：华夏回报证券投资基金、华夏兴华混合型证券投资基金（MRF 基金，官网无月报类别）、华夏港美人工智能ETF、华夏数字黄金ETF、华夏全球固定收益+基金、华夏臻选离岸人民币债券基金（均为新基金/ETF，官网无月报）、华夏亚洲ESG债券基金（官网基金库未收录，官网仅有「华夏恒指ESG ETF」）。
- 命名坑：官网「华夏精选稳定收益基金（此基金并不是货币基金）*」带尾部括注+星号，归一化需反复剥离尾部括注与 `*` 才能命中。
- 待办：GUI 端到端验收（阶段 5）由用户执行。

### 2026-10-06｜汇丰（hsbc）

- 页表：`汇丰`（94 支，含“英文”列；插在`华夏`之后），备份 `/tmp/爬虫list.before_hsbc_add.xlsx`、回填历史 I/J 前 `/tmp/爬虫list.before_hsbc_history_fill.xlsx`。
- 官网摸查：基金中心是 SPA，清单接口 `POST /api/v1/nav/funds` **需要 Bearer token**（`/api/v1/token/issue` 按路由下发，页面 JS 自动带上）。做法：真实浏览器打开 `/zh/intermediary/fund-centre` 捕获一次请求（URL+请求体+Authorization），再用 requests 分页回放（`paging.currentPage` 1→4，清空 `appliedFilters`）；中/英两套目录各取一次，共 79 支。月报接口 `GET /api/v1/download/document/<isin>/hk/zh/factsheet` **纯 requests 直连可用**，且 LU/IE/CNE 等任意注册地 ISIN 都能取到 PDF。
- 脚本：`code/companies/hsbc/hsbc_fund_link.py`（浏览器捕 token → requests 回放取目录；中文名/英文名双索引；逐行用流式探测月报是否可下载）。
- 下载脚本最小改动：`download_hsbc_factsheet.py` 原先只认 `hk\d{10}`，导致 LU/IE 注册地基金全部失败（`MissingSchema`）。新增 `ANY_ISIN_RE`，`is_hk_source`/`extract_hk_isin` 放开到任意 ISIN（HK 行为不变），修复后 LU/HK 均实测可下载。
- 注册：`BACKFILL_COMPANY_ALIASES["汇丰"] = "hsbc"`。
- 历史回填（阶段 1.3）：`汇丰亚洲高入息债券基金` 官网基金中心未收录，但 Sheet1/历史报告有 ISIN `hk0000317518` 且实测可下载，已按历史值填入正式表。
- 端到端（批次 `hsb_round_002`，跑在 `/tmp` 副本上；`--workers 3` 降并发避开官网限流）：**成功 70｜跳过 24｜失败 0**，70 份月报（含 LU/IE ISIN）落盘，抽读 PDF 标题与页表基金一一对应。
- 未匹配清单（交业务确认，24 支）：HSBC Index Tracker 系列（American/European/Japan/FTSE 100/Pacific Index Fund）、汇丰环球基金ICAV 若干 UCITS ETF、汇丰环球投资基金若干（环球企业债券、前沿市场、环球房地产股票、欧元债券、亚洲ESG债券等）、汇丰集合投资信托若干、汇丰组合 1-5、汇丰晋信两只内地基金等——官网香港基金中心未收录或已终止。
- 坑：首次端到端用默认 5 并发时出现 39 条“网络连接失败”（官网限流），降到 3 并发后 0 失败；浏览器捕 token 偶发 `Page.goto` 超时（重试即可）。
- 待办：GUI 端到端验收（阶段 5）由用户执行；失败批次 `data/hsb_round_001` 可删。

### 2026-10-07｜惠理（valuepartners）

- 页表：`惠理`（18 支，含“英文”列；插在`汇丰`之后），备份 `/tmp/爬虫list.before_valuepartners_add.xlsx`。
- 官网摸查：目录取官网**简体基金总览页** `https://www.valuepartners-group.com/sc/products-services-sc/funds-sc/`（服务端渲染，静态 HTML 里 23 个产品页链接；注意 `/sc/investment-solutions/institutional-funds/` 直接访问会 404）。站点证书链不完整，requests 需 `verify=False`（下载脚本用浏览器上下文绕开）。月报=页面“文件”页签里的**基金月刊**下拉（点开才由 JS 加载，故下载脚本必须渲染）。
- 脚本：`code/companies/valuepartners/valuepartners_fund_link.py`（解析总览页 name↔URL；匹配=中文核心名去系列前缀 → 英文名↔产品页 slug 兜底）。
- 注册：`BACKFILL_COMPANY_ALIASES["惠理"] = "valuepartners"`。
- 历史回填（阶段 1.3）：`惠理价值基金`、`惠理高息股票基金` 从 Sheet1 取已知 I/J 填入。
- 端到端（批次 `vp_round_001`，跑在 `/tmp` 副本上，`--workers 3`）：**回填 16｜成功 18｜跳过 0｜失败 0**；逐份比对 PDF 标题与页表基金一一对应（个别 PDF 为繁体版本，比对需先做简繁归一）。
- 待办：GUI 端到端验收（阶段 5）由用户执行。

### 2026-10-07｜汇添富（99fund）

- 页表：`汇添富`（10 支，含“英文”列；插在`惠理`之后），备份 `/tmp/爬虫list.before_99fund_add.xlsx`。
- 官网摸查：首页 `/main/hkweb_new/hk/` 返回 **403**，但产品页/列表页正常（200）。基金目录在“香港认可基金”总览页 `/main/hkweb_new/hk/products/HKfunds/index.shtml`，左栏每支基金写成 `<p fundId="XXX">基金名</p>`（注意锚文本是免责声明的“同意”，必须按 `fundId` 属性取值）。实测官网只有 6 支香港认可基金；页面上另有 `083008/083107/1` 等 404 的失效链接，勿采信。页面编码用 `gb18030/utf-8` 兜底解码（下载脚本已有 `fetch_page`）。月报＝产品页里的「基金月報/基金月报」文档表。
- 脚本：`code/companies/99fund/99fund_fund_link.py`（复用下载脚本的 `fetch_page`；解析 fundId→名称；逐支抓产品页判定月报存在；中文核心名去“汇添富/CUAM”前缀后匹配）。
- 注册：`BACKFILL_COMPANY_ALIASES["汇添富"] = "99fund"`。
- 历史回填（阶段 1.3）：`汇添富中港策略基金` 从 Sheet1 取已知 I/J（页面 id `888888`）。
- 端到端（批次 `cuf_round_001`，跑在 `/tmp` 副本上）：**回填 5｜成功 6｜跳过 4｜失败 0**；6 份月报 PDF 内文（繁体基金名）与页表一一对应。
- 未匹配清单（交业务确认）：汇添富价值精选/民营活力/医药保健混合型证券投资基金（内地互认基金，香港站无产品页）、汇添富人民币货币基金（香港站仅有「人民币债券基金」）。
- 待办：GUI 端到端验收（阶段 5）由用户执行。

### 2026-10-07｜嘉实（harvest，本轮补了下载脚本）

- 页表：`嘉实`（12 支，含“英文”列；插在`汇添富`之后），备份 `/tmp/爬虫list.before_harvest_add.xlsx`。
- 官网摸查：站点有免责声明门（`/hgi/index.php?landing`，会下发 `ReVisit` 放行 cookie）——**必须先请求 landing 再访问其他页面**。基金清单在总览页内嵌的**转义 JSON** 里：`https://www.harvestglobal.com.hk/hgi/index.php/funds/list-view` → `{...,"funds":{"ACTIVE":[{fund_code,fund_name}...],"MRF":[...],"PASSIVE":[...]}}`（4 个分组共 17 支）。基金页 URL 为 `/hgi/index.php/funds/<active|mrf|passive>/<fund_code>`，文档列表同样是内嵌 JSON（`fund_file_title` / `fund_file_media_pdf_info.media_url` / `fund_file_launch_date`），PDF 在 S3。
- 新增下载脚本：`code/companies/harvest/download_harvest_factsheet.py`（先过 landing；解析月报类文档 `Monthly Factsheet / 基金月報 / Monthly Return Report`；取最新一期，同月按 港元>美元；用法 `python download_harvest_factsheet.py "<基金页URL>"`）。
- 新增补全脚本：`code/companies/harvest/harvest_fund_link.py`（拉总览页 JSON 建目录；逐支抓基金页判定月报；**按页表“英文”列匹配官网英文名**，因为官网无中文名）。
- 注册：`BACKFILL_COMPANY_ALIASES["嘉实"] = "harvest"`。
- 端到端（批次 `hv_round_001`，跑在 `/tmp` 副本上）：**回填 4｜成功 4｜跳过 8｜失败 0**，4 份 PDF 内文与页表基金对应。
- 未匹配/跳过（交业务确认）：嘉实成长收益、嘉实研究精选（MRF，官网仅季度报告）、嘉实美元?（no，该支已成功）、嘉实国际美元投资级债券收息基金、嘉实中银香港多元收益基金（官网无月报文档）、嘉实中美科技50 ETF（官网名为 `Harvest Tech 50 ETF`，英文名不再含 G2，未做猜测映射）、嘉实全球房地产机遇基金／嘉实亚洲股息基金／嘉实中国多元资产入息基金（官网总览未收录）。
- 说明：Sheet1 里 `嘉实ESG中国股票基金` 的历史 I/J 走的是 FSMOne 第三方平台，本轮改用官网（HCEF）生成，未沿用历史值。
- 待办：GUI 端到端验收（阶段 5）由用户执行。

### 2026-10-08｜建银国际（ccbintl）

- 进度：本表接入后，Sheet1 业务清单 27 家公司已接入 **16** 家。
- 页表：`建银国际`（4 支，含“英文”列；插在`嘉实`之后），备份 `/tmp/爬虫list.before_ccbintl_add.xlsx`。
- 官网摸查：繁体站左侧产品导航一次性给出全部产品页（`<li><div><a href="/Big5/managementN.html">基金名</a></div></li>`）：management3=国策主导、4=人民币收益、5=全球精选、8=美元货币市场、9=全球投资级短期债券。月报＝页面里的「基金月刊」入口（下载脚本默认标签）。站点证书链不完整，requests 需 `verify=False`（下载脚本本就忽略校验）。
- 脚本：`code/companies/ccbintl/ccbintl_fund_link.py`（解析导航建目录；逐支抓产品页判定「基金月刊」；中文核心名去“建银国际/建銀國際”前缀后匹配）。
- 注册：`BACKFILL_COMPANY_ALIASES["建银国际"] = "ccbintl"`。
- 端到端（批次 `ccbi_round_001`，跑在 `/tmp` 副本上）：**回填 4｜成功 4｜跳过 0｜失败 0**，4 份月报落盘且内文与页表基金对应。
- 待办：GUI 端到端验收（阶段 5）由用户执行。

### 2026-10-08｜无人值守模式（摩根 / 南方 / 首域盈信）

- 模式变更：不再限制“每轮一家”，依次推进剩余公司；每完成一家即归档，遇到阻塞先记录再跳到下一家，最后回头攻坚。
- **首域盈信（firstsentier）✅（简化接入）**：Sheet2 无该公司行，业务清单（Sheet1）只有 1 支 `首域盈信中国增长基金`；页表按 Sheet1 建（1 行），I/J 直接用 Sheet1 已知值（`https://www.firstsentierinvestors.com/hk/zh/retail/performance/literature.html` + `python download_firstsentier_factsheet.py "FSSA-China-Growth-Fund"`）。已注册 `BACKFILL_COMPANY_ALIASES["首域盈信"]="firstsentier"`，**暂未写 `firstsentier_fund_link.py`**（沿用柏瑞“先注册别名、后补脚本”的先例）；若后续加入更多该司基金，再按阶段 3 补脚本（文献页为服务端渲染，101 个 PDF 链接，脚本化不难）。备份 `/tmp/爬虫list.before_firstsentier_add.xlsx`。
- **摩根（jpmorgan）⏸ 阻塞（待回攻）**：产品页 URL 形如 `/hk/zh/asset-management/per/products/<slug>#/documents`，但基金清单由 AEM `FundsMarketingHandler` 前端异步加载，`POST /hk/zh/asset-management/per/products/FundsMarketingHandler` 返回 **403（CSRF）**；`fund-explorer` 页在无头浏览器里未渲染出基金表格（networkidle 超时，DOM 仅 4 个链接）。可用的线索：`fund-documents` 页内嵌了 `documents = {"<ISIN>":[{documentType,docURL,title}]}`（含全部基金文档，369 个 ISIN），可作为下载数据源；下一步需要用真实浏览器补全 CSRF/会话后再取基金名→slug 映射。
- **南方（csop）⏸ 阻塞（待回攻）**：站点对 requests 一律 **403**，需真实浏览器（下载脚本本身就用 Playwright）。`/tc/products`、`/en/products` 渲染后仍取不到产品表格（DOM 无产品链接），但 `sitemap.xml` 暴露了 102 个 en 产品页 slug（如 `us-dollar-bond`、`china-new-balance`）。下一步需先解决 slug ↔ 基金名映射（部分基金如“黄金ETF/增强债券”在 sitemap 里没有直白 slug）。

### 2026-10-08｜摩根（jpmorgan）✅ 主力接入（38/100）

- 页表：`摩根`（100 支，取自 Sheet2 管理人「摩根大通」；含“英文”列；插在`首域盈信`之后），备份 `/tmp/爬虫list.before_jpmorgan_add.xlsx`。
- **接口突破**（之前判定阻塞的原因）：基金清单要走站点自己的请求
  `GET /FundsMarketingHandler/fund-explorer?country=hk&role=per&userLoggedIn=false&language=zh`
  —— 纯 requests 返回 **400**（经 `digitalmarketing-ifa-app-proxy` 代理校验），但**在浏览器上下文里由站点自己发起即可 200**，返回 271 条份额记录（含 `displayName` 英文名、`displayNameTranslatedName` 繁体中文名、`identifier` ISIN、`currencyCode`、`fundIdentifier`）。脚本用 Playwright 打开 `fund-explorer/retail-funds` 页捕获该响应，并保留「用捕获请求头由 context.request 重放」的兜底。
- 产品页 URL 规律（实测可用）：`/hk/zh/asset-management/per/products/<displayName 转小写连字符>-<isin 小写>#/documents`，与 Sheet1 历史写法完全一致。
- 月报判定：`fund-documents` 页内嵌 `documents = {"<ISIN>":[{documentType,docURL,title}]}`，其中 `documentType = "Fact Sheet (US EMEA)"` 即月报（页面显示为“基金報告”）。
- 脚本：`code/companies/jpmorgan/jpmorgan_fund_link.py`（浏览器取清单 → 按 `fundIdentifier` 聚合份额、同基金取 港元>美元>其他 → 中文核心名（去“摩根/基金/系列”与尾部份额括注）优先、英文名兜底（含“互相包含”宽松规则）→ 用 ISIN 判断月报可用）。
- 注册：`BACKFILL_COMPANY_ALIASES` 增加 `"摩根"`/`"摩根大通"`/`"jpmorgan"`。
- 端到端（批次 `jpm_round_001`，跑在 `/tmp` 副本上，`--workers 3`）：**回填 38｜成功 38｜跳过 62｜失败 0**，抽读 PDF 均为“基金報告 2026年8月31日”。
- ⚠️ 未覆盖 59 支（交业务确认/后续回攻）：以 `摩根基金 - xxx基金`、`摩根投资基金` 等**SICAV/系列名**为主（美国科技、美国、大中华、日本股票、俄罗斯、新兴欧洲…）。原因：`role=per`（零售）清单只返回 77 支基金（271 份额），这些 SICAV 系列多半挂在其它角色/渠道清单下；后续可尝试 `role=adv`、`institutional` 或 `fund-explorer` 其它 tab 的同一接口。另有 3 支官网无 Fact Sheet。

#### 摩根后续回攻（2026-10-08 第二轮）：59 → 2

同一接口支持 **`fundType` 分区**，四类合并后 591 个份额（去重）：
`retail-funds`(271) / `sar-funds`(24) / `provident-funds`(22) / `mainland-hk-mrf-funds`(3)（并在会话内用 `context.request.get` 直连四类，见脚本 `EXPLORER_FUND_TYPES`）。
命中率从 38/100 提升到 **98/100**，关键三步：
1. **多变体键匹配**：官网份额名是 `JPM美國科技（美元）－A股（累計）`，页表是 `摩根基金 - 美国科技基金`，两侧结构不同 → 各自产出「整名 / 首个括注前段 / 系列末段」三类核心名再比对；允许 2 字核心名（美國/中國/台灣）；
2. **异体字与标注**：`大韓→韓國`、`台灣→臺灣`、去 `*`；英文侧切掉 `A (acc)` 份额标记并剥离结尾份额字母（`Emerging Markets Equity A` → `emergingmarketsequity`）；
3. **份额优选**：同一基金多份额时，优先“有月报（Fact Sheet）”的份额，其次 港元>美元>其他（此前会挑到没有月报的份额而误判 SKIP）。
结果：批次 `jpm_round_002` → **回填 98｜成功 98｜跳过 2｜失败 0**（98 份 PDF 均为“基金報告 2026年8月31日”）。
仍未覆盖 2 支（合理）：`摩根基金 - 大韩股票基金`（LU0301634860，卢森堡 SICAV，香港文件库无 Fact Sheet；同名的 `摩根南韓`(HK0000055712) 是另一支基金，未做猜测映射）、`摩根宜安投资等级企业债券基金`（官网基金清单未收录该名称）。

### 2026-10-08｜南方（csop）✅ 回攻成功

- 页表：`南方`（10 支，取自 Sheet2；插在`摩根`之后），备份 `/tmp/爬虫list.before_csop_add.xlsx`。
- 官网摸查：站点对 requests 一律 403 → 必须真实浏览器。**产品目录可从 9 个英文分类页抓取**：
  `/en/products/etf?FundName=<分类>&type=<ETF|FUND>`（分类：Commodity/Covered Call/Thematic/Regional/Fixed Income/Money Market/HK Index ETFs、Hong Kong Authorized Funds、Mainland-HK MRF），表格里的产品链接形如 `/en/products/<slug>`，实测共 71 个产品。`sitemap.xml` 里的 slug 不全且无法区分在售/失效，改用分类页。
- 脚本：`code/companies/csop/csop_fund_link.py`（浏览器逐分类抓目录；英文名归一化后先精确匹配、再“前缀包含”兜底；**并复用下载脚本的 `latest_monthly_newsletter()` 判定该产品是否真的有月报**）。
- 注册：`BACKFILL_COMPANY_ALIASES["南方"] = "csop"`。
- 端到端（批次 `csop_round_002`，跑在 `/tmp` 副本上，`--workers 3`）：**回填 4｜成功 4｜跳过 6｜失败 0**。
- 跳过 6 支（合理）：5 支 ETF（KOSPI 200 备兑认购/黄金/富时亚洲科技/KOSPI 200/野村富时港日现金流）官网**没有 Monthly Newsletter**（ETF 只有产品页与持仓，无月报文件）；`南方多利增强债券型证券投资基金` 官网产品列表未收录。
- 坑：第一版只做“互相包含”兜底，把 `南方多利增强债券…` 误配到 `CSOP Enhanced Bond Fund`；收紧为“前缀包含”后修正，并补上月报可用性判定，避免把无月报的产品写进表。失败批次 `data/csop_round_001` 可删。

### 2026-10-08｜瑞银（ubs）✅

- 页表：`瑞银`（36 支，取自 Sheet2；插在`南方`之后），备份 `/tmp/爬虫list.before_ubs_add.xlsx`。
- 官网摸查：基金表 `https://www.ubs.com/hk/tc/assetmanagement/funds/asset-class.html` 需真实浏览器渲染，选择器 `#app table tbody tr`；每行 10 列 = ISIN｜基金名(含份额后缀)｜日期｜币种｜淨值｜基金章程｜**基金月報**｜產品資料概要｜年報｜中期報告（共 161 行）。文档链接形如 `https://www.ubs.com/2/e/files/RET/FS_RET_<ISIN>_HK_ZH.pdf`。
- 脚本：`code/companies/ubs/ubs_fund_link.py`（渲染表格 → 按“中文核心名”聚合份额：去括注/类别标记(A類/B類…)/币种/“瑞銀” → 同基金优先“有月报”的份额，其次 港元>美元>其他）。
- 注册：`BACKFILL_COMPANY_ALIASES["瑞银"] = "ubs"`。
- 端到端（批次 `ubs_round_001`，跑在 `/tmp` 副本上，`--workers 3`）：**回填 34｜成功 34｜跳过 2｜失败 0**，抽读 PDF 均为“資料截至 2026 年 8 月月底”的月度报告。
- 跳过 2 支（合理）：`瑞银欧洲精选股票基金`、`瑞银科技机会股票基金` 官网表格里无「基金月報」列链接（该行其它文档有，月报缺失）。

### 2026-10-20｜睿远（foresight）✅

- 页表：`睿远`（1 支：睿远中国股票基金；取自 Sheet2；插在`瑞银`之后），备份 `/tmp/爬虫list.before_foresight_add.xlsx`。
- 官网摸查：官网页 `gmjj` 是 JS 渲染，但背后 API 纯 requests 可用：
  `https://webhk.foresightfund.com.hk/ry-api/prod/prodinfo?id=<id>`（PROD_NAME/TC/EN、FUND_CODE_TA）
  与 `/ry-api/prod/report?id=<id>&type=yb`（月报列表）。产品 id 为小整数（睿远中国股票基金 = 1，FCEF01）。
- 脚本：`code/companies/foresight/foresight_fund_link.py`（扫描 id 1–20 建目录；月报判定用 `type=yb` 列表是否非空；中文核心名去“睿远/基金”后匹配，英文兜底）。
- 注册：`BACKFILL_COMPANY_ALIASES["睿远"] = "foresight"`。
- 端到端（批次 `fs_round_001`，跑在 `/tmp` 副本上）：**回填 1｜成功 1｜跳过 0｜失败 0**，PDF 内文为“睿遠中國股票基金”。

### 2026-10-20｜太平（taiping）⚠️ 页表已建，下载受阻（未算完成）

- 页表：`太平`（3 支，含 Sheet2 管理人「太平」1 支 +「太平资管」2 支；插在`睿远`之后），备份 `/tmp/爬虫list.before_taiping_add.xlsx`。
- 阻塞事实（实测）：
  1. 官网资料页 `https://www.tpfh.cntaiping.com/ywjs-taipingdzhinfo/` 对 requests 返回 **412**，对无头浏览器返回**空页**（content 长度 39），无法枚举目录与文档；
  2. 现有下载脚本 `download_taiping_factsheet.py` 只支持 `TGCNMEB`（太平大中华新动力股票基金），内置兜底清单最近一期 PDF `https://www.cntaiping.com/upload/cms/tpfh/202607/14141111q5a8.pdf` 现返回 **HTTP 400**（换 UA/Referer 均无效，判定为失效或反爬加严）。
- 脚本：`code/companies/taiping/taiping_fund_link.py`（白名单 + **PDF 探活**：探活失败不认领，输出 SKIP 原因，避免 GUI 出现失败行）。
- 注册：`BACKFILL_COMPANY_ALIASES` 增加 `"太平"`/`"太平资管"`/`"taiping"`。
- 端到端（批次 `tp_round_002`）：**成功 0｜跳过 3｜失败 0**，三条均有明确原因（页面拦截 / PDF 失效）。
- 待办（需人工/业务）：确认太平三支基金是否仍在售、官网新文档页地址与真实 PDF 规律；恢复后把探活 URL 换成新地址即可自动放行。
- 2026-10-20 补充核查：用户提供的 `https://www.cntaiping.com/upload/cms/tpfh/202604/291122483gbi.pdf` **可正常下载**（HTTP 200 / application/pdf / 1.04MB / 7 页），内容为「太平大中華新動力股票基金 - **產品資料概要**（2026 年 4 月）」，**不是**月报（基金概覽）。说明 PDF 主机正常、此前 202607 月报 400 是**单文件失效**；但资料页 `www.tpfh.cntaiping.com/ywjs-taipingdzhinfo/` 对 requests/无头 Chrome 一律 412（带 UA/Referer/协议切换、浏览器 cookie 均无效），`/upload/` 目录也 412，故仍无法枚举当月月报文件名（`/upload/cms/tpfh/YYYYMM/<随机串>.pdf`）。下一步：由能通过 WAF 的机器打开资料页取「基金概覽」当期链接，或由业务提供当月 PDF URL。
- 2026-10-20 证监会披露站核查（链接 `http://eid.csrc.gov.cn/fund/disclose/mutual_show_pdf_id.do?reportPdfId=3573479`）：可下载，200 / PDF 1.7 / 558KB / 14 页，内容为「太平大中华新动力股票基金 - **产品资料概要**（2026 年 8 月，内地销售版，R 类人民币份额）」，同样不是月报。
  站内互认基金检索接口：`/fund/disclose/mutualAfficheSearchData.json`（筛选字典）、`mutual_list_page_orderBy_report_send_date.do`（列表）、`mutual_show_pdf_id.do?reportPdfId=<idStr>`（PDF）。**报告类型全表**：基金发行（招募说明书/产品资料概要/发售公告/信托契约）、定期报告（年度/一至四季度/中期）、净值公告、临时公告、其他——**没有“月报/基金概览”类别**，所以证监会披露站无法替代太平（及任何 MRF 基金）的香港月报来源。
- 2026-10-20 月报直链打通（用户提供）：`https://www.cntaiping.com/upload/cms/tpfh/202610/15093400vuxd.pdf` = **太平大中華新動力股票基金 2026 年 10 月月报**（数据截至 2026-09-30，2 页 / 307KB）。下载脚本支持直接传 PDF（`python download_taiping_factsheet.py "<pdf>"`）。已把 `taiping_fund_link.py` 的 `SUPPORTED` 改为携带 `pdf_url` 并探活，端到端批次 `tp_round_003` → **回填 1｜成功 1｜跳过 2｜失败 0**。
  **注意**：`/upload/cms/tpfh/YYYYMM/<随机串>.pdf` 的文件名不可推导，需每月更新 `SUPPORTED[*]["pdf_url"]`（或由能过 WAF 的机器提供当期链接）；官网 `www.tpfh.cntaiping.com` 对本环境仍 412。剩余两支（太平美元货币市场、太平投资级债券）仍缺当期 PDF 直链。
- 2026-10-20 脚本改造为**配置驱动**：新增 `code/companies/taiping/taiping_links.json`（`{"links": {"基金全名": "当期月报PDF"}}`），`taiping_fund_link.py` 读取配置→逐条探活（200+PDF）→命中回填 I=PDF、J=`python download_taiping_factsheet.py "<PDF>"`，失效/未配置均输出 SKIP 原因。**以后补其它基金只需在 JSON 里加一行，不用改代码**。
  已验证：批次 `tp_round_004` → 回填 1｜成功 1｜跳过 2｜失败 0。仍待补齐：太平美元货币市场基金、太平投资级债券基金的当期月报直链（官网 412，需能过 WAF 的机器复制链接）。
- 2026-10-20 **改为从资料页自动发现（最终形态）**：不再依赖随机直链配置。
  1. 目录：`/sc/ywjs-jj/` 基金列表表格（`| 太平美元货币基金 TPMMFUA HK … | [查阅](…/ywjs-taipingmmfundinfo/) |`），得到「基金名 + 代码 + 基本资料页」；三支基金资料页分别为 `ywjs-taipingdzhinfo`（大中华新动力）、`ywjs-taipingmmfundinfo`（美元货币）、`ywjs-taipingfundinfo`（投资级债券）。
  2. 资料页里月报长这样：`基金概览 (二零二六年九月)_TGCNMEB [下载 ![icon](…) ](https://www.cntaiping.com/upload/cms/tpfh/202610/15093400vuxd.pdf)` —— 解析「基金概览 (中文日期)_代码」标题 + 其后第一个 PDF 链接，按中文数字日期取最新。
  3. WAF：`www.tpfh.cntaiping.com` 对本机 412，脚本**直连失败自动改用 `https://r.jina.ai/<url>`** 取同一页面（内容一致），使脚本在本环境也能跑通。
  4. 匹配（按 SOP）：伞名+基金名 → 中文核心名 → 英文核心名 → 包含兜底；`taiping_links.json` 现只保留**可选的 `pages` 覆盖**（某基金资料页变动时用）。
  5. 实测（批次 `tp_round_005`）：**回填 2｜成功 2｜跳过 1｜失败 0** —— 大中华新动力 2026-10、投资级债券 2026-10 均成功；美元货币资料页**没有“基金概览”条目**（只有基金说明书/产品数据概要/净值），输出 SKIP「该基金基本资料页无『基金概览』月报条目」。

### 2026-10-20｜泰康（taikang）✅

- 页表：`泰康`（11 支，取自 Sheet2；插在`太平`之后），备份 `/tmp/爬虫list.before_taikang_add.xlsx`。
- 官网摸查：接口纯 requests 可用（证书链不完整，需 `verify=False`）：
  `GET https://hk.taikangasset.cn/api/product/fundListTree/hk` → 分类树 + `childProduct[{fundName,fundCode}]`；
  月报 `POST /api/tblProductInformation/getList`（`{"language":"hk","fundCode":…,"pageNum":1,"pageSize":100}`）→ records 里含“月報”的 PDF。
- 脚本：`code/companies/taikang/taikang_fund_link.py`（树接口建目录；**复用下载脚本的 `latest_monthly_report()`** 取最新月报直链并探活；匹配＝伞名+基金名 → 中文核心名（去“泰康开泰/基金/市场”）→ 英文名 → 包含兜底）。
- 注册：`BACKFILL_COMPANY_ALIASES["泰康"] = "taikang"`。
- 端到端（批次 `tk_round_001`，`--workers 3`）：**回填 10｜成功 10｜跳过 1｜失败 0**；抽读 PDF 为 2026-09 月度资料（净值截至 2026/09/30）。
- 跳过：`泰康开泰全球精选50股票基金`（接口无月报记录）。

### 2026-10-20｜兴证（xyzq）✅

- 页表：`兴证`（4 支，取自 Sheet2 管理人「兴证国际」；插在`泰康`之后），备份 `/tmp/爬虫list.before_xyzq_add.xlsx`。
- 官网摸查：SPA + 公开 CMS 接口（纯 requests 可用）：
  目录 `GET /official-website/cms/list?path_key=zcgl:jjlb&pageSize=1000&pageIndex=1&lang=1` → 4 支基金（cols 内 `fundId/fundCode/fundName`）；
  文档 `path_key=jjlb:jjda:<fundId>`（下载脚本已有 `fetch_documents`）。**注意：接口必须带 `lang` 参数，缺省时返回 0 条**。
- 脚本：`code/companies/xyzq/xyzq_fund_link.py`（CMS 目录 + `fetch_documents` 判定月报；匹配＝伞名+基金名 → 中文核心名（去“兴证国际/核心精选系列/基金”，统一“环球=全球、级别=级”）→ 英文名 → 包含兜底）。
- 注册：`BACKFILL_COMPANY_ALIASES` 增加 `兴证／兴证国际／xyzq`。
- **下载脚本最小改动**：`download_xyzq_factsheet.py` 的 `select_latest_document()` 原先直接取最新一条；实测 `2026年9月基金月报` 在 OSS 上已 404（列表仍在）。改为**按日期倒序逐条探活，取最新可下载的一期**，全部失效才回退首条。
- 端到端（批次 `xz_round_002`，`--workers 3`）：**回填 3｜成功 3｜跳过 1｜失败 0**；中国核心资产回退到 2026-08（2026-09 文件 404），两支货币基金为 2026-09。
- 跳过：`兴证国际精选全球投资级债券基金`（接口只有产品资料概要/基金注释备忘录，无月报）。

### 2026-10-20｜易方达（efunds_hk）✅

- 页表：`易方达`（35 支，取自 Sheet2；插在`兴证`之后），备份 `/tmp/爬虫list.before_efunds_add.xlsx`。
- 官网摸查：产品列表页 `https://www.efunds.com.hk/sc/products/` 服务端渲染，34 个产品（`/sc/products/<id>/info`，锚文本含“权益:/固定收益:”前缀）；文档页 `/sc/products/<id>/documents/` 的 `<select>` 里含 `MonthlyFact`（月报）等类型，复用下载脚本 `fetch_page`/`parse_documents`。
- 脚本：`code/companies/efunds_hk/efunds_fund_link.py`（列表页建目录；`parse_documents` 判定 `MonthlyFact.versions` 非空即“有月报”；匹配＝伞名+基金名 → 中文核心名（去“易方达/香港/基金/系列”）→ 英文名 → 包含兜底）。I=文档页，J=`python download_hk_documents.py <id>`。
- 注册：`易方达／efunds／efunds_hk／efundshk`。
- **通用修复**：`run_all_downloaders.py` 的脚本发现逻辑只对**目录名**去下划线/连字符、对别名没去，导致 `efunds_hk` 目录匹配不上；现两侧都归一化（`efunds_hk ↔ efundshk`），后续带下划线的公司目录不再踩坑。
- 端到端（批次 `ef_round_003`，`--workers 3`）：**回填 23｜成功 23｜跳过 12｜失败 0**；抽读 PDF 为 2026-08 月报。
- 跳过 12 支：10 支为内地互认/未在港站上架的产品（如易方达安心回报、科翔、价值成长等），2 支港站产品无月报。首轮曾因 `MonthlyFact` 键名判定写成中文而 0 命中，已修正。
- 2026-10-20 **内地站适配**（针对上述 12 支跳过项）：
  1. 内地官网 `https://www.efunds.com.cn/fund/<code>.shtml` 有「基金月度资料」板块（复用 `efunds_cn/download_monthly_report.py` 的 `fetch_page`/`parse_monthly_reports`），但**只有香港互认（968xxx）基金有**；内地公募基金页只有公告/季报/年报。
  2. 实测映射：`968117/968118/968119` = 易方达（香港）精选债券基金；`968233` = 易方达（香港）中国股票股息基金。**全量扫描 968000–968999 后，易方达仅此两类**；其余易方达香港产品内地站没有。
  3. 四支内地公募基金代码（东方财富代码库）：安心回报 `110027`、科翔 `110013`、价值成长 `110010`、中债7-10年国开行 `003358` —— 页面均**无“基金月度资料”**（0 条），属正常（内地公募不披露月报）。
  4. 证监会 EID「内地公示」站亦无月报类别（只有发行文件/定期报告（年/季/中期）/净值/临时公告），不能作为月报来源。
  5. 脚本已接内地兜底：新增 `code/companies/efunds_hk/efunds_links.json`（`mainland_codes` 名称→代码），港站未收录/无月报时按配置抓内地「基金月度资料」；缺失则输出精确原因（“内地站(code)无基金月度资料”/“两站均未收录”）。
  - 端到端复跑 `ef_round_004`：仍 **回填 23｜成功 23｜跳过 12｜失败 0**（无新增覆盖，但 4 支内地基金现在有明确结论：官网无月报）。
- 2026-10-20 **再降一级：定期报告**。内地站既无「基金月度资料」时，下载页面「最新公告」里最新一条**定期报告**（中期/年度/季度）。
  - 新增 `code/companies/efunds_cn/download_periodic_report.py`（按页面顺序取第一条标题含“年度/中期/季度报告”的 PDF；`--all` 可下全部）。
  - `efunds_fund_link.py` 内地兜底链改为：**月报 → 定期报告 → SKIP**：
    I = `https://www.efunds.com.cn/fund/<code>.shtml`，J = `python download_periodic_report.py <code>`。
  - `run_all_downloaders.py` 的 `DISCOVERY_PATTERNS` 增加 `download_periodic_report.py`（否则报“下载配置不完整”）。
  - 端到端 `ef_round_006`：**回填 27｜成功 27｜跳过 8｜失败 0**；4 支内地基金均下到 **2026 年中期报告**（47–60 页），与用户示例一致。
  - 剩余 8 支：6 支两站均未收录（疑为已终止/未上架）、2 支港站无月报。

### 2026-10-20｜招银国际（cmbi）✅

- 页表：`招银国际`（5 支，取自 Sheet2；插在`易方达`之后），备份 `/tmp/爬虫list.before_cmbi_add.xlsx`。
- 官网摸查：产品页 `https://www.cmbi.com.hk/zh-HK/mutualfund?id=<id>` 服务端渲染；基金名在 `<p class="banner-text">`，文档在 `.list-block`（`.list-title` 如“基金月報/基金半年報/基金說明書/產品資料”）。产品 id 为小整数，实测 1/4/5/6/7。
- 脚本：`code/companies/cmbi/cmbi_fund_link.py`（扫描 id 1–20 建目录；`基金月報` 块存在即认领；匹配＝伞名+基金名 → 中文核心名（去“招銀國際/基金/系列”、统一“環球=全球”）→ 英文 → 包含兜底）。
- 注册：`BACKFILL_COMPANY_ALIASES["招银国际"] = "cmbi"`。
- 端到端（批次 `cm_round_001`，`--workers 3`）：**回填 4｜成功 4｜跳过 1｜失败 0**，PDF 数据截至 2026-09。
- 跳过：`招银国际AI股票基金`（页面只有產品資料/基金說明書/基金半年報，无「基金月報」块）。

### 2026-10-20｜中银香港（bochk）✅ 收官

- 页表：`中银香港`（20 支，取自 Sheet2；插在`招银国际`之后），备份 `/tmp/爬虫list.before_bochk_add.xlsx`。
- 官网摸查：`https://www.bochkam.com/cn/` 列出 18 个 `fund-detail-*.html`（横幅图，无锚文本）；逐个详情页取 `<h1 class="extendedWidth">` 得基金名；月报＝页面里的 `FFS_*.pdf`（Fund Fact Sheet）。
- 脚本：`code/companies/bochk/bochk_fund_link.py`（列表页+详情页建目录；`FFS_*.pdf` 判定月报；匹配＝伞名+基金名 → 中文核心名（去“中银香港/基金/系列”）→ 英文 → 包含兜底）。I=详情页，J=`python download_bochk_factsheet.py "<I>"`。
- 注册：`BACKFILL_COMPANY_ALIASES["中银香港"] = "bochk"`。
- 端到端（批次 `bk_round_001`，`--workers 3`）：**回填 18｜成功 18｜跳过 2｜失败 0**；抽读 PDF 为「2026 年9 月（截至 2026 年8 月31 日）」。
- 跳过 2 支：`中银收益混合型证券投资基金`、`中银持续增长混合型证券投资基金`（内地 BOC 基金，香港资管站未收录）。
- **收官**：Sheet1 业务清单 27 家公司**全部建有页表**（27/27）。

### 2026-10-20｜页表列结构调整 + 全量覆盖摸查

- **删除「全称」列**：27 张公司页表统一去掉第 4 列“全称”（与用户手工页表一致），备份 `/tmp/爬虫list.before_drop_fullname.xlsx`；逐格比对除“全称”外的所有单元格 **零差异**，高腾已回填的 I/J 值随列左移后完好。
- **代码适配**：回填/扫描本来就按表头名定位 I/J（`月报链接`/`运行方式`），无需改列坐标；仅 `common/fund_link_backfill.load_fund_rows()` 的 `full_name` 原写死第 4 列，已改为按表头取（无该列时为空），避免误读“英文”列。
- **全量摸查**（`/tmp` 副本，27 家 × 775 行，跑各公司 `--backfill` 统计）：已覆盖 **629 行 / 775 行 = 81.2%**（覆盖=已有 I/J 的历史行 + 本轮补全成功行）。分家：柏瑞 22/32、贝莱德 95/101、博时 6/15、大成 9/11、东方汇理 38/43、东亚联丰 17/24、富达 104/137、富国 5/8、高腾 5/5、海通 5/8、华夏 20/27、汇丰 70/94、惠理 18/18、汇添富 6/10、嘉实 4/12、建银国际 4/4、首域盈信 1/1、摩根 98/100、南方 4/10、瑞银 33/36、睿远 1/1、太平 2/3、泰康 10/11、兴证 3/4、易方达 27/35、招银国际 4/5、中银香港 18/20。
- **内地财务报告适配现状**：易方达已实现（月度资料 → 定期报告，4 支内地基金下到 2026 中期报告）。其余内地基金（华夏回报/兴华、南方多利、中银收益/持续增长、嘉实成长收益/研究精选、汇添富若干、博时/大成部分）尚未适配：其内地官网多为 JS 渲染（华夏官网 50KB shell 无报告链接、天天基金公告亦为 JS 加载），需逐家找数据接口或代码映射，列入后续。

#### 内地财务报告逐个探路记录（2026-10-20）

待适配内地基金与代码（东方财富代码库映射）：华夏回报 002001 / 华夏兴华 519908；南方多利 202103(A)/202102(C)；中银收益 163804 / 中银持续增长 163803；嘉实成长收益 070001 / 嘉实研究精选 070013；汇添富价值精选 519069 / 民营活力 470009 / 医药保健 470006；博时信用债券 050011 / 特许价值 050010。

各站实测结论：
- 证监会 EID 公募披露：`publicContent.json` 仅 243 只基金名录，无按代码的报告 PDF；`fund_detail_search.do` 是 JS 外壳 → 不可用。
- 天天基金：`fund.eastmoney.com/<code>.html` 与 `fundf10.eastmoney.com/jjgg_<code>.html` 均无静态报告链接（JS 加载）→ 不可用。
- 华夏 `chinaamc.com/fund/002001/`：50KB JS 外壳，无报告链接。
- 南方 `nffund.com`：首页仅 11 个链接，基金页 `/main/fundinfo/<code>.shtml` 404 → 需找新接口。
- 中银 `bocim.com`：基金页为 `/Channel/56094?fundCode=<code>`（服务端渲染），报告列表仍需进一步定位。
- 嘉实 `jsfund.cn`：基金列表 `/main/fund/index.shtml` 可用，报告链接未在静态页。
- 汇添富 `99fund.com/main/products/pofund/<code>/fundgk.shtml`：200，但只有 fundgk/fundyield/fundzh 三个子页，无公告页；"中期报告"仅为引用文字。
- 博时 `bosera.com/fund/050011.html`：200 但无报告链接（JS）。

#### 早期手工适配公司的补全脚本排查（2026-10-20）

排查 27 张页表与脚本的对应关系，发现**只有 2 家缺补全脚本**：

| 公司 | 行数 | 缺口 | 处置 |
|---|---:|---:|---|
| 柏瑞（pinebridge） | 32 | 10 | 新增 `code/companies/pinebridge/pinebridge_fund_link.py` |
| 首域盈信（firstsentier） | 1 | 0（已填） | 新增 `code/companies/firstsentier/firstsentier_fund_link.py` |

- 柏瑞：MetLife 基金页为 JS 渲染且先弹免责声明（requests/无头浏览器均只拿到 258–280KB 外壳），`assets.pinebridge.com` 的 PDF 直链带不可推导的 uuid。脚本改为**从已填行反推目录**（按“伞+基金名”精确索引，避免跨伞同名误配），已登记的行可补，未登记输出 SKIP「需人工补充 PDF 直链」。实测 10 支仍为缺口。
- **直链是否每月变？不必变**：抽查 3 条已知 `assets.pinebridge.com/asset/<uuid>/...PDF`，当前均返回 **2026-09-30 数据**的最新月报，响应头无 Last-Modified/ETag，说明该 UUID 地址是**“始终最新”的稳定端点**，每月由服务端覆盖同名 PDF；无需改时间戳/日期字段，拿到一次 URL 即可长期复用。
- **数据质量修复**：核对 22 条已知直链的 PDF 文件名，发现 `柏瑞亚洲高收益总回报债券基金` 指向的是 `PineBridge Asia Dynamic Asset Allocation Fund Class L`（与“亚洲动态资产配置”同一 URL，属复制错配）。已清空该行 I/J（备份 `/tmp/爬虫list.before_pb_fix.xlsx`），柏瑞缺口由 10 变 **11**（32 行中 21 行有效）。

### 2026-10-21｜富达 ILAS（FGIF 伞级报告按章节切分）✅ 方案 B

- 背景：FGIF（富达环球投资基金）是 ILAS/MPF 平台的伞子基金，官网无逐支基金产品页；官方只发布伞级半年度/年度报告。已确认 `https://www.fidelity.com.cn/media/Fund_Report/MRF/fgif-semi-annual-report-2026-mrf.pdf`（353 页）为 **截至 2026-06-30 的半年度报告**，目录覆盖全部子基金。
- 新增 `code/companies/fidelity_hk/download_fidelity_ilas_factsheet.py`：
  1. 下载伞级报告到 `data/_cache/fidelity_ilas/`（一次缓存，31 次复用）；
  2. 解析第 2–4 页目录，得到「子基金名 → 起止页」（实测目录页码＝PDF 物理页索引）；
  3. 名称归一化（简繁、引号、括号、空格、`美国↔美洲`、`富达/储蓄易` 前缀差异）；
  4. 用 PyMuPDF `insert_pdf` 切出该子基金章节，落盘到当前批次目录。
- `fidelity_fund_link.py` 增加 ILAS 分支：伞为「富达环球投资基金」时，从报告目录匹配子基金 → I=伞级报告 URL，J=`python download_fidelity_ilas_factsheet.py "<基金名>"`；目录里找不到才输出 SKIP。
- 端到端（批次 `ff_ilas_001`，`--only 环球投资基金`）：**回填 31｜成功 31｜失败 0**，31 个独立 PDF 页数与目录一致（如增长基金 251–260 共 10 页），首页均为对应子基金的「资产净值表」。
- 覆盖率更新：**629 → 660 / 775 = 85.2%**（富达由 104/137 提升到 135/137，仅剩 2 支）。
- **报告链接自适应（2026-10-21）**：I 列不再写死 2026 文件名。
  1. `resolve_latest_report_url()` 按官方命名规律探测近 5 年的 `fgif-semi-annual-report-YYYY-mrf.pdf` 与 `fgif-annual-report-YYYY-mrf.pdf`，按“期间截止日”选最新（实测 2026 半年报 2026-06-30 优于 2025 年报 2025-12-31）；下载时总是使用该最新一期，即使表内 I 文本过期也不影响下载内容。
  2. `--refresh-links` 模式：把「富达」表 31 行 ILAS 的 I 列统一刷新为当前最新报告 URL（J 不变）。
  3. 回填（`--backfill`）时 `fidelity_fund_link.py` 也写入当前解析到的最新 URL。
  - 验证：`--refresh-links` → 「最新 FGIF 报告 …2026…（期间截至 2026-06-30）；已刷新 0 条」（已是最新）；动态解析后切分“增长基金”仍为 251–260 页。

### 2026-10-21｜公共“伞级报告章节切分” + 汇丰 GIF 年报兜底

- 新增 `code/common/annual_report_split.py`（通用）：
  `download_report()`／`parse_toc_entries()`（支持「名 + a-b」与「名 + 起始页」两种目录版式）／
  `resolve_entry()`（归一化名 + 别名 + 包含兜底）／`extract_pages()`／`split_to_file()`。
- 富达 `download_fidelity_ilas_factsheet.py` 已改为调用该公共模块（行为不变，回归通过：增长基金 251–260）。
- 新增 `code/companies/hsbc/download_hsbc_annual_factsheet.py`：下载 HSBC GIF 年报（Audited Annual Report FY ended 31 Mar 2026，1,064 页，缓存）→ 解析英文目录 → 按基金英文名切章节。
- `hsbc_fund_link.py` 增加兜底：`HSBC Global Investment Funds` 子基金在“无月报/未收录”时，改用年报章节；I=年报 URL，J=`python download_hsbc_annual_factsheet.py "<英文名>"`。
- 实测汇丰补全：**77 OK / 16 SKIP**（原 69/24，新增 8 支年报章节）；端到端 `hs_annual_001` 下载 8 支章节（各 1–7 页，内容为 `Statement of Investments as at 31 March 2026`）。
- 注意：HSBC 年报目录的“逐基金条目”只给 **Statement of Investments** 页码；净资产表/运营表/关键数据是按整本列示、非按基金分节，故切分结果是“投资组合章节”，不等同于整本财务报表。
- 汇丰剩余 16 支缺口属于其它伞/产品线：Index Tracker（5）、ICAV（2）、Portfolios/Money Funds/Jintrust/Managed 等（9，含亚洲ESG债券未入年报目录）。

#### 港交所 IFP 基金文件 API（2026-10-21 实测）

- 页面：`https://ifp.hkex.com.hk/fund-repository/zh-HK/fund/<基金中央编号>`（Next.js SPA）。
- 文件列表接口：
  `GET /fund-repository/ifp/api/v1/fund/getFundFileList?fundId=<ID>&docTypeParent=<TYPE>&page=1&size=5&lang=zh_HK`
  - **必须带请求头 `x-requested-lang: zh_HK`**，并用浏览器会话里的 `bm_s/bm_so/bm_lso` cookie；缺任一项返回 **500**（不是 403）。
  - `docTypeParent`：`OFFERDOC`（销售文件）/ **`FINANCIAL_REPORT`（财务报告）** / 通告等。
  - 返回 `data[].fundFileVOList[].docUrl`（相对路径），PDF 地址 =
    `https://ifp.hkex.com.hk/fund-repository/ifp/api/v1/fund/<docUrl>`（302/直出均可）。
- 示例：`AGE753`（HSBC Index Tracker – American Index Fund）返回 **5 份财务报告**，最新为 `HSBC Index Tracker Report - Annual Report 20260515.pdf`（551 页）。
- 新增 `code/companies/hsbc/download_hsbc_it_factsheet.py`：浏览器取 cookie → API 取最新年报 → 解析目录（英国 OEIC 目录页码 = PDF 索引 + 1，偏移 −1）→ 复用 `common.annual_report_split` 切章节。
- `hsbc_fund_link.py` 增加 Index Tracker 兜底（按 C 列伞名 `HSBC Index Tracker Investment Funds` 识别）。
- 实测：汇丰补全 **77 → 82 OK / 11 SKIP**；`hs_it_001` 下载 5 支（American 31 页 / European 28 / FTSE 100 54 / Japan 28 / Pacific 31），内容均为对应子基金完整章节。

#### 汇丰补全逻辑漏洞修复（2026-10-21）

- 现象：`汇丰环球基金ICAV - 环球新兴市场政府当地债券指数基金`（ISIN `IE00BH4GHJ02`）被判定“未收录”，但基金页与 factsheet API 均正常。
- 根因：`hsbc_fund_link.py` 的目录只来自 `FundsMarketingHandler/fund-explorer` 的 4 个 fundType 列表；部分 ICAV/ETF 基金不在其中 → 拿不到 ISIN → 直接返回“未收录”，**从未尝试 factsheet API**（该 API 对任意 ISIN 可用）。
- 修复：新增 `code/companies/hsbc/hsbc_links.json`（`isins`: 基金名→ISIN），`_resolve_single` 在目录未匹配时先查配置 ISIN 并探活 factsheet；命中则回填 I=基金中心页、J=`download_hsbc_factsheet.py <isin>`，否则才走 GIF/Index Tracker 年报兜底或 SKIP。
- 验证：`ie00bh4ghj02` factsheet 返回 200/PDF（5 页，月度报告 2026-09-30）；汇丰补全 **82 → 83 OK / 10 SKIP**。
- 上述静态 `hsbc_links.json` 方案已废弃并删除，改用下述官方搜索接口；不要再用基金名→ISIN 的硬编码字段兜底。

#### 汇丰可靠名称/ISIN 解析与英国官网优先规则（2026-10-21 最终版）

- **匹配顺序固定为**：
  1. 香港基金中心目录已有精确中文/英文名 → 直接使用目录 ISIN，并探活月报；
  2. 香港目录未命中 → 调香港基金中心站内搜索；
  3. 香港搜索仍未命中且页表有英文名 → 查英国官网；
  4. 仍无月报 → GIF 年报章节；
  5. 最后才走港交所 IFP 搜索 + 最近一份实际含该基金章节的财报。
- 香港基金中心搜索：
  - 页面入口 `https://www.assetmanagement.hsbc.com.hk/zh/intermediary/fund-centre?s=<名称或ISIN>`；
  - 前端实际请求为 `POST /api/v1/nav/funds`，核心字段 `searchTerm: ["词1", "词2"]`（数组，不是字符串）；
  - 响应 `funds[].name` 和 `funds[].shareClasses[].data[columnId=UniqueIdentifier]` 提供中文名与 ISIN；
  - 每次进程用浏览器捕获一次 token，之后由 requests 回放；不得对已在目录精确命中的普通基金重复搜索。
- 英国官网仅补香港缺口：
  - 入口 `https://www.assetmanagement.hsbc.co.uk/en/individual-investor/funds?s=<英文名>`；
  - 同样调用本域 `POST /api/v1/nav/funds`；
  - 月报 API 为 `GET /api/v1/download/document/<ISIN>/gb/en/factsheet`，内容标题明确为 `Monthly report`；
  - 只有香港目录/搜索均未命中的英文基金才进入英国官网，避免同名跨市场误配。
- Index Tracker 实测优先走英国月报，不再默认切年报：
  - American（GB0000469741）
  - European（GB0000468776）
  - FTSE 100（GB0000407618）
  - Japan（GB0000150150）
  - Pacific（GB0000150606）
- 港交所名称搜索：
  `GET /fund-repository/ifp/api/v1/fund/getFundList?page=1&size=100&fund=<英文名>&lang=en_HK`
  配合 `x-requested-lang: en_HK` 可返回英文 `fundId`；不再硬编码 AGE753/AIV664 等基金编号。
- 港交所财报章节定位新增 `find_contiguous_section()`：按基金名在连续页面中出现定位；若提供章节标题关键词，必须命中 `Schedule of Investments`、`Statement of Investments`、`Statement of Financial Position`、`Portfolio Statement` 等标题，避免把清算说明或全伞附表当成基金正文。标题可出现在连续区间的任意一页，不能只看首页。
- 封闭基金边界案例：`Asia ESG Bond` 于 2025-05-16 关闭；2026 年报及 2025-09 中期报告只有清算说明，不采用。最终取关闭前最后一期完整 2025-03-31 年报第 164–165 页（PDF 索引 163–164）。
- 管理基金边界案例：`HSBC Managed Stable Growth Fund` 由 IFP 搜索得到 `AIV664`，最新 2026-01-31 中期报告 PDF 索引 57–67（11 页），首页为对应基金的 `Statement of Financial Position`。
- 回归结果：汇丰原 10 个缺口全部解决；7 支使用香港基金中心搜索恢复月报，1 支使用英国官网月报，2 支使用 IFP 财报章节。另有 5 支 Index Tracker 从年报切分改走英国官网月报。汇丰 **94/94 行覆盖**，全量 27 家预计由 673/775 提升到 **683/775（88.1%）**。
- 首域盈信：静态目录（文献页 + `FSSA-China-Growth-Fund`），1/1 已覆盖，脚本已可被编排发现。

### 2026-11-04｜中金（cicc）全量基金接入

- 页表：`爬虫list.xlsx` 新增 `中金`，按 HKEX 主数据补齐 7 只中金基金：BOG055、BPL776、BQE115、BSC770、BSQ366、BVS643、BWO333；工作簿当前 783 条来源行。
- 稳定产品页：BOG055→`/sc/product/documents/4?pro=1`，BQE115→`/sc/product/documents/4?pro=7`，BPL776→`/sc/product/documents/5?pro=5`，BVS643→`/sc/product/documents/5?pro=11`，BWO333→`/sc/product/documents/5?pro=13`。两只已终止基金 BSC770/BSQ366 仍使用中金基金系列历史文件页 `/sc/product/documents/4?pro=1`，不冒充存续子基金页面。
- 官网摸查：产品文件页 `https://cicchkam.com/sc/product/documents/4?pro=1` 同时列出上市类别、非上市类别月报及基金系列中期/年度报告。直链文件名含发布日期和报告期，不能作为长期来源键。
- 下载器：新增 `code/companies/cicc/download_cicc_factsheet.py`。每次运行重新解析产品页，按 `monthly + listed/unlisted` 或 `semiannual/annual` 选择最新 PDF；支持中英文日期、中文简繁标题和 `YYYYMM` 文件名，下载前校验 `%PDF-`。
- 链接补全：新增 `code/companies/cicc/cicc_fund_link.py`，I 列写稳定产品文件页，J 列写 `python download_cicc_factsheet.py "<产品页>" monthly listed`；不再固化 2026-09 月报 PDF。中金已注册到 `BACKFILL_COMPANY_ALIASES`。
- 半年报：`中金` 页表新增“半年报链接/半年报运行方式”两列，当前同样保存产品文件页和 `... semiannual` 命令；扩展 crawler 读取器、D1 导入及来源 API，把两字段持久化到 `fund_report_sources.semiannual_report_url/semiannual_run_command`。半年报任务沿用现有独立来源优先规则。
- 验证：`py_compile`、API `compileall` 通过；crawler 读取测试与下载来源测试 **6 passed**；离线样例验证能分别选出 2026-09 上市/非上市月报和 2026-06-30 中期报告。
- UI：基金详情“数据来源”在半年报/年报来源下优先打开 `semiannual_report_url`，不再回退到月报入口。
- 补全脚本：`cicc_fund_link.py --backfill` 现在会对中金表全部 7 行写入月报/半年报稳定页和两套运行方式；产品目录页不可访问时使用已核验映射兜底。`--probe` 和 `run_all_downloaders.py --sheet 中金 --list` 已验证 7 行。
- 目标报告选择：运行方式带目标基金名，半年报使用 `all` 份额类别；同一页面同时存在系列报告和子基金报告时优先目标子基金，避免 BQE115 页面误选 BPL776 报告。
- 终止基金边界：BSC770（碳期货 ETF）和 BSQ366（中国股票基金）官方资料显示已终止，产品页没有当前月报；其月报命令保留为可追溯入口，运行时若无匹配会明确报“没有匹配的 monthly 文档”，半年报/年度报告仍可从基金系列历史文件页追溯。未写入失效 PDF 直链。
- 验证：crawler 读取 783 行、其中中金 7 行；`test_hkex_data.py` 与 `test_download_sources.py` 共 6 passed；Python compileall 通过。

### 2026-11-04｜中金过程归档补充与南方东英（csop）全量接入

- 中金过程归档：先从 HKEX 主数据按管理人筛选基金，再核验官网稳定产品文件页；月报命令携带 `monthly listed "目标基金名"`，半年报命令携带 `semiannual all "目标基金名"`；同页出现伞级报告和子基金报告时按目标名优先，避免同日期误选；官网目录失败时仅使用已核验稳定页兜底，不写随机 PDF 直链；正式表和导入计数同步更新并用 `/tmp` 副本做脚本验收。
- 南方东英范围：HKEX 主数据中管理人“南方东英资产管理有限公司”共 **70** 行，新增 `南方东英` 页表；保留既有 `南方` 页表中的南方基金/历史来源，不与南方东英管理人混用。
- 官网入口：CSOP 站点对普通 requests/无头 Chromium 可能返回 403，脚本继续使用真实 Chrome/Edge 浏览器；产品目录按 ETF、L&I、货币市场、固收、商品、备兑认购、区域和香港授权基金等分类抓取。新增 L&I 分类，覆盖杠杆/反向产品；目录不可访问时使用官方分类页作为稳定入口，并由运行时按 `productName` 查询报告。
- 下载器：`code/companies/csop/download_csop_factsheet.py` 新增 `monthly`/`semiannual`/`annual` 三类报告选择。报告类型 API 采用多枚举尝试，旧接口不支持类型枚举时退回无类型列表后按标题关键词筛选；PDF 仍校验 `%PDF-`，月报保留页面 Holdings API 结构化数据。
- 补全脚本：`code/companies/csop/csop_fund_link.py` 默认处理 `南方东英`，兼容显式 `--sheet 南方`；`--backfill` 全量写入月报、半年报、年报三组稳定 URL/运行方式。南方东英页表 70 行均已写入分类页兜底入口和目标英文名命令，后续在可访问官网环境运行时会优先替换为具体产品页。
- 数据契约：`CrawlerReportRow`、`FundReportSource`、导入 upsert、API `ReportSource`、Web 类型新增 `annual_report_url/annual_run_command`；新增迁移 `20261104_0013_annual_source_commands.py`。年报 UI 链接优先年报入口，半年报仍优先半年报入口。
- 注册与验证：`BACKFILL_COMPANY_ALIASES["南方东英"] = "csop"`；`run_all_downloaders.py --sheet 南方东英 --list` 输出 70 行；crawler 总数由 783 增至 **853**，唯一序号 819；新增 CSOP/CICC 定向测试，合计 **11 passed**，compileall 通过。
- 能力边界：部分 ETF 官网没有 Monthly Newsletter，页面仍保存稳定入口和运行命令，实际运行会返回“无匹配月报”；这类基金可改用半年报/年报任务。南方东英本轮未把历史 PDF UUID 写入长期来源键，避免链接随版本变化失效。

### 2026-11-04｜中金正文选择修复与华泰（huatai）接入

- 中金问题复盘：中金产品页可能同时出现“2026 中期报告公告”和真正的 `C_CICC Fund Series Final 6.30.2026.pdf`。旧选择器按发布日期倒序，可能把较新的公告 PDF 当成半年报输入，造成持仓、资产配置和流动性字段全部为空。
- 中金修复：`code/companies/cicc/download_cicc_factsheet.py` 新增公告/通知标记和正文标记；半年报/年报在候选阶段排除 `公告/Announcement/Notice`，只有公告时明确失败；正文候选按正文标记、报告日期、URL 日期排序。新增“公告较新但正文优先”和“只有公告则拒绝”回归测试。中金、来源、HKEX 相关测试 **11 passed**，CICC 模块编译通过。
- 通用接入原则：报告正文和公告必须分开选择；半年报/年报下载器不得把 `Notice To Unitholders`、`Announcement`、`公告` 当作结构化输入。页面只有公告时应报告缺少正文，而不是静默回退。
- 华泰范围：HKEX 主数据中管理人为“华泰金融控股(香港)有限公司”共 **6** 只：BTS953 华泰港元货币市场基金、BTS952 华泰美元货币市场基金、BUF873 华泰亚太目标收益基金、BUR381 华泰环球精选债券基金、BVO540 华泰人民币货币市场基金、BWA843 华泰美国精选基金。
- 官方入口：华泰详情页使用稳定 `https://hk.htsc.com.hk/?f=<序号>&l=en`，已核验 `f=1..6` 分别对应上述 6 只基金；BTS953 使用 `?f=1&l=en`。官网页面同时展示 Factsheet、2026 Semi-Annual Report、Annual Report，以及单独的 Announcements & Notices 区域，不能按页面发布时间简单取第一份。
- 下载器：新增 `code/companies/huatai/download_huatai_factsheet.py`，运行时重新读取详情页 PDF，支持 `monthly`/`semiannual`/`annual`；半年报/年报排除 notice/announcement，下载前校验 `%PDF-`，月报/半年报/年报均保留稳定详情页作为长期来源键。
- 补全脚本：新增 `code/companies/huatai/huatai_fund_link.py`，自动创建“华泰”页表结构（含月报、半年报、年报三组链接/运行方式），并将 6 只基金映射到 `f=1..6`；注册 `BACKFILL_COMPANY_ALIASES["华泰"] = "huatai"`。
- 验证：在 `/tmp/huatai-adapter.BE5TAB/爬虫list.huatai.xlsx` 副本执行 `--backfill`，自动建立 6 行“华泰”页表并写入三类稳定入口；`run_all_downloaders.py --sheet 华泰 --list` 输出 6 行；华泰/CICC/来源/HKEX 相关测试 **15 passed**，compileall 通过。正式工作簿 I/J 未直接写入，保留 GUI「爬虫补全」验收流程。
- 资料边界：BTS953 的 HKEX IFP 页面确认基金代码、管理人、港元基础货币和货币市场基金属性；华泰官网页面确认报告入口和报告/公告分区。若官网页面结构或附件地址变化，脚本应继续使用稳定 `f` 页面重新发现，而不保存随机 PDF UUID。

### 2026-11-04｜招商证券资产管理（香港）（cmsam）接入与流程复盘

- 本轮复盘：先通过 HKEX 主数据按管理人精确筛选，再用官网稳定产品页核验基金名称、英文名称和报告入口；不把同名的“招银国际”管理人混入招商证券资产管理（香港）。正式工作簿不直接写入，先复制到 `/tmp` 做回填和 `--list` 验收，再由 GUI 爬虫补全流程落盘。
- 接入范围：HKEX 主数据中管理人“招商证券资产管理(香港)有限公司”共 **5** 只：BSX133 招商货币市场基金、BUZ208 招商美元货币市场基金、AYF850 招商中国灵活策略基金、BAZ964 招商红利回报均衡基金、BVW680 招商恒生科技指数ETF。BSX133 作为样例，稳定入口为 `https://www.cmschina.com.hk/en/AM/FundProduct?view=CMMF`。
- 官方入口映射：CMMF（招商货币市场）、NPCF（招商美元货币市场）、CHOF（招商中国灵活策略）、HKMIF（招商红利回报均衡）；ETF 使用官方目录 `https://www.cmschina.com.hk/gb/AM/EtfFundProducts.aspx?view=ETF`，避免把动态详情或随机 PDF UUID 固化为长期来源键。
- 下载器：新增 `code/companies/cmsam/download_cmsam_factsheet.py`，每次运行重新读取稳定页 PDF，支持 `monthly`/`semiannual`/`annual`；月报匹配 Factsheet，半年报/年报匹配正文并排除 `Announcement/Notice/公告/通知`。只找到公告时明确失败，下载前校验 `%PDF-`，防止网页错误页进入 MinerU。
- 补全脚本：新增 `code/companies/cmsam/cmsam_fund_link.py`，自动创建“招商”页表，写入月报、半年报、年报三组稳定 URL 和运行方式；注册 `BACKFILL_COMPANY_ALIASES["招商"] = "cmsam"`，同时兼容“招证资管”“招商证券资产管理（香港）有限公司”等管理人名称。
- 验证日志：在临时副本执行 `--backfill`，5 行全部写入三类来源和命令；`run_all_downloaders.py --sheet 招商 --list` 输出 5 行；招商、华泰、中金、来源、HKEX 定向测试 **19 passed**；`compileall` 通过。正式 `爬虫list.xlsx` 未被本轮脚本直接修改。
- 可复用规则：报告选择必须按“报告类型 + 正文/公告语义”双重筛选；长期保存稳定产品/目录页，运行时发现最新 PDF；ETF/动态目录若无法枚举具体附件，保留官方稳定分类页并在运行时按基金名匹配，不能静默写入过期直链。
- 能力边界：本轮只完成来源入口、运行方式和选择器验证；官网若在当前网络环境返回 403、动态渲染或页面没有对应类型报告，任务应输出“没有匹配的正文/报告”，不把目录页本身当作 PDF。实际下载和结构化需在可访问官网、MinerU/Ollama 服务正常时运行。

### 2026-11-04｜广发国际（gfintl）接入与 BWE554 样例核验

- 管理人核验：HKEX BWE554 页面确认其管理人为“广发国际资产管理有限公司 / GF International Investment Management Limited”，基金为“广发港元货币市场基金 / GF HKD Money Market Fund”，基础货币为港元；不能与境内“广发基金”或已有 `gffunds` 国内脚本混用。
- 范围：HKEX 主数据中该管理人共 **12** 只：BPC697、BSG488、BTE502、BTV055、BTT545、BVG225、BWE554、BXB473、BXV822、BXW016、BYG136、BYG137。BWE554 使用官方稳定页 `https://ifp.hkex.com.hk/fund-repository/fund/BWE554`。
- 来源策略：半年报/年报使用 HKEX IFP 财务报告 API，按 `SEMIANNUAL`/`ANNUAL` 精确选择正文；月报使用广发国际官方资料页重新发现 PDF，避免将某一月份的 PDF 直链固化为长期来源。官网月报资料页为 `https://www.gffunds.com.hk/ch/product/funds/`。
- 下载器：新增 `code/companies/gfintl/download_gfintl_factsheet.py`，支持 `monthly`/`semiannual`/`annual`，所有下载校验 `%PDF-`；没有报告正文时明确失败，不把 HKEX 公告或网页 HTML 送入 MinerU。
- 补全脚本：新增 `code/companies/gfintl/gfintl_fund_link.py`，自动创建“广发国际”页表并回填 12 行三类稳定来源和运行方式；注册 `广发国际`、`广发国际资产管理有限公司`、英文管理人别名到 `gfintl`。
- 验证日志：在 `/tmp` 工作簿副本执行 `--backfill`，12 行全部成功；`run_all_downloaders.py --sheet 广发国际 --list` 输出 12 行；广发国际、招商、华泰、中金等定向测试 **23 passed**，compileall 通过；正式 `爬虫list.xlsx` 未直接写入。
- 复用规则：管理人必须按 HKEX 主数据精确匹配；稳定来源页与动态报告分离；财报按报告类型字段精确匹配；月报只能在运行时重新发现，不能把搜索结果中的单月 PDF 误当作永久入口。
- 能力边界：当前网络环境命令行 DNS 无法访问 `gffunds.com.hk`，因此本轮完成了官方链接核验、IFP 选择器和离线回填；实际广发月报下载需在官网可访问环境运行，若官网没有可解析月报入口，任务会明确报告缺失，不静默成功。

### 2026-11-04｜中国平安资产管理（香港）（pinganhk）接入与 BUM340 样例核验

- 管理人核验：HKEX BUM340 页面确认基金为“平安港元货币基金 / Ping An Hong Kong Dollar Money Market Fund”，管理人为“中国平安资产管理(香港)有限公司 / Ping An of China Asset Management (Hong Kong) Co Ltd”，基础货币为港元；不能与境内“平安基金管理有限公司”混用。
- 范围：HKEX 主数据中该管理人共 **11** 只：AWO198、AXL009、BBS387、BCE463、BQN919、BTU900、BUM340、BWH864、BWH865、BYM560、BYM561。BUM340 稳定产品页为 `https://asset.pingan.com.hk/en/PACSIF-PAHKMMF`。
- 官方入口策略：普通基金使用 `PACSIF-*` 产品页，ETF 使用 `PACT-*` 产品页；产品页明确分为 Documents 和 Notices，月报、半年报、年报从 Documents 区动态发现，公告不能作为财报正文。平安货币基金产品页已公开列出 Annual Report、Interim Report、Monthly Fund Fact Sheet。
- 下载器：新增 `code/companies/pinganhk/download_pinganhk_factsheet.py`，支持 `monthly`/`semiannual`/`annual`，半年报/年报排除 Notice/Announcement/Dividend，下载前校验 `%PDF-`，只有公告时明确失败。
- 补全脚本：新增 `code/companies/pinganhk/pinganhk_fund_link.py`，自动创建“平安资管”页表，回填 11 行三类稳定来源和运行方式；注册平安资管中英文别名到 `pinganhk`。
- 验证日志：在 `/tmp` 工作簿副本执行 `--backfill`，11 行全部回填；`run_all_downloaders.py --sheet 平安资管 --list` 输出 11 行；平安、广发、招商、华泰、中金等定向测试 **27 passed**，compileall 通过；正式 `爬虫list.xlsx` 未直接修改。
- 复用规则：基金页优先保存稳定产品入口而非单月 PDF；报告选择按“Documents 区 + 报告类型 + 正文/公告语义”三重判断；ETF 与普通基金使用不同产品目录，避免把 ETF 月报和伞型基金财报混配。
- 能力边界：平安官网部分 ETF 页面采用动态渲染，脚本保留稳定产品页并在运行时重新发现附件；若页面暂时没有某种报告，任务会明确输出“没有匹配的正文”，不静默使用公告或旧 PDF。

### 2026-11-04｜国泰君安资产管理（亚洲）（gtjai）接入与 BTF710 样例核验

- 管理人核验：通过 HKEX BTF710 官方页确认 BTF710 为“国泰君安港元货币市场基金 / Guotai Junan HKD Money Market Fund”，管理人为“国泰君安资产管理(亚洲)有限公司 / Guotai Junan Assets (Asia) Limited”，基础货币为港元；不能与“国泰全球投资管理有限公司”同名管理人混用。
- 范围：本地 HKEX 主数据按管理人精确筛选出 5 只：AOM552 国泰君安大中华增长基金、BTF710 国泰君安港元货币市场基金、BTF711 国泰君安美元货币市场基金、BVH535 国泰君安投资级债券基金、BXB327 国泰君安环球精选债券基金。
- 官方入口：月报使用每只基金自己的 GTJAI 报告页（BTF710 为 `https://www.gtjai.com/tc/fund_gjhkdmmf_report`，BTF711 为 `https://www.gtjai.com/tc/fund_gjusdmmf_report`）；半年报/年报使用 HKEX 稳定基金页（例如 BTF710：`https://ifp.hkex.com.hk/fund-repository/fund/BTF710`）并在运行时调用 IFP 财务报告接口。
- 报告选择：月报在官网目录中按中英文基金名、月报关键词和日期重新发现最新 PDF；半年报/年报按 IFP `docType=SEMIANNUAL`/`ANNUAL` 精确选择正文，避免公告、通知或其他披露文件进入 MinerU；下载前统一校验 `%PDF-`。
- 实现：新增 `code/companies/gtjai/download_gtjai_factsheet.py` 与 `code/companies/gtjai/gtjai_fund_link.py`，建立“国泰君安”页表并回填月报、半年报、年报稳定入口和运行方式；在 `code/run_all_downloaders.py` 注册中英文管理人别名及 `gtjai` 别名。
- 验证：在 `/tmp/gtjai-adapter.DNhTmb/爬虫list.gtjai.xlsx` 临时副本完成 5 行回填；`run_all_downloaders.py --sheet 国泰君安 --list` 识别 5 行，月报命令均指向对应基金的动态报告页，半年报/年报命令均携带对应 HKEX 基金代码；正式 `爬虫list.xlsx` 未直接写入。编译通过，联合回归测试 **31 passed**。
- 可复用经验：管理人接入必须先用 HKEX 主数据做精确边界，再将“稳定产品页”和“动态报告附件”分离保存；月报不固化单月 PDF，财报必须按正文类型字段精确选择；名称、语言和报告类型都匹配失败时要显式报错，而不是回退到公告或旧文件。
- 能力边界：GTJAI 月报页可能受动态渲染或网络访问限制，脚本会在运行时重新解析并在无匹配正文时失败；当前已完成入口、选择器和调度接入，实际下载与结构化需在 GTJAI 官网、HKEX IFP、MinerU/Ollama 均可访问时执行。

### 2026-11-04｜东方资产管理（香港）（orientam）接入与 BVB640 样例核验

- 管理人核验：HKEX BVB640 官方页确认基金为“东方红人民币货币市场基金 / Orient Sun Rise RMB Money Market Fund”，管理人为“东方资产管理（香港）有限公司 / Orient Asset Management (Hong Kong) Limited”，基础货币为人民币；不能与“东方汇理（Amundi）”混用。东方官网也将东方资管（香港）列为东方红启元基金系列的管理平台。
- 范围：本地 HKEX 主数据按管理人精确筛选出 3 只：BVB640 东方红人民币货币市场基金、BQL528 东方红大中华债券基金、BTJ308 东方红动态策略基金。
- 官方入口：月报保存东方金融控股（香港）官网公募基金稳定产品页 `https://www.dfzq.com.hk/main/mainbusiness/assetmanagement/product-and-service/fundsProduct/raised/index.shtml`；半年报/年报保存对应 HKEX 稳定基金页（BVB640：`https://ifp.hkex.com.hk/fund-repository/fund/BVB640`），运行时调用 IFP 财务报告接口。
- 报告选择：月报按基金中英文名称、月报/Factsheet 关键词和日期动态发现 PDF；半年报/年报按 IFP `docType=SEMIANNUAL`/`ANNUAL` 精确选择正文，避免公告、通知进入 MinerU；下载前校验 `%PDF-`。
- 实现：新增 `code/companies/orientam/download_orientam_factsheet.py` 与 `code/companies/orientam/orientam_fund_link.py`，建立“东方资管”页表并回填 3 行月报、半年报、年报入口和运行方式；在 `code/run_all_downloaders.py` 注册东方资管中英文别名。
- 验证：在 `/tmp/orientam-adapter.olQJmr/爬虫list.orientam.xlsx` 临时副本完成 3 行回填；`run_all_downloaders.py --sheet 东方资管 --list` 识别 3 行；正式 `爬虫list.xlsx` 未直接写入。编译通过，联合回归测试 **34 passed**。
- 可复用经验：东方官网产品目录为动态页面，长期保存稳定目录页，运行时发现具体附件；不能把官网“财富管理月报”（宏观研究材料）误当成基金月报。若产品页没有匹配的基金月报，脚本必须明确失败，等待官网入口补充。
- 能力边界：当前已完成管理人边界、来源入口、正文选择器和调度接入；东方官网月报附件是否持续公开取决于动态页面内容，实际下载与结构化需在官网、HKEX IFP、MinerU/Ollama 均可访问时执行。

### 2026-11-04｜华夏基金（香港）（chinaamc）财报通道补齐与 BSR551 样例核验

- 既有能力：华夏月报已经通过 `chinaamc.com.hk` 的 jeecg-boot 目录接口和文档接口适配，BSR551 对应官网基金 ID 由目录接口动态解析；本轮不替换既有月报逻辑。
- 管理人核验：HKEX BSR551 官方页确认基金为“华夏精选美元货币基金 / ChinaAMC Select USD Money Market Fund”，管理人为“华夏基金(香港)有限公司 / China Asset Management (Hong Kong) Limited”，并在基金文件区分列 Factsheet、Financial Reports 与公告。
- 范围：本地 HKEX 主数据与 `华夏` 页表共 27 行；其中 25 只存在香港华夏 HKEX 基金代码，可建立半年报/年报入口；华夏回报证券投资基金、华夏兴华混合型证券投资基金 2 行属于境内/未纳入香港华夏主数据，不伪造代码，保留缺失状态。
- 实现：新增 `code/companies/chinaamc/download_chinaamc_reports.py`，月报继续走官网 Factsheet 接口，半年报/年报调用 HKEX IFP 财务报告接口，按 `docType=SEMIANNUAL`/`ANNUAL` 精确选择正文，下载前校验 `%PDF-`；扩展 `chinaamc_fund_link.py`，为既有“华夏”页表追加半年报链接、半年报运行方式、年报链接、年报运行方式和 HKEX 代码列；统一调度器补充中英文管理人别名。
- 验证：`/tmp/chinaamc-reports.aMELmw/爬虫list.chinaamc.xlsx` 临时副本中 25 行写入财报入口、2 行明确缺少 HKEX 代码；BSR551 行已生成半年报/年报命令；正式 `爬虫list.xlsx` 未直接修改；compileall 通过，联合回归测试 **37 passed**。
- 网络边界：本轮命令行运行时 `www.chinaamc.com.hk` DNS 不可达，月报目录回填结果为可重试 `SKIP`，不能据此判断官网没有月报；HKEX 财报入口和选择器已完成离线验收。后续在可访问网络环境重新运行 `chinaamc_fund_link.py --backfill` 即可补月报。
- 复用经验：已有月报适配的管理人补充半年报/年报时，应新增独立报告下载器并复用稳定 HKEX 基金页；财报必须使用正文类型字段选择，不能把“Announcement - Unaudited Semi-annual Report”公告当成半年报正文。

### 2026-11-04｜博时基金（国际）（bosera）财报通道补齐与 BSG677 样例核验

- 管理人核验：通过 HKEX BSG677 官方页确认“博时美元货币市场基金 / Bosera USD Money Market Fund”，管理人为“博时基金(国际)有限公司 / Bosera Asset Management (International) Co., Limited”，基础货币为美元；稳定入口为 `https://ifp.hkex.com.hk/fund-repository/fund/BSG677`。
- 既有能力：博时月报仍使用 `bosera.com.hk` 的基金目录、净值类别和 `factsheet.json` 接口动态发现最新月报，不固化单月 PDF；已保留原有 BSG677 月报逻辑。
- 范围：`爬虫list.xlsx` 的“博时”页共 16 行，其中 13 行可由本地 HKEX 主数据精确映射到香港基金代码（含 BSG677、BTE097、BCY423、BDI575、BLS840、BRK536、BRR570、BRS611/612/613、BVN008、BXU444/445）；3 行为境内互认/境内基金，未在香港 IFP 中确认，保持空白，不猜测代码。
- 实现：新增 `code/companies/bosera/download_bosera_reports.py`，通过 HKEX IFP `getFundFileList` 读取财务报告，半年报/年报分别按 `docType=SEMIANNUAL`/`ANNUAL` 精确选择正文，下载前校验 `%PDF-`；扩展 `bosera_fund_link.py`，追加半年报链接、半年报运行方式、年报链接、年报运行方式、HKEX 代码字段；调度器新增博时中英文管理人别名。
- 验证：在临时副本 `/tmp/bosera-adapter.VwwXqU/爬虫list.bosera.xlsx` 回填 **13 行** 财报入口，**3 行**明确缺少 HKEX 代码；BSG677 已生成半年报/年报命令。联合回归测试 **42 passed**，compileall 通过；正式 `爬虫list.xlsx` 未直接修改。
- 网络边界：本轮 `bosera.com.hk` 月报目录接口因当前环境 DNS 不可达，backfill 日志对 9 行输出可重试 `SKIP`，不能据此判断官网无月报；财报入口、类型选择器和离线映射已完成验收，待官网/HKEX IFP 可访问时重跑即可下载并进入 MinerU/结构化流水线。
- 复用经验：管理人接入先按 HKEX 主数据精确划边界，再将稳定基金页与运行时动态报告发现分开保存；财报只接受正文 `docType`，不回退到公告；无法确认的境内基金必须显式留空并记录原因。

### 2026-11-25｜纽约梅隆、美富信、高盛依次接入

- 纽约梅隆：新增 23 行页表和 `code/companies/bny/` 适配器/下载器；官方 BNY Mellon Hong Kong Fund Centre 的 FundOptions 目录可验证 18 条，临时副本 **OK 18｜SKIP 5｜FAIL 0**。五条未命中保留缺口，不猜测链接。抽样 Global Credit Fund 下载归档成功。
- 美富信：新增 23 行页表和 `code/companies/mfs/` 适配器/下载器；MFS Meridian 香港区域 API 的 23 个 ISIN 均找到 English (Hong Kong) Factsheet，临时副本 **OK 23｜SKIP 0｜FAIL 0**。抽样 Asia Ex-Japan Fund 下载归档成功。
- 高盛：新增 23 行页表和 `code/companies/goldman/` 适配器/下载器；官方 `/api/documents` 按基金名、英语、fundDocuments、monthlyFundUpdate 和香港优先过滤，临时副本 **OK 23｜SKIP 0｜FAIL 0**。抽样 Global CORE Equity Portfolio 下载归档成功。
- 三家公司均注册统一调度器别名和月报覆盖审计；正式 `爬虫list.xlsx` 新页 I/J 保持空白，备份分别为 `爬虫list.xlsx.bny-before-20261125.bak`、`爬虫list.xlsx.mfs-before-20261125.bak`、`爬虫list.xlsx.goldman-before-20261125.bak`。审计基线为 57 个适配器、2479 只主数据基金、1039 只有月报入口。
- 相关实现细节：`docs/development/34-bny-mellon-adapter.md`、`35-mfs-meridian-adapter.md`、`36-goldman-adapter.md`。

### 2026-11-25｜信安、安盛、品浩依次接入

- 信安：新增 `code/companies/principal/` 适配器/下载器和“信安”页表 21 行；读取信安香港官方 `api/mutual-funds`，按产品核心名及 HKD→USD 份额优先级选 Fact Sheet。临时副本 **OK 18｜SKIP 3｜FAIL 0**；未命中 `Asian High Yield Fund`、`Principal Pension Bond Fund`、`Global Listed Infrastructure Fund`，官方 API 当前未返回同名 Fact Sheet。
- 安盛：新增 `code/companies/axa/` 适配器/下载器和“安盛”页表 21 行；AXA IM 迁移后的 BNPP AM Fund Explorer/DocFinder 作为官方入口。优先 `DOC_FACTSHEET`，当前 AXA 产品无 factsheet 时透明回退 `DOC_REPS` 半年报并在运行命令保留 `semiannual`。临时副本 **OK 17｜SKIP 4｜FAIL 0**；四只历史/当前目录缺失产品保留缺口。
- 品浩：新增 `code/companies/pimco/` 适配器/下载器和“品浩”页表 19 行；使用 PIMCO 香港 Product Finder API，按官方 `CountryCode=HK/UserRole=FI/LangCode=EN` 请求头读取 CUSIP 文档，优先 HK 份额 Fact Sheet。临时副本 **OK 18｜SKIP 1｜FAIL 0**；`PIMCO Asia Bond Fund` 当前官方目录未返回，未映射到相似基金。
- 三家均已注册统一调度器别名和月报覆盖审计；正式 `爬虫list.xlsx` 新页 I/J 保持空白，备份分别为 `爬虫list.xlsx.principal-before-20261125.bak`、`爬虫list.xlsx.axa-before-20261125.bak`、`爬虫list.xlsx.pimco-before-20261125.bak`。抽样官方 PDF 下载成功；通用日期解析补充支持 `DD/MM/YYYY`，Principal 的 `As of 30/9/2026` 已正确归档。
- 适配器总数由 57 增至 **60**；详细说明见 `docs/development/37-principal-adapter.md`、`38-axa-adapter.md`、`39-pimco-adapter.md`。

### 2026-11-25｜木星资管（Jupiter Asset Management）接入

- 范围：`爬虫list.xlsx` 的“木星资管”页共 15 行；官方入口为 Jupiter 香港 Fund Centre/DIFRAAS API。适配器通过 `getShareClassList` 获取香港 share class，再用 `getDocumentCategories` 读取报告，不把随机单月 PDF 当作长期目录入口。
- 实现：新增 `code/companies/jupiter/jupiter_fund_link.py`、`download_jupiter_factsheet.py`、`tools/add_jupiter_sheet.py`；报告类型选择优先 Monthly Investment Report，其次 Factsheet，最后 Quarterly Investment Report；下载器统一校验 `%PDF-` 并归档。
- 验证：临时副本 `/tmp/jupiter.xlsx` **12 OK｜3 SKIP｜0 FAIL**；3 条缺口为木星全球价值基金、木星环球债券基金、木星新兴市场债券基金，当前香港官方目录没有同名可验证报告，未猜测映射；原生网络权限下 12/12 PDF 下载成功。
- 正式工作簿新增“木星资管”页表，I/J 保持空白，备份 `爬虫list.xlsx.jupiter-before-20261125.bak`；已注册统一调度器别名与月报覆盖审计，适配器总数 **61**。详细说明见 `docs/development/40-jupiter-adapter.md`。
