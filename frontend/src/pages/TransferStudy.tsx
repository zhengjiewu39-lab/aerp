import { useEffect, useState } from "react";
import { api } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";
import { useWorkspace } from "../workspace";

function fmt(value: number | null | undefined) {
  return value == null || Number.isNaN(value) ? "暂无" : value.toFixed(2);
}

export function TransferStudy() {
  const { disease } = useWorkspace();
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .transferDemo()
      .then(setResult)
      .catch((err) => setError(String(err.message || err)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Loading label="正在运行合成迁移对照…" />;
  if (error) return <EmptyState title="迁移实验失败" detail={error} />;
  if (!result) return <EmptyState title="暂无迁移结果" />;

  const comparable = disease?.transmission === "respiratory_person_to_person" && disease?.default_target === "hospital_admissions";

  return (
    <div className="space-y-4">
      <section className="card">
        <h2 className="font-display text-2xl">跨疾病迁移实验</h2>
        <p className="mt-2 text-sm text-slate-600">
          当前选择：{disease?.name_zh || "未选"}。{comparable ? "属于可比呼吸道住院目标范围。" : "当前疾病不在第一阶段可比呼吸道住院范围内，或数据不足。"}
        </p>
        <p className="mt-2 text-xs text-slate-500">{result.note}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <StatusBadge status={result.data_mode === "synthetic_stress_test" ? "demo_only" : "verified"} />
          <span className="text-xs text-slate-500">数据模式 {result.data_mode}</span>
        </div>
      </section>

      <section className="card">
        <h3 className="font-display text-lg">可比性检查</h3>
        <ul className="mt-2 list-disc pl-5 text-sm text-slate-700">
          <li>目标疾病 {result.target_disease_id}，来源 { (result.source_disease_ids || []).join("、") }</li>
          <li>排除目标疾病训练：{result.exclude_target_from_training ? "是" : "否"}</li>
          <li>尺度变换只用训练可见中位数，不拼接原始计数。</li>
        </ul>
      </section>

      <section className="card overflow-x-auto">
        <h3 className="mb-3 font-display text-lg">本地 / 固定迁移 / 可退出迁移</h3>
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">模型</th>
              <th className="px-3 py-2">n</th>
              <th className="px-3 py-2">WIS</th>
              <th className="px-3 py-2">MAE</th>
              <th className="px-3 py-2">80% 覆盖</th>
            </tr>
          </thead>
          <tbody>
            {["transfer_local", "transfer_fixed", "transfer_exit"].map((key) => {
              const row = result.models?.[key] || {};
              return (
                <tr key={key} className="border-t border-slate-200">
                  <td className="px-3 py-2">{key}</td>
                  <td className="px-3 py-2">{row.n ?? "暂无"}</td>
                  <td className="px-3 py-2">{fmt(row.wis)}</td>
                  <td className="px-3 py-2">{fmt(row.mae)}</td>
                  <td className="px-3 py-2">{fmt(row.coverage_80)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h3 className="font-display text-lg">负迁移任务</h3>
        <p className="mt-1 text-sm text-slate-600">发生率 {fmt(result.negative_transfer_rate)}。退出是否减少损失：{result.exit_reduced_loss ? "是（合成对照）" : "否或尚无"}。</p>
        {!(result.negative_transfer_tasks || []).length ? (
          <p className="mt-2 text-sm text-slate-500">暂无负迁移任务。</p>
        ) : (
          <ul className="mt-2 space-y-1 text-sm">
            {result.negative_transfer_tasks.slice(0, 12).map((item: any) => (
              <li key={item.cutoff_time}>
                {item.cutoff_time} · 本地 {fmt(item.local_wis)} · 固定迁移 {fmt(item.fixed_wis)}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
