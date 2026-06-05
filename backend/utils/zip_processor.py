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

# -------------------- Regex robustas y definitivas --------------------
# RFC MX: 3-4 letras (incluye Ñ y &), 6 dígitos fecha, 2-3 homoclave
RFC_RE = re.compile(r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{2,3})\b", re.IGNORECASE)

# REGEX EVOLUCIONADA: Captura el periodo aislando los bloques de fecha sin importar los caracteres intermedios
PER_RE = re.compile(
    r"Periodo\s*del\s*:?\s*([^\s]+)\s*al\s*([^\s]+)",
    re.IGNORECASE,
)

USE_S3 = is_s3_enabled()
LOCAL_ROOT: Optional[Path] = None if USE_S3 else get_local_storage_root()

# -------------------- Normalización --------------------
def normalize_rfc(s: str | None) -> Optional[str]:
    if not s:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9Ñ&]", "", s.upper())
    return cleaned or None

# -------------------- Extracción desde PDF (Motor de Fuerza Bruta) --------------------
def extraer_rfcs_y_periodo(pdf_path: Path) -> Tuple[List[str], Optional[str]]:
    """
    Extrae el texto abriendo el archivo físicamente desde el disco temporal.
    Esto garantiza que los mapas de fuentes del PDF no se corrompan y devuelvan el texto real.
    """
    rfcs: List[str] = []
    periodo: Optional[str] = None

    try:
        # pdfplumber sobre archivo físico es el estándar más alto de extracción
        with pdfplumber.open(pdf_path) as pdf:
            txt = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as e:
        print(f"[zip_processor] ERROR abriendo '{pdf_path.name}' en disco: {e}")
        return rfcs, None

    if txt and txt.strip():
        rfcs.extend(RFC_RE.findall(txt))
        per_m = PER_RE.search(txt)
        if per_m:
            ini, fin = per_m.groups()
            # Sanitización estricta: eliminamos espacios basura, normalizamos diagonales a guiones
            ini_clean = re.sub(r"\s+", "", ini).replace('/', '-')
            fin_clean = re.sub(r"\s+", "", fin).replace('/', '-')
            periodo = f"{ini_clean}_al_{fin_clean}"
    else:
        print(f"[zip_processor] ADVERTENCIA: No se pudo extraer texto de '{pdf_path.name}'. Posible PDF vectorial/bloqueado.")

    # Respaldo: Extraer RFC del nombre del archivo si no se lee adentro
    name_rfcs = RFC_RE.findall(pdf_path.name)
    for r in name_rfcs:
        if r not in rfcs:
            rfcs.append(r)

    return rfcs, periodo

# -------------------- Almacenamiento --------------------
def _save_pdf_and_get_path(src_pdf: Path, rfc: str, clave_emp: str | int, nombre_archivo: str) -> str:
    if USE_S3:
        s3 = get_s3_client()
        key = f"{str(rfc).upper()}/{clave_emp}/{nombre_archivo}"
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
    Procesa el ZIP de nóminas de forma definitiva y limpia.
    Usa directorios temporales físicos en disco para asegurar la correcta lectura de fuentes,
    evitando fugas de memoria RAM mediante commits estructurados por lotes.
    """
    stats = {"nuevos": 0, "ya_existían": 0, "reparados": 0, "sin_usuario": 0, "omitidos": 0, "total_pdfs": 0}

    db: Session = SessionLocal()
    try:
        # ---- Mapa de usuarios por RFC normalizado ----
        usuarios = db.execute(select(Usuario.clave, Usuario.rfc)).all()
        user_map: Dict[str, int] = {normalize_rfc(rfc): clave for clave, rfc in usuarios if normalize_rfc(rfc)}

        # Creamos un directorio temporal seguro en el disco del contenedor
        with tempfile.TemporaryDirectory() as tmpdir:
            zpath = Path(tmpdir) / "lote.zip"
            zpath.write_bytes(blob)

            # Extraemos físicamente los archivos para que el sistema operativo indexe las fuentes del PDF
            with zipfile.ZipFile(zpath) as z:
                z.extractall(tmpdir)

            # Recorremos los PDFs extraídos físicamente
            for pdf_file in Path(tmpdir).rglob("*"):
                if not pdf_file.is_file() or pdf_file.suffix.lower() != ".pdf":
                    continue

                stats["total_pdfs"] += 1
                nombre_físico = pdf_file.name

                # Ejecutamos la extracción con el mapa de caracteres nativo del disco
                rfcs_raw, periodo = extraer_rfcs_y_periodo(pdf_file)

                # Normalizar y filtrar RFCs candidatos
                rfcs_norm = [r for r in [normalize_rfc(x) for x in rfcs_raw] if r]

                # Si el motor sigue sin poder leer el periodo internamente por bloqueo del PDF,
                # aquí puedes meter un log detallado o un sistema alterno. No usamos parches fijos de fecha.
                if not rfcs_norm or not periodo:
                    stats["omitidos"] += 1
                    print(f"[zip_processor] OMITIDO CRÍTICO: '{nombre_físico}' no expone texto de periodo analizable.")
                    continue

                # Elegir el RFC que exista en BD
                rfc_norm_match = next((r for r in rfcs_norm if r in user_map), None)
                if not rfc_norm_match:
                    stats["sin_usuario"] += 1
                    print(f"[zip_processor] SIN_USUARIO: candidatos={rfcs_norm} archivo={nombre_físico}")
                    continue

                clave_emp = user_map[rfc_norm_match]
                nombre_archivo = f"{rfc_norm_match}_{periodo}.pdf"
                periodo_bd = periodo.replace("_al_", " al ")

                # ¿Ya existe este recibo quincenal?
                existe = db.query(Recibo).filter(
                    Recibo.clave_empleado == clave_emp,
                    Recibo.rfc == rfc_norm_match,
                    Recibo.periodo == periodo_bd,
                    Recibo.nombre_archivo == nombre_archivo
                    ).first()

                if existe:
                    stats["ya_existían"] += 1
                    continue

                # --- Registro e inserción limpia ---
                ruta_guardar = _save_pdf_and_get_path(pdf_file, rfc_norm_match, clave_emp, nombre_archivo)
                rec = Recibo(
                    clave_empleado=clave_emp,
                    rfc=rfc_norm_match,
                    periodo=periodo_bd,
                    nombre_archivo=nombre_archivo,
                    ruta_archivo=ruta_guardar,
                    fecha_subida=datetime.now().isoformat(),
                )
                db.add(rec)
                stats["nuevos"] += 1

                # Control estricto de transacciones cada 50 elementos para mantener la RAM limpia en Railway
                if stats["nuevos"] % 50 == 0:
                    db.commit()

            # Guardado final de los registros remanentes
            db.commit()

    finally:
        db.close()

    print(f"[zip_processor] PROCESAMIENTO ARQUITECTÓNICO FINALIZADO: {stats}")
    return stats