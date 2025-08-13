# === utils/zip_processor.py ===
from __future__ import annotations

import re
import io
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, List

import pdfplumber
from sqlalchemy.orm import Session
from sqlalchemy import select
from botocore.exceptions import ClientError

from config import settings, is_s3_enabled, get_s3_client, get_local_storage_root
from database import SessionLocal
from models import Usuario, Recibo

# -------------------- Regex robustas --------------------
# RFC MX: 3-4 letras (incluye Ñ y &), 6 dígitos fecha, 2-3 homoclave
RFC_RE = re.compile(r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{2,3})\b", re.IGNORECASE)

# "Periodo del: 01-ene.-2025 al 15-ene.-2025" (tolerante a espacios y / o -)
PER_RE = re.compile(
    r"Periodo\s*del\s*:?\s*"
    r"(\d{1,2}[/-][A-Za-zÁÉÍÓÚáéíóú\.]+[/-]\d{4})\s*"
    r"al\s*"
    r"(\d{1,2}[/-][A-Za-zÁÉÍÓÚáéíóú\.]+[/-]\d{4})",
    re.IGNORECASE,
)

USE_S3 = is_s3_enabled()
LOCAL_ROOT: Optional[Path] = None if USE_S3 else get_local_storage_root()

# -------------------- Normalización --------------------
def normalize_rfc(s: str | None) -> Optional[str]:
    if not s:
        return None
    # quitar espacios/guiones y dejar solo alfanumérico
    cleaned = re.sub(r"[^A-Za-z0-9Ñ&]", "", s.upper())
    return cleaned or None

# -------------------- Extracción desde PDF --------------------
def extraer_rfcs_y_periodo(pdf_path: Path) -> Tuple[List[str], Optional[str]]:
    """
    Devuelve ([rfcs_encontrados], periodo or None).
    rfcs_encontrados son strings en bruto (sin normalizar).
    """
    rfcs: List[str] = []
    periodo: Optional[str] = None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            txt = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as e:
        print(f"[zip_processor] ERROR abriendo '{pdf_path.name}': {e}")
        return rfcs, None

    if txt and txt.strip():
        rfcs.extend(RFC_RE.findall(txt))
        per_m = PER_RE.search(txt)
        if per_m:
            ini, fin = per_m.groups()
            periodo = f"{ini.replace('/', '-')}_al_{fin.replace('/', '-')}"
    else:
        print(f"[zip_processor] SIN_TEXTO: '{pdf_path.name}' (posible escaneado)")

    # RFCs en nombre de archivo como respaldo
    name_rfcs = RFC_RE.findall(pdf_path.name)
    for r in name_rfcs:
        if r not in rfcs:
            rfcs.append(r)

    return rfcs, periodo

# -------------------- Almacenamiento --------------------
def _s3_key(rfc: str | int, clave_emp: str | int, nombre_archivo: str) -> str:
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
        raise

def _save_pdf_and_get_path(src_pdf: Path, rfc: str, clave_emp: str | int, nombre_archivo: str) -> str:
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
      - Encuentra *todos* los RFC posibles (texto y nombre de archivo)
      - Elige el RFC que SÍ exista en BD (comparando por RFC normalizado)
      - Extrae periodo y guarda/actualiza el recibo en S3/FS
      - Evita duplicados y repara faltantes

    Devuelve:
      {"nuevos": X, "ya_existían": Y, "reparados": Z, "sin_usuario": W, "omitidos": U, "total_pdfs": T}
    """
    stats: Dict[str, int] = {
        "nuevos": 0,
        "ya_existían": 0,
        "reparados": 0,
        "sin_usuario": 0,
        "omitidos": 0,
        "total_pdfs": 0,
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        zpath = Path(tmpdir) / "lote.zip"
        zpath.write_bytes(blob)

        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmpdir)

        db: Session = SessionLocal()
        try:
            # ---- Mapa de usuarios por RFC normalizado ----
            # Evita problemas de mayúsculas/espacios/guiones y colaciones SQL
            usuarios = db.execute(select(Usuario.clave, Usuario.rfc)).all()
            user_map: Dict[str, int] = {}
            for clave, rfc in usuarios:
                nrfc = normalize_rfc(rfc)
                if nrfc:
                    user_map[nrfc] = clave

            # ---- Recorrer PDFs ----
            for pdf_file in Path(tmpdir).rglob("*"):
                if not pdf_file.is_file() or pdf_file.suffix.lower() != ".pdf":
                    continue

                stats["total_pdfs"] += 1

                rfcs_raw, periodo = extraer_rfcs_y_periodo(pdf_file)

                # Normalizar y filtrar RFCs
                rfcs_norm = [normalize_rfc(r) for r in rfcs_raw]
                rfcs_norm = [r for r in rfcs_norm if r]  # quitar None

                if not rfcs_norm or not periodo:
                    stats["omitidos"] += 1
                    print(f"[zip_processor] OMITIDO (sin RFC/Periodo): '{pdf_file.name}' rfcs={rfcs_raw} periodo={periodo}")
                    continue

                # Elegir el RFC que exista en BD
                rfc_norm_match: Optional[str] = next((r for r in rfcs_norm if r in user_map), None)
                if not rfc_norm_match:
                    stats["sin_usuario"] += 1
                    print(f"[zip_processor] SIN_USUARIO: candidatos={rfcs_norm} archivo={pdf_file.name}")
                    continue

                clave_emp = user_map[rfc_norm_match]
                rfc_guardar = rfc_norm_match  # ya upper y sin separadores
                nombre_archivo = f"{rfc_guardar}_{periodo}.pdf"
                periodo_bd = periodo.replace("_al_", " al ")

                # ¿Ya existe?
                existe: Optional[Recibo] = (
                    db.query(Recibo)
                    .filter(
                        Recibo.clave_empleado == clave_emp,
                        Recibo.rfc == rfc_guardar,
                        Recibo.periodo == periodo_bd,
                        Recibo.nombre_archivo == nombre_archivo,
                    )
                    .first()
                )

                if existe:
                    # Reparar si falta el blob
                    missing = False
                    if USE_S3:
                        key = _s3_key(rfc_guardar, clave_emp, nombre_archivo)
                        try:
                            missing = not _s3_exists(settings.s3_bucket, key)
                        except Exception as e:
                            print(f"[zip_processor] HEAD S3 error ({nombre_archivo}): {e}")
                            missing = True
                    else:
                        missing = not Path(existe.ruta_archivo).exists()

                    if missing:
                        nueva_ruta = _save_pdf_and_get_path(pdf_file, rfc_guardar, clave_emp, nombre_archivo)
                        existe.ruta_archivo = nueva_ruta
                        db.commit()
                        stats["reparados"] += 1
                        print(f"[zip_processor] REPARADO: {nombre_archivo}")
                    else:
                        stats["ya_existían"] += 1
                    continue

                # Nuevo registro
                ruta_guardar = _save_pdf_and_get_path(pdf_file, rfc_guardar, clave_emp, nombre_archivo)
                rec = Recibo(
                    clave_empleado=clave_emp,
                    rfc=rfc_guardar,
                    periodo=periodo_bd,
                    nombre_archivo=nombre_archivo,
                    ruta_archivo=ruta_guardar,
                    fecha_subida=datetime.now().isoformat(),
                )
                db.add(rec)
                db.commit()
                stats["nuevos"] += 1
                print(f"[zip_processor] NUEVO: {nombre_archivo}")
        finally:
            db.close()

    print(f"[zip_processor] RESUMEN: {stats}")
    return stats
