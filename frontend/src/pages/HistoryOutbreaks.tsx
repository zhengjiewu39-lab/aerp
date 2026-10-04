import { useEffect, useState } from "react";
import { api } from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { Loading } from "../components/Loading";
import { StatusBadge } from "../components/StatusBadge";

export function HistoryOutbreaks() {
  const [cards, setCards] = useState<any[]>([]);
  const [transfer, setTransfer] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api.history(), api.meta()])
      .then(([history, meta]) => {
        setCards(history);
        setTransfer(meta.transfer);
      })
      .catch((err) => setError(String(err.message || err)))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Loading />;
  if (error) return <EmptyState title="历史资料加载失败" detail={error} />;

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
        <h2 className="font-display text-xl">跨疾病迁移扩展</h2>
        <p className="mt-2 text-sm text-slate-600">
          {transfer?.message || "研究扩展，尚未实现"}。保留不迁移基线，禁止伪造迁移输出。
        </p>
        <div className="mt-3">
          <StatusBadge status="unconfigured" />
        </div>
      </section>
      <div className="grid gap-4 md:grid-cols-2">
        {cards.map((card) => (
          <article key={card.id} className="rounded-2xl border border-slate-200/80 bg-white/80 p-4">
            <div className="text-xs uppercase tracking-[0.2em] text-sea">历史资料卡</div>
            <h3 className="mt-2 font-display text-2xl">{card.name}</h3>
            <p className="mt-1 text-sm text-slate-500">{card.period_note}</p>
            <p className="mt-3 text-sm leading-6 text-slate-700">{card.summary}</p>
            <div className="mt-4 rounded-xl bg-mist px-3 py-2 text-sm">
              <div className="font-medium">适用范围</div>
              <p className="mt-1 text-slate-600">{card.scope}</p>
            </div>
            <div className="mt-4 text-sm">
              <div className="font-medium">来源</div>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-slate-600">
                {(card.sources || []).map((source: any) => (
                  <li key={source.url}>
                    <a className="underline decoration-slate-300" href={source.url} target="_blank" rel="noreferrer">
                      {source.title}
                    </a>
                    {source.note ? ` · ${source.note}` : ""}
                  </li>
                ))}
              </ul>
            </div>
            <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
              <div className="rounded-xl border border-slate-200 px-3 py-2">
                <div className="font-medium">可训练时序</div>
                <p className="mt-1 text-slate-600">{card.trainable_series_available ? "已提供" : "未提供（与资料卡分开）"}</p>
              </div>
              <div className="rounded-xl border border-slate-200 px-3 py-2">
                <div className="font-medium">待补充字段</div>
                <p className="mt-1 text-slate-600">{(card.pending_fields || []).join("、") || "无"}</p>
              </div>
            </div>
            <p className="mt-3 text-xs text-slate-500">{card.notes}</p>
          </article>
        ))}
      </div>
    </div>
  );
}
