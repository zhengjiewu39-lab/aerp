const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail?.message || payload.detail || payload.message || JSON.stringify(payload);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    return response.json() as Promise<T>;
  }
  return response.text() as Promise<T>;
}

export const api = {
  health: () => request<any>("/api/health"),
  meta: () => request<any>("/api/meta"),
  diseases: () => request<any[]>("/api/diseases"),
  regions: () => request<any[]>("/api/regions"),
  sources: () => request<any[]>("/api/sources"),
  connectors: () => request<any[]>("/api/connectors"),
  models: () => request<any>("/api/models"),
  history: () => request<any[]>("/api/history-cards"),
  analogs: () => request<any>("/api/analogs"),
  analogMatch: (body: Record<string, unknown>) =>
    request<any>("/api/analogs/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  overview: (diseaseId: string, regionId: string, cutoff?: string) => {
    const q = new URLSearchParams({ disease_id: diseaseId, region_id: regionId });
    if (cutoff) q.set("cutoff", cutoff);
    return request<any>(`/api/overview?${q}`);
  },
  imports: () => request<any[]>("/api/imports"),
  completeness: (diseaseId: string, regionId: string) =>
    request<any>(`/api/completeness?disease_id=${encodeURIComponent(diseaseId)}&region_id=${encodeURIComponent(regionId)}`),
  alerts: (experimentId?: string) =>
    request<any[]>(experimentId ? `/api/alerts?experiment_id=${encodeURIComponent(experimentId)}` : "/api/alerts"),
  patchAlert: (id: number, review_status: string) =>
    request<any>(`/api/alerts/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ review_status }),
    }),
  experiments: () => request<any[]>("/api/experiments"),
  createExperiment: (body: Record<string, unknown>) =>
    request<any>("/api/experiments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  runExperiment: (id: string) => request<any>(`/api/experiments/${id}/run`, { method: "POST" }),
  experiment: (id: string) => request<any>(`/api/experiments/${id}`),
  ablationSuite: (body: Record<string, unknown>) =>
    request<any>("/api/experiments/ablation-suite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  ablationSuiteStatus: (suiteId: string) => request<any>(`/api/experiments/ablation-suite/${encodeURIComponent(suiteId)}`),
  forecasts: (id: string, regionId?: string) => {
    const q = new URLSearchParams({ experiment_id: id });
    if (regionId) q.set("region_id", regionId);
    return request<any[]>(`/api/forecasts?${q}`);
  },
  evaluations: (id: string) => request<any[]>(`/api/evaluations?experiment_id=${encodeURIComponent(id)}`),
  exportUrl: (id: string) => `${API_BASE}/api/experiments/${id}/export`,
  templateUrl: () => `${API_BASE}/api/data/template`,
  previewCsv: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<any>("/api/data/preview", { method: "POST", body: form });
  },
  importCsv: async (file: File, sourceName: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("source_name", sourceName);
    return request<any>("/api/data/import", { method: "POST", body: form });
  },
  seedDemo: () => request<any>("/api/demo/seed?force=true&replace=true", { method: "POST" }),
};
