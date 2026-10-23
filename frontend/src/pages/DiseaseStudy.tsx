import { useEffect, useState } from "react";
import { api } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspace } from "../workspace";

export function DiseaseStudy() {
  const { diseaseId, regionId, target, disease } = useWorkspace();
  const [overview, setOverview] = useState<any>(null);
  const [recommend, setRecommend] = useState<any>(null);
  const [completeness, setCompleteness] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!diseaseId) return;
    setLoading(true);
    const tasks = [api.recommend(diseaseId, target)];
    if (regionId) {
      tasks.push(api.overview(diseaseId, regionId, undefined, target));
      tasks.push(api.completeness(diseaseId, regionId));
    }
    Promise.all(tasks)
      .then((payload) => {
        setRecommend(payload[0]);
        setOverview(payload[1] || null);
        setCompleteness(payload[2] || null);
      })
      .catch((err) => setError(String(err.message || err)))
      .finally(() => setLoading(false));
  }, [diseaseId, regionId, target]);

  if (loading && !disease) return <Loading />;
  if (error) return <EmptyState title="疾病研究页加载失败" detail={error} />;
  if (!disease) return <EmptyState title="请选择疾病" />;

  return (
    <div className="space-y-4">
      <section className="card">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-display text-2xl">{disease.name_zh || disease.name}</h2>
          <StatusBadge status={disease.status || "pending"} />
        </div>
        <p className="mt-2 text-sm text-slate-600">{disease.status_label}</p>
        <dl className="mt-4 grid gap-3 text-sm md:grid-cols-2">
          <div>
            <div className="text-xs text-slate-500">传播机制</div>
            <p>{disease.transmission === "vector_borne" ? "媒介传播" : disease.transmission === "respiratory_person_to_person" ? "呼吸道人际传播" : "其他/扩展接口"}</p>
          </div>
          <div>
            <div className="text-xs text-slate-500">监测定义</div>
            <p>{disease.surveillance_definition || "暂无"}</p>
          </div>
          <div className="md:col-span-2">
            <div className="text-xs text-slate-500">定义变化</div>
            <p>{disease.surveillance_changes || "暂无"}</p>
          </div>
          <div className="md:col-span-2">
            <div className="text-xs text-slate-500">适用限制</div>
            <p>{disease.uncertainty_limits || "暂无"}</p>
          </div>
        </dl>
      </section>

      <section className="card">
        <h3 className="font-display text-lg">数据来源</h3>
        <ul className="mt-3 space-y-2 text-sm">
          {(disease.sources || []).map((source: any) => (
            <li key={source.id} className="rounded-lg border border-slate-200 px-3 py-2">
              <div className="font-medium">{source.name}</div>
              <div className="text-xs text-slate-500">{source.field}</div>
              {source.url ? (
                <a className="text-xs text-sea underline" href={source.url} target="_blank" rel="noreferrer">
                  {source.url}
                </a>
              ) : null}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-slate-500">{disease.license_note}</p>
      </section>

      <section className="card">
        <h3 className="font-display text-lg">候选模型及理由</h3>
        <p className="mt-2 text-sm text-slate-600">{recommend?.note}</p>
        <ul className="mt-3 list-disc pl-5 text-sm text-slate-700">
          {(recommend?.reasons || []).map((item: string) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-slate-500">候选：{(recommend?.candidates || []).join("、") || "暂无"}。所有推荐仍需与基线比较。</p>
      </section>

      <section className="card">
        <h3 className="font-display text-lg">数据完整性与修订</h3>
        <p className="text-sm text-slate-600">{completeness?.note || overview?.revision?.message || "暂无"}</p>
        <p className="mt-2 text-xs text-slate-500">
          历史资料与可训练序列分区：{disease.trainable ? "可训练（以实际覆盖为准）" : "仅历史资料，不作为真实训练集"}。
        </p>
      </section>
    </div>
  );
}
