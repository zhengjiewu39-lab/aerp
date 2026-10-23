export function Loading({ label = "加载中…" }: { label?: string }) {
  return (
    <div className="card animate-pulse text-center text-slate-600" aria-busy="true">
      {label}
    </div>
  );
}
