# backend/models.py

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from backend.database import Base
from datetime import datetime


def init_db():
    from backend.database import engine
    Base.metadata.create_all(bind=engine)

class Usuario(Base):
    __tablename__ = "usuarios"
    rfc = Column(String, primary_key=True, index=True)
    clave = Column(String, index=True)
    nombre = Column(String)
    curp = Column(String)
    fecha_alta = Column(String)  # Si quieres Date, puedes cambiar a Date
    departamento = Column(String)
    puesto = Column(String)
    tipo = Column(String)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    activo = Column(Integer, default=1)
    rol = Column(String, default="usuario")
    is_verified = Column(Integer, default=0)
    reset_token = Column(String, nullable=True)

class Recibo(Base):
    __tablename__ = "recibos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    clave_empleado = Column(String)
    rfc = Column(String, ForeignKey("usuarios.rfc"))
    periodo = Column(String)
    nombre_archivo = Column(String)
    ruta_archivo = Column(String)
    fecha_subida = Column(String)

class CargaExcel(Base):
    __tablename__ = "carga_excel"
    id = Column(Integer, primary_key=True, autoincrement=True)
    nombre_archivo = Column(String)
    fecha_carga = Column(DateTime, default=datetime.utcnow)
    usuario = Column(String)


# Al final de backend/models.py

def create_all_tables(engine):
    from backend.database import Base  # Asegúrate de tener Base importado correctamente
    Base.metadata.create_all(bind=engine)
