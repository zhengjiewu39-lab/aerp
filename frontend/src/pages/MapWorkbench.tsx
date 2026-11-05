import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { ChartPanel } from "../components/ChartPanel";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { MapCanvas } from "./MapCanvas";

const VIEWS = [
  { id: "count", label: "报告人数" },
  { id: "rate_per_100k", label: "每10万人" },
  { id: "wow_change", label: "趋势变化" },
];

function preferredMetric(spec: any, current?: string, parent?: string) {
  const id = spec?.id || spec?.disease_id;
  const targets = (spec?.targets || []).filter((item: string) => item !== "threshold_risk");
  const coverage = (spec?.coverage?.metrics || []).filter((item: string) => item !== "threshold_risk");
  const available = [...new Set([...targets, ...coverage])];
  if (id === "influenza" || id === "rsv") {
    if (!parent && available.includes("lab_detections")) return "lab_detections";
    if (parent && available.includes("hospital_admissions")) return "hospital_admissions";
    if (current && available.includes(current)) return current;
    return available[0] || "lab_detections";
  }
  if (current && available.includes(current) && (targets.includes(current) || coverage.includes(current))) return current;
  const def = spec?.default_target;
  if (def && available.includes(def)) return def;
  if (id === "covid19" && available.includes("weekly_cases")) return "weekly_cases";
  if (available.includes("hospital_admissions")) return "hospital_admissions";
  return available[0] || "weekly_cases";
}

const WORKBENCH_DISEASES = new Set(["covid19", "influenza", "rsv"]);

function trendMark(dir: string | undefined) {
  if (dir === "up") return "↑";
  if (dir === "down") return "↓";
  if (dir === "stable") return "→";
  return "·";
}

const emptyManual = {
  summary: "",
  observations: "",
  possible_explanations: "",
  uncertainties: "",
  data_quality_issues: "",
  suggested_checks: "",
  citations: "",
};

export function MapWorkbench() {
  const [params, setParams] = useSearchParams();
  const [diseases, setDiseases] = useState<any[]>([]);
  const [bounds, setBounds] = useState<any>(null);
  const [adminBounds, setAdminBounds] = useState<any>(null);
  const [obs, setObs] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [coverage, setCoverage] = useState<any>(null);
  const [aiStatus, setAiStatus] = useState<any>(null);
  const [analysis, setAnalysis] = useState<any>(null);
  const [analyses, setAnalyses] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [listFilter, setListFilter] = useState<"value" | "all">("value");
  const [search, setSearch] = useState("");
  const [searchHits, setSearchHits] = useState<any[]>([]);
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [mode, setMode] = useState<"ai" | "manual">("ai");
  const [manual, setManual] = useState(emptyManual);
  const [reviewComment, setReviewComment] = useState("");
  const [busy, setBusy] = useState("");
  const [flyTo, setFlyTo] = useState<any>(null);
  const gen = useRef(0);
  const lockPeriod = useRef<string | undefined>(undefined);

  const diseaseId = params.get("disease") || "covid19";
  const metric = params.get("metric") || "weekly_cases";
  const view = params.get("view") || "count";
  const timeMode = params.get("time") || "latest";
  const period = params.get("period") || "";
  const geoId = params.get("geo") || "";
  const parent = params.get("parent") || "";
  const dataMode = params.get("mode") || "real";
  const lastDisease = useRef(diseaseId);

  const setQuery = useCallback(
    (patch: Record<string, string | null>) => {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        for (const [key, value] of Object.entries(patch)) {
          if (!value) next.delete(key);
          else next.set(key, value);
        }
        return next;
      }, { replace: true });
    },
    [setParams],
  );

  useEffect(() => {
    api.diseases().then(setDiseases).catch((err) => setError(String(err.message || err)));
    api.aiStatus().then(setAiStatus).catch(() => setAiStatus({ configured: false, message: "AI未配置/调用失败" }));
    api.geoBoundaries({ level: "country" }).then(setBounds).catch((err) => setError(String(err.message || err)));
  }, []);

  useEffect(() => {
    if (!diseases.length) return;
    const spec = diseases.find((item) => item.id === diseaseId);
    const nextMetric = preferredMetric(spec, metric, parent);
    if (lastDisease.current !== diseaseId) {
      lastDisease.current = diseaseId;
      lockPeriod.current = undefined;
      setPlaying(false);
      setQuery({ period: null, geo: null, parent: null, metric: preferredMetric(spec, undefined, undefined) });
      return;
    }
    if (nextMetric !== metric) setQuery({ metric: nextMetric });
  }, [diseaseId, diseases, metric, parent, setQuery]);

  useEffect(() => {
    const current = ++gen.current;
    const ctrl = new AbortController();
    setLoading(true);
    const q: Record<string, string> = { disease_id: diseaseId, metric, view, time_mode: timeMode, data_mode: dataMode };
    if (period) q.period_end = period;
    if (parent) q.parent_geo_id = parent;
    if (playing && lockPeriod.current) q.lock_period_end = lockPeriod.current;
    api
      .mapObservations(q, { signal: ctrl.signal })
      .then((payload) => {
        if (current !== gen.current) return;
        setObs(payload);
        if (payload.coverage) setCoverage(payload.coverage);
        setError("");
        if (!lockPeriod.current && payload.period_end) lockPeriod.current = payload.period_end;
        const ends = (payload.periods || []).map((item: any) => item.period_end);
        const patch: Record<string, string | null> = {};
        if ((!period || (ends.length && !ends.includes(period))) && payload.period_end) patch.period = payload.period_end;
        if (payload.switch_view && view === "rate_per_100k") patch.view = payload.switch_view;
        const spec = diseases.find((item) => item.id === diseaseId);
        const targets = spec?.targets || [];
        if (
          payload.suggested_metric &&
          payload.suggested_metric !== metric &&
          (!targets.length || !targets.includes(metric)) &&
          !(payload.features || []).some((item: any) => item.count != null && item.data_status === "reported")
        ) {
          patch.metric = payload.suggested_metric;
        }
        if (payload.suggested_metric && payload.suggested_metric !== metric && metric === "hospital_admissions" && !parent) {
          patch.metric = payload.suggested_metric;
        }
        if (Object.keys(patch).length) setQuery(patch);
      })
      .catch((err) => {
        if (err.name === "AbortError") return;
        if (current !== gen.current) return;
        setError(String(err.message || err));
      })
      .finally(() => {
        if (current === gen.current) setLoading(false);
      });
    return () => ctrl.abort();
  }, [diseaseId, metric, view, timeMode, period, parent, dataMode, playing, setQuery]);

  useEffect(() => {
    if (!parent) {
      setAdminBounds(null);
      return;
    }
    api.geoBoundaries({ level: "adm1", parent_geo_id: parent }).then(setAdminBounds).catch(() => setAdminBounds(null));
  }, [parent]);

  useEffect(() => {
    if (!geoId) {
      setDetail(null);
      return;
    }
    const ctrl = new AbortController();
    const current = gen.current;
    api
      .mapRegion(geoId, { disease_id: diseaseId, metric, period_end: period || "", time_mode: timeMode, view }, { signal: ctrl.signal })
      .then((payload) => {
        if (current !== gen.current) return;
        setDetail(payload);
      })
      .catch(() => undefined);
    api.regionAnalyses(geoId, { disease_id: diseaseId, metric }).then(setAnalyses).catch(() => setAnalyses([]));
    return () => ctrl.abort();
  }, [geoId, diseaseId, metric, period, timeMode, view, analysis?.id, analysis?.status]);

  useEffect(() => {
    if (!playing) return;
    const periods: string[] = (obs?.periods || []).map((item: any) => item.period_end);
    if (!periods.length) return;
    const timer = setInterval(() => {
      const idx = Math.max(0, periods.indexOf(period));
      const next = periods[(idx + 1) % periods.length];
      setQuery({ period: next });
    }, 1200);
    return () => clearInterval(timer);
  }, [playing, obs?.periods, period, setQuery]);

  useEffect(() => {
    if (!search.trim()) {
      setSearchHits([]);
      return;
    }
    const handle = setTimeout(() => {
      api.geoUnits({ q: search.trim() }).then((payload) => setSearchHits(payload.items || [])).catch(() => undefined);
    }, 200);
    return () => clearTimeout(handle);
  }, [search]);

  const regionList = useMemo(() => {
    const rank: Record<string, number> = { reported: 0, stale: 1, partial: 2, zero: 3, unreported: 4, no_data: 5 };
    const rows = [...(obs?.features || [])].sort((a, b) => {
      const d = (rank[a.data_status] ?? 9) - (rank[b.data_status] ?? 9);
      if (d) return d;
      return String(a.name || a.geo_id).localeCompare(String(b.name || b.geo_id), "zh");
    });
    if (listFilter === "value") {
      return rows.filter((item) => ["reported", "zero", "stale", "partial"].includes(item.data_status));
    }
    return rows;
  }, [obs, listFilter]);
  const comparableCount = (obs?.features || []).filter((item: any) => item.rank_eligible).length;
  const selectedFeature = useMemo(
    () => (obs?.features || []).find((item: any) => item.geo_id === geoId) || null,
    [obs, geoId],
  );
  const histogramMax = Math.max(1, ...((obs?.class_histogram || []).map((item: any) => Number(item.n) || 0)));

  const disease = diseases.find((item) => item.id === diseaseId);
  const demo = Boolean(obs?.watermark || disease?.is_synthetic);

  function selectGeo(id: string) {
    setQuery({ geo: id });
    setRightOpen(true);
  }

  function locate(hit: any) {
    const all = [...(bounds?.geojson?.features || []), ...(bounds?.points?.features || []), ...(adminBounds?.geojson?.features || [])];
    const feat = all.find((item: any) => item.id === hit.geo_id || item.properties?.geo_id === hit.geo_id);
    setFlyTo({ geoId: hit.geo_id, geometry: feat?.geometry });
    selectGeo(hit.geo_id);
    setSearch("");
  }

  async function runAi() {
    if (!geoId) return;
    setBusy("正在调用模型…");
    try {
      const payload = await api.createAiAnalysis({
        disease_id: diseaseId,
        geo_id: geoId,
        metric,
        period_end: period,
        time_mode: timeMode,
        view,
      });
      setAnalysis(payload);
    } catch (err: any) {
      setAnalysis({ error: true, message: String(err.message || err) });
    } finally {
      setBusy("");
    }
  }

  async function runManual(asDraft = true) {
    if (!geoId) return;
    setBusy("保存手动分析…");
    const structured = {
      summary: manual.summary,
      observations: [{ text: manual.observations, evidence_ids: ["e2"] }],
      possible_explanations: [{ text: manual.possible_explanations, evidence_ids: ["e2"] }],
      uncertainties: [{ text: manual.uncertainties, evidence_ids: ["e6"] }],
      data_quality_issues: [{ text: manual.data_quality_issues, evidence_ids: ["e6"] }],
      suggested_checks: [{ text: manual.suggested_checks, evidence_ids: ["e7"] }],
      citations: [{ evidence_id: "e2", note: manual.citations || "手动标注" }],
    };
    try {
      const payload = await api.createManualAnalysis({
        disease_id: diseaseId,
        geo_id: geoId,
        metric,
        period_end: period,
        time_mode: timeMode,
        view,
        as_draft: asDraft,
        structured,
      });
      setAnalysis(payload);
    } catch (err: any) {
      setAnalysis({ error: true, message: String(err.message || err) });
    } finally {
      setBusy("");
    }
  }

  async function review(action: string, edited?: any) {
    if (!analysis?.id) return;
    setBusy("提交审核…");
    try {
      const payload = await api.reviewAnalysis(analysis.id, {
        action,
        expected_revision_id: analysis.current_revision_id,
        snapshot_hash: analysis.snapshot_hash,
        comment: reviewComment,
        edited,
      });
      setAnalysis(payload);
    } catch (err: any) {
      setAnalysis((current: any) => ({ ...current, review_error: String(err.message || err) }));
    } finally {
      setBusy("");
    }
  }

  function exportView() {
    const blob = new Blob(
      [
        JSON.stringify(
          {
            generated_at: new Date().toISOString(),
            query: Object.fromEntries(params.entries()),
            observations: obs,
            detail,
            analysis,
            note: analysis && analysis.status !== "confirmed" ? "含未审核草稿，不得作为正式报告。" : undefined,
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `aerp-map-${diseaseId}-${period || "na"}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  const mapDiseases = diseases.filter((item) => WORKBENCH_DISEASES.has(item.id) && !item.is_synthetic);
  const metrics = (disease?.coverage?.metrics?.length ? disease.coverage.metrics : disease?.targets || ["weekly_cases"]).filter(
    (item: string) => item !== "threshold_risk",
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden">
      <div className="card flex shrink-0 flex-wrap items-end gap-3">
        <label className="text-sm">
          疾病
          <select className="field mt-1" value={diseaseId} onChange={(ev) => {
            lockPeriod.current = undefined;
            setPlaying(false);
            const next = diseases.find((item) => item.id === ev.target.value);
            setQuery({ disease: ev.target.value, metric: preferredMetric(next), geo: null, parent: null, period: null });
          }}>
            {mapDiseases.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name_zh || item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          指标
          <select className="field mt-1" value={metric} onChange={(ev) => { lockPeriod.current = undefined; setQuery({ metric: ev.target.value, period: null }); }}>
            {metrics.map((item: string) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          图层
          <select className="field mt-1" value={view} onChange={(ev) => setQuery({ view: ev.target.value })}>
            {VIEWS.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          时间模式
          <select className="field mt-1" value={timeMode} onChange={(ev) => setQuery({ time: ev.target.value })}>
            <option value="unified">统一周期</option>
            <option value="latest">各地最新（显示时差）</option>
          </select>
        </label>
        <label className="text-sm">
          数据
          <select
            className="field mt-1"
            value={dataMode}
            onChange={(ev) => {
              lockPeriod.current = undefined;
              setQuery({ mode: ev.target.value, period: null });
            }}
          >
            <option value="real">真实监测</option>
            <option value="demo">演示（主动打开）</option>
          </select>
        </label>
        <label className="relative min-w-[220px] flex-1 text-sm">
          搜索国家/地区
          <input className="field mt-1" value={search} onChange={(ev) => setSearch(ev.target.value)} placeholder="名称、ISO 或 FIPS" />
          {searchHits.length ? (
            <ul className="absolute z-20 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-slate-200 bg-white text-sm shadow-card">
              {searchHits.slice(0, 12).map((item) => (
                <li key={item.geo_id}>
                  <button className="w-full px-3 py-2 text-left hover:bg-slate-50" onClick={() => locate(item)}>
                    {item.name} <span className="text-slate-500">{item.geo_id}</span>
                    {item.representative_point ? " · 代表点" : ""}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </label>
        <button className="btn" onClick={() => setQuery({ parent: null })}>
          全球
        </button>
        {obs?.suggested_parent_geo_id && !parent ? (
          <button className="btn" onClick={() => setQuery({ parent: obs.suggested_parent_geo_id })}>
            查看有数据的下级地区
          </button>
        ) : null}
        <button className="btn" onClick={exportView}>
          导出当前视图
        </button>
      </div>

      {demo ? <div className="rounded-lg border-2 border-dashed border-amber-400 bg-amber-50 px-3 py-2 text-center text-sm font-medium text-amber-950">演示数据水印 · 不得当作真实国家监测</div> : null}
      {error ? <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-900">{error}</div> : null}

      <div className="flex min-h-0 flex-1 flex-col gap-3 lg:flex-row">
        <aside className={`shrink-0 space-y-3 overflow-auto lg:w-72 ${leftOpen ? "" : "hidden lg:block"}`}>
          <section className="card text-sm">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-medium">图例与覆盖</h2>
              <button className="text-xs text-sea lg:hidden" onClick={() => setLeftOpen(false)}>收起</button>
            </div>
            <p className="text-slate-600">{obs?.coverage_note || coverage?.note}</p>
            <dl className="mt-3 grid grid-cols-2 gap-2">
              <div>边界 {coverage?.geo_coverage?.n_boundary_units ?? coverage?.n_boundary_units ?? "—"}</div>
              <div>有病种数据 {coverage?.disease_coverage?.n_with_data ?? coverage?.n_with_data ?? "—"}</div>
              <div>无病种数据 {coverage?.disease_coverage?.n_without_data ?? coverage?.n_without_data ?? "—"}</div>
              <div>未匹配 {obs?.unmatched_count ?? coverage?.n_unmatched_records ?? 0}</div>
            </dl>
            <p className="mt-2 text-xs text-slate-500">分级：{obs?.legend?.method}。{obs?.legend?.method_note}</p>
            {obs?.class_histogram?.length ? (
              <div className="mt-3 space-y-1">
                <div className="text-xs text-slate-500">本周各档地区数</div>
                {obs.class_histogram.map((item: any) => (
                  <div key={item.label} className="flex items-center gap-2 text-xs">
                    <span className="w-16 shrink-0">{item.label}</span>
                    <span className="h-2 flex-1 rounded-sm bg-slate-100">
                      <span className="block h-2 rounded-sm" style={{ width: `${(Number(item.n) / histogramMax) * 100}%`, background: item.color }} />
                    </span>
                    <span className="w-6 text-right">{item.n}</span>
                  </div>
                ))}
              </div>
            ) : null}
            {view === "count" ? <p className="mt-2 text-xs text-amber-800">{obs?.count_warning}</p> : null}
            {coverage?.unmatched_preview?.length ? (
              <p className="mt-2 text-xs text-slate-500">未匹配示例：{coverage.unmatched_preview.slice(0, 3).map((item: any) => item.source_region_id).join("、")}</p>
            ) : null}
          </section>
          <section className="card max-h-72 overflow-auto text-sm">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h2 className="font-medium">地区列表</h2>
              <button className="text-xs text-sea" onClick={() => setListFilter(listFilter === "value" ? "all" : "value")}>
                {listFilter === "value" ? "显示全部" : "只看有数值"}
              </button>
            </div>
            <p className="mb-2 text-xs text-slate-500">口径可比 {comparableCount} 个。{obs?.coverage_note}</p>
            <ul>
              {regionList.map((item) => (
                <li key={item.geo_id}>
                  <button
                    className={`w-full py-1 text-left ${item.geo_id === geoId ? "bg-teal-50" : ""}`}
                    onClick={() => selectGeo(item.geo_id)}
                  >
                    {item.name}
                    <span className="ml-2 text-xs text-slate-500">{item.data_status || "无数据"}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </aside>

        <section className="relative flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="mb-2 flex gap-2 lg:hidden">
            <button className="btn" onClick={() => setLeftOpen(true)}>筛选</button>
            <button className="btn" onClick={() => setRightOpen(true)}>详情</button>
          </div>
          {loading && !obs ? <Loading label="加载地图数据…" /> : null}
          <div className="min-h-[240px] flex-1">
            <MapCanvas
              countryGeo={bounds?.geojson || null}
              pointsGeo={bounds?.points || null}
              adminGeo={parent ? adminBounds?.geojson : null}
              features={obs?.features || []}
              selectedId={geoId}
              isForecast={false}
              onSelect={selectGeo}
              flyTo={flyTo}
              error={bounds ? "" : error}
            />
          </div>
        </section>

        <aside className={`shrink-0 space-y-3 overflow-auto lg:w-[360px] ${rightOpen ? "" : "hidden lg:block"}`}>
          <section className="card text-sm">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-medium">地区详情</h2>
              <button className="text-xs text-sea lg:hidden" onClick={() => setRightOpen(false)}>收起</button>
            </div>
            {!geoId ? (
              <EmptyState title="点击地图或地区列表打开趋势、来源、订正与分析" />
            ) : (
              <div className="space-y-2">
                <div className="text-lg font-display">{detail?.name || geoId}</div>
                <div>状态：{detail?.current?.data_status || obs?.note || "无数据"}</div>
                {obs?.note ? (
                  <div className="rounded bg-amber-50 px-2 py-1 text-amber-950">
                    {obs.note}
                    {obs.switch_metric ? (
                      <button className="btn ml-2" onClick={() => setQuery({ metric: obs.switch_metric })}>
                        切换到 {obs.switch_metric}
                      </button>
                    ) : null}
                    {obs.switch_view ? (
                      <button className="btn ml-2" onClick={() => setQuery({ view: obs.switch_view })}>
                        改为报告人数
                      </button>
                    ) : null}
                  </div>
                ) : null}
                <div>报告人数：{detail?.current?.count ?? "—"} {detail?.current?.unit || ""}</div>
                <div>每10万人：{detail?.current?.rate_per_100k ?? "—"}</div>
                {selectedFeature ? <div>数值区间：{selectedFeature.label || "—"} {trendMark(selectedFeature.trend_dir)}</div> : null}
                <div>周期：{detail?.current?.period_start || "—"} → {detail?.current?.period_end || "—"}</div>
                <div>采集/发布：{detail?.current?.issued_at || "—"}（无官方发布时间时为采集时点快照）</div>
                <div>来源：{detail?.current?.source_id || "—"} {detail?.source_url ? <a className="text-sea" href={detail.source_url} target="_blank" rel="noreferrer">链接</a> : null}</div>
                {detail?.current?.source_conflicts?.length ? (
                  <p className="text-xs text-amber-800">另有来源未合并：{detail.current.source_conflicts.map((item: any) => item.source_id).join("、")}</p>
                ) : null}
                {detail?.comparability ? (
                  <p className="text-xs">{detail.comparability.comparable ? "口径可比" : `不可比：${detail.comparability.reason}`}</p>
                ) : null}
                {detail?.revision_contrast ? (
                  <div className="rounded border border-slate-200 p-2 text-xs">
                    <div className="font-medium">订正对照</div>
                    {detail.revision_contrast.comparable ? (
                      <>
                        <div>首次 {detail.revision_contrast.first?.value}（{detail.revision_contrast.first?.issued_at}）</div>
                        <div>修订 {detail.revision_contrast.latest?.value}（{detail.revision_contrast.latest?.issued_at}）</div>
                        <div>差值 {detail.revision_contrast.delta}</div>
                        <p>这是同一报告周的数据订正，不是报告趋势变化。</p>
                      </>
                    ) : (
                      <p>{detail.revision_contrast.reason || "没有版本历史，不可比较。"}</p>
                    )}
                  </div>
                ) : null}
                {detail?.population ? <div>人口分母：{detail.population.population}（{detail.population.year}，{detail.population.source}）</div> : <div>无可靠人口分母，不计算人均率。</div>}
                {detail?.point_note ? <p className="text-xs text-slate-500">{detail.point_note}</p> : null}
                {detail?.drilldown_available ? (
                  <button className="btn-primary btn" onClick={() => setQuery({ parent: geoId, geo: null })}>下钻到一级行政区</button>
                ) : (
                  <p className="text-xs text-slate-500">{detail?.drilldown_message || "没有下级数据，不把本区数值复制到省州。"}</p>
                )}
              </div>
            )}
          </section>
          <ChartPanel
            title="趋势（观测）"
            height={180}
            empty="选择有序列的地区"
            option={
              detail?.series?.length
                ? {
                    tooltip: { trigger: "axis" },
                    xAxis: { type: "category", data: detail.series.map((item: any) => item.period_end) },
                    yAxis: { type: "value" },
                    series: [{ type: "line", data: detail.series.map((item: any) => item.value), color: "#1f7a78" }],
                  }
                : undefined
            }
          />
          <section className="card space-y-3 text-sm">
            <div className="flex gap-2">
              <button className={`btn ${mode === "ai" ? "btn-primary" : ""}`} onClick={() => setMode("ai")}>AI 分析</button>
              <button className={`btn ${mode === "manual" ? "btn-primary" : ""}`} onClick={() => setMode("manual")}>手动分析</button>
            </div>
            {mode === "ai" ? (
              <>
                <p className="text-xs text-slate-500">{aiStatus?.reason || aiStatus?.message || "核查 AI 配置"}。密钥只在后端。{aiStatus?.live_verified ? " 最近真实调用已记录。" : " 尚未验证。"}</p>
                {aiStatus?.probe_available ? (
                  <button className="btn" onClick={() => api.aiProbe().then(setAiStatus).catch((err) => setAiStatus({ configured: false, message: String(err.message || err) }))}>真实连通检查</button>
                ) : null}
                <button className="btn-primary btn" disabled={!geoId || Boolean(busy) || !aiStatus?.configured || aiStatus?.state === "test_stub"} onClick={runAi}>生成 AI 草稿</button>
              </>
            ) : (
              <div className="space-y-2">
                {Object.keys(emptyManual).map((key) => (
                  <label key={key} className="block text-xs">
                    {key}
                    <textarea className="field mt-1" rows={2} value={(manual as any)[key]} onChange={(ev) => setManual({ ...manual, [key]: ev.target.value })} />
                  </label>
                ))}
                <div className="flex gap-2">
                  <button className="btn" disabled={!geoId || Boolean(busy)} onClick={() => runManual(true)}>保存草稿</button>
                  <button className="btn-primary btn" disabled={!geoId || Boolean(busy)} onClick={() => runManual(false)}>提交待确认</button>
                </div>
              </div>
            )}
            {busy ? <p>{busy}</p> : null}
            {analysis?.error || analysis?.message && analysis.status == null ? <p className="text-rose-800">{analysis.message}</p> : null}
            {analysis?.id ? (
              <div className="space-y-2 rounded-lg border border-slate-200 p-3">
                {analysis.draft_banner ? <div className="rounded bg-amber-100 px-2 py-1 text-amber-950">{analysis.draft_banner}</div> : null}
                <div>状态：{analysis.status_label} · {analysis.source_note}</div>
                <div className="whitespace-pre-wrap text-slate-800">{analysis.current?.structured?.summary}</div>
                <ul className="list-disc pl-4">
                  {(analysis.current?.structured?.observations || []).map((item: any, idx: number) => (
                    <li key={idx}>{item.text} <span className="text-xs text-slate-500">{(item.evidence_ids || []).join(", ")}</span></li>
                  ))}
                </ul>
                <p className="text-xs text-slate-500">局限：{(analysis.current?.structured?.uncertainties || []).map((item: any) => item.text).join(" ")}</p>
                {analysis.review_error ? <p className="text-rose-800">{analysis.review_error}</p> : null}
                <textarea className="field" rows={2} placeholder="审核意见" value={reviewComment} onChange={(ev) => setReviewComment(ev.target.value)} />
                <div className="flex flex-wrap gap-2">
                  <button className="btn-primary btn" onClick={() => review("confirm")}>确认采用</button>
                  <button className="btn" onClick={() => review("edit_confirm", analysis.current?.structured)}>编辑后确认</button>
                  <button className="btn" onClick={() => review("reject")}>驳回</button>
                </div>
                <p className="text-xs text-slate-500">{analysis.identity_note}</p>
              </div>
            ) : null}
            {analyses.length ? (
              <div>
                <h3 className="mb-1 font-medium">历史记录</h3>
                {analyses.slice(0, 6).map((item) => (
                  <button key={item.id} className="block w-full py-1 text-left text-xs" onClick={() => setAnalysis(item)}>
                    {item.created_at} · {item.mode} · {item.status_label}
                  </button>
                ))}
              </div>
            ) : null}
          </section>
        </aside>
      </div>

      <footer className="card shrink-0 space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <button
            className="btn"
            disabled={(obs?.periods || []).length < 2}
            onClick={() => {
              const periods: string[] = (obs?.periods || []).map((item: any) => item.period_end);
              if (periods.length < 2) return;
              if (!playing) {
                const idx = periods.indexOf(period);
                if (idx < 0 || idx >= periods.length - 1) setQuery({ period: periods[0] });
                setPlaying(true);
              } else {
                setPlaying(false);
              }
            }}
          >
            {playing ? "暂停" : "播放"}
          </button>
          <input
            type="range"
            className="min-w-[200px] flex-1"
            min={0}
            max={Math.max(0, (obs?.periods || []).length - 1)}
            value={Math.max(0, (obs?.periods || []).findIndex((item: any) => item.period_end === period))}
            disabled={(obs?.periods || []).length < 2}
            onChange={(ev) => {
              const item = obs?.periods?.[Number(ev.target.value)];
              if (item) setQuery({ period: item.period_end });
            }}
          />
          <span className="text-sm">{period || "无周期"}</span>
          <span className="text-xs text-slate-500">
            {(obs?.periods || []).length < 2
              ? "当前病种没有可播放的多周时序。"
              : `共 ${(obs?.periods || []).length} 周 · 锁定同一口径色阶：${obs?.classification?.lock_period_end || lockPeriod.current || period}`}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          {(obs?.legend?.bins || []).map((bin: any) => (
            <span key={bin.label} className="inline-flex items-center gap-1">
              <i className="inline-block h-3 w-4 rounded-sm" style={{ background: bin.color }} />
              {bin.label} {bin.min != null ? `(${Number(bin.min).toFixed(1)}–${bin.max != null ? Number(bin.max).toFixed(1) : ""})` : ""}
            </span>
          ))}
          {(obs?.legend?.states || []).map((item: any) => (
            <span key={item.id} className="inline-flex items-center gap-1">
              <i className="inline-block h-3 w-4 rounded-sm border" style={{ background: item.color || "transparent", borderStyle: item.outline ? "dashed" : "solid", borderColor: item.outline || "#94a3b8" }} />
              {item.label}
            </span>
          ))}
        </div>
        <p className="text-xs text-slate-500">
          {obs?.legend?.metric_name} · {obs?.legend?.unit} · 周周期 · 来源 {(obs?.legend?.sources || []).join(", ") || "无"} · 截止 {obs?.as_of || "—"}
          。颜色按可核验指标区间，不是官方严重程度。预测比较请用「预测比较」页。历史病种请用「历史资料」。
          {obs?.note ? ` ${obs.note}` : ""}
        </p>
      </footer>
    </div>
  );
}
