import { useEffect, useState } from "react";
import { api } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";

const DEFAULT_MODELS = [
  "persistence",
  "fixed_glm",
  "retrain_glm",
  "pooled_glm",
  "hierarchical",
  "hierarchical_dynamic",
  "equal_weight",
  "dynamic_weight",
  "adaptive",
  "analog",
];

export function Experiments() {
  const [diseases, setDiseases] = useState<any[]>([]);
  const [regions, setRegions] = useState<any[]>([]);
  const [experiments, setExperiments] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<any>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [ablationBusy, setAblationBusy] = useState(false);
  const [ablationResult, setAblationResult] = useState<any>(null);
  const [form, setForm] = useState({
    name: "演示回放",
    disease_id: "",
    region_ids: [] as string[],
    target_metric: "hospital_admissions",
    replay_mode: "vintage",
    cutoff_time: "2024-10-07",
    maturity_lag_days: 28,
    train_window_weeks: 52,
    random_seed: 42,
    models: DEFAULT_MODELS,
    holdout_region_ids: [] as string[],
    rolling_window_weeks: 52,
    ablations: {
      no_revision: false,
      no_anomaly: false,
      no_dynamic_weight: false,
      no_hierarchy: false,
      no_dynamic_update: false,
    },
  });

  const reload = () =>
    Promise.all([api.diseases(), api.regions(), api.experiments()])
      .then(async ([d, r, e]) => {
        setDiseases(d);
        setRegions(r);
        setExperiments(e);
        let cutoff = form.cutoff_time;
        if (d[0] && r[0]) {
          const overview = await api.overview(d[0].id, r[0].id);
          cutoff = overview?.quality?.cutoff_time?.slice(0, 10) || cutoff;
        }
        setForm((prev) => ({
          ...prev,
          disease_id: prev.disease_id || d[0]?.id || "",
          region_ids: prev.region_ids.length ? prev.region_ids : r.map((item: any) => item.id),
          cutoff_time: cutoff,
        }));
        if (!selectedId && e[0]) setSelectedId(e[0].id);
      })
      .catch((err) => setError(String(err.message || err)))
      .finally(() => setLoading(false));

  useEffect(() => {
    reload();
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    api
      .experiment(selectedId)
      .then(setDetail)
      .catch((err) => setError(String(err.message || err)));
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || !detail || !["queued", "running"].includes(detail.status)) return;
    const timer = setInterval(() => {
      api.experiment(selectedId).then((exp) => {
        setDetail(exp);
        if (!["queued", "running"].includes(exp.status)) {
          api.experiments().then(setExperiments);
        }
      });
    }, 2000);
    return () => clearInterval(timer);
  }, [selectedId, detail]);

  if (loading) return <Loading />;
  if (error) return <EmptyState title="实验工作台加载失败" detail={error} />;

  const modelRows = detail?.summary?.models ? Object.entries(detail.summary.models) : [];

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h2 className="font-display text-xl">配置研究实验</h2>
        <p className="mt-1 text-sm text-slate-600">
          真实历史版本回放与最终数据时间切分必须区分。缺少多发布时间时，后者不能称为真实实时回测。
        </p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="text-sm">
            名称
            <input className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="text-sm">
            疾病
            <select className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" value={form.disease_id} onChange={(e) => setForm({ ...form, disease_id: e.target.value })}>
              {diseases.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm md:col-span-2">
            地区（可多选）
            <select
              multiple
              className="mt-1 h-28 w-full rounded-lg border border-slate-300 px-3 py-2"
              value={form.region_ids}
              onChange={(e) => setForm({ ...form, region_ids: Array.from(e.target.selectedOptions).map((option) => option.value) })}
            >
              {regions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            回放模式
            <select className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" value={form.replay_mode} onChange={(e) => setForm({ ...form, replay_mode: e.target.value })}>
              <option value="vintage">真实历史版本回放</option>
              <option value="final_time_split">最终数据时间切分回放（非真实实时）</option>
            </select>
          </label>
          <label className="text-sm">
            截止时间
            <input className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" value={form.cutoff_time} onChange={(e) => setForm({ ...form, cutoff_time: e.target.value })} />
          </label>
          <label className="text-sm">
            留出地区（冷启动，可多选）
            <select
              multiple
              className="mt-1 h-24 w-full rounded-lg border border-slate-300 px-3 py-2"
              value={form.holdout_region_ids}
              onChange={(e) => setForm({ ...form, holdout_region_ids: Array.from(e.target.selectedOptions).map((option) => option.value) })}
            >
              {regions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            动态分层滚动窗口（周）
            <input
              type="number"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              value={form.rolling_window_weeks}
              onChange={(e) => setForm({ ...form, rolling_window_weeks: Number(e.target.value) })}
            />
          </label>
          <label className="text-sm">
            训练窗口（周）
            <input
              type="number"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              value={form.train_window_weeks}
              onChange={(e) => setForm({ ...form, train_window_weeks: Number(e.target.value) })}
            />
          </label>
          <label className="text-sm">
            消融（可多选关闭模块）
            <div className="mt-2 flex flex-wrap gap-3 text-xs">
              {(["no_revision", "no_anomaly", "no_dynamic_weight", "no_hierarchy", "no_dynamic_update"] as const).map((key) => (
                <label key={key} className="inline-flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={Boolean(form.ablations[key])}
                    onChange={(e) => setForm({ ...form, ablations: { ...form.ablations, [key]: e.target.checked } })}
                  />
                  {key}
                </label>
              ))}
            </div>
          </label>
        </div>
        <div className="mt-4 flex flex-wrap gap-3">
          <button
            className="rounded-lg bg-sea px-4 py-2 text-sm text-white"
            onClick={() =>
              api
                .createExperiment({ ...form, horizons: [1, 2, 3, 4] })
                .then(async (created) => {
                  setSelectedId(created.id);
                  setMessage(`已创建实验 ${created.id}`);
                  await api.runExperiment(created.id);
                  setMessage(`已启动实验 ${created.id}`);
                  return reload();
                })
                .catch((err) => setError(String(err.message || err)))
            }
          >
            创建并启动回放
          </button>
          <button
            className="rounded-lg border border-sea px-4 py-2 text-sm text-sea disabled:opacity-50"
            disabled={ablationBusy}
            onClick={async () => {
              setAblationBusy(true);
              setMessage("消融对照已提交后台，页面可继续浏览；完成后自动刷新结果。");
              try {
                const submitted = await api.ablationSuite({
                  ...form,
                  horizons: [1, 2, 3, 4],
                  name: form.name || "消融对照",
                });
                const applyResult = async (suite: any) => {
                  const result = suite.result || suite;
                  if (result?.variants) {
                    setAblationResult(result);
                    setSelectedId(result.variants?.[0]?.experiment_id || "");
                    setMessage(result.comparison?.note || "消融对照完成");
                    await reload();
                  }
                };
                if (submitted.status === "completed" && (submitted.result || submitted.variants)) {
                  await applyResult(submitted.result ? submitted : { result: submitted });
                  setAblationBusy(false);
                  return;
                }
                const suiteId = submitted.suite_id;
                const poll = async () => {
                  const suite = await api.ablationSuiteStatus(suiteId);
                  if (suite.status === "completed") {
                    await applyResult(suite);
                    setAblationBusy(false);
                    return;
                  }
                  if (suite.status === "failed" || suite.status === "interrupted") {
                    setError(suite.error_message || "消融对照失败");
                    setAblationBusy(false);
                    return;
                  }
                  setMessage(`消融对照${suite.status === "running" ? "运行中" : "排队中"}…可继续使用其他页面。`);
                  setTimeout(poll, 2000);
                };
                setTimeout(poll, 1500);
              } catch (err: any) {
                setError(String(err.message || err));
                setAblationBusy(false);
              }
            }}
          >
            {ablationBusy ? "消融对照后台运行中…" : "一键运行消融对照"}
          </button>
          {selectedId ? (
            <a className="rounded-lg border border-slate-300 px-4 py-2 text-sm" href={api.exportUrl(selectedId)}>
              导出当前实验结果
            </a>
          ) : null}
        </div>
        {message ? <p className="mt-3 text-sm text-teal-800">{message}</p> : null}
        {ablationResult ? (
          <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
            <table className="min-w-full text-left text-sm">
              <thead>
                <tr className="border-b bg-mist text-slate-500">
                  <th className="px-3 py-2">对照</th>
                  <th className="px-3 py-2">适应 WIS</th>
                  <th className="px-3 py-2">等权 WIS</th>
                  <th className="px-3 py-2">适应 MAE</th>
                  <th className="px-3 py-2">实验 ID</th>
                </tr>
              </thead>
              <tbody>
                {ablationResult.variants.map((row: any) => (
                  <tr key={row.key} className="border-b border-slate-100">
                    <td className="px-3 py-2">{row.label}</td>
                    <td className="px-3 py-2">{row.adaptive_wis?.toFixed?.(2) ?? "—"}</td>
                    <td className="px-3 py-2">{row.equal_weight_wis?.toFixed?.(2) ?? "—"}</td>
                    <td className="px-3 py-2">{row.adaptive_mae?.toFixed?.(2) ?? "—"}</td>
                    <td className="px-3 py-2">
                      <button className="text-sea underline" onClick={() => setSelectedId(row.experiment_id)}>
                        {row.experiment_id}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="px-3 py-2 text-xs text-slate-500">
              {ablationResult.comparison?.note}
              {ablationResult.comparison?.full_beats_ablations === false
                ? " 本次完整系统并未全面优于消融变体。"
                : ablationResult.comparison?.full_beats_ablations === true
                  ? " 本次完整系统在适应臂 WIS 上不差于各消融。"
                  : ""}
            </p>
          </div>
        ) : null}
      </section>

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
          <h3 className="font-display text-lg">实验列表</h3>
          <div className="mt-3 space-y-2">
            {experiments.map((item) => (
              <button
                key={item.id}
                className={`w-full rounded-xl border px-3 py-2 text-left text-sm ${selectedId === item.id ? "border-sea bg-teal-50" : "border-slate-200"}`}
                onClick={() => setSelectedId(item.id)}
              >
                <div className="font-medium">{item.name}</div>
                <div className="mt-1 flex items-center gap-2">
                  <StatusBadge status={item.status} />
                  <span className="text-xs text-slate-500">{item.replay_mode}</span>
                </div>
              </button>
            ))}
          </div>
        </section>
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
          <h3 className="font-display text-lg">结果摘要</h3>
          {!detail ? (
            <EmptyState title="未选择实验" />
          ) : (
            <div className="mt-3 space-y-3 text-sm">
              <div className="flex flex-wrap gap-2">
                <StatusBadge status={detail.status} />
                <span>配置哈希 {String(detail.config_hash || "").slice(0, 12)}…</span>
                <span>输入哈希 {String(detail.input_hash || "").slice(0, 12)}…</span>
              </div>
              {detail.error_message ? <pre className="overflow-x-auto rounded-xl bg-orange-50 p-3 text-xs text-orange-900">{detail.error_message}</pre> : null}
              <p className="text-slate-600">{detail.summary?.replay?.warning || detail.summary?.note}</p>
              <div className="overflow-x-auto">
                <table className="min-w-full text-left">
                  <thead>
                    <tr className="border-b text-slate-500">
                      <th className="py-2 pr-3">模型</th>
                      <th className="py-2 pr-3">n</th>
                      <th className="py-2 pr-3">WIS</th>
                      <th className="py-2 pr-3">MAE</th>
                      <th className="py-2 pr-3">95% 覆盖</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modelRows.map(([modelId, row]: any) => (
                      <tr key={modelId} className="border-b border-slate-100">
                        <td className="py-2 pr-3">{modelId}</td>
                        <td className="py-2 pr-3">{row.n}</td>
                        <td className="py-2 pr-3">{row.wis?.toFixed?.(2)}</td>
                        <td className="py-2 pr-3">{row.mae?.toFixed?.(2)}</td>
                        <td className="py-2 pr-3">{((row.coverage_95 || 0) * 100).toFixed(0)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {detail.summary?.alert_skill ? (
                <div className="rounded-xl border border-slate-200 px-3 py-2">
                  预警精确率 {detail.summary.alert_skill.precision?.toFixed?.(2) ?? "—"} · 召回率 {detail.summary.alert_skill.recall?.toFixed?.(2) ?? "—"}
                </div>
              ) : (
                <div className="rounded-xl border border-slate-200 px-3 py-2 text-slate-600">没有可信异常标签时，不计算误报率或召回率。</div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
