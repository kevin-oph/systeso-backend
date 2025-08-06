# === routers/upload.py ===
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from auth import get_current_user
from utils.zip_processor import procesar_zip

router = APIRouter(prefix="/api/upload", tags=["upload"])

@router.post("/zip")
def upload_zip(file: UploadFile = File(...), current=Depends(get_current_user)):
    if current["rol"] != "admin":
        raise HTTPException(status_code=403, detail="No autorizado")
    blob = file.file.read()
    resumen = procesar_zip(blob)
    return resumen
