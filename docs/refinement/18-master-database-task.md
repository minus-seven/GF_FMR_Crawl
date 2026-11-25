# 综合资料库任务入口

## 目标

把只读的 `HKEX_Fund_Crawl_v4` GUI 能力接入 Web「数据任务 → 任务目标 → 综合资料库」，复用 Web 任务队列、全局写锁、SSE 事件、任务记录和 Run detail，不修改旧项目源码。

## Web 入口

综合资料库任务保留旧 GUI 的核心参数：

- 参考文件（服务器端 xlsx 路径，默认使用项目内 `HKEX基金综合资料库_完整_20261008.xlsx`）；
- 数据 Sheet、改名 Sheet；
- 使用缓存；
- 改名检测方式：不检测 / API / 详情页；
- 数据更新方式：API / 详情页 + API；
- 完成后处理：仅缓存，或下载完成后自动落库。

页面会显示预计时间、任务记录、阶段进度和实时 Run detail。任务只执行一个“下载”阶段，但内部结果是完整综合资料库 xlsx，而不是基金月报 PDF。

## 运行与缓存

`platform/api/app/services/master_database.py` 是薄适配层。它将冻结的 Web 参数映射为白名单命令，调用外部项目的 `crawler.py` 子进程，并把输出复制到：

```text
data/jobs/<job_id>/staging/<job_id>/master_database/HKEX基金综合资料库_latest.xlsx
```

旧目录只读；缓存、日志、manifest 均由 Web 项目持有。任务详情可下载缓存文件；取消操作会终止受控子进程。

## 落库

任务完成后可以在 Run detail 点击「落库到数据库」，或者在创建时选择自动落库。落库建立新的不可变 `DatasetBatch`：

1. 复制当前批次的指标、持仓、资产配置、规模历史和质量问题；
2. 用新 xlsx 更新基金、管理人、分类、地区、标签、规模、改名及 HKEX 原始行；
3. 重新激活新批次，基金详情和二级类别筛选立即读取新主数据；
4. 相同 xlsx SHA 重复落库时幂等返回，不重复写入。

## 接口

- `GET /api/jobs/master-database/options`
- `POST /api/jobs`，`task_target=master_database`，`document_policy.master_database=...`
- `GET /api/jobs/{job_id}/master-artifact`
- `POST /api/jobs/{job_id}/master-import`

综合资料库任务只允许下载阶段；旧月报/半年报任务路径不变。
