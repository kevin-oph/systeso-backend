# backend/routers/recibos.py
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from pathlib import Path
from typing import List
from urllib.parse import urlparse

from database import get_db
from models import Recibo
from schemas import ReciboOut
from routers.users import get_current_user, require_admin, User
from utils.zip_processor import procesar_zip
from config import settings, is_s3_enabled, get_s3_client, get_local_storage_root

router = APIRouter(prefix="/recibos", tags=["Recibos"])

@router.get("/", response_model=List[ReciboOut])
def list_recibos(current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
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
def download_recibo(recibo_id: int,
                    current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):

    row = db.query(Recibo).filter(Recibo.id == recibo_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Recibo no encontrado")
    if row.rfc.upper() != current_user.rfc.upper():
        raise HTTPException(status_code=403, detail="No tienes acceso a este recibo")

    ruta_str = row.ruta_archivo or ""
    # Caso S3: ruta "s3://bucket/key"
    if ruta_str.startswith("s3://"):
        if not is_s3_enabled():
            raise HTTPException(500, "S3 no configurado")
        s3 = get_s3_client()
        parsed = urlparse(ruta_str)  # s3://bucket/key
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        try:
            url = s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": bucket,
                    "Key": key,
                    "ResponseContentType": "application/pdf",
                    "ResponseContentDisposition": f'inline; filename="{row.nombre_archivo}"',
                },
                ExpiresIn=60,  # 1 minuto
            )
        except Exception:
            raise HTTPException(500, "No se pudo generar URL firmada")
        # 307 para mantener método GET; requests sigue el redirect por defecto
        return RedirectResponse(url, status_code=307)

    # Caso filesystem local (producción on-prem)
    ruta = Path(ruta_str)
    if not ruta.is_absolute():
        ruta = get_local_storage_root() / ruta
    if not ruta.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en el servidor")

    return FileResponse(
        path=str(ruta),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{row.nombre_archivo}"'}
    )

@router.post("/upload_zip")
def upload_zip(archivo: UploadFile = File(...),
               current_admin: User = Depends(require_admin)):
    """Carga masiva de recibos dentro de un archivo ZIP (solo administradores)."""
    blob = archivo.file.read()
    resumen = procesar_zip(blob)  # maneja transacciones internamente
    return {
        "msg": "ZIP procesado",
        "nuevo": resumen["nuevos"],
        "duplicados": resumen["ya_existían"],
        "reparados": resumen["reparados"],
        "sin_usuario": resumen["sin_usuario"],
    }
