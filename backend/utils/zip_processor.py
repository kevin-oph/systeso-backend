# === utils/zip_processor.py ===
import zipfile
import tempfile
import re
import time
import pdfplumber
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session
from database import SessionLocal
from models import Usuario, Recibo

from config import settings, is_s3_enabled, get_s3_client, get_local_storage_root
from botocore.exceptions import ClientError

# ----------------- Regex -----------------
RFC_RE = re.compile(r"\b([A-Z]{4}\d{6}[A-Z0-9]{3})\b")
# "01-ene.-2025" o "01-ene-2025" (permitimos punto opcional tras el mes)
FECHA_TOKEN = r"\d{2}-[a-zA-ZáéíóúÁÉÍÓÚ]{3,4}\.?\-\d{4}"
PER_RE = re.compile(
    rf"Periodo del:\s*({FECHA_TOKEN})\s*al\s*({FECHA_TOKEN})",
    re.IGNORECASE
)
# Para intentar por nombre de archivo: ...RFC_01-ene.-2025...al...15-ene.-2025...
NAME_PER_RE = re.compile(
    rf"{RFC_RE.pattern}.*?({FECHA_TOKEN}).*?al.*?({FECHA_TOKEN})",
    re.IGNORECASE
)

# ----------------- Config -----------------
USE_S3 = is_s3_enabled()
LOCAL_ROOT = None if USE_S3 else get_local_storage_root()

MAX_PAGES_TO_SCAN = 2         # solo 1–2 páginas del PDF
LOG_EVERY_N = 25              # log cada N archivos para volumen
EXTRACT_WARN_SEC = 3.0        # si extraer texto tarda más por PDF, dejamos log

# ----------------- Helpers -----------------
def _s3_key(rfc: str, clave_emp: str | int, nombre_archivo: str) -> str:
    return f"{rfc}/{clave_emp}/{nombre_archivo}"

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
    """
    Guarda el PDF en S3 o FS local y devuelve la 'ruta_archivo' para BD.
    - S3: 's3://bucket/key'
    - FS: ruta absoluta POSIX
    """
    if USE_S3:
        s3 = get_s3_client()
        key = _s3_key(rfc, clave_emp, nombre_archivo)
        s3.upload_file(str(src_pdf), settings.s3_bucket, key, ExtraArgs={"ContentType": "application/pdf"})
        return f"s3://{settings.s3_bucket}/{key}"
    else:
        dest_dir = LOCAL_ROOT / str(clave_emp)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / nombre_archivo
        dest_path.write_bytes(src_pdf.read_bytes())
        return dest_path.as_posix()

# ----------------- Extracción de datos -----------------
def _from_filename(filename: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Intenta extraer RFC y periodo desde el nombre del archivo (rápido).
    Devuelve (RFC, '01-ene.-2025_al_15-ene.-2025') o (None, None).
    """
    m = NAME_PER_RE.search(filename)
    if not m:
        return None, None
    rfc = m.group(1)
    ini = m.group(2).replace(".", "")
    fin = m.group(3).replace(".", "")
    periodo = f"{ini.replace('-', '-')}_al_{fin.replace('-', '-')}"
    return rfc, periodo

def _from_pdf(pdf_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Extrae (RFC, periodo) desde el contenido del PDF leyendo SOLO
    las primeras 1–2 páginas para ahorrar tiempo.
    """
    try:
        t0 = time.time()
        with pdfplumber.open(pdf_path) as pdf:
            pages = pdf.pages[:MAX_PAGES_TO_SCAN]
            txt_parts = []
            for p in pages:
                txt_parts.append(p.extract_text() or "")
            txt = "\n".join(txt_parts)

        rfc_m = RFC_RE.search(txt)
        per_m = PER_RE.search(txt)
        if rfc_m and per_m:
            ini, fin = per_m.groups()
            # normalizar puntos opcionales tras el mes
            ini = ini.replace(".", "")
            fin = fin.replace(".", "")
            periodo = f"{ini.replace('/', '-')}_al_{fin.replace('/', '-')}"
            return rfc_m.group(1), periodo

        # Log si fue “lento” y no encontró nada
        if time.time() - t0 > EXTRACT_WARN_SEC:
            print(f"[zip] WARN: extracción lenta en {pdf_path.name} ({time.time() - t0:.1f}s) sin coincidencias")
    except Exception as e:
        print(f"[zip] ERROR leyendo {pdf_path.name}: {e}")
    return None, None

def extraer_datos(pdf_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Estrategia de extracción:
      1) Intentar por nombre de archivo (rápido).
      2) Si falla, leer primeras páginas del PDF con pdfplumber (acotado).
    """
    rfc, per = _from_filename(pdf_path.name)
    if rfc and per:
        return rfc, per
    return _from_pdf(pdf_path)

# ----------------- Pipeline ZIP -----------------
def procesar_zip(blob: bytes) -> Dict[str, int]:
    """
    Procesa un ZIP con recibos PDF:
      - Extrae RFC y Periodo (rápido/eficiente).
      - Busca usuario por RFC (obtiene `clave`).
      - Sube/guarda PDF y registra/actualiza la ruta.
      - Evita duplicados; “repara” si el archivo falta en el storage.
    """
    stats = {"nuevos": 0, "ya_existían": 0, "sin_usuario": 0, "reparados": 0}

    with tempfile.TemporaryDirectory() as tmpdir:
        zpath = Path(tmpdir) / "lote.zip"
        zpath.write_bytes(blob)

        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmpdir)

        pdf_list = list(Path(tmpdir).rglob("*.pdf"))
        print(f"[zip] PDFs detectados: {len(pdf_list)}")

        db: Session = SessionLocal()
        try:
            for idx, pdf_file in enumerate(pdf_list, 1):
                if idx == 1 or idx % LOG_EVERY_N == 0:
                    print(f"[zip] Procesando {idx}/{len(pdf_list)}: {pdf_file.name}")

                rfc, periodo = extraer_datos(pdf_file)
                if not (rfc and periodo):
                    # No se pudo extraer: lo omitimos para no frenar todo el lote
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
                    # Autoreparación si falta físicamente
                    missing = False
                    if USE_S3:
                        key = _s3_key(rfc, clave_emp, nombre_archivo)
                        missing = not _s3_exists(settings.s3_bucket, key)
                    else:
                        try:
                            missing = not Path(existe.ruta_archivo).exists()
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
        finally:
            db.close()

    print(f"[zip] Resumen: {stats}")
    return stats
