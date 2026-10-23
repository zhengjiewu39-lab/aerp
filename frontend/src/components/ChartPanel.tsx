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
  const merged = option
    ? {
        toolbox: {
          feature: {
            dataZoom: { title: { zoom: "缩放", back: "还原" } },
            saveAsImage: { title: "导出", name: title },
          },
        },
        ...option,
      }
    : undefined;
  return (
    <section className="card">
      <h3 className="mb-3 font-display text-lg text-ink">{title}</h3>
      {!merged ? (
        <div className="flex h-40 items-center justify-center text-sm text-slate-500">{empty || "暂无"}</div>
      ) : (
        <ReactECharts option={merged} style={{ height }} notMerge lazyUpdate />
      )}
    </section>
  );
}
