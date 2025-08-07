from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from passlib.hash import bcrypt
from schemas import User, Token, UserRegister, UserLogin, TokenData
from crud import get_user_by_email, create_user
from database import get_db
from models import Usuario
from datetime import datetime, timedelta
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordBearer
import os
from typing import Annotated
from utils.email_utils import enviar_correo_verificacion, enviar_correo_recuperacion

router = APIRouter()

def hash_password(password: str) -> str:
    return bcrypt.hash(password)

SECRET_KEY = os.getenv("JWT_SECRET", "supersecreto")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

# ===> ESTA ES LA NUEVA VARIABLE (ponla en tu panel de Railway backend)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/users/login")

# ------------------------------------------------------------------
# Utilidades JWT
# ------------------------------------------------------------------

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# ------------------------------------------------------------------
# Dependencia: usuario autenticado
# ------------------------------------------------------------------

def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email, rol=payload.get("rol"))
    except JWTError:
        raise credentials_exception

    user = db.query(Usuario).filter(Usuario.email == token_data.email).first()
    if user is None:
        raise credentials_exception

    return User(
        clave=user.clave,
        email=user.email,
        rol=user.rol,
        rfc=user.rfc,
        nombre=user.nombre
    )

# ------------------------------------------------------------------
# Dependencia: solo admins
# ------------------------------------------------------------------

def require_admin(current_user: User = Depends(get_current_user)):
    if current_user.rol != "admin":
        raise HTTPException(status_code=403, detail="No autorizado")
    return current_user

# ------------------------------------------------------------------
# Registro
# ------------------------------------------------------------------

@router.post("/register", response_model=Token, status_code=201)
def register(user: UserRegister, db: Session = Depends(get_db)):
    clave_str = str(user.clave) 
    # Buscar por clave y RFC
    existente = db.query(Usuario).filter(
        Usuario.clave == str(user.clave),
        Usuario.rfc.ilike(user.rfc)
    ).first()

    if not existente:
        raise HTTPException(status_code=404, detail="No se encontró un usuario con esa clave y RFC")

    # Validar que ese usuario no tenga ya un email asignado
    if existente.email:
        raise HTTPException(status_code=400, detail="Este usuario ya está registrado con un correo")

    # Generar hash
    pw_hash = bcrypt.hash(user.password)

    try:
        existente.email = user.email
        existente.password_hash = pw_hash
        existente.is_verified = 0
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error actualizando el usuario: {str(e)}")

    # Obtener datos para token
    rol = existente.rol
    nombre = existente.nombre
    rfc = existente.rfc

    # Generar token
    token = create_access_token({"sub": user.email, "rol": rol})
    enlace_verificacion = f"{FRONTEND_URL}/?token={token}"
    enviar_correo_verificacion(user.email, enlace_verificacion)

    return {
        "access_token": token,
        "token_type": "bearer",
        "rol": rol,
        "nombre": nombre,
        "rfc": rfc
    }

# ------------------------------------------------------------------
# Login
# ------------------------------------------------------------------

@router.post("/login", response_model=Token)
def login(credentials: UserLogin, db: Session = Depends(get_db)):
    user = db.query(Usuario).filter(Usuario.email == credentials.email).first()

    if not user:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    if not user.is_verified:
        raise HTTPException(status_code=401, detail="Correo no verificado. Por favor revisa tu bandeja de entrada.")

    if not bcrypt.verify(credentials.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    token = create_access_token({"sub": credentials.email, "rol": user.rol})
    return {
        "access_token": token,
        "token_type": "bearer",
        "rol": user.rol,
        "nombre": user.nombre,
        "rfc": user.rfc
    }

# ------------------------------------------------------------------
# Perfil autenticado
# ------------------------------------------------------------------

@router.get("/me", response_model=User)
def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

# ------------------------------------------------------------------
# verificar_email
# ------------------------------------------------------------------
@router.get("/verificar_email")
def verificar_email(token: str, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")

        if email is None:
            raise HTTPException(status_code=400, detail="Token inválido")

        user = db.query(Usuario).filter(Usuario.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")

        user.is_verified = 1
        db.commit()
        return {"message": "Correo verificado exitosamente"}

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="El token ha expirado")
    except JWTError:
        raise HTTPException(status_code=400, detail="Token inválido")

# ------------------------------------------------------------------
# reenviar_verificacion
# ------------------------------------------------------------------
@router.post("/reenviar_verificacion")
def reenviar_verificacion(data: dict = Body(...), db: Session = Depends(get_db)):
    email = data.get("email")
    usuario = db.query(Usuario).filter(Usuario.email == email).first()

    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if usuario.is_verified:
        return {"mensaje": "El usuario ya ha verificado su correo."}

    token = create_access_token({"sub": email, "rol": usuario.rol})
    enlace = f"{FRONTEND_URL}/?token={token}"

    enviar_correo_verificacion(email, enlace)

    return {"mensaje": "Correo reenviado exitosamente"}

# ------------------------------------------------------------------
# prueba_enviar correo
# ------------------------------------------------------------------
@router.post("/test/enviar_correo")
def test_enviar_correo(email: str):
    enlace_ficticio = f"{FRONTEND_URL}/verificar?token=PRUEBA123"
    enviar_correo_verificacion(email, enlace_ficticio)
    return {"mensaje": "Correo enviado (ver consola para confirmación o errores)"}

# ------------------------------------------------------------------
# solicitaar_reset
# ------------------------------------------------------------------
@router.post("/solicitar_reset")
def solicitar_reset(data: dict = Body(...), db: Session = Depends(get_db)):
    email = data.get("email")
    user = db.query(Usuario).filter(Usuario.email == email).first()

    if not user:
        raise HTTPException(status_code=404, detail="Correo no registrado")

    token = create_access_token({"sub": email, "rol": user.rol})
    enlace = f"{FRONTEND_URL}/?reset_password=1&token={token}"

    user.reset_token = token
    db.commit()

    enviar_correo_recuperacion(email, enlace)

    return {"mensaje": "Se ha enviado un enlace para restablecer tu contraseña"}

# ------------------------------------------------------------------
# reset_password
# ------------------------------------------------------------------
@router.post("/reset_password")
def reset_password(data: dict = Body(...), db: Session = Depends(get_db)):
    token = data.get("token")
    nueva_password = data.get("nueva_password")

    if not token or not nueva_password:
        raise HTTPException(status_code=400, detail="Token y nueva contraseña requeridos.")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")

        if not email:
            raise HTTPException(status_code=400, detail="Token inválido")

        user = db.query(Usuario).filter(Usuario.email == email).first()
        if not user or user.reset_token != token:
            raise HTTPException(status_code=400, detail="Token inválido o expirado")

        user.password_hash = bcrypt.hash(nueva_password)
        user.reset_token = None
        db.commit()

        return {"mensaje": "Contraseña restablecida exitosamente"}

    except JWTError:
        raise HTTPException(status_code=400, detail="Token inválido o expirado")

