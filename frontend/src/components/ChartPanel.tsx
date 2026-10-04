import ReactECharts from "echarts-for-react";

export function ChartPanel({
  title,
  option,
  height = 320,
  empty,
}: {
  title: string;
  option?: Record<string, unknown>;
  height?: number;
  empty?: string;
}) {
  return (
    <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4 shadow-sm backdrop-blur">
      <h3 className="mb-3 font-display text-lg text-ink">{title}</h3>
      {!option ? (
        <div className="flex h-40 items-center justify-center text-sm text-slate-500">{empty || "暂无数据"}</div>
      ) : (
        <ReactECharts option={option} style={{ height }} notMerge lazyUpdate />
      )}
    </section>
  );
}
