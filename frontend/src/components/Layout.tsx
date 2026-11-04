import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Banner } from "./Banner";
import { WorkspaceBar } from "./WorkspaceBar";

const links = [
  { to: "/", label: "全球地图" },
  { to: "/overview", label: "研究总览" },
  { to: "/disease", label: "疾病研究" },
  { to: "/forecasts", label: "预测比较" },
  { to: "/transfer", label: "跨疾病迁移" },
  { to: "/alerts", label: "可靠性与异常" },
  { to: "/experiments", label: "研究实验" },
  { to: "/data", label: "数据管理" },
  { to: "/history", label: "历史资料" },
];

export function Layout({ banner }: { banner?: string }) {
  const location = useLocation();
  const isMap = location.pathname === "/";
  return (
    <div className={`bg-mist text-ink ${isMap ? "flex h-screen flex-col overflow-hidden" : "min-h-screen"}`}>
      <Banner text={banner} />
      <header className="border-b border-slate-200 bg-white">
        <div className={`mx-auto flex flex-col gap-2 px-4 py-2 lg:flex-row lg:items-end lg:justify-between ${isMap ? "max-w-none" : "max-w-7xl lg:gap-3 lg:py-3"}`}>
          <div>
            <div className="text-xs tracking-[0.2em] text-sea">AERP · 研究工作台</div>
            <h1 className={`font-display tracking-tight text-ink ${isMap ? "text-lg md:text-xl" : "text-xl md:text-2xl"}`}>
              {isMap ? "全球交互式疾病地图工作台" : "多病种疫情预测研究平台"}
            </h1>
            <p className="mt-1 max-w-3xl text-sm text-slate-600">
              {isMap
                ? "全球底图完整不代表疾病数据完整。无数据、零值、过期和未报告分开显示。AI 草稿须人工确认。"
                : "比较监测修订、地区共享与跨疾病迁移，而不把住院预测说成传播机制发现。"}
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
      <main className={isMap ? "mx-auto flex min-h-0 w-full max-w-none flex-1 flex-col overflow-hidden px-4 py-2" : "mx-auto max-w-7xl px-4 py-6"}>
        {isMap ? null : <WorkspaceBar />}
        <Outlet />
      </main>
    </div>
  );
}
