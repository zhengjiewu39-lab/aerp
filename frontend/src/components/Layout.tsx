import { NavLink, Outlet } from "react-router-dom";
import { Banner } from "./Banner";
import { WorkspaceBar } from "./WorkspaceBar";

const links = [
  { to: "/", label: "多病种总览" },
  { to: "/disease", label: "疾病研究" },
  { to: "/forecasts", label: "预测比较" },
  { to: "/transfer", label: "跨疾病迁移" },
  { to: "/alerts", label: "可靠性与异常" },
  { to: "/experiments", label: "研究实验" },
  { to: "/data", label: "数据管理" },
  { to: "/history", label: "历史资料" },
];

export function Layout({ banner }: { banner?: string }) {
  return (
    <div className="min-h-screen bg-mist text-ink">
      <Banner text={banner} />
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="text-xs tracking-[0.2em] text-sea">AERP · 研究工作台</div>
            <h1 className="font-display text-2xl tracking-tight text-ink md:text-3xl">多病种疫情预测研究平台</h1>
            <p className="mt-1 max-w-2xl text-sm text-slate-600">
              比较监测修订、地区共享与跨疾病迁移，而不把住院预测说成传播机制发现。
            </p>
          </div>
          <nav className="flex flex-wrap gap-2">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === "/"}
                className={({ isActive }) =>
                  `rounded-lg px-3 py-1.5 text-sm ${
                    isActive ? "bg-sea text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                  }`
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <WorkspaceBar />
        <Outlet />
      </main>
    </div>
  );
}
