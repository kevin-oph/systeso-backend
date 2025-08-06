# === utils/zip_processor.py ===
import zipfile, tempfile, re, pdfplumber
import os
from datetime import datetime
from pathlib import Path
from backend.config import settings
from backend.database import get_db, SessionLocal
from backend.models import Usuario, Recibo

RFC_RE = re.compile(r"\b([A-Z]{4}\d{6}[A-Z0-9]{3})\b")
PER_RE = re.compile(
    r"Periodo del:\s*(\d{2}/[a-zA-ZáéíóúÁÉÍÓÚ.]+/\d{4})\s*al\s*"
    r"(\d{2}/[a-zA-ZáéíóúÁÉÍÓÚ.]+/\d{4})"
)

# Carpeta donde guardaremos los PDFs
if settings.database_url.startswith("sqlite:///"):
    db_path = settings.database_url.split("///")[-1]
    STORAGE = Path(db_path).parent / "pdfs"
else:
    STORAGE = Path(os.environ.get("PDF_STORAGE_PATH", "pdfs")).absolute()
STORAGE.mkdir(exist_ok=True, parents=True)

def extraer_datos_pdf(pdf_path: Path):
    """Devuelve (RFC, periodo) extraídos del PDF o (None, None) si falla."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            txt = "\n".join((p.extract_text() or "") for p in pdf.pages)
        rfc_m = RFC_RE.search(txt)
        per_m = PER_RE.search(txt)
        if rfc_m and per_m:
            ini, fin = per_m.groups()
            periodo = f"{ini.replace('/', '-')}_al_{fin.replace('/', '-')}"
            return rfc_m.group(1), periodo
    except Exception:
        pass
    return None, None

def procesar_zip(blob: bytes) -> dict:
    """
    Procesa un ZIP con recibos PDF usando SQLAlchemy:

    * Guarda cada PDF en disco en STORAGE/<clave_empleado>/.
    * Inserta un registro en la tabla `recibos`
      (clave_empleado, rfc, periodo, nombre_archivo, ruta_archivo, fecha_subida).
    * Devuelve estadísticas de nuevos / duplicados / sin usuario.
    """
    stats = {"nuevos": 0, "ya_existían": 0, "sin_usuario": 0}
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "lote.zip"
        zip_path.write_bytes(blob)

        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmpdir)

        # Usar SQLAlchemy para toda la lógica de base de datos
        db = SessionLocal()
        try:
            for pdf_file in Path(tmpdir).rglob("*.pdf"):
                rfc, periodo = extraer_datos_pdf(pdf_file)
                if not (rfc and periodo):
                    continue

                # Buscar usuario por RFC
                usuario = db.query(Usuario).filter(Usuario.rfc == rfc).first()
                if not usuario:
                    stats["sin_usuario"] += 1
                    continue

                clave_emp = usuario.clave
                nombre_archivo = f"{rfc}_{periodo}.pdf"
                periodo_bd = periodo.replace("_al_", " al ")

                # Verificar duplicado
                existe = db.query(Recibo).filter(
                    Recibo.clave_empleado == clave_emp,
                    Recibo.rfc == rfc,
                    Recibo.periodo == periodo_bd,
                    Recibo.nombre_archivo == nombre_archivo,
                ).first()
                if existe:
                    stats["ya_existían"] += 1
                    continue

                # Guardar PDF
                dest_folder = STORAGE / str(clave_emp)
                dest_folder.mkdir(exist_ok=True, parents=True)
                dest_path = dest_folder / nombre_archivo
                dest_path.write_bytes(pdf_file.read_bytes())

                # Insertar en BD
                recibo = Recibo(
                    clave_empleado=clave_emp,
                    rfc=rfc,
                    periodo=periodo_bd,
                    nombre_archivo=nombre_archivo,
                    ruta_archivo=dest_path.as_posix(),
                    fecha_subida=datetime.now().isoformat(),
                )
                db.add(recibo)
                db.commit()
                stats["nuevos"] += 1
        finally:
            db.close()
    return stats
