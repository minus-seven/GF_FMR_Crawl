# 周验收运行手册与下周计划（2026-11-09 周）

> 本文是当周验收记录。当前版本的安装、启停和依赖路径以 [运行环境、依赖与服务器启停手册](59-runtime-startup-and-dependency-runbook-20261126.md) 为准。

## 验收环境

- PostgreSQL：`127.0.0.1:55432/fund_research`。
- API：`127.0.0.1:8000`；Web：`127.0.0.1:5173`。
- Worker：正式运行必须从原生 Terminal、macOS launchd 或 Windows Service 启动；入口和预检见 `docs/development/14-native-worker-browser-runtime.md`。受限开发会话只允许运行静态检查，不应领取真实任务。
- 数据库迁移：`20261104_0014 (head)`。
- 当前发布快照：`9d679bd7-9c2a-43b6-9f4f-aeb1d2514950`。

## 常用命令

```bash
./platform/scripts/postgres.sh start
./platform/scripts/api.sh start
./platform/scripts/web.sh start
./platform/scripts/worker.sh browser-check
./platform/scripts/worker.sh start

PYTHONPATH=code:platform/api $FACTSHEET_VENV_ROOT/bin/pytest -q platform/api/tests --tb=no
PATH=/Applications/ChatGPT.app/Contents/Resources/cua_node/bin:$PATH \
  platform/web/node_modules/.bin/tsc --noEmit -p platform/web/tsconfig.json
```

## 本周验收结论

- 20/20 QA 请求 HTTP 200，预期状态匹配 20/20，claim-evidence 绑定 20/20；P95 6.5ms。
- API 十次采样的 P95：基金列表 12.4ms、基金详情 5.4ms、知识全文 3.1ms、SQL 问答 2.8ms。
- 后端测试：217 passed、27 skipped、0 failed；跳过项均因未配置独立 `PLATFORM_TEST_DATABASE_URL`。
- 当前索引质量检查通过；源缺口重新统计为 906 条中的 136 条。
- 本地 `qwen3:4b` 为 Q4_K_M、4.0B、约 3.18GB；一次真实结构化提示耗时约 22.4s，需作为开发测试基线而非生产 SLA。

## 已知边界

1. 当前周验收完成了“问答→证据→受控 Agent 计划→确认创建/取消”的真实写链路；为避免产生未经业务确认的新发布快照，本周未执行生产数据的“新快照→再问答”写入演示。
2. 向量运行时未安装时，知识问答可降级到全文检索；响应必须保留 limitation，不得伪装为向量结果。
3. 20 道样本不是正式准确率评估；结构化验证告警仍需逐只复核。
4. Vitest 仍受本机 Rollup 原生模块签名冲突影响，TypeScript 检查和 Vite 页面探活通过。

## 下一阶段入口

下一阶段已从按工作日排期改为按依赖和验收门推进，详见[下一阶段知识问答与 Agent 分步计划](10-next-knowledge-agent-step-plan.md)。执行顺序为：

1. 冻结意图契约与 150 道意图评测集；
2. 规则预解析与候选意图；
3. Qwen 结构化意图分类、置信度和澄清；
4. 意图到工具的受控路由；
5. canary 任务闭环与发布后复问；
6. 灰度评测、模型审计和发布。

详细计划见[下一阶段知识问答 Agent 计划：意图识别与受控编排优先](10-next-knowledge-agent-step-plan.md)。来源补齐和结构化覆盖改为并行保障线，不再阻塞意图识别开发，但缺证据问题仍必须拒答。
