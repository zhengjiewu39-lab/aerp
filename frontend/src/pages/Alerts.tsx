import { useEffect, useState } from "react";
import { api } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";

const CATEGORY_LABELS: Record<string, string> = {
  suspected_reporting_change: "疑似报告过程变化",
  suspected_epidemic_change: "疑似疫情动态变化",
  data_quality: "数据质量问题",
  insufficient_evidence: "证据不足",
};

const REVIEW_OPTIONS = [
  { value: "unreviewed", label: "未核实" },
  { value: "supported", label: "支持" },
  { value: "rejected", label: "驳回" },
  { value: "needs_more_info", label: "需要更多信息" },
];

export function Alerts() {
  const [alerts, setAlerts] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const reload = () =>
    api
      .alerts()
      .then(setAlerts)
      .catch((err) => setError(String(err.message || err)))
      .finally(() => setLoading(false));

  useEffect(() => {
    reload();
  }, []);

  if (loading) return <Loading />;
  if (error) return <EmptyState title="异常详情加载失败" detail={error} />;
  if (!alerts.length) return <EmptyState title="暂无异常记录" detail="运行实验回放后，这里会显示可解释的触发规则与证据。" />;

  return (
    <div className="space-y-4">
      {alerts.map((alert) => (
        <article key={alert.id} className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={alert.review_status} />
            <h2 className="font-display text-xl">{CATEGORY_LABELS[alert.category] || alert.category}</h2>
            <span className="text-sm text-slate-500">
              {alert.region_id} · {alert.period_start} → {alert.period_end}
            </span>
          </div>
          <dl className="mt-4 grid gap-3 text-sm md:grid-cols-2">
            <div>
              <dt className="text-slate-500">触发规则</dt>
              <dd className="mt-1 font-medium">{alert.rule}</dd>
            </div>
            <div>
              <dt className="text-slate-500">相关指标</dt>
              <dd className="mt-1">{(alert.metrics || []).join("、")}</dd>
            </div>
            <div>
              <dt className="text-slate-500">来源 / 导入批次</dt>
              <dd className="mt-1">
                {alert.source_id || "无"} / {alert.import_batch_id || "无"}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">截止时间</dt>
              <dd className="mt-1">{alert.cutoff_time || "—"}</dd>
            </div>
            <div className="md:col-span-2">
              <dt className="text-slate-500">支持证据</dt>
              <dd className="mt-1">{alert.supporting_evidence}</dd>
            </div>
            <div className="md:col-span-2">
              <dt className="text-slate-500">冲突证据</dt>
              <dd className="mt-1">{alert.conflicting_evidence}</dd>
            </div>
            <div className="md:col-span-2">
              <dt className="text-slate-500">适用限制</dt>
              <dd className="mt-1 text-slate-600">{alert.limitations}</dd>
            </div>
          </dl>
          <div className="mt-4 flex flex-wrap gap-2">
            {REVIEW_OPTIONS.map((option) => (
              <button
                key={option.value}
                className={`rounded-lg px-3 py-1.5 text-sm ${
                  alert.review_status === option.value ? "bg-sea text-white" : "border border-slate-300"
                }`}
                onClick={() =>
                  api
                    .patchAlert(alert.id, option.value)
                    .then(reload)
                    .catch((err) => setError(String(err.message || err)))
                }
              >
                {option.label}
              </button>
            ))}
          </div>
        </article>
      ))}
    </div>
  );
}
