from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from aerp.core.csvcheck import validate_csv_bytes
from aerp.core.timeutil import utcnow
from aerp.db.models import DataSource, Disease, ImportBatch, ObservationVintage, Region


TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "data" / "observation_template.csv"


def template_bytes() -> bytes:
    return TEMPLATE_PATH.read_bytes()


def preview_csv(payload: bytes) -> dict:
    return validate_csv_bytes(payload)


def import_csv(
    db: Session,
    payload: bytes,
    *,
    filename: str,
    source_name: str | None = None,
    source_id: str | None = None,
) -> dict:
    checked = validate_csv_bytes(payload)
    if checked["errors"] or not checked.get("valid_rows"):
        return {"imported": False, "validation": checked}
    batch_id = f"import-{uuid4().hex[:12]}"
    resolved_source_id = source_id or f"csv-{uuid4().hex[:8]}"
    source = db.get(DataSource, resolved_source_id)
    if source is None:
        db.add(
            DataSource(
                id=resolved_source_id,
                name=source_name or filename or resolved_source_id,
                url="",
                license="用户导入；系统不核验是否为真实监测数据。",
                update_note="CSV 导入",
                connector_status="manual_import",
            )
        )
    for row in checked["valid_rows"]:
        if db.get(Disease, row["disease_id"]) is None:
            db.add(
                Disease(
                    id=row["disease_id"],
                    name=row["disease_id"],
                    description="由 CSV 导入创建的疾病标识。",
                    is_synthetic=False,
                )
            )
        if db.get(Region, row["region_id"]) is None:
            db.add(
                Region(
                    id=row["region_id"],
                    name=row["region_id"],
                    population=None,
                    scenario=None,
                    note="由 CSV 导入创建。无人口分母时不计算人均指标。",
                )
            )
    db.add(
        ImportBatch(
            id=batch_id,
            source_id=resolved_source_id,
            file_hash=checked["file_hash"],
            filename=filename,
            imported_at=utcnow(),
            row_count=len(checked["valid_rows"]),
            note="CSV 导入。原始版本追加保存，不覆盖旧版本。",
            is_synthetic=False,
        )
    )
    for row in checked["valid_rows"]:
        db.add(
            ObservationVintage(
                disease_id=row["disease_id"],
                region_id=row["region_id"],
                period_start=datetime.fromisoformat(row["period_start"]),
                period_end=datetime.fromisoformat(row["period_end"]),
                issued_at=datetime.fromisoformat(row["issued_at"]),
                metric=row["metric"],
                value=row["value"],
                unit=row["unit"],
                source_id=row["source_id"] if row["source_id"] else resolved_source_id,
                quality_flag=row["quality_flag"],
                import_batch_id=batch_id,
            )
        )
    db.commit()
    return {
        "imported": True,
        "batch_id": batch_id,
        "file_hash": checked["file_hash"],
        "row_count": len(checked["valid_rows"]),
        "source_id": resolved_source_id,
        "validation": {
            "warnings": checked["warnings"],
            "accepted_row_count": checked["accepted_row_count"],
            "error_row_count": 0,
        },
    }


def list_imports(db: Session) -> list[dict]:
    rows = db.query(ImportBatch).order_by(ImportBatch.imported_at.desc()).all()
    return [
        {
            "id": row.id,
            "source_id": row.source_id,
            "file_hash": row.file_hash,
            "filename": row.filename,
            "imported_at": row.imported_at.isoformat(sep=" ", timespec="seconds"),
            "row_count": row.row_count,
            "note": row.note,
            "is_synthetic": row.is_synthetic,
        }
        for row in rows
    ]
