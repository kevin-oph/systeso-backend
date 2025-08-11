# backend/routers/recibos.py
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from typing import List
from pathlib import Path
from urllib.parse import urlparse  # <-- necesario
import io, zipfile, traceback, sys

from sqlalchemy.orm import Session
from database import get_db
from models import Recibo
from schemas import ReciboOut
from routers.users import get_current_user, require_admin, User

from config import is_s3_enabled, get_s3_client, get_local_storage_root, settings  # <-- añadí get_local_storage_root
from botocore.exceptions import ClientError

router = APIRouter(prefix="/recibos", tags=["Recibos"])

# ----------------------------- LISTA -----------------------------
@router.get("/", response_model=List[ReciboOut])
def list_recibos(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
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

# ----------------------------- DESCARGA / VIEW -----------------------------
@router.get("/{recibo_id}/file")
def download_recibo(
    recibo_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(Recibo).filter(Recibo.id == recibo_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Recibo no encontrado")

    if row.rfc.upper() != current_user.rfc.upper():
        raise HTTPException(status_code=403, detail="No tienes acceso a este recibo")

    ruta_str = (row.ruta_archivo or "").strip()

    # Caso S3: ruta "s3://bucket/key"
    if ruta_str.startswith("s3://"):
        if not is_s3_enabled():
            raise HTTPException(status_code=500, detail="S3 no configurado")

        s3 = get_s3_client()
        parsed = urlparse(ruta_str)  # s3://bucket/key
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")

        # (Opcional pero útil) verificar existencia para devolver 404 limpio
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                raise HTTPException(status_code=404, detail="Archivo no encontrado en el servidor")
            raise HTTPException(status_code=500, detail=f"Error S3: {code}")

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
            raise HTTPException(status_code=500, detail="No se pudo generar URL firmada")

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
        headers={"Content-Disposition": f'inline; filename="{row.nombre_archivo}"'},
    )

# ----------------------------- UPLOAD ZIP -----------------------------
@router.post("/upload_zip")
def upload_zip(
    archivo: UploadFile = File(...),
    current_admin: User = Depends(require_admin),
):
    """
    Carga masiva de recibos dentro de un archivo ZIP (solo administradores).
    Agregamos logs y validaciones para diagnosticar caídas del proceso.
    """
    try:
        # 1) Leer bytes del archivo
        blob = archivo.file.read()
        size_mb = round(len(blob) / (1024 * 1024), 2) if blob else 0
        print(f"[upload_zip] recibido: {archivo.filename} ({size_mb} MB)")

        if not blob:
            raise HTTPException(status_code=400, detail="Archivo vacío")

        # 2) Validar que sea un ZIP válido
        if not zipfile.is_zipfile(io.BytesIO(blob)):
            raise HTTPException(status_code=400, detail="El archivo no es un ZIP válido")

        # 3) Procesar ZIP (import local para evitar fallas al arrancar si zip_processor tiene un error)
        from utils.zip_processor import procesar_zip

        resumen = procesar_zip(blob)

        # 4) Respuesta clara
        payload = {
            "msg": "ZIP procesado",
            "nuevo": resumen.get("nuevos", 0),
            "duplicados": resumen.get("ya_existían", 0),
            "reparados": resumen.get("reparados", 0),
            "sin_usuario": resumen.get("sin_usuario", 0),
            "tamaño_mb": size_mb,
        }
        print(f"[upload_zip] OK -> {payload}")
        return JSONResponse(payload, status_code=200)

    except HTTPException:
        raise
    except Exception as e:
        tb = "".join(traceback.format_exception(*sys.exc_info()))
        print("[upload_zip] ERROR:", e)
        print(tb)
        # devolvemos detalle para que lo veas en el front
        raise HTTPException(
            status_code=500,
            detail=f"Fallo al procesar ZIP: {type(e).__name__}: {e}",
        )
