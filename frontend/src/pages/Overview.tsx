import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { ChartPanel } from "../components/ChartPanel";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";

const CATEGORY_LABELS: Record<string, string> = {
  suspected_reporting_change: "疑似报告过程变化",
  suspected_epidemic_change: "疑似疫情动态变化",
  data_quality: "数据质量问题",
  insufficient_evidence: "证据不足",
};

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

export function Overview() {
  const [diseases, setDiseases] = useState<any[]>([]);
  const [regions, setRegions] = useState<any[]>([]);
  const [diseaseId, setDiseaseId] = useState("");
  const [regionId, setRegionId] = useState("");
  const [data, setData] = useState<any>(null);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [completeness, setCompleteness] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.diseases(), api.regions()])
      .then(([d, r]) => {
        setDiseases(d);
        setRegions(r);
        setDiseaseId(d[0]?.id || "");
        setRegionId(r[0]?.id || "");
      })
      .catch((err) => setError(String(err.message || err)));
  }, []);

  useEffect(() => {
    if (!diseaseId || !regionId) return;
    setLoading(true);
    Promise.all([api.overview(diseaseId, regionId), api.alerts(), api.completeness(diseaseId, regionId)])
      .then(([overview, alertRows, complete]) => {
        setData(overview);
        setAlerts(alertRows.filter((row: any) => row.region_id === regionId).slice(0, 12));
        setCompleteness(complete);
      })
      .catch((err) => setError(String(err.message || err)))
      .finally(() => setLoading(false));
  }, [diseaseId, regionId]);

  const trendOption = useMemo(() => {
    if (!data?.series?.length) return undefined;
    return {
      tooltip: { trigger: "axis" },
      grid: { left: 48, right: 24, top: 24, bottom: 40 },
      xAxis: { type: "category", data: data.series.map((row: any) => row.period_end) },
      yAxis: { type: "value", name: "周新增住院" },
      series: [
        {
          type: "line",
          data: data.series.map((row: any) => row.value),
          showSymbol: false,
          lineStyle: { color: "#0f6a6a", width: 2 },
          areaStyle: { color: "rgba(15,106,106,0.12)" },
        },
      ],
    };
  }, [data]);

  const revisionOption = useMemo(() => {
    const paths = data?.revision?.paths || [];
    if (!paths.length) return undefined;
    const periods = paths.map((path: any) => path.period_end);
    const lagSeries = [1, 14, 28].map((lag) => ({
      name: `滞后 ${lag} 天`,
      type: "line",
      data: paths.map((path: any) => {
        const point = (path.points || []).find((item: any) => item.lag_days === lag);
        return point?.value ?? null;
      }),
      connectNulls: false,
    }));
    return {
      tooltip: { trigger: "axis" },
      legend: { data: lagSeries.map((item) => item.name) },
      grid: { left: 48, right: 20, top: 40, bottom: 40 },
      xAxis: { type: "category", data: periods },
      yAxis: { type: "value", name: "住院版本" },
      series: lagSeries,
    };
  }, [data]);

  if (error) return <EmptyState title="无法加载总览" detail={error} />;
  if (loading && !data) return <Loading />;

  return (
    <div className="space-y-5">
      <section className="grid gap-4 rounded-2xl border border-slate-200/80 bg-white/80 p-4 md:grid-cols-3 lg:grid-cols-6">
        <label className="text-sm">
          <div className="mb-1 text-slate-500">疾病</div>
          <select className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2" value={diseaseId} onChange={(e) => setDiseaseId(e.target.value)}>
            {diseases.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-slate-500">地区</div>
          <select className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2" value={regionId} onChange={(e) => setRegionId(e.target.value)}>
            {regions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <div className="text-sm">
          <div className="mb-1 text-slate-500">数据截止时间</div>
          <div className="rounded-lg border border-slate-200 bg-mist px-3 py-2 text-xs">{data?.quality?.cutoff_time || "尚未设定"}</div>
        </div>
        <div className="text-sm md:col-span-2">
          <div className="mb-1 text-slate-500">地区上下文</div>
          <div className="rounded-lg border border-slate-200 bg-mist px-3 py-2 text-xs leading-5">
            {data?.region?.context?.available
              ? (data.region.context.fields || []).map((field: any) => field.value).join(" · ")
              : "未提供国家/人群/环境标签"}
          </div>
        </div>
        <div className="text-sm">
          <div className="mb-1 text-slate-500">数据可信程度</div>
          <div className="rounded-lg border border-slate-200 bg-mist px-3 py-2 text-xs leading-5">
            可见点 {data?.quality?.n_points ?? 0} · 缺失周 {data?.quality?.n_missing ?? 0} · 暂定比例{" "}
            {((data?.quality?.provisional_share || 0) * 100).toFixed(0)}%
          </div>
        </div>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartPanel title="最近住院趋势" option={trendOption} empty="当前地区没有可见住院序列" />
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
          <h3 className="mb-3 font-display text-lg">修订估计（与未来预测分开）</h3>
          {!data?.revision?.estimable ? (
            <EmptyState title="无法估计修订过程" detail={data?.revision?.message || "缺少历史版本时继续用原始数据运行。"} />
          ) : (
            <div className="space-y-2 text-sm text-slate-700">
              <p>{data.revision.message}</p>
              <p>中位修订量：{data.revision.median_delta?.toFixed?.(2)}；负向修订占比 {(data.revision.negative_revision_share * 100).toFixed(0)}%</p>
              {data.nowcast ? (
                <p>
                  近期初报成熟估计区间：{data.nowcast.lower?.toFixed?.(1)} – {data.nowcast.upper?.toFixed?.(1)}（中位{" "}
                  {data.nowcast.median?.toFixed?.(1)}）。{data.nowcast.note}
                </p>
              ) : null}
              <p className="text-xs text-slate-500">{data.revision.uncertainty_note}</p>
            </div>
          )}
        </section>
      </div>

      <ChartPanel
        title="修订轨迹（同一周期、不同发布时间）"
        option={revisionOption}
        empty="无法展示修订轨迹：缺少多个发布时间版本。"
        height={300}
      />

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h3 className="mb-2 font-display text-lg">地区与人群画像</h3>
        {(() => {
          const profile = data?.region?.profile || data?.region?.context?.profile || {};
          const age = profile.age_distribution || {};
          return (
            <div className="grid gap-3 text-sm md:grid-cols-3">
              <div className="rounded-xl border border-slate-200 px-3 py-2">
                <div className="text-slate-500">国家 / 人口</div>
                <p className="mt-1">{profile.country_id || data?.region?.country_id || "未提供"} · {data?.region?.population?.toLocaleString?.() || "无分母"}</p>
              </div>
              <div className="rounded-xl border border-slate-200 px-3 py-2">
                <div className="text-slate-500">年龄结构（人口比例）</div>
                <p className="mt-1">
                  0–17 {age["0_17"] != null ? `${Math.round(age["0_17"] * 100)}%` : "缺失"} · 18–64{" "}
                  {age["18_64"] != null ? `${Math.round(age["18_64"] * 100)}%` : "缺失"} · 65+{" "}
                  {age["65plus"] != null ? `${Math.round(age["65plus"] * 100)}%` : "缺失"}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200 px-3 py-2">
                <div className="text-slate-500">家庭与拥挤</div>
                <p className="mt-1">
                  户均 {profile.household_size ?? "缺失"} · 拥挤指数 {profile.crowding_index ?? "缺失"}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200 px-3 py-2 md:col-span-3">
                <div className="text-slate-500">数据完整性（缺失≠0）</div>
                <p className="mt-1 text-xs text-slate-600">
                  {(completeness?.metrics || []).join("、") || "尚无指标"}。{completeness?.note}
                </p>
                <p className="mt-2 text-xs text-slate-500">{profile.age_note}</p>
                <p className="mt-1 text-xs text-slate-500">
                  族裔分析：{profile.ethnicity_analysis === "skipped_no_data" ? "无合法明确数据，已跳过，不生成族裔标签。" : profile.ethnicity_analysis || "未声明"}
                </p>
              </div>
            </div>
          );
        })()}
      </section>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h3 className="mb-2 font-display text-lg">与历史疫情形态对照</h3>
        <p className="text-sm text-slate-600">
          {data?.analog?.top
            ? `当前序列更接近「${data.analog.top.name}」，阶段 ${PHASE_LABELS[data.analog.top.phase] || data.analog.top.phase}，方向 ${DIRECTION_LABELS[data.analog.top.direction] || data.analog.top.direction}，综合相似度 ${Number(data.analog.top.combined_score).toFixed(2)}。`
            : "可见周数不足或形态不明确，未调整预测方向。"}
        </p>
        <p className="mt-2 text-xs text-slate-500">{data?.analog?.disclaimer}</p>
        {data?.region?.context?.note ? <p className="mt-1 text-xs text-slate-500">{data.region.context.note}</p> : null}
      </section>

      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h3 className="mb-3 font-display text-lg">异常列表</h3>
        {!alerts.length ? (
          <EmptyState title="暂无异常" detail="运行实验回放后会出现可解释告警。分类不是已确认的传播机制。" />
        ) : (
          <div className="space-y-3">
            {alerts.map((alert: any) => (
              <div key={alert.id} className="rounded-xl border border-slate-200 px-3 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge status={alert.review_status} />
                  <span className="font-medium">{CATEGORY_LABELS[alert.category] || alert.category}</span>
                  <span className="text-xs text-slate-500">{alert.period_end}</span>
                </div>
                <p className="mt-2 text-sm text-slate-600">{alert.supporting_evidence}</p>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
