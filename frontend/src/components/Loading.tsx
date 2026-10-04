export function Loading({ label = "加载中…" }: { label?: string }) {
  return <div className="rounded-xl border border-slate-200 bg-white/70 px-4 py-8 text-center text-slate-600">{label}</div>;
}
