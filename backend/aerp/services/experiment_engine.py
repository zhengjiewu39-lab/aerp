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
from aerp.core.anomaly import detect
from aerp.core.hashing import canonical_hash, hash_observations
from aerp.core.models.catalog import MODEL_CARDS
from aerp.core.models.damped import DampedTrendModel
from aerp.core.models.ensemble import apply_downweight, combine_predictions, dynamic_weights, equal_weight_map
from aerp.core.models.negbin import NegBinGLMModel
from aerp.core.models.persistence import PersistenceModel
from aerp.core.phases import classify_phase
from aerp.core.quantiles import scale_around_median
from aerp.core.revision import apply_nowcast, estimate_revision
from aerp.core.scoring import score_forecast
from aerp.core.series import frame_from_grid, prepare_weekly, tail_weeks
from aerp.core.skill import alert_skill, recovery_summary
from aerp.core.synthetic import suggested_origins
from aerp.core.timeutil import as_timestamp, utcnow
from aerp.core.vintages import has_multiple_vintages, mature_value, replay_descriptor, select_snapshot
from aerp.db.models import Alert, Evaluation, Experiment, Forecast, ModelRun
from aerp.services.observations import labels_frame, observations_frame


COMPONENT_MODELS = ("persistence", "damped_trend", "retrain_glm")
DEFAULT_ARMS = ("persistence", "fixed_glm", "retrain_glm", "equal_weight", "dynamic_weight", "adaptive")


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
    config = {
        "ablations": payload.get("ablations") or {"no_revision": False, "no_anomaly": False, "no_dynamic_weight": False},
        "origins": payload.get("origins"),
        "short_window_weeks": payload.get("short_window_weeks", 26),
        "compare_arms": True,
        "demo_note": "结果由真实计算生成，不保证适应系统优于等权或其他对照。",
    }
    config.update(payload.get("config") or {})
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
        dependency_versions=dependency_versions(),
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


def _fit_components(train: pd.DataFrame, horizons: list[int], fixed_glm_state: dict | None):
    models = {
        "persistence": PersistenceModel(),
        "damped_trend": DampedTrendModel(),
        "retrain_glm": NegBinGLMModel("retrain_glm"),
    }
    results = {}
    for model_id, model in models.items():
        fit = model.fit(train, {"horizons": horizons})
        pred = model.predict(horizons) if fit.status == "success" else None
        results[model_id] = {"model": model, "fit": fit, "predict": pred}
    if fixed_glm_state is None:
        fixed = NegBinGLMModel("fixed_glm")
        fit = fixed.fit(train, {"horizons": horizons})
        pred = fixed.predict(horizons) if fit.status == "success" else None
        fixed_glm_state = {"model": fixed, "fit": fit, "anchor": None if train.empty else pd.to_datetime(train["period_end"]).max()}
        fixed_glm_state["predict"] = pred
    else:
        fixed = fixed_glm_state["model"]
        if fixed_glm_state["fit"].status == "success":
            pred = fixed.predict(horizons, anchor=fixed_glm_state.get("anchor"))
        else:
            pred = None
        fixed_glm_state["predict"] = pred
    results["fixed_glm"] = fixed_glm_state
    return results, fixed_glm_state


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
) -> dict:
    outputs = {}
    component_preds = [components[item]["predict"] for item in COMPONENT_MODELS if components[item].get("predict")]
    equal_weights = equal_weight_map([pred.model_id for pred in component_preds])
    dyn_weights, dyn_note = dynamic_weights(history_scores, list(equal_weights), cutoff)
    if ablations.get("no_dynamic_weight"):
        dyn_weights, dyn_note = equal_weights, "消融：删除动态权重，退回等权。"

    decision = AdaptationDecision()
    if not ablations.get("no_anomaly"):
        decision = decide(alerts, config)
        if decision.downweight_models:
            dyn_weights = apply_downweight(dyn_weights, decision.downweight_models, 0.5)

    window_choice = "long"
    window_note = ""
    adaptive_train = train_frame
    if decision.compare_windows and not ablations.get("no_anomaly"):
        short_weeks = int(config.get("short_window_weeks", 26))
        short_wis, short_ok = validation_wis(train_frame, short_weeks)
        long_wis, long_ok = validation_wis(train_frame, None)
        window_choice, window_note = choose_window(short_wis, long_wis, short_ok, long_ok)
        if window_choice == "short":
            adaptive_train = tail_weeks(train_frame, short_weeks)
            # 短窗口下重训阻尼趋势作为适应候选
            short_model = DampedTrendModel()
            short_fit = short_model.fit(adaptive_train, {"horizons": [1, 2, 3, 4], "min_obs": 8, "min_residuals": 4})
            if short_fit.status == "success":
                components = dict(components)
                components["damped_trend"] = {
                    "model": short_model,
                    "fit": short_fit,
                    "predict": short_model.predict([1, 2, 3, 4]),
                }
                component_preds = [components[item]["predict"] for item in COMPONENT_MODELS if components[item].get("predict")]

    if "persistence" in arm_ids and components["persistence"].get("predict"):
        outputs["persistence"] = components["persistence"]["predict"]
    if "fixed_glm" in arm_ids and components["fixed_glm"].get("predict"):
        outputs["fixed_glm"] = components["fixed_glm"]["predict"]
    if "retrain_glm" in arm_ids and components["retrain_glm"].get("predict"):
        outputs["retrain_glm"] = components["retrain_glm"]["predict"]
    if "damped_trend" in arm_ids and components["damped_trend"].get("predict"):
        outputs["damped_trend"] = components["damped_trend"]["predict"]
    if "equal_weight" in arm_ids:
        outputs["equal_weight"] = combine_predictions(
            component_preds,
            equal_weights,
            model_id="equal_weight",
            note="等权分位数平均，强制对照。" + (candidate_note(component_preds) or ""),
        )
    if "dynamic_weight" in arm_ids:
        outputs["dynamic_weight"] = combine_predictions(
            component_preds,
            dyn_weights,
            model_id="dynamic_weight",
            note=dyn_note + " " + (candidate_note(component_preds) or ""),
        )
    if "adaptive" in arm_ids:
        adaptive_components = component_preds
        if decision.use_revision_adjustment and not ablations.get("no_revision"):
            note_extra = "已启用修订模块调整训练输入；修订不确定性第一版未完整传入预测区间。"
        else:
            note_extra = "未启用修订调整。" if ablations.get("no_revision") else "规则未要求修订调整。"
        adaptive = combine_predictions(
            adaptive_components,
            dyn_weights,
            model_id="adaptive",
            note="异常识别与适应规则。" + " ".join(decision.notes) + " " + window_note + " " + note_extra,
        )
        if adaptive.status == "success" and decision.interval_scale != 1.0:
            for horizon, item in adaptive.horizons.items():
                item.quantiles = scale_around_median(item.quantiles, decision.interval_scale)
                item.method = item.method + f"|interval_scale={decision.interval_scale}"
                item.quality_note += " 区间扩大倍率需由回放覆盖率检验，不保证名义覆盖。"
        outputs["adaptive"] = adaptive
        outputs["_adaptive_meta"] = {
            "decision": decision.__dict__,
            "window_choice": window_choice,
            "weights": dyn_weights,
        }
    outputs["_equal_weights"] = equal_weights
    outputs["_dynamic_weights"] = dyn_weights
    return outputs


def _store_forecasts(
    db: Session,
    *,
    experiment: Experiment,
    model_runs: dict[str, ModelRun],
    region_id: str,
    cutoff,
    predictions: dict,
    arm: str,
) -> list[Forecast]:
    saved = []
    generated_at = utcnow()
    for model_id, result in predictions.items():
        if model_id.startswith("_") or result is None or result.status != "success":
            continue
        run = model_runs[model_id]
        run.status = "success"
        run.trained_at = generated_at
        run.metadata_json = result.metadata
        for horizon, item in result.horizons.items():
            target_end = as_timestamp(cutoff) + pd.Timedelta(days=7 * int(horizon) - 1)
            # cutoff 是 period_end+1 的常见原点；目标周结束 = cutoff 当周起向后 horizon 周
            # 更稳妥：以 cutoff 前最近可见 period_end 为锚
            anchor = as_timestamp(cutoff) - pd.Timedelta(days=1)
            target_end = anchor + pd.Timedelta(days=7 * int(horizon))
            target_start = target_end - pd.Timedelta(days=6)
            q = item.quantiles
            row = Forecast(
                experiment_id=experiment.id,
                model_run_id=run.id,
                model_id=model_id,
                region_id=region_id,
                metric=experiment.target_metric,
                issued_at=as_timestamp(cutoff).to_pydatetime(),
                cutoff_time=as_timestamp(cutoff).to_pydatetime(),
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
                arm=arm,
                weights=result.metadata.get("weights") or {},
                extra={"message": result.message},
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
) -> list[dict]:
    score_rows = []
    # 用最终成熟轨迹做阶段标签（事后分组，不参与模型选择）
    phase_series_cache: dict[str, pd.Series] = {}
    for forecast in forecasts:
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
            notes="目标版本=固定成熟滞后可见值，不等于最终真实值。",
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
    evaluations = db.query(Evaluation).filter(Evaluation.experiment_id == experiment.id).all()
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
        "alert_skill": skill,
        "recovery": recovery,
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
        for model_id in experiment.models:
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

        for origin in origins:
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

                components, fixed_states[region_id] = _fit_components(
                    train, experiment.horizons, fixed_states[region_id]
                )
                # 若适应要求修订，则用修订后序列重训组件供 adaptive 使用
                adaptive_components = components
                if any(alert.category == "suspected_reporting_change" for alert in alerts) and not ablations.get("no_revision"):
                    adaptive_components, _ = _fit_components(revised_train, experiment.horizons, None)

                predictions = _predict_arms(
                    components,
                    arm_ids=experiment.models,
                    history_scores=usable_history,
                    cutoff=origin,
                    alerts=alerts,
                    train_frame=train,
                    config=experiment.config,
                    ablations=ablations,
                )
                if "adaptive" in experiment.models and adaptive_components is not components:
                    adaptive_only = _predict_arms(
                        adaptive_components,
                        arm_ids=["adaptive"],
                        history_scores=usable_history,
                        cutoff=origin,
                        alerts=alerts,
                        train_frame=revised_train,
                        config=experiment.config,
                        ablations=ablations,
                    )
                    predictions["adaptive"] = adaptive_only["adaptive"]

                saved = _store_forecasts(
                    db,
                    experiment=experiment,
                    model_runs=model_runs,
                    region_id=region_id,
                    cutoff=origin,
                    predictions=predictions,
                    arm="main",
                )
                db.flush()
                # 增量评价：只评价在当前 origin 已成熟的历史预测，供后续动态权重使用
                new_scores = _evaluate_forecasts(
                    db,
                    experiment=experiment,
                    all_obs=all_obs,
                    forecasts=saved,
                    evaluation_as_of=origin,
                )
                history_scores.extend(new_scores)

        # 最终用实验 cutoff 再补评一次，标记仍未成熟者为 pending
        all_forecasts = db.query(Forecast).filter(Forecast.experiment_id == experiment.id).all()
        # 清除中间评价后统一按 cutoff 评价，避免重复
        db.query(Evaluation).filter(Evaluation.experiment_id == experiment.id).delete()
        db.flush()
        final_scores = _evaluate_forecasts(
            db,
            experiment=experiment,
            all_obs=all_obs,
            forecasts=all_forecasts,
            evaluation_as_of=evaluation_as_of,
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
