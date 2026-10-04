import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { Layout } from "./components/Layout";
import { Alerts } from "./pages/Alerts";
import { DataManage } from "./pages/DataManage";
import { Experiments } from "./pages/Experiments";
import { ForecastCompare } from "./pages/ForecastCompare";
import { HistoryOutbreaks } from "./pages/HistoryOutbreaks";
import { Overview } from "./pages/Overview";

export default function App() {
  const [banner, setBanner] = useState("合成演示数据。模拟实验成功不代表现实世界有效。");

  useEffect(() => {
    api
      .health()
      .then((payload) => setBanner(payload.banner || payload.disclaimer))
      .catch(() => undefined);
  }, []);

  return (
    <Routes>
      <Route element={<Layout banner={banner} />}>
        <Route index element={<Overview />} />
        <Route path="data" element={<DataManage />} />
        <Route path="forecasts" element={<ForecastCompare />} />
        <Route path="alerts" element={<Alerts />} />
        <Route path="experiments" element={<Experiments />} />
        <Route path="history" element={<HistoryOutbreaks />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
