# backend/routers/recibos.py

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from typing import List
from pathlib import Path
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Recibo
from schemas import ReciboOut
from routers.users import get_current_user, require_admin, User
from utils.zip_processor import procesar_zip

router = APIRouter(prefix="/recibos", tags=["Recibos"])

@router.get("/", response_model=List[ReciboOut])
def list_recibos(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Devuelve los recibos del usuario autenticado ordenados por fecha de subida."""
    rows = (
        db.query(Recibo)
        .filter(Recibo.rfc == current_user.rfc)
        .order_by(Recibo.fecha_subida.desc())
        .all()
    )
    return [
        {"id": r.id, "periodo": r.periodo, "nombre_archivo": r.nombre_archivo}
        for r in rows
    ]

@router.get("/{recibo_id}/file")
def download_recibo(
    recibo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    row = (
        db.query(Recibo)
        .filter(Recibo.id == recibo_id)
        .first()
    )

    if not row:
        raise HTTPException(status_code=404, detail="Recibo no encontrado")

    if row.rfc.upper() != current_user.rfc.upper():
        raise HTTPException(status_code=403, detail="No tienes acceso a este recibo")

    ruta_str = row.ruta_archivo
    ruta = Path(ruta_str)

    if not ruta.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en el servidor")

    return FileResponse(
        path=ruta,
        filename=row.nombre_archivo,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"}
    )

@router.post("/upload_zip")
def upload_zip(
    archivo: UploadFile = File(...),
    current_admin: User = Depends(require_admin)
):
    """Carga masiva de recibos dentro de un archivo ZIP (solo administradores)."""
    blob = archivo.file.read()
    resumen = procesar_zip(blob)  # procesar_zip ya maneja la sesión SQLAlchemy internamente
    return {
        "msg": "ZIP procesado",
        "nuevo": resumen["nuevos"],
        "duplicados": resumen["ya_existían"],
        "sin_usuario": resumen["sin_usuario"],
    }
