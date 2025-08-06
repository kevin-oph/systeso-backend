from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from config import settings

# Crea el engine soportando tanto SQLite como PostgreSQL, según DATABASE_URL
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    echo=True  # Solo para debugging, quita en producción
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Dependencia para endpoints FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
