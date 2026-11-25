# 项目补全路线图：全量月报、综合资料库与 Windows 发布

> 版本：V0.1  
> 基线日期：2026-11-24（Asia/Hong_Kong）  
> 目标：把当前“案例和重点管理人可运行”的平台，整理为“香港公募基金全量可追踪、主数据库可更新、Windows 可部署”的可交付版本。

## 1. 当前基线

| 领域 | 当前事实 | 判断 |
|---|---|---|
| HKEX 主数据 | `HKEX基金综合资料库_完整_20261008.xlsx` 主数据 2,479 条基金记录，187 个管理人实体，含 126 只货币基金 | 可作为基金代码、管理人、分类、规模和标签的权威基线 |
| 月报工作簿 | `爬虫list.xlsx` 全部可识别工作表共 2,956 行、2,084 个去重基金、864 行已有月报链接；其中 `Sheet2` 有 2,191 行、2,003 个去重基金 | 尚未覆盖 HKEX 全量，且工作簿与主数据没有以基金代码作为唯一对账键 |
| 公司适配器 | `code/companies` 当前有 36 个 `*_fund_link.py` 适配器（含安联环球 ORSO/Retail 双目录） | 已有可复用站点经验，但不是所有 HKEX 管理人都已接入 |
| 平台来源模型 | `fund_report_sources`、来源补全、失败分类、来源健康和任务范围已经存在 | 可承接全量补全，不应继续把 Excel 当运行时状态库 |
| 综合资料库 | Web 任务目标、缓存下载、SHA 幂等落库和 Run detail 已实现 | 当前通过 `HKEX_CRAWLER_ROOT` 调用旧 `crawler.py`，需要 Windows 可运行的外部目录/发布包方案 |
| Mac 运行时 | PostgreSQL、API、Worker、Web 均有项目脚本 | 仅 Bash/本机路径，不能直接作为 Windows 发布包 |
| Windows 设计 | 已有 V2 部署、备份、迁移、服务化设计文档 | 仍缺 PowerShell 安装器、服务注册、路径抽象、真实 Windows 冒烟和回滚演练 |

## 2. 总体原则

1. **基金代码优先**：HKEX 基金代码是跨来源唯一主键；基金名称、伞名和管理人名称只用于匹配和审计。
2. **来源与主数据分离**：综合资料库负责基金身份/分类/规模；月报适配器负责候选报告来源；两者通过 `fund_id` 和来源血缘关联。
3. **先对账再补链**：不直接对 2,479 只基金批量请求官网，先生成“主数据—工作簿—数据库来源”差异队列。
4. **任务化、可恢复**：所有批量补全通过 Job/Worker 执行，保留 staging、事件、失败原因和重试边界；不直接覆盖正式数据。
5. **发布包不依赖开发机路径**：Windows 版本必须把配置、数据目录、日志、备份、服务身份和外部爬虫依赖显式化。

## 3. 工作流 A：月报覆盖到全部香港公募基金和管理人

### A0：建立全量对账底表

输入：

- HKEX 主数据 2,479 只基金；
- `爬虫list.xlsx` 所有工作表；
- 数据库 `funds`、`fund_report_sources`；
- 35 个现有适配器及其 manifest/测试。

输出：`source_coverage_audit`，至少包含：

```text
fund_code
fund_id
hkex_manager_entity
manager_alias
crawler_sheet
crawler_row
source_adapter
monthly_url
run_command
database_source_status
match_status
gap_reason
next_action
```

必须先解决：

- 主数据基金代码与工作簿名称无法直接对应的问题；
- 管理人法律实体、品牌名、简称和中英文名称归并；
- 同一基金多份额、多币种和同伞基金的来源优先级；
- 已关闭、待上市、无月报、只有英文月报和官网受限等状态的区分。

### A1：适配器分层

| 层级 | 范围 | 策略 |
|---|---|---|
| Tier 1 | 已有 35 个公司适配器 | 迁移为平台 manifest，统一 probe/backfill/健康检查和报告期识别 |
| Tier 2 | 有稳定官网目录但无适配器的管理人 | 先实现目录发现 + 月报候选链接，不立即结构化 |
| Tier 3 | 无公开目录、反爬或仅有人工下载的管理人 | 接入候选来源/人工补链入口，记录能力缺失，不伪造覆盖 |
| Fallback | 所有管理人 | 官网详情页 → 官网搜索/接口 → 国际站 → HKEX/披露易 → 人工候选来源 |

适配器统一返回：基金身份、报告类型、截止日、语言、候选 URL、下载方式、匹配置信度和失败分类。失败分类必须至少覆盖 `NO_FUND_PAGE`、`PAGE_FOUND_NO_MONTHLY`、`SOURCE_BLOCKED`、`NAME_AMBIGUOUS`、`NO_VERIFIABLE_FILE`、`FUND_CLOSED`。

### A2：批量补全和持续更新

1. 先对缺口最大的管理人做 dry-run，只生成候选结果；
2. 人工抽样确认后写入 `fund_report_sources`，不写正式 Excel；
3. 以报告期和 URL/SHA 为幂等键，发现同 URL 内容变化时重新下载；
4. 以月度任务运行全量健康检查，只有变化来源进入下载/解析队列；
5. 每个管理人记录覆盖率、成功率、最近健康检查和失败趋势；
6. 月报全量覆盖率分三档发布：`verified`（可下载且基金匹配）、`candidate`（链接存在但未验证）、`missing`（没有可验证来源）。

### A3：验收门

- 2,479 只基金全部有对账状态，不能以“未适配”静默丢失；
- 100% 来源行可回溯到基金代码、管理人、Sheet 和 Excel 行号或数据库来源记录；
- `verified` 月报来源覆盖率达到目标值后，才启动全量 MinerU/结构化；
- 每个失败基金都有可读原因、重试策略和人工处理入口；
- 现有重点基金的月报结构化回归不能下降。

## 4. 工作流 B：基金数据库更新功能平台化

当前 `master_database.py` 已经把旧 `HKEX_Fund_Crawl_v4` 包装成 Web 任务，但外部 crawler 仍是运行依赖。补全分为两步：

### B1：先固化“导入发布”契约

- 参考文件、Sheet、改名 Sheet、更新方式和改名检测方式冻结进 Job；
- 下载产物进入 `data/jobs/<job_id>/staging/...`，用 SHA-256 幂等；
- 生成结构化 diff：新增/删除/改名/分类变化/规模变化/标签变化；
- 新批次校验通过后再切换发布指针；
- 旧基金、来源、结构化指标和知识切片在没有新主数据映射时不得被删除；
- 导入报告可下载，包含行数、基金数、变化数、警告数和未匹配数。

### B2：替换“外部目录依赖”

优先级：

1. 把旧 crawler 固化为独立、版本化的 `hkex-master-crawler` 发布包；
2. Web 通过配置指定该包路径/版本，而不是直接依赖开发机目录；
3. 若旧 crawler 无法 Windows 化，则保留“上传/指定已生成 xlsx → 校验 → 导入”能力，下载爬虫作为可选组件；
4. 综合资料库更新失败不影响月报任务和已发布快照。

### B3：验收门

- 同一 xlsx SHA 重复导入不产生新重复批次；
- 改名、规模、标签和基金新增/删除均可生成 diff；
- 发布失败自动保留旧快照；
- Web 过滤、基金详情、ECharts 和知识问答都读取新发布主数据；
- 导入和回滚在真实数据库备份上完成一次演练。

## 5. 工作流 C：Windows 端迁移与打包

### C1：运行时边界

Windows 发布包包含：

- FastAPI API；
- Python Worker；
- React 静态构建产物；
- 配置模板和 Alembic migrations；
- 月报适配器和结构化代码；
- 可选 `hkex-master-crawler`；
- PowerShell 安装、升级、健康检查、备份和回滚脚本。

PostgreSQL 不建议把 Mac 的 `$PLATFORM_RUNTIME_ROOT/postgres` 直接复制到 Windows。Windows 应使用已批准的 PostgreSQL 15+ 服务和 pgvector 扩展，数据库数据目录由安装器或 IT 运维管理。

### C2：必须完成的兼容性工作

1. 将 Bash 启停脚本转换为 PowerShell；
2. 把 `Path`、临时目录、文件锁、子进程终止和编码处理统一封装；
3. 取消用户目录、临时目录和 macOS 专属动态库假设；
4. API、Worker、Web 通过 Windows Service 或任务计划常驻；
5. 凭据使用 Windows Credential Manager/DPAPI 或公司批准的密钥存储；
6. 下载、MinerU、Qwen/Ollama 的本地路径和可选云端配置分离；
7. 通过浏览器访问局域网地址，默认不暴露 PostgreSQL 公网端口；
8. 备份 PostgreSQL、文件产物和发布快照元数据，支持按版本回滚。

### C3：发布包和验收

```text
源码版本
→ Windows Runner 构建
→ 锁定依赖/生成前端静态包
→ 生成版本化 ZIP 或 MSI
→ 安装/迁移/健康检查
→ 导入小型验收快照
→ 跑 1 个下载、1 个解析、1 个结构化、1 个问答样例
→ 备份/恢复/回滚演练
```

验收至少包括：中文路径、中文基金名、PDF 下载、MinerU 产物、Qwen 结构化、SSE 事件、任务取消、服务重启恢复、数据库迁移和浏览器访问。

## 6. 推荐实施顺序

```text
全量对账底表
  → 适配器 manifest 与来源健康
  → 重点管理人月报补全
  → 综合资料库 diff/发布强化
  → Windows 路径与服务抽象
  → Windows 安装包和真实验收
  → 全量月报运行与持续监控
```

不建议先做 Windows 打包再补月报：如果基金身份、来源优先级和数据目录尚未冻结，Windows 包会把错误的运行假设固化下来。

## 7. 第一批可立即执行的任务

1. 生成 2,479 只基金的主数据—工作簿—数据库来源对账报告；当前名称级基线见 [`monthly-coverage-baseline.json`](artifacts/monthly-coverage-baseline.json)，正式对账仍需接入基金代码和数据库来源；
2. 将 35 个适配器登记为 manifest，并为每个适配器补齐健康检查和失败分类；
3. 对缺口最多的前 10 个管理人做 dry-run，不写生产来源；
4. 为综合资料库任务增加 diff 报告和“旧快照保持不变”的集成测试；
5. 建立 Windows 构建机/测试机矩阵，先验证 PostgreSQL 服务、Python、Node、MinerU 和 Ollama；
6. 交付一个只包含 API + Worker + Web + 数据库连接配置的 Windows 内测包，再加入完整爬虫。

## 8. 暂不承诺的事项

- 不能承诺所有管理人都存在可自动化的公开月报入口；
- 不能把“候选 URL 存在”当作“基金月报覆盖”；
- 不能把 Mac 的 PostgreSQL 数据目录直接复制到 Windows；
- 不能在没有独立 Windows 测试机、数据库备份和服务权限时宣称 Windows 迁移完成；
- 不能在来源未验证时批量启动 MinerU/Qwen，避免把来源错误放大成结构化错误。
