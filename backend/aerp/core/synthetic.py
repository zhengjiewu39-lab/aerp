from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from aerp.core.mechanistic import MechanisticSimulator

DISEASE_ID = "synth-ari"
SOURCE_ID = "synthetic-demo"
BATCH_ID = "synthetic-demo-seed"

REGIONS = (
    {"region_id": "R1", "name": "北部示范区", "scenario": "stable_seasonal", "population": 2_000_000},
    {"region_id": "R2", "name": "东部示范区", "scenario": "true_growth", "population": 3_500_000},
    {"region_id": "R3", "name": "西部示范区", "scenario": "reporting_change", "population": 1_200_000},
    {"region_id": "R4", "name": "南部示范区", "scenario": "delay_missing", "population": 800_000},
)

SCENARIO_NOTES = {
    "stable_seasonal": "潜在传播保持平稳季节性。",
    "true_growth": "第 50 周之后提高模拟中的传播强度，住院在感染之后上升。",
    "reporting_change": "潜在传播不变。第 62 周同时提高病例报告比例和检测量，住院生成过程不变。",
    "delay_missing": "潜在传播与北部相近，但第 68 周后初报更不完整，并插入缺失周。",
}


@dataclass
class SyntheticBundle:
    disease: dict
    regions: list[dict]
    source: dict
    observations: pd.DataFrame
    truth: pd.DataFrame
    labels: pd.DataFrame
    clock: pd.Timestamp
    seed: int
    n_weeks: int
    note: str


def _beta(n_weeks: int, scenario: str) -> np.ndarray:
    week = np.arange(n_weeks)
    seasonal = 0.92 + 0.16 * np.sin(2 * np.pi * (week - 12) / 52.0)
    if scenario != "true_growth":
        return seasonal
    beta = seasonal.copy()
    if n_weeks > 50:
        stop = min(n_weeks, 58)
        beta[50:stop] = seasonal[50:stop] + np.linspace(0.0, 0.42, stop - 50)
        if n_weeks > 58:
            beta[58:] = seasonal[58:] + 0.42
    return beta


def _factors(rng: np.random.Generator, week: int, scenario: str) -> list[float]:
    if scenario == "delay_missing" and week >= 68:
        first, second, third = rng.uniform(0.40, 0.68), rng.uniform(0.72, 0.95), rng.uniform(0.94, 1.06)
    else:
        first, second, third = rng.uniform(0.74, 1.06), rng.uniform(0.90, 1.05), rng.uniform(0.97, 1.04)
    if week % 5 == 0:
        third = min(third, second - 0.07)
    return [float(first), float(second), float(third)]


def _series_for_region(spec: dict, n_weeks: int, seed: int, region_index: int) -> dict:
    rng = np.random.default_rng(np.random.SeedSequence([seed, region_index, 7]))
    beta = _beta(n_weeks, spec["scenario"])
    infections = MechanisticSimulator().simulate(beta, rng)
    admissions = np.zeros(n_weeks)
    cases = np.zeros(n_weeks)
    ascertainment = np.full(n_weeks, 0.35)
    if spec["scenario"] == "reporting_change":
        ascertainment[min(62, n_weeks) :] = 0.82
    for week in range(n_weeks):
        source = infections[week - 2] if week >= 2 else infections[0] * 0.25
        admissions[week] = float(rng.poisson(max(0.045 * source, 0.0)))
        cases[week] = float(rng.poisson(max(ascertainment[week] * infections[week], 0.0)))
    week_index = np.arange(n_weeks)
    tests = 2600 + 280 * np.sin(2 * np.pi * (week_index - 8) / 52.0)
    tests = np.maximum(tests + rng.normal(0, 40, size=n_weeks), 400)
    if spec["scenario"] == "reporting_change":
        tests[min(62, n_weeks) :] *= 0.82 / 0.35
    positivity = np.divide(cases, tests, out=np.full(n_weeks, np.nan), where=tests > 0)
    return {
        "infections": infections,
        "admissions": admissions,
        "cases": cases,
        "tests": tests,
        "positivity": positivity,
        "rng": rng,
    }


def generate_synthetic(seed: int = 42, n_weeks: int = 104, start: str = "2023-01-02") -> SyntheticBundle:
    if n_weeks < 16:
        raise ValueError("合成序列至少需要 16 周")
    start_date = pd.Timestamp(start)
    period_starts = [start_date + pd.Timedelta(days=7 * week) for week in range(n_weeks)]
    period_ends = [item + pd.Timedelta(days=6) for item in period_starts]
    observation_rows = []
    truth_rows = []
    label_rows = []
    label_plan = {
        "R2": (50, "suspected_epidemic_change"),
        "R3": (62, "suspected_reporting_change"),
        "R4": (68, "data_quality"),
    }
    missing_weeks = {72, 73, 81, 90}
    for region_index, spec in enumerate(REGIONS):
        generated = _series_for_region(spec, n_weeks, seed, region_index)
        rng = generated["rng"]
        for week in range(n_weeks):
            truth_rows.append(_truth_row(spec["region_id"], period_starts[week], period_ends[week], "latent_infections", generated["infections"][week]))
            truth_rows.append(
                _truth_row(
                    spec["region_id"],
                    period_starts[week],
                    period_ends[week],
                    "simulation_hospital_admissions",
                    generated["admissions"][week],
                )
            )
            factors = _factors(rng, week, spec["scenario"])
            _append_count_vintages(
                observation_rows,
                spec["region_id"],
                period_starts[week],
                period_ends[week],
                "hospital_admissions",
                generated["admissions"][week],
                factors,
                spec["scenario"],
                week,
                missing_weeks,
            )
            _append_count_vintages(
                observation_rows,
                spec["region_id"],
                period_starts[week],
                period_ends[week],
                "reported_cases",
                generated["cases"][week],
                factors,
                spec["scenario"],
                week,
                missing_weeks,
            )
            observation_rows.append(
                _observation(
                    spec["region_id"],
                    period_starts[week],
                    period_ends[week],
                    period_ends[week] + pd.Timedelta(days=1),
                    "tests",
                    float(np.round(generated["tests"][week], 1)),
                    "count",
                    "complete",
                )
            )
            for lag, factor in zip((1, 14, 28), factors):
                case_value = None
                quality = "missing"
                if not _is_missing_vintage(spec["scenario"], week, lag, missing_weeks):
                    case_value = float(max(0, int(round(generated["cases"][week] * factor))))
                    quality = "complete" if lag == 28 else "provisional"
                positivity = None if case_value is None else float(case_value / generated["tests"][week])
                positivity_quality = "missing" if positivity is None else quality
                observation_rows.append(
                    _observation(
                        spec["region_id"],
                        period_starts[week],
                        period_ends[week],
                        period_ends[week] + pd.Timedelta(days=lag),
                        "positivity",
                        None if positivity is None else float(np.round(positivity, 6)),
                        "proportion",
                        positivity_quality,
                    )
                )
        if spec["region_id"] in label_plan and label_plan[spec["region_id"]][0] < n_weeks:
            week, category = label_plan[spec["region_id"]]
            label_rows.append(
                {
                    "region_id": spec["region_id"],
                    "period_start": period_starts[week],
                    "period_end": period_ends[week],
                    "category": category,
                    "note": "模拟设计标签，只用于评价，不能进入模型输入。",
                }
            )
    observations = pd.DataFrame(observation_rows)
    clock = pd.Timestamp(period_ends[-1] + timedelta(days=1))
    return SyntheticBundle(
        disease={
            "id": DISEASE_ID,
            "name": "合成急性呼吸道感染（演示）",
            "description": "固定种子生成的单一演示病种，不是某种真实病原体的监测提取。",
        },
        regions=[
            {
                "id": item["region_id"],
                "name": item["name"],
                "population": item["population"],
                "scenario": item["scenario"],
                "note": SCENARIO_NOTES[item["scenario"]] + " 人口只作背景，不计算人均指标。",
            }
            for item in REGIONS
        ],
        source={
            "id": SOURCE_ID,
            "name": "固定种子合成演示",
            "url": "",
            "license": "原型内部生成，不是真实监测数据，也没有外部数据许可。",
            "update_note": f"seed={seed}，{n_weeks} 周，四个场景。",
            "connector_status": "available",
        },
        observations=observations,
        truth=pd.DataFrame(truth_rows),
        labels=pd.DataFrame(label_rows),
        clock=clock,
        seed=seed,
        n_weeks=n_weeks,
        note=(
            "合成数据区分潜在感染、观测病例、住院、检测量、发布版本和模拟真值。"
            "住院使用感染后两周的量，病例使用当周感染。模拟真值不进入观测表。"
            "SIRS 骨架只用于生成曲线，不从住院数据反推参数。"
        ),
    )


def _is_missing_vintage(scenario: str, week: int, lag: int, missing_weeks: set[int]) -> bool:
    if scenario != "delay_missing" or week not in missing_weeks:
        return False
    if lag == 1:
        return True
    if lag == 14 and week in {72, 73}:
        return True
    return False


def _append_count_vintages(rows, region_id, period_start, period_end, metric, final_value, factors, scenario, week, missing_weeks):
    for lag, factor in zip((1, 14, 28), factors):
        if _is_missing_vintage(scenario, week, lag, missing_weeks):
            value = None
            quality = "missing"
        else:
            value = float(max(0, int(round(float(final_value) * factor))))
            quality = "complete" if lag == 28 else "provisional"
        rows.append(
            _observation(
                region_id,
                period_start,
                period_end,
                period_end + pd.Timedelta(days=lag),
                metric,
                value,
                "count",
                quality,
            )
        )


def _observation(region_id, period_start, period_end, issued_at, metric, value, unit, quality) -> dict:
    return {
        "disease_id": DISEASE_ID,
        "region_id": region_id,
        "period_start": pd.Timestamp(period_start),
        "period_end": pd.Timestamp(period_end),
        "issued_at": pd.Timestamp(issued_at),
        "metric": metric,
        "value": value,
        "unit": unit,
        "source_id": SOURCE_ID,
        "quality_flag": quality,
        "import_batch_id": BATCH_ID,
    }


def _truth_row(region_id, period_start, period_end, metric, value) -> dict:
    return {
        "region_id": region_id,
        "period_start": pd.Timestamp(period_start),
        "period_end": pd.Timestamp(period_end),
        "metric": metric,
        "value": float(value),
    }


def suggested_origins(observations: pd.DataFrame, clock, start_week: int = 64, step: int = 8) -> list[pd.Timestamp]:
    weeks = sorted(
        pd.to_datetime(observations.loc[observations["metric"] == "hospital_admissions", "period_end"]).unique()
    )
    origins = []
    for index in range(start_week, len(weeks), step):
        origins.append(pd.Timestamp(weeks[index]) + pd.Timedelta(days=1))
    clock_ts = pd.Timestamp(clock)
    if not origins or pd.Timestamp(origins[-1]) != clock_ts:
        if clock_ts not in origins:
            origins.append(clock_ts)
    return [pd.Timestamp(item) for item in origins if pd.Timestamp(item) <= clock_ts]
