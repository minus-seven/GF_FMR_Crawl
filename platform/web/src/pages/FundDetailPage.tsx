import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Clock3, ExternalLink, FileText, Fingerprint, Layers3, ShieldCheck, Upload, X, Minus } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Allocation, FundDetail, SourceManagementData } from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/States";
import { QualityBadge } from "../components/StatusBadge";
import { ContextualAskPanel } from "../components/ContextualAskPanel";
import { useScope } from "../context/ScopeContext";
import {
  dimensionLabels,
  formatDate,
  formatNumber,
  metricLabels,
} from "../lib/format";

const primaryMetricNames = [
  "fund_size_hkd_100m",
  "portfolio_duration_years",
  "portfolio_ytm_percent",
  "credit_rating",
  "seven_day_yield_percent",
  "annualized_yield_percent",
  "weighted_average_maturity_days",
  "weighted_average_life_days",
  "daily_liquid_assets_percent",
  "weekly_liquid_assets_percent",
];

const marketPerformanceMetricNames = [
  "annualized_return_percent",
  "annualized_volatility_percent",
];

function metricDisplay(value: FundDetail["metrics"][number]): string {
  if (value.numeric_value !== null) {
    const suffix =
      value.unit === "HKD_100M"
        ? "亿港币"
        : value.unit === "percent"
          ? "%"
          : value.unit === "years"
            ? "年"
            : value.unit === "days"
              ? "天"
              : value.unit === "USD"
                ? "美元"
                : value.unit === "USD_M"
                  ? "百万美元"
                : "";
    return `${formatNumber(value.numeric_value)} ${suffix}`.trim();
  }
  return value.text_value || "未披露";
}

function AllocationGroup({
  title,
  items,
}: {
  title: string;
  items: Allocation[];
}) {
  const total = items.reduce((sum, item) => sum + (item.weight ?? 0), 0);
  return (
    <section className="allocation-group">
      <div className="allocation-heading">
        <strong>{title}</strong>
        <span>{formatNumber(total)}%</span>
      </div>
      {items.slice(0, 8).map((item) => (
        <div className="allocation-row" key={`${item.dimension}-${item.category}`}>
          <span>{item.category}</span>
          <div><i style={{ width: `${Math.min(item.weight ?? 0, 100)}%` }} /></div>
          <b>{formatNumber(item.weight)}%</b>
        </div>
      ))}
    </section>
  );
}

export function FundDetailPage() {
  const { code = "" } = useParams();
  const { setScope } = useScope();
  const [fund, setFund] = useState<FundDetail | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const [selectedReportKey, setSelectedReportKey] = useState<string | null>(null);
  const [supplementOpen, setSupplementOpen] = useState(false);
  const [supplementUrl, setSupplementUrl] = useState("");
  const [supplementFile, setSupplementFile] = useState<File | undefined>();
  const [supplementProvider, setSupplementProvider] = useState<"local" | "deepseek" | "qwen">("local");
  const [supplementFixed, setSupplementFixed] = useState(true);
  const [supplementJobId, setSupplementJobId] = useState<string | null>(null);
  const [supplementStatus, setSupplementStatus] = useState<"idle" | "submitting" | "running" | "success" | "error">("idle");
  const [supplementMessage, setSupplementMessage] = useState("");
  const [supplementMinimized, setSupplementMinimized] = useState(false);
  const [sourceManageOpen, setSourceManageOpen] = useState(false);
  const [sourceManageData, setSourceManageData] = useState<SourceManagementData | null>(null);
  const [sourceManageSelected, setSourceManageSelected] = useState<string[]>([]);
  const [sourceManageBusy, setSourceManageBusy] = useState(false);

  useEffect(() => {
    if (!supplementJobId) return;
    let active = true;
    const poll = async () => {
      try {
        const response = await api.job(supplementJobId);
        if (!active) return;
        const status = response.data.status;
        if (status === "completed") {
          setSupplementStatus("success");
          setSupplementMessage("结构化成功，数据已落库。点击确定刷新基金详情。");
          return;
        }
        if (["failed", "cancelled", "publish_failed"].includes(status)) {
          setSupplementStatus("error");
          setSupplementMessage(response.data.error_message || "补充来源失败，可修改输入后重试。");
          return;
        }
        setSupplementStatus("running");
        setSupplementMessage(`任务正在${response.data.current_stage || "排队"}…`);
      } catch (reason) {
        if (active) {
          setSupplementStatus("error");
          setSupplementMessage(reason instanceof ApiError ? reason.message : "无法读取任务状态");
        }
      }
    };
    void poll();
    const timer = window.setInterval(poll, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [supplementJobId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .fund(code)
      .then((response) => {
        if (!active) return;
        setFund(response.data);
        setSelectedReportKey(response.data.reports[0]?.document_version_id ?? null);
        setError(null);
        setScope({
          fundCodes: [response.data.fund_code],
          label: response.data.name_cn,
          sourceDocumentVersionId: response.data.reports[0]?.document_version_id,
        });
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof ApiError ? reason : null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [code, setScope, tick]);

  const currentReport = useMemo(() => {
    if (!fund) return null;
    return fund.reports.find((report) => report.document_version_id === selectedReportKey)
      ?? fund.reports[0]
      ?? null;
  }, [fund, selectedReportKey]);

  useEffect(() => {
    if (!fund) return;
    setScope({
      fundCodes: [fund.fund_code],
      label: fund.name_cn,
      sourceDocumentVersionId: currentReport?.document_version_id,
    });
  }, [currentReport?.document_version_id, fund, setScope]);

  const marketPerformance = useMemo(() => {
    if (!fund) return [];
    const values = new Map<string, FundDetail["metrics"][number]>();
    for (const report of fund.reports) {
      for (const metric of report.metrics) {
        if (marketPerformanceMetricNames.includes(metric.metric_name) && !values.has(metric.metric_name)) {
          values.set(metric.metric_name, metric);
        }
      }
    }
    return marketPerformanceMetricNames
      .map((name) => values.get(name))
      .filter((metric): metric is FundDetail["metrics"][number] => Boolean(metric));
  }, [fund]);

  const groupedAllocations = useMemo(() => {
    const groups = new Map<string, Allocation[]>();
    for (const item of currentReport?.allocations ?? []) {
      const current = groups.get(item.dimension) ?? [];
      current.push(item);
      groups.set(item.dimension, current);
    }
    return groups;
  }, [currentReport]);

  if (loading) return <LoadingState label="正在整理基金详情…" />;
  if (error) return <ErrorState message={error.message} requestId={error.requestId} onRetry={() => setTick((v) => v + 1)} />;
  if (!fund) return null;

  const longTextMetrics = new Set(["dividend_note", "investment_objective", "investment_strategy", "portfolio_ytm_note"]);
  const primary = (currentReport?.metrics ?? fund.metrics).filter((metric) => primaryMetricNames.includes(metric.metric_name));
  const additionalMetrics = (currentReport?.metrics ?? fund.metrics).filter((metric) => !primaryMetricNames.includes(metric.metric_name) && !longTextMetrics.has(metric.metric_name));
  const reportHoldings = currentReport?.holdings ?? fund.holdings;
  const displayedHoldings = reportHoldings.slice(0, 10);
  const reportUrl = fund.report_sources.find((source) => source.monthly_report_url)?.monthly_report_url;
  const submitSupplement = async () => {
    if (!supplementUrl.trim() && !supplementFile) {
      setSupplementStatus("error");
      setSupplementMessage("请输入 PDF 链接或选择 PDF 文件。");
      return;
    }
    setSupplementStatus("submitting");
    setSupplementMessage("正在创建并排队…");
    try {
      const response = await api.createSupplementarySource(code, {
        url: supplementUrl,
        file: supplementFile,
        provider: supplementProvider,
        fixedCandidate: supplementFixed,
        idempotencyKey: `supplementary-${code}-${Date.now()}`,
      });
      setSupplementJobId(response.data.job.job_id);
      setSupplementMessage(`预计用时约 ${response.data.estimated_minutes} 分钟`);
    } catch (reason) {
      setSupplementStatus("error");
      setSupplementMessage(reason instanceof ApiError ? reason.message : "创建任务失败");
    }
  };
  const closeSupplement = () => {
    if (supplementJobId && ["running", "submitting"].includes(supplementStatus)) {
      void api.cancelJob(supplementJobId).catch(() => undefined);
    }
    const shouldRefresh = supplementStatus === "success";
    setSupplementOpen(false);
    setSupplementJobId(null);
    setSupplementStatus("idle");
    setSupplementMessage("");
    setSupplementMinimized(false);
    if (shouldRefresh) setTick((value) => value + 1);
  };
  const openSourceManage = async () => {
    try {
      const response = await api.sourceManagement(code);
      setSourceManageData(response.data);
      setSourceManageSelected([]);
      setSourceManageOpen(true);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
    }
  };
  const runSourceAction = async (action: "hide" | "show" | "delete") => {
    if (!sourceManageSelected.length || sourceManageBusy) return;
    if (action === "delete" && !window.confirm(`确认删除 ${sourceManageSelected.length} 个来源及其派生数据？原始文档不会删除。`)) return;
    setSourceManageBusy(true);
    try {
      const response = await api.sourceManagementAction(code, sourceManageSelected, action);
      setSourceManageData(response.data);
      setSourceManageSelected([]);
      setTick((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
    } finally {
      setSourceManageBusy(false);
    }
  };

  return (
    <div className="detail-page">
      <Link to="/funds" className="back-link"><ArrowLeft size={15} /> 返回基金库</Link>
      <PageHeader
        eyebrow={`${fund.fund_code} · ${fund.asset_category ?? "未分类"}`}
        title={fund.name_cn}
        description={fund.name_en || fund.manager_name}
        actions={
          <div className="detail-actions">
            {fund.quality_status === "complete" ? <QualityBadge status={fund.quality_status} /> : <Link className="quality-review-entry" to={`/quality/${encodeURIComponent(fund.fund_code)}`}><QualityBadge status={fund.quality_status} /></Link>}
            {reportUrl ? (
              <a className="button primary" href={reportUrl} target="_blank" rel="noreferrer">
                打开报告源 <ExternalLink size={15} />
              </a>
            ) : null}
          </div>
        }
      />

      <section className="entity-strip">
        <div><small>管理人</small><strong>{fund.manager_name}</strong></div>
        <div><small>伞 / 系列</small><strong>{fund.umbrella_name || "未归属伞"}</strong></div>
        <div><small>基础货币</small><strong>{fund.base_currency || "未披露"}</strong></div>
        <div><small>认可日期</small><strong>{formatDate(fund.sfc_authorized_at)}</strong></div>
        <div><small>注册地</small><strong>{fund.domicile || "未披露"}</strong></div>
      </section>

      <ContextualAskPanel
        fundCodes={[fund.fund_code]}
        title={`询问 ${fund.fund_code}`}
        description="已自动带入当前基金范围；回答只使用当前发布快照，并显示对应报告来源和页码证据。"
        contextNote={`${fund.fund_code} · ${fund.name_cn}`}
        suggestions={[
          `${fund.fund_code} 最新报告的规模和到期收益率是多少？`,
          `${fund.fund_code} 最新半年报前十大持仓有哪些？`,
          `${fund.fund_code} 的数据来源和报告期是什么？`,
        ]}
      />

      <div className="detail-grid">
        <div className="detail-main">
          <section className="panel">
            <div className="panel-heading">
              <div><span className="eyebrow">Key metrics</span><h2>关键指标</h2></div>
              <span className="mono">{currentReport?.source_label ?? formatDate(currentReport?.report_period ?? fund.report_period)}</span>
            </div>
            {fund.reports.length > 1 ? (
              <div className="report-switcher" role="tablist" aria-label="报告期">
                {fund.reports.map((report) => (
                  <button
                    className={report.document_version_id === currentReport?.document_version_id ? "active" : ""}
                    key={report.document_version_id}
                    onClick={() => setSelectedReportKey(report.document_version_id)}
                    type="button"
                  >
                    <strong>{report.source_label ?? (report.report_kind === "annual" ? "年报" : report.report_kind === "semiannual" ? "半年报" : "报告")}</strong>
                    <span>{formatDate(report.report_period)}</span>
                  </button>
                ))}
              </div>
            ) : null}
            <div className="metric-grid">
              {primary.map((metric) => (
                <article className="metric-card" key={metric.metric_name}>
                  <small>{metricLabels[metric.metric_name] ?? metric.metric_name}</small>
                  <strong>{metricDisplay(metric)}</strong>
                  <span className={`metric-review ${metric.review_status}`}>
                    {metric.source_label ? `${metric.source_label} · ` : ""}
                    {metric.review_status === "pending" ? "待人工复核" : metric.review_status}
                  </span>
                </article>
              ))}
              {(currentReport?.metrics ?? fund.metrics).filter((metric) => longTextMetrics.has(metric.metric_name)).map((metric) => (
                <article className="metric-card metric-card-wide" key={`long-${metric.metric_name}`}>
                  <small>{metricLabels[metric.metric_name] ?? metric.metric_name}</small>
                  <p>{metric.text_value || metricDisplay(metric)}</p>
                  <span className={`metric-review ${metric.review_status}`}>{metric.source_label ? `${metric.source_label} · ` : ""}{metric.review_status === "pending" ? "待人工复核" : metric.review_status}</span>
                </article>
              ))}
              {!primary.length ? <div className="panel-empty">当前报告未提取到主要指标。</div> : null}
            </div>
            {additionalMetrics.length ? (
              <div className="metric-grid secondary-metric-grid">
                {additionalMetrics.map((metric) => (
                  <article className="metric-card" key={metric.metric_name}>
                    <small>{metricLabels[metric.metric_name] ?? metric.metric_name}</small>
                    <strong>{metricDisplay(metric)}</strong>
                    <span className={`metric-review ${metric.review_status}`}>
                      {metric.source_label ? `${metric.source_label} · ` : ""}
                      {metric.review_status === "pending" ? "待人工复核" : metric.review_status}
                    </span>
                  </article>
                ))}
              </div>
            ) : null}
          </section>

          {marketPerformance.length ? (
            <section className="panel">
              <div className="panel-heading">
                <div><span className="eyebrow">Bloomberg performance</span><h2>市场表现</h2></div>
                <span className="source-chip">{marketPerformance[0]?.source_label ?? "Bloomberg"}</span>
              </div>
              <div className="metric-grid market-performance-grid">
                {marketPerformance.map((metric) => (
                  <article className="metric-card" key={metric.metric_name}>
                    <small>{metricLabels[metric.metric_name] ?? metric.metric_name}</small>
                    <strong>{metricDisplay(metric)}</strong>
                    <span className={`metric-review ${metric.review_status}`}>
                      {metric.report_period} · 日点位总回报
                    </span>
                  </article>
                ))}
              </div>
              <p className="panel-note">计算区间和样本数记录在 Bloomberg 数据来源中；日点位文件仅作为 case 目录证据保留，未写入数据库。</p>
            </section>
          ) : null}

          <section className="panel">
              <div className="panel-heading">
              <div><span className="eyebrow">Holdings</span><h2>主要持仓</h2></div>
              <span>前 10 项 · 共 {reportHoldings.length} 项已落库</span>
            </div>
            {reportHoldings.length ? (
              <div className="table-wrap compact-table">
                <table>
                  <thead><tr><th>#</th><th>证券</th><th>Ticker</th><th className="holding-registration">注册地</th><th className="holding-type">类型</th><th>持仓份额</th><th>持仓市值（原币）</th><th>权重</th><th>来源</th></tr></thead>
                  <tbody>
                    {displayedHoldings.map((holding, index) => (
                      <tr key={`${holding.security_name}-${holding.ticker ?? index}`}>
                        <td className="muted mono">{String(index + 1).padStart(2, "0")}</td>
                        <td><strong className="table-secondary">{holding.security_name}</strong></td>
                        <td className="mono">{holding.ticker || "—"}</td>
                        <td className="holding-registration">{holding.registration_place || "—"}</td>
                        <td className="holding-type">{holding.holding_type || "—"}</td>
                        <td className="mono">{holding.holding_quantity === null || holding.holding_quantity === undefined ? "—" : formatNumber(holding.holding_quantity, 0)}</td>
                        <td className="mono">{holding.market_value === null || holding.market_value === undefined ? "—" : formatNumber(holding.market_value, 0)}</td>
                        <td><b>{formatNumber(holding.weight)}%</b></td>
                        <td><span className="source-chip">{holding.source_label || "—"}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="panel-empty">报告未披露可配对的持仓数据。</div>
            )}
          </section>

          <section className="panel">
            <div className="panel-heading">
              <div><span className="eyebrow">Structure</span><h2>投资组合结构</h2></div>
            </div>
            <div className="allocation-grid">
              {[...groupedAllocations.entries()].map(([dimension, items]) => (
                <AllocationGroup
                  key={dimension}
                  title={dimensionLabels[dimension] ?? dimension}
                  items={items}
                />
              ))}
              {!groupedAllocations.size ? <div className="panel-empty">当前报告没有可用的地区和行业分布。</div> : null}
            </div>
          </section>
        </div>

        <aside className="detail-side">
          <section className="panel evidence-panel">
            <div className="panel-heading evidence-heading">
              <div><span className="eyebrow">Evidence</span><h2>数据来源</h2></div>
              <div className="detail-actions"><button className="button ghost" type="button" onClick={() => void openSourceManage()}>管理</button><button className="button secondary supplement-trigger" type="button" onClick={() => { setSupplementOpen(true); setSupplementMinimized(false); }}><Upload size={13} />补充来源</button></div>
            </div>
            {fund.report_sources.map((source) => {
              const label = source.source_label || source.report_kind || "报告来源";
              const url = source.report_kind === "monthly"
                ? source.monthly_report_url || source.fallback_report_url
                : source.report_kind === "annual"
                  ? source.annual_report_url || source.semiannual_report_url || source.fallback_report_url || source.monthly_report_url
                  : source.report_kind === "semiannual"
                    ? source.semiannual_report_url || source.fallback_report_url || source.monthly_report_url
                    : source.monthly_report_url || source.fallback_report_url || source.semiannual_report_url || source.annual_report_url;
              return (
                <div className="evidence-block" key={source.source_id}>
                  <FileText size={18} />
                  <div>
                    <strong>{label}</strong>
                    <span>
                      {source.report_period ? `报告期 ${formatDate(source.report_period)}` : "候选报告入口"}
                      {source.language ? ` · ${source.language.toUpperCase()}` : ""}
                    </span>
                    {source.available_sections.length ? (
                      <small>{source.available_sections.join(" · ")}</small>
                    ) : null}
                    {url ? (
                      <a href={url} target="_blank" rel="noreferrer">
                        打开{label} <ExternalLink size={13} />
                      </a>
                    ) : <code>未配置 URL</code>}
                    {source.document_version_id ? (
                      <button
                        className="source-data-link"
                        type="button"
                        onClick={() => setSelectedReportKey(source.document_version_id)}
                      >
                        查看该来源结构化数据
                      </button>
                    ) : null}
                  </div>
                </div>
              );
            })}
            <div className="evidence-block">
              <Fingerprint size={18} />
              <div>
                <strong>{fund.latest_document ? `报告 ${formatDate(fund.latest_document.report_date)}` : "暂无原文版本"}</strong>
                <span>SHA-256</span>
                <code>{fund.latest_document?.sha256?.slice(0, 18) ?? "—"}</code>
              </div>
            </div>
            <div className="evidence-block">
              <ShieldCheck size={18} />
              <div>
                {fund.quality_status === "complete" ? <strong>质量门已通过</strong> : <Link className="quality-review-text" to={`/quality/${encodeURIComponent(fund.fund_code)}`}>待复核 · 打开复核页面</Link>}
                <span>未披露和缺失值不会转成 0。</span>
              </div>
            </div>
          </section>

          <section className="panel">
            <span className="eyebrow">Taxonomy</span>
            <h2>分类与标签</h2>
            <div className="tag-cloud">
              {fund.asset_category ? <span>{fund.asset_category}</span> : null}
              {fund.asset_subcategory ? <span>{fund.asset_subcategory}</span> : null}
              {fund.tags.slice(0, 12).map((tag) => <span key={`${tag.type}-${tag.value}`}>{tag.value}</span>)}
            </div>
            <div className="region-list">
              {fund.regions.map((region) => (
                <div key={`${region.level}-${region.name}`}>
                  <small>{region.level.replaceAll("_", " ")}</small>
                  <strong>{region.name}</strong>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <span className="eyebrow">Identifiers</span>
            <div className="id-list">
              <div><small>HKEX code</small><code>{fund.fund_code}</code></div>
              {fund.stock_codes.map((code) => <div key={code}><small>Stock code</small><code>{code}</code></div>)}
            </div>
          </section>
        </aside>
      </div>
      {supplementOpen && !supplementMinimized ? (
        <div className="modal-backdrop" role="presentation">
          <div className="supplement-modal" role="dialog" aria-modal="true" aria-labelledby="supplement-title">
            <div className="modal-heading"><div><span className="eyebrow">Supplementary source</span><h2 id="supplement-title">补充来源 · {fund.fund_code}</h2></div><div className="modal-heading-actions"><button className="icon-button" type="button" onClick={() => setSupplementMinimized(true)} aria-label="最小化"><Minus size={16} /></button><button className="icon-button" type="button" onClick={closeSupplement} aria-label="关闭并取消任务"><X size={16} /></button></div></div>
            <p className="modal-hint">输入新的报告链接，或上传 PDF。解析完成后会核对文件中的基金名称，再决定是否自动落库。</p>
            <label className="modal-field"><span>PDF 链接</span><input value={supplementUrl} onChange={(event) => setSupplementUrl(event.target.value)} placeholder="https://…/report.pdf" disabled={supplementStatus === "running" || supplementStatus === "submitting"} /></label>
            <div className="modal-or">或</div>
            <label className="file-picker"><Upload size={15} /><span>{supplementFile?.name || "选择本地 PDF 文件"}</span><input type="file" accept="application/pdf,.pdf" onChange={(event) => setSupplementFile(event.target.files?.[0])} disabled={supplementStatus === "running" || supplementStatus === "submitting"} /></label>
            <div className="modal-row"><label className="modal-field"><span>结构化模型</span><select value={supplementProvider} onChange={(event) => setSupplementProvider(event.target.value as typeof supplementProvider)} disabled={supplementStatus === "running" || supplementStatus === "submitting"}><option value="local">本地模型（Qwen 4B）</option><option value="deepseek">DeepSeek</option><option value="qwen">Qwen 云端</option></select></label><label className="check-field"><input type="checkbox" checked={supplementFixed} onChange={(event) => setSupplementFixed(event.target.checked)} disabled={supplementStatus === "running" || supplementStatus === "submitting"} /><span>固定为候选来源</span></label></div>
            <div className="estimate-line"><Clock3 size={14} />预计用时：本地模型约 5 分钟，云端模型约 12 分钟</div>
            {supplementMessage ? <div className={`supplement-status ${supplementStatus}`}>{supplementMessage}</div> : null}
            <div className="modal-actions"><button className="button ghost" type="button" onClick={closeSupplement}>{supplementStatus === "success" ? "确定" : "关闭"}</button>{supplementStatus !== "success" ? <button className="button primary" type="button" onClick={() => void submitSupplement()} disabled={supplementStatus === "submitting" || supplementStatus === "running"}>创建并排队</button> : null}</div>
          </div>
        </div>
      ) : null}
      {supplementOpen && supplementMinimized ? <button className="supplement-minimized" type="button" onClick={() => setSupplementMinimized(false)}>补充来源任务 · {supplementStatus === "running" ? "执行中" : "排队中"}</button> : null}
      {sourceManageOpen && sourceManageData ? (
        <div className="modal-backdrop" role="presentation">
          <div className="supplement-modal source-manage-modal" role="dialog" aria-modal="true" aria-labelledby="source-manage-title">
            <div className="modal-heading"><div><span className="eyebrow">Source management</span><h2 id="source-manage-title">数据来源管理 · {fund.fund_code}</h2></div><button className="icon-button" type="button" onClick={() => setSourceManageOpen(false)} aria-label="关闭"><X size={16} /></button></div>
            <p className="modal-hint">隐藏只影响默认展示；删除会移除当前基金的来源关联及派生数据，原始文档仍保留。</p>
            <div className="source-manage-list">{sourceManageData.items.map((item) => <label className="source-manage-row" key={item.source_id}><input type="checkbox" checked={sourceManageSelected.includes(item.source_id)} onChange={() => setSourceManageSelected((current) => current.includes(item.source_id) ? current.filter((id) => id !== item.source_id) : [...current, item.source_id])} /><span><strong>{item.label}</strong><small>{item.report_kind || "报告"} · {item.report_period || "未识别"} · {item.is_visible ? "显示" : "已隐藏"} · 指标 {item.metric_count} · 持仓 {item.holding_count}</small></span></label>)}</div>
            <div className="modal-actions"><button className="button ghost" type="button" disabled={!sourceManageSelected.length || sourceManageBusy} onClick={() => void runSourceAction("show")}>显示</button><button className="button ghost" type="button" disabled={!sourceManageSelected.length || sourceManageBusy} onClick={() => void runSourceAction("hide")}>隐藏</button><button className="button danger" type="button" disabled={!sourceManageSelected.length || sourceManageBusy} onClick={() => void runSourceAction("delete")}>删除</button></div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
