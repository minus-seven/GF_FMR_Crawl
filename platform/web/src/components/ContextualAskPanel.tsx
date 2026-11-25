import { useEffect, useMemo, useState } from "react";
import { BookOpen, CircleAlert, FileText, Hash, Send, ShieldCheck } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { ChatEvidence, ChatResponseData } from "../api/types";
import { ErrorState, LoadingState } from "./States";

interface ContextualAskPanelProps {
  title?: string;
  eyebrow?: string;
  description?: string;
  fundCodes?: string[];
  initialQuestion?: string;
  suggestions?: string[];
  contextNote?: string;
}

const statusLabels: Record<ChatResponseData["status"], string> = {
  answered: "已回答",
  insufficient_evidence: "证据不足",
  evidence_only: "仅返回证据",
};

const statusClasses: Record<ChatResponseData["status"], string> = {
  answered: "chat-status-answered",
  insufficient_evidence: "chat-status-insufficient",
  evidence_only: "chat-status-evidence",
};

const routeLabels: Record<ChatResponseData["route"], string> = {
  sql: "SQL 结构化查询",
  "sql+knowledge": "SQL + 知识库",
  knowledge_search: "知识库证据检索",
};

const limitationLabels: Record<string, string> = {
  model_summarization_not_enabled: "模型归纳尚未启用",
  no_citation_ready_evidence: "没有找到可引用的证据片段",
  vector_unavailable_fallback_to_fulltext: "向量服务不可用，已降级全文检索",
  fund_scope_required: "需要指定基金范围",
};

function evidenceSource(evidence: ChatEvidence): string {
  return evidence.source_label ?? evidence.source_path ?? evidence.source_url ?? "来源未记录";
}

function evidenceAnchor(evidenceId: string): string {
  return `context-evidence-${encodeURIComponent(evidenceId)}`;
}

function EvidenceItem({ evidence }: { evidence: ChatEvidence }) {
  return (
    <article className="context-evidence-item" id={evidenceAnchor(evidence.evidence_id)}>
      <div className="context-evidence-head">
        <span>{evidence.evidence_type === "structured_record" ? <ShieldCheck size={13} /> : <FileText size={13} />}{evidence.evidence_type === "structured_record" ? "结构化记录" : "原文片段"}</span>
        <code>{evidence.evidence_id}</code>
      </div>
      <div className="context-evidence-meta"><b>{evidence.fund_code}</b><span>{evidence.fund_name}</span><span>报告期 {evidence.report_period ?? "未记录"}</span><span>{evidence.page_number ? `第 ${evidence.page_number} 页` : "页码未记录"}</span></div>
      <p>{evidence.content}</p>
      <div className="context-evidence-foot"><span title={evidenceSource(evidence)}>{evidenceSource(evidence)}</span>{evidence.source_url ? <a href={evidence.source_url} target="_blank" rel="noreferrer">打开来源</a> : null}</div>
    </article>
  );
}

export function ContextualAskPanel({
  title = "在当前范围提问",
  eyebrow = "Contextual knowledge",
  description = "问题会绑定当前发布快照，并返回可核验的结构化结果和原文证据。",
  fundCodes = [],
  initialQuestion = "",
  suggestions = [],
  contextNote,
}: ContextualAskPanelProps) {
  const [question, setQuestion] = useState(initialQuestion);
  const [result, setResult] = useState<ChatResponseData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const scopedCodes = useMemo(() => [...new Set(fundCodes)].filter(Boolean).slice(0, 100), [fundCodes]);
  const evidenceById = useMemo(() => new Map((result?.evidence ?? []).map((item) => [item.evidence_id, item])), [result]);

  useEffect(() => {
    if (!result && initialQuestion) setQuestion(initialQuestion);
  }, [initialQuestion, result]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || loading) return;
    setLoading(true);
    setError(null);
    try {
      const response = await api.chat({ question: trimmed, fund_codes: scopedCodes, mode: "hybrid", limit: 8 });
      setResult(response.data);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="panel contextual-ask-panel">
      <div className="contextual-ask-head">
        <div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2><p>{description}</p></div>
        <span className="contextual-ask-contract"><ShieldCheck size={13} /> SQL 优先 · 证据绑定</span>
      </div>
      <form className="contextual-ask-form" onSubmit={submit}>
        <textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：这只基金 2026 年 9 月月报的到期收益率是多少？" rows={2} maxLength={2000} />
        <div className="contextual-ask-actions"><span>{contextNote ?? (scopedCodes.length ? `已带入 ${scopedCodes.length} 只基金` : "未指定基金范围，问题中请写明基金代码")}</span><button className="button primary" type="submit" disabled={!question.trim() || loading}><Send size={14} />{loading ? "检索中…" : "提交问题"}</button></div>
      </form>
      {suggestions.length ? <div className="contextual-ask-suggestions"><span>试试：</span>{suggestions.map((suggestion) => <button type="button" key={suggestion} onClick={() => { setQuestion(suggestion); setResult(null); setError(null); }}>{suggestion}</button>)}</div> : null}
      {loading ? <LoadingState label="正在查询当前发布快照并组装证据…" /> : null}
      {error ? <ErrorState message={error.message} requestId={error.requestId} onRetry={() => void submit({ preventDefault: () => undefined } as React.FormEvent)} /> : null}
      {result ? (
        <div className="contextual-ask-result">
          <div className="contextual-ask-result-head"><div><span className="eyebrow">Answer</span><h3>回答</h3></div><span className={`chat-status ${statusClasses[result.status]}`}><span />{statusLabels[result.status]}</span></div>
          <div className="contextual-ask-meta"><span><BookOpen size={12} />{routeLabels[result.route]}</span><span>快照 <code>{result.snapshot_id.slice(0, 8)}</code></span><span>证据 {result.evidence.length} 条</span></div>
          <p className="contextual-ask-answer">{result.answer}</p>
          {result.limitations.length ? <div className="knowledge-limitations"><CircleAlert size={13} /><div>{result.limitations.map((item) => <span key={item}>{limitationLabels[item] ?? item}</span>)}</div></div> : null}
          {result.structured_values.length ? <div className="contextual-structured"><strong>结构化结果</strong><div className="table-wrap"><table><thead><tr><th>基金</th><th>字段</th><th>值</th><th>报告期</th></tr></thead><tbody>{result.structured_values.map((value) => <tr key={`${value.document_version_id}-${value.field}-${value.fund_code}`}><td><b>{value.fund_code}</b></td><td className="mono">{value.field}</td><td>{value.value === null ? "未披露" : `${value.value}${value.unit === "percent" ? "%" : value.unit ? ` ${value.unit}` : ""}`}</td><td className="mono">{value.report_period}</td></tr>)}</tbody></table></div></div> : null}
          {result.claims.length ? <div className="contextual-claims"><strong>事实声明</strong>{result.claims.map((claim) => <div key={claim.claim_id}><p>{claim.text}</p><span>{claim.evidence_ids.map((id) => <a href={`#${evidenceAnchor(id)}`} key={id}><Hash size={10} />{evidenceById.get(id)?.fund_code ?? id}</a>)}</span></div>)}</div> : null}
          <div className="contextual-evidence"><div className="contextual-evidence-title"><strong>证据查看</strong><span>{result.evidence.length} 条</span></div>{result.evidence.length ? result.evidence.map((item) => <EvidenceItem key={item.evidence_id} evidence={item} />) : <p className="muted">当前回答没有可展示的证据片段。</p>}</div>
        </div>
      ) : null}
    </section>
  );
}
