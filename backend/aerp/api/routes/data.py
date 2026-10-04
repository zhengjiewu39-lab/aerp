from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from aerp.db.session import get_db
from aerp.services.importing import import_csv, list_imports, preview_csv, template_bytes
from aerp.services.observations import completeness

router = APIRouter()


@router.get("/data/template")
def download_template():
    return Response(
        content=template_bytes(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="aerp_observation_template.csv"'},
    )


@router.post("/data/preview")
async def preview(file: UploadFile = File(...)):
    payload = await file.read()
    return preview_csv(payload)


@router.post("/data/import")
async def import_data(
    file: UploadFile = File(...),
    source_name: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    payload = await file.read()
    result = import_csv(db, payload, filename=file.filename or "upload.csv", source_name=source_name)
    if not result["imported"]:
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/imports")
def imports(db: Session = Depends(get_db)):
    return list_imports(db)


@router.get("/completeness")
def completeness_view(disease_id: str, region_id: str, db: Session = Depends(get_db)):
    return completeness(db, disease_id, region_id)
