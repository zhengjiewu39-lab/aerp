import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { ChartPanel } from "../components/ChartPanel";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";

const PHASE_LABELS: Record<string, string> = {
  early_growth: "早期上升",
  acceleration: "加速上升",
  peak: "峰值附近",
  decline: "回落",
  trough: "低谷",
  second_wave: "第二波迹象",
  stable_seasonal: "季节性平稳",
};

const DIRECTION_LABELS: Record<string, string> = {
  growth: "继续上升",
  decline: "转为回落",
  peak: "高位徘徊",
  stable: "相对平稳",
};

export function HistoryOutbreaks() {
  const [cards, setCards] = useState<any[]>([]);
  const [analogs, setAnalogs] = useState<any[]>([]);
  const [disclaimer, setDisclaimer] = useState("");
  const [transfer, setTransfer] = useState<any>(null);
  const [diseases, setDiseases] = useState<any[]>([]);
  const [regions, setRegions] = useState<any[]>([]);
  const [diseaseId, setDiseaseId] = useState("");
  const [regionId, setRegionId] = useState("");
  const [match, setMatch] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.history(), api.analogs(), api.meta(), api.diseases(), api.regions()])
      .then(([history, analogPayload, meta, diseaseRows, regionRows]) => {
        setCards(history);
        setAnalogs(analogPayload.items || []);
        setDisclaimer(analogPayload.disclaimer || "");
        setTransfer(meta.transfer);
        setDiseases(diseaseRows);
        setRegions(regionRows);
        setDiseaseId(diseaseRows[0]?.id || "");
        setRegionId(regionRows[0]?.id || "");
      })
      .catch((err) => setError(String(err.message || err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!diseaseId || !regionId) return;
    api
      .analogMatch({ disease_id: diseaseId, region_id: regionId })
      .then(setMatch)
      .catch((err) => setError(String(err.message || err)));
  }, [diseaseId, regionId]);

  const overlayOption = useMemo(() => {
    const query = match?.query_shape || [];
    const aligned = match?.top?.aligned_shape || [];
    if (!query.length) return undefined;
    const axis = query.map((_: number, index: number) => `t-${query.length - index}`);
    return {
      tooltip: { trigger: "axis" },
      legend: { data: ["当前序列（归一化）", match?.top?.name || "最佳形态"] },
      grid: { left: 40, right: 20, top: 40, bottom: 36 },
      xAxis: { type: "category", data: axis },
      yAxis: { type: "value", min: 0, max: 1, name: "相对强度" },
      series: [
        { name: "当前序列（归一化）", type: "line", data: query, showSymbol: false, itemStyle: { color: "#0f6a6a" } },
        {
          name: match?.top?.name || "最佳形态",
          type: "line",
          data: aligned.length === query.length ? aligned : [],
          showSymbol: false,
          itemStyle: { color: "#7c3aed" },
        },
      ],
    };
  }, [match]);

  const analogCharts = useMemo(
    () =>
      analogs.map((item) => ({
        id: item.id,
        title: item.name,
        option: {
          tooltip: { trigger: "axis" },
          grid: { left: 36, right: 12, top: 16, bottom: 24 },
          xAxis: { type: "category", data: (item.shape || []).map((_: number, index: number) => index + 1), show: false },
          yAxis: { type: "value", min: 0, max: 1, show: false },
          series: [{ type: "line", data: item.shape || [], showSymbol: false, itemStyle: { color: "#1f6f78" }, areaStyle: { color: "rgba(31,111,120,0.12)" } }],
        },
      })),
    [analogs],
  );

  if (loading) return <Loading />;
  if (error) return <EmptyState title="历史资料加载失败" detail={error} />;

  const selectedRegion = regions.find((item) => item.id === regionId);
  const top = match?.top;

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h2 className="font-display text-xl">历史形态如何进入短期预测</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">{disclaimer}</p>
        <p className="mt-2 text-sm text-slate-600">
          {transfer?.message || "真实跨疾病病例序列迁移尚未实现。"} 等权、持续值等不迁移基线始终保留。
        </p>
        <div className="mt-3">
          <StatusBadge status="available" />
        </div>
      </section>

      <section className="grid gap-4 rounded-2xl border border-slate-200/80 bg-white/80 p-4 md:grid-cols-2">
        <label className="text-sm">
          <div className="mb-1 text-slate-500">当前疾病</div>
          <select className="w-full rounded-lg border border-slate-300 px-3 py-2" value={diseaseId} onChange={(e) => setDiseaseId(e.target.value)}>
            {diseases.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-slate-500">当前地区</div>
          <select className="w-full rounded-lg border border-slate-300 px-3 py-2" value={regionId} onChange={(e) => setRegionId(e.target.value)}>
            {regions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartPanel title="当前序列 vs 最佳历史形态" option={overlayOption} empty="可见周数不足，无法做形态对齐" />
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
          <h3 className="font-display text-lg">动态调整建议</h3>
          {top ? (
            <div className="mt-3 space-y-2 text-sm text-slate-700">
              <p>
                最佳对照：{top.name}（{top.source === "peer_region" ? "对照地区" : "历史形态"}）
              </p>
              <p>
                阶段 {PHASE_LABELS[top.phase] || top.phase} · 方向 {DIRECTION_LABELS[top.direction] || top.direction}
              </p>
              <p>
                形状分 {Number(top.shape_score).toFixed(2)} · 上下文分 {Number(top.context_score).toFixed(2)} · 综合{" "}
                {Number(top.combined_score).toFixed(2)}
              </p>
              <p className="text-xs text-slate-500">{top.comparison_limit}</p>
            </div>
          ) : (
            <EmptyState title="没有可用形态" detail={match?.message || "先选择有住院序列的地区。"} />
          )}
          <div className="mt-4 flex flex-wrap gap-2">
            {(selectedRegion?.context?.fields || []).map((field: any) => (
              <span key={field.key} className="rounded-full bg-mist px-3 py-1 text-xs text-slate-600">
                {field.label}：{field.value}
              </span>
            ))}
          </div>
          {selectedRegion?.context?.is_demo_label ? (
            <p className="mt-3 text-xs text-slate-500">{selectedRegion.context.note}</p>
          ) : null}
        </section>
      </div>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h3 className="font-display text-lg">候选形态相似度</h3>
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr className="border-b text-slate-500">
                <th className="py-2 pr-3">对照</th>
                <th className="py-2 pr-3">综合</th>
                <th className="py-2 pr-3">形状</th>
                <th className="py-2 pr-3">上下文</th>
                <th className="py-2 pr-3">方向</th>
              </tr>
            </thead>
            <tbody>
              {(match?.matches || []).map((row: any) => (
                <tr key={row.analog_id} className="border-b border-slate-100">
                  <td className="py-2 pr-3">{row.name}</td>
                  <td className="py-2 pr-3">{Number(row.combined_score).toFixed(2)}</td>
                  <td className="py-2 pr-3">{Number(row.shape_score).toFixed(2)}</td>
                  <td className="py-2 pr-3">{Number(row.context_score).toFixed(2)}</td>
                  <td className="py-2 pr-3">{DIRECTION_LABELS[row.direction] || row.direction}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        {analogCharts.map((item) => (
          <ChartPanel key={item.id} title={item.title} option={item.option} height={180} />
        ))}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {cards.map((card) => (
          <article key={card.id} className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
            <div className="text-xs uppercase tracking-[0.2em] text-sea">历史资料卡</div>
            <h3 className="mt-2 font-display text-2xl">{card.name}</h3>
            <p className="mt-1 text-sm text-slate-500">{card.period_note || card.era}</p>
            <p className="mt-3 text-sm leading-6 text-slate-700">{card.summary}</p>
            <div className="mt-4 rounded-xl bg-mist px-3 py-2 text-sm">
              <div className="font-medium">适用范围</div>
              <p className="mt-1 text-slate-600">{card.scope || card.notes_vs_series}</p>
            </div>
            {(card.qualitative_phases || []).length ? (
              <p className="mt-3 text-sm text-slate-600">定性阶段：{(card.qualitative_phases || []).join(" → ")}</p>
            ) : null}
            <div className="mt-4 text-sm">
              <div className="font-medium">来源</div>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-slate-600">
                {(card.sources || []).map((source: any) => (
                  <li key={source.url}>
                    <a className="underline decoration-slate-300" href={source.url} target="_blank" rel="noreferrer">
                      {source.title}
                    </a>
                    {source.scope || source.note ? ` · ${source.scope || source.note}` : ""}
                  </li>
                ))}
              </ul>
            </div>
            <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
              <div className="rounded-xl border border-slate-200 px-3 py-2">
                <div className="font-medium">可训练时序</div>
                <p className="mt-1 text-slate-600">{card.trainable_series_available ? "已提供" : "未提供（与资料卡分开）"}</p>
              </div>
              <div className="rounded-xl border border-slate-200 px-3 py-2">
                <div className="font-medium">待补充字段</div>
                <p className="mt-1 text-slate-600">{(card.pending_fields || []).join("、") || "无"}</p>
              </div>
            </div>
            <p className="mt-3 text-xs text-slate-500">{card.notes || card.notes_vs_series}</p>
          </article>
        ))}
      </div>
    </div>
  );
}
