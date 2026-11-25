# Bloomberg 货币基金任务范围（2026-08-31规模快照）

## 范围

`01-money-market-bloomberg-scope.csv` 按项目主数据 `主数据` 页筛选：

规模排名来自项目工作簿 `HKEX基金综合资料库_完整_20261008.xlsx`；Bloomberg 代码和份额类别来自产品会附件 `公募1030（产品会）宏v12.xlsm` 的“货币基金-美元/港元/人民币”页。

- `资产类别一级分类 = 货币`
- `管理人中外资 = 中资`
- 以 `2026-08-31规模（亿港币）` 降序，分别取美元前 10、港元前 15、人民币前 10。

人民币范围已按要求调整为前 10，且前 10 只均有附件中可唯一核对的 Bloomberg ticker。当前可安全请求 Bloomberg 的范围为：美元 10 + 港元 15 + 人民币 10 = 35 只。主数据中排在人民币前 10 之后、且附件没有可唯一核对 ticker 的基金仍记录在 `bloomberg_money_market_unresolved.csv`，没有猜测证券代码。

相对于原 `01-money-market-top15.csv`，本次新增并完成 ticker 唯一核对的基金代码为：

`BMV635、BNO281、BVJ945、BTF710、BTS953、BSX133、BUM340、BWE554、BVW984、BVQ026、BTP458、BSH785、BUK159、BWH820、BDO182、BWG919、BVC695、BVO540、BVB640、BXW016`。

## 代码口径与核对规则

- `code` 是 HKEX 基金代码（例如 `BSG677`），不是 Bloomberg 证券代码。
- `bbg_ticker` 来自附件工作簿对应币种页的“代码”列（例如 `BOUMMIU HK EQUITY`）。基金名称在主数据与附件中做唯一匹配后才写入映射。
- 运行前脚本会拒绝重复基金代码、重复 Bloomberg ticker 和空基金名称；不要依据基金代码前缀或名称自行拼接 ticker。
- `BUE512` 的附件份额栏写作 `I HKD ACC`，与美元基金基础货币存在疑点，已在 notes 中保留，需在 Bloomberg 终端复核份额类别后再发表。

## 运行

```bash
python bloomberg_money_market_stats.py --strict-identifiers \
  --start-date 2025-06-30 --end-date 2026-06-30
```

`--strict-identifiers` 会在发起请求前发现未映射 ticker；日点位写入 `bloomberg_money_market_output/daily_points.csv`，年化收益率和年化波动率写入 `summary.csv`。Bloomberg 返回 ticker 会经过大小写、尾部 `Equity` 规范化匹配，但输出仍保留终端返回值。
