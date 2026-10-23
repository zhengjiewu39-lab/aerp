import { targetLabel, useWorkspace } from "../workspace";
import { StatusBadge } from "./StatusBadge";

export function WorkspaceBar() {
  const { diseases, regions, diseaseId, regionId, target, setDiseaseId, setRegionId, setTarget, disease } = useWorkspace();
  const targets = disease?.targets?.length ? disease.targets : [target];
  return (
    <section className="sticky top-0 z-20 mb-5 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-card">
      <div className="grid gap-3 md:grid-cols-4">
        <label className="text-sm">
          <div className="mb-1 text-xs text-slate-500">疾病</div>
          <select className="field" value={diseaseId} onChange={(e) => setDiseaseId(e.target.value)}>
            {diseases.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name_zh || item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-slate-500">预测目标</div>
          <select className="field" value={target} onChange={(e) => setTarget(e.target.value)}>
            {targets.map((item) => (
              <option key={item} value={item}>
                {targetLabel(item)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-slate-500">地区</div>
          <select className="field" value={regionId} onChange={(e) => setRegionId(e.target.value)} disabled={!regions.length}>
            {!regions.length ? <option value="">暂无可用地区</option> : null}
            {regions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <div className="text-sm">
          <div className="mb-1 text-xs text-slate-500">数据状态</div>
          <div className="flex min-h-[42px] flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <StatusBadge status={disease?.status || "pending"} />
            <span className="text-xs text-slate-600">{disease?.status_label || "暂无"}</span>
          </div>
        </div>
      </div>
      <p className="mt-2 text-xs text-slate-500">
        更新时间 {disease?.coverage?.updated_at || "暂无"} · 覆盖 {disease?.coverage?.start || "暂无"} 至 {disease?.coverage?.end || "暂无"} ·
        {disease?.is_synthetic ? " 合成/演示数据" : " 真实或待接入数据"}
      </p>
    </section>
  );
}
