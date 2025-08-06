# === crud.py ===
import bcrypt
from typing import Optional, List
from sqlalchemy.orm import Session
from models import Usuario, Recibo

# Users -----------------------------------------------------------------------

def get_user_by_email(db: Session, email: str) -> Optional[Usuario]:
    return db.query(Usuario).filter(Usuario.email == email).first()

def get_user_by_clave(db: Session, clave: str) -> Optional[Usuario]:
    return db.query(Usuario).filter(Usuario.clave == clave).first()

def create_user(db: Session, data: dict) -> Usuario:
    pw_hash = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
    user = Usuario(
        clave=data["clave"],
        rfc=data["rfc"],
        nombre=data.get("nombre", ""),
        email=data["email"],
        password_hash=pw_hash,
        rol=data.get("rol", "usuario"),
        is_verified=0
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

# Recibos ---------------------------------------------------------------------

def list_recibos(db: Session, clave_empleado: str) -> List[dict]:
    recibos = db.query(Recibo).filter(Recibo.clave_empleado == clave_empleado).order_by(Recibo.fecha_subida.desc()).all()
    # Devuelve una lista de diccionarios, solo con los campos requeridos
    return [
        {
            "id": r.id,
            "periodo": r.periodo,
            "nombre_archivo": r.nombre_archivo,
            "fecha_subida": r.fecha_subida,
        }
        for r in recibos
    ]
