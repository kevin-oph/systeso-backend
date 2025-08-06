from fastapi import APIRouter, File, UploadFile, Depends, HTTPException
from backend.routers.users import require_admin
from backend.database import get_db
from backend.models import Usuario, CargaExcel
import pandas as pd
from datetime import datetime
import io
from sqlalchemy.orm import Session

router = APIRouter(prefix="/empleados", tags=["Cargar Excel"])

EXCEL_TO_DB = {
    "Clave": "clave",
    "Nombre del trabajador": "nombre",
    "RFC": "rfc",
    "CURP": "curp",
    "Fecha de Alta": "fecha_alta",
    "Departamento": "departamento",
    "Puesto": "puesto",
    "Tipo Salario": "tipo",
}

EXTRAS = {
    "email": None,
    "password_hash": None,
    "activo": 1,
    "is_verified": 0,
    "rol": "usuario",
}

@router.post("/cargar_excel")
def cargar_excel(
    archivo: UploadFile = File(...),
    current_user: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    if not archivo.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="El archivo debe ser Excel (.xlsx o .xls)")

    try:
        content = archivo.file.read()
        df = pd.read_excel(io.BytesIO(content), skiprows=10)

        faltantes = [col for col in EXCEL_TO_DB if col not in df.columns]
        if faltantes:
            raise HTTPException(status_code=400, detail=f"Faltan columnas requeridas: {', '.join(faltantes)}")

        df = df[list(EXCEL_TO_DB)].rename(columns=EXCEL_TO_DB)
        if "fecha_alta" in df.columns:
            df["fecha_alta"] = pd.to_datetime(df["fecha_alta"]).dt.date.astype(str)

        for col, default in EXTRAS.items():
            df[col] = default

        df = df.where(pd.notna(df), None)

        # --- SQLAlchemy: Detectar empleados existentes y nuevos
        existentes_rfcs = {u.rfc for u in db.query(Usuario.rfc).filter(Usuario.rfc.isnot(None)).all()}
        nuevos = df[~df["rfc"].isin(existentes_rfcs)]
        nuevos_rfcs = set(df["rfc"].dropna())
        bajas = (
            db.query(Usuario.rfc, Usuario.nombre)
            .filter(Usuario.rfc.isnot(None), ~Usuario.rfc.in_(nuevos_rfcs))
            .all()
        )

        # Insertar nuevos empleados
        insertados = 0
        for _, row in nuevos.iterrows():
            user = Usuario(**row.to_dict())
            db.add(user)
            insertados += 1
        db.commit()

        # --- Registrar el archivo Excel cargado
        existe = (
            db.query(CargaExcel)
            .filter(
                CargaExcel.nombre_archivo == archivo.filename,
                CargaExcel.usuario == current_user.email
            )
            .first()
        )
        if not existe:
            carga = CargaExcel(
                nombre_archivo=archivo.filename,
                usuario=current_user.email,
                fecha_carga=datetime.now()
            )
            db.add(carga)
            db.commit()

        return {
            "insertados": insertados,
            "omitidos": len(df) - insertados,
            "bajas": [{"rfc": b.rfc, "nombre": b.nombre} for b in bajas],
            "archivo_cargado": archivo.filename
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al procesar archivo: {str(e)}")

@router.get("/historial_cargas")
def historial_cargas(_: dict = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (
        db.query(CargaExcel)
        .order_by(CargaExcel.fecha_carga.desc())
        .all()
    )
    return [
        {
            "nombre_archivo": r.nombre_archivo,
            "fecha_carga": r.fecha_carga,
            "usuario": r.usuario
        }
        for r in rows
    ]
