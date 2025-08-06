from dotenv import load_dotenv
from fastapi import FastAPI
from models import init_db, Usuario
from routers import users, recibos, excel_upload
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from fastapi.openapi.utils import get_openapi
from routers.users import hash_password
from database import engine, SessionLocal
from models import create_all_tables
from config import settings
import os

load_dotenv()
create_all_tables(engine)

app = FastAPI(title="Nomina API", version="1.0")

# ------------------ CORS Configuración ------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🧩 Crear usuario admin si no existe
def crear_usuario_admin_sqlite():
    """Solo para SQLite (desarrollo rápido/local)."""
    from backend.models import get_db_connection  # Solo está definido para sqlite
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM usuarios WHERE email = ?", ("admin@zapata.gob.mx",))
    existente = cur.fetchone()
    if existente:
        print("🟡 Usuario administrador ya existe, no se crea de nuevo.")
        conn.close()
        return
    cur.execute(
        """
        INSERT INTO usuarios (clave, rfc, nombre, email, password_hash, rol, is_verified)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            9999,
            "ADMRFC001",
            "Administrador General",
            "admin@zapata.gob.mx",
            hash_password("admin123"),
            "admin",
            1
        )
    )
    conn.commit()
    conn.close()
    print("✅ Usuario administrador creado exitosamente (SQLite).")

def crear_usuario_admin_postgres():
    """Para Postgres usando SQLAlchemy (producción)."""
    session = SessionLocal()
    admin_email = "admin@zapata.gob.mx"
    existe = session.query(Usuario).filter_by(email=admin_email).first()
    if existe:
        print("🟡 Usuario admin ya existe")
        session.close()
        return
    import bcrypt
    hash_admin = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode()
    admin = Usuario(
        clave="9999",
        nombre="Administrador General",
        rfc="ADMRFC001",
        curp=None,
        fecha_alta=None,
        departamento=None,
        puesto=None,
        tipo=None,
        email=admin_email,
        password_hash=hash_admin,
        activo=1,
        rol="admin",
        is_verified=1,
        reset_token=None
    )
    session.add(admin)
    session.commit()
    session.close()
    print("✅ Usuario administrador creado exitosamente (Postgres).")

# Inicializar la base de datos y usuario admin según motor
if settings.database_url.startswith("sqlite"):
    init_db()
    crear_usuario_admin_sqlite()
else:
    print("🔵 Usando Postgres: inicializa tus migraciones con Alembic o manualmente en producción.")
    # También puedes descomentar esto para crear admin automáticamente en pruebas:
    crear_usuario_admin_postgres()

# Routers
app.include_router(users.router, prefix="/users", tags=["Usuarios"])
app.include_router(recibos.router)
app.include_router(excel_upload.router)

# —— Security scheme para Swagger ——
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/users/login")

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description="API para el sistema de nómina",
        routes=app.routes,
    )

    # 1️⃣  Añadir BearerAuth
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }

    # 2️⃣  Eliminar posibles restos de OAuth2PasswordBearer generados automáticamente
    if "OAuth2PasswordBearer" in openapi_schema["components"]["securitySchemes"]:
        del openapi_schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]

    # 3️⃣  Limpiar referencias en todos los paths y reemplazar por BearerAuth
    for path in openapi_schema["paths"].values():
        for method in path.values():
            if "security" in method:
                method["security"] = [s for s in method["security"] if "OAuth2PasswordBearer" not in s]
            method.setdefault("security", []).append({"BearerAuth": []})

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi
