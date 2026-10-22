import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { ChartPanel } from "../components/ChartPanel";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";

const MODEL_COLORS: Record<string, string> = {
  persistence: "#6b7280",
  damped_trend: "#1f6f78",
  retrain_glm: "#2f5d8c",
  fixed_glm: "#8a6d3b",
  equal_weight: "#3f7d5a",
  dynamic_weight: "#9a4a3a",
  adaptive: "#102a33",
  analog: "#7c3aed",
  pooled_glm: "#0f766e",
  hierarchical: "#1d4ed8",
  hierarchical_dynamic: "#9f1239",
};

export function ForecastCompare() {
  const [experiments, setExperiments] = useState<any[]>([]);
  const [experimentId, setExperimentId] = useState("");
  const [regionId, setRegionId] = useState("");
  const [regions, setRegions] = useState<any[]>([]);
  const [forecasts, setForecasts] = useState<any[]>([]);
  const [evaluations, setEvaluations] = useState<any[]>([]);
  const [summary, setSummary] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.experiments(), api.regions()])
      .then(([exps, regs]) => {
        setExperiments(exps);
        setRegions(regs);
        const preferred = exps.find((item: any) => item.status === "completed") || exps[0];
        setExperimentId(preferred?.id || "");
        setRegionId(preferred?.region_ids?.[0] || regs[0]?.id || "");
      })
      .catch((err) => setError(String(err.message || err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!experimentId) return;
    setLoading(true);
    Promise.all([api.forecasts(experimentId, regionId || undefined), api.evaluations(experimentId), api.experiment(experimentId)])
      .then(([f, e, exp]) => {
        setForecasts(f);
        setEvaluations(e.filter((row: any) => !regionId || row.region_id === regionId));
        setSummary(exp.summary);
      })
      .catch((err) => setError(String(err.message || err)))
      .finally(() => setLoading(false));
  }, [experimentId, regionId]);

  const curveOption = useMemo(() => {
    const rows = forecasts.filter((row) => row.horizon === 1);
    if (!rows.length) return undefined;
    const models = Array.from(new Set(rows.map((row) => row.model_id)));
    const dates = Array.from(new Set(rows.map((row) => row.target_period_end))).sort();
    const actualByDate = new Map<string, number | null>();
    for (const row of evaluations) {
      if (row.horizon !== 1 || row.status !== "scored") continue;
      actualByDate.set(row.target_period_end, row.actual_value);
    }
    return {
      tooltip: { trigger: "axis" },
      legend: { data: [...models, "成熟实际值"] },
      grid: { left: 48, right: 20, top: 40, bottom: 40 },
      xAxis: { type: "category", data: dates },
      yAxis: { type: "value", name: "1 周预测 / 成熟结果" },
      series: [
        ...models.map((model) => ({
          name: model,
          type: "line",
          itemStyle: { color: MODEL_COLORS[model] || "#333" },
          data: dates.map((date) => rows.find((row) => row.model_id === model && row.target_period_end === date)?.median ?? null),
        })),
        {
          name: "成熟实际值",
          type: "scatter",
          symbolSize: 8,
          itemStyle: { color: "#b45309" },
          data: dates.map((date) => actualByDate.get(date) ?? null),
        },
      ],
    };
  }, [forecasts, evaluations]);

  const fanOption = useMemo(() => {
    if (!forecasts.length) return undefined;
    const cutoffs = [...forecasts].map((row) => row.cutoff_time).sort();
    const latestCutoff = cutoffs.length ? cutoffs[cutoffs.length - 1] : null;
    const latest = forecasts.filter((row) => row.cutoff_time === latestCutoff && row.model_id === "adaptive");
    if (!latest.length) return undefined;
    const ordered = [...latest].sort((a, b) => a.horizon - b.horizon);
    return {
      tooltip: { trigger: "axis" },
      legend: { data: ["中位数", "50% 下界", "50% 上界", "80% 下界", "80% 上界", "95% 下界", "95% 上界"] },
      grid: { left: 48, right: 20, top: 48, bottom: 40 },
      xAxis: { type: "category", data: ordered.map((row) => `${row.horizon}周`) },
      yAxis: { type: "value", name: "预测范围" },
      series: [
        { name: "中位数", type: "line", data: ordered.map((row) => row.median), itemStyle: { color: "#102a33" } },
        { name: "50% 下界", type: "line", data: ordered.map((row) => row.interval_50?.[0]), lineStyle: { type: "dashed", color: "#1f6f78" }, symbol: "none" },
        { name: "50% 上界", type: "line", data: ordered.map((row) => row.interval_50?.[1]), lineStyle: { type: "dashed", color: "#1f6f78" }, symbol: "none" },
        { name: "80% 下界", type: "line", data: ordered.map((row) => row.interval_80?.[0]), lineStyle: { type: "dotted", color: "#6b7280" }, symbol: "none" },
        { name: "80% 上界", type: "line", data: ordered.map((row) => row.interval_80?.[1]), lineStyle: { type: "dotted", color: "#6b7280" }, symbol: "none" },
        { name: "95% 下界", type: "line", data: ordered.map((row) => row.interval_95?.[0]), lineStyle: { type: "dotted", color: "#9ca3af" }, symbol: "none" },
        { name: "95% 上界", type: "line", data: ordered.map((row) => row.interval_95?.[1]), lineStyle: { type: "dotted", color: "#9ca3af" }, symbol: "none" },
      ],
    };
  }, [forecasts]);

  const weights = useMemo(() => {
    const cutoffs = [...forecasts].map((row) => row.cutoff_time).sort();
    const latestCutoff = cutoffs.length ? cutoffs[cutoffs.length - 1] : null;
    const row = forecasts.find(
      (item) => item.cutoff_time === latestCutoff && (item.model_id === "dynamic_weight" || item.model_id === "adaptive"),
    );
    return row?.weights || {};
  }, [forecasts]);

  const analogNote = useMemo(() => {
    const cutoffs = [...forecasts].map((row) => row.cutoff_time).sort();
    const latestCutoff = cutoffs.length ? cutoffs[cutoffs.length - 1] : null;
    const row = forecasts.find((item) => item.cutoff_time === latestCutoff && item.model_id === "analog");
    const extra = row?.extra || {};
    if (!extra.analog_id && !row?.quality_note) return null;
    return extra;
  }, [forecasts]);

  const modelSummary = summary?.models ? Object.entries(summary.models) : [];
  const pendingCount = evaluations.filter((row) => row.status === "pending").length;

  if (loading && !forecasts.length && experiments.length === 0) return <Loading />;
  if (error) return <EmptyState title="预测比较加载失败" detail={error} />;
  if (!experiments.length) return <EmptyState title="尚无实验" detail="请先在实验工作台创建并运行历史回放。" />;

  return (
    <div className="space-y-5">
      <section className="grid gap-4 rounded-2xl border border-slate-200/80 bg-white/80 p-4 md:grid-cols-2">
        <label className="text-sm">
          <div className="mb-1 text-slate-500">实验</div>
          <select className="w-full rounded-lg border border-slate-300 px-3 py-2" value={experimentId} onChange={(e) => setExperimentId(e.target.value)}>
            {experiments.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.status}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-slate-500">地区</div>
          <select className="w-full rounded-lg border border-slate-300 px-3 py-2" value={regionId} onChange={(e) => setRegionId(e.target.value)}>
            {regions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      </section>

      <ChartPanel title="历史 1 周预测中位数 vs 成熟实际值" option={curveOption} empty="尚无时长=1 的预测" />
      <ChartPanel title="最新截止时间的适应系统预测范围" option={fanOption} empty="尚无适应系统预测区间" />

      {analogNote ? (
        <section className="rounded-2xl border border-violet-200 bg-violet-50/70 p-4 text-sm">
          <h3 className="font-display text-lg">历史形态类比</h3>
          <p className="mt-2 text-slate-700">
            对齐 {analogNote.analog_id || "未知形态"} · 阶段 {analogNote.phase || "—"} · 方向 {analogNote.direction || "—"} ·
            综合相似度 {analogNote.similarity != null ? Number(analogNote.similarity).toFixed(2) : "—"}
          </p>
          <p className="mt-2 text-xs text-slate-500">
            形态类比使用归一化历史曲线或对照地区，不是真实病例数训练，也不默认优于等权或基线。
          </p>
        </section>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
          <h3 className="font-display text-lg">模型权重</h3>
          <p className="mt-1 text-xs text-slate-500">动态权重只使用已成熟预测；未成熟结果不更新权重。动态组合不默认优于等权。</p>
          {!Object.keys(weights).length ? (
            <EmptyState title="暂无权重记录" detail="等权或尚未产生可评价历史时会出现此状态。" />
          ) : (
            <ul className="mt-3 space-y-2 text-sm">
              {Object.entries(weights).map(([modelId, weight]) => (
                <li key={modelId} className="flex justify-between rounded-lg border border-slate-100 px-3 py-2">
                  <span>{modelId}</span>
                  <span>{(Number(weight) * 100).toFixed(1)}%</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
          <h3 className="font-display text-lg">模型评分摘要</h3>
          <p className="mt-1 text-sm text-slate-600">WIS 越低越好。待评价 {pendingCount} 条。</p>
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead>
                <tr className="border-b text-slate-500">
                  <th className="py-2 pr-3">模型</th>
                  <th className="py-2 pr-3">n</th>
                  <th className="py-2 pr-3">MAE</th>
                  <th className="py-2 pr-3">WIS</th>
                  <th className="py-2 pr-3">80% 覆盖</th>
                </tr>
              </thead>
              <tbody>
                {modelSummary.map(([modelId, row]: any) => (
                  <tr key={modelId} className="border-b border-slate-100">
                    <td className="py-2 pr-3">{modelId}</td>
                    <td className="py-2 pr-3">{row.n}</td>
                    <td className="py-2 pr-3">{row.mae?.toFixed?.(2)}</td>
                    <td className="py-2 pr-3">{row.wis?.toFixed?.(2)}</td>
                    <td className="py-2 pr-3">{((row.coverage_80 || 0) * 100).toFixed(0)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h3 className="font-display text-lg">地区可靠性（成熟评价）</h3>
        <p className="mt-1 text-xs text-slate-500">WIS 越低越好。没有足够评分时不展示准确率。分层不默认优于统一或独立模型。</p>
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr className="border-b text-slate-500">
                <th className="py-2 pr-3">模型</th>
                <th className="py-2 pr-3">地区</th>
                <th className="py-2 pr-3">n</th>
                <th className="py-2 pr-3">MAE</th>
                <th className="py-2 pr-3">WIS</th>
                <th className="py-2 pr-3">80% 覆盖</th>
              </tr>
            </thead>
            <tbody>
              {modelSummary.flatMap(([modelId, row]: any) =>
                Object.entries(row.by_region || {}).map(([rid, stats]: any) => (
                  <tr key={`${modelId}-${rid}`} className="border-b border-slate-100">
                    <td className="py-2 pr-3">{modelId}</td>
                    <td className="py-2 pr-3">{rid}</td>
                    <td className="py-2 pr-3">{stats.n}</td>
                    <td className="py-2 pr-3">{stats.mae?.toFixed?.(2)}</td>
                    <td className="py-2 pr-3">{stats.wis?.toFixed?.(2)}</td>
                    <td className="py-2 pr-3">{((stats.coverage_80 || 0) * 100).toFixed(0)}%</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h3 className="font-display text-lg">动态调整记录</h3>
        <p className="mt-1 text-xs text-slate-500">按周回放原点记录。回退表示该原点分层拟合未成功，不使用未成熟结果更新权重。</p>
        {!(summary?.update_log || []).length ? (
          <EmptyState title="尚无更新记录" detail="运行包含分层或动态分层臂的回放后会出现。" />
        ) : (
          <div className="mt-3 max-h-72 overflow-auto">
            <table className="min-w-full text-left text-sm">
              <thead>
                <tr className="border-b text-slate-500">
                  <th className="py-2 pr-3">时间</th>
                  <th className="py-2 pr-3">模型</th>
                  <th className="py-2 pr-3">动作</th>
                  <th className="py-2 pr-3">触发</th>
                  <th className="py-2 pr-3">回退</th>
                </tr>
              </thead>
              <tbody>
                {(summary.update_log || []).slice(-40).map((row: any, index: number) => (
                  <tr key={`${row.cutoff}-${row.model_id}-${index}`} className="border-b border-slate-100">
                    <td className="py-2 pr-3">{row.cutoff}</td>
                    <td className="py-2 pr-3">{row.model_id}</td>
                    <td className="py-2 pr-3">{row.action}</td>
                    <td className="py-2 pr-3">{row.trigger}</td>
                    <td className="py-2 pr-3">{row.rollback ? "是" : "否"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h3 className="font-display text-lg">历史预测与成熟结果</h3>
        <div className="mt-3 max-h-96 overflow-auto">
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr className="border-b text-slate-500">
                <th className="py-2 pr-3">模型</th>
                <th className="py-2 pr-3">目标周</th>
                <th className="py-2 pr-3">时长</th>
                <th className="py-2 pr-3">状态</th>
                <th className="py-2 pr-3">实际</th>
                <th className="py-2 pr-3">WIS</th>
                <th className="py-2 pr-3">阶段</th>
              </tr>
            </thead>
            <tbody>
              {evaluations.slice(0, 200).map((row) => (
                <tr key={row.id} className="border-b border-slate-100">
                  <td className="py-2 pr-3">{row.model_id}</td>
                  <td className="py-2 pr-3">{row.target_period_end}</td>
                  <td className="py-2 pr-3">{row.horizon}</td>
                  <td className="py-2 pr-3">
                    <StatusBadge status={row.status === "pending" ? "pending" : "scored"} />
                  </td>
                  <td className="py-2 pr-3">{row.actual_value ?? "—"}</td>
                  <td className="py-2 pr-3">{row.wis?.toFixed?.(2) ?? "—"}</td>
                  <td className="py-2 pr-3">{row.phase}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
