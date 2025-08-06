# backend/scripts/reenviar_verificacion.py

import sys
from utils.email_utils import enviar_correo_verificacion
from routers.users import create_access_token
import os

# Cambia esto por el email que ya está en tu base de datos
email_destino = "koph190508@gmail.com"  # ← MODIFICA ESTE VALOR
rol = "usuario"

# Ruta base del frontend
FRONTEND_URL = "http://localhost:8501"

# Generar token
token = create_access_token({"sub": email_destino, "rol": rol})

# Construir enlace
enlace_verificacion = f"{FRONTEND_URL}?token={token}"

# Enviar correo
enviar_correo_verificacion(email_destino, enlace_verificacion)
print("✅ Correo reenviado exitosamente.")
