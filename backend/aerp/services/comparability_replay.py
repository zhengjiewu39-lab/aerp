"""Compare naive ranking with comparability-filtered ranking on a real snapshot.

Does not report transmission-change accuracy unless the caller supplies manual labels.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from aerp.core.change_labels import diagnosis_report
from aerp.services.map_data import map_observations, revision_contrast


def _rank(rows: list[dict[str, Any]]) -> dict[str, int]:
    ordered = sorted(
        [item for item in rows if item.get("count") is not None],
        key=lambda item: (-float(item["count"]), item["geo_id"]),
    )
    return {item["geo_id"]: idx + 1 for idx, item in enumerate(ordered)}


def ranking_attribution(
    db: Session,
    *,
    disease_id: str,
    metric: str,
    period_end: str | None,
    previous_period_end: str | None = None,
    view: str = "count",
    data_mode: str = "real",
    parent_geo_id: str | None = None,
    labels=None,
) -> dict[str, Any]:
    current = map_observations(
        db,
        disease_id=disease_id,
        metric=metric,
        period_end=period_end,
        view=view,
        data_mode=data_mode,
        parent_geo_id=parent_geo_id,
    )
    features = [item for item in current.get("features") or [] if item.get("data_status") not in {None}]
    valued = [item for item in features if item.get("count") is not None]
    naive = _rank(valued)
    comparable = [item for item in valued if item.get("rank_eligible")]
    filtered = _rank(comparable)
    rank_shifts = []
    for item in valued:
        geo_id = item["geo_id"]
        if geo_id not in filtered:
            rank_shifts.append({"geo_id": geo_id, "name": item.get("name"), "reason": (item.get("comparability") or {}).get("reason") or item.get("data_status"), "naive_rank": naive.get(geo_id), "comparable_rank": None})
            continue
        if naive.get(geo_id) != filtered.get(geo_id):
            rank_shifts.append({"geo_id": geo_id, "name": item.get("name"), "naive_rank": naive.get(geo_id), "comparable_rank": filtered.get(geo_id), "reason": "口径过滤后名次变化"})
    prev = None
    trend_bin_changes = 0
    if previous_period_end:
        prev = map_observations(
            db,
            disease_id=disease_id,
            metric=metric,
            period_end=previous_period_end,
            view=view,
            data_mode=data_mode,
            parent_geo_id=parent_geo_id,
            lock_period_end=current.get("period_end"),
        )
        prev_by = {item["geo_id"]: item for item in prev.get("features") or []}
        for item in valued:
            earlier = prev_by.get(item["geo_id"])
            if earlier and earlier.get("bin") != item.get("bin") and item.get("bin") is not None and earlier.get("bin") is not None:
                trend_bin_changes += 1
    revision_cross = []
    for item in comparable[:80]:
        contrast = revision_contrast(
            db,
            geo_id=item["geo_id"],
            disease_id=disease_id,
            metric=metric,
            period_end=current.get("period_end"),
            source_id=item.get("source_id"),
        )
        if contrast.get("comparable") and contrast.get("delta") not in (None, 0):
            revision_cross.append({"geo_id": item["geo_id"], "delta": contrast.get("delta"), "first": contrast.get("first"), "latest": contrast.get("latest")})
    diagnosis = diagnosis_report(None, labels)
    return {
        "snapshot_hash": current.get("snapshot_hash"),
        "period_end": current.get("period_end"),
        "previous_period_end": previous_period_end,
        "n_valued": len(valued),
        "n_comparable": len(comparable),
        "n_excluded_from_rank": len(valued) - len(comparable),
        "n_rank_shifts_from_comparability": len(rank_shifts),
        "n_week_to_week_bin_changes": trend_bin_changes,
        "n_same_week_revisions": len(revision_cross),
        "rank_shifts_preview": rank_shifts[:20],
        "revision_preview": revision_cross[:20],
        "note": "周与周分级变化是报告趋势变化。同一周多版本差值才是数据订正。二者都不能自动归因于传播。",
        "transmission_accuracy": diagnosis,
        "is_demo": current.get("is_demo"),
    }
