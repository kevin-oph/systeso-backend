from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from config import settings

# --- Configuración de argumentos dinámicos para blindar el pool ---
connect_args = {}
pool_kwargs = {}

if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
else:
    # Optimización estricta para PostgreSQL en producción (Railway)
    pool_kwargs = {
        "pool_size": 15,          # Conexiones máximas simultáneas por hilo
        "max_overflow": 25,       # Conexiones extra permitidas en picos de alta demanda de nómina
        "pool_recycle": 1800,     # Mata y recicla conexiones inactivas cada 30 minutos (evita fugas)
        "pool_pre_ping": True     # Valida si la conexión sigue viva antes de usarla (evita errores zombies)
    }

# Crea el engine optimizado
engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=False,                   # Desactivado en producción para evitar lag en logs por miles de líneas SQL
    **pool_kwargs
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Dependencia estándar para endpoints FastAPI (mantenida por compatibilidad)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()