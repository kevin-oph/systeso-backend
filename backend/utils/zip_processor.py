# === utils/zip_processor.py ===
from __future__ import annotations

import re
import io
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import pdfplumber
from sqlalchemy.orm import Session
from botocore.exceptions import ClientError

from config import settings, is_s3_enabled, get_s3_client, get_local_storage_root
from database import SessionLocal
from models import Usuario, Recibo


# -------------------- Regex robustas --------------------
# RFC mexicano (3-4 letras (incluye Ñ y &), 6 dígitos fecha, 2-3 homoclave)
RFC_RE = re.compile(r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{2,3})\b", re.IGNORECASE)

# "Periodo del: 01-ene.-2025 al 15-ene.-2025" con tolerancias de espacios/puntuación
PER_RE = re.compile(
    r"Periodo\s*del\s*:?\s*"
    r"(\d{1,2}[/-][A-Za-zÁÉÍÓÚáéíóú\.]+[/-]\d{4})\s*"
    r"al\s*"
    r"(\d{1,2}[/-][A-Za-zÁÉÍÓÚáéíóú\.]+[/-]\d{4})",
    re.IGNORECASE,
)

USE_S3 = is_s3_enabled()
LOCAL_ROOT: Optional[Path] = None if USE_S3 else get_local_storage_root()


# -------------------- Utilidades de extracción --------------------
def extraer_datos_pdf(pdf_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Abre un PDF y extrae (RFC, periodo_str) del contenido de texto.
    Devuelve (None, None) si no puede extraer ambos.
    periodo_str se devuelve como "dd-mmm.-yyyy_al_dd-mmm.-yyyy" (con '-')
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            txt = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as e:
        print(f"[zip_processor] ERROR abriendo '{pdf_path.name}': {e}")
        return None, None

    if not txt or not txt.strip():
        # PDF probablemente escaneado sin capa de texto
        print(f"[zip_processor] OMITIDO (sin texto): '{pdf_path.name}'")
        return None, None

    rfc_m = RFC_RE.search(txt)
    per_m = PER_RE.search(txt)

    if not rfc_m or not per_m:
        return None, None

    ini, fin = per_m.groups()
    periodo = f"{ini.replace('/', '-')}_al_{fin.replace('/', '-')}"
    return rfc_m.group(1).upper(), periodo


# -------------------- Almacenamiento --------------------
def _s3_key(rfc: str | int, clave_emp: str | int, nombre_archivo: str) -> str:
    # Estructura sugerida: rfc/clave/nombre.pdf
    return f"{str(rfc).upper()}/{clave_emp}/{nombre_archivo}"

def _s3_exists(bucket: str, key: str) -> bool:
    s3 = get_s3_client()
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        # Otros errores (permisos, red, etc.)
        raise

def _save_pdf_and_get_path(src_pdf: Path, rfc: str, clave_emp: str | int, nombre_archivo: str) -> str:
    """
    Guarda el PDF en S3 o filesystem y devuelve la 'ruta_archivo' a almacenar en BD:
    - S3:  's3://bucket/key'
    - FS:  ruta absoluta POSIX
    """
    if USE_S3:
        s3 = get_s3_client()
        key = _s3_key(rfc, clave_emp, nombre_archivo)
        s3.upload_file(
            Filename=str(src_pdf),
            Bucket=settings.s3_bucket,
            Key=key,
            ExtraArgs={"ContentType": "application/pdf"},
        )
        return f"s3://{settings.s3_bucket}/{key}"
    else:
        assert LOCAL_ROOT is not None, "get_local_storage_root() devolvió None"
        dest_dir = LOCAL_ROOT / str(clave_emp)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / nombre_archivo
        dest_path.write_bytes(src_pdf.read_bytes())
        return dest_path.as_posix()


# -------------------- Proceso principal --------------------
def procesar_zip(blob: bytes) -> Dict[str, int]:
    """
    Procesa un ZIP con recibos PDF:
      - Extrae RFC y Período del texto del PDF.
      - Busca usuario por RFC; obtiene `clave` del empleado.
      - Guarda/Actualiza PDF (S3 o FS) y registra/actualiza ruta en `recibos`.
      - Evita duplicados, pero 'repara' si el archivo falta en el storage.

    Devuelve un resumen con llaves compatibles con tu router:
      {"nuevos": X, "ya_existían": Y, "reparados": Z, "sin_usuario": W, "omitidos": U, "total_pdfs": T}
    """
    stats: Dict[str, int] = {
        "nuevos": 0,
        "ya_existían": 0,   # <- tu router toma esta key con acento
        "reparados": 0,
        "sin_usuario": 0,
        "omitidos": 0,
        "total_pdfs": 0,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        zpath = Path(tmpdir) / "lote.zip"
        zpath.write_bytes(blob)

        # Extraer ZIP completo
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmpdir)

        db: Session = SessionLocal()
        try:
            # Buscar PDFs sin importar mayúsculas/minúsculas
            for pdf_file in Path(tmpdir).rglob("*"):
                if not pdf_file.is_file():
                    continue
                if pdf_file.suffix.lower() != ".pdf":
                    continue

                stats["total_pdfs"] += 1

                # Extraer datos del contenido
                rfc, periodo = extraer_datos_pdf(pdf_file)
                if not (rfc and periodo):
                    stats["omitidos"] += 1
                    print(f"[zip_processor] OMITIDO (sin RFC/Periodo): '{pdf_file.name}'")
                    continue

                usuario: Optional[Usuario] = db.query(Usuario).filter(Usuario.rfc == rfc).first()
                if not usuario:
                    stats["sin_usuario"] += 1
                    print(f"[zip_processor] SIN_USUARIO: RFC={rfc} archivo={pdf_file.name}")
                    continue

                clave_emp = usuario.clave
                nombre_archivo = f"{rfc}_{periodo}.pdf"
                periodo_bd = periodo.replace("_al_", " al ")

                # ¿Existe registro?
                existe: Optional[Recibo] = (
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
                    # Autoreparación: si el archivo ya no está en storage, re-grabar y actualizar ruta
                    missing = False
                    if USE_S3:
                        key = _s3_key(rfc, clave_emp, nombre_archivo)
                        try:
                            missing = not _s3_exists(settings.s3_bucket, key)
                        except Exception as e:
                            # Si S3 falla por un error transitorio, no contarlo como duplicado silencioso
                            print(f"[zip_processor] HEAD S3 error ({nombre_archivo}): {e}")
                            missing = True
                    else:
                        missing = not Path(existe.ruta_archivo).exists()

                    if missing:
                        nueva_ruta = _save_pdf_and_get_path(pdf_file, rfc, clave_emp, nombre_archivo)
                        existe.ruta_archivo = nueva_ruta
                        db.commit()
                        stats["reparados"] += 1
                        print(f"[zip_processor] REPARADO: {nombre_archivo}")
                    else:
                        stats["ya_existían"] += 1
                    continue

                # Nuevo registro
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
                print(f"[zip_processor] NUEVO: {nombre_archivo}")
        finally:
            db.close()

    print(f"[zip_processor] RESUMEN: {stats}")
    return stats
