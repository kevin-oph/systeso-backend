# === routers/auth.py ===
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from schemas import Token, UserCreate, User
from routers.users import create_access_token, authenticate_user, get_current_user
from crud import create_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", status_code=201)
def register(data: UserCreate):
    # Validación mínima; asume registros precargados en usuarios
    create_user(data.dict())
    return {"msg": "Usuario creado"}

@router.get("/me", response_model=User)
def me(current=Depends(get_current_user)):
    return current
