import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { ChartPanel } from "../components/ChartPanel";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";

export function DataManage() {
  const [imports, setImports] = useState<any[]>([]);
  const [sources, setSources] = useState<any[]>([]);
  const [connectors, setConnectors] = useState<any[]>([]);
  const [completeness, setCompleteness] = useState<any>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const reload = () =>
    Promise.all([api.imports(), api.sources(), api.connectors(), api.diseases(), api.regions()])
      .then(async ([imp, src, conn, diseases, regions]) => {
        setImports(imp);
        setSources(src);
        setConnectors(conn);
        if (diseases[0] && regions[0]) {
          setCompleteness(await api.completeness(diseases[0].id, regions[0].id));
        }
      })
      .catch((err) => setError(String(err.message || err)))
      .finally(() => setLoading(false));

  useEffect(() => {
    reload();
  }, []);

  const completenessOption = useMemo(() => {
    const rows = completeness?.matrix || [];
    if (!rows.length) return undefined;
    return {
      tooltip: { trigger: "axis" },
      legend: { data: ["完整", "暂定", "缺失"] },
      grid: { left: 40, right: 20, top: 40, bottom: 40 },
      xAxis: { type: "category", data: rows.map((row: any) => row.period_end) },
      yAxis: { type: "value", max: 1 },
      series: [
        {
          name: "完整",
          type: "bar",
          stack: "v",
          data: rows.map((row: any) => (row.hospital_admissions === "complete" ? 1 : 0)),
        },
        {
          name: "暂定",
          type: "bar",
          stack: "v",
          data: rows.map((row: any) => (row.hospital_admissions === "provisional" ? 1 : 0)),
        },
        {
          name: "缺失",
          type: "bar",
          stack: "v",
          data: rows.map((row: any) => (row.hospital_admissions === "missing" || row.hospital_admissions === "absent" ? 1 : 0)),
        },
      ],
    };
  }, [completeness]);

  if (loading) return <Loading />;
  if (error) return <EmptyState title="数据管理加载失败" detail={error} />;

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h2 className="font-display text-xl">CSV 导入</h2>
        <p className="mt-1 text-sm text-slate-600">请先下载模板，预览校验后再导入。同一周期多个发布时间版本会全部保留。</p>
        <div className="mt-4 flex flex-wrap gap-3">
          <a className="rounded-lg bg-sea px-4 py-2 text-sm text-white" href={api.templateUrl()}>
            下载模板
          </a>
          <button
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm"
            onClick={() =>
              api
                .seedDemo()
                .then((result) => {
                  setMessage(result.message || `已处理合成数据（seed=${result.seed}）。`);
                  return reload();
                })
                .catch((err) => setError(String(err.message || err)))
            }
          >
            重新生成合成演示数据
          </button>
          <button
            className="btn"
            onClick={() =>
              api
                .seedMeasles()
                .then((result) => {
                  setMessage(result.message || "已写入麻疹合成示例。");
                  return reload();
                })
                .catch((err) => setError(String(err.message || err)))
            }
          >
            生成麻疹合成示例
          </button>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto_auto]">
          <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <button
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm"
            disabled={!file}
            onClick={() => file && api.previewCsv(file).then(setPreview).catch((err) => setError(String(err.message || err)))}
          >
            预览校验
          </button>
          <button
            className="rounded-lg bg-ink px-4 py-2 text-sm text-white disabled:opacity-40"
            disabled={!file || !preview || preview.errors?.length}
            onClick={() =>
              file &&
              api
                .importCsv(file, "用户 CSV 导入")
                .then((result) => {
                  setMessage(`导入成功：批次 ${result.import_batch_id || result.batch_id}`);
                  setPreview(null);
                  return reload();
                })
                .catch((err) => setError(String(err.message || err)))
            }
          >
            确认导入
          </button>
        </div>
        {message ? <p className="mt-3 text-sm text-teal-800">{message}</p> : null}
        {preview ? (
          <div className="mt-4 space-y-2 text-sm">
            <div>
              文件哈希：<code>{preview.file_hash}</code> · 错误 {preview.errors?.length ?? 0} · 可接受 {preview.accepted_row_count ?? preview.valid_rows?.length ?? 0}
            </div>
            {preview.errors?.length ? (
              <ul className="list-disc space-y-1 pl-5 text-orange-800">
                {preview.errors.slice(0, 20).map((item: any, index: number) => (
                  <li key={index}>
                    第 {item.row} 行 · {item.column}：{item.message}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-teal-800">校验通过。缺失值保持为空，不会写成 0。</p>
            )}
          </div>
        ) : null}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
          <h3 className="font-display text-lg">导入历史</h3>
          <div className="mt-3 space-y-2">
            {imports.map((item) => (
              <div key={item.id} className="rounded-xl border border-slate-200 px-3 py-2 text-sm">
                <div className="font-medium">
                  {item.filename} {item.is_synthetic ? "· 合成" : ""}
                </div>
                <div className="text-slate-500">
                  {item.imported_at} · {item.row_count} 行 · 哈希 {String(item.file_hash || "").slice(0, 12)}…
                </div>
              </div>
            ))}
          </div>
        </section>
        <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
          <h3 className="font-display text-lg">来源与连接器</h3>
          <div className="mt-3 space-y-2">
            {[...sources.map((s) => ({ ...s, status: s.connector_status })), ...connectors].map((item: any) => (
              <div key={`${item.id}-${item.name}`} className="flex items-start justify-between gap-3 rounded-xl border border-slate-200 px-3 py-2 text-sm">
                <div>
                  <div className="font-medium">{item.name}</div>
                  <div className="text-slate-500">{item.note || item.update_note || item.license}</div>
                </div>
                <StatusBadge status={item.status || item.connector_status} />
              </div>
            ))}
          </div>
        </section>
      </div>

      <ChartPanel title="数据完整性（住院）" option={completenessOption} empty={completeness?.note || "暂无完整性矩阵"} />
    </div>
  );
}
