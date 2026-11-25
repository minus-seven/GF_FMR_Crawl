# 150 道正式意图评测集

> 版本：intent-golden-v1  
> 生成日期：2026-11-15（Asia/Hong_Kong）  
> 评测实现：`platform/scripts/evaluate_intent.py`

## 1. 交付物

- 题集：[intent-golden-v1.jsonl](artifacts/20261109-week/intent-golden-v1.jsonl)
- 机器报告：[intent-golden-v1-report.json](artifacts/20261109-week/intent-golden-v1-report.json)
- 契约测试：`platform/api/tests/test_intent_evaluation.py`

每行是一道独立题，包含 `case_id`、题目分类、自然语言问题和 `gold`。`gold` 固定记录：

- `intent`：正式意图枚举；
- `fund_codes`、`report_type`、`report_date`、`metric`、`top_n`：关键槽位；
- `operation`：`read` 或 `plan`；
- `needs_clarification`：是否必须先澄清；
- `route`：受控路由模式（`read`、`plan` 或 `clarification`）。

## 2. 题集构成

| 类别 | 数量 | 覆盖内容 |
|---|---:|---|
| `query_metric` | 25 | 到期收益率、加权平均到期日、日/周流动资产、年化收益率 |
| `query_holdings` | 15 | 半年报前十、月报前五、主要持仓 |
| `compare_funds` | 20 | 收益率、流动性、规模排名、投资策略差异 |
| `explain_report` | 20 | 月报/半年报投资目标和投资策略 |
| `trace_quality_issue` | 15 | 持仓缺失、结构化错误、待复核原因 |
| `locate_source` | 10 | 原文页码、来源和页码 |
| `find_missing_data` | 10 | 缺失半年报/月报来源 |
| `plan_monthly_job` | 10 | 创建月报任务 |
| `plan_semiannual_job` | 10 | 创建/补充半年报任务 |
| `plan_supplement_source` | 10 | URL 补充来源计划 |
| `unknown` | 5 | 无范围、无目标的模糊请求 |
| **合计** | **150** | **5 只真实基金代码 + 5 类模糊输入** |

## 3. 评测口径与结果

评测使用规则优先的确定性分类器（`allow_local_model=False`），不依赖 Ollama、网络或数据库，保证结果可重复。每道题同时检查意图、槽位、澄清安全门和路由安全。

| 指标 | 结果 | 发布门 |
|---|---:|---:|
| 意图准确率 | 150/150（100%） | ≥95% |
| 关键槽位准确率 | 100% | ≥95% |
| 澄清判断准确率 | 150/150（100%） | 100% |
| 路由安全准确率 | 150/150（100%） | 100% |
| 未确认写操作 | 0 | 0 |
| 失败题 | 0 | 0 |

## 4. 重跑命令

```bash
PYTHONPATH=code:platform/api $FACTSHEET_VENV_ROOT/bin/python platform/scripts/evaluate_intent.py
PYTHONPATH=code:platform/api $FACTSHEET_VENV_ROOT/bin/pytest -q platform/api/tests/test_intent_evaluation.py
```

脚本会重新生成 JSONL 和 JSON 报告；只要存在失败题，进程退出码为 `1`。正式题集不是线上流量采样，下一阶段仍应增加真实匿名问题、粤/繁体表达、跨轮指代和同义词变体，并另行建立 canary 集合。

## 5. 当前边界

本评测验证的是“意图识别 → 槽位 → 安全路由”契约，不等同于数据答案正确率。回答仍必须经过 SQL/证据服务、claim-evidence 绑定和发布快照约束；Qwen 只作为歧义输入的可选结构化分类器，不能直接调用工具或绕过确认门。
