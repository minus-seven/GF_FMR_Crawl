import { useEffect, useState } from "react";
import { ArrowRight, Database, Files, ShieldCheck, Workflow } from "lucide-react";
import { Link, useOutletContext } from "react-router-dom";
import { api } from "../api/client";
import type { Job, QualityPage } from "../api/types";
import type { ShellOutletContext } from "../app/AppShell";
import { PageHeader } from "../components/PageHeader";
import { JobBadge } from "../components/StatusBadge";
import { formatDateTime, formatNumber } from "../lib/format";

export function OverviewPage() {
  const { snapshot } = useOutletContext<ShellOutletContext>();
  const [quality, setQuality] = useState<QualityPage | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    Promise.all([api.quality({ pageSize: 5 }), api.jobs({ pageSize: 4 })]).then(
      ([qualityResponse, jobResponse]) => {
        setQuality(qualityResponse.data);
        setJobs(jobResponse.data.items);
      },
    );
  }, [snapshot?.snapshot_id]);

  return (
    <>
      <PageHeader
        eyebrow="Research pulse"
        title="基金研究工作台"
        description="从权威主数据出发，核验报告质量，并让每一次更新都进入可回放的批次。"
        actions={
          <Link className="button primary" to="/funds">
            浏览基金库 <ArrowRight size={16} />
          </Link>
        }
      />

      <section className="hero-metrics">
        <article>
          <span className="metric-icon"><Database size={18} /></span>
          <small>HKEX 主数据</small>
          <strong>{formatNumber(snapshot?.fund_count)}</strong>
          <p>唯一基金代码 · 全量主数据已入库</p>
        </article>
        <article>
          <span className="metric-icon"><ShieldCheck size={18} /></span>
          <small>当前质量问题</small>
          <strong>{quality?.total ?? "—"}</strong>
          <p>按当前批次统计 · 未披露保持缺失</p>
        </article>
        <article>
          <span className="metric-icon"><Files size={18} /></span>
          <small>快照发布时间</small>
          <strong className="metric-date">{formatDateTime(snapshot?.published_at)}</strong>
          <p>{snapshot?.source_kind ?? "等待发布来源信息"}</p>
        </article>
        <article>
          <span className="metric-icon"><Workflow size={18} /></span>
          <small>当前版本</small>
          <strong className="metric-version">{snapshot?.release_version ?? "—"}</strong>
          <p className="mono">{snapshot?.snapshot_id?.slice(0, 8) ?? "no snapshot"}</p>
        </article>
      </section>

      <div className="overview-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Data health</span>
              <h2>数据质量提醒</h2>
            </div>
            <Link to="/quality">查看全部</Link>
          </div>
          <div className="quality-list compact">
            {quality?.items.map((issue) => (
              <Link className="quality-overview-row" to={issue.fund_code ? `/quality/${encodeURIComponent(issue.fund_code)}` : "/quality"} key={issue.issue_id}>
                <span className={`severity-marker ${issue.severity}`} />
                <div>
                  <strong>{issue.fund_name ?? "批次级问题"}</strong>
                  <p>{issue.details}</p>
                </div>
                <span className="issue-code">{issue.issue_type}</span>
              </Link>
            ))}
            {!quality?.items.length ? <p className="muted">当前批次没有未处理质量问题。</p> : null}
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Pipeline</span>
              <h2>最近任务</h2>
            </div>
            <Link to="/tasks">任务中心</Link>
          </div>
          <div className="job-mini-list">
            {jobs.map((job) => (
              <Link to="/tasks" className="job-mini-row" key={job.job_id}>
                <div>
                  <code>{job.job_id.slice(0, 8)}</code>
                  <span>{job.scope_count} 只基金 · {job.current_stage ?? job.outcome ?? "已结束"}</span>
                </div>
                <JobBadge status={job.status} />
              </Link>
            ))}
            {!jobs.length ? <p className="muted">尚未创建任务。</p> : null}
          </div>
        </section>
      </div>

      <section className="workflow-strip">
        <div><span>01</span><strong>Scope</strong><p>按基金、公司或标签冻结范围</p></div>
        <div><span>02</span><strong>Refresh</strong><p>下载、解析和结构化形成 staging</p></div>
        <div><span>03</span><strong>Verify</strong><p>质量与 manifest 通过后发布快照</p></div>
        <div><span>04</span><strong>Research</strong><p>查询、比较、追踪并回到原文</p></div>
      </section>
    </>
  );
}
