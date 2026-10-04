export type Disease = {
  id: string;
  name: string;
  description: string;
  is_synthetic: boolean;
};

export type Region = {
  id: string;
  name: string;
  population: number | null;
  scenario: string | null;
  note: string;
};

export type Banner = {
  mode: string;
  banner: string;
  is_synthetic: boolean;
};

export type AlertItem = {
  id: number;
  experiment_id: string | null;
  region_id: string;
  category: string;
  level: string;
  rule: string;
  metrics: string[];
  period_start: string;
  period_end: string;
  source_id: string | null;
  import_batch_id: string | null;
  supporting_evidence: string;
  conflicting_evidence: string;
  limitations: string;
  affected_models: string[];
  review_status: string;
  created_at: string;
  cutoff_time: string | null;
};

export type Experiment = {
  id: string;
  name: string;
  disease_id: string;
  region_ids: string[];
  target_metric: string;
  cutoff_time: string;
  train_window_weeks: number | null;
  horizons: number[];
  models: string[];
  replay_mode: string;
  maturity_lag_days: number;
  random_seed: number;
  config: Record<string, unknown>;
  config_hash: string;
  input_hash: string;
  code_version: string;
  dependency_versions: Record<string, string>;
  status: string;
  error_message: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  summary: Record<string, unknown>;
};

export type ForecastItem = {
  id: number;
  model_id: string;
  region_id: string;
  cutoff_time: string;
  target_period_end: string;
  horizon: number;
  median: number;
  interval_50: [number, number];
  interval_80: [number, number];
  interval_95: [number, number];
  interval_method: string;
  quality_note: string;
  weights: Record<string, number>;
};

export type EvaluationItem = {
  id: number;
  model_id: string;
  region_id: string;
  horizon: number;
  target_period_end: string;
  status: string;
  actual_value: number | null;
  abs_error: number | null;
  wis: number | null;
  covered_50: boolean | null;
  covered_80: boolean | null;
  covered_95: boolean | null;
  phase: string;
  notes: string;
};
