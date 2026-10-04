import { NavLink, Outlet } from "react-router-dom";
import { Banner } from "./Banner";

const links = [
  { to: "/", label: "总览" },
  { to: "/data", label: "数据管理" },
  { to: "/forecasts", label: "预测比较" },
  { to: "/alerts", label: "异常详情" },
  { to: "/experiments", label: "实验工作台" },
  { to: "/history", label: "历史疫情研究" },
];

export function Layout({ banner }: { banner?: string }) {
  return (
    <div className="min-h-screen text-ink">
      <Banner text={banner} />
      <header className="border-b border-slate-200/80 bg-white/70 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.24em] text-sea">AERP</div>
            <h1 className="font-display text-3xl tracking-tight text-ink md:text-4xl">自适应疫情研究与预测平台</h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-600">
              研究原型：比较历史资料、接入周监测版本、估计报告不完整程度，并在变化识别后再更新短期住院预测。
            </p>
          </div>
          <nav className="flex flex-wrap gap-2">
            {links.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === "/"}
                className={({ isActive }) =>
                  `rounded-full px-3 py-1.5 text-sm transition ${
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
        <Outlet />
      </main>
    </div>
  );
}
