# 来源管理、基金集合与任务目标设计

## 1. 口径

- 来源的 `is_visible` 只控制默认 UI 和透视查询；原始 `Document/DocumentVersion`、知识库索引和审计记录保留。
- 删除是“当前基金关联 + 派生结构化数据”的删除，不删除可能被其他基金复用的原始文档。
- 二级筛选在同一维度内为 OR，不同维度之间为 AND。`Z=非杠反/不适用`、`AC=港元，美元/港元，美元，人民币`、`AM=是/指数符合/不适用`、`AR=是/不适用/否`按 UI 合并口径映射。
- 默认排序不指定业务指标；指定“最新规模”后按当前 HKEX 快照最新规模排序，同值按基金代码稳定排序。

## 2. 数据流

```text
基金库筛选 → matching_fund_codes → 勾选集合 → fund_collections(session_key)
                                      ↓
                         数据任务 collection 范围冻结 JobFundScope

数据来源管理 → hide/show（可见性+审计）
              delete（基金派生数据 + DocumentSubject）→ 原始文档保留

任务目标：基金月报 / 半年报 / 综合资料库
          ↓              ↓                  ↓
      既有流程      semiannual 来源优先      暂不开放且按钮禁用
```

## 3. 兼容与边界

- 旧 Job 没有 `task_target` 时按 `fund_monthly` 解释。
- 半年报下载优先使用 `semiannual_report_url` 和 `semiannual_run_command`；若来源未配置可执行命令，任务会在下载阶段给出可追踪的跳过/失败原因，不回退为月报。
- 多基金半年报的切片、章节识别和结构化仍复用现有解析器；识别失败时保留整本报告送入结构化的既有兜底策略。
- 临时集合在服务重启后的清理由过期回收逻辑完成；前端 session_key 只用于归属校验，不作为用户身份认证替代品。

## 4. 主要接口

- `GET /api/funds?secondary=维度:标签&sort_by=latest_scale&sort_direction=asc|desc`
- `POST /api/funds/collections`
- `GET/POST /api/funds/{fund_code}/sources/manage`
- `POST /api/jobs` 的 `task_target` 与 `scope.mode=collection`

## 5. 验证清单

- 迁移 `20261104_0012` 已应用。
- 后端编译、前端 TypeScript 检查通过。
- 已验证二级筛选、规模排序、来源隐藏/显示、集合创建、集合任务冻结与会话校验。
