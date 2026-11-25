import { useMemo, useState } from "react";
import {
  BookOpen,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  FileText,
  Hash,
  MessageSquareText,
  ClipboardCheck,
  Send,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { api, ApiError } from "../api/client";
import type { AgentPlanData, ChatEvidence, ChatResponseData } from "../api/types";
import { useScope } from "../context/ScopeContext";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/States";
import { Link } from "react-router-dom";

const statusCopy: Record<ChatResponseData["status"], { label: string; className: string; description: string }> = {
  answered: {
    label: "已回答",
    className: "chat-status-answered",
    description: "回答中的事实均来自当前发布快照。",
  },
  insufficient_evidence: {
    label: "证据不足",
    className: "chat-status-insufficient",
    description: "当前范围或报告期没有足够结构化证据，系统没有补猜。",
  },
  evidence_only: {
    label: "仅返回证据",
    className: "chat-status-evidence",
    description: "已找到原文片段，但当前页面不会对未定义字段做模型归纳。",
  },
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

const agentIntentLabels: Record<AgentPlanData["intent"], string> = {
  query: "只读查询",
  compare: "只读比较",
  supplementary_source: "补充来源",
  create_monthly_job: "创建月报任务",
  create_semiannual_job: "创建半年报任务",
};

function evidenceAnchor(evidenceId: string): string {
  return `evidence-${encodeURIComponent(evidenceId)}`;
}

function evidenceSource(evidence: ChatEvidence): string {
  return evidence.source_label ?? evidence.source_path ?? evidence.source_url ?? "来源未记录";
}

function EvidenceCard({ evidence }: { evidence: ChatEvidence }) {
  return (
    <article className="chat-evidence-card" id={evidenceAnchor(evidence.evidence_id)}>
      <div className="chat-evidence-card-head">
        <div className="chat-evidence-kind">
          {evidence.evidence_type === "structured_record" ? <ShieldCheck size={14} /> : <FileText size={14} />}
          <span>{evidence.evidence_type === "structured_record" ? "结构化记录" : "原文切片"}</span>
        </div>
        <code>{evidence.evidence_id}</code>
      </div>
      <div className="chat-evidence-meta">
        <span><b>{evidence.fund_code}</b> {evidence.fund_name}</span>
        <span>报告期 {evidence.report_period ?? "未记录"}</span>
        <span>{evidence.page_number ? `第 ${evidence.page_number} 页` : "页码未记录"}</span>
      </div>
      <p className="chat-evidence-content">{evidence.content}</p>
      <div className="chat-evidence-footer">
        <span title={evidenceSource(evidence)}>{evidenceSource(evidence)}</span>
        {evidence.source_url ? (
          <a href={evidence.source_url} target="_blank" rel="noreferrer">
            打开来源 <ChevronRight size={12} />
          </a>
        ) : null}
        {evidence.content_hash ? <code title={evidence.content_hash}>hash {evidence.content_hash.slice(0, 12)}…</code> : null}
      </div>
    </article>
  );
}

export function KnowledgePage() {
  const { scope } = useScope();
  const scopedCodes = scope.fundCodes.length > 0 && scope.fundCodes.length <= 100
    ? scope.fundCodes
    : [];
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<ChatResponseData | null>(null);
  const [agentPlan, setAgentPlan] = useState<AgentPlanData | null>(null);
  const [agentLoading, setAgentLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const resultEvidence = useMemo(
    () => new Map((result?.evidence ?? []).map((evidence) => [evidence.evidence_id, evidence])),
    [result],
  );

  async function submit(event?: React.FormEvent) {
    event?.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || loading) return;
    setLoading(true);
    setError(null);
    try {
      const response = await api.chat({
        question: trimmed,
        fund_codes: scopedCodes,
        mode: "hybrid",
        limit: 8,
      });
      setResult(response.data);
      setAgentPlan(null);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  async function createAgentPlan() {
    const trimmed = question.trim();
    if (!trimmed || agentLoading) return;
    setAgentLoading(true);
    setError(null);
    try {
      const response = await api.agentPlan({ question: trimmed, fund_codes: scopedCodes });
      setAgentPlan(response.data);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
    } finally {
      setAgentLoading(false);
    }
  }

  async function confirmAgentPlan(confirmed: boolean) {
    if (!agentPlan || agentLoading) return;
    setAgentLoading(true);
    setError(null);
    try {
      const response = await api.confirmAgentRun(agentPlan.run_id, confirmed);
      setAgentPlan(response.data);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason : null);
    } finally {
      setAgentLoading(false);
    }
  }

  function usePrompt(value: string) {
    setQuestion(value);
    setResult(null);
    setError(null);
  }

  const status = result ? statusCopy[result.status] : null;

  return (
    <div className="knowledge-page">
      <PageHeader
        eyebrow="Knowledge workspace"
        title="知识问答"
        description="先查结构化数据库，再返回可核验的报告证据。问答不会用模型补猜缺失数据。"
        actions={
          <div className="knowledge-header-state">
            <Sparkles size={14} />
            <span>{scope.label}{scope.fundCodes.length ? ` · ${scope.fundCodes.length} 只` : ""}</span>
          </div>
        }
      />

      <section className="knowledge-compose panel">
        <div className="knowledge-compose-head">
          <div>
            <span className="eyebrow">Ask the published snapshot</span>
            <h2>提出一个可验证的问题</h2>
          </div>
          <span className="knowledge-contract"><ShieldCheck size={14} /> SQL 优先 · 证据绑定</span>
        </div>
        <form onSubmit={submit}>
          <label className="knowledge-question">
            <span>问题</span>
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="例如：比较 BJK348 与 BMV635 2026年6月30日的日流动资产"
              rows={3}
              maxLength={2000}
            />
          </label>
          <div className="knowledge-submit-row">
            <button className="button secondary" type="button" onClick={() => void createAgentPlan()} disabled={!question.trim() || agentLoading}>
              <ClipboardCheck size={15} /> {agentLoading ? "生成中…" : "生成受控计划"}
            </button>
            <button className="button primary knowledge-submit" type="submit" disabled={!question.trim() || loading}>
              <Send size={15} /> {loading ? "检索中…" : "提交问题"}
            </button>
          </div>
        </form>
        <div className="knowledge-scope-note">
          <BookOpen size={13} />
          <span>当前范围：{scope.label}。基金代码和报告期可直接写在问题中。</span>
          {scopedCodes.length ? <code>{scopedCodes.length} 个代码已带入</code> : null}
        </div>
        <div className="knowledge-prompts">
          <span>试试：</span>
          <button type="button" onClick={() => usePrompt("BJK348 的年化收益率")}>BJK348 的年化收益率</button>
          <button type="button" onClick={() => usePrompt("比较 BDO182 与 BMV635 2026年6月30日的日流动资产")}>比较两只基金的日流动资产</button>
          <button type="button" onClick={() => usePrompt("BJK348 2026年9月前五大持仓")}>查看前五大持仓</button>
        </div>
      </section>

      {loading ? <LoadingState label="正在查询当前发布快照并组装证据…" /> : null}
      {error ? <ErrorState message={error.message} requestId={error.requestId} onRetry={() => void submit()} /> : null}

      {agentPlan ? (
        <section className="panel agent-plan-card">
          <div className="agent-plan-head">
            <div><span className="eyebrow">Controlled agent</span><h2>任务计划确认</h2></div>
            <span className={`agent-plan-status ${agentPlan.status}`}>{agentPlan.status === "awaiting_confirmation" ? "待确认" : agentPlan.status === "needs_clarification" ? "待澄清" : agentPlan.status === "confirmed" ? "已创建" : agentPlan.status === "rejected" ? "已拒绝" : "只读计划"}</span>
          </div>
          <div className="agent-plan-summary">
            <span><b>意图</b>{agentIntentLabels[agentPlan.intent]}</span>
            <span><b>范围</b>{agentPlan.scope_count} 只基金</span>
            <span><b>目标</b>{agentPlan.task_target === "fund_semiannual" ? "半年报" : agentPlan.task_target === "fund_monthly" ? "月报" : "不创建任务"}</span>
            <span><b>模型</b>{String(agentPlan.model_policy.structured_provider ?? "不调用模型")}</span>
            {agentPlan.estimated_minutes.maximum > 0 ? <span><b>预计</b>{agentPlan.estimated_minutes.minimum}–{agentPlan.estimated_minutes.maximum} 分钟</span> : null}
          </div>
          <ul className="agent-plan-impact">{agentPlan.impact_summary.map((item) => <li key={item}>{item}</li>)}</ul>
          {agentPlan.limitations.length ? <div className="agent-plan-limitations">{agentPlan.limitations.map((item) => <span key={item}>{limitationLabels[item] ?? item}</span>)}</div> : null}
          {agentPlan.status === "needs_clarification" ? (
            <div className="agent-plan-clarification">{agentPlan.limitations[0] ?? "请补充基金范围、报告类型或报告期。"}</div>
          ) : agentPlan.status === "awaiting_confirmation" ? (
            <div className="agent-plan-actions">
              <button className="button primary" type="button" onClick={() => void confirmAgentPlan(true)} disabled={agentLoading}><CheckCircle2 size={15} /> 确认并创建任务</button>
              <button className="button ghost" type="button" onClick={() => void confirmAgentPlan(false)} disabled={agentLoading}>拒绝</button>
              <small>未确认前不会下载、解析、结构化或写入任务队列。</small>
            </div>
          ) : null}
          {agentPlan.status === "confirmed" && agentPlan.job_id ? <Link className="button secondary" to="/tasks">进入数据任务查看进度 <ChevronRight size={14} /></Link> : null}
        </section>
      ) : null}

      {!loading && !error && !result ? (
        <section className="knowledge-welcome panel">
          <div className="knowledge-welcome-icon"><MessageSquareText size={22} /></div>
          <div>
            <span className="eyebrow">Evidence first</span>
            <h2>从结构化答案开始，再回到原文</h2>
            <p>指标和持仓问题由 SQL 直接回答；文字问题最多返回当前快照中的证据片段。每条事实都会标记基金、报告期、来源和页码。</p>
          </div>
          <div className="knowledge-welcome-points">
            <span><CheckCircle2 size={14} /> 不跨基金拼接</span>
            <span><CheckCircle2 size={14} /> 不读取隐藏来源</span>
            <span><CheckCircle2 size={14} /> 证据不足明确拒答</span>
          </div>
        </section>
      ) : null}

      {result && status ? (
        <div className="knowledge-result-stack">
          <section className="panel knowledge-answer-card">
            <div className="knowledge-result-head">
              <div>
                <span className="eyebrow">Answer</span>
                <h2>回答</h2>
              </div>
              <span className={`chat-status ${status.className}`}><span />{status.label}</span>
            </div>
            <div className="knowledge-answer-meta">
              <span><BookOpen size={13} /> {routeLabels[result.route]}</span>
              <span>快照 <code>{result.snapshot_id.slice(0, 8)}</code></span>
              <span>检索模式 <b>hybrid</b></span>
            </div>
            <p className="knowledge-answer-text">{result.answer}</p>
            <p className="knowledge-status-description">{status.description}</p>
            {result.limitations.length ? (
              <div className="knowledge-limitations">
                <CircleAlert size={14} />
                <div>{result.limitations.map((limitation) => <span key={limitation}>{limitationLabels[limitation] ?? limitation}</span>)}</div>
              </div>
            ) : null}
          </section>

          {result.structured_values.length ? (
            <section className="panel knowledge-structured-card">
              <div className="panel-heading"><div><span className="eyebrow">Structured values</span><h2>结构化结果</h2></div><span>{result.structured_values.length} 条</span></div>
              <div className="table-wrap"><table><thead><tr><th>基金</th><th>字段</th><th>值</th><th>报告期</th><th>质量</th></tr></thead><tbody>
                {result.structured_values.map((value) => <tr key={`${value.document_version_id}-${value.field}-${value.fund_code}`}>
                  <td><strong>{value.fund_code}</strong><span className="table-tertiary">{value.fund_name}</span></td>
                  <td className="mono">{value.field}</td>
                  <td><b>{value.value === null ? "未披露" : `${value.value}${value.unit === "percent" ? "%" : value.unit ? ` ${value.unit}` : ""}`}</b></td>
                  <td className="mono">{value.report_period}</td>
                  <td><span className={`chat-quality-token ${value.quality_status}`}>{value.quality_status}</span></td>
                </tr>)}
              </tbody></table></div>
            </section>
          ) : null}

          {result.claims.length ? (
            <section className="panel knowledge-claims-card">
              <div className="panel-heading"><div><span className="eyebrow">Claims</span><h2>事实声明与引用</h2></div><span>{result.claims.length} 条声明</span></div>
              <div className="knowledge-claims">
                {result.claims.map((claim) => <article key={claim.claim_id}>
                  <p>{claim.text}</p>
                  <div>{claim.evidence_ids.map((id) => {
                    const evidence = resultEvidence.get(id);
                    return <a href={`#${evidenceAnchor(id)}`} key={id}><Hash size={11} /> {evidence?.fund_code ?? id}</a>;
                  })}</div>
                </article>)}
              </div>
            </section>
          ) : null}

          <section className="panel knowledge-evidence-card">
            <div className="panel-heading"><div><span className="eyebrow">Evidence</span><h2>证据查看</h2></div><span>{result.evidence.length} 条可核验证据</span></div>
            {result.evidence.length ? <div className="knowledge-evidence-list">{result.evidence.map((evidence) => <EvidenceCard key={evidence.evidence_id} evidence={evidence} />)}</div> : <p className="muted">当前回答没有可展示的证据片段。</p>}
          </section>
        </div>
      ) : null}
    </div>
  );
}
