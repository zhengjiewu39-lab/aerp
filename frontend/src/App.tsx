import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { Layout } from "./components/Layout";
import { WorkspaceProvider } from "./workspace";

const MapWorkbench = lazy(() => import("./pages/MapWorkbench").then((mod) => ({ default: mod.MapWorkbench })));
const Overview = lazy(() => import("./pages/Overview").then((mod) => ({ default: mod.Overview })));
const DiseaseStudy = lazy(() => import("./pages/DiseaseStudy").then((mod) => ({ default: mod.DiseaseStudy })));
const DataManage = lazy(() => import("./pages/DataManage").then((mod) => ({ default: mod.DataManage })));
const ForecastCompare = lazy(() => import("./pages/ForecastCompare").then((mod) => ({ default: mod.ForecastCompare })));
const TransferStudy = lazy(() => import("./pages/TransferStudy").then((mod) => ({ default: mod.TransferStudy })));
const Alerts = lazy(() => import("./pages/Alerts").then((mod) => ({ default: mod.Alerts })));
const Experiments = lazy(() => import("./pages/Experiments").then((mod) => ({ default: mod.Experiments })));
const HistoryOutbreaks = lazy(() => import("./pages/HistoryOutbreaks").then((mod) => ({ default: mod.HistoryOutbreaks })));

export default function App() {
  const [banner, setBanner] = useState("合成演示数据。模拟实验成功不代表现实世界有效。");

  useEffect(() => {
    api
      .health()
      .then((payload) => setBanner(payload.banner || payload.disclaimer))
      .catch(() => undefined);
  }, []);

  return (
    <WorkspaceProvider>
      <Routes>
        <Route element={<Layout banner={banner} />}>
          <Route
            index
            element={
              <Suspense fallback={<p className="p-4 text-sm">正在加载地图…</p>}>
                <MapWorkbench />
              </Suspense>
            }
          />
          <Route path="overview" element={<Suspense fallback={<p className="p-4 text-sm">加载中…</p>}><Overview /></Suspense>} />
          <Route path="disease" element={<Suspense fallback={<p className="p-4 text-sm">加载中…</p>}><DiseaseStudy /></Suspense>} />
          <Route path="data" element={<Suspense fallback={<p className="p-4 text-sm">加载中…</p>}><DataManage /></Suspense>} />
          <Route path="forecasts" element={<Suspense fallback={<p className="p-4 text-sm">加载中…</p>}><ForecastCompare /></Suspense>} />
          <Route path="transfer" element={<Suspense fallback={<p className="p-4 text-sm">加载中…</p>}><TransferStudy /></Suspense>} />
          <Route path="alerts" element={<Suspense fallback={<p className="p-4 text-sm">加载中…</p>}><Alerts /></Suspense>} />
          <Route path="experiments" element={<Suspense fallback={<p className="p-4 text-sm">加载中…</p>}><Experiments /></Suspense>} />
          <Route path="history" element={<Suspense fallback={<p className="p-4 text-sm">加载中…</p>}><HistoryOutbreaks /></Suspense>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </WorkspaceProvider>
  );
}
