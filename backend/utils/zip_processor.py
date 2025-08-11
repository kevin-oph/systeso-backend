# === utils/zip_processor.py ===
import re
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

import pdfplumber
from sqlalchemy.orm import Session
from botocore.exceptions import ClientError

from database import SessionLocal
from models import Usuario, Recibo
from config import settings, is_s3_enabled, get_s3_client, get_local_storage_root

# --- Patrones para extraer RFC y periodo del PDF ---
RFC_RE = re.compile(r"\b([A-Z]{4}\d{6}[A-Z0-9]{3})\b")
PER_RE = re.compile(
    r"Periodo del:\s*(\d{2}/[a-zA-ZáéíóúÁÉÍÓÚ.]+/\d{4})\s*al\s*"
    r"(\d{2}/[a-zA-ZáéíóúÁÉÍÓÚ.]+/\d{4})"
)

# ¿Usamos S3?
USE_S3 = is_s3_enabled()
# En filesystem queremos preparar la raíz una sola vez
LOCAL_ROOT = None if USE_S3 else get_local_storage_root()


# ----------------------------- helpers -----------------------------
def extraer_datos_pdf(pdf_path: Path) -> Tuple[str | None, str | None]:
    """
    Devuelve (RFC, periodo_formateado) o (None, None) si no se puede extraer.
    periodo_formateado: '01-ene.-2025_al_15-ene.-2025'
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            txt = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return None, None

    rfc_m = RFC_RE.search(txt or "")
    per_m = PER_RE.search(txt or "")
    if not (rfc_m and per_m):
        return None, None

    ini, fin = per_m.groups()
    periodo = f"{ini.replace('/', '-')}_al_{fin.replace('/', '-')}"
    return rfc_m.group(1), periodo


def _s3_key(rfc: str, clave_emp: str | int, nombre_archivo: str) -> str:
    # Estructura de objetos en el bucket
    return f"{rfc}/{clave_emp}/{nombre_archivo}"


def _s3_exists(bucket: str, key: str) -> bool:
    s3 = get_s3_client()
    if s3 is None:
        raise RuntimeError("S3 no configurado correctamente")
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        # Propagamos con contexto para que el /upload_zip lo muestre en el detail
        raise RuntimeError(f"S3 head_object error ({code}): {e}") from e


def _save_pdf_and_get_path(src_pdf: Path, rfc: str, clave_emp: str | int, nombre_archivo: str) -> str:
    """
    Guarda el PDF en el backend configurado y devuelve la 'ruta_archivo' a almacenar en BD.
      - S3: 's3://bucket/key'
      - FS: ruta absoluta POSIX
    """
    if USE_S3:
        s3 = get_s3_client()
        if s3 is None:
            raise RuntimeError("S3 no configurado correctamente")
        key = _s3_key(rfc, clave_emp, nombre_archivo)
        try:
            s3.upload_file(
                str(src_pdf),
                settings.s3_bucket,
                key,
                ExtraArgs={"ContentType": "application/pdf"},
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            raise RuntimeError(f"S3 upload_file error ({code}): {e}") from e
        return f"s3://{settings.s3_bucket}/{key}"
    else:
        dest_dir = LOCAL_ROOT / str(clave_emp)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / nombre_archivo
        dest_path.write_bytes(src_pdf.read_bytes())
        return dest_path.as_posix()


# ----------------------------- main -----------------------------
def procesar_zip(blob: bytes) -> Dict[str, int]:
    """
    Procesa un ZIP con recibos PDF:
      - Extrae RFC y Periodo.
      - Busca usuario por RFC para obtener `clave`.
      - Sube/guarda PDF (S3 o FS) y registra/actualiza la ruta en `recibos`.
      - Evita duplicados, pero 'repara' si el archivo falta en el storage.
    """
    stats = {"nuevos": 0, "ya_existían": 0, "sin_usuario": 0, "reparados": 0}

    with tempfile.TemporaryDirectory() as tmpdir:
        zpath = Path(tmpdir) / "lote.zip"
        zpath.write_bytes(blob)

        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmpdir)

        db: Session = SessionLocal()
        try:
            for pdf_file in Path(tmpdir).rglob("*.pdf"):
                rfc, periodo = extraer_datos_pdf(pdf_file)
                if not (rfc and periodo):
                    continue

                usuario = db.query(Usuario).filter(Usuario.rfc == rfc).first()
                if not usuario:
                    stats["sin_usuario"] += 1
                    continue

                clave_emp = usuario.clave
                nombre_archivo = f"{rfc}_{periodo}.pdf"
                periodo_bd = periodo.replace("_al_", " al ")

                # ¿Existe registro?
                existe: Recibo | None = (
                    db.query(Recibo)
                    .filter(
                        Recibo.clave_empleado == clave_emp,
                        Recibo.rfc == rfc,
                        Recibo.periodo == periodo_bd,
                        Recibo.nombre_archivo == nombre_archivo,
                    )
                    .first()
                )

                if existe:
                    # Autoreparación de storage
                    missing = False
                    if USE_S3:
                        key = _s3_key(rfc, clave_emp, nombre_archivo)
                        missing = not _s3_exists(settings.s3_bucket, key)
                    else:
                        try:
                            missing = not Path(existe.ruta_archivo or "").exists()
                        except Exception:
                            missing = True

                    if missing:
                        nueva_ruta = _save_pdf_and_get_path(pdf_file, rfc, clave_emp, nombre_archivo)
                        existe.ruta_archivo = nueva_ruta
                        db.commit()
                        stats["reparados"] += 1
                    else:
                        stats["ya_existían"] += 1
                    continue

                # Registro nuevo
                ruta_guardar = _save_pdf_and_get_path(pdf_file, rfc, clave_emp, nombre_archivo)
                recibo = Recibo(
                    clave_empleado=clave_emp,
                    rfc=rfc,
                    periodo=periodo_bd,
                    nombre_archivo=nombre_archivo,
                    ruta_archivo=ruta_guardar,
                    fecha_subida=datetime.now().isoformat(),
                )
                db.add(recibo)
                db.commit()
                stats["nuevos"] += 1
        finally:
            db.close()

    return stats
