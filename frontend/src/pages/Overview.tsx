import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { ChartPanel } from "../components/ChartPanel";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";
import { targetLabel, useWorkspace } from "../workspace";

const CATEGORY_LABELS: Record<string, string> = {
  suspected_reporting_change: "疑似报告过程变化",
  suspected_epidemic_change: "疑似疫情动态变化",
  data_quality: "数据质量问题",
  insufficient_evidence: "证据不足",
};

export function Overview() {
  const { diseases, diseaseId, regionId, target, disease } = useWorkspace();
  const [data, setData] = useState<any>(null);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [experiments, setExperiments] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const gen = useRef(0);

  useEffect(() => {
    if (!diseaseId) return;
    const current = ++gen.current;
    setLoading(true);
    setError("");
    const tasks: Promise<any>[] = [api.alerts(), api.experiments()];
    if (regionId) tasks.unshift(api.overview(diseaseId, regionId, undefined, target));
    else tasks.unshift(Promise.resolve(null));
    Promise.all(tasks)
      .then(([overview, alertRows, exps]) => {
        if (current !== gen.current) return;
        setData(overview);
        setAlerts((alertRows || []).filter((row: any) => !regionId || row.region_id === regionId).slice(0, 8));
        setExperiments(exps || []);
      })
      .catch((err) => {
        if (current !== gen.current) return;
        setError(String(err.message || err));
      })
      .finally(() => {
        if (current === gen.current) setLoading(false);
      });
  }, [diseaseId, regionId, target]);

  const trendOption = useMemo(() => {
    if (!data?.series?.length) return undefined;
    return {
      tooltip: { trigger: "axis" },
      legend: { data: ["观测"] },
      grid: { left: 52, right: 24, top: 32, bottom: 40 },
      xAxis: { type: "category", data: data.series.map((row: any) => row.period_end) },
      yAxis: { type: "value", name: targetLabel(target) },
      dataZoom: [{ type: "inside" }, { type: "slider" }],
      series: [
        {
          name: "观测",
          type: "line",
          data: data.series.map((row: any) => row.value),
          showSymbol: false,
          lineStyle: { color: "#1f7a78", width: 2 },
        },
      ],
    };
  }, [data, target]);

  if (error) return <EmptyState title="无法加载总览" detail={error} />;
  if (loading && !data) return <Loading />;

  return (
    <div className="space-y-5">
      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {diseases.map((item) => (
          <article key={item.id} className={`card ${item.id === diseaseId ? "ring-2 ring-sea/30" : ""}`}>
            <div className="flex items-start justify-between gap-2">
              <h3 className="font-display text-lg">{item.name_zh || item.name}</h3>
              <StatusBadge status={item.status || "pending"} />
            </div>
            <p className="mt-2 text-sm text-slate-600">{item.status_label || "暂无"}</p>
            <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-600">
              <div>
                目标
                <div className="text-ink">{(item.targets || []).map(targetLabel).join("、") || "暂无"}</div>
              </div>
              <div>
                地区数
                <div className="text-ink">{item.coverage?.n_regions ?? "暂无"}</div>
              </div>
              <div className="col-span-2">
                覆盖时间
                <div className="text-ink">
                  {item.coverage?.start && item.coverage?.end ? `${item.coverage.start} 至 ${item.coverage.end}` : "暂无"}
                </div>
              </div>
            </dl>
          </article>
        ))}
      </section>

      <ChartPanel title={`${disease?.name_zh || "当前疾病"} · ${targetLabel(target)}`} option={trendOption} empty="当前疾病/地区没有可见序列，不填假数字。" />

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card">
          <h3 className="mb-3 font-display text-lg">异常、降级与待评价</h3>
          {!alerts.length ? (
            <EmptyState title="暂无异常" detail="运行实验后显示可解释告警。分类不是已确认的传播机制。" />
          ) : (
            <ul className="space-y-2 text-sm">
              {alerts.map((alert: any) => (
                <li key={alert.id} className="rounded-lg border border-slate-200 px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={alert.review_status} />
                    <span>{CATEGORY_LABELS[alert.category] || alert.category}</span>
                    <span className="text-xs text-slate-500">{alert.period_end}</span>
                  </div>
                  <p className="mt-1 text-slate-600">{alert.supporting_evidence}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="card">
          <h3 className="mb-3 font-display text-lg">最近实验</h3>
          {!experiments.length ? (
            <EmptyState title="暂无实验" />
          ) : (
            <ul className="space-y-2 text-sm">
              {experiments.slice(0, 6).map((item: any) => (
                <li key={item.id} className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2">
                  <span>
                    {item.name}
                    <span className="ml-2 text-xs text-slate-500">{item.disease_id}</span>
                  </span>
                  <StatusBadge status={item.status} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
