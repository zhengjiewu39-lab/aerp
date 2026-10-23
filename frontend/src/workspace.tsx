import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api/client";

type Disease = {
  id: string;
  name: string;
  name_zh?: string;
  status?: string;
  status_label?: string;
  is_synthetic?: boolean;
  default_target?: string;
  targets?: string[];
  coverage?: { n_regions?: number; start?: string; end?: string; updated_at?: string; metrics?: string[] } | null;
  transmission?: string;
  [key: string]: any;
};

type Region = { id: string; name: string };

type Workspace = {
  diseases: Disease[];
  regions: Region[];
  diseaseId: string;
  regionId: string;
  target: string;
  setDiseaseId: (id: string) => void;
  setRegionId: (id: string) => void;
  setTarget: (id: string) => void;
  disease: Disease | undefined;
  loading: boolean;
  error: string;
};

const Ctx = createContext<Workspace | null>(null);
const TARGET_LABELS: Record<string, string> = {
  hospital_admissions: "周住院数",
  weekly_cases: "周病例数",
  positivity: "阳性率",
  threshold_risk: "超阈风险",
};

export function targetLabel(id: string) {
  return TARGET_LABELS[id] || id;
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [diseases, setDiseases] = useState<Disease[]>([]);
  const [regions, setRegions] = useState<Region[]>([]);
  const [diseaseId, setDiseaseIdState] = useState("");
  const [regionId, setRegionId] = useState("");
  const [target, setTarget] = useState("hospital_admissions");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const generation = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    const load = () => {
      attempts += 1;
      api
        .diseases()
        .then((rows) => {
          if (cancelled) return;
          setDiseases(rows);
          setError("");
          const preferred = rows.find((item: Disease) => item.id === "synth-ari") || rows[0];
          if (preferred) setDiseaseIdState((current) => current || preferred.id);
        })
        .catch((err) => {
          if (cancelled) return;
          setError(String(err.message || err));
          if (attempts < 8) setTimeout(load, 750);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    };
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!diseaseId) return;
    const current = ++generation.current;
    const spec = diseases.find((item) => item.id === diseaseId);
    const allowed = spec?.targets || [];
    if (allowed.length && !allowed.includes(target)) {
      setTarget(spec?.default_target || allowed[0]);
    } else if (spec?.default_target && !target) {
      setTarget(spec.default_target);
    }
    api
      .regions(diseaseId)
      .then((rows) => {
        if (current !== generation.current) return;
        setRegions(rows);
        setRegionId((prev) => (rows.some((item: Region) => item.id === prev) ? prev : rows[0]?.id || ""));
      })
      .catch((err) => {
        if (current !== generation.current) return;
        setError(String(err.message || err));
      });
  }, [diseaseId, diseases]);

  const value = useMemo(
    () => ({
      diseases,
      regions,
      diseaseId,
      regionId,
      target,
      setDiseaseId: (id: string) => setDiseaseIdState(id),
      setRegionId,
      setTarget,
      disease: diseases.find((item) => item.id === diseaseId),
      loading,
      error,
    }),
    [diseases, regions, diseaseId, regionId, target, loading, error],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("Workspace 未初始化");
  return ctx;
}
