# 投资组合结构关键词归并（2026-11-26）

## 目标

投资组合结构饼图可能同时出现 `现金`、`现金及其他`、`現金及其他`、`现金和等价物` 等同义分类。新增跨语言关键词归并层，避免同一资产类别被拆成多个扇区。

## 归并规则

按优先级提取关键词并映射到稳定展示标签：

| 归并标签 | 覆盖示例 |
| --- | --- |
| 现金及其他 | 现金、現金及其他、现金和等价物、Cash and other net assets |
| 债券/固定收益 | 债券、債券、Debt securities、Fixed income |
| 存款 | 存款、存款证、存款凭证、定期存款、银行存款、Time deposits |
| 货币市场工具 | 货币市场基金、其他货币市场工具、货币市场 ETF、Money market funds |
| 股票/权益 | 股票、股份、权益、Equity、Stocks |
| 其他净资产 | 其他净资产、Other net assets |

未命中关键词的标签保留原始名称；关键词优先级保证“现金及其他”不会被宽泛的“其他”再次拆分。

## 实现位置

- `platform/api/app/services/allocation_labels.py`：统一标签归一化和权重合并。
- `platform/api/app/importers/service.py`：新导入的 `asset_type` 记录先归一化再落库。
- `platform/api/app/services/queries.py`：当前及历史数据在生成 ECharts “投资组合结构”时再次归并，兼容已经落库的旧标签。

## 验证

当前发布批次的饼图已由多个现金/债券/货币市场分类合并为单一扇区；接口返回示例包括 `现金及其他`、`债券/固定收益`、`货币市场工具`、`存款` 等。相关 Python 测试 13 项通过。
