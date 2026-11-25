import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Database,
  FileSearch,
  LibraryBig,
  LayoutDashboard,
  MessageSquareText,
  Search,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, eventSource } from "../api/client";
import type { Job, JobEvent, Snapshot } from "../api/types";
import { PerspectivePanel } from "../components/PerspectivePanel";
import { JobBadge } from "../components/StatusBadge";
import { formatDateTime } from "../lib/format";
import { mergeJobEvent } from "../lib/jobEvents";

const nav = [
  { to: "/", label: "研究总览", icon: LayoutDashboard },
  { to: "/funds", label: "基金数据库", icon: LibraryBig },
  { to: "/knowledge", label: "知识问答", icon: MessageSquareText },
  { to: "/quality", label: "数据质量", icon: ShieldCheck },
  { to: "/tasks", label: "数据任务", icon: Activity },
];

const activeStageLabels: Record<string, string> = {
  download: "下载",
  parse: "解析",
  structure: "结构化",
  validating: "校验中",
};

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [search, setSearch] = useState("");
  const [offline, setOffline] = useState<ApiError | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let active = true;
    Promise.all([api.snapshot(), api.jobs({ pageSize: 5 })])
      .then(([snapshotResponse, jobResponse]) => {
        if (!active) return;
        setSnapshot(snapshotResponse.data);
        setJobs(jobResponse.data.items);
        setOffline(null);
      })
      .catch((reason: unknown) => {
        if (active) setOffline(reason instanceof ApiError ? reason : null);
      });
    return () => {
      active = false;
    };
  }, [refreshKey]);

  useEffect(() => {
    const refresh = () => setRefreshKey((value) => value + 1);
    window.addEventListener("snapshot:refresh", refresh);
    window.addEventListener("job:refresh", refresh);
    return () => {
      window.removeEventListener("snapshot:refresh", refresh);
      window.removeEventListener("job:refresh", refresh);
    };
  }, []);

  useEffect(() => {
    let active = true;
    const refreshJobs = () => {
      api
        .jobs({ pageSize: 20 })
        .then((response) => {
          if (active) {
            setJobs(response.data.items);
            setOffline(null);
          }
        })
        .catch((reason: unknown) => {
          if (active) {
            setOffline(reason instanceof ApiError ? reason : null);
          }
        });
    };
    const timer = window.setInterval(refreshJobs, 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const activeJob = useMemo(
    () =>
      jobs.find((job) =>
        ["waiting_lock", "queued", "running", "retrying", "validating", "publishing"].includes(job.status),
      ),
    [jobs],
  );

  useEffect(() => {
    if (!activeJob) return;
    let active = true;
    let source: EventSource | null = null;
    const attach = (afterSequence: number) => {
      if (!active) return;
      source = eventSource(activeJob.job_id, afterSequence);
      const update = (message: MessageEvent) => {
        try {
          const event = JSON.parse(message.data) as JobEvent;
          if (!active) return;
          setJobs((current) =>
            current.map((job) =>
              job.job_id === event.job_id ? mergeJobEvent(job, event) : job,
            ),
          );
          if (["job.completed", "job.warning"].includes(event.type)) {
            source?.close();
            setRefreshKey((value) => value + 1);
          }
        } catch {
          // SSE heartbeat 不包含 JSON 数据。
        }
      };
      [
        "job.snapshot",
        "job.stage",
        "job.fund",
        "job.backfill",
        "job.warning",
        "job.retry",
        "job.resume",
        "job.publish",
        "job.completed",
      ].forEach((type) =>
        source?.addEventListener(type, update as EventListener),
      );
    };
    api.jobEventHistory(activeJob.job_id)
      .then((response) => {
        const lastEvent = response.data.items.at(-1);
        if (lastEvent && ["job.completed", "job.warning"].includes(lastEvent.type)) {
          setRefreshKey((value) => value + 1);
          return;
        }
        attach(response.data.last_sequence_no);
      })
      .catch(() => attach(0));
    return () => {
      active = false;
      source?.close();
    };
  }, [activeJob?.job_id]);

  function submitSearch(event: React.FormEvent) {
    event.preventDefault();
    const value = search.trim();
    if (!value) return;
    navigate(`/funds?q=${encodeURIComponent(value)}`);
  }

  const pageName = location.pathname.startsWith("/settings")
    ? "设置"
    : nav.find((item) =>
        item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to),
      )?.label;
  const isTasksPage = location.pathname.startsWith("/tasks");
  const isKnowledgePage = location.pathname.startsWith("/knowledge");

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Database size={20} /></div>
          <div>
            <strong>FundScope</strong>
            <span>基金研究工作台</span>
          </div>
        </div>
        <nav className="primary-nav" aria-label="主导航">
          {nav.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === "/"}>
              <Icon size={17} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="system-state">
            <span className={offline ? "dot dot-error" : "dot"} />
            <span>{offline ? "API 连接异常" : "本地服务正常"}</span>
          </div>
          <small>V1 · Local research workspace</small>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <FileSearch size={16} />
            <span>{pageName ?? "基金详情"}</span>
          </div>
          <form className="global-search" onSubmit={submitSearch}>
            <Search size={16} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索基金名称或 HKEX 基金代码"
              aria-label="全局基金搜索"
            />
          </form>
          <div className="snapshot-chip">
            <Sparkles size={15} />
            <div>
              <small>当前快照</small>
              <strong>{snapshot ? formatDateTime(snapshot.published_at) : "读取中"}</strong>
            </div>
          </div>
          {activeJob ? (
            <NavLink to="/tasks" className="active-task">
              <span>
                任务 {activeJob.task_target === "master_database"
                  ? "综合资料库"
                  : activeJob.current_stage
                  ? activeStageLabels[activeJob.current_stage]
                    ?? activeJob.current_stage
                  : activeJob.status}
              </span>
              <b>{activeJob.progress.processed}/{activeJob.progress.total}</b>
            </NavLink>
          ) : (
            <div className="active-task idle">
              <JobBadge status="completed" />
            </div>
          )}
          <NavLink
            to="/settings"
            className={({ isActive }: { isActive: boolean }) =>
              `user-settings-link ${isActive ? "active" : ""}`
            }
            aria-label="打开设置"
          >
            <UserRound size={17} />
            <span>设置</span>
          </NavLink>
        </header>
        <div className={`content-shell ${isTasksPage ? "task-page-shell" : ""} ${isKnowledgePage ? "knowledge-page-shell" : ""}`}>
          <main className="main-canvas">
            {offline ? (
              <div className="global-alert">
                API 无法连接：{offline.message}
                <button onClick={() => setRefreshKey((value) => value + 1)}>重试</button>
              </div>
            ) : null}
            <Outlet context={{ snapshot, refreshSnapshot: () => setRefreshKey((v) => v + 1) }} />
          </main>
          {isTasksPage || isKnowledgePage ? null : <PerspectivePanel />}
        </div>
      </div>
    </div>
  );
}

export interface ShellOutletContext {
  snapshot: Snapshot | null;
  refreshSnapshot: () => void;
}
