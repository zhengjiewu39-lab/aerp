from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from aerp.core.timeutil import as_timestamp


@dataclass
class AlertDraft:
    category: str
    level: str
    rule: str
    metrics: list[str]
    period_start: str
    period_end: str
    source_id: str | None
    import_batch_id: str | None
    supporting_evidence: str
    conflicting_evidence: str
    limitations: str
    affected_models: list[str] = field(default_factory=list)
    review_status: str = "unreviewed"

    def as_dict(self) -> dict:
        return {
            "category": self.category,
            "level": self.level,
            "rule": self.rule,
            "metrics": self.metrics,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "source_id": self.source_id,
            "import_batch_id": self.import_batch_id,
            "supporting_evidence": self.supporting_evidence,
            "conflicting_evidence": self.conflicting_evidence,
            "limitations": self.limitations,
            "affected_models": self.affected_models,
            "review_status": self.review_status,
        }


def _params(values: np.ndarray, scan: int, floor: float = 3.5):
    if len(values) < scan + 12:
        return None
    train = np.asarray(values[:-scan], dtype=float)
    finite = train[np.isfinite(train)]
    if finite.size < 12:
        return None
    center = float(np.median(finite))
    scale = 1.4826 * float(np.median(np.abs(finite - center)))
    if scale < 1e-6:
        scale = float(np.std(finite))
    if scale < 1e-6:
        return None
    scores = np.abs((finite - center) / scale)
    threshold = float(max(floor, np.quantile(scores, 0.99)))
    return center, scale, threshold


def _z(value: float, params) -> float:
    if params is None or not np.isfinite(value):
        return 0.0
    center, scale, _threshold = params
    return float((value - center) / scale)


def _threshold(params) -> float:
    return 3.5 if params is None else float(params[2])


def _cusum_recent(values: np.ndarray, scan: int) -> bool:
    if len(values) < scan + 12:
        return False
    train_diffs = []
    for index in range(1, len(values) - scan):
        if np.isfinite(values[index]) and np.isfinite(values[index - 1]):
            train_diffs.append(float(values[index] - values[index - 1]))
    if len(train_diffs) < 12:
        return False
    train = np.asarray(train_diffs)
    sigma = float(np.std(train))
    if sigma < 1e-6:
        return False
    allowance = float(np.mean(train) + 0.5 * sigma)
    limit = 5.0 * sigma
    state = 0.0
    recent = False
    for index in range(1, len(values)):
        if not (np.isfinite(values[index]) and np.isfinite(values[index - 1])):
            continue
        state = max(0.0, state + float(values[index] - values[index - 1]) - allowance)
        if state > limit:
            recent = index >= len(values) - scan
            state = 0.0
    return recent


def _best_lag(cases: np.ndarray, admissions: np.ndarray, scan: int, max_lag: int = 4) -> int:
    if cases is None or len(cases) <= scan + 10:
        return 2
    left = cases[:-scan]
    right = admissions[:-scan]
    best_lag = 2
    best_corr = -np.inf
    found = False
    for lag in range(0, max_lag + 1):
        if lag == 0:
            left_lag, right_lag = left, right
        else:
            left_lag, right_lag = left[:-lag], right[lag:]
        mask = np.isfinite(left_lag) & np.isfinite(right_lag)
        if int(mask.sum()) < 10:
            continue
        if float(np.std(left_lag[mask])) < 1e-9 or float(np.std(right_lag[mask])) < 1e-9:
            continue
        corr = float(np.corrcoef(left_lag[mask], right_lag[mask])[0, 1])
        found = True
        if corr > best_corr:
            best_corr = corr
            best_lag = lag
    return best_lag if found else 2


def _period_strings(index: pd.DatetimeIndex) -> tuple[str, str]:
    end = pd.Timestamp(index[-1])
    start = end - pd.Timedelta(days=6)
    return start.date().isoformat(), end.date().isoformat()


def _error_alerts(error_records: list[dict] | None, cutoff, period_start: str, period_end: str, source_id, batch_id, limitations: str) -> list[AlertDraft]:
    if not error_records:
        return []
    usable = []
    for record in error_records:
        matured = record.get("matured_at")
        if matured is None or record.get("wis") is None:
            continue
        if cutoff is not None and as_timestamp(matured) > as_timestamp(cutoff):
            continue
        usable.append(record)
    alerts = []
    model_ids = sorted({record["model_id"] for record in usable})
    worsened = []
    for model_id in model_ids:
        rows = [record for record in usable if record["model_id"] == model_id]
        rows.sort(key=lambda record: as_timestamp(record["target_period_end"]))
        if len(rows) < 6:
            continue
        earlier = np.mean([float(record["wis"]) for record in rows[:-3]])
        recent = np.mean([float(record["wis"]) for record in rows[-3:]])
        if earlier > 0 and recent > 1.75 * earlier:
            worsened.append(model_id)
    if not worsened:
        return alerts
    if len(worsened) < len(model_ids):
        alerts.append(
            AlertDraft(
                category="insufficient_evidence",
                level="watch",
                rule="forecast_error_deterioration",
                metrics=["hospital_admissions"],
                period_start=period_start,
                period_end=period_end,
                source_id=source_id,
                import_batch_id=batch_id,
                supporting_evidence=f"模型 {', '.join(worsened)} 最近 3 次已成熟预测的平均 WIS 高于更早阶段的 1.75 倍。",
                conflicting_evidence="其他候选模型没有同步恶化，因此不把这一现象单独解释为疫情机制变化。",
                limitations=limitations + " 误差比较只使用已经成熟的预测。",
                affected_models=worsened,
            )
        )
    return alerts


def detect(
    *,
    admissions: np.ndarray,
    index: pd.DatetimeIndex,
    cases: np.ndarray | None = None,
    tests: np.ndarray | None = None,
    positivity: np.ndarray | None = None,
    revision_deltas: np.ndarray | None = None,
    error_records: list[dict] | None = None,
    source_id: str | None = None,
    import_batch_id: str | None = None,
    cutoff=None,
    scan_weeks: int = 4,
) -> list[AlertDraft]:
    if index is None or len(index) == 0 or len(admissions) == 0:
        return []
    limitations = "阈值只在扫描窗口之前的数据上计算。分类不是已确认的传播机制。病例与住院未同步时，不直接认定报告过程变化。"
    period_start, period_end = _period_strings(index)
    scan = admissions[-scan_weeks:]
    missing_share = float(np.mean(~np.isfinite(scan))) if len(scan) else 1.0
    alerts: list[AlertDraft] = []
    if missing_share >= 0.5:
        alerts.append(
            AlertDraft(
                category="data_quality",
                level="watch",
                rule="missingness_scan",
                metrics=["hospital_admissions"],
                period_start=period_start,
                period_end=period_end,
                source_id=source_id,
                import_batch_id=import_batch_id,
                supporting_evidence=f"最近 {scan_weeks} 周中有 {missing_share:.0%} 的住院记录缺失。",
                conflicting_evidence="缺失本身不能说明感染趋势上升或下降。",
                limitations=limitations + " 缺失值没有被当成 0。",
            )
        )
        alerts.extend(_error_alerts(error_records, cutoff, period_start, period_end, source_id, import_batch_id, limitations))
        return alerts

    adm_params = _params(admissions, scan_weeks)
    case_params = _params(cases, scan_weeks) if cases is not None else None
    test_params = _params(tests, scan_weeks) if tests is not None else None
    pos_params = _params(positivity, scan_weeks) if positivity is not None else None
    adm_now = _z(float(admissions[-1]) if np.isfinite(admissions[-1]) else np.nan, adm_params)
    case_now = _z(float(cases[-1]), case_params) if cases is not None and np.isfinite(cases[-1]) else 0.0
    test_now = _z(float(tests[-1]), test_params) if tests is not None and np.isfinite(tests[-1]) else 0.0
    pos_now = _z(float(positivity[-1]), pos_params) if positivity is not None and np.isfinite(positivity[-1]) else 0.0
    adm_thr = _threshold(adm_params)
    case_thr = _threshold(case_params)
    test_thr = _threshold(test_params)
    pos_thr = _threshold(pos_params)
    cusum_hit = _cusum_recent(np.asarray(admissions, dtype=float), scan_weeks)
    admission_move = abs(adm_now) > adm_thr or cusum_hit
    lag = _best_lag(cases, admissions, scan_weeks) if cases is not None else 2
    if cases is not None and len(cases) > lag and np.isfinite(cases[-1 - lag]):
        case_leading = _z(float(cases[-1 - lag]), case_params)
    else:
        case_leading = case_now
    direction = 1.0 if adm_now >= 0 else -1.0
    cases_agree = case_leading * direction > case_thr
    positivity_agrees = pos_now * direction > pos_thr
    tests_jump = abs(test_now) > test_thr
    positivity_flat = abs(pos_now) <= pos_thr
    admissions_flat = abs(adm_now) <= adm_thr

    revision_flag = False
    revision_text = ""
    if revision_deltas is not None and len(revision_deltas) >= 9 and np.isfinite(revision_deltas[-1]):
        train = np.asarray(revision_deltas[:-1], dtype=float)
        train = train[np.isfinite(train)]
        if train.size >= 8:
            center = float(np.median(train))
            scale = 1.4826 * float(np.median(np.abs(train - center)))
            if scale < 1e-6:
                scale = float(np.std(train))
            if scale > 1e-6:
                score = abs((float(revision_deltas[-1]) - center) / scale)
                threshold = max(3.5, float(np.quantile(np.abs((train - center) / scale), 0.99)))
                revision_flag = score > threshold
                revision_text = f"最近可见修订量为 {float(revision_deltas[-1]):.2f}，稳健标准化绝对值 {score:.2f}，训练阈值 {threshold:.2f}。修订可以为负。"

    if revision_flag and not admission_move:
        alerts.append(
            AlertDraft(
                category="suspected_reporting_change",
                level="watch",
                rule="revision_magnitude",
                metrics=["hospital_admissions"],
                period_start=period_start,
                period_end=period_end,
                source_id=source_id,
                import_batch_id=import_batch_id,
                supporting_evidence=revision_text,
                conflicting_evidence="同期住院水平没有超过训练阈值，因此不把修订异常直接解释为感染趋势变化。",
                limitations=limitations,
            )
        )
    elif admission_move and cases_agree and positivity_agrees and not tests_jump:
        alerts.append(
            AlertDraft(
                category="suspected_epidemic_change",
                level="watch",
                rule="growth_or_turning_with_lagged_indicators",
                metrics=["hospital_admissions", "reported_cases", "positivity"],
                period_start=period_start,
                period_end=period_end,
                source_id=source_id,
                import_batch_id=import_batch_id,
                supporting_evidence=(
                    f"住院稳健标准化值 {adm_now:.2f}，CUSUM 命中：{cusum_hit}。"
                    f"按训练数据选择的病例领先 {lag} 周，其标准化值 {case_leading:.2f}，阳性比例标准化值 {pos_now:.2f}。"
                ),
                conflicting_evidence="检测量没有同时越过阈值。" if not tests_jump else "检测量也有变化，需人工核对。",
                limitations=limitations + " 领先周数只在扫描窗口之前估计。",
            )
        )
    elif abs(case_now) > case_thr and tests_jump and positivity_flat and admissions_flat:
        alerts.append(
            AlertDraft(
                category="suspected_reporting_change",
                level="watch",
                rule="testing_or_reporting_shift",
                metrics=["reported_cases", "tests", "positivity", "hospital_admissions"],
                period_start=period_start,
                period_end=period_end,
                source_id=source_id,
                import_batch_id=import_batch_id,
                supporting_evidence=f"病例标准化值 {case_now:.2f}，检测量标准化值 {test_now:.2f}，阳性比例标准化值 {pos_now:.2f}。",
                conflicting_evidence=f"住院在预期滞后 {lag} 周的方向上没有同步越过阈值，所以不把病例上升单独当成疫情动态。",
                limitations=limitations,
            )
        )
    elif admission_move or abs(case_now) > case_thr or revision_flag:
        alerts.append(
            AlertDraft(
                category="insufficient_evidence",
                level="info",
                rule="incomplete_cross_signal",
                metrics=["hospital_admissions", "reported_cases"],
                period_start=period_start,
                period_end=period_end,
                source_id=source_id,
                import_batch_id=import_batch_id,
                supporting_evidence=(
                    f"住院标准化值 {adm_now:.2f}，病例标准化值 {case_now:.2f}，"
                    f"领先 {lag} 周的病例标准化值 {case_leading:.2f}，阳性比例标准化值 {pos_now:.2f}。"
                ),
                conflicting_evidence="指标之间的时间和方向不一致，或缺少阳性比例、检测量的同步支持。",
                limitations=limitations,
            )
        )
    alerts.extend(_error_alerts(error_records, cutoff, period_start, period_end, source_id, import_batch_id, limitations))
    return alerts
