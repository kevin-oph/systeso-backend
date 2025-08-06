# backend/utils/email_utils.py

import smtplib
from email.message import EmailMessage
import os

EMAIL_ORIGEN = os.getenv("EMAIL_FROM", "jefatura.nominas@zapatamorelos.gob.mx")
SMTP_SERVER = os.getenv("SMTP_SERVER", "mail.zapatamorelos.gob.mx")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
EMAIL_USER = os.getenv("EMAIL_USER", "jefatura.nominas@zapatamorelos.gob.mx")
EMAIL_PASS = os.getenv("EMAIL_PASSWORD", "Admin123Nomina")

def enviar_correo_verificacion(destino: str, enlace: str):
    msg = EmailMessage()
    msg["Subject"] = "Confirma tu correo - Recibos Ayuntamiento"
    msg["From"] = EMAIL_ORIGEN
    msg["To"] = destino
    msg.set_content(f"""\
    Hola,

    Gracias por registrarte. Por favor confirma tu correo haciendo clic en el siguiente enlace:

    {enlace}

    Si no fuiste tú, ignora este mensaje.

    Atentamente,
    SYSTESO - Ayuntamiento de Emiliano Zapata
    """)

    msg.add_alternative(f"""\
    <html>
    <body>
        <p>Hola,</p>
        <p>Gracias por registrarte. Por favor haz clic en el siguiente enlace para verificar tu correo:</p>
        <p><a href="{enlace}">{enlace}</a></p>
        <p>Si no fuiste tú, puedes ignorar este mensaje.</p>
        <br>
        <p><b>SYSTESO</b><br>Ayuntamiento de Emiliano Zapata</p>
    </body>
    </html>
    """, subtype="html")

    try:
        # Usamos SSL si estás en el puerto 465
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.send_message(msg)
            print("✅ Correo de verificación enviado exitosamente.")
    except Exception as e:
        print(f"❌ Error enviando correo: {e}")


# ------------------------------------------------------------------
# Enviar correo de recuperación de contraseña   
# ------------------------------------------------------------------

def enviar_correo_recuperacion(destino: str, enlace: str):
    msg = EmailMessage()
    msg["Subject"] = "Recuperación de contraseña - Recibos Ayuntamiento"
    msg["From"] = EMAIL_ORIGEN
    msg["To"] = destino
    msg.set_content(f"""
Hola,

Solicitaste restablecer tu contraseña. Haz clic en este enlace para continuar:

{enlace}

Si no fuiste tú, ignora este mensaje.

Atentamente,
SYSTESO - Ayuntamiento de Emiliano Zapata
""")
    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.send_message(msg)
            print("✅ Correo de recuperación enviado exitosamente.")
    except Exception as e:
        print(f"❌ Error enviando correo: {e}")