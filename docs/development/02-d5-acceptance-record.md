# D5 集成、数据补齐与验收记录

> 日期：2026-10-26  
> 结论：D5 技术验收通过，垂直切片可重复运行  
> 当前快照：`65b785f3-75b1-4cbf-9de2-bbde5bb4bbe1`

> 本文记录历史验收结果。重新启动系统或恢复服务时，请使用 [运行环境、依赖与服务器启停手册](59-runtime-startup-and-dependency-runbook-20261126.md)。

## 1. 验收结论

| D5 验收门 | 结果 | 证据 |
|---|---|---|
| 按文档重新启动系统 | 通过 | PostgreSQL、API、Worker、Web 均在运行 |
| 2,479 只基金与 775 条 crawler 来源可追踪 | 通过 | `verify_d1.py`、`verify_d2.py` |
| 任务完成并生成当前快照 | 通过 | AGD276 三阶段任务 `aad7493c-...` |
| 两个 ECharts 与 API 范围一致 | 通过 | AGD276 `scope_count=1`，两图同一快照 |
| 质量、失败原因和缺失值可见 | 通过 | 质量页 11 条问题；任务事件可回放 |
| 无明文密钥 | 通过 | 29 个日志/前端/manifest 文件扫描，0 命中 |
| 空库迁移和恢复冒烟 | 通过 | Alembic 空库、重复升级、dump restore |
| 测试与缺口已记录 | 通过 | 后端 23/23、前端 5/5 |

## 2. 运行环境

- macOS arm64。
- Python 3.14.6。
- PostgreSQL 17.11，pgvector 0.8.1。
- Node.js 24.19.0，pnpm 11.25.0。
- FastAPI：`http://127.0.0.1:8000`。
- Web：`http://127.0.0.1:5173`。
- PostgreSQL：`127.0.0.1:55432/fund_research`。

## 3. 实际数据覆盖

当前快照不是一个“2,479 只基金均已有完整结构化详情”的全量宣传口径，而是：

| 数据 | 实际结果 |
|---|---|
| HKEX 基金主数据 | 2,479 |
| crawler 补充来源 | 775，全部匹配 HKEX |
| 已补全来源（I/J 完整） | 338 |
| 来源仍不完整 | 437 |
| 有任意结构化详情记录的基金 | 21 |
| 当前批次指标 | 72 |
| 当前批次持仓 | 220 |
| 当前批次分类/地区/期限配置 | 282 |
| 当前批次开放质量问题 | 11 |
| 有规模历史的基金 | 2,427 |
| 当前批次规模记录 | 29,565 |

基金主数据和来源已全量可追踪；结构化详情仍是本周垂直切片范围。

## 4. 端到端任务

AGD276“摩根基金 - 大中华基金”执行：

```text
冻结单基金范围
→ 平台自主爬虫补全
→ PostgreSQL 提交 I/J
→ 下载 PDF
→ MinerU 解析
→ Qwen 结构化
→ manifest 校验与快照发布
```

- 任务：`aad7493c-0d85-4dd3-a5f0-5ced6e4342bf`
- 状态：`completed`
- outcome：`complete`
- 阶段：download / parse / structure 全部完成
- 事件：21 条，可回放 `job.backfill`、`job.stage`、`job.fund`、`job.publish`
- 当前快照与任务批次一致。

真实执行与缓存边界：

- 下载：真实访问摩根官网并下载 PDF。
- 来源补全：真实调用 `jpmorgan_fund_link.py`，不是读取 Excel 回填值。
- 解析：Precise Token 过期后真实降级到 MinerU Agent API；不是缓存冒充。
- 结构化：真实调用 Qwen；不是缓存复制。
- Agent 解析只保留 `full.md`，没有 Precise ZIP 的 `content_list.json` 和图片目录。

## 5. Web 与图表

浏览器验证通过以下路径：

```text
/                      2,479 基金、11 条质量问题、两个 ECharts
/funds?q=AGD276         精确返回 1 只基金
/funds/AGD276           5 个指标、10 条持仓、来源与质量状态
/quality                11 条开放质量问题
/tasks                  AGD276 任务和完整英文状态事件回放
```

AGD276 透视数据：

- `scope_count=1`
- `fund_scale_histogram`
- `fund_scale_trend`
- 两个视图均绑定当前快照。

任务范围选择器已验证：

- 全量基金：2,479；
- 管理人：187 个；
- 资产类别：商品、固收、权益、混合、货币；
- 指定基金代码。

![批量任务范围选择器](../../platform/backups/d5/batch-scope-ui.png)

桌面验收没有横向溢出，页面没有错误状态；截图见
[`platform/backups/d5/d5-current-tasks.png`](../../platform/backups/d5/d5-current-tasks.png)。

本次浏览器验收截图还包括：

- [`d5-current-overview.png`](../../platform/backups/d5/d5-current-overview.png)
- [`d5-current-bim280.png`](../../platform/backups/d5/d5-current-bim280.png)
- [`d5-current-quality.png`](../../platform/backups/d5/d5-current-quality.png)

## 6. 安全

扫描范围：

- `$PLATFORM_RUNTIME_ROOT/*.log`
- `data/jobs/*/worker.log`
- `data/batches/*/manifest.json`
- `platform/web/dist` 的 HTML、JS 和 CSS

结果：29 个文件，0 个明文配置密钥或密钥字段命中。

## 7. 迁移与恢复

Alembic：

- 现有库重复执行 `upgrade head`：通过。
- 全新空库执行完整迁移：通过。
- 空库导入 HKEX、crawler 和结构化种子：通过。
- 空库再次执行 `upgrade head`：通过。
- 当前 revision：`20261023_0004`。
- 种子结果：2,479 基金、775 来源、62 指标、200 持仓、266 配置。

数据库 dump：

- 文件：[`fund_research_20261026_104222.dump`](../../platform/backups/d5/fund_research_20261026_104222.dump)
- 大小：11,541,227 bytes。
- SHA-256：`5f1b1bb670fdd91da175e6bc494390b68a22f33a3512b9858f9c21040763730e`
- 恢复库：`fund_research_d5_restore_20261026_104222`
- 恢复后基金、来源、批次数量和当前发布指针与源库一致。

当前批次文件 manifest：

- 证据：[`file_manifest.json`](../../platform/backups/d5/file_manifest.json)
- 文件数：15。
- manifest SHA-256：`3060c2e5ebdf33c60fec0e4603c087fdf1d08b46b94db378c1317eed51aa3080`。
- 所有文件存在且 SHA-256 一致。

综合报告：

- [`d5_acceptance.json`](../../platform/backups/d5/d5_acceptance.json)
- [`migration_report_20261026_104221.json`](../../platform/backups/d5/migration_report_20261026_104221.json)
- [`restore_report_20261026_104222.json`](../../platform/backups/d5/restore_report_20261026_104222.json)

## 8. 自动化测试

```text
后端测试：28 passed
前端测试：5 passed
前端生产构建：通过
D1 / D2 / D3 / D5 验收脚本：通过
```

### 8.1 MinerU 性能基准

- 当前摩根 45 份 PDF 中只有 1 份命中历史内容哈希缓存，缓存不是主要提速来源；
- 单份 Agent 解析实测约 25–90 秒；
- 两份额外摩根 PDF 串行约 95 秒，`workers=2` 实测 `28.69s`；
- 两份结果均成功，仍使用相同 `agent` / `pipeline-lightweight` 解析路径；
- 平台与 GUI 默认 MinerU PDF 并发均为 4，单份内部任务保持串行。
- 跨任务复用按 PDF SHA-256 匹配历史 manifest，Precise 优先于 Agent。
- 基准报告：[`mineru_parallel_benchmark_20261023.json`](../../platform/backups/d5/mineru_parallel_benchmark_20261023.json)。

## 9. 已知缺口

1. 只有 21 只基金有结构化详情，未完成 2,479 只全量报告迁移。
2. 437 条 crawler 来源仍缺 I/J；需要逐管理人执行平台补全或人工复核。
3. 52 只基金没有当前批次规模历史，图表会明确显示数据缺口。
4. Web 已提供全量、管理人和资产类别范围；多基金自由选择和标签选择器尚未实现。
5. Tableau 只提供只读连接入口，尚无 `.twbx` 工作簿下载。
6. MinerU Precise Token 已过期，当前使用 Agent 轻量解析；PDF 已改为 4 路并发，
   单份解析质量路径不变，但证据颗粒度仍低于 Precise。
7. RAG、知识库、Agent、DOCX/PNG 和完整 Tableau 交付不在本周范围。
8. 90% 覆盖、2 小时全量更新和 100 题问答评测尚未完成。
9. 2026-10-23 的 47 只摩根批量任务在下载 45 只后因 MinerU 退出码 2 失败；
   失败状态和原因在 Web 可见，未污染当前发布快照。

## 10. 下一阶段

1. 完成剩余 437 条来源补全并复核 SKIP。
2. 扩展结构化和历史 PDF 迁移，把详情覆盖从 21 只提升到验收目标。
3. 增加 Web 多基金/公司/标签范围任务。
4. 更新 MinerU Token，恢复 Precise/VLM 证据结构。
5. 建设页级知识库、全文与向量检索、引用和 RAG 评测。
6. 完成 Agent 工具、多轮报告、Tableau/XLSX/DOCX/PNG 交付。
7. 执行 90% 覆盖、2 小时更新和 100 题问答验收。

## 11. 复现命令

```bash
cd ${FACTSHEET_PROJECT_ROOT}

./platform/scripts/postgres.sh status
./platform/scripts/api.sh status
./platform/scripts/worker.sh status
./platform/scripts/web.sh status

PYTHONPATH=platform/api $FACTSHEET_VENV_ROOT/bin/python \
  platform/scripts/verify_d5.py \
  --database-url 'postgresql+psycopg://hu@127.0.0.1:55432/fund_research' \
  --api-url 'http://127.0.0.1:8000' \
  --evidence-dir platform/backups/d5

./platform/scripts/d5_migration_check.sh
./platform/scripts/d5_backup_restore.sh
```
