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
    stats = {"nuevos": 0, "ya_existían": 0, "reparados": 0, "sin_usuario": 0, "omitidos": 0, "total_pdfs": 0}

    # Importación ligera como alternativa a pdfplumber para optimizar RAM
    import pypdf

    # Cargamos el ZIP directamente desde el flujo de bytes en memoria
    zip_buffer = io.BytesIO(blob)
    
    db: Session = SessionLocal()
    try:
        # ---- Mapa de usuarios por RFC normalizado ----
        usuarios = db.execute(select(Usuario.clave, Usuario.rfc)).all()
        user_map: Dict[str, int] = {normalize_rfc(rfc): clave for clave, rfc in usuarios if normalize_rfc(rfc)}

        with zipfile.ZipFile(zip_buffer) as z:
            # Iteramos sobre la lista de archivos del ZIP sin extraerlos a disco
            for member in z.infolist():
                if member.is_dir() or not member.filename.lower().endswith('.pdf'):
                    continue
                
                stats["total_pdfs"] += 1
                
                # Leemos el archivo actual del ZIP en un entorno aislado de memoria
                with z.open(member) as pdf_file:
                    pdf_bytes = pdf_file.read()
                    
                    # --- Extracción ligera de texto usando pypdf ---
                    rfcs: List[str] = []
                    periodo: Optional[str] = None
                    try:
                        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
                        txt = "\n".join((page.extract_text() or "") for page in reader.pages)
                        
                        if txt and txt.strip():
                            rfcs.extend(RFC_RE.findall(txt))
                            per_m = PER_RE.search(txt)
                            if per_m:
                                ini, fin = per_m.groups()
                                periodo = f"{ini.replace('/', '-')}_al_{fin.replace('/', '-')}"
                    except Exception as e:
                        print(f"[zip_processor] Error leyendo PDF interno {member.filename}: {e}")
                        continue

                    # Respaldo con el nombre del archivo dentro del ZIP
                    filename_only = Path(member.filename).name
                    name_rfcs = RFC_RE.findall(filename_only)
                    for r in name_rfcs:
                        if r not in rfcs:
                            rfcs.append(r)

                    # --- Validaciones de Negocio ---
                    rfcs_norm = [r for r in [normalize_rfc(x) for x in rfcs] if r]
                    if not rfcs_norm or not periodo:
                        stats["omitidos"] += 1
                        continue

                    rfc_norm_match = next((r for r in rfcs_norm if r in user_map), None)
                    if not rfc_norm_match:
                        stats["sin_usuario"] += 1
                        continue

                    clave_emp = user_map[rfc_norm_match]
                    nombre_archivo = f"{rfc_norm_match}_{periodo}.pdf"
                    periodo_bd = periodo.replace("_al_", " al ")

                    # --- Verificación de duplicados ---
                    existe = db.query(Recibo).filter(
                        Recibo.clave_empleado == clave_emp,
                        Recibo.rfc == rfc_norm_match,
                        Recibo.periodo == periodo_bd,
                        Recibo.nombre_archivo == nombre_archivo
                    ).first()

                    # Para evitar escribir archivos temporales a disco, creamos un archivo temporal rápido
                    # solo si se requiere subir a S3 o almacenar localmente
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
                        tmp_pdf.write(pdf_bytes)
                        tmp_pdf_path = Path(tmp_pdf.name)

                    try:
                        if existe:
                            stats["ya_existían"] += 1
                            # Aquí puedes mantener tu lógica de "reparados" si lo requieres
                            continue

                        # --- Guardar registro nuevo ---
                        ruta_guardar = _save_pdf_and_get_path(tmp_pdf_path, rfc_norm_match, clave_emp, nombre_archivo)
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
                        
                        # Hacemos commits en lotes para no saturar la BD (ej. cada 50 registros)
                        if stats["nuevos"] % 50 == 0:
                            db.commit()
                            
                    finally:
                        # Nos aseguramos de borrar el archivo temporal del PDF individual inmediatamente
                        if tmp_pdf_path.exists():
                            tmp_pdf_path.unlink()

            # Commit final para los registros restantes
            db.commit()

    finally:
        db.close()
        zip_buffer.close()

    print(f"[zip_processor] RESUMEN OPTIMIZADO: {stats}")
    return stats
