# 契约文件

本目录承载前端、API、Worker、Agent 和测试共享的机器可读契约。

| 文件 | 内容 | 对应设计 |
|---|---|---|
| [openapi-v0.3.yaml](openapi-v0.3.yaml) | FastAPI 实际生成的可读 OpenAPI v0.3 | [P0 API 与事件契约](../06-p0-api-and-event-contracts.md) |
| [openapi-generated-v0.3.json](openapi-generated-v0.3.json) | FastAPI 实际生成的 D2/D3 OpenAPI 文档 | [开发计划](../../development/01-development-plan.md) |
| [job-event.schema.json](job-event.schema.json) | SSE 与持久化任务事件信封 | [P0 API 与事件契约](../06-p0-api-and-event-contracts.md) |
| [source-adapter.schema.json](source-adapter.schema.json) | 来源适配器 manifest | [P1 来源适配器协议](../08-p1-source-adapter-contract.md) |
| [answer.schema.json](answer.schema.json) | claim、citation 和无证据回答 | [P1 RAG 证据协议](../09-p1-rag-evidence-contract.md) |

`POST /api/chat` 的运行时请求/响应契约由
`platform/api/app/schemas/chat.py` 提供，并在导出的 OpenAPI 中登记；它将本目录
`answer.schema.json` 的 claim/citation 原则落到 `ChatClaim`、`ChatEvidence` 和
`ChatResponseData` 字段上。新增字段时应同步更新该 Pydantic Schema、OpenAPI 和周验收测试。

`/api/agent/plan` 与 `/api/agent/runs/*` 的计划/确认契约由
`platform/api/app/schemas/agent.py` 提供，写任务必须先取得 `confirmed=true`，再复用
现有 `JobCreateRequest` 和任务状态机。

## 使用约定

- `openapi-v0.3.yaml` 和 `openapi-generated-v0.3.json` 由 FastAPI 同一份应用生成；
- 二者是 D2/D3 已实现路由和字段的机器契约，不再手工维护平行字段；
- JSON Schema 中尚未实现的任务、来源适配器和 RAG 契约仍为设计草案；
- 更新 API 后使用 `platform/scripts/export_openapi.py` 同时刷新 YAML 和 JSON；
- JSON Schema 进入代码仓库后，应使用统一 validator 在 CI 中测试；
- 新增字段默认可选，删除字段或改变枚举值需要提升主版本；
- Agent 工具输入输出也必须引用这些 Schema 或相同字段的领域子 Schema。

## 最小校验

```text
Markdown 链接和表格检查
OpenAPI YAML 解析
JSON Schema 语法解析
任务事件样例校验
来源适配器样例校验
回答和引用样例校验
Mermaid 图渲染
```
