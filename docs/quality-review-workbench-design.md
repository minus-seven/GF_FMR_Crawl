# 数据质量待复核工作台设计与实现说明

## 1. 目标

为“数据质量”页面增加一个复合复核工作台，解决结构化结果进入数据库后“能发现问题、但不能就地处理”的断点。

页面采用“看板列表 → 单基金复核页”的两级结构，不再使用左右栏：

- 一级 `/quality`：数据质量看板，以基金为行而不是以问题为行；同一基金的多个质量问题合并到一行，直接展示基金代码、名称、管理人、类别、币种/地区、报告期、状态和问题摘要；
- 二级 `/quality/{fund_code}`：单基金复核页，展示基金基础信息、该基金的全部待处理问题，以及完整记录编辑器和确认操作。

两个核心动作位于单基金复核页：

1. **确认**：不修改结构化数值，只把问题标记为 `resolved`，记录复核人、时间和备注；
2. **编辑**：打开包含基金身份、报告期、指标、持仓、资产/银行分配、来源等信息的 JSON 文本框；保存后更新结构化表，并默认同时确认问题。

基金代码、名称、管理人、资产类别等身份字段在编辑器中展示但不作为可变主数据写回；可编辑范围是 `metrics`、`holdings` 和 `allocations`，避免误改 HKEX 主数据。

## 2. API 设计

新增接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/quality/issues/{issue_id}/review` | 返回问题、基金详情、完整可编辑记录和审计摘要 |
| `POST` | `/api/quality/issues/{issue_id}/confirm` | 直接确认问题，支持可选备注 |
| `PUT` | `/api/quality/issues/{issue_id}/review` | 提交编辑后的 JSON；`resolve=true` 时保存并确认 |

编辑提交示例：

```json
{
  "record": {
    "report_period": "2026-08-01",
    "metrics": [
      {
        "metric_name": "weighted_average_maturity_days",
        "numeric_value": 34.57,
        "text_value": null,
        "unit": "days"
      }
    ],
    "holdings": [],
    "allocations": []
  },
  "comment": "已回到半年报第 12 页复核",
  "resolve": true
}
```

写入策略：

- 复核问题写入 `quality_issues.status`；
- 编辑前后的记录及最多 20 条操作历史写入 `quality_issues.metadata_json.review`；
- 指标更新 `fund_metrics`，编辑确认后标记为 `review_status=approved`；
- 持仓和分配按当前基金、批次、报告期、文档版本替换，原始编辑 JSON 保留在审计 metadata 中；
- 所有写操作限制在当前发布快照，问题 ID 必须属于当前批次；
- 基金身份字段不写回 `funds` 主表。

## 3. 前端交互

页面交互：

1. `/quality` 默认读取 `open` 问题并按基金代码聚合；
2. 点击看板中的基金行进入 `/quality/{fund_code}`；
3. 单基金页顶部显示代码、管理人、资产类别、币种、注册地和报告期；
4. 页面中部列出该基金全部待处理问题，点击问题卡片切换当前操作对象；
5. 下方以只读 JSON 展示完整记录；点击“编辑”后进入可编辑文本框；
6. 点击“保存编辑并确认”先校验 JSON，再提交 API；点击“确认此问题”只关闭当前问题；
7. 基金详情页的“待复核”状态徽章和证据区文字均跳转到同一个单基金复核页。

编辑器采用 JSON 而不是拆分成大量表单控件，原因是结构化字段随资产类别扩展，JSON 可以同时容纳新增字段、来源信息和未知字段；后续可在此基础上增加 schema 表单模式。

## 4. 质量与安全边界

- 单次编辑 JSON 限制为 1 MB；非对象、非法日期、非法数字会返回 422；
- 质量问题必须属于当前发布快照，避免跨快照误修改；
- 复核写入 `reviewed_at`、`reviewed_by`、`comment` 和历史记录；
- 当前项目没有用户认证体系，`reviewed_by` 暂记为 `web-user`。接入登录后应替换为真实用户 ID；
- 编辑不会删除 PDF、Markdown 或原始结构化 JSON；原始值由审计记录保留；
- Qwen 候选结果默认仍是 `review`/`pending`，只有人工确认或编辑确认才提升为人工审核状态。

## 5. 本次验证

- `GET /api/quality/issues/{id}/review` 已用当前快照真实问题联调，能返回基金全量字段；
- 前端 `tsc -b && vite build` 通过；
- 质量页面按钮、编辑器和响应式布局均已落地；
- 既有货币流水线/任务测试保持通过。

## 6. 后续建议

1. 接入真实登录身份，替换 `web-user`；
2. 增加“拒绝/退回模型”动作，将指标标记为 `rejected` 并触发重跑队列；
3. 为 JSON 编辑器增加字段级 schema 校验、页码引用和差异对比；
4. 将 `quality_issues.metadata_json.review.history` 迁移为独立审计表，支持审计报表和并发锁；
5. 对重复模板值增加自动规则，禁止未经过人工确认的异常 WAM/WAL 进入知识库 Agent。
