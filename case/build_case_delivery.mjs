import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = process.cwd();
const out = `${root}/case/01-money-market-case-delivery.xlsx`;
const dbData = JSON.parse(await fs.readFile("/tmp/case_data.json", "utf8"));
const top15Text = await fs.readFile(`${root}/case/01-money-market-top15.csv`, "utf8");

function parseCsvLine(line) {
  return line.replace(/^\uFEFF/, "").split(",");
}
const metaByCode = {};
for (const line of top15Text.trim().split(/\r?\n/).slice(1)) {
  const p = parseCsvLine(line);
  if (p.length >= 7) {
    metaByCode[p[1]] = {
      rank: Number(p[0]), code: p[1], name: p[2], manager: p[3], currency: p[4],
      scale: Number(p[5]), recognizedDate: p[6],
    };
  }
}

const blue = "#D9EAF7";       // 2026-06-30 半年报/结构化来源
const green = "#E2F0D9";      // 最新月报 fallback
const purple = "#E4DFEC";     // Bloomberg 年化收益率
const amber = "#FFF2CC";      // 缺失、替代或需要复核
const navy = "#1F4E78";
const lightBlue = "#EEF5FB";
const border = "#C9D5E2";

function fmt(v, digits = 2) {
  if (v === null || v === undefined || v === "") return "—";
  return typeof v === "number" ? v.toFixed(digits) : String(v);
}
function sourceFill(source) {
  if (!source) return amber;
  if (String(source).includes("Bloomberg")) return purple;
  if (String(source).includes("半年报")) return blue;
  if (String(source).includes("月报")) return green;
  return amber;
}
function sourceLabel(obj) {
  if (!obj) return "—";
  const d = obj.report_date || obj.report_period || "";
  return `${obj.source_kind || "未知来源"}${d ? `（${d}）` : ""}`;
}
function isSuspiciousMetric(code, key, obj) {
  if (!obj || obj.numeric === null || obj.numeric === undefined) return false;
  if (["daily_liquid_assets_percent", "weekly_liquid_assets_percent"].includes(key)) {
    if (obj.numeric > 100) return true;
    if (obj.numeric === 32.7 || obj.numeric === 28.5 || obj.numeric === 190.31) return true;
    if (code === "BTF715") return true; // 2026-09-03 补充来源与月报页面混淆，暂不采用
  }
  return false;
}
function classifyHolding(h) {
  const n = String(h.name || "");
  const t = String(h.type || "");
  const bank = /bank|bk\.?\b|securities|finance|financial|capital|mizuho|intesa|saudi national/i.test(n);
  const asset = t || (/\bCD\b|certificate of deposit/i.test(n) ? "存款证 / Certificate of Deposit" :
    /deposit/i.test(n) ? "定期存款 / Deposit" :
    /money market|ETF/i.test(n) ? "货币市场基金/ETF" : "债务证券 / Fixed Income Security");
  return { bank: bank ? "银行/金融机构" : "非银行发行人或待复核", asset };
}
function cleanRecord(rec) {
  const meta = metaByCode[rec.code] || {};
  const notes = [];
  const fields = { ...rec.fields };
  for (const key of ["daily_liquid_assets_percent", "weekly_liquid_assets_percent"]) {
    if (isSuspiciousMetric(rec.code, key, fields[key])) {
      notes.push(`${key === "daily_liquid_assets_percent" ? "日" : "周"}流动资产候选值疑似模板复用/来源错配，已不纳入比较`);
      fields[key] = null;
    }
  }
  let holdings = (rec.holdings || []).filter((h) => Number(h.weight || 0) > 0);
  const before = holdings.length;
  holdings = holdings.filter((h) => !/^HSBC Hong Kong Treasury Bill/i.test(String(h.name || "")) && !/^[货幣货币市场工具存款][A-Z]$/.test(String(h.name || "")));
  if (holdings.length < before) notes.push("持仓候选含跨基金模板占位值，已剔除；需回到原报告复核");
  if (rec.code === "BTE502" && holdings.every((h) => /Derivative Exposure/i.test(String(h.name)))) {
    holdings = [];
    notes.push("半年报结构化仅生成衍生品敞口行，未识别到投资组合持仓");
  }
  if (!rec.semiannual_available) notes.push("数据库未找到 2026-06-30 半年报，结构字段按最新月报/补充来源回退");
  if (!fields.inception_date) {
    if (meta.recognizedDate) notes.push("报告未披露成立日期，以下成立时间使用 SFC 认可日期替代，不等同法律成立日");
  }
  if (!holdings.length) notes.push("未取得可核验的前十大持仓（报告未披露、子基金切片缺失或结构化未识别）");
  const inception = fields.inception_date || {
    value: meta.recognizedDate || null,
    report_period: meta.recognizedDate || null,
    report_date: meta.recognizedDate || null,
    source_kind: "数据库 SFC 认可日期（替代）",
  };
  return { ...rec, meta, fields, holdings, notes };
}
const records = dbData.map(cleanRecord).sort((a, b) => a.meta.rank - b.meta.rank);

function applyTableStyle(range, header = false) {
  range.format = {
    fill: header ? navy : "#FFFFFF",
    font: { name: "Aptos", size: header ? 10 : 9, bold: header, color: header ? "#FFFFFF" : "#1F2937" },
    horizontalAlignment: header ? "center" : "left",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: border },
  };
}

const wb = Workbook.create();
const s1 = wb.worksheets.add("口径与颜色");
const s2 = wb.worksheets.add("中资货基Top15");
const s3 = wb.worksheets.add("SFC监管要求");
for (const s of [s1, s2, s3]) { s.showGridLines = false; }
s1.tabColor = navy; s2.tabColor = "#548235"; s3.tabColor = "#7F6000";

// Sheet 1
s1.getRange("A1:H1").merge();
s1.getRange("A1").values = [["Case 01｜中资货币基金 Top15 数据口径与时间标识"]];
s1.getRange("A1:H1").format = { fill: navy, font: { name: "Aptos Display", size: 16, bold: true, color: "#FFFFFF" }, verticalAlignment: "center" };
s1.getRange("A2:H2").merge();
s1.getRange("A2").values = [["生成日期：2026-11-03｜数据主口径：项目数据库；规模排名沿用 HKEX 2026-08-31 快照；字段优先选择 2026-06-30，缺失回退最新月报"]];
s1.getRange("A2:H2").format = { fill: lightBlue, font: { size: 10, color: "#374151" }, wrapText: true };
s1.getRange("A4:H4").merge();
s1.getRange("A4").values = [["一、字段与取数规则"]];
s1.getRange("A4:H4").format = { fill: "#DCE6F1", font: { bold: true, size: 12, color: navy } };
const defs = [
  ["字段", "口径", "优先来源", "缺失/替代规则", "注意事项"],
  ["规模", "亿港币；按 HKEX 2026-08-31 快照排序", "HKEX 主数据", "无规模则不进入 Top15", "规模日期与报告日期不同，不作同日资产规模比较"],
  ["币种", "基金主数据 base_currency", "数据库基金主表", "缺失则备注", "不把不同币种规模直接相加作收益比较"],
  ["过去一年年化收益率", "2025-06-30 至 2026-06-30，Bloomberg 总回报指数净分红序列年化", "Bloomberg 历史数据", "缺失则备注", "不是月报中的七日年化收益率；两者不可混用"],
  ["日/周流动资产", "报告披露的 daily / weekly liquid assets 占比", "2026-06-30 半年报；否则最新月报", "无可靠页内证据则留空", "跨基金重复的模板候选值不作为正式比较"],
  ["成立时间", "优先报告的成立日期", "2026-06-30 半年报；否则最新月报", "改用数据库 SFC 认可日期并在备注标明", "认可日期不是法律意义上的成立日期"],
  ["半年报前十大持仓", "按 2026-06-30 半年报中持仓比例排序，最多 10 笔", "2026-06-30 半年报", "无半年报则用最新月报；仍无则备注", "持仓名称保留报告原文，未强行补 ticker"],
  ["银行/资产类型分类", "仅对已识别的前十大持仓做名称/原文类型归类", "半年报 holding_type + 名称规则", "无法识别标记待复核", "分类不是发行人信用评级"],
  ["股债比", "报告明确披露的 equity_weight : debt_weight", "2026-06-30 半年报；否则最新月报", "未披露不按货币基金属性推定", "货币市场工具不自动等同普通债券"],
];
s1.getRange(`A6:E${5 + defs.length}`).values = defs;
applyTableStyle(s1.getRange(`A6:E${5 + defs.length}`));
applyTableStyle(s1.getRange("A6:E6"), true);
s1.getRange("A6:E6").format.rowHeight = 28;
s1.getRange("A18:H18").merge();
s1.getRange("A18").values = [["二、颜色图例（Sheet2 中按单元格标识）"]];
s1.getRange("A18:H18").format = { fill: "#DCE6F1", font: { bold: true, size: 12, color: navy } };
const legend = [
  ["颜色", "含义", "时间点/来源", "使用方式"],
  ["2026-06-30 半年报", "#", "报告期精确至 2026-06-30", "优先作为持仓、流动性、股债比来源"],
  ["最新月报 fallback", "#", "数据库中最新可用月报", "仅在半年报缺失时使用"],
  ["Bloomberg", "#", "2025-06-30 至 2026-06-30", "过去一年年化收益率"],
  ["缺失/替代/复核", "#", "无可靠来源或使用认可日期替代", "查看备注，不作无条件结论"],
];
s1.getRange("A20:D24").values = legend;
applyTableStyle(s1.getRange("A20:D24")); applyTableStyle(s1.getRange("A20:D20"), true);
s1.getRange("A21").format.fill = blue; s1.getRange("A22").format.fill = green; s1.getRange("A23").format.fill = purple; s1.getRange("A24").format.fill = amber;
s1.getRange("A26:H26").merge();
s1.getRange("A26").values = [["三、来源与限制"]];
s1.getRange("A26:H26").format = { fill: "#DCE6F1", font: { bold: true, size: 12, color: navy } };
const notes = [
  ["来源", "数据库结构化指标、持仓和分配表；规模与排名沿用 case/01-money-market-top15.csv；年化收益率来自 case/summary.csv/Bloomberg 导入。"],
  ["质量控制", "对日/周流动资产和持仓执行了跨基金模板复用清理；被清理的值不再填入主表，并在备注说明。"],
  ["监管时间", "截至 2026-11-03，SFC 已公布修订 UT Code 于 2026-11-01 生效，并通常给予现有基金 12 个月过渡期；正式合规判断应以当前 UT Code、过渡安排及基金文件为准。"],
  ["SFC Handbook", "https://en-rules.sfc.hk/-/media/EN/assets/components/codes/files-current/web/codes/sfc-handbook-for-unit-trusts-and-mutual-funds/20241001-sfc-handbook-for-unit-trusts-and-mutual-funds-Eng-Final.pdf"],
  ["SFC MMF circular", "https://apps.sfc.hk/edistributionWeb/api/circular/list-content/circular/doc?lang=EN&refNo=24EC64"],
];
s1.getRange("A28:B32").values = notes;
applyTableStyle(s1.getRange("A28:B32"));

// Sheet 2
s2.getRange("A1:U1").merge();
s2.getRange("A1").values = [["香港中资货币基金规模 Top15｜字段对比（数据库交付版）"]];
s2.getRange("A1:U1").format = { fill: navy, font: { name: "Aptos Display", size: 16, bold: true, color: "#FFFFFF" }, verticalAlignment: "center" };
s2.getRange("A2:U2").merge();
s2.getRange("A2").values = [["规模排序：HKEX 2026-08-31；结构字段：2026-06-30 优先、缺失回退最新月报；收益率：Bloomberg 2025-06-30—2026-06-30"]];
s2.getRange("A2:U2").format = { fill: lightBlue, font: { size: 10, color: "#374151" }, wrapText: true };
const headers = ["排名", "代码", "基金名称", "管理人", "规模（亿港币）", "规模日期", "币种", "过去一年年化收益率（%）", "收益率来源/日期", "日流动资产（%）", "日流动资产来源/日期", "周流动资产（%）", "周流动资产来源/日期", "成立时间", "成立时间来源", "半年报前十大持仓（名称｜比例）", "前十大银行/金融机构分类", "前十大资产类型分类", "股债比（股票:债务）", "股债比来源/日期", "备注" ];
s2.getRange("A4:U4").values = [headers];
applyTableStyle(s2.getRange("A4:U4"), true);
const body = [];
const cellSources = [];
for (const rec of records) {
  const f = rec.fields;
  const holdingLines = rec.holdings.map((h) => `${h.name}｜${fmt(h.weight, 2)}%${h.registration ? `｜注册地:${h.registration}` : ""}${h.type ? `｜类型:${h.type}` : ""}`);
  const bankSet = new Set(); const assetSet = new Set();
  for (const h of rec.holdings) { const c = classifyHolding(h); bankSet.add(`${c.bank}: ${h.name}`); assetSet.add(c.asset); }
  const daily = f.daily_liquid_assets_percent;
  const weekly = f.weekly_liquid_assets_percent;
  const ratio = f.equity_debt_ratio;
  const note = rec.notes.join("；") || "—";
  body.push([
    rec.meta.rank, rec.code, rec.name, rec.meta.manager, rec.meta.scale, "2026-08-31", rec.currency,
    f.annualized_return_percent ? fmt(f.annualized_return_percent.numeric, 4) : "—", sourceLabel(f.annualized_return_percent),
    daily ? fmt(daily.numeric, 2) : "—", sourceLabel(daily),
    weekly ? fmt(weekly.numeric, 2) : "—", sourceLabel(weekly),
    f.inception_date ? f.inception_date.value : (rec.meta.recognizedDate || "—"),
    f.inception_date ? sourceLabel(f.inception_date) : "数据库 SFC 认可日期（替代）",
    holdingLines.length ? holdingLines.join("\n") : "—",
    bankSet.size ? [...bankSet].join("\n") : "—",
    assetSet.size ? [...assetSet].join("；") : "—",
    ratio ? ratio.value : "—", ratio ? sourceLabel(ratio) : "—", note,
  ]);
  cellSources.push({
    annual: sourceFill(f.annualized_return_percent), daily: daily ? sourceFill(daily) : amber,
    weekly: weekly ? sourceFill(weekly) : amber, inception: f.inception_date ? sourceFill(f.inception_date) : amber,
    holdings: rec.holdings_source ? sourceFill({ source_kind: rec.holdings_source }) : amber,
    ratio: ratio ? sourceFill(ratio) : amber,
  });
}
s2.getRange(`A5:U${4 + body.length}`).values = body;
applyTableStyle(s2.getRange(`A5:U${4 + body.length}`));
for (let i = 0; i < body.length; i++) {
  const row = 5 + i; const c = cellSources[i];
  s2.getRange(`H${row}`).format.fill = c.annual;
  s2.getRange(`J${row}`).format.fill = c.daily; s2.getRange(`L${row}`).format.fill = c.weekly;
  s2.getRange(`N${row}`).format.fill = c.inception; s2.getRange(`P${row}:R${row}`).format.fill = c.holdings;
  s2.getRange(`S${row}`).format.fill = c.ratio;
  s2.getRange(`U${row}`).format.fill = body[i][20] === "—" ? "#FFFFFF" : amber;
}
s2.getRange(`A5:A${4 + body.length}`).format.horizontalAlignment = "center";
s2.getRange(`E5:E${4 + body.length}`).format.numberFormat = "0.00";
s2.getRange(`H5:H${4 + body.length}`).format.numberFormat = "0.0000";
s2.getRange(`J5:J${4 + body.length}`).format.numberFormat = "0.00";
s2.getRange(`L5:L${4 + body.length}`).format.numberFormat = "0.00";
s2.freezePanes.freezeRows(4);

// Sheet 3
s3.getRange("A1:F1").merge();
s3.getRange("A1").values = [["香港证监会（SFC）货币市场基金风险管理要求｜研究口径"]];
s3.getRange("A1:F1").format = { fill: "#7F6000", font: { name: "Aptos Display", size: 16, bold: true, color: "#FFFFFF" }, verticalAlignment: "center" };
s3.getRange("A2:F2").merge();
s3.getRange("A2").values = [["数值阈值主要依据 2024-10-01—2026-10-30 版本 SFC Handbook；修订 UT Code 已于 2026-11-01 生效，现有基金通常有 12 个月过渡期。此表为研究/复核参考，不是法律意见或合规审计。"]];
s3.getRange("A2:F2").format = { fill: "#FFF2CC", font: { size: 10, color: "#7F6000" }, wrapText: true };
const reqs = [
  ["主题", "要求/管理要点", "定量阈值", "适用/状态", "在本 Case 中的用法", "官方来源"],
  ["投资范围", "主要投资于短期存款、高质量货币市场工具，以及符合条件的货币市场基金。", "—", "UT Code MMF 框架", "持仓分类不能把所有货币市场工具自动归为普通债券。", "SFC Handbook §8.2"],
  ["加权平均到期日 WAM", "组合 WAM 不得超过上限。", "≤ 60 天", "硬性数值要求（以适用版本为准）", "可把数据库 WAM 与 60 天阈值做复核，但不能用年化收益率替代 WAM。", "SFC Handbook §8.2(f)"],
  ["加权平均存续期 WAL", "组合 WAL 用于信用风险及流动性风险衡量。", "≤ 120 天", "硬性数值要求（以适用版本为准）", "如报告披露 WAL，单独记录；不能与 WAM 混为一项。", "SFC Handbook §8.2(f)"],
  ["单项剩余期限", "不得购买剩余期限超过规定上限的工具。", "一般 ≤397 天；政府/公共证券 ≤2 年", "硬性数值要求（例外需核对）", "半年报持仓到期日可用于异常期限筛查。", "SFC Handbook §8.2(f)"],
  ["发行人/存款人集中度", "单一实体发行或存款工具合计价值受限；存在大型金融机构及政府/公共证券例外条件。", "一般 ≤10%；符合条件的大型金融机构可至 25%；政府/公共证券同一发行可至 30%", "需结合例外条件", "前十大持仓分类可用于集中度初筛，但不能替代完整合规计算。", "SFC Handbook §8.2(g)"],
  ["高质量与信用评估", "管理人应同时考虑信用质量和流动性，不应过度依赖外部信用评级；通常不应投资无评级或低投资级工具。", "—", "SFC 监管期望/良好实践", "表中资产类型与银行分类仅是数据层分类，不是信用结论。", "SFC 24EC64 §5"],
  ["流动性风险政策", "建立并执行有效的流动性风险管理政策，考虑投资策略、投资者基础、资产流动性、负债/赎回义务和赎回政策。", "—", "持续管理要求", "日/周流动资产是报告披露指标，不等于完整流动性风险评估。", "SFC 24EC64 §2"],
  ["压力测试与流动性工具", "持续进行合理压力测试，准备行动计划和流动性风险管理工具（LMTs），并公平对待赎回与留存投资者。", "—", "良好实践/监管期望", "缺失值要保留在备注，不得用规模或收益率推断流动性安全。", "SFC 16EC29；24EC64"],
  ["年化收益率披露", "披露短于一年期限的年化收益时，应同时呈现七日年化收益率等一致基础信息；年化收益率不代表未来回报。", "7 日年化收益率应按 SFC 通函披露", "披露要求；非资产约束", "本表“过去一年年化收益率”来自 Bloomberg 总回报序列，不能与七日年化直接比较。", "SFC 23EC49"],
  ["现行版本与过渡", "修订 UT Code 于 2026-11-01 生效；SFC 公告称现有 SFC-authorised funds 通常有 12 个月过渡期，后续应核对补充指引/FAQ。", "—", "截至 2026-11-03", "本表适合研究和数据复核；正式交付前需按基金适用版本重核。", "SFC 26PR161；SFC Codes"],
];
s3.getRange(`A4:F${3 + reqs.length}`).values = reqs;
applyTableStyle(s3.getRange(`A4:F${3 + reqs.length}`)); applyTableStyle(s3.getRange("A4:F4"), true);
s3.getRange("A4:F4").format.rowHeight = 30;
s3.getRange("A16:F16").merge();
s3.getRange("A16").values = [["官方链接：SFC Handbook：https://en-rules.sfc.hk/ ｜ SFC MMF circular 24EC64：https://apps.sfc.hk/edistributionWeb/api/circular/list-content/circular/doc?lang=EN&refNo=24EC64 ｜ SFC current Codes：https://www.sfc.hk/en/Rules-and-standards/Codes-and-guidelines/Codes"]];
s3.getRange("A16:F16").format = { fill: "#FFF2CC", font: { size: 9, color: "#7F6000" }, wrapText: true };

// widths and row heights
const widths = {
  "口径与颜色": [18, 38, 28, 34, 48, 12, 12, 12],
  "中资货基Top15": [7, 10, 22, 12, 14, 12, 9, 16, 24, 14, 24, 14, 24, 14, 24, 55, 42, 34, 16, 24, 56],
  "SFC监管要求": [18, 48, 28, 24, 44, 46],
};
for (const [name, ws] of [["口径与颜色", s1], ["中资货基Top15", s2], ["SFC监管要求", s3]]) {
  const arr = widths[name];
  for (let i = 0; i < arr.length; i++) ws.getRangeByIndexes(0, i, 100, 1).format.columnWidth = arr[i];
  ws.getUsedRange().format.verticalAlignment = "center";
  ws.getUsedRange().format.wrapText = true;
}
s1.getRange("A1:H2").format.rowHeight = 28;
s2.getRange("A1:U2").format.rowHeight = 28;
s2.getRange(`A5:U${4 + body.length}`).format.rowHeight = 84;
s3.getRange("A1:F2").format.rowHeight = 34;
s3.getRange(`A5:F${3 + reqs.length}`).format.rowHeight = 62;
s1.getRange("A1:H32").format.font.name = "Aptos";
s2.getRange(`A1:U${4 + body.length}`).format.font.name = "Aptos";
s3.getRange(`A1:F${3 + reqs.length}`).format.font.name = "Aptos";

await wb.recalculate();
const inspect = await wb.inspect({ kind: "sheet,region", sheetId: s2.name, range: "A1:U8", maxChars: 4000 });
console.log(inspect.ndjson || inspect);
const preview = await wb.render({ sheetName: "中资货基Top15", range: "A1:U10", scale: 1, format: "png" });
await fs.writeFile(`${root}/case/01-money-market-case-delivery-preview.png`, new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(out);
console.log(`saved ${out}`);
