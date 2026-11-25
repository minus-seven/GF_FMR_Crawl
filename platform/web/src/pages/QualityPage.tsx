import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowUpRight, Search, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { QualityIssue, QualityPage as QualityPageData } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { PageHeader } from "../components/PageHeader";
import { formatDate, formatDateTime } from "../lib/format";
import { ContextualAskPanel } from "../components/ContextualAskPanel";

interface ReviewFundRow {
  key: string;
  fundCode: string | null;
  fundName: string;
  managerName: string | null;
  assetCategory: string | null;
  assetSubcategory: string | null;
  baseCurrency: string | null;
  domicile: string | null;
  fundStatus: string | null;
  reportPeriod: string | null;
  issues: QualityIssue[];
}

function groupIssues(items: QualityIssue[]): ReviewFundRow[] {
  const groups = new Map<string, ReviewFundRow>();
  for (const issue of items) {
    const key = issue.fund_code ?? `batch-${issue.issue_id}`;
    const existing = groups.get(key);
    if (existing) {
      existing.issues.push(issue);
      continue;
    }
    groups.set(key, {
      key,
      fundCode: issue.fund_code,
      fundName: issue.fund_name ?? "批次级问题",
      managerName: issue.manager_name ?? null,
      assetCategory: issue.asset_category ?? null,
      assetSubcategory: issue.asset_subcategory ?? null,
      baseCurrency: issue.base_currency ?? null,
      domicile: issue.domicile ?? null,
      fundStatus: issue.fund_status ?? null,
      reportPeriod: issue.report_period ?? null,
      issues: [issue],
    });
  }
  return [...groups.values()];
}

const severityLabel: Record<string, string> = {
  warning: "待确认",
  error: "需修正",
  blocker: "阻断",
};

function IssueBadge({ severity }: { severity: string }) {
  return <span className={`mini-status severity-${severity}`}>{severityLabel[severity] ?? severity}</span>;
}

export function QualityPage() {
  const [data, setData] = useState<QualityPageData | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [severity, setSeverity] = useState("");
  const [fundCode, setFundCode] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.quality({ severity, status: "open", fundCode, pageSize: 200 })
      .then((response) => { if (active) { setData(response.data); setError(null); } })
      .catch((reason: unknown) => { if (active) setError(reason instanceof ApiError ? reason : null); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [fundCode, severity, tick]);

  const rows = useMemo(() => groupIssues(data?.items ?? []), [data]);
  const priorityCount = useMemo(() => rows.filter((row) => row.issues.some((issue) => issue.severity !== "warning")).length, [rows]);
  const warningCount = rows.length - priorityCount;

  return (
    <>
      <PageHeader eyebrow="Data quality dashboard" title="数据质量" description="这里展示当前发布快照中所有待复核基金。点击基金进入单基金复核页面。" actions={<span className="result-count"><b>{rows.length}</b> 只基金待处理</span>} />
      <section className="filter-bar quality-filters">
        <label><span>问题级别</span><select value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">全部</option><option value="warning">待确认</option><option value="error">需修正</option><option value="blocker">阻断</option></select></label>
        <label className="search-field"><Search size={16} /><input value={fundCode} onChange={(event) => setFundCode(event.target.value.trim())} placeholder="按基金代码筛选" /></label>
        <button className="button ghost" onClick={() => { setSeverity(""); setFundCode(""); }}>清空筛选</button>
      </section>
      <ContextualAskPanel
        title="询问当前质量范围"
        eyebrow="Quality evidence assistant"
        description="可直接追问当前待复核基金为什么异常、原文在哪里，或比较当前筛选结果的缺失字段。"
        fundCodes={rows.flatMap((row) => row.fundCode ? [row.fundCode] : [])}
        contextNote={rows.length ? `当前筛选结果 · ${rows.length} 只基金` : "当前筛选结果尚未加载"}
        suggestions={[
          "为什么这些基金进入待复核清单？",
          "列出当前筛选结果缺少半年报来源的基金",
          "当前问题对应的原文和页码在哪里？",
        ]}
      />
      {loading ? <LoadingState label="正在读取待复核基金…" /> : null}
      {error ? <ErrorState message={error.message} requestId={error.requestId} onRetry={() => setTick((value) => value + 1)} /> : null}
      {!loading && !error && !rows.length ? <EmptyState title="当前没有待复核基金" description="所有基金都已通过质量门，或当前筛选没有匹配项。" /> : null}
      {!loading && !error && rows.length ? (
        <section className="quality-dashboard">
          <div className="quality-dashboard-stats">
            <div><ShieldCheck size={19} /><span>待复核基金<strong>{rows.length}</strong></span></div>
            <div><AlertTriangle size={19} /><span>需优先处理<strong>{priorityCount}</strong></span></div>
            <div><span className="stat-dot warning" /><span>普通提醒<strong>{warningCount}</strong></span></div>
          </div>
          <section className="panel quality-fund-panel">
            <div className="panel-heading quality-dashboard-heading"><div><span className="eyebrow">Funds requiring review</span><h2>待复核基金列表</h2><p>基础信息和问题摘要已展开显示；点击整行进入确认与编辑。</p></div><span className="mono">{rows.length} funds</span></div>
            <div className="quality-fund-table" role="table" aria-label="待复核基金列表">
              <div className="quality-fund-table-head" role="row"><span>基金</span><span>基础信息</span><span>待处理问题</span><span>报告期</span><span>操作</span></div>
              {rows.map((row) => (
                <Link className={`quality-fund-row ${row.fundCode ? "clickable" : "disabled"}`} to={row.fundCode ? `/quality/${encodeURIComponent(row.fundCode)}` : "/quality"} key={row.key} role="row">
                  <div className="quality-fund-identity"><span className="fund-code">{row.fundCode ?? "批次级"}</span><strong>{row.fundName}</strong><small>{row.issues.length} 个质量问题</small></div>
                  <div className="quality-fund-basics"><span><b>管理人</b>{row.managerName ?? "未披露"}</span><span><b>类别</b>{row.assetCategory ?? "未分类"}{row.assetSubcategory ? ` · ${row.assetSubcategory}` : ""}</span><span><b>币种/地区</b>{row.baseCurrency ?? "未披露"} · {row.domicile ?? "未披露"}</span><span><b>状态</b>{row.fundStatus ?? "未披露"}</span></div>
                  <div className="quality-fund-issues"><div>{row.issues.map((issue) => <IssueBadge severity={issue.severity} key={issue.issue_id} />)}</div><p>{row.issues[0]?.details}</p><small>最近发现 {formatDateTime(row.issues[0]?.created_at)}</small></div>
                  <div className="quality-fund-period"><b>{formatDate(row.reportPeriod)}</b><small>最新报告期</small></div>
                  <div className="quality-fund-action">{row.fundCode ? <><span>进入复核</span><ArrowUpRight size={15} /></> : <span>批次级问题</span>}</div>
                </Link>
              ))}
            </div>
          </section>
        </section>
      ) : null}
    </>
  );
}
