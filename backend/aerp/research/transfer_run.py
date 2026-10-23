"""本地 / 固定迁移 / 可退出迁移对照。默认可在合成压力测试上运行。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from aerp.core.models.damped import DampedTrendModel
from aerp.core.models.persistence import PersistenceModel
from aerp.core.models.transfer import TransferModel, blend_local_and_transfer, exit_weight
from aerp.core.quantiles import mix_predictive_cdfs
from aerp.core.scoring import score_forecast
from aerp.core.vintages import mature_value, select_snapshot


def _local_predict(frame: pd.DataFrame, horizons: list[int]) -> object:
    persist = PersistenceModel()
    damped = DampedTrendModel()
    persist.fit(frame)
    damped.fit(frame)
    left = persist.predict(horizons)
    right = damped.predict(horizons)
    if left.status != "success":
        return left
    if right.status != "success":
        return left
    for horizon, item in left.horizons.items():
        other = right.horizons.get(horizon)
        if other:
            item.quantiles = mix_predictive_cdfs([item.quantiles, other.quantiles])
            item.method = "local_persistence_damped_cdf_mix"
    left.model_id = "transfer_local"
    left.message = "本地对照：持续值与阻尼趋势的 CDF 混合。"
    return left


def _score_row(actual: float, quantiles: dict[float, float]) -> dict:
    scored = score_forecast(
        actual,
        quantiles[0.5],
        quantiles[0.025],
        quantiles[0.1],
        quantiles[0.25],
        quantiles[0.75],
        quantiles[0.9],
        quantiles[0.975],
    )
    return {"wis": scored.wis, "abs_error": scored.abs_error, "covered_80": scored.covered_80}


def generate_paired_respiratory(seed: int = 11, n_weeks: int = 80) -> dict[str, pd.DataFrame]:
    """合成压力测试：两个呼吸道住院序列，尺度不同。明确不是真实 COVID/流感。"""
    rng = np.random.default_rng(int(seed))
    ends = pd.date_range("2022-10-08", periods=n_weeks, freq="7D")
    season = 40 + 25 * np.sin(np.arange(n_weeks) / 52.0 * 2 * np.pi)
    flu = np.clip(rng.poisson(np.maximum(season, 4)), 0, None).astype(float)
    covid = np.clip(rng.poisson(np.maximum(1.8 * season + 10, 4)), 0, None).astype(float)
    covid[40:50] = covid[40:50] * 0.2  # 制造一段负迁移压力

    def pack(disease_id: str, values: np.ndarray) -> pd.DataFrame:
        rows = []
        for stamp, value in zip(ends, values):
            rows.append(
                {
                    "disease_id": disease_id,
                    "region_id": "PA",
                    "period_start": stamp - pd.Timedelta(days=6),
                    "period_end": stamp,
                    "issued_at": stamp + pd.Timedelta(days=2),
                    "metric": "hospital_admissions",
                    "value": float(value),
                    "unit": "count",
                    "source_id": "synthetic-transfer",
                    "quality_flag": "synthetic",
                }
            )
        return pd.DataFrame(rows)

    return {"synth-flu-hosp": pack("synth-flu-hosp", flu), "synth-covid-hosp": pack("synth-covid-hosp", covid)}


def run_transfer_comparison(
    target_frame: pd.DataFrame,
    source_frames: dict[str, pd.DataFrame],
    *,
    target_disease_id: str,
    region_id: str,
    metric: str = "hospital_admissions",
    horizons: list[int] | None = None,
    min_train_weeks: int = 24,
    step_weeks: int = 2,
    exclude_target_from_training: bool = True,
    data_mode: str = "synthetic_stress_test",
) -> dict:
    horizons = horizons or [1, 2, 3, 4]
    target = target_frame.loc[(target_frame["region_id"] == region_id) & (target_frame["metric"] == metric)].copy()
    if target.empty:
        return {"status": "error", "message": "目标疾病该地区没有可用序列。"}
    target["period_end"] = pd.to_datetime(target["period_end"])
    origins = sorted(target["period_end"].unique())[min_train_weeks::step_weeks]
    history: list[dict] = []
    rows = []
    negative = []
    for origin in origins:
        cutoff = pd.Timestamp(origin) + pd.Timedelta(days=2)
        visible_target = select_snapshot(target, cutoff, "vintage")
        sources_visible = {}
        for disease_id, frame in source_frames.items():
            if exclude_target_from_training and disease_id == target_disease_id:
                continue
            subset = frame.loc[(frame["region_id"] == region_id) & (frame["metric"] == metric)].copy()
            sources_visible[disease_id] = select_snapshot(subset, cutoff, "vintage")
        local = _local_predict(visible_target, horizons)
        transfer = TransferModel()
        transfer.fit(
            visible_target,
            sources_visible,
            config={
                "target_disease_id": target_disease_id,
                "cutoff": cutoff,
                "exclude_target_from_training": exclude_target_from_training,
            },
        )
        transferred = transfer.predict(horizons)
        exit_info = exit_weight(history, local_id="transfer_local", transfer_id="transfer_fixed")
        fixed = blend_local_and_transfer(local, transferred, weight=0.35, model_id="transfer_fixed", note="固定迁移权重 0.35")
        exited = blend_local_and_transfer(
            local,
            transferred,
            weight=float(exit_info["weight"]),
            model_id="transfer_exit",
            note=f"可退出迁移：{exit_info['reason']}",
        )
        for model_id, pred in (("transfer_local", local), ("transfer_fixed", fixed), ("transfer_exit", exited)):
            if pred.status != "success":
                continue
            for horizon, item in pred.horizons.items():
                target_end = pd.Timestamp(origin) + pd.Timedelta(days=7 * int(horizon))
                actual = mature_value(
                    target,
                    region_id=region_id,
                    metric=metric,
                    period_end=target_end,
                    as_of=pd.Timestamp(origins[-1]) + pd.Timedelta(days=40),
                    maturity_lag_days=0,
                    replay_mode="final_time_split",
                )
                if actual is None:
                    continue
                scored = _score_row(float(actual[0]), item.quantiles)
                record = {
                    "model_id": model_id,
                    "region_id": region_id,
                    "cutoff_time": cutoff.isoformat(),
                    "target_period_end": target_end.date().isoformat(),
                    "horizon": int(horizon),
                    "status": "scored",
                    "wis": scored["wis"],
                    "abs_error": scored["abs_error"],
                    "covered_80": scored["covered_80"],
                    "actual_value": float(actual[0]),
                    "transfer_weight": pred.metadata.get("weight") if hasattr(pred, "metadata") else None,
                    "exit": exit_info,
                }
                rows.append(record)
                history.append(record)
        local_wis = [row["wis"] for row in rows if row["cutoff_time"] == cutoff.isoformat() and row["model_id"] == "transfer_local"]
        fixed_wis = [row["wis"] for row in rows if row["cutoff_time"] == cutoff.isoformat() and row["model_id"] == "transfer_fixed"]
        if local_wis and fixed_wis and float(np.mean(fixed_wis)) > float(np.mean(local_wis)) * 1.05:
            negative.append({"cutoff_time": cutoff.isoformat(), "local_wis": float(np.mean(local_wis)), "fixed_wis": float(np.mean(fixed_wis))})

    def summarize(model_id: str) -> dict:
        subset = [row for row in rows if row["model_id"] == model_id]
        if not subset:
            return {"n": 0, "wis": None, "mae": None, "coverage_80": None}
        return {
            "n": len(subset),
            "wis": float(np.mean([row["wis"] for row in subset])),
            "mae": float(np.mean([row["abs_error"] for row in subset])),
            "coverage_80": float(np.mean([row["covered_80"] for row in subset])),
        }

    summary = {key: summarize(key) for key in ("transfer_local", "transfer_fixed", "transfer_exit")}
    return {
        "status": "success",
        "data_mode": data_mode,
        "target_disease_id": target_disease_id,
        "source_disease_ids": list(source_frames),
        "exclude_target_from_training": exclude_target_from_training,
        "n_origins": len(origins),
        "n_scored": len(rows),
        "models": summary,
        "negative_transfer_tasks": negative,
        "negative_transfer_rate": None if not origins else float(len(negative) / max(len(origins), 1)),
        "exit_reduced_loss": (
            summary["transfer_exit"]["wis"] is not None
            and summary["transfer_fixed"]["wis"] is not None
            and summary["transfer_exit"]["wis"] < summary["transfer_fixed"]["wis"]
        ),
        "note": "研究假设对照。合成压力测试不能写成真实病种提升。真实数据不足时保持该标记。",
        "paper_claim": None,
    }
