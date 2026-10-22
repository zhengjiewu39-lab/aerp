from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from uuid import uuid4

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from aerp import __version__ as AERP_VERSION
from aerp.core.adapt import AdaptationDecision, candidate_note, choose_window, decide, validation_wis
from aerp.core.analogs import analog_blend_weight
from aerp.core.anomaly import detect
from aerp.core.calendar import ForecastTask, build_forecast_task, last_visible_week_end
from aerp.core.hashing import canonical_hash, git_commit_hash, hash_observations
from aerp.core.models.analog import AnalogShapeModel
from aerp.core.models.base import HorizonForecast, clone_prediction
from aerp.core.models.catalog import MODEL_CARDS
from aerp.core.models.hierarchical import PanelCountModel
from aerp.core.models.damped import DampedTrendModel
from aerp.core.models.ensemble import apply_downweight, combine_predictions, dynamic_weights, equal_weight_map
from aerp.core.models.negbin import NegBinGLMModel
from aerp.core.models.persistence import PersistenceModel
from aerp.core.research_mode import RESEARCH_QUESTION, analog_allowed_in_forecasts, public_mode_payload, resolve_mode, research_notes
from aerp.core.phases import classify_phase
from aerp.core.quantiles import scale_around_median
from aerp.core.revision import apply_nowcast, estimate_revision, mix_quantile_dicts, sample_revised_frames
from aerp.core.scoring import score_forecast
from aerp.core.series import frame_from_grid, prepare_weekly, tail_weeks
from aerp.core.change_labels import diagnosis_report, infer_label_source
from aerp.core.skill import alert_skill, recovery_summary
from aerp.core.synthetic import suggested_origins
from aerp.core.timeutil import as_timestamp, utcnow
from aerp.core.vintages import has_multiple_vintages, mature_value, replay_descriptor, select_snapshot
from aerp.db.models import Alert, Evaluation, Experiment, Forecast, ModelRun
from aerp.services.observations import labels_frame, observations_frame, region_context_map


COMPONENT_MODELS = ("persistence", "damped_trend", "retrain_glm")
DEFAULT_ARMS = (
    "persistence",
    "fixed_glm",
    "retrain_glm",
    "pooled_glm",
    "hierarchical",
    "hierarchical_dynamic",
    "equal_weight",
    "dynamic_weight",
    "adaptive",
    "analog",
)
PANEL_ARMS = ("pooled_glm", "hierarchical", "hierarchical_dynamic")


def dependency_versions() -> dict:
    packages = ["fastapi", "pandas", "numpy", "scipy", "scikit-learn", "statsmodels", "sqlalchemy"]
    out = {"aerp": AERP_VERSION}
    for name in packages:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = "unknown"
    return out


def create_experiment(db: Session, payload: dict) -> Experiment:
    experiment_id = payload.get("id") or f"exp-{uuid4().hex[:10]}"
    horizons = [int(item) for item in payload.get("horizons", [1, 2, 3, 4])]
    models = list(payload.get("models") or list(DEFAULT_ARMS))
    incoming = dict(payload.get("config") or {})
    mode = resolve_mode(incoming, payload)
    config = {
        "ablations": payload.get("ablations")
        or {
            "no_revision": False,
            "no_anomaly": False,
            "no_dynamic_weight": False,
            "no_hierarchy": False,
            "no_dynamic_update": False,
            "no_uncertainty_propagation": False,
        },
        "origins": payload.get("origins"),
        "short_window_weeks": payload.get("short_window_weeks", 26),
        "holdout_region_ids": payload.get("holdout_region_ids") or [],
        "rolling_window_weeks": payload.get("rolling_window_weeks"),
        "compare_arms": True,
        "mode": mode,
        "reference_week_rule": incoming.get("reference_week_rule", "saturday_on_or_before_cutoff"),
        "allow_demo_analogs": incoming.get("allow_demo_analogs", mode != "research"),
        "demo_note": "结果由真实计算生成，不保证分层或动态模型优于统一、独立或等权对照。",
        "research_question": RESEARCH_QUESTION,
        "git_commit": git_commit_hash(),
    }
    config.update(incoming)
    config["mode"] = mode
    region_ids = list(payload["region_ids"])
    cutoff = as_timestamp(payload["cutoff_time"]).to_pydatetime()
    frame = observations_frame(db, disease_id=payload["disease_id"])
    visible = select_snapshot(frame, cutoff, payload.get("replay_mode", "vintage"))
    experiment = Experiment(
        id=experiment_id,
        name=payload.get("name") or f"回放 {experiment_id}",
        disease_id=payload["disease_id"],
        region_ids=region_ids,
        target_metric=payload.get("target_metric", "hospital_admissions"),
        cutoff_time=cutoff,
        train_window_weeks=payload.get("train_window_weeks"),
        horizons=horizons,
        models=models,
        replay_mode=payload.get("replay_mode", "vintage"),
        maturity_lag_days=int(payload.get("maturity_lag_days", 28)),
        random_seed=int(payload.get("random_seed", 42)),
        config=config,
        config_hash=canonical_hash({"payload": payload, "config": config}),
        input_hash=hash_observations(visible),
        code_version=AERP_VERSION,
        dependency_versions={**dependency_versions(), "git_commit": git_commit_hash()},
        status="created",
        created_at=utcnow(),
        summary={},
    )
    db.add(experiment)
    db.commit()
    db.refresh(experiment)
    return experiment


def _metric_frame(all_obs: pd.DataFrame, region_id: str, metric: str, cutoff, replay_mode: str) -> pd.DataFrame:
    subset = all_obs[(all_obs["region_id"] == region_id) & (all_obs["metric"] == metric)]
    return select_snapshot(subset, cutoff, replay_mode)


def _series_array(frame: pd.DataFrame) -> tuple[pd.DatetimeIndex, np.ndarray]:
    return prepare_weekly(frame)


def _fit_components(
    train: pd.DataFrame,
    horizons: list[int],
    fixed_glm_state: dict | None,
    target_week_ends: dict[int, pd.Timestamp] | None = None,
):
    models = {
        "persistence": PersistenceModel(),
        "damped_trend": DampedTrendModel(),
        "retrain_glm": NegBinGLMModel("retrain_glm"),
    }
    results = {}
    for model_id, model in models.items():
        fit = model.fit(train, {"horizons": horizons})
        pred = model.predict(horizons, target_week_ends=target_week_ends) if fit.status == "success" else None
        results[model_id] = {"model": model, "fit": fit, "predict": pred}
    if fixed_glm_state is None:
        fixed = NegBinGLMModel("fixed_glm")
        fit = fixed.fit(train, {"horizons": horizons})
        pred = fixed.predict(horizons, target_week_ends=target_week_ends) if fit.status == "success" else None
        fixed_glm_state = {
            "model": fixed,
            "fit": fit,
            "anchor": None if train.empty else pd.to_datetime(train["period_end"]).max(),
        }
        fixed_glm_state["predict"] = pred
    else:
        fixed = fixed_glm_state["model"]
        if fixed_glm_state["fit"].status == "success":
            pred = fixed.predict(
                horizons, anchor=fixed_glm_state.get("anchor"), target_week_ends=target_week_ends
            )
        else:
            pred = None
        fixed_glm_state["predict"] = pred
    results["fixed_glm"] = fixed_glm_state
    return results, fixed_glm_state


def _clone_components(components: dict) -> dict:
    cloned = {}
    for key, pack in components.items():
        cloned[key] = {
            "model": pack.get("model"),
            "fit": pack.get("fit"),
            "predict": clone_prediction(pack.get("predict")),
            "anchor": pack.get("anchor"),
        }
    return cloned


def _successful_preds(components: dict, model_ids: tuple[str, ...] | list[str]):
    preds = []
    for model_id in model_ids:
        pred = (components.get(model_id) or {}).get("predict")
        if pred is not None:
            preds.append(pred)
    return preds


def _fit_panel_models(
    trains: dict[str, pd.DataFrame],
    contexts: dict[str, dict],
    auxiliaries: dict[str, dict],
    experiment: Experiment,
    origin,
    ablations: dict,
    target_week_ends: dict[int, pd.Timestamp] | None = None,
) -> dict:
    pack: dict = {arm: {} for arm in PANEL_ARMS}
    events = []
    requested = [arm for arm in PANEL_ARMS if arm in (experiment.models or [])]
    if not requested:
        pack["_events"] = events
        return pack
    holdout = list((experiment.config or {}).get("holdout_region_ids") or [])
    for arm in requested:
        if ablations.get("no_hierarchy") and arm in {"hierarchical", "hierarchical_dynamic"}:
            events.append(
                {
                    "cutoff": as_timestamp(origin).isoformat(),
                    "model_id": arm,
                    "action": "ablate_hierarchy",
                    "trigger": "消融：删除分层部分共享，该臂不产生分层预测。",
                    "rollback": True,
                }
            )
            continue
        use_slope = arm == "hierarchical_dynamic" and not ablations.get("no_dynamic_update")
        rolling = 0
        if arm == "hierarchical_dynamic" and not ablations.get("no_dynamic_update"):
            rolling = int((experiment.config or {}).get("rolling_window_weeks") or 52)
        if ablations.get("no_dynamic_update") and arm == "hierarchical_dynamic":
            events.append(
                {
                    "cutoff": as_timestamp(origin).isoformat(),
                    "model_id": arm,
                    "action": "ablate_dynamic_update",
                    "trigger": "消融：删除动态更新，保留固定分层截距。",
                    "rollback": True,
                }
            )
        model = PanelCountModel(arm)
        fit = model.fit(
            trains,
            contexts=contexts,
            auxiliaries=auxiliaries,
            config={
                "mode": "pooled_glm" if arm == "pooled_glm" else "hierarchical",
                "holdout_region_ids": holdout,
                "rolling_window_weeks": rolling,
                "dynamic_slope": use_slope,
                "horizons": experiment.horizons,
            },
        )
        for region_id in trains:
            pack[arm][region_id] = model.predict_region(
                region_id,
                experiment.horizons,
                contexts.get(region_id),
                target_week_ends=target_week_ends,
            )
        events.append(
            {
                "cutoff": as_timestamp(origin).isoformat(),
                "model_id": arm,
                "action": "weekly_refit",
                "status": fit.status,
                "trigger": "按周回放原点更新",
                "window": (fit.metadata or {}).get("window"),
                "used_features": (fit.metadata or {}).get("used_features"),
                "holdout_region_ids": holdout,
                "dynamic_slope": use_slope,
                "validation": fit.message if fit.status != "success" else "分层拟合完成，待用成熟预测评价。",
                "rollback": fit.status != "success",
            }
        )
    pack["_events"] = events
    return pack


def _revision_deltas(vintages: pd.DataFrame, cutoff) -> np.ndarray | None:
    profile = estimate_revision(vintages, cutoff)
    if not profile.get("estimable"):
        return None
    paths = profile.get("paths") or []
    deltas = []
    for path in paths:
        points = [point for point in path["points"] if point["value"] is not None]
        if len(points) < 2:
            deltas.append(np.nan)
        else:
            deltas.append(float(points[-1]["value"] - points[0]["value"]))
    return np.asarray(deltas, dtype=float) if deltas else None


def _mix_revision_samples(adaptive_pred, sampled: dict, horizons: list[int], target_week_ends) -> object:
    if adaptive_pred is None or getattr(adaptive_pred, "status", None) != "success":
        return adaptive_pred
    if sampled.get("status") != "success" or not sampled.get("samples"):
        if adaptive_pred.metadata is not None:
            adaptive_pred.metadata["revision_sampling"] = sampled.get("message") or "未抽样"
        return adaptive_pred
    bags: dict[int, list] = {int(horizon): [] for horizon in horizons}
    for frame in sampled["samples"]:
        model = PersistenceModel()
        fit = model.fit(frame, {"horizons": horizons, "min_obs": 8, "min_residuals": 4})
        if fit.status != "success":
            continue
        pred = model.predict(horizons, target_week_ends=target_week_ends)
        if pred.status != "success":
            continue
        for horizon, item in pred.horizons.items():
            bags[int(horizon)].append(item.quantiles)
    for horizon, item in adaptive_pred.horizons.items():
        mixed = mix_quantile_dicts(bags.get(int(horizon)) or [])
        if not mixed:
            continue
        blended = mix_quantile_dicts([item.quantiles, mixed])
        adaptive_pred.horizons[horizon] = HorizonForecast(
            int(horizon),
            blended,
            item.method + "|revision_sample_mix",
            item.quality_note + " 修订抽样与点预测分位数等权混合；不声称覆盖全部不确定性。",
        )
    adaptive_pred.metadata = dict(adaptive_pred.metadata or {})
    adaptive_pred.metadata["revision_sampling"] = {
        "n_samples": sampled.get("n_samples"),
        "seed": sampled.get("seed"),
        "assumptions": sampled.get("assumptions"),
    }
    return adaptive_pred


def _predict_arms(
    components: dict,
    *,
    arm_ids: list[str],
    history_scores: list[dict],
    cutoff,
    alerts,
    train_frame: pd.DataFrame,
    config: dict,
    ablations: dict,
    analog_pred=None,
    target_week_ends: dict[int, pd.Timestamp] | None = None,
) -> dict:
    """对照臂使用未修改的组件预测；短窗口、修订、形态和区间倍率只作用于 adaptive。"""
    outputs = {}
    control = _clone_components(components)
    control_preds = _successful_preds(control, COMPONENT_MODELS)
    equal_weights = equal_weight_map([pred.model_id for pred in control_preds])
    dyn_weights, dyn_note = dynamic_weights(history_scores, list(equal_weights), cutoff)
    adaptive_weights = dict(dyn_weights)
    if ablations.get("no_dynamic_weight"):
        dyn_weights, dyn_note = equal_weights, "消融：删除动态权重，退回等权。"
        adaptive_weights = dict(equal_weights)

    decision = AdaptationDecision()
    if not ablations.get("no_anomaly"):
        decision = decide(alerts, config)
        if decision.downweight_models:
            adaptive_weights = apply_downweight(adaptive_weights, decision.downweight_models, 0.5)

    window_choice = "long"
    window_note = ""
    adaptive_pack = _clone_components(components)
    if decision.compare_windows and not ablations.get("no_anomaly"):
        short_weeks = int(config.get("short_window_weeks", 26))
        short_wis, short_ok = validation_wis(train_frame, short_weeks)
        long_wis, long_ok = validation_wis(train_frame, None)
        window_choice, window_note = choose_window(short_wis, long_wis, short_ok, long_ok)
        if window_choice == "short":
            adaptive_train = tail_weeks(train_frame, short_weeks)
            short_model = DampedTrendModel()
            short_fit = short_model.fit(adaptive_train, {"horizons": [1, 2, 3, 4], "min_obs": 8, "min_residuals": 4})
            if short_fit.status == "success":
                adaptive_pack["damped_trend"] = {
                    "model": short_model,
                    "fit": short_fit,
                    "predict": short_model.predict([1, 2, 3, 4], target_week_ends=target_week_ends),
                }

    display_ids = set(arm_ids) | set(COMPONENT_MODELS)
    if "persistence" in display_ids and control["persistence"].get("predict"):
        outputs["persistence"] = control["persistence"]["predict"]
    if "fixed_glm" in display_ids and control.get("fixed_glm", {}).get("predict"):
        outputs["fixed_glm"] = control["fixed_glm"]["predict"]
    if "retrain_glm" in display_ids and control["retrain_glm"].get("predict"):
        outputs["retrain_glm"] = control["retrain_glm"]["predict"]
    if "damped_trend" in display_ids and control["damped_trend"].get("predict"):
        outputs["damped_trend"] = control["damped_trend"]["predict"]
    use_analog = analog_allowed_in_forecasts(config)
    if "analog" in arm_ids and analog_pred is not None and use_analog:
        outputs["analog"] = analog_pred
    if "equal_weight" in arm_ids:
        outputs["equal_weight"] = combine_predictions(
            control_preds,
            equal_weights,
            model_id="equal_weight",
            note="等权分位数平均，强制对照。" + (candidate_note(control_preds) or ""),
        )
    if "dynamic_weight" in arm_ids:
        outputs["dynamic_weight"] = combine_predictions(
            control_preds,
            dyn_weights,
            model_id="dynamic_weight",
            note=dyn_note + " " + (candidate_note(control_preds) or ""),
        )
    if "adaptive" in arm_ids:
        adaptive_preds = _successful_preds(adaptive_pack, COMPONENT_MODELS)
        analog_note = "未并入历史形态类比。"
        if use_analog and analog_pred is not None and analog_pred.status == "success":
            blend = analog_blend_weight((analog_pred.metadata or {}).get("similarity"))
            if blend > 0:
                remaining = 1.0 - blend
                adaptive_weights = {key: value * remaining for key, value in adaptive_weights.items()}
                adaptive_weights["analog"] = blend
                adaptive_preds = list(adaptive_preds) + [analog_pred]
                analog_note = f"历史形态类比权重 {blend:.2f}。类比不是真实跨疾病训练。"
            else:
                analog_note = "历史形态相似度不足，类比未进入适应组合。"
        elif not use_analog:
            analog_note = "研究模式：教学形态库未进入正式适应组合。"
        if decision.use_revision_adjustment and not ablations.get("no_revision"):
            if ablations.get("no_uncertainty_propagation"):
                note_extra = "已启用修订点调整；消融关闭了修订抽样不确定性传播。"
            else:
                note_extra = "已启用修订调整。若提供抽样路径，预测区间混合修订、参数与未来随机性，不声称覆盖全部不确定性。"
        else:
            note_extra = "未启用修订调整。" if ablations.get("no_revision") else "规则未要求修订调整。"
        adaptive = combine_predictions(
            adaptive_preds,
            adaptive_weights,
            model_id="adaptive",
            note="异常识别与适应规则。" + " ".join(decision.notes) + " " + window_note + " " + note_extra + " " + analog_note,
        )
        if adaptive.status == "success" and decision.interval_scale != 1.0 and not ablations.get("no_anomaly"):
            for _horizon, item in adaptive.horizons.items():
                item.quantiles = scale_around_median(item.quantiles, decision.interval_scale)
                item.method = item.method + f"|interval_scale={decision.interval_scale}"
                item.quality_note += " 区间扩大倍率需由回放覆盖率检验，不保证名义覆盖。"
        outputs["adaptive"] = adaptive
        outputs["_adaptive_meta"] = {
            "decision": decision.__dict__,
            "window_choice": window_choice,
            "weights": adaptive_weights,
            "control_dynamic_weights": dyn_weights,
        }
    outputs["_equal_weights"] = equal_weights
    outputs["_dynamic_weights"] = dyn_weights
    outputs["_component_preds"] = {pred.model_id: pred for pred in control_preds}
    return outputs


def _ensure_model_run(db: Session, experiment: Experiment, model_runs: dict[str, ModelRun], model_id: str) -> ModelRun:
    if model_id in model_runs:
        return model_runs[model_id]
    run = ModelRun(
        id=f"{experiment.id}-{model_id}",
        experiment_id=experiment.id,
        model_id=model_id,
        status="pending",
        metadata_json={},
    )
    db.add(run)
    db.flush()
    model_runs[model_id] = run
    return run


def _store_forecasts(
    db: Session,
    *,
    experiment: Experiment,
    model_runs: dict[str, ModelRun],
    region_id: str,
    task: ForecastTask,
    predictions: dict,
    arm: str,
    last_visible=None,
) -> list[Forecast]:
    saved = []
    generated_at = utcnow()
    calendar = task.as_dict(last_visible)
    for model_id, result in predictions.items():
        if model_id.startswith("_") or result is None or result.status != "success":
            continue
        run = _ensure_model_run(db, experiment, model_runs, model_id)
        run.status = "success"
        run.trained_at = generated_at
        run.metadata_json = result.metadata
        for horizon, item in result.horizons.items():
            target_end = task.target_week_end(int(horizon))
            target_start = task.target_week_start(int(horizon))
            q = item.quantiles
            row = Forecast(
                experiment_id=experiment.id,
                model_run_id=run.id,
                model_id=model_id,
                region_id=region_id,
                metric=experiment.target_metric,
                issued_at=task.cutoff.to_pydatetime(),
                cutoff_time=task.cutoff.to_pydatetime(),
                target_period_start=target_start.to_pydatetime(),
                target_period_end=target_end.to_pydatetime(),
                horizon=int(horizon),
                q025=float(q[0.025]),
                q10=float(q[0.1]),
                q25=float(q[0.25]),
                q50=float(q[0.5]),
                q75=float(q[0.75]),
                q90=float(q[0.9]),
                q975=float(q[0.975]),
                interval_method=item.method,
                quality_note=item.quality_note,
                generated_at=generated_at,
                arm=arm if model_id in (experiment.models or []) else "component",
                weights=result.metadata.get("weights") or {},
                extra={
                    "message": result.message,
                    "analog_id": result.metadata.get("analog_id"),
                    "similarity": result.metadata.get("similarity"),
                    "phase": result.metadata.get("phase"),
                    "direction": result.metadata.get("direction"),
                    "context_score": result.metadata.get("context_score"),
                    "calendar": calendar,
                    "reference_week_end": task.reference_week_end.date().isoformat(),
                    "last_visible_week_end": None
                    if last_visible is None
                    else as_timestamp(last_visible).date().isoformat(),
                    "trajectory_method": "gaussian_copula_ar1",
                    "trajectory_note": "联合预警概率在评价时由分位数生成 copula 轨迹；无轨迹才退回独立边际。",
                },
            )
            db.add(row)
            saved.append(row)
    return saved


def _evaluate_forecasts(
    db: Session,
    *,
    experiment: Experiment,
    all_obs: pd.DataFrame,
    forecasts: list[Forecast],
    evaluation_as_of,
    purpose: str = "final",
    write_pending: bool = True,
) -> list[dict]:
    score_rows = []
    existing_rows = (
        db.query(Evaluation)
        .filter(Evaluation.experiment_id == experiment.id, Evaluation.purpose == purpose)
        .all()
    )
    existing = {row.forecast_id for row in existing_rows if row.forecast_id is not None}
    phase_series_cache: dict[str, pd.Series] = {}
    for forecast in forecasts:
        if forecast.id in existing:
            continue
        key = forecast.region_id
        if key not in phase_series_cache:
            final = select_snapshot(
                all_obs[(all_obs["region_id"] == key) & (all_obs["metric"] == experiment.target_metric)],
                evaluation_as_of,
                "final_time_split",
            )
            index, values = prepare_weekly(final)
            phase_series_cache[key] = pd.Series(values, index=index)
        matured = mature_value(
            all_obs,
            region_id=forecast.region_id,
            metric=experiment.target_metric,
            period_end=forecast.target_period_end,
            as_of=evaluation_as_of,
            maturity_lag_days=experiment.maturity_lag_days,
            replay_mode=experiment.replay_mode,
        )
        if matured is None:
            if write_pending:
                evaluation = Evaluation(
                    experiment_id=experiment.id,
                    forecast_id=forecast.id,
                    model_id=forecast.model_id,
                    region_id=forecast.region_id,
                    horizon=forecast.horizon,
                    target_period_end=forecast.target_period_end,
                    status="pending",
                    phase=classify_phase(phase_series_cache[key], forecast.target_period_end),
                    arm=forecast.arm,
                    purpose=purpose,
                    notes="待评价：固定成熟滞后版本在评价时点尚不可见。",
                )
                db.add(evaluation)
            continue
        actual, matured_at = matured
        scored = score_forecast(
            actual,
            forecast.q50,
            forecast.q025,
            forecast.q10,
            forecast.q25,
            forecast.q75,
            forecast.q90,
            forecast.q975,
        )
        evaluation = Evaluation(
            experiment_id=experiment.id,
            forecast_id=forecast.id,
            model_id=forecast.model_id,
            region_id=forecast.region_id,
            horizon=forecast.horizon,
            target_period_end=forecast.target_period_end,
            status="scored",
            actual_value=actual,
            matured_at=matured_at.to_pydatetime(),
            scored_at=utcnow(),
            abs_error=scored.abs_error,
            wis=scored.wis,
            covered_50=scored.covered_50,
            covered_80=scored.covered_80,
            covered_95=scored.covered_95,
            width_50=scored.width_50,
            width_80=scored.width_80,
            width_95=scored.width_95,
            phase=classify_phase(phase_series_cache[key], forecast.target_period_end),
            arm=forecast.arm,
            purpose=purpose,
            notes="目标版本=固定成熟滞后可见值，不等于最终真实值。在线评分不用于最终论文补评反向改权重。",
        )
        db.add(evaluation)
        score_rows.append(
            {
                "model_id": forecast.model_id,
                "region_id": forecast.region_id,
                "horizon": forecast.horizon,
                "target_period_end": forecast.target_period_end,
                "wis": scored.wis,
                "abs_error": scored.abs_error,
                "matured_at": matured_at,
                "status": "scored",
            }
        )
    return score_rows


def _summarize(db: Session, experiment: Experiment, labels: pd.DataFrame) -> dict:
    evaluations = (
        db.query(Evaluation)
        .filter(Evaluation.experiment_id == experiment.id, Evaluation.purpose == "final")
        .all()
    )
    alerts = db.query(Alert).filter(Alert.experiment_id == experiment.id).all()
    by_model: dict[str, list[Evaluation]] = defaultdict(list)
    for row in evaluations:
        if row.status == "scored":
            by_model[row.model_id].append(row)
    model_summary = {}
    for model_id, rows in by_model.items():
        model_summary[model_id] = {
            "n": len(rows),
            "mae": float(np.mean([row.abs_error for row in rows])),
            "wis": float(np.mean([row.wis for row in rows])),
            "coverage_50": float(np.mean([row.covered_50 for row in rows])),
            "coverage_80": float(np.mean([row.covered_80 for row in rows])),
            "coverage_95": float(np.mean([row.covered_95 for row in rows])),
            "width_50": float(np.mean([row.width_50 for row in rows])),
            "width_80": float(np.mean([row.width_80 for row in rows])),
            "width_95": float(np.mean([row.width_95 for row in rows])),
            "by_horizon": {},
            "by_phase": {},
        }
        for horizon in sorted({row.horizon for row in rows}):
            subset = [row for row in rows if row.horizon == horizon]
            model_summary[model_id]["by_horizon"][str(horizon)] = {
                "mae": float(np.mean([row.abs_error for row in subset])),
                "wis": float(np.mean([row.wis for row in subset])),
                "n": len(subset),
            }
        by_region = {}
        for region_id in sorted({row.region_id for row in rows}):
            subset = [row for row in rows if row.region_id == region_id]
            by_region[region_id] = {
                "mae": float(np.mean([row.abs_error for row in subset])),
                "wis": float(np.mean([row.wis for row in subset])),
                "coverage_80": float(np.mean([row.covered_80 for row in subset])),
                "n": len(subset),
            }
        model_summary[model_id]["by_region"] = by_region
        for phase in sorted({row.phase for row in rows}):
            subset = [row for row in rows if row.phase == phase]
            model_summary[model_id]["by_phase"][phase] = {
                "mae": float(np.mean([row.abs_error for row in subset])),
                "wis": float(np.mean([row.wis for row in subset])),
                "n": len(subset),
            }
    alert_dicts = [
        {
            "region_id": alert.region_id,
            "category": alert.category,
            "period_start": alert.period_start,
            "period_end": alert.period_end,
        }
        for alert in alerts
    ]
    skill = alert_skill(alert_dicts, labels if not labels.empty else None)
    label_source = infer_label_source(None if labels is None or labels.empty else labels)
    if resolve_mode(experiment.config) == "research" and label_source != "manual":
        if labels is None or labels.empty:
            label_source = "none"
        skill_for_research = None
    else:
        skill_for_research = skill
    change = diagnosis_report(
        alert_dicts,
        None if labels is None or labels.empty else labels,
        source=label_source,
        mode=resolve_mode(experiment.config),
    )
    recovery = recovery_summary(
        [
            {
                "model_id": row.model_id,
                "region_id": row.region_id,
                "horizon": row.horizon,
                "target_period_end": row.target_period_end,
                "status": row.status,
                "abs_error": row.abs_error,
            }
            for row in evaluations
        ],
        labels if not labels.empty else None,
        model_id="adaptive",
    )
    pending = sum(1 for row in evaluations if row.status == "pending")
    return {
        "models": model_summary,
        "pending_evaluations": pending,
        "alert_skill": skill_for_research,
        "change_diagnosis": change,
        "recovery": recovery,
        "recovery_hierarchical_dynamic": recovery_summary(
            [
                {
                    "model_id": row.model_id,
                    "region_id": row.region_id,
                    "horizon": row.horizon,
                    "target_period_end": row.target_period_end,
                    "status": row.status,
                    "abs_error": row.abs_error,
                }
                for row in evaluations
            ],
            labels if not labels.empty else None,
            model_id="hierarchical_dynamic",
        ),
        "note": "动态组合不默认优于等权。阶段标签仅用于事后分组。",
        "model_cards": MODEL_CARDS,
    }


def run_experiment(db: Session, experiment_id: str) -> Experiment:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise ValueError("实验不存在")
    experiment.status = "running"
    experiment.started_at = utcnow()
    experiment.error_message = ""
    db.query(Forecast).filter(Forecast.experiment_id == experiment_id).delete()
    db.query(Evaluation).filter(Evaluation.experiment_id == experiment_id).delete()
    db.query(Alert).filter(Alert.experiment_id == experiment_id).delete()
    db.query(ModelRun).filter(ModelRun.experiment_id == experiment_id).delete()
    db.commit()

    try:
        all_obs = observations_frame(db, disease_id=experiment.disease_id)
        labels = labels_frame(db)
        contexts = region_context_map(db)
        multi = has_multiple_vintages(all_obs)
        descriptor = replay_descriptor(experiment.replay_mode, multi)
        ablations = experiment.config.get("ablations") or {}
        origins_cfg = experiment.config.get("origins")
        if origins_cfg:
            origins = [as_timestamp(item) for item in origins_cfg]
        else:
            origins = suggested_origins(all_obs, experiment.cutoff_time)
        origins = [item for item in origins if item <= as_timestamp(experiment.cutoff_time)]
        if not origins:
            origins = [as_timestamp(experiment.cutoff_time)]

        model_runs = {}
        needed_models = list(dict.fromkeys(list(experiment.models or []) + list(COMPONENT_MODELS)))
        for model_id in needed_models:
            run = ModelRun(
                id=f"{experiment.id}-{model_id}",
                experiment_id=experiment.id,
                model_id=model_id,
                status="pending",
                metadata_json={},
            )
            db.add(run)
            model_runs[model_id] = run
        db.flush()

        history_scores: list[dict] = []
        fixed_states: dict[str, dict | None] = {region_id: None for region_id in experiment.region_ids}
        evaluation_as_of = as_timestamp(experiment.cutoff_time)
        update_events: list[dict] = []
        reference_rule = str((experiment.config or {}).get("reference_week_rule") or "saturday_on_or_before_cutoff")

        for origin in origins:
            week_grid = pd.to_datetime(all_obs.loc[all_obs["metric"] == experiment.target_metric, "period_end"]).unique()
            task = build_forecast_task(
                origin,
                experiment.horizons,
                rule=reference_rule,
                week_grid=week_grid,
            )
            previous = [
                row
                for row in db.query(Forecast).filter(Forecast.experiment_id == experiment.id).all()
                if as_timestamp(row.cutoff_time) < task.cutoff
            ]
            newly_matured = _evaluate_forecasts(
                db,
                experiment=experiment,
                all_obs=all_obs,
                forecasts=previous,
                evaluation_as_of=origin,
                purpose="online",
                write_pending=False,
            )
            history_scores.extend(
                row
                for row in newly_matured
                if row.get("status") == "scored" and row.get("wis") is not None and row.get("matured_at") is not None
            )
            origin_trains = {}
            origin_aux = {}
            for region_id in experiment.region_ids:
                train_panel = _metric_frame(all_obs, region_id, experiment.target_metric, origin, experiment.replay_mode)
                if experiment.train_window_weeks:
                    train_panel = tail_weeks(train_panel, int(experiment.train_window_weeks))
                origin_trains[region_id] = train_panel
                origin_aux[region_id] = {
                    "mobility_index": _metric_frame(all_obs, region_id, "mobility_index", origin, experiment.replay_mode),
                    "vaccination_coverage": _metric_frame(
                        all_obs, region_id, "vaccination_coverage", origin, experiment.replay_mode
                    ),
                }
            panel_pack = _fit_panel_models(
                origin_trains,
                contexts,
                origin_aux,
                experiment,
                origin,
                ablations,
                target_week_ends=task.target_week_ends(),
            )
            update_events.extend(panel_pack.get("_events") or [])
            for region_id in experiment.region_ids:
                admissions_v = all_obs[
                    (all_obs["region_id"] == region_id) & (all_obs["metric"] == experiment.target_metric)
                ]
                train = _metric_frame(all_obs, region_id, experiment.target_metric, origin, experiment.replay_mode)
                if experiment.train_window_weeks:
                    train = tail_weeks(train, int(experiment.train_window_weeks))
                revision_profile = estimate_revision(admissions_v, origin)
                if revision_profile.get("estimable") and not ablations.get("no_revision"):
                    # 适应臂可能使用修订后训练；对照臂保持原可见序列
                    revised_train = apply_nowcast(train, origin, revision_profile, experiment.maturity_lag_days)
                else:
                    revised_train = train

                cases = _metric_frame(all_obs, region_id, "reported_cases", origin, experiment.replay_mode)
                tests = _metric_frame(all_obs, region_id, "tests", origin, experiment.replay_mode)
                positivity = _metric_frame(all_obs, region_id, "positivity", origin, experiment.replay_mode)
                adm_index, adm_values = _series_array(train)
                case_index, case_values = _series_array(cases)
                test_index, test_values = _series_array(tests)
                pos_index, pos_values = _series_array(positivity)

                def _align(values, index, target_index):
                    if len(index) == 0:
                        return np.full(len(target_index), np.nan)
                    series = pd.Series(values, index=index).reindex(target_index)
                    return series.to_numpy(dtype=float)

                case_aligned = _align(case_values, case_index, adm_index)
                test_aligned = _align(test_values, test_index, adm_index)
                pos_aligned = _align(pos_values, pos_index, adm_index)
                revision_deltas = _revision_deltas(admissions_v, origin)
                usable_history = [
                    row
                    for row in history_scores
                    if row.get("region_id") == region_id and as_timestamp(row["matured_at"]) <= origin
                ]
                alerts = []
                if not ablations.get("no_anomaly"):
                    alerts = detect(
                        admissions=adm_values,
                        index=adm_index,
                        cases=case_aligned,
                        tests=test_aligned,
                        positivity=pos_aligned,
                        revision_deltas=revision_deltas,
                        error_records=usable_history,
                        source_id=None if train.empty else str(train.iloc[-1]["source_id"]),
                        import_batch_id=None if train.empty else str(train.iloc[-1].get("import_batch_id")),
                        cutoff=origin,
                    )
                    for draft in alerts:
                        db.add(
                            Alert(
                                experiment_id=experiment.id,
                                region_id=region_id,
                                category=draft.category,
                                level=draft.level,
                                rule=draft.rule,
                                metrics=draft.metrics,
                                period_start=as_timestamp(draft.period_start).to_pydatetime(),
                                period_end=as_timestamp(draft.period_end).to_pydatetime(),
                                source_id=draft.source_id,
                                import_batch_id=draft.import_batch_id,
                                supporting_evidence=draft.supporting_evidence,
                                conflicting_evidence=draft.conflicting_evidence,
                                limitations=draft.limitations,
                                affected_models=draft.affected_models,
                                review_status=draft.review_status,
                                created_at=utcnow(),
                                cutoff_time=as_timestamp(origin).to_pydatetime(),
                            )
                        )

                last_visible = last_visible_week_end(adm_index, adm_values)
                components, fixed_states[region_id] = _fit_components(
                    train, experiment.horizons, fixed_states[region_id], task.target_week_ends()
                )
                peers = {}
                for other_id in experiment.region_ids:
                    if other_id == region_id:
                        continue
                    other_train = _metric_frame(all_obs, other_id, experiment.target_metric, origin, experiment.replay_mode)
                    _other_index, other_values = prepare_weekly(other_train)
                    peers[other_id] = {
                        "values": other_values,
                        "context": contexts.get(other_id) or {},
                        "name": other_id,
                    }
                analog_model = AnalogShapeModel()
                analog_fit = analog_model.fit(
                    train,
                    {
                        "horizons": experiment.horizons,
                        "context": contexts.get(region_id) or {},
                        "peers": peers,
                    },
                )
                analog_pred = (
                    analog_model.predict(experiment.horizons, target_week_ends=task.target_week_ends())
                    if analog_fit.status == "success"
                    else None
                )
                analog_match = (analog_pred.metadata if analog_pred is not None else analog_model.serialize_metadata()).get("match")
                local_config = dict(experiment.config or {})
                local_config["analog_match"] = analog_match
                local_config["context"] = contexts.get(region_id) or {}
                # 若适应要求修订，则用修订后序列重训组件供 adaptive 使用
                adaptive_components = components
                if any(alert.category == "suspected_reporting_change" for alert in alerts) and not ablations.get(
                    "no_revision"
                ):
                    adaptive_components, _ = _fit_components(
                        revised_train, experiment.horizons, None, task.target_week_ends()
                    )

                predictions = _predict_arms(
                    components,
                    arm_ids=experiment.models,
                    history_scores=usable_history,
                    cutoff=origin,
                    alerts=alerts,
                    train_frame=train,
                    config=local_config,
                    ablations=ablations,
                    analog_pred=analog_pred,
                    target_week_ends=task.target_week_ends(),
                )
                if "adaptive" in experiment.models and adaptive_components is not components:
                    adaptive_only = _predict_arms(
                        adaptive_components,
                        arm_ids=["adaptive"],
                        history_scores=usable_history,
                        cutoff=origin,
                        alerts=alerts,
                        train_frame=revised_train,
                        config=local_config,
                        ablations=ablations,
                        analog_pred=analog_pred,
                        target_week_ends=task.target_week_ends(),
                    )
                    predictions["adaptive"] = adaptive_only["adaptive"]
                    if not ablations.get("no_uncertainty_propagation"):
                        sampled = sample_revised_frames(
                            train,
                            origin,
                            revision_profile,
                            n_samples=8,
                            seed=int(experiment.random_seed),
                            maturity_lag_days=experiment.maturity_lag_days,
                        )
                        predictions["adaptive"] = _mix_revision_samples(
                            predictions["adaptive"],
                            sampled,
                            experiment.horizons,
                            task.target_week_ends(),
                        )
                for arm in PANEL_ARMS:
                    if arm in experiment.models and (panel_pack.get(arm) or {}).get(region_id) is not None:
                        predictions[arm] = panel_pack[arm][region_id]

                _store_forecasts(
                    db,
                    experiment=experiment,
                    model_runs=model_runs,
                    region_id=region_id,
                    task=task,
                    predictions=predictions,
                    arm="main",
                    last_visible=last_visible,
                )
                db.flush()

        all_forecasts = db.query(Forecast).filter(Forecast.experiment_id == experiment.id).all()
        final_scores = _evaluate_forecasts(
            db,
            experiment=experiment,
            all_obs=all_obs,
            forecasts=all_forecasts,
            evaluation_as_of=evaluation_as_of,
            purpose="final",
            write_pending=True,
        )
        db.flush()
        # 用最终评分重建 history 不是必需；summary 直接基于 Evaluation
        for model_id, run in model_runs.items():
            if run.status == "pending":
                # 若完全没有成功预测
                any_forecast = any(item.model_id == model_id for item in all_forecasts)
                run.status = "success" if any_forecast else "degraded"
                if not any_forecast:
                    run.error_message = "该臂未产生成功预测。"

        summary = _summarize(db, experiment, labels)
        summary["replay"] = descriptor
        summary["origins"] = [item.isoformat() for item in origins]
        summary["ablations"] = ablations
        summary["n_forecasts"] = len(all_forecasts)
        summary["n_scored"] = len(final_scores)
        summary["update_log"] = update_events[-80:]
        summary["research_question"] = RESEARCH_QUESTION
        summary["mode"] = public_mode_payload(experiment.config)
        summary["feedback"] = {
            "online_scores": sum(1 for row in history_scores if row.get("status") == "scored"),
            "final_scores": len(final_scores),
            "note": "在线成熟评分用于当时权重；最终补评不回写过去原点的权重。",
        }
        summary["notes"] = research_notes(experiment.config)
        summary["identifiability"] = (
            "分层动态更新只调整地区截距和短期斜率。疫苗覆盖率不是免疫比例。"
            "地区效应不是生物学差异证明。没有分年龄住院时不识别年龄组传播。"
        )
        summary["holdout_region_ids"] = list((experiment.config or {}).get("holdout_region_ids") or [])
        experiment.summary = summary
        experiment.status = "completed"
        experiment.finished_at = utcnow()
        db.commit()
        db.refresh(experiment)
        return experiment
    except Exception as exc:  # noqa: BLE001
        experiment.status = "failed"
        experiment.error_message = str(exc)
        experiment.finished_at = utcnow()
        db.commit()
        raise


def export_experiment(db: Session, experiment_id: str) -> dict:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise ValueError("实验不存在")
    forecasts = db.query(Forecast).filter(Forecast.experiment_id == experiment_id).all()
    evaluations = db.query(Evaluation).filter(Evaluation.experiment_id == experiment_id).all()
    alerts = db.query(Alert).filter(Alert.experiment_id == experiment_id).all()
    runs = db.query(ModelRun).filter(ModelRun.experiment_id == experiment_id).all()
    return {
        "experiment": {
            "id": experiment.id,
            "name": experiment.name,
            "disease_id": experiment.disease_id,
            "region_ids": experiment.region_ids,
            "target_metric": experiment.target_metric,
            "cutoff_time": experiment.cutoff_time.isoformat(sep=" ", timespec="seconds"),
            "train_window_weeks": experiment.train_window_weeks,
            "horizons": experiment.horizons,
            "models": experiment.models,
            "replay_mode": experiment.replay_mode,
            "maturity_lag_days": experiment.maturity_lag_days,
            "random_seed": experiment.random_seed,
            "config": experiment.config,
            "config_hash": experiment.config_hash,
            "input_hash": experiment.input_hash,
            "code_version": experiment.code_version,
            "dependency_versions": experiment.dependency_versions,
            "status": experiment.status,
            "summary": experiment.summary,
        },
        "model_runs": [
            {
                "id": run.id,
                "model_id": run.model_id,
                "status": run.status,
                "error_message": run.error_message,
                "metadata": run.metadata_json,
            }
            for run in runs
        ],
        "forecasts": [
            {
                "model_id": row.model_id,
                "region_id": row.region_id,
                "cutoff_time": row.cutoff_time.isoformat(sep=" ", timespec="seconds"),
                "target_period_end": row.target_period_end.date().isoformat(),
                "horizon": row.horizon,
                "quantiles": {
                    "0.025": row.q025,
                    "0.1": row.q10,
                    "0.25": row.q25,
                    "0.5": row.q50,
                    "0.75": row.q75,
                    "0.9": row.q90,
                    "0.975": row.q975,
                },
                "interval_method": row.interval_method,
                "quality_note": row.quality_note,
                "weights": row.weights,
            }
            for row in forecasts
        ],
        "evaluations": [
            {
                "model_id": row.model_id,
                "region_id": row.region_id,
                "horizon": row.horizon,
                "target_period_end": row.target_period_end.date().isoformat(),
                "status": row.status,
                "actual_value": row.actual_value,
                "abs_error": row.abs_error,
                "wis": row.wis,
                "covered_50": row.covered_50,
                "covered_80": row.covered_80,
                "covered_95": row.covered_95,
                "phase": row.phase,
                "purpose": getattr(row, "purpose", "final"),
                "notes": row.notes,
            }
            for row in evaluations
        ],
        "alerts": [
            {
                "id": row.id,
                "region_id": row.region_id,
                "category": row.category,
                "rule": row.rule,
                "supporting_evidence": row.supporting_evidence,
                "conflicting_evidence": row.conflicting_evidence,
                "limitations": row.limitations,
                "review_status": row.review_status,
                "period_end": row.period_end.date().isoformat(),
            }
            for row in alerts
        ],
        "disclaimer": "研究原型导出。不得作为临床或公共卫生决策指令。合成演示成功不代表现实有效。",
    }


def experiment_to_dict(experiment: Experiment) -> dict:
    return {
        "id": experiment.id,
        "name": experiment.name,
        "disease_id": experiment.disease_id,
        "region_ids": experiment.region_ids,
        "target_metric": experiment.target_metric,
        "cutoff_time": experiment.cutoff_time.isoformat(sep=" ", timespec="seconds"),
        "train_window_weeks": experiment.train_window_weeks,
        "horizons": experiment.horizons,
        "models": experiment.models,
        "replay_mode": experiment.replay_mode,
        "maturity_lag_days": experiment.maturity_lag_days,
        "random_seed": experiment.random_seed,
        "config": experiment.config,
        "config_hash": experiment.config_hash,
        "input_hash": experiment.input_hash,
        "code_version": experiment.code_version,
        "dependency_versions": experiment.dependency_versions,
        "status": experiment.status,
        "error_message": experiment.error_message,
        "created_at": experiment.created_at.isoformat(sep=" ", timespec="seconds"),
        "started_at": None if experiment.started_at is None else experiment.started_at.isoformat(sep=" ", timespec="seconds"),
        "finished_at": None if experiment.finished_at is None else experiment.finished_at.isoformat(sep=" ", timespec="seconds"),
        "summary": experiment.summary,
    }
