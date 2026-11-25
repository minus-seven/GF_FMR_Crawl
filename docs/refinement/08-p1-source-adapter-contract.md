# P1 — 基金来源适配器协议

> 状态：Draft v0.1  
> 范围：27 个公司适配组及其余 HKEX 管理人实体扩展  
> 目标：让新增来源不修改 API、数据库和 Agent 协议  
> 机器可读约束：[source-adapter.schema.json](contracts/source-adapter.schema.json)

基金身份、分类、标签和规模以 HKEX 综合资料库为权威来源。crawler 工作表不是
基金主数据源，只保存报告页面链接、运行方式和来源适配信息；其记录必须先匹配
HKEX `fund_code`，不能使用公司页“序号”作为全局主键。

## 1. 适配器职责

一个来源适配器只负责：

1. 判断自己是否负责某基金；
2. 发现基金基本资料页和候选报告；
3. 下载文件到受控 staging 目录；
4. 提取报告类型、报告期、语言和来源；
5. 执行站点健康检查和失败分类。

适配器不负责：

- 切换正式快照；
- 直接修改 `publication_state`；
- 绕过来源白名单；
- 执行任意脚本；
- 向 Agent 暴露自由 URL；
- 把未验证文件写入已发布目录。

## 2. Manifest

```json
{
  "schema_version": "1.0",
  "adapter_id": "hsbc-hk",
  "display_name": "汇丰环球资产管理与香港基金中心",
  "version": "2026.10.1",
  "priority": 100,
  "manager_codes": ["HSBC"],
  "manager_names": ["汇丰", "HSBC"],
  "allowed_domains": [
    "assetmanagement.hsbc.com.hk",
    "assetmanagement.hsbc.co.uk",
    "ifp.hkex.com.hk"
  ],
  "capabilities": {
    "monthly": true,
    "annual": true,
    "interim": true,
    "isnr_search": true,
    "annual_section_split": true
  },
  "document_sources": [
    {
      "source_id": "hsbc-hk-factsheet",
      "type": "factsheet_api",
      "priority": 10
    },
    {
      "source_id": "hsbc-uk-factsheet",
      "type": "international_site",
      "priority": 30,
      "only_when": "name_language=en"
    },
    {
      "source_id": "hkex-financial-report",
      "type": "fallback",
      "priority": 90
    }
  ],
  "policies": {
    "prefer_monthly": true,
    "allow_annual_fallback": true,
    "allow_interim_fallback": true,
    "allow_section_split": true
  },
  "health_check": {
    "endpoint": "https://assetmanagement.hsbc.com.hk/zh/intermediary/fund-centre",
    "interval_minutes": 60
  }
}
```

## 3. 匹配与发现顺序

```text
1. 精确基金代码或来源映射
2. 伞名 + 中文基金名精确匹配
3. HKEX `改名` 页中的旧名称匹配
4. 标准化英文名精确匹配
5. 官网搜索接口
6. 同伞已成功基金的反向推断
7. 国际站或英文站
8. 年报/中报
9. 港交所 IFP
10. 标记 SKIP，不猜随机 UUID、不编造 ISIN
```

每一步必须返回一个明确原因：

```text
MATCHED
NO_FUND_PAGE
PAGE_FOUND_NO_MONTHLY
MONTHLY_FOUND
FALLBACK_FOUND
NEW_OR_PENDING_FUND
FUND_CLOSED
SOURCE_BLOCKED
NAME_AMBIGUOUS
NO_VERIFIABLE_FILE
```

## 4. 补充来源记录

crawler 工作簿的每一行都必须先写入 `fund_report_sources`，即使暂时无法匹配：

```text
fund_report_sources
  id
  fund_id
  source_system
  source_key
  source_sheet
  source_row
  manager_name_source
  fund_name_source
  umbrella_name_source
  monthly_report_url
  fallback_report_url
  run_command
  match_status
  match_method
  match_confidence
  is_active
  metadata_json
```

- `source_key` 由来源系统、工作表和 Excel 行号组成；
- 同一来源行重复导入时更新同一记录；
- `fund_id` 为空表示尚未确认，不能删除；
- 改名页匹配必须记录 `match_method = manager_historical_name`；
- `run_command` 只作为受控适配器参数，不能直接交给任意 Shell 执行。

### 4.1 平台侧爬虫补全

来源缺失或无效时，Worker 按 `source_sheet` 和管理人匹配
`companies/<manager>/*_fund_link.py`，并复用公司脚本的 `probe`、
`OK`、`SKIP` 契约。平台调用时增加 `--rows`，只允许脚本处理
`job_fund_scope` 已冻结的 Excel 行。

平台不会读取正式 Excel 已有的 I/J 作为补全结果，也不会修改正式 Excel。
每次任务会复制到 `data/jobs/<job_id>/backfill/爬虫list.xlsx`，只清空
数据库中确实缺来源的目标行 I/J，然后让公司脚本自主重新发现。

V1 当前按工作表和管理人目录发现补全脚本；正式 adapter manifest 可进一步声明：

```json
{
  "source_backfill": {
    "script": "companies/hsbc/hsbc_fund_link.py",
    "sheet_names": ["汇丰"],
    "supports_probe": true,
    "supports_rows_filter": true
  }
}
```

处理顺序与原 GUI 一致：

1. 探测是否需要补全；
2. 先下载已有完整来源；
3. 运行公司补全脚本；
4. 解析 `OK` / `SKIP`，把 I/J 写入 `fund_report_sources`；
5. 对补全成功的行继续下载。

补全结果保存在 `metadata_json.backfill`：

```json
{
  "status": "succeeded",
  "script": "companies/hsbc/hsbc_fund_link.py",
  "reason": null,
  "attempted_at": "2026-10-23T02:49:23Z"
}
```

- 状态可为 `succeeded`、`skipped`、`failed`、`unsupported`；
- `is_active=true` 表示来源可执行；
- 平台不修改正式 Excel I/J，数据库是平台运行时权威来源；
- 补全 OK 后立即提交 PostgreSQL，后续下载失败不回滚 I/J；
- D1 重复导入时，Excel 空值不得覆盖数据库已有的有效 I/J。

## 5. Python 接口建议

```python
class SourceAdapter(Protocol):
    adapter_id: str
    version: str

    def matches(self, fund: FundRow) -> bool:
        """判断适配器是否负责该基金。"""

    def discover(self, fund: FundRow, policy: DocumentPolicy) -> list[DocumentCandidate]:
        """发现候选文档，按优先级返回。"""

    def download(self, candidate: DocumentCandidate, target_dir: Path) -> DownloadedDocument:
        """下载文件到受控目录并返回哈希和来源元数据。"""

    def health_check(self) -> HealthResult:
        """检查站点、接口和关键解析规则是否仍可用。"""
```

新增可选方法：

```python
def resolve_subject(pdf_path: Path, fund: FundRow) -> DocumentSubject | None:
    """在整伞报告中定位子基金章节和页码。"""
```

接口必须保持纯 Python 类型、不可变输入和结构化输出，禁止适配器自行打印关键状态作为唯一结果。

## 6. 下载与文件规则

- 文件先写 `staging/<batch_id>/downloads/`；
- 下载完成后计算 SHA-256；
- MIME、文件头、页数和 PDF 可打开性都要校验；
- 来源 URL、重定向链、报告日期、语言和哈希写入 manifest；
- 重名文件使用内容哈希区分，不用随机名字覆盖旧文件；
- 官网超时、403、CAPTCHA、页面结构变化分别映射到不同错误码；
- 不可重试错误停止盲试并标记适配器版本。

## 7. 任务级策略

```json
{
  "prefer": "monthly",
  "allow_fallback": ["annual", "interim", "hkex"],
  "max_candidates_per_fund": 5,
  "max_redirects": 5,
  "timeout_seconds": 30,
  "allow_section_split": true,
  "require_human_review_on_name_ambiguity": true
}
```

### 7.1 发布候选文件

候选文件必须包含：

- `fund_id`
- `document_type`
- `report_date`
- `source_url`
- `fund_report_source_id`
- `source_domain`
- `sha256`
- `mime_type`
- `language`
- `fallback_reason`
- `subject_name`
- `start_page` / `end_page`
- `adapter_id` / `adapter_version`

## 8. 来源健康与回归

每个适配器至少维护：

| 测试 | 内容 |
|---|---|
| Fixture | 1 个成功基金页、1 个月报、1 个缺失页、1 个错误响应 |
| 健康检查 | 关键目录页和 API 状态 |
| 名称归一化 | 简繁、空格、标点、英文前缀 |
| 文件校验 | 实际 PDF 页数和首页标题 |
| 备用通路 | 月报缺失时财报或港交所路径 |
| 反爬边界 | 重定向、403、超时和限流 |

健康状态：

```text
healthy
degraded
blocked
schema_changed
unverified
disabled
```

适配器 schema 变更、网站结构变化或连续失败率超阈值时，不自动发布新批次，进入 `review_required`。

## 9. 安全和维护边界

- `allowed_domains` 是强制网络白名单；
- 不执行页面提供的脚本或命令；
- 不凭随机 UUID 猜测文件；
- 不从不可信 HTML 获取任意下载路径；
- 适配器可以输出候选 URL，但下载器必须再次校验域名和协议；
- 新增适配器需要单元测试、真实 fixture、人工审批和版本登记；
- 适配器日志不得包含用户 Key 或无必要的报告全文。
