# ===============================================================
# schemas.py
# ===============================================================
from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional, List
import re

PASSWORD_REGEX = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$")

class UserCreate(BaseModel):
    clave: int
    rfc: str
    email: EmailStr
    password: str = Field(..., min_length=8)

    @validator("password")
    def strong_password(cls, v):
        if not PASSWORD_REGEX.match(v):
            raise ValueError(
                "La contraseña debe tener al menos 8 caracteres, 1 mayúscula, 1 minúscula y 1 número"
            )
        return v
    
class UserRegister(BaseModel):
    clave: int
    rfc: str
    email: EmailStr
    password: str = Field(..., min_length=8)

    @validator("password")
    def strong_password(cls, v):
        if not PASSWORD_REGEX.match(v):
            raise ValueError(
                "La contraseña debe tener al menos 8 caracteres, 1 mayúscula, 1 minúscula y 1 número"
            )
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str
    rol: str
    nombre: Optional[str]
    rfc: Optional[str]


class User(BaseModel):
    clave: int
    email: str
    rol: str
    rfc: str
    nombre: str
    is_verified: Optional[bool] = False  


    model_config = {
        "from_attributes": True
    }


class TokenData(BaseModel):
    email: str
    rol: str


class ReciboOut(BaseModel):
    id: int
    periodo: str
    nombre_archivo: str

    model_config = {
        "from_attributes": True
    }


class Recibo(BaseModel):
    id: int
    clave_empleado: int
    periodo: str
    nombre_archivo: str
    ruta_archivo: str
    fecha_subida: str

    model_config = {
        "from_attributes": True
    }
