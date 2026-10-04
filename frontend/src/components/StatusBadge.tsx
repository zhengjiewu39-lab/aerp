const LABELS: Record<string, string> = {
  available: "可用",
  unconfigured: "未配置",
  unverified: "尚未验证",
  manual_import: "手动导入",
  completed: "已完成",
  running: "运行中",
  queued: "排队中",
  failed: "失败",
  interrupted: "已中断",
  created: "已创建",
  pending: "待评价",
  scored: "已评分",
  unreviewed: "未核实",
  supported: "支持",
  rejected: "驳回",
  needs_more_info: "需要更多信息",
};

export function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "available" || status === "completed" || status === "scored" || status === "supported"
      ? "bg-teal-50 text-teal-900 border-teal-200"
      : status === "failed" || status === "rejected"
        ? "bg-orange-50 text-orange-900 border-orange-200"
        : "bg-slate-100 text-slate-700 border-slate-200";
  return <span className={`inline-flex rounded-md border px-2 py-0.5 text-xs ${tone}`}>{LABELS[status] || status}</span>;
}
