import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Filter, Search, X, ListChecks } from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { FundPage } from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { QualityBadge } from "../components/StatusBadge";
import { EmptyState, ErrorState, LoadingState } from "../components/States";
import { useScope } from "../context/ScopeContext";
import {
  applySavedFundFilters,
  clearSavedFundFilters,
  hasExplicitFundFilters,
  readSavedFundFilters,
  saveFundFilters,
} from "../lib/fundFilters";
import { formatNumber } from "../lib/format";

const categories = ["权益", "固收", "混合", "货币", "商品"];
const SESSION_FILTER_KEY = "fundscope:fund-database-secondary-sort:v1";

function readSessionFilterState(): { secondary: string[]; sortBy: string; sortDirection: string } | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_FILTER_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<{ secondary: unknown; sortBy: unknown; sortDirection: unknown }>;
    return {
      secondary: Array.isArray(value.secondary) ? value.secondary.filter((item): item is string => typeof item === "string") : [],
      sortBy: typeof value.sortBy === "string" ? value.sortBy : "",
      sortDirection: typeof value.sortDirection === "string" ? value.sortDirection : "",
    };
  } catch {
    return null;
  }
}

function saveSessionFilterState(state: { secondary: string[]; sortBy: string; sortDirection: string }): void {
  try {
    window.sessionStorage.setItem(SESSION_FILTER_KEY, JSON.stringify(state));
  } catch {
    // 浏览器隐私设置可能禁用 sessionStorage。
  }
}

export function FundsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { setScope } = useScope();
  const [data, setData] = useState<FundPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [filtersReady, setFiltersReady] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const initializedFilters = useRef(false);
  const [draftQuery, setDraftQuery] = useState(params.get("q") ?? "");
  const [draftManager, setDraftManager] = useState(params.get("manager") ?? "");
  const parsedPage = Number(params.get("page") ?? "1");
  const currentPage = Number.isFinite(parsedPage) && parsedPage > 0
    ? Math.floor(parsedPage)
    : 1;
  const query = params.get("q") ?? "";
  const manager = params.get("manager") ?? "";
  const category = params.get("category") ?? "";
  const reportPeriod = params.get("report_period") ?? "";
  const secondary = params.getAll("secondary");
  const sortBy = params.get("sort_by") ?? "";
  const sortDirection = params.get("sort_direction") ?? "";
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (initializedFilters.current) return;
    initializedFilters.current = true;
    const sessionState = readSessionFilterState();
    const hasUrlState = hasExplicitFundFilters(params) || params.has("secondary") || params.has("sort_by");
    if (hasUrlState) {
      if (sessionState && (!params.getAll("secondary").length || !params.get("sort_by"))) {
        const restored = new URLSearchParams(params);
        if (!restored.getAll("secondary").length) sessionState.secondary.forEach((item) => restored.append("secondary", item));
        if (!restored.get("sort_by") && sessionState.sortBy) restored.set("sort_by", sessionState.sortBy);
        if (!restored.get("sort_direction") && sessionState.sortDirection) restored.set("sort_direction", sessionState.sortDirection);
        setParams(restored, { replace: true });
      }
      setFiltersReady(true);
      return;
    }
    const saved = readSavedFundFilters(window.localStorage);
    let next = saved ? applySavedFundFilters(params, saved) : new URLSearchParams(params);
    if (sessionState) {
      sessionState.secondary.forEach((item) => next.append("secondary", item));
      if (sessionState.sortBy) next.set("sort_by", sessionState.sortBy);
      if (sessionState.sortDirection) next.set("sort_direction", sessionState.sortDirection);
    }
    if (saved) {
      setDraftQuery(saved.query);
      setDraftManager(saved.manager);
    }
    if (saved || sessionState) setParams(next, { replace: true });
    setFiltersReady(true);
  }, [params, setParams]);

  useEffect(() => {
    const handler = (event: PointerEvent) => {
      const target = event.target as Node | null;
      document.querySelectorAll<HTMLDetailsElement>(".filter-popover[open]").forEach((popover) => {
        if (target && !popover.contains(target)) popover.open = false;
      });
    };
    document.addEventListener("pointerdown", handler);
    return () => document.removeEventListener("pointerdown", handler);
  }, []);

  useEffect(() => {
    if (!filtersReady) return;
    saveFundFilters(window.localStorage, {
      query,
      manager,
      category,
      reportPeriod,
    });
  }, [category, filtersReady, manager, query, reportPeriod]);

  useEffect(() => {
    if (!filtersReady) return;
    saveSessionFilterState({ secondary, sortBy, sortDirection });
  }, [filtersReady, secondary.join("|"), sortBy, sortDirection]);

  function updateFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.set("page", "1");
    setParams(next);
  }

  function updatePage(page: number) {
    const next = new URLSearchParams(params);
    next.set("page", String(Math.max(1, page)));
    setParams(next);
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (draftQuery !== query) updateFilter("q", draftQuery.trim());
    }, 350);
    return () => window.clearTimeout(timer);
  }, [draftQuery]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (draftManager !== manager) updateFilter("manager", draftManager.trim());
    }, 350);
    return () => window.clearTimeout(timer);
  }, [draftManager]);

  useEffect(() => {
    setDraftManager(manager);
  }, [manager]);

  useEffect(() => {
    setDraftQuery(query);
  }, [query]);

  useEffect(() => {
    if (!filtersReady) return;
    let active = true;
    setLoading(true);
    api
      .funds({
        q: query,
        manager,
        category,
        reportPeriod,
        secondary,
        sortBy,
        sortDirection,
        page: currentPage,
        pageSize: 20,
      })
      .then((response) => {
        if (!active) return;
        setData(response.data);
        setError(null);
        const hasNarrowing = Boolean(query || manager || reportPeriod || secondary.length || category);
        const matchingCodes = response.data.matching_fund_codes ?? response.data.items.map((item) => item.fund_code);
        setScope({
          fundCodes: selectedCodes.size ? [...selectedCodes] : matchingCodes,
          category: category || undefined,
          label: selectedCodes.size ? `已选集合 · ${selectedCodes.size} 只` : hasNarrowing ? "当前筛选结果" : "全部基金",
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
  }, [
    category,
    currentPage,
    filtersReady,
    manager,
    query,
    reportPeriod,
    secondary.join("|"),
    sortBy,
    sortDirection,
    selectedCodes,
    setScope,
  ]);

  function resetFilters() {
    setDraftQuery("");
    setDraftManager("");
    clearSavedFundFilters(window.localStorage);
    try { window.sessionStorage.removeItem(SESSION_FILTER_KEY); } catch { /* ignore */ }
    setSelectedCodes(new Set());
    setParams(new URLSearchParams({ page: "1" }));
  }

  function toggleSecondary(key: string, value: string) {
    const token = `${key}:${value}`;
    const next = new URLSearchParams(params);
    const current = next.getAll("secondary");
    const values = current.includes(token)
      ? current.filter((item) => item !== token)
      : [...current.filter((item) => !item.startsWith(`${key}:`)), token];
    next.delete("secondary");
    values.forEach((item) => next.append("secondary", item));
    next.set("page", "1");
    saveSessionFilterState({ secondary: values, sortBy, sortDirection });
    setParams(next);
  }

  function setSort(direction: string) {
    const next = new URLSearchParams(params);
    if (sortBy === "latest_scale" && sortDirection === direction) {
      next.delete("sort_by"); next.delete("sort_direction");
    } else {
      next.set("sort_by", "latest_scale"); next.set("sort_direction", direction);
    }
    next.set("page", "1");
    saveSessionFilterState({ secondary, sortBy: next.get("sort_by") ?? "", sortDirection: next.get("sort_direction") ?? "" });
    setParams(next);
  }

  function toggleFund(code: string) {
    setSelectedCodes((previous) => {
      const next = new Set(previous);
      if (next.has(code)) next.delete(code); else next.add(code);
      return next;
    });
  }

  async function createCollection() {
    if (!selectedCodes.size) return;
    const sessionKey = window.sessionStorage.getItem("fund-collection-session") ?? crypto.randomUUID();
    window.sessionStorage.setItem("fund-collection-session", sessionKey);
    const response = await api.createFundCollection([...selectedCodes], sessionKey);
    window.sessionStorage.setItem("fund-collection-count", String(response.data.count));
    navigate(`/tasks?collection_id=${encodeURIComponent(response.data.collection_id)}`);
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <>
      <PageHeader
        eyebrow="Fund universe"
        title="基金数据库"
        description="以 HKEX 基金代码为唯一身份，筛选当前研究范围并进入结构化详情。"
        actions={<span className="result-count"><b>{formatNumber(data?.total)}</b> 只基金{selectedCodes.size ? ` · 已选集合 ${selectedCodes.size} 只` : ""}</span>}
      />
      <section className="filter-bar">
        <label className="search-field">
          <Search size={16} />
          <input
            value={draftQuery}
            onChange={(event) => setDraftQuery(event.target.value)}
            placeholder="名称、英文名或基金代码"
          />
          {draftQuery ? (
            <button type="button" onClick={() => setDraftQuery("")} aria-label="清空搜索">
              <X size={14} />
            </button>
          ) : null}
        </label>
        <label>
          <span>管理人</span>
          <input
            value={draftManager}
            onChange={(event) => setDraftManager(event.target.value)}
            placeholder="如 摩根大通"
          />
        </label>
        <label>
          <span>资产类别</span>
          <select value={category} onChange={(event) => updateFilter("category", event.target.value)}>
            <option value="">全部类别</option>
            {categories.map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
        <details className="filter-popover">
          <summary>二级类别</summary>
          <div className="filter-popover-panel">
            {(data?.secondary_options ?? []).map((dimension) => (
              <div className="filter-option-row" key={dimension.key}>
                <span>{dimension.label}</span>
                {dimension.options.map((value) => {
                  const token = `${dimension.key}:${value}`;
                  return <label key={token}><input type="checkbox" checked={secondary.includes(token)} onChange={() => toggleSecondary(dimension.key, value)} /> {value}</label>;
                })}
              </div>
            ))}
          </div>
        </details>
        <label>
          <span>报告期</span>
          <input
            type="month"
            value={reportPeriod ? reportPeriod.slice(0, 7) : ""}
            onChange={(event) =>
              updateFilter("report_period", event.target.value ? `${event.target.value}-01` : "")
            }
          />
        </label>
        <details className="filter-popover">
          <summary>排序</summary>
          <div className="filter-popover-panel">
            <div className="filter-option-row"><span>最新规模</span><label><input type="checkbox" checked={sortBy === "latest_scale" && sortDirection === "desc"} onChange={() => setSort("desc")} /> 降序</label><label><input type="checkbox" checked={sortBy === "latest_scale" && sortDirection === "asc"} onChange={() => setSort("asc")} /> 升序</label></div>
          </div>
        </details>
        <button className="button ghost" onClick={resetFilters}>
          <Filter size={15} /> 清空
        </button>
      </section>

      {loading ? <LoadingState label="正在加载基金列表…" /> : null}
      {error ? <ErrorState message={error.message} requestId={error.requestId} /> : null}
      {!loading && !error && !data?.items.length ? <EmptyState /> : null}
      {!loading && !error && data?.items.length ? (
        <section className="data-panel">
          <div className="table-wrap fund-database-table-wrap">
            <table className="fund-database-table">
              <colgroup>
                <col className="fund-database-select-col" />
                <col className="fund-database-name-col" />
                <col className="fund-database-manager-col" />
                <col className="fund-database-category-col" />
                <col className="fund-database-period-col" />
                <col className="fund-database-quality-col" />
              </colgroup>
              <thead>
                <tr>
                  <th aria-label="选择" />
                  <th>基金</th>
                  <th>管理人 / 伞</th>
                  <th>类别</th>
                  <th>报告期</th>
                  <th>质量</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((fund) => (
                  <tr key={fund.fund_id}>
                    <td className="fund-database-select-cell"><input type="checkbox" checked={selectedCodes.has(fund.fund_code)} onChange={() => toggleFund(fund.fund_code)} aria-label={`选择 ${fund.fund_code}`} /></td>
                    <td className="fund-database-name-cell">
                      <Link className="fund-name-link" to={`/funds/${fund.fund_code}`}>
                        <span
                          className={`fund-name-marquee${fund.name_cn.length > 18 ? " is-long" : ""}`}
                          title={fund.name_cn}
                        >
                          <strong>{fund.name_cn}</strong>
                        </span>
                        <span>{fund.name_en || fund.fund_code}</span>
                      </Link>
                    </td>
                    <td className="fund-database-manager-cell">
                      <strong className="table-secondary">{fund.manager_name}</strong>
                      <span className="table-tertiary">{fund.umbrella_name || "无伞信息"}</span>
                    </td>
                    <td className="fund-database-category-cell">
                      <div className="fund-category-values">
                        <span className="category-token">{fund.asset_category || "未分类"}</span>
                        {fund.asset_subcategory ? <span className="category-token category-token-secondary">{fund.asset_subcategory}</span> : null}
                      </div>
                    </td>
                    <td className="mono fund-database-period-cell">{fund.report_period ?? "—"}</td>
                    <td className="fund-database-quality-cell"><QualityBadge status={fund.quality_status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <footer className="pagination">
            <span>第 {currentPage} / {pages} 页</span>
            {selectedCodes.size ? <button className="button primary" onClick={createCollection}><ListChecks size={15} /> 基于集合创建任务</button> : null}
            <div>
              <button
                className="icon-button"
                disabled={currentPage <= 1}
                onClick={() => updatePage(currentPage - 1)}
              >
                <ChevronLeft size={16} />
              </button>
              <button
                className="icon-button"
                disabled={currentPage >= pages}
                onClick={() => updatePage(currentPage + 1)}
              >
                <ChevronRight size={16} />
              </button>
            </div>
          </footer>
        </section>
      ) : null}
    </>
  );
}
