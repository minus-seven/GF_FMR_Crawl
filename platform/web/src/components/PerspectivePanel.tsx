import { useEffect, useState, type ComponentType } from "react";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import { ExternalLink, Info, RefreshCw } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { PerspectiveData } from "../api/types";
import { useScope } from "../context/ScopeContext";
import { ErrorState, LoadingState } from "./States";

const palette = ["#cb6b3c", "#1d746c", "#d0a54a", "#3f657a", "#93a992"];

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer]);

// echarts-for-react 3.x still declares the legacy React class shape. Cast it
// at this integration boundary so React 19's JSX types do not reject the
// runtime component; the rendered behavior and props stay unchanged.
const EChartsView = ReactEChartsCore as unknown as ComponentType<Record<string, unknown>>;

function histogramOption(view: PerspectiveData["views"][number]) {
  return {
    animationDuration: 450,
    grid: { left: 6, right: 8, top: 14, bottom: 4, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: {
      type: "category",
      data: view.dataset.map((item) => String(item.label ?? "")),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "#d7d2c7" } },
      axisLabel: { color: "#6e716c", fontSize: 10, interval: 0, rotate: 32 },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "#ebe7de" } },
      axisLabel: { color: "#8b8d87", fontSize: 10 },
    },
    series: [
      {
        type: "bar",
        data: view.dataset.map((item) => Number(item.value ?? 0)),
        itemStyle: {
          color: "#1d746c",
          borderRadius: [4, 4, 0, 0],
        },
        barMaxWidth: 22,
      },
    ],
  };
}

function trendOption(view: PerspectiveData["views"][number]) {
  return {
    animationDuration: 500,
    color: palette,
    grid: { left: 6, right: 8, top: 16, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value: number) => `${Number(value).toFixed(2)} 亿港币`,
    },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: view.dataset.map((item) => String(item.month_end ?? "")),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "#d7d2c7" } },
      axisLabel: {
        color: "#6e716c",
        fontSize: 10,
        formatter: (value: string) => value.slice(2, 7),
      },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "#ebe7de" } },
      axisLabel: { color: "#8b8d87", fontSize: 10 },
    },
    series: [
      {
        type: "line",
        smooth: 0.28,
        symbol: "circle",
        symbolSize: 4,
        data: view.dataset.map((item) => Number(item.value ?? 0)),
        lineStyle: { width: 2.5, color: "#cb6b3c" },
        itemStyle: { color: "#cb6b3c" },
        areaStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(203,107,60,.28)" },
              { offset: 1, color: "rgba(203,107,60,.01)" },
            ],
          },
        },
      },
    ],
  };
}

function pieOption(view: PerspectiveData["views"][number]) {
  return {
    animationDuration: 450,
    color: palette,
    tooltip: {
      trigger: "item",
      formatter: (params: { name: string; value: number; percent: number }) =>
        `${params.name}<br/>${Number(params.value).toFixed(2)}% (${Number(params.percent).toFixed(2)}%)`,
    },
    legend: {
      orient: "vertical",
      left: "57%",
      top: 12,
      bottom: 12,
      width: "39%",
      itemWidth: 8,
      itemHeight: 8,
      itemGap: 9,
      padding: [9, 8],
      backgroundColor: "rgba(247,246,241,.86)",
      borderColor: "#e4dfd4",
      borderWidth: 1,
      borderRadius: 6,
      formatter: (name: string) => (name.length > 13 ? `${name.slice(0, 12)}…` : name),
      textStyle: { color: "#6e716c", fontSize: 9 },
    },
    series: [
      {
        type: "pie",
        radius: ["35%", "60%"],
        center: ["29%", "50%"],
        avoidLabelOverlap: true,
        label: { show: false },
        data: view.dataset.map((item) => ({
          name: String(item.label ?? ""),
          value: Number(item.value ?? 0),
        })),
      },
    ],
  };
}

export function PerspectivePanel() {
  const { scope, setScope } = useScope();
  const [data, setData] = useState<PerspectiveData | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [tableauOpen, setTableauOpen] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .perspectives({
        fundCodes: scope.fundCodes,
        managerId: scope.managerId,
        category: scope.category,
        sourceDocumentVersionId: scope.sourceDocumentVersionId,
      })
      .then((response) => {
        if (active) {
          setData(response.data);
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof ApiError ? reason : new ApiError(String(reason), { code: "E_NETWORK", requestId: "", retryable: true, status: 0 }));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [scope.category, scope.fundCodes, scope.managerId, scope.sourceDocumentVersionId, tick]);

  return (
    <aside className="insights-panel">
      <div className="insights-heading">
        <div>
          <span className="eyebrow">ECharts 数据透视</span>
          <h2>{scope.label}</h2>
        </div>
        <button
          className="icon-button"
          title="刷新图表"
          onClick={() => setTick((value) => value + 1)}
        >
          <RefreshCw size={16} />
        </button>
      </div>
      {loading ? <LoadingState label="正在绘制图表…" /> : null}
      {error ? <ErrorState message={error.message} onRetry={() => setTick((v) => v + 1)} /> : null}
      {!loading && !error && data ? (
        <div className="chart-stack">
          {data.views
            .filter((view) => view.view_id !== "portfolio_structure" || view.dataset.length > 0)
            .map((view) => (
            <section className={`chart-card ${view.view_id === "portfolio_structure" ? "portfolio-structure-card" : ""}`} key={view.view_id}>
              <div className="chart-title">
                <div>
                  <h3>{view.title}</h3>
                  <span>
                    {view.source_period
                      ? `来源报告期 ${view.source_period}`
                      : view.unit === "HKD_100M" ? "亿港币" : view.unit}
                  </span>
                </div>
                <span className={`mini-status quality-${view.quality}`}>{view.quality === "complete" ? "完整" : "待复核"}</span>
              </div>
              <EChartsView
                echarts={echarts}
                option={
                  view.view_id === "fund_scale_histogram"
                    ? histogramOption(view)
                    : view.view_id === "portfolio_structure"
                      ? pieOption(view)
                      : trendOption(view)
                }
                style={{ height: view.view_id === "portfolio_structure" ? 232 : 172, width: "100%" }}
                opts={{ renderer: "svg" }}
              />
            </section>
          ))}
          {!data.views.length ? (
            <div className="panel-empty">当前范围暂无规模数据。</div>
          ) : null}
        </div>
      ) : null}
      <div className="tableau-card">
        <div className="tableau-title">
          <span>
            <strong>Tableau Desktop</strong>
            <small>深度分析与工作簿交付</small>
          </span>
          <button className="icon-button" onClick={() => setTableauOpen((value) => !value)}>
            <Info size={16} />
          </button>
        </div>
        {tableauOpen ? (
          <dl className="connection-meta">
            <div><dt>Host</dt><dd>127.0.0.1</dd></div>
            <div><dt>Port</dt><dd>55432</dd></div>
            <div><dt>Database</dt><dd>fund_research</dd></div>
            <div><dt>Schema</dt><dd>analytics_readonly</dd></div>
            <div><dt>User</dt><dd>fund_research_ro</dd></div>
          </dl>
        ) : null}
        <button className="tableau-link" onClick={() => setTableauOpen(true)}>
          <ExternalLink size={14} />
          查看只读视图连接信息
        </button>
      </div>
      <button className="scope-reset" onClick={() => setScope({ fundCodes: [], label: "全部基金" })}>
        恢复全市场范围
      </button>
    </aside>
  );
}
