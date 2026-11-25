import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Check, ChevronDown, ChevronRight, Edit3, FileJson } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { QualityIssue, QualityReview } from "../api/types";
import { ErrorState, LoadingState } from "../components/States";
import { PageHeader } from "../components/PageHeader";
import { QualityBadge } from "../components/StatusBadge";
import { ContextualAskPanel } from "../components/ContextualAskPanel";
import { formatDate, formatDateTime } from "../lib/format";

const severityLabel: Record<string, string> = { warning: "待确认", error: "需修正", blocker: "阻断" };

type JsonTreeValue = string | number | boolean | null | JsonTreeValue[] | { [key: string]: JsonTreeValue };

function isJsonBranch(value: JsonTreeValue): value is JsonTreeValue[] | { [key: string]: JsonTreeValue } {
  return typeof value === "object" && value !== null;
}

function JsonTreeNode({
  label,
  value,
  path,
  depth,
  collapsed,
  onToggle,
}: {
  label: string;
  value: JsonTreeValue;
  path: string;
  depth: number;
  collapsed: Set<string>;
  onToggle: (path: string) => void;
}) {
  const branch = isJsonBranch(value);
  if (!branch) {
    return (
      <div className="json-tree-line json-tree-leaf" style={{ paddingLeft: `${depth * 18 + 22}px` }}>
        <span className="json-tree-key">{label}</span>
        <span className={`json-tree-value json-${value === null ? "null" : typeof value}`}>
          {JSON.stringify(value)}
        </span>
      </div>
    );
  }
  const isArray = Array.isArray(value);
  const entries = isArray
    ? value.map((item, index) => [String(index), item] as const)
    : Object.entries(value);
  const isCollapsed = collapsed.has(path);
  return (
    <div className="json-tree-branch">
      <div className="json-tree-line" style={{ paddingLeft: `${depth * 18}px` }}>
        <button className="json-tree-toggle" type="button" onClick={() => onToggle(path)} aria-label={isCollapsed ? "展开层级" : "折叠层级"}>
          {isCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
        </button>
        <span className="json-tree-key">{label}</span>
        <span className="json-tree-kind">{isArray ? `[${entries.length}]` : `{${entries.length}}`}</span>
      </div>
      {!isCollapsed ? entries.map(([key, child]) => (
        <JsonTreeNode
          key={`${path}.${key}`}
          label={isArray ? `[${key}]` : key}
          value={child}
          path={`${path}.${key}`}
          depth={depth + 1}
          collapsed={collapsed}
          onToggle={onToggle}
        />
      )) : null}
    </div>
  );
}

function JsonTreeView({ value }: { value: string }) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const parsed = useMemo(() => {
    try {
      return { ok: true as const, value: JSON.parse(value) as JsonTreeValue };
    } catch {
      return { ok: false as const };
    }
  }, [value]);

  useEffect(() => setCollapsed(new Set()), [value]);

  if (!parsed.ok) {
    return <pre className="json-tree-invalid">JSON 暂时无法解析，请点击“编辑”修正格式。
{value}</pre>;
  }
  return (
    <div className="review-json-tree" role="tree" aria-label="结构化 JSON 层级视图">
      <div className="json-tree-hint">点击左侧箭头展开或折叠层级</div>
      <JsonTreeNode label="$" value={parsed.value} path="$" depth={0} collapsed={collapsed} onToggle={(path) => setCollapsed((current) => {
        const next = new Set(current);
        if (next.has(path)) next.delete(path); else next.add(path);
        return next;
      })} />
    </div>
  );
}

export function QualityReviewPage() {
  const { fundCode = "" } = useParams();
  const [issues, setIssues] = useState<QualityIssue[]>([]);
  const [activeIssueId, setActiveIssueId] = useState<string | null>(null);
  const [review, setReview] = useState<QualityReview | null>(null);
  const [listLoading, setListLoading] = useState(true);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [reviewError, setReviewError] = useState<ApiError | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    setListLoading(true);
    api.quality({ fundCode, status: "open", pageSize: 100 })
      .then((response) => {
        if (!active) return;
        const nextIssues = response.data.items.filter((item) => item.fund_code === fundCode);
        setIssues(nextIssues);
        setActiveIssueId((current) => (current && nextIssues.some((item) => item.issue_id === current) ? current : nextIssues[0]?.issue_id ?? null));
        setError(null);
      })
      .catch((reason: unknown) => { if (active) setError(reason instanceof ApiError ? reason : null); })
      .finally(() => { if (active) setListLoading(false); });
    return () => { active = false; };
  }, [fundCode, tick]);

  useEffect(() => {
    if (!activeIssueId) { setReview(null); return; }
    let active = true;
    setReviewLoading(true);
    setReviewError(null);
    api.qualityReview(activeIssueId)
      .then((response) => {
        if (!active) return;
        setReview(response.data);
        setDraft(JSON.stringify(response.data.record, null, 2));
        setEditing(false);
        setComment("");
      })
      .catch((reason: unknown) => { if (active) setReviewError(reason instanceof ApiError ? reason : null); })
      .finally(() => { if (active) setReviewLoading(false); });
    return () => { active = false; };
  }, [activeIssueId]);

  async function confirmSelected() {
    if (!activeIssueId) return;
    setBusy(true);
    try { await api.confirmQuality(activeIssueId, comment.trim() || undefined); setTick((value) => value + 1); }
    catch (reason: unknown) { setReviewError(reason instanceof ApiError ? reason : null); }
    finally { setBusy(false); }
  }

  async function saveEdited() {
    if (!activeIssueId) return;
    let record: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(draft);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("编辑内容必须是 JSON 对象");
      record = parsed as Record<string, unknown>;
    } catch (reason) {
      setReviewError(new ApiError(reason instanceof Error ? reason.message : "JSON 格式不正确", { code: "E_JSON_EDITOR", requestId: "", retryable: false, status: 422 }));
      return;
    }
    setBusy(true);
    try { await api.saveQualityReview(activeIssueId, record, { comment: comment.trim() || undefined, resolve: true }); setEditing(false); setTick((value) => value + 1); }
    catch (reason: unknown) { setReviewError(reason instanceof ApiError ? reason : null); }
    finally { setBusy(false); }
  }

  const fund = review?.fund;
  const name = fund?.name_cn ?? issues[0]?.fund_name ?? fundCode;

  return (
    <>
      <Link to="/quality" className="back-link"><ArrowLeft size={15} /> 返回数据质量看板</Link>
      {listLoading ? <LoadingState label="正在读取基金复核问题…" /> : null}
      {error ? <ErrorState message={error.message} requestId={error.requestId} onRetry={() => setTick((value) => value + 1)} /> : null}
      {!listLoading && !error && !issues.length ? <section className="panel quality-review-empty"><h2>{fundCode} 暂无待复核问题</h2><p>该基金可能已经完成复核，或当前快照没有质量问题。</p><Link to="/quality" className="button secondary">返回数据质量看板</Link></section> : null}
      {!listLoading && !error && issues.length ? (
        <>
          <PageHeader eyebrow={`${fundCode} · 单基金复核`} title={name} description="先选择要处理的问题，再在下方执行确认或编辑。所有操作都会留下审计记录。" actions={fund ? <QualityBadge status={fund.quality_status} /> : null} />
          <section className="review-fund-strip">
            <div><small>基金代码</small><strong>{fundCode}</strong></div><div><small>管理人</small><strong>{fund?.manager_name ?? issues[0]?.manager_name ?? "未披露"}</strong></div><div><small>资产类别</small><strong>{fund?.asset_category ?? issues[0]?.asset_category ?? "未分类"}{fund?.asset_subcategory ? ` · ${fund.asset_subcategory}` : ""}</strong></div><div><small>基础币种</small><strong>{fund?.base_currency ?? issues[0]?.base_currency ?? "未披露"}</strong></div><div><small>注册地</small><strong>{fund?.domicile ?? issues[0]?.domicile ?? "未披露"}</strong></div><div><small>最新报告期</small><strong>{formatDate(fund?.report_period ?? issues[0]?.report_period)}</strong></div>
          </section>
          <ContextualAskPanel
            key={activeIssueId ?? "quality-review-context"}
            fundCodes={[fundCode]}
            title={`询问 ${fundCode} 的质量问题`}
            eyebrow="Review evidence assistant"
            description="问题会绑定当前基金和发布快照，适合追问异常原因、原文页码和结构化字段来源。"
            initialQuestion={review?.issue.details ? `为什么 ${fundCode}：${review.issue.details}？` : `为什么 ${fundCode} 进入待复核清单？`}
            contextNote={`${fundCode} · 当前问题已自动带入`}
            suggestions={[
              `为什么 ${fundCode} 进入待复核清单？`,
              `${fundCode} 的异常字段对应原文哪一页？`,
              `${fundCode} 最新报告的结构化数据有哪些缺失？`,
            ]}
          />
          <section className="panel review-issues-section">
            <div className="panel-heading"><div><span className="eyebrow">Issues for this fund</span><h2>待处理问题</h2></div><span>{issues.length} 条</span></div>
            <div className="review-issue-cards">
              {issues.map((issue) => <button className={`review-issue-card ${activeIssueId === issue.issue_id ? "selected" : ""}`} key={issue.issue_id} onClick={() => setActiveIssueId(issue.issue_id)}><div><span className={`mini-status severity-${issue.severity}`}>{severityLabel[issue.severity] ?? issue.severity}</span><small>{issue.issue_type} · {formatDateTime(issue.created_at)}</small></div><strong>{issue.details}</strong><span>{activeIssueId === issue.issue_id ? "正在处理" : "点击处理"}</span></button>)}
            </div>
          </section>
          <section className="panel review-editor-section">
            {reviewLoading ? <LoadingState label="正在读取基金完整记录…" /> : null}
            {reviewError ? <ErrorState message={reviewError.message} requestId={reviewError.requestId} /> : null}
            {!reviewLoading && review ? (
              <>
                <div className="panel-heading"><div><span className="eyebrow">Review record</span><h2>确认与编辑</h2><p>基金身份信息用于核对；编辑保存范围为结构化指标、持仓和资产分配。</p></div>{!editing ? <button className="button ghost" onClick={() => setEditing(true)}><Edit3 size={14} /> 编辑</button> : null}</div>
                <div className="review-record-meta"><FileJson size={15} /><span>当前问题：{review.issue.details}</span><span className="mono">报告期 {formatDate(review.fund?.report_period)}</span></div>
                {editing ? (
                  <div className="review-json-edit-stack">
                    <JsonTreeView value={draft} />
                    <textarea className="review-json-editor editing" value={draft} onChange={(event) => setDraft(event.target.value)} spellCheck={false} />
                  </div>
                ) : (
                  <JsonTreeView value={draft} />
                )}
                <label className="review-comment"><span>复核备注（可选）</span><input value={comment} onChange={(event) => setComment(event.target.value)} placeholder="说明依据、页码或修订原因" /></label>
                <div className="review-actions">{editing ? <><button className="button secondary" onClick={() => { setEditing(false); setDraft(JSON.stringify(review.record, null, 2)); }}>取消</button><button className="button primary" disabled={busy} onClick={saveEdited}><Check size={14} /> 保存编辑并确认</button></> : <button className="button primary" disabled={busy || review.issue.status === "resolved"} onClick={confirmSelected}><Check size={14} /> 确认此问题</button>}</div>
              </>
            ) : null}
          </section>
        </>
      ) : null}
    </>
  );
}
