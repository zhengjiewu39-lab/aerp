const LABELS: Record<string, string> = {
  available: "可用",
  unconfigured: "未配置",
  unverified: "尚未验证",
  pending: "待接入",
  verified: "已验证",
  real_forecastable: "真实可预测",
  demo_only: "仅演示",
  has_page: "仅资料页",
  completed: "已完成",
  running: "运行中",
  queued: "排队中",
  failed: "失败",
  interrupted: "已中断",
  created: "已创建",
  pending_eval: "待评价",
  scored: "已评分",
  unreviewed: "未核实",
  supported: "支持",
  rejected: "驳回",
  needs_more_info: "需要更多信息",
  locked_test: "锁定测试",
  validation: "验证",
  development: "开发",
};

const TONE: Record<string, string> = {
  available: "bg-teal-50 text-teal-950 border-teal-200",
  verified: "bg-teal-50 text-teal-950 border-teal-200",
  real_forecastable: "bg-sky-50 text-sky-950 border-sky-200",
  completed: "bg-teal-50 text-teal-950 border-teal-200",
  scored: "bg-teal-50 text-teal-950 border-teal-200",
  supported: "bg-teal-50 text-teal-950 border-teal-200",
  failed: "bg-orange-50 text-orange-950 border-orange-200",
  rejected: "bg-orange-50 text-orange-950 border-orange-200",
  demo_only: "bg-amber-50 text-amber-950 border-amber-200",
  pending: "bg-slate-100 text-slate-800 border-slate-300",
  has_page: "bg-slate-100 text-slate-800 border-slate-300",
};

export function StatusBadge({ status }: { status: string }) {
  const tone = TONE[status] || "bg-slate-100 text-slate-800 border-slate-300";
  return <span className={`inline-flex rounded-md border px-2 py-0.5 text-xs ${tone}`}>{LABELS[status] || status}</span>;
}
